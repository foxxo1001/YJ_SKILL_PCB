"""Connect EasyEDA Pro project evidence to the shared EHE review profile."""
from pathlib import Path

from . import easyeda_native as native, easyeda_source as source
from ..domain import result
from ..io import save
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
    try:
        canonical = source.geometry(index, board, files, inputs)
        normal = out / "canonical-pcb.json"
        save(normal, canonical)
        outline = {**canonical["project"]["outline"], "level": "E3"}
        target["canonical_pcb"] = str(normal)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        outline = result("inconclusive", missing=[f"EasyEDA V3 PCB geometry: {exc}"])

    checks = native.audit(index, board, pages, files, inputs, out, offline=offline, bom=design_verification)
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
    consistent = inputs == source_manifest(index.parent, source.project_files(index))
    return inputs, coverage, target, consistent
