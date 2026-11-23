"""Runtime task dispatch and exact input attribution."""
from pathlib import Path

from ..adapters import kicad
from ..domain import run_domain
from ..project import engineering_path, source_manifest


def task_inputs(root: Path, kind: str) -> dict[str, str]:
    configuration = root / "project.json"
    if kind in {"schematic", "pcb"}:
        return source_manifest(root, kicad.native_inputs(root))
    return source_manifest(root, [configuration, engineering_path(root)])


def execute(root: Path, kind: str, time_left: float) -> dict:
    if kind in {"schematic", "pcb"}:
        return kicad.run(root, kind, timeout=max(0.05, min(120, time_left)))
    if kind == "budget":
        return run_domain(engineering_path(root))
    if kind == "reality":
        from ..reality import CHECKS
    elif kind == "advanced":
        from ..advanced import CHECKS
    else:
        raise ValueError(f"Unknown task kind: {kind}")
    return run_domain(engineering_path(root), CHECKS)
