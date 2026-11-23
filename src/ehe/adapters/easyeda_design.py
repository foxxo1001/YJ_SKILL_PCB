"""Generate a native single-copper-layer test and interconnect board."""
from itertools import pairwise
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from uuid import uuid4

from .easyeda_source import MIL_MM
from ..domain import number
from ..io import atomic_write, load, now, save


def _dimension(value, label, minimum, maximum):
    return round(number(value, label, minimum, maximum) / MIL_MM, 6)


def _box(a, b, radius):
    return tuple(min(x, y) - radius for x, y in zip(a, b)), tuple(max(x, y) + radius for x, y in zip(a, b))


def _clear(box, other, gap):
    return any(a1 + gap <= b0 or b1 + gap <= a0 for a0, a1, b0, b1 in zip(*box, *other))


def layout(spec):
    if not isinstance(spec, dict) or set(spec) != {"width_mm", "height_mm", "gap_mm", "edge_mm",
                                                  "trace_mm", "pad_mm", "nets"}:
        raise ValueError("Use width_mm, height_mm, gap_mm, edge_mm, trace_mm, pad_mm and nets")
    width, height = (_dimension(spec[f"{axis}_mm"], axis, 5, 300) for axis in ("width", "height"))
    gap = _dimension(spec["gap_mm"], "copper gap", 0.2, 10)
    edge = _dimension(spec["edge_mm"], "edge clearance", 0.3, 10)
    trace = _dimension(spec["trace_mm"], "trace width", 0.2, 8)
    pad = _dimension(spec["pad_mm"], "pad diameter", 0.8, 12)
    if trace > pad:
        raise ValueError("Trace width must not exceed pad diameter")
    nets = spec["nets"]
    if not isinstance(nets, dict) or not 1 <= len(nets) <= 24:
        raise ValueError("Provide between 1 and 24 two-terminal nets")
    terminals, paths, occupied = {}, {}, []
    for net, points in sorted(nets.items(), key=lambda item: str(item[0])):
        if not isinstance(net, str) or not re.fullmatch(r"[A-Za-z0-9_+.-]{1,32}", net) or not isinstance(points, list) or len(points) != 2:
            raise ValueError("Each net requires a name and two XY pairs in mm")
        pins = []
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError(f"Expected two coordinates on {net}")
            xy = tuple(_dimension(value, net, 0, 300) for value in point)
            box = _box(xy, xy, pad / 2)
            if any(low < edge or high > limit - edge for low, high, limit in zip(*box, (width, height))):
                raise ValueError(f"Pad on {net} violates board edge clearance")
            if any(not _clear(box, other, gap) for _, other in occupied):
                raise ValueError(f"Pads on {net} violate copper clearance")
            occupied.append((net, box))
            pins.append(xy)
        terminals[net] = pins

    for net, (a, b) in terminals.items():
        candidates = ((a, b),) if a[0] == b[0] or a[1] == b[1] else (
            (a, (a[0], b[1]), b), (a, (b[0], a[1]), b))
        for points in candidates:
            boxes = [_box(start, end, trace / 2) for start, end in pairwise(points)]
            if all(_clear(box, other, gap) for box in boxes for owner, other in occupied if owner != net):
                paths[net] = list(pairwise(points))
                occupied.extend((net, box) for box in boxes)
                break
        else:
            raise ValueError(f"Cannot route {net} on the top layer with the requested clearance")
    return width, height, trace, pad, terminals, paths


def _record(kind, ticket, body, ident=None):
    header = {"type": kind, "ticket": ticket}
    if ident is not None:
        header["id"] = ident
    compact = {"ensure_ascii": False, "separators": (",", ":")}
    return f"{json.dumps(header, **compact)}||{json.dumps(body, **compact)}|"


