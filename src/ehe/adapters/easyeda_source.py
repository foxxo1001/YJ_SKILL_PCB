"""Bounded read-only extraction of EasyEDA Pro folder-format design records."""
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import NamedTuple
import json

from ..domain import number, result
from ..io import load, sha256
from ..models import CanonicalProject, Evidence

HEX = frozenset("0123456789abcdefABCDEF")
DOCUMENT_GROUPS = ("boards", "schematics", "sheets", "pcbs", "panels")
MAX_INPUT_BYTES = 256 * 1024 * 1024
MIL_MM = 0.0254
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".svg", ".step", ".stp", ".wrl", ".obj"}


class Document(NamedTuple):
    id: str
    name: str
    board: str | None
    schematic: str | None


def index_at(path: Path) -> Path:
    path = path.resolve()
    if path.is_dir():
        candidates = sorted(path.glob("*.eprj3"))
        if len(candidates) != 1:
            raise ValueError(f"Choose one .eprj3 index inside {path}: {[p.name for p in candidates]}")
        path = candidates[0]
    if path.suffix.lower() != ".eprj3" or not path.is_file() or path.resolve() != path:
        raise ValueError("Expected a regular EasyEDA Pro .eprj3 project index")
    return path


def documents(path: Path) -> tuple[str, dict[str, list[Document]]]:
    data = load(path)
    if not isinstance(data, dict) or data.get("format") != "folder" or not isinstance(data.get("profile"), dict):
        raise ValueError("Unsupported EasyEDA Pro folder project index")
    groups = {}
    for kind in DOCUMENT_GROUPS:
        group = data["profile"].get(kind, {})
        if not isinstance(group, dict):
            raise ValueError(f"Invalid .eprj3 profile group {kind}")
        entries = []
        for key, row in group.items():
            if (not 16 <= len(key) <= 32 or not set(key) <= HEX or not isinstance(row, dict)
                    or row.get("uuid", key) != key):
                raise ValueError(f"Invalid .eprj3 document identity in {kind}")
            entries.append(Document(key, str(row.get("title", row.get("name", key))),
                                    row.get("board") or None, row.get("schematic_uuid") or None))
        groups[kind] = entries
    return str(data.get("name", path.stem)), groups


def selection(groups: dict, pcb: str | None) -> tuple[Document, list[Document]]:
    boards = groups["pcbs"]
    matches = boards if pcb is None else [x for x in boards if x.id == pcb or x.name == pcb]
    if len(matches) != 1:
        options = ", ".join(f"{row.name} ({row.id})" for row in boards)
        raise ValueError(f"Select one EasyEDA PCB with --pcb <title-or-uuid>. Available: {options or 'none'}")
    selected = matches[0]
    linked = {s.id for s in groups["schematics"] if selected.board and s.board == selected.board}
    pages = [p for p in groups["sheets"] if p.schematic in linked]
    return selected, pages


def project_files(index: Path) -> list[Path]:
    sources = [index]
    for entry in index.parent.iterdir():
        if entry.suffix.lower() in ASSET_SUFFIXES:
            if not entry.is_file() or entry.resolve() != entry:
                raise ValueError(f"Unsupported linked EasyEDA asset: {entry.name}")
            sources.append(entry)
    for child in ("sch", "pcb", "panel"):
        folder = index.parent / child
        if not folder.exists():
            if folder.is_symlink():
                raise ValueError(f"Broken project link: {folder.name}")
            continue
        pending = [folder]
        while pending:
            entry = pending.pop()
            if entry.resolve() != entry:
                raise ValueError(f"Linked EasyEDA source unsupported: {entry.relative_to(index.parent)}")
            if entry.is_dir():
                pending.extend(p for p in entry.iterdir() if not p.name.startswith("."))
            elif entry.is_file():
                sources.append(entry)
            else:
                raise ValueError(f"Unsupported file type in EasyEDA project: {entry.name}")
            if len(sources) + len(pending) > 2000:
                raise ValueError("EasyEDA project exceeds the isolated-copy file budget")
    if sum(p.stat().st_size for p in sources) > MAX_INPUT_BYTES:
        raise ValueError("EasyEDA project exceeds the 256 MiB isolated-copy budget")
    return sorted(sources)


