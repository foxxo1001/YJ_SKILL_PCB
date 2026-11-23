"""Independent runtime acceptance. Raw tool evidence is evaluated again here."""
from dataclasses import asdict
from pathlib import Path

from ..adapters.kicad import read_native_report
from ..io import load, sha256
from ..models import GateResult
from ..project import output_path


def native_gate(root: Path, name: str, result: dict, inputs: dict) -> tuple[str, str]:
    if result["status"] in {"blocked", "not_run", "inconclusive"}:
        return result["status"], result.get("reason", "Native evidence unavailable")
    try:
        evidence, = result["evidence"]
        raw = Path(evidence["path"])
        if not raw.is_file() or raw != raw.resolve() or not raw.is_relative_to(output_path(root, "eda")):
            raise ValueError("Artifact is outside the native evidence store")
        metadata = load(raw.parent / "execution.json")
        if metadata != result["execution"] or metadata["inputs"] != inputs:
            raise ValueError("Tool transcript does not bind the evaluated input hashes")
        if sha256(raw) != evidence["sha256"] or evidence["kind"] != "tool_verified":
            raise ValueError("Raw tool evidence hash or grade invalid")
        check = result["check"]
        argv = metadata["argv"]
        command = ["sch", "erc"] if check == "erc" else ["pcb", "drc"]
        if argv[1:3] != command or argv[-1] != metadata["target"] or str(raw) != metadata["artifact"]:
            raise ValueError("Unexpected tool command or artifact")
        data = load(raw)
        violations = read_native_report(data, check)
        if (Path(metadata["target"]).name != data["source"] or data["kicad_version"] != metadata["tool_version"]
                or metadata["target"] not in inputs or set(data["included_severities"]) != {"error", "warning"}
                or data["coordinate_units"] != "mm" or any(e["severity"] not in {"error", "warning"} for e in violations)
                or metadata["exit_code"] != (5 if violations else 0)):
            raise ValueError("Independent exit code and report reconciliation failed")
        expected = sorted((e["type"], e["scope"]) for e in violations)
        reported = sorted((e["id"], e["scope"]["object"]) for e in result["findings"])
        if reported != expected:
            raise ValueError("Normalized findings disagree with raw diagnostics")
    except (KeyError, TypeError, OSError, ValueError) as exc:
        return "inconclusive", str(exc)
    return ("fail", f"{len(violations)} native rule violations") if violations else ("pass", "Native rules verified")


def acceptance(root: Path, plan: dict, records: dict) -> list[dict]:
    from ..runtime.tasks import execute, task_inputs

    results = []
    for name in plan["required"]:
        record = records.get(name)
        evidence, missing = [], []
        if not record:
            status, reason = "not_run", "Required task did not run"
        else:
            outcome, kind = record["result"], record["kind"]
            current = task_inputs(root, kind)
            evidence = [item["id"] for item in outcome.get("evidence", [])]
            missing = outcome.get("missing_evidence", [])
            if current != record["inputs"]:
                status, reason = "inconclusive", "Inputs changed since the check; resume on the new revision"
            elif outcome["status"] == "blocked" and not evidence and kind not in {"schematic", "pcb"}:
                status, reason = "blocked", outcome.get("reason", "No executable evidence")
            elif kind in {"schematic", "pcb"}:
                status, reason = native_gate(root, name, outcome, current)
            else:
                try:
                    recomputed = execute(root, kind, 0)
                    def observations(value):
                        return value["status"], value.get("checks", {}), value.get("missing_evidence", [])
                    if observations(recomputed) != observations(outcome):
                        raise ValueError("Deterministic calculation replay differs")
                    status, reason = recomputed["status"], "Recomputed declared-input engineering model"
                except (ValueError, KeyError, OSError, TypeError) as exc:
                    status, reason = "inconclusive", str(exc)
        results.append(asdict(GateResult(name, status, evidence, reason, "ehe-independent-gate", missing)))
    if plan["physical_required"]:
        results.append(asdict(GateResult("physical", "not_run", [], "Independent physical acceptance not recorded",
                                         "ehe-independent-gate", ["reviewed raw measurements and acceptance conditions"])))
    return results
