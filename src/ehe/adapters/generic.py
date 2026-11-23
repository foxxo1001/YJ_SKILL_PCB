"""BOM and rule-report import with explicit loss of native evidence."""
import csv
import re
from dataclasses import asdict
from pathlib import Path
from xml.etree import ElementTree

from ..domain import bom
from ..io import load, sha256
from ..models import CanonicalProject, Evidence
from .kicad import read_native_report

REF_NAMES = {"reference", "references", "ref", "refs", "refdes", "designator", "位号"}
QTY_NAMES = {"quantity", "qty", "quanity", "数量"}
PART_NAMES = {"mpn", "manufacturer part number", "part number", "型号"}
VALUE_NAMES = {"value", "comment", "参数"}
FOOTPRINT_NAMES = {"footprint", "package", "封装"}


def _column(row, names):
    return next((value.strip() for key, value in row.items()
                 if key and key.strip().casefold() in names and value), "")


def import_bom(path: Path) -> tuple[list[dict], list[dict], dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        first = stream.readline()
        stream.seek(0)
        delimiter = "\t" if "\t" in first and "," not in first else ","
        reader = csv.DictReader(stream, delimiter=delimiter)
        if not reader.fieldnames or not {key.strip().casefold() for key in reader.fieldnames} & REF_NAMES:
            raise ValueError("BOM requires a reference/designator column")
        rows, missing = [], []
        for row in reader:
            if None in row:
                raise ValueError(f"BOM row {reader.line_num}: unquoted delimiter or extra fields")
            refs = [entry for entry in re.split(r"[,;\s]+", _column(row, REF_NAMES)) if entry]
            for ref in refs:
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]*", ref):
                    raise ValueError(f"Unsupported reference designator: {ref}")
            if not refs:
                raise ValueError(f"BOM row {reader.line_num}: empty designator field")
            raw_qty = _column(row, QTY_NAMES)
            if not raw_qty:
                missing.append(f"line:{reader.line_num}: explicit quantity")
            rows.append({"references": refs, "quantity": int(raw_qty) if raw_qty else len(refs),
                         "mpn": _column(row, PART_NAMES), "value": _column(row, VALUE_NAMES),
                         "footprint": _column(row, FOOTPRINT_NAMES), "locator": f"line:{reader.line_num}"})
    evaluation = bom(rows)
    if missing:
        evaluation["missing_evidence"].extend(missing)
        if evaluation["status"] == "pass":
            evaluation["status"] = "inconclusive"
    components = [{"id": ref, "reference": ref, "mpn": row["mpn"], "value": row["value"],
                   "footprint": row["footprint"], "locator": row["locator"]}
                  for row in rows for ref in row["references"]]
    return rows, components, evaluation


def import_netlist(path: Path) -> tuple[list, list, list]:
    raw = path.read_text(encoding="utf-8-sig")
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", raw, re.I):
        raise ValueError("XML DTD/entity declarations are disallowed")
    try:
        node = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise ValueError(f"Invalid XML netlist: {exc}") from exc
    if node.tag != "export" or node.find("components") is None or node.find("nets") is None:
        raise ValueError("Unsupported XML netlist, expected KiCad XML export")
    components = [{"id": comp.attrib["ref"], "reference": comp.attrib["ref"],
                   "value": comp.findtext("value"), "footprint": comp.findtext("footprint")}
                  for comp in node.findall("components/comp")]
    ids = {item["id"] for item in components}
    if len(ids) != len(components):
        raise ValueError("Duplicate component in netlist")
    nets, pins = [], []
    seen = set()
    for net in node.findall("nets/net"):
        name = net.attrib["name"]
        if not name or name in seen:
            raise ValueError("Blank or duplicate net name")
        seen.add(name)
        nets.append({"id": name, "name": name})
        for pin in net.findall("node"):
            component, number = pin.attrib["ref"], pin.attrib["pin"]
            if component not in ids:
                raise ValueError(f"Net references an unknown component: {component}")
            pins.append({"id": f"{component}:{number}", "component_id": component,
                         "number": number, "net": name, "type": pin.get("pintype")})
    if len({entry["id"] for entry in pins}) != len(pins):
        raise ValueError("Pin assigned more than once in netlist")
    return components, nets, pins


def ingest(path: Path, producer: str = "generic") -> dict:
    suffix = path.suffix.lower()
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("EDA export exceeds 32 MiB")
    base = {"project": {"path": str(path), "sha256": sha256(path), "producer_claim": producer},
            "evidence": [asdict(Evidence.file(path, "file", "documented", "eda_export_import"))],
            "limitations": ["Export provenance is supplied by the caller. No native execution or full board geometry."]}
    if suffix in {".csv", ".tsv"}:
        rows, components, evaluation = import_bom(path)
        for item in evaluation["findings"]:
            item["evidence_ids"] = [base["evidence"][0]["id"]]
        parts = [{"mpn": row["mpn"], "footprint": row["footprint"]}
                 for row in rows if row["mpn"]]
        project = CanonicalProject(**base, components=components, parts=parts)
        return {"status": evaluation["status"], "level": "E1", "kind": "bom", "evaluation": evaluation,
                "rows": rows, "canonical": project.to_dict()}
    if suffix == ".xml":
        components, nets, pins = import_netlist(path)
        return {"status": "imported", "level": "E1", "kind": "netlist",
                "canonical": CanonicalProject(**base, components=components, nets=nets, pins=pins).to_dict()}
    if suffix == ".json":
        data = load(path)
        if not isinstance(data, dict):
            raise ValueError("Rule report must be a JSON object")
        schema = data.get("$schema")
        if schema in {"https://schemas.kicad.org/erc.v1.json", "https://schemas.kicad.org/drc.v1.json"}:
            rule = schema.rsplit("/", 1)[-1].split(".", 1)[0]
            findings = read_native_report(data, rule)
        elif data.get("format") == "ehe-rule-report-v1" and isinstance(data.get("violations"), list):
            findings = data["violations"]
            if any(not isinstance(item, dict) or not all(item.get(k) for k in ("type", "severity", "description"))
                   for item in findings):
                raise ValueError("Rule report entries require type/severity/description")
        else:
            raise ValueError("Unknown rule report schema")
        return {"status": "imported", "level": "E1", "kind": "rule_report", "reported_findings": findings,
                "canonical": CanonicalProject(**base).to_dict()}
    return {"status": "inconclusive", "level": "E0", "kind": "unstructured",
            "canonical": CanonicalProject(**base).to_dict(), "missing_evidence": ["BOM, netlist or native PCB"]}
