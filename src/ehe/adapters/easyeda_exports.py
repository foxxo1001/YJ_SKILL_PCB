"""Compare native JLCEDA netlists and audit native BOM rows."""
import csv
from dataclasses import asdict
import io
import json
from pathlib import Path

from ..domain import finding, result
from ..io import atomic_write, load
from ..models import Evidence


def _export(page, key):
    path = page.get("export_artifact")
    if not path:
        raise ValueError("Native export did not run for the expected document")
    path = Path(path)
    raw = load(path)
    captured = raw["execution"]
    value = captured.get("native", {}).get("value") or {}
    if captured["status"] != "available" or raw.get("identity_verified") is not True:
        raise ValueError("Native export did not verify the active document")
    entry = value.get(key) or {}
    if not isinstance(entry.get("text"), str) or not entry["text"].strip():
        raise ValueError(entry.get("error") or f"Missing native {key} text")
    if key == "netlist" and json.loads(entry["text"]).get("projectId") != raw["target"]["project"]:
        raise ValueError("Native netlist reported a different project identity")
    evidence = asdict(Evidence.file(path, f"EasyEDA:{key}:{raw['document']}", "tool_verified",
                                    "EasyEDA native export API", raw["version"]))
    return entry["text"], evidence


def _netlist(raw):
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("version") != "2.0.0" or not isinstance(data.get("components"), dict):
        raise ValueError("Expected a native JLCEDA version 2.0.0 netlist")
    parts, pins = {}, {}
    for component in data["components"].values():
        props, pin_map = component["props"], component["pinInfoMap"]
        if str(props.get("Convert to PCB", "")).lower() == "no":
            continue
        ref = props.get("Designator")
        if not isinstance(ref, str) or not ref.strip() or ref in parts:
            raise ValueError(f"Missing or ambiguous native designator: {ref!r}")
        parts[ref] = props.get("Footprint")
        for key, pin in pin_map.items():
            net = pin.get("net")
            if not isinstance(net, str):
                raise ValueError(f"Invalid net on {ref}.{key}")
            if net:
                ident = (ref, str(pin.get("number") or key))
                if ident in pins and pins[ident] != net:
                    raise ValueError(f"Ambiguous native pin assignment: {ident}")
                pins[ident] = net
    return parts, pins


def parity(pcb, pages, page_index=None):
    if not pages:
        return result("not_run", missing=["Selected PCB has no linked schematic sheet"])
    locations = (page_index or {}).get("locations", {})
    unsupported = (page_index or {}).get("unsupported", [])
    if len(pages) > 1 and (page_index is None or unsupported):
        return result("inconclusive", missing=unsupported or ["Missing source location inventory for multiple sheets"])
    try:
        pcb_text, pcb_ev = _export(pcb, "netlist")
        schematic_exports = [_export(page, "netlist") for page in pages]
        graphs = [_netlist(text) for text, _ in schematic_exports]
        actual_parts, actual_pins = _netlist(pcb_text)
        expected_parts, expected_pins = graphs[0]
    except (KeyError, TypeError, AttributeError, OSError, ValueError) as exc:
        return result("blocked", missing=[str(exc)])
    evidence = [ev for _, ev in schematic_exports] + [pcb_ev]
    if len(pages) > 1 and (any(graph != graphs[0] for graph in graphs[1:]) or expected_parts.keys() != locations.keys()):
        return {**result("inconclusive", {"pages_exported": len(pages)},
                         missing=["Native exports do not attest to the same complete linked flat schematic. "
                                  "Check the selected board, sheet scopes and transfer-to-PCB attributes."]),
                "evidence": evidence}
    if not actual_parts and not expected_parts:
        return {**result("inconclusive", missing=["Both native netlists contain no components"]), "evidence": evidence}
    compared = compare_graphs(graphs[0], (actual_parts, actual_pins), locations, evidence)
    compared["metrics"]["pages_exported"] = len(pages)
    compared["scope"] = "complete flat linked schematic" if len(pages) > 1 else "one linked sheet"
    return compared


