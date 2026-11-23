"""Approval-bound, idempotent changes in a versioned working copy."""
import difflib
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

from ..adapters import kicad
from ..adapters.sexpr import read
from ..io import digest, inside, load, save, sha256
from ..project import engineering_path, output_path, source_manifest
from .plan import impact

ALLOWED = {".kicad_sch": "kicad_sch", ".kicad_pcb": "kicad_pcb"}


def _source(root: Path, relative: str) -> Path:
    path = inside(root, relative)
    parts = path.relative_to(root).parts
    if not parts or parts[0].startswith(".") or path != root / Path(relative):
        raise ValueError("Change inputs must be ordinary project files with canonical relative paths")
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Change input file is missing or exceeds 2 MiB")
    return path


def _copy_inputs(root, replacement):
    paths = [p for p in kicad.native_inputs(root) if p != replacement]
    return source_manifest(root, [*paths, root / "project.json", engineering_path(root)])


def propose(root: Path, request: dict, snapshot: dict) -> dict:
    operation = request["operation"]
    labels = set(request.get("impact", []))
    body = {"operation": operation, "description": request["description"], "request": request, "snapshot": snapshot,
            "rollback": "Use the untouched original revision. Retain the working copy and its receipt for audit."}
    if operation == "workcopy_replace":
        target, replacement = (_source(root, request[key]) for key in ("target", "replacement"))
        suffix = target.suffix.lower()
        if suffix not in ALLOWED or target == replacement or replacement.suffix.lower() != suffix:
            raise ValueError("Workcopy replacement requires distinct native KiCad files of the same type")
        parsed = read(replacement)
        if not parsed or parsed[0] != ALLOWED[suffix]:
            raise ValueError("Replacement KiCad file has the wrong root object")
        kicad.check_references(replacement)
        body["copy_inputs"] = _copy_inputs(root, replacement)
        body["target"] = target.relative_to(root).as_posix()
        body["replacement"] = replacement.relative_to(root).as_posix()
        body["before_sha256"] = sha256(target)
        body["after_sha256"] = sha256(replacement)
        if body["before_sha256"] == body["after_sha256"]:
            raise ValueError("Working-copy replacement would not change the target")
        if body["copy_inputs"].get(body["target"]) != body["before_sha256"]:
            raise ValueError("Target is not a recognized project source or changed during proposal")
        diff = list(difflib.unified_diff(target.read_text(encoding="utf-8-sig").splitlines(),
                                        replacement.read_text(encoding="utf-8-sig").splitlines(),
                                        fromfile=body["target"], tofile=body["replacement"], lineterm=""))
        body["preview"] = "\n".join(diff[:250])[:24000]
        body["preview_truncated"] = len(diff) > 250 or len("\n".join(diff[:250])) > 24000
        labels.add("schematic" if suffix == ".kicad_sch" else "pcb")
    body["impact_labels"] = sorted(labels)
    body["impact_domains"] = impact(labels)
    return {"id": "chg-" + digest(body)[:20], **body}


def copy_root(root: Path, name: str) -> Path:
    return output_path(root, name)


def apply_approved(root: Path, proposal: dict) -> dict:
    operation = proposal["operation"]
    if proposal.get("decision", {}).get("action") != "approve":
        return {"status": "blocked", "proposal": proposal["id"], "reason": "Explicit scoped approval missing"}
    if operation == "manual_remediation":
        return {"status": "recorded", "proposal": proposal["id"], "effect": "manual approval recorded"}
    if operation != "workcopy_replace":
        return {"status": "blocked", "proposal": proposal["id"], "reason": "Operation has no enabled runtime effect"}
    try:
        return _publish_copy(root, proposal)
    except (OSError, ValueError, KeyError) as exc:
        return {"status": "blocked", "proposal": proposal["id"], "reason": str(exc)}


def _publish_copy(root, proposal):
    replacement = _source(root, proposal["replacement"])
    if sha256(replacement) != proposal["after_sha256"] or _copy_inputs(root, replacement) != proposal["copy_inputs"]:
        raise ValueError("Approved content changed before copying")
    scope = f'work/{proposal["id"]}'
    destination = copy_root(root, scope)
    destination.parent.mkdir(parents=True, exist_ok=True)
    receipt = {"status": "applied", "proposal": proposal["id"], "working_copy": scope,
               "target": proposal["target"], "before_sha256": proposal["before_sha256"],
               "after_sha256": proposal["after_sha256"], "rollback": proposal["rollback"],
               "input_hashes": proposal["copy_inputs"]}
    expected = {**proposal["copy_inputs"], proposal["target"]: proposal["after_sha256"]}
    if destination.is_dir():
        prior = load(destination / "change-receipt.json")
        observed = source_manifest(destination, [inside(destination, path) for path in expected])
        if prior != receipt or expected != observed:
            raise ValueError("Existing receipt or working copy disagrees with the approved scope")
        return receipt
    with TemporaryDirectory(dir=destination.parent, prefix=".staging-") as element:
        staged = Path(element) / "copy"
        for relative in proposal["copy_inputs"]:
            input_file = replacement if relative == proposal["target"] else inside(root, relative)
            if input_file != input_file.resolve():
                raise ValueError("Linked source in authorized copy")
            target = staged / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(input_file, target)
            if sha256(target) != expected[relative]:
                raise ValueError(f"Approved bytes changed while copying {relative}")
            with target.open("rb+") as stream:
                os.fsync(stream.fileno())
        if _copy_inputs(root, replacement) != proposal["copy_inputs"]:
            raise ValueError("Original input changed during copy")
        save(staged / "change-receipt.json", receipt)
        os.rename(staged, destination)
    return receipt
