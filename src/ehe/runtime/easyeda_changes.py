"""Persist, approve and publish a PCB document replacement into a separate revision."""
import difflib
from itertools import islice
import os
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory

from .plan import impact
from .store import exclusive
from ..adapters import easyeda_source as source
from ..adapters.easyeda_design import _record
from ..domain import number
from ..io import atomic_write, digest, load, now, save, sha256
from ..project import output_path, source_manifest

MAX_DOCUMENT = 16 * 1024 * 1024


def _manifest(index):
    return source_manifest(index.parent, source.project_files(index))


def _state_path(root, proposal, filename):
    if not re.fullmatch(r"eda-[0-9a-f]{24}", proposal):
        raise ValueError("Invalid EasyEDA proposal ID")
    return output_path(root, f"easyeda-changes/{proposal}/{filename}")


def _proposal(index, ident):
    proposal = load(_state_path(index.parent, ident, "proposal.json"))
    if "id" in proposal and proposal["id"] == ident:
        body = {key: value for key, value in proposal.items() if key != "id"}
        if "eda-" + digest(body)[:24] == ident and body["index"] == index.name:
            return proposal
    raise ValueError("Corrupt or unrelated EasyEDA proposal")


def _verify_candidate(path, board):
    header, rows = source._matching_records(path, board)
    meta, _ = rows.get(("META", "META"), ({}, 0))
    if not header or (meta.get("board") or None) != board.board:
        raise ValueError("Replacement must preserve the selected PCB UUID and board association")
    documents = [row for row in source._records(path) if row[0]["type"] == "DOCHEAD"
                 and (row[1] or {}).get("docType") == "PCB"]
    if len(documents) != 1:
        raise ValueError("Replacement must contain exactly one PCB document")


def propose(project: Path, pcb: str | None, replacement: Path, *, expected=None):
    index = source.index_at(project)
    root = index.parent
    replacement = replacement.absolute()
    if (replacement.resolve() != replacement or replacement.suffix.lower() != ".epcb2"
            or not replacement.is_file() or replacement.stat().st_size > MAX_DOCUMENT):
        raise ValueError("Replacement requires an ordinary .epcb2 file no larger than 16 MiB")
    with exclusive(root):
        baseline = _manifest(index)
        if expected is not None and expected != baseline:
            raise ValueError("Original project changed while constructing the edit")
        board, _ = source.selection(source.documents(index)[1], pcb)
        files = source.project_files(index)
        canonical = source.geometry(index, board, files, baseline)
        target = Path(canonical["project"]["path"])
        if target.stat().st_size > MAX_DOCUMENT or replacement in files:
            raise ValueError("Use a separate PCB candidate outside the original EDA documents")
        after = sha256(replacement)
        before = sha256(target)
        if before == after:
            raise ValueError("Replacement is identical to the original PCB")
        _verify_candidate(replacement, board)
        relative = target.relative_to(root).as_posix()
        diff = difflib.unified_diff(target.read_text(encoding="utf-8-sig").splitlines(),
                                    replacement.read_text(encoding="utf-8-sig").splitlines(),
                                    fromfile=relative, tofile="candidate.epcb2", lineterm="")
        lines = list(islice(diff, 251))
        preview = "\n".join(lines[:250])
        body = {"index": index.name, "pcb": board.id, "target": relative, "before_sha256": before,
                "candidate_sha256": after, "manifest": baseline, "preview": preview[:24000],
                "preview_truncated": len(lines) > 250 or len(preview) > 24000,
                "impact": impact(("pcb", "schematic")),
                "rollback": "Create a separate revision with the approved baseline PCB and assets"}
        ident = "eda-" + digest(body)[:24]
        candidate = _state_path(root, ident, "candidate.epcb2")
        metadata = _state_path(root, ident, "proposal.json")
        if not metadata.is_file():
            metadata.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(replacement, candidate)
            if sha256(candidate) != after or _manifest(index) != baseline:
                raise ValueError("Inputs changed while recording the proposal")
            save(metadata, {"id": ident, **body})
        elif sha256(candidate) != after:
            raise ValueError("Staged PCB candidate does not match its hash")
        return {"status": "WAITING_APPROVAL", "id": ident, **body, "candidate": str(candidate),
                "proposal_file": str(metadata)}


def propose_edits(project: Path, pcb: str | None, edit_file: Path):
    index = source.index_at(project)
    manifest = _manifest(index)
    board, _ = source.selection(source.documents(index)[1], pcb)
    canonical = source.geometry(index, board, source.project_files(index), manifest)
    original = Path(canonical["project"]["path"])
    plan = load(edit_file)
    if not isinstance(plan, dict) or set(plan) != {"trace_width_mm"}:
        raise ValueError("EasyEDA PCB edit plan requires a trace_width_mm mapping")
    widths = plan["trace_width_mm"]
    if not isinstance(widths, dict) or not 1 <= len(widths) <= 50:
        raise ValueError("Request 1–50 changes to explicitly identified PCB traces")
    if original.stat().st_size > MAX_DOCUMENT:
        raise ValueError("Selected PCB exceeds the editable document size")
    _verify_candidate(original, board)
    _, rows = source._matching_records(original, board)
    records = list(source._records(original))
    end = len(records)
    armed = False
    for head, body, line in records:
        if head["type"] != "DOCHEAD":
            continue
        if armed:
            end = line - 1
            break
        armed = (body or {}).get("docType") == "PCB" and (body or {}).get("uuid") == board.id
    else:
        end = records[-1][2]
    ticket = max(int(head.get("ticket") or 0) for head, _, _ in records)
    changed = []
    for ident, width in widths.items():
        if ("LINE", ident) not in rows:
            raise ValueError(f"No routed LINE primitive on the selected PCB with ID {ident!r}")
        body, _ = rows[("LINE", ident)]
        if body.get("locked") or not body.get("netName"):
            raise ValueError(f"Trace {ident} is locked or has no assigned net")
        mil = round(number(width, "trace width (mm)", 0.127, 8) / source.MIL_MM, 6)
        if abs(number(body["width"]) - mil) <= 1e-6:
            raise ValueError(f"Trace {ident} already has the requested width")
        ticket += 1
        changed.append(_record("LINE", ticket, {**body, "width": mil}, ident))
    old = original.read_text(encoding="utf-8-sig").splitlines()
    if end and not old[end - 1].endswith("|"):
        old[end - 1] += "|"
    with TemporaryDirectory(prefix="ehe-easyeda-candidate-") as folder:
        candidate = Path(folder) / "candidate.epcb2"
        atomic_write(candidate, "\n".join([*old[:end], *changed, *old[end:]]) + "\n")
        return propose(index, board.id, candidate, expected=manifest)