def _records(path: Path):
    decoder = json.JSONDecoder()
    with path.open(encoding="utf-8-sig") as stream:
        for index, raw in enumerate(stream, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                head, end = decoder.raw_decode(line)
                remainder = line[end:]
                terminated = remainder.endswith("|")
                if (not isinstance(head, dict) or not remainder.startswith("||")
                        or not terminated and raw.endswith(("\r", "\n"))):
                    raise ValueError("Expected an EasyEDA V3 log envelope")
                payload = (remainder[2:-1] if terminated else remainder[2:]).strip()
                body = json.loads(payload) if payload else None
                if not isinstance(head.get("type"), str) or body is not None and not isinstance(body, dict):
                    raise ValueError("Expected a typed EasyEDA V3 record")
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{path.name}:{index}: {exc}") from exc
            yield head, body, index


def _matching_records(path: Path, document: Document):
    active, header, active_rows = False, None, {}
    for head, body, line in _records(path):
        if head["type"] == "DOCHEAD":
            active = (body or {}).get("docType") == "PCB" and (body or {}).get("uuid") == document.id
            if active:
                header = body
            continue
        if not active or not isinstance(head.get("id"), str):
            continue
        ident = (head["type"], head["id"])
        previous = active_rows.get(ident)
        ticket = head.get("ticket")
        rank = (ticket if type(ticket) is int else -1, line)
        if previous is None or rank >= previous[0]:
            active_rows[ident] = (rank, body, line)
    return header, {key: (item[1], item[2]) for key, item in active_rows.items() if item[1] is not None}


def _mil(value) -> float:
    return round(number(value, "EasyEDA PCB coordinate") * MIL_MM, 9)


def _structured_id(ident: str, kind: str, length: int) -> list:
    fields = json.loads(ident)
    if not isinstance(fields, list) or len(fields) != length or fields[0] != kind:
        raise ValueError(f"Invalid {kind} record identity: {ident[:80]}")
    return fields


def _outline(geometries: list[dict]) -> dict:
    outlines = [g for g in geometries if g["kind"] == "board_outline"]
    metrics, missing = {}, []
    if len(outlines) != 1:
        missing.append("One unambiguous board outline contour is required for nominal size")
    else:
        obj = outlines[0]
        path = obj["path"]
        if isinstance(path, list) and len(path) >= 6 and path[0] == "R":
            _, x, y, width, height, rotation, *_ = path
            if rotation in (0, 90, 180, 270):
                w, h = abs(_mil(width)), abs(_mil(height))
                if rotation in (90, 270):
                    w, h = h, w
                if w > 0 and h > 0:
                    metrics = {"width_mm": w, "height_mm": h, "anchor_mm": [_mil(x), _mil(y)],
                               "primitive_id": obj["id"]}
        if not metrics:
            missing.append("Review the native editor for this non-rectangular or rotated board outline")
    return {**result("pass" if metrics else "inconclusive", metrics, missing=missing),
            "scope": "Nominal PCB outline from an admitted rectangular POLY, separate from assembly tolerances"}


def geometry(index: Path, board: Document, sources: list[Path], inputs: dict[str, str]) -> dict:
    matches = []
    for path in sources:
        if path.suffix.lower() != ".epcb2":
            continue
        header, rows = _matching_records(path, board)
        if header is not None:
            matches.append((path, header, rows))
    if len(matches) != 1:
        raise ValueError(f"Expected one .epcb2 source for PCB UUID {board.id}, found {len(matches)}")
    path, header, rows = matches[0]
    content = sha256(path)
    if content != inputs.get(path.relative_to(index.parent).as_posix()):
        raise ValueError("EasyEDA PCB source changed during extraction")
    attrs = defaultdict(dict)
    for (kind, _), (body, _) in rows.items():
        if kind == "ATTR" and body.get("parentId") and isinstance(body.get("key"), str):
            attrs[body["parentId"]][body["key"]] = body.get("value")
    evidence = asdict(Evidence.file(path, f"PCB:{board.id}", "documented", "easyeda_v3_native_source",
                                    str(header.get("editVersion", "")), content_sha256=content))
    parts, components, pins, layers, shapes, nets = [], [], [], [], [], set()
    for (kind, ident), (body, line) in rows.items():
        loc = {"document": board.id, "line": line}
        if kind == "COMPONENT":
            fields = attrs[ident]
            mpn = fields.get("Manufacturer Part") or fields.get("Manufacturer Part Number")
            components.append({"id": ident, "reference": fields.get("Designator"), "mpn": mpn,
                               "footprint": fields.get("Footprint"), "layer": str(body["layerId"]),
                               "at_mm": [_mil(body["x"]), _mil(body["y"])], "locator": loc})
            if mpn:
                parts.append({"mpn": mpn, "footprint": fields.get("Footprint")})
        elif kind == "PAD_NET":
            _, component, pin, _ = _structured_id(ident, kind, 4)
            net = body.get("padNet") or None
            if net:
                nets.add(net)
            pins.append({"id": ident, "component_id": component, "number": str(pin), "net": net,
                         "locator": loc})
        elif kind == "NET":
            _, name = _structured_id(ident, kind, 2)
            if name:
                nets.add(name)
        elif kind == "LAYER" and body.get("use"):
            _, layer = _structured_id(ident, kind, 2)
            layers.append({"id": str(layer), "name": body["layerName"], "type": body["layerType"]})
        elif kind == "LINE":
            shapes.append({"kind": "segment", "id": ident, "net": body.get("netName") or None,
                           "layer": str(body["layerId"]), "width_mm": _mil(body["width"]),
                           "start_mm": [_mil(body["startX"]), _mil(body["startY"])],
                           "end_mm": [_mil(body["endX"]), _mil(body["endY"])], "locator": loc})
        elif kind == "PAD":
            net = body.get("netName") or None
            if net:
                nets.add(net)
            shapes.append({"kind": "pad", "id": ident, "net": net, "layer": str(body["layerId"]),
                           "at_mm": [_mil(body["centerX"]), _mil(body["centerY"])], "locator": loc})
        elif kind == "VIA":
            shapes.append({"kind": "via", "id": ident, "net": body.get("netName") or None,
                           "at_mm": [_mil(body["centerX"]), _mil(body["centerY"])],
                           "size_mm": _mil(body["viaDiameter"]), "drill_mm": _mil(body["holeDiameter"]),
                           "locator": loc})
        elif kind == "POLY" and body.get("polyType") == "BOARD_OUTLINE":
            shapes.append({"kind": "board_outline", "id": ident, "layer": str(body["layerId"]),
                           "path": body.get("path"), "locator": loc})
    outlines = _outline(shapes)
    outlines["evidence"] = [evidence]
    canonical = CanonicalProject(
        project={"eda": "EasyEDA Pro", "name": board.name, "id": board.id, "path": str(path),
                 "source_version": header.get("editVersion"), "sha256": content, "outline": outlines},
        components=components, parts=parts, pins=pins, nets=[{"id": n, "name": n} for n in sorted(nets)],
        layers=layers, geometry=shapes, evidence=[evidence],
        limitations=["Source-object subset: routing arcs, copper-fill geometry, pad XY transforms and 3D assembly are not resolved.",
                     "PAD_NET mappings describe design intent. Physical copper continuity requires a rule check."],
    )
    return canonical.to_dict()
