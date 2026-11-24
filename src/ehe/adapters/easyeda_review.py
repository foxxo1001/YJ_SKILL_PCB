"""Connect EasyEDA Pro project evidence to the shared EHE review profile."""
from pathlib import Path

from . import easyeda_exports, easyeda_layout, easyeda_native as native, easyeda_source as source
from ..domain import result
from ..io import load, save, sha256
from ..project import source_manifest


def project_geometry(index: Path, pcb: str | None):
    files = source.project_files(index)
    inputs = source_manifest(index.parent, files)
    _, groups = source.documents(index)
    board, _ = source.selection(groups, pcb)
    canonical = source.geometry(index, board, files, inputs)
    if inputs != source_manifest(index.parent, source.project_files(index)):
        raise ValueError("EasyEDA inputs changed during geometry extraction")
    return {"status": "parsed", "level": "E3", "backend": "easyeda", "canonical": canonical}


def review(index: Path, pcb: str | None, out: Path, design_verification: bool, offline: bool):
    files = source.project_files(index)
    inputs = source_manifest(index.parent, files)
    name, groups = source.documents(index)
    board, pages = source.selection(groups, pcb)
    target = {"index": str(index), "name": name, "pcb": {"uuid": board.id, "title": board.name, "board": board.board},
              "schematics": [{"uuid": page.id, "title": page.name, "schematic": page.schematic} for page in pages]}
    canonical = None
    try:
        canonical = source.geometry(index, board, files, inputs)
        normal = out / "canonical-pcb.json"
        save(normal, canonical)
        outline = {**canonical["project"]["outline"], "level": "E3"}
        target["canonical_pcb"] = str(normal)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        outline = result("inconclusive", missing=[f"EasyEDA V3 PCB geometry: {exc}"])

    intent_file = index.parent / "circuit-intent.json"
    intent = None
    intent_hash = None
    if design_verification and intent_file.is_file() and intent_file.resolve() == intent_file:
        intent_hash = sha256(intent_file)
        intent = load(intent_file)
        if intent.get("board") != board.id:
            raise ValueError("Design intent refers to a different PCB UUID")
    checks = native.audit(index, board, pages, files, inputs, out, offline=offline, bom=design_verification,
                          intent=intent)
    coverage = {
        "erc": {"topic": "嘉立创原理图规则检查", **checks["erc"]},
        "drc": {"topic": "嘉立创 PCB DRC", **checks["drc"]},
        "schematic_parity": {"topic": "原理图与 PCB 网络及器件一致性",
                             **checks["schematic_parity"]},
        "board_outline": {"topic": "嘉立创原生板框与标称尺寸", **outline},
    }
    if design_verification:
        coverage["design_bom"] = {"topic": "嘉立创 BOM 与采购料号",
                                  **checks["design_bom"]}
    if intent is not None:
        coverage["design_intent"] = {"topic": "设计约定对原生原理图和 PCB 网表", **checks["design_intent"],
                                     "spec": str(intent_file), "sha256": intent_hash}
        if intent.get("spec_format") == "ehe-easyeda-circuit-v1":
            try:
                expected = easyeda_exports.intent_graph(intent)
                graph, locations = source.authored_schematic_graph(index.parent, pages, files)
                check = easyeda_exports.compare_graphs(expected, graph, locations, [],
                                                       actual_label="schematic_source", expected_label="design")
                check["scope"] = "Independently read embedded symbols, pin endpoints and named wires in emitted V3 source"
            except (OSError, TypeError, KeyError, ValueError) as exc:
                check = result("inconclusive", missing=[str(exc)])
            coverage["schematic_source_intent"] = {"topic": "原理图图元连线独立读回", **check,
                                                    "spec": str(intent_file), "sha256": intent_hash}
    plan_file = index.parent / "layout-plan.json"
    plan_hash = None
    if plan_file.is_file() and plan_file.resolve() == plan_file:
        plan_hash = sha256(plan_file)
        plan = load(plan_file)
        if plan.get("pcb") == board.id:
            try:
                layout_result = (easyeda_layout.verify(canonical, plan) if canonical else
                                 result("inconclusive", missing=["Native PCB geometry unavailable"]))
            except (KeyError, ValueError, TypeError) as exc:
                layout_result = result("inconclusive", missing=[f"Invalid multilayer layout evidence: {exc}"])
            coverage["multilayer_layout"] = {"topic": "实际线路、地铜、回流与散热过孔", **layout_result,
                                               "spec": str(plan_file), "sha256": plan_hash}
    consistent = inputs == source_manifest(index.parent, source.project_files(index))
    if intent_hash:
        consistent = consistent and intent_file.is_file() and sha256(intent_file) == intent_hash
    if plan_hash:
        consistent = consistent and plan_file.is_file() and sha256(plan_file) == plan_hash
    return inputs, coverage, target, consistent
