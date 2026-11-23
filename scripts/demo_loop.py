"""Retain a full real ERC/DRC repair and acceptance transcript."""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from ehe.adapters.kicad import native_inputs, run
from ehe.io import atomic_write, save
from ehe.project import source_manifest
from ehe.report import render
from ehe.runtime.engine import resume, start
from inject import disconnect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--external-demo", type=Path, help="Optional installed KiCad reference design")
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = ROOT / "examples/continuity"
    project = out / "design"
    shutil.copytree(source, project, ignore=shutil.ignore_patterns(".ehe"))
    disconnect(project)
    hashes_bad = source_manifest(project, native_inputs(project))
    before = start(project, project / "goal.yaml")
    assert hashes_bad == source_manifest(project, native_inputs(project)), "Unexpected project edit"
    assert before["state"] == "REVISE", before["state"]
    atomic_write(out / "01-findings.md", render(project))
    save(out / "01-state.json", before)
    paused = resume(project)
    assert paused["state"] == "PAUSED" and paused["calls"] == before["calls"], paused["state"]
    save(out / "02-paused.json", paused)
    for suffix in ("sch", "pcb"):
        name = f"continuity.kicad_{suffix}"
        shutil.copyfile(source / name, project / name)
    hashes_good = source_manifest(project, native_inputs(project))
    after = resume(project)
    assert after["state"] == "PASSED" and after["round"] == 2, after["state"]
    assert hashes_good == source_manifest(project, native_inputs(project)), "Unexpected project edit"
    atomic_write(out / "03-regression.md", render(project))
    save(out / "03-state.json", after)
    save(out / "hashes.json", {"injected": hashes_bad, "repaired": hashes_good})
    continuous = ["REVISE", "PAUSED", "PASSED"]
    if args.external_demo:
        source = args.external_demo.resolve()
        external = out / "external_design"
        external.mkdir()
        for path in native_inputs(source):
            target = external / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        hashes = source_manifest(external, native_inputs(external))
        checks = [run(external, domain) for domain in ("schematic", "pcb")]
        assert [item["status"] for item in checks] == ["pass", "pass"]
        assert hashes == source_manifest(source, native_inputs(source))
        save(out / "04-installed-reference.json", checks)
    print(json.dumps({"states": continuous, "l5": "not_run", "output": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