def compare_graphs(expected, actual, locations, evidence, *, actual_label="pcb", expected_label="schematic"):
    expected_parts, expected_pins = expected
    actual_parts, actual_pins = actual

    def issue(code, ref, reason, **scope):
        row = finding(code, "schematic", reason, ref,
                      "按设计连接核对所列位号和引脚，修正错误的连线或标注。",
                      "重新导出原生网表，对照端点并复跑相关规则。")
        row["scope"].update(schematic_location=locations.get(ref), checked_document=actual_label, **scope)
        row["evidence_ids"] = [item["id"] for item in evidence]
        return row

    issues = []
    for ref in sorted(expected_parts.keys() | actual_parts.keys()):
        if ref not in expected_parts or ref not in actual_parts:
            side = expected_label if ref in expected_parts else actual_label
            issues.append(issue("easyeda_component_mismatch", ref, f"{ref} 仅出现在 {side} 网表", present_on=side))
        elif expected_parts[ref] and actual_parts[ref] and expected_parts[ref] != actual_parts[ref]:
            issues.append(issue("easyeda_footprint_mismatch", ref, f"{ref} 的封装标识不同",
                                schematic_footprint=expected_parts[ref], pcb_footprint=actual_parts[ref]))
    common = expected_parts.keys() & actual_parts.keys()
    actual_groups, expected_groups = {}, {}
    for table, groups in ((actual_pins, actual_groups), (expected_pins, expected_groups)):
        for pin, name in table.items():
            groups.setdefault(name, set()).add(pin)
    renames, topology = 0, 0
    for pin in sorted(expected_pins.keys() | actual_pins.keys()):
        ref, number = pin
        if ref not in common:
            continue
        a, b = actual_pins.get(pin), expected_pins.get(pin)
        a_group, b_group = actual_groups.get(a, set()), expected_groups.get(b, set())
        if a_group != b_group:
            topology += 1
            issues.append(issue("easyeda_net_mismatch", ref, f"{ref}.{number} 的实际连接端点不同", pin=number,
                                schematic_net=b, pcb_net=a, missing_endpoints=sorted(b_group - a_group),
                                unexpected_endpoints=sorted(a_group - b_group)))
        elif a != b and not (a.startswith("$") and b.startswith("$")):
            renames += 1
            issues.append(issue("easyeda_net_name_changed", ref, f"{ref}.{number} 的端点相同，网名不同",
                                pin=number, schematic_net=b, pcb_net=a))
    metrics = {"schematic_components": len(expected_parts), "pcb_components": len(actual_parts),
               "schematic_connected_pins": len(expected_pins), "pcb_connected_pins": len(actual_pins),
               "connectivity_mismatches": topology, "named_endpoint_differences": renames}
    return {**result("fail" if issues else "pass", metrics, issues), "level": "E1", "evidence": evidence}


def intent_graph(intent):
    return ({ref: part["footprint"] for ref, part in intent["parts"].items()},
            {(ref, str(pin)): net for net, endpoints in intent["nets"].items() for ref, pin in endpoints})


def design_intent(pcb, pages, intent, page_index):
    try:
        expected = intent_graph(intent)
        native = [("schematic:" + str(page.get("document")), page) for page in pages] + [("pcb", pcb)]
        checks = {}
        for name, document in native:
            text, evidence = _export(document, "netlist")
            checks[name] = compare_graphs(expected, _netlist(text), page_index["locations"], [evidence],
                                          actual_label=name, expected_label="design")
    except (KeyError, TypeError, AttributeError, OSError, ValueError) as exc:
        return result("blocked", missing=[str(exc)])
    if not pages:
        return result("not_run", missing=["Design intent requires native schematic pages"])
    return {**result("fail" if any(row["status"] == "fail" for row in checks.values()) else "pass",
                     {"native_documents_compared": len(checks)},
                     [item for row in checks.values() for item in row["findings"]]),
            "evidence": [item for row in checks.values() for item in row["evidence"]], "documents": checks}


def bom(pcb, out):
    try:
        text, evidence = _export(pcb, "bom")
        netlist, net_evidence = _export(pcb, "netlist")
        _netlist(netlist)
        expected = {row["props"]["Designator"] for row in json.loads(netlist)["components"].values()
                    if str(row["props"].get("Add into BOM", "")).lower() != "no"
                    and str(row["props"].get("Convert to PCB", "")).lower() != "no"}
        table = csv.DictReader(io.StringIO(text.lstrip("\ufeff")), delimiter="\t")
        if not {"Designator", "Quantity", "Manufacturer Part", "Supplier Part"} <= set(table.fieldnames or []):
            raise ValueError("Unexpected native EasyEDA BOM columns")
        rows, missing, seen = 0, [], set()
        for entry in table:
            refs = [ref.strip() for ref in entry["Designator"].split(",") if ref.strip()]
            if not refs or seen.intersection(refs) or int(entry["Quantity"]) != len(set(refs)) or len(refs) != len(set(refs)):
                raise ValueError(f"Invalid or duplicate BOM designators at native row {table.line_num}")
            seen.update(refs)
            rows += 1
            if not entry["Manufacturer Part"].strip() and not entry["Supplier Part"].strip():
                missing.extend(f"Manufacturer or supplier part number for {ref}" for ref in refs)
        if not rows:
            missing.append("Native BOM contains no purchasable components")
        path = out / "easyeda-native-bom.tsv"
        atomic_write(path, text)
        findings = []
        for ref in sorted(expected ^ seen):
            where = "netlist" if ref in expected else "bom"
            issue = finding("easyeda_bom_ref_mismatch", "pcb", f"{ref} 仅在应采购网表或 BOM 的 {where} 一侧出现",
                            ref, "核对器件的 Add into BOM 属性、装配变体和导出范围。",
                            "用同一 PCB 和同一装配变体重新导出 BOM 并对照位号。")
            issue["scope"]["present_on"] = where
            issue["evidence_ids"] = [evidence["id"], net_evidence["id"]]
            findings.append(issue)
        status = "fail" if findings else "inconclusive" if missing else "pass"
        return {**result(status, {"rows": rows, "designators": len(seen), "expected_designators": len(expected)},
                         findings, missing), "artifact": str(path), "level": "E1", "evidence": [evidence, net_evidence]}
    except (AttributeError, KeyError, TypeError, OSError, ValueError, csv.Error) as exc:
        return result("blocked", missing=[str(exc)])
