"""Generate a native folder project from a checked flat circuit and four-layer layout specification."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from uuid import uuid4

from .easyeda_design import _record
from .easyeda_source import MIL_MM
from . import easyeda_layout
from ..domain import number
from ..io import atomic_write, load, save

FORMAT = "ehe-easyeda-circuit-v1"
LABEL = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,39}")
LAYERS = {1: ("TOP", "Top Layer", "#ff0000"), 15: ("SIGNAL", "Inner1", "#999966"),
          16: ("SIGNAL", "Inner2", "#008000"), 2: ("BOTTOM", "Bottom Layer", "#0000ff"),
          3: ("TOP_SILK", "Top Silkscreen", "#ffff00"), 11: ("OUTLINE", "Board Outline", "#ff00ff")}
PCB_BASE = {"partitionId": "", "groupId": 0, "locked": True}


def _mil(mm):
    return round(mm / MIL_MM, 8)


class _Document:
    def __init__(self, kind, ident, client, stamp):
        self.lines = [_record("DOCHEAD", 1, {"docType": kind, "uuid": ident, "client": client,
                                             "version": str(stamp), "updateTime": stamp,
                                             "editVersion": "4.1.60", "user": {}})]

    def add(self, kind, body, ident=None):
        ticket = len(self.lines) + 1
        ident = ident or f"e{ticket}"
        self.lines.append(_record(kind, ticket, body, ident))
        return ident


def _write(path, *docs):
    atomic_write(path, "\n".join(line for doc in docs for line in doc.lines) + "\n")


@dataclass(frozen=True)
class _Library:
    title: str
    symbol: str
    footprint: str
    device: str
    part_name: str
    pads: dict


def _attribute(doc, parent, key, value, at=None, part=None):
    x, y = at or (None, None)
    fields = {"parentId": parent, "key": key, "value": value, "groupId": "", "locked": False,
              "x": x, "y": y, "rotation": 0, "align": "LEFT_BOTTOM", "version": "2.0",
              "keyVisible": False, "valueVisible": at is not None, "zIndex": len(doc.lines)}
    if part:
        fields["partId"] = part
    doc.add("ATTR", fields)


def _symbol_positions(pins):
    half = (len(pins) + 1) // 2
    result = {}
    for index, pin in enumerate(pins):
        left = index < half
        result[pin] = (-55 if left else 55, 15 * (half - 1) - 30 * (index % half), left)
    return result


def _layer(doc, layer):
    layer_type, name, color = LAYERS[layer]
    doc.add("LAYER", {"layerId": layer, "layerType": layer_type, "layerName": name, "use": True,
                      "show": True, "locked": False, "activeColor": color, "activateTransparency": 1,
                      "inactiveColor": color, "inactiveTransparency": 0.5}, json.dumps(["LAYER", layer]))


def _pad_body(pin, pad):
    x, y = map(_mil, pad["xy"])
    w, h = map(_mil, pad["size"])
    drill = _mil(pad.get("drill", 0))
    shape = {"padType": "ELLIPSE" if drill else "RECT", "width": w, "height": h}
    if not drill:
        shape["radius"] = 0
    return {**PCB_BASE, "layerId": 12 if drill else 1, "zIndex": None, "num": pin, "netName": "",
            "centerX": x, "centerY": y, "padAngle": 0, "relativeAngle": 0, "plated": True,
            "hole": {"holeType": "ROUND", "width": drill, "height": drill} if drill else None,
            "defaultPad": shape, "specialPad": [], "padType": "NORMAL",
            "padOffsetX": 0, "padOffsetY": 0, "topSolderExpansion": None, "bottomSolderExpansion": None,
            "topPasteExpansion": None, "bottomPasteExpansion": None, "connectMode": None, "spokeSpace": None,
            "spokeWidth": None, "spokeAngle": None, "padLen": 0, "propagationDelay": 0}


def _library(ref, part, package, client, stamp):
    symbol, footprint, device = (uuid4().hex for _ in range(3))
    title = part["mpn"]
    part_name = f"{ref}.1"
    pads = package["pads"]
    lib = _Library(title, symbol, footprint, device, part_name, {pin: f"p{pin}" for pin in pads})
    sym = _Document("SYMBOL", symbol, client, stamp)
    sym.add("META", {"title": title, "docType": 2, "source": "", "tags": []}, "META")
    sym.add("CANVAS", {"originX": 0, "originY": 0}, "CANVAS")
    positions = _symbol_positions(pads)
    edge = max(abs(y) for _, y, _ in positions.values()) + 14
    sym.add("PART", {"title": part_name, "BBOX": [-45, -edge, 45, edge]}, part_name)
    sym.add("RECT", {"partId": part_name, "x": -45, "y": edge, "width": 90, "height": edge * 2,
                     "rotation": 0, "groupId": "", "locked": False, "zIndex": 1})
    for pin, pad in pads.items():
        x, y, left = positions[pin]
        ident = sym.add("PIN", {"partId": part_name, "groupId": "", "locked": False, "zIndex": 4,
                                "display": True, "x": x, "y": y, "length": 10, "rotation": 0 if left else 180,
                                "color": None, "pinShape": "NONE"})
        for key, value in (("Pin Name", pad["name"]), ("Pin Number", pin), ("Pin Type", pad["kind"])):
            _attribute(sym, ident, key, value, (x, y + 3) if key == "Pin Name" else None, part_name)
    dev = _Document("DEVICE", device, client, stamp)
    dev.add("META", {"title": title, "source": "", "tags": [], "images": [],
                     "attributes": {"Symbol": symbol, "Footprint": footprint, "Designator": ref, "Name": title,
                                    "Value": part.get("value", title), "Manufacturer Part": title,
                                    "Datasheet": package["source"], "Add into BOM": "yes", "Convert to PCB": "yes"}}, "META")
    fpt = _Document("FOOTPRINT", footprint, client, stamp)
    fpt.add("META", {"title": part["package"], "source": "", "tags": []}, "META")
    fpt.add("CANVAS", {"originX": 0, "originY": 0, "unit": "mil"}, "CANVAS")
    for layer in (1, 2, 3):
        _layer(fpt, layer)
    for pin, pad in pads.items():
        fpt.add("PAD", _pad_body(pin, pad), lib.pads[pin])
    return lib, (sym, fpt, dev)


def _sheet(doc, name, schematic, order, parts, packages, libs, pin_nets):
    doc.add("META", {"title": name, "schematic": schematic, "zIndex": order}, "META")
    doc.add("CANVAS", {"originX": 0, "originY": 0}, "CANVAS")
    for i, (ref, part) in enumerate(parts.items()):
        x, y = 200 + 295 * (i % 3), -190 - 190 * (i // 3)
        lib = libs[ref]
        ident = doc.add("COMPONENT", {"partId": lib.part_name, "x": x, "y": y, "rotation": 0,
                                      "isMirror": False, "attrs": {}, "locked": False,
                                      "zIndex": len(doc.lines), "groupId": ""})
        positions = _symbol_positions(packages[part["package"]]["pads"])
        edge = max(abs(dy) for _, dy, _ in positions.values()) + 14
        attrs = {"Symbol": lib.symbol, "Device": lib.device, "Footprint": lib.footprint, "Designator": ref,
                 "Unique ID": "ehe-" + ref, "Add into BOM": "yes", "Convert to PCB": "yes"}
        for key, value in attrs.items():
            _attribute(doc, ident, key, value, (x - 43, y + edge + 3) if key == "Designator" else None)
        _attribute(doc, ident, "Name", part.get("value", lib.title), (x - 43, y - edge - 8))
        for pin, (dx, dy, left) in positions.items():
            net = pin_nets.get((ref, pin))
            if net is None:
                continue
            sx, sy = x + dx, y + dy
            ex = sx + (-27 if left else 27)
            wire = doc.add("WIRE", {"groupId": "", "locked": False, "zIndex": len(doc.lines)})
            doc.add("LINE", {"lineGroup": wire, "startX": sx, "startY": sy, "endX": ex, "endY": sy,
                             "strokeColor": None, "strokeStyle": None, "strokeWidth": None})
            _attribute(doc, wire, "NET", net, (min(sx, ex), sy))


def _pcb(doc, board, parts, libs, pin_nets, plan):
    doc.add("META", {"title": "Board", "board": board, "parent": "", "source": "", "zIndex": None}, "META")
    doc.add("CANVAS", {"originX": 0, "originY": 0, "unit": "mil", "gridXSize": 5, "gridYSize": 5,
                       "snapXSize": 5, "snapYSize": 5, "altSnapXSize": 1, "altSnapYSize": 1,
                       "gridType": "OUTLETS", "multiGridType": "NONE"}, "CANVAS")
    for layer in LAYERS:
        _layer(doc, layer)
    for net in sorted(set(pin_nets.values())):
        doc.add("NET", {"netType": None, "retLine": True, "specialColor": None, "isPositiveNet": False},
                json.dumps(["NET", net], separators=(",", ":")))
    width, height = map(_mil, plan["size"])
    doc.add("POLY", {**PCB_BASE, "layerId": 11, "netName": "", "width": 6, "zIndex": None,
                     "polyType": "BOARD_OUTLINE", "path": ["R", 0, height, width, height, 0, 0]}, "outline")
    for ref, (x, y) in plan["placement"].items():
        lib = libs[ref]
        ident = uuid4().hex[:16]
        flags = {ident + pid: {"pinClass": "", "differentialPairClass": ""} for pid in lib.pads.values()}
        doc.add("COMPONENT", {**PCB_BASE, "layerId": 1, "x": _mil(x), "y": _mil(y), "angle": 0,
                              "attrs": {"Unique ID": "ehe-" + ref, "DeviceName": json.dumps(
                                  {"name": lib.title, "source": "", "uuid": lib.device})},
                              "zIndex": None, "pinSwap": False, "pinSwapInfo": flags,
                              "footprintPrimitives": True}, ident)
        for key, value in {"Designator": ref, "Footprint": lib.footprint, "Device": lib.device,
                           "Manufacturer Part": parts[ref]["mpn"], "Add into BOM": "yes",
                           "Convert to PCB": "yes"}.items():
            visible = key == "Designator"
            doc.add("ATTR", {**PCB_BASE, "parentId": ident, "layerId": 3, "key": key, "value": value,
                             "x": _mil(x) if visible else None, "y": _mil(y + 1.5) if visible else None,
                             "keyVisible": False, "valueVisible": visible, "fontFamily": "default",
                             "fontSize": 30, "strokeWidth": 4, "origin": "LEFT_BOTTOM", "angle": 0, "zIndex": None})
        for pin, pad_id in lib.pads.items():
            doc.add("PAD_NET", {"partitionId": "", "padNet": pin_nets.get((ref, pin), ""), "padLen": None,
                                "propagationDelay": None, "attrsMap": {}}, json.dumps(["PAD_NET", ident, pin, pad_id]))
    for idx, trace in enumerate(plan["tracks"]):
        sx, sy = map(_mil, trace["start"])
        ex, ey = map(_mil, trace["end"])
        doc.add("LINE", {**PCB_BASE, "layerId": trace["layer"], "netName": trace["net"], "width": _mil(trace["width_mm"]),
                         "startX": sx, "startY": sy, "endX": ex, "endY": ey, "zIndex": None}, f"trace{idx + 1}")
    for idx, via in enumerate(plan["vias"]):
        x, y = map(_mil, via["at"])
        doc.add("VIA", {**PCB_BASE, "netName": via["net"], "centerX": x, "centerY": y,
                        "holeDiameter": _mil(via["drill_mm"]), "viaDiameter": _mil(via["outer_mm"]),
                        "ruleName": "", "viaType": "NORMAL", "topSolderExpansion": None,
                        "bottomSolderExpansion": None, "unusedInnerLayers": [], "zIndex": None}, f"via{idx + 1}")
    for idx, zone in enumerate(plan["copper"]):
        x0, y0, x1, y1 = zone["box"]
        doc.add("FILL", {**PCB_BASE, "layerId": zone["layer"], "netName": zone["net"], "width": 0,
                         "path": [["R", _mil(x0), _mil(y1), _mil(x1 - x0), _mil(y1 - y0), 0, 0]],
                         "fillStyle": "SOLID", "zIndex": None}, f"copper{idx + 1}")
    for i, (x, y, diameter) in enumerate(plan["holes"]):
        doc.add("POLY", {**PCB_BASE, "layerId": 11, "netName": "", "width": 6, "zIndex": None,
                         "polyType": "BOARD_OUTLINE",
                         "path": ["CIRCLE", _mil(x), _mil(y), _mil(diameter / 2)]}, f"hole{i + 1}")


def _validate(spec):
    if spec.get("format") != FORMAT or not LABEL.fullmatch(spec["name"]):
        raise ValueError("Use the EasyEDA circuit specification and a safe project name")
    sheets, parts, packages, nets = (spec[key] for key in ("sheets", "parts", "packages", "nets"))
    if not 1 <= len(sheets) <= 8 or len(set(sheets)) != len(sheets) or not all(LABEL.fullmatch(s) for s in sheets):
        raise ValueError("Specify unique local schematic sheet names")
    if not 1 <= len(parts) <= 30:
        raise ValueError("Specify between 1 and 30 circuit components")
    endpoints = set()
    for ref, part in parts.items():
        if not LABEL.fullmatch(ref) or part["sheet"] not in sheets or not part["mpn"]:
            raise ValueError(f"Invalid component {ref}")
        package = packages[part["package"]]
        if not package["source"].startswith("https://") or not 1 <= len(package["pads"]) <= 18:
            raise ValueError(f"Part {ref} requires a sourced package with 1 to 18 pads")
        for pin, pad in package["pads"].items():
            if not re.fullmatch(r"[A-Za-z0-9]{1,8}", pin) or pad["kind"] not in {"IN", "OUT", "BI", "PASSIVE", "POWER"}:
                raise ValueError(f"Invalid pin {ref}.{pin}")
            if len(pad["xy"]) != 2 or len(pad["size"]) != 2:
                raise ValueError(f"Invalid geometry on {ref}.{pin}")
            for value in pad["xy"]:
                number(value, "pad coordinate", -30, 30)
            for value in pad["size"]:
                number(value, "pad size", 0.15, 15)
            endpoints.add((ref, pin))
    connections = {}
    for net, seq in nets.items():
        if not LABEL.fullmatch(net) or len(seq) < 2:
            raise ValueError(f"Invalid net {net!r}")
        for ref, pin in seq:
            if (ref, pin) not in endpoints or (ref, pin) in connections:
                raise ValueError(f"Unknown or duplicated endpoint {ref}.{pin}")
            connections[ref, pin] = net
    unused = {tuple(endpoint) for endpoint in spec.get("no_connect", [])}
    if unused & connections.keys() or unused | connections.keys() != endpoints:
        raise ValueError("Account for every pad in nets or no_connect")
    return connections


def prepare(spec_path: Path, destination: Path):
    spec = load(spec_path)
    pin_nets = _validate(spec)
    plan = easyeda_layout.plan(spec, pin_nets)
    destination = destination.absolute()
    if destination.resolve() != destination or destination.exists():
        raise ValueError("Circuit generation requires a new local project directory")
    parts, packages, sheets = (spec[key] for key in ("parts", "packages", "sheets"))
    if plan["status"] != "pass":
        return plan
    client = uuid4().hex[:16]
    board, pcb, schematic = (uuid4().hex for _ in range(3))
    stamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    page_ids = {name: uuid4().hex for name in sheets}
    libs, drawings = {}, {}
    for ref, part in parts.items():
        libs[ref], drawings[ref] = _library(ref, part, packages[part["package"]], client, stamp)
    profile = {key: {} for key in ("boards", "schematics", "sheets", "pcbs", "panels", "blockSymbols", "owner")}

    def profile_doc(kind, ident, **meta):
        profile[kind][ident] = {"uuid": ident, "version": str(stamp), "updateTime": stamp, **meta}

    profile_doc("boards", board, title=spec["name"], zIndex=1)
    profile_doc("schematics", schematic, name="Circuit", board=board, source="")
    profile_doc("pcbs", pcb, title="Board", board=board, source="", parent_uuid="", zIndex=None)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent, prefix=".ehe-circuit-") as folder:
        stage = Path(folder) / "circuit"
        for order, name in enumerate(sheets, 1):
            ident = page_ids[name]
            profile_doc("sheets", ident, title=name, schematic_uuid=schematic, zIndex=order, source="")
            page = _Document("SCH_PAGE", ident, client, stamp)
            sheet_parts = {ref: part for ref, part in parts.items() if part["sheet"] == name}
            _sheet(page, name, schematic, order, sheet_parts, packages, libs, pin_nets)
            _write(stage / "sch" / "Circuit" / f"{name}.esch2",
                   *(item for ref in sheet_parts for item in drawings[ref]), page)
        cfg = _Document("SCH", schematic, client, stamp)
        cfg.add("META", {"title": "Circuit", "board": board, "zIndex": None}, "META")
        _write(stage / "sch" / "Circuit" / "Circuit.ecfg", cfg)
        pcb_doc = _Document("PCB", pcb, client, stamp)
        _pcb(pcb_doc, board, parts, libs, pin_nets, plan)
        _write(stage / "pcb" / "Board.epcb2", *(item for docs in drawings.values() for item in docs), pcb_doc)
        filename = spec["name"] + ".eprj3"
        save(stage / filename, {"format": "folder", "name": spec["name"], "ticket": 1, "g_ticket": 1, "boards": [],
                                "config": {"defaultSheet": page_ids[sheets[0]], "settings": {}}, "profile": profile})
        save(stage / "circuit-intent.json", {"board": pcb, "nets": spec["nets"], "spec_format": FORMAT,
             "parts": {ref: {"footprint": libs[ref].footprint, "sheet": part["sheet"], "mpn": part["mpn"]}
                       for ref, part in parts.items()}})
        save(stage / "layout-plan.json", {**plan, "pcb": pcb})
        save(stage / "engineering-assumptions.json", spec.get("acceptance", {}))
        os.rename(stage, destination)
    return {"status": "created", "project": str(destination / filename), "pcb": pcb}


def generate(spec_path: Path, destination: Path):
    from ..review import full_review

    authored = prepare(spec_path, destination)
    if authored["status"] != "created":
        return authored
    project = Path(authored["project"])
    report = full_review(project, pcb=authored["pcb"], design_verification=True)
    return {"status": report["handoff_status"], "project": str(project),
            "report": report["report_path"], "layout": str(destination / "layout-plan.json"),
            "native": {key: report["coverage"][key]["status"] for key in ("erc", "drc", "schematic_parity", "design_intent",
                                                                      "design_bom", "multilayer_layout")},
            "physical_acceptance": "not_run"}