def _current(index, proposal):
    candidate = _state_path(index.parent, proposal["id"], "candidate.epcb2")
    if _manifest(index) != proposal["manifest"] or sha256(candidate) != proposal["candidate_sha256"]:
        raise ValueError("Approval scope is stale: original revision or candidate checksum changed")
    return candidate


def decide(project: Path, ident: str, action: str, reviewer: str, reason: str):
    if action not in {"approve", "deny"} or not reviewer.strip() or not reason.strip():
        raise ValueError("Approval requires a reviewer, an explicit decision and a reason")
    index = source.index_at(project)
    with exclusive(index.parent):
        proposal = _proposal(index, ident)
        decision = _state_path(index.parent, ident, "decision.json")
        if decision.exists():
            raise ValueError("This proposal already has a recorded decision")
        if action == "approve":
            _current(index, proposal)
        receipt = {"proposal": ident, "action": action, "reviewer": reviewer.strip(), "reason": reason.strip(),
                   "candidate_sha256": proposal["candidate_sha256"], "at": now()}
        save(decision, receipt)
        return {"status": "recorded", **receipt, "decision_file": str(decision)}


def _copy_revision(root, destination, files, replacement=None, receipt=None):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent, prefix=".ehe-publish-") as folder:
        stage = Path(folder) / "revision"
        for relative, sha in files.items():
            src = replacement[1] if replacement and relative == replacement[0] else root / relative
            dest = stage / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            if sha256(dest) != sha:
                raise ValueError(f"Approved inputs changed while copying {relative}")
        if receipt:
            save(stage / "change-receipt.json", receipt)
        os.rename(stage, destination)


def revise(project: Path, ident: str, *, rollback=None):
    from ..review import full_review

    index = source.index_at(project)
    root = index.parent
    with exclusive(root):
        proposal = _proposal(index, ident)
        approval_path = _state_path(root, ident, "decision.json")
        approval = load(approval_path) if approval_path.is_file() else {}
        if (approval.get("action") != "approve" or approval.get("proposal") != ident
                or approval.get("candidate_sha256") != proposal["candidate_sha256"]):
            return {"status": "blocked", "proposal": ident, "reason": "Explicit approval for this revision is missing"}
        changed_dir = output_path(root, f"work/{ident}")
        restoring = rollback is not None
        if restoring and (not rollback.strip() or not (changed_dir / "change-receipt.json").is_file()):
            raise ValueError("Rollback requires a published working revision and a reason")
        baseline = _state_path(root, ident, "baseline")
        replacement = None
        if not restoring:
            candidate = _current(index, proposal)
            if not baseline.exists():
                _copy_revision(root, baseline, proposal["manifest"])
            _current(index, proposal)
            replacement = (proposal["target"], candidate)
        if not baseline.is_dir() or _manifest(baseline / index.name) != proposal["manifest"]:
            raise ValueError("Approved baseline backup is missing or has changed")
        suffix = "baseline" if restoring else "changed"
        directory = output_path(root, f"work/{ident}-baseline") if restoring else changed_dir
        expected = dict(proposal["manifest"])
        if not restoring:
            expected[proposal["target"]] = proposal["candidate_sha256"]
        receipt = {"proposal": ident, "revision": suffix, "hashes": expected, "approval": approval,
                   "rollback_reason": rollback, "source": str(index)}
        receipt_path = directory / "change-receipt.json"
        if not directory.exists():
            _copy_revision(baseline, directory, expected, replacement, receipt)
        elif load(receipt_path) != receipt or _manifest(directory / index.name) != expected:
            raise ValueError("Existing working revision or receipt differs from the approved scope")
        prior = directory / "verification.json"
        if prior.is_file():
            previous = load(prior)
            if previous["status"] not in {"blocked", "inconclusive"}:
                return previous
        report = full_review(directory / index.name, pcb=proposal["pcb"], design_verification=True)
        checked = report["coverage"]
        outcome = {"status": report["eda_status"], "write_status": "rolled_back" if restoring else "applied",
                   "proposal": ident, "project": str(directory / index.name), "receipt": str(receipt_path),
                   "report": report["report_path"], "drc_status": checked["drc"]["status"],
                   "erc_status": checked["erc"]["status"], "parity_status": checked["schematic_parity"]["status"]}
        save(prior, outcome)
        return outcome