def _pcb(uuid, client, stamp, geometry):
    width, height, trace, pad, terminals, paths = geometry
    records = [_record("DOCHEAD", 1, {"docType": "PCB", "client": client, "uuid": uuid,
                                      "updateTime": stamp, "version": str(stamp), "editVersion": "4.1.60", "user": {}})]

    def append(kind, body, ident=None):
        ticket = len(records) + 1
        records.append(_record(kind, ticket, body, f"e{ticket}" if ident is None else ident))

    append("META", {"title": "Board", "parent": "", "source": "", "board": "", "zIndex": None}, "META")
    append("CANVAS", {"originX": 0, "originY": 0, "unit": "mil", "gridXSize": 5, "gridYSize": 5,
                      "snapXSize": 5, "snapYSize": 5, "altSnapXSize": 1, "altSnapYSize": 1,
                      "gridType": "OUTLETS", "multiGridType": "NONE"}, "CANVAS")
    for layer, kind, title, color in [(1, "TOP", "Top Layer", "#ff0000"), (2, "BOTTOM", "Bottom Layer", "#0000ff"),
                                       (11, "OUTLINE", "Board Outline", "#ff00ff")]:
        append("LAYER", {"layerId": layer, "layerType": kind, "layerName": title, "use": True,
                         "show": True, "locked": False, "activeColor": color, "activateTransparency": 1,
                         "inactiveColor": color, "inactiveTransparency": 0.5}, json.dumps(["LAYER", layer]))
    base = {"partitionId": "", "groupId": 0, "locked": False}
    append("POLY", {**base, "layerId": 11, "netName": "", "width": 8, "zIndex": -1,
                    "polyType": "BOARD_OUTLINE", "path": ["R", 0, height, width, height, 0, 0]})
    for net, points in terminals.items():
        append("NET", {"netType": None, "specialColor": None, "retLine": True,
                       "differentialName": None, "isPositiveNet": False, "equalLengthGroupName": None},
               json.dumps(["NET", net], separators=(",", ":")))
        for x, y in points:
            ticket = len(records) + 1
            append("PAD", {**base, "layerId": 1, "zIndex": ticket, "netName": net, "num": str(ticket),
                           "centerX": x, "centerY": y, "padAngle": 0, "hole": None,
                           "defaultPad": {"padType": "ELLIPSE", "width": pad, "height": pad},
                           "specialPad": [], "padOffsetX": 0, "padOffsetY": 0, "relativeAngle": 0,
                           "plated": False, "padType": "TEST", "topSolderExpansion": None,
                           "bottomSolderExpansion": None, "topPasteExpansion": -pad, "bottomPasteExpansion": None,
                           "connectMode": None, "spokeSpace": None, "spokeWidth": None, "spokeAngle": None,
                           "padLen": 0, "propagationDelay": 0})
        for (x1, y1), (x2, y2) in paths[net]:
            append("LINE", {**base, "layerId": 1, "netName": net, "width": trace, "startX": x1, "startY": y1,
                            "endX": x2, "endY": y2, "zIndex": len(records) + 1})
    return "\n".join(records) + "\n"


def generate(spec_path: Path, destination: Path):
    from ..review import full_review

    spec = load(spec_path)
    geometry = layout(spec)
    destination = destination.absolute()
    if destination.exists() or destination.resolve() != destination:
        raise ValueError("EasyEDA generation requires a new local directory")
    stamp = 1767225600000
    board, client = uuid4().hex, uuid4().hex[:16]
    profile = {key: {} for key in ("boards", "schematics", "sheets", "panels", "blockSymbols", "owner")}
    profile["pcbs"] = {board: {"uuid": board, "title": "Board", "board": "", "parent_uuid": "",
                              "source": "", "zIndex": None, "version": str(stamp), "updateTime": stamp}}
    index = "Interconnect.eprj3"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent, prefix=".ehe-design-") as folder:
        stage = Path(folder) / "design"
        save(stage / index, {"format": "folder", "name": "Interconnect", "ticket": 1, "g_ticket": 1,
                             "boards": [], "config": {"defaultSheet": "", "settings": {}}, "profile": profile})
        atomic_write(stage / "pcb" / "Board.epcb2", _pcb(board, client, stamp, geometry))
        save(stage / "generation.json", {"created_at": now(), "requirements": spec,
                                         "generator": "easyeda-interconnect-v1", "pcb": board,
                                         "scope": "Single-sided copper interconnect with standalone test pads"})
        os.rename(stage, destination)
    report = full_review(destination / index, pcb=board)
    return {"status": report["coverage"]["drc"]["status"], "project": str(destination / index), "pcb": board,
            "review": report["report_path"], "drc": report["coverage"]["drc"],
            "limitations": ["Interconnect/test-pad boards with fixed terminals and top copper only",
                            "No schematic or component placement synthesized"]}
