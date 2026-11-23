"""Inject explicit electrical discontinuities into disposable fixture copies."""
import re
from pathlib import Path


def disconnect(root: Path) -> None:
    sch = root / "continuity.kicad_sch"
    pcb = root / "continuity.kicad_pcb"
    source = sch.read_text(encoding="utf-8")
    changed, count = re.subn(r"(?m)^ *\((?:wire|label) .*\n", "", source)
    if count != 2:
        raise ValueError("Fixture schematic must have exactly one wire and label")
    layout = pcb.read_text(encoding="utf-8")
    omitted, count = re.subn(r"(?m)^ *\(segment .*\)\)\)\n", ")\n", layout)
    if count != 1:
        raise ValueError("Fixture PCB must have exactly one disconnectable segment")
    sch.write_text(changed, encoding="utf-8")
    pcb.write_text(omitted, encoding="utf-8")
