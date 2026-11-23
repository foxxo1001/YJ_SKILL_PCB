"""Finite engineering loop. Checkpoints precede every external tool attempt."""
from pathlib import Path
from time import monotonic
from uuid import uuid4

from ..doctor import kicad_probe
from ..evalops.gate import acceptance
from ..io import digest, load, now
from ..project import manifest
from .plan import TASK_DOMAINS, goal_plan, impact
from .store import Store, exclusive
from .tasks import execute, task_inputs

TERMINAL = {"CANCELLED", "BUDGET_EXHAUSTED"}


def _environment(plan):
    if any(task["kind"] in {"pcb", "schematic"} for task in plan["tasks"]):
        tool = kicad_probe()
        return {key: tool.get(key) for key in ("status", "path", "version")}
    return {}


def _snapshot(root, plan):
    by_kind = {kind: task_inputs(root, kind) for kind in {t["kind"] for t in plan["tasks"]}}
    return {"files": by_kind, "tool": _environment(plan), "project": manifest(root)}


def _changed_labels(old, new):
    if not old or old["project"] != new["project"]:
        return {"model", "pcb", "schematic"}
    labels = set()
    for kind, files in new["files"].items():
        previous = old["files"].get(kind, {})
        for entry in previous.keys() | files.keys():
            if previous.get(entry) == files.get(entry):
                continue
            suffix = Path(entry).suffix.lower()
            labels.add({".kicad_sch": "schematic", ".kicad_pcb": "pcb",
                        ".kicad_sym": "part", ".kicad_mod": "part",
                        ".kicad_pro": "pcb", ".kicad_dru": "pcb"}.get(suffix, "model"))
    return labels


def _working_root(root, state):
    if not state.get("working_copy"):
        return root
    from .changes import copy_root

    return copy_root(root, state["working_copy"])


def _new(root, plan):
    return {"run_id": str(uuid4()), "project": str(root), "plan": plan, "state": "CREATED",
            "round": 0, "calls": 0, "spent_seconds": 0.0, "results": {}, "active": None,
            "snapshot": None, "attempts": {}, "queue": [], "gates": [], "change": None,
            "created_at": now(), "cost": {"tokens": "unknown", "external": "unknown"}}


def start(root: Path, goal: Path) -> dict:
    root = root.resolve()
    manifest(root)
    plan = goal_plan(load(goal), root)
    with exclusive(root), Store(root, create=True) as store:
        if store.read() is not None:
            raise ValueError("A project run already exists. Use resume or start in a separate project copy.")
        state = _new(root, plan)
        store.commit(state, "created")
        store.commit(state, "planned", {"task_ids": [t["id"] for t in plan["tasks"]]}, transition="PLANNED")
        return _drive(root, store, state)


def status(root: Path) -> dict:
    with Store(root.resolve()) as store:
        if (state := store.read()) is None:
            raise ValueError("No runtime state")
        return state


def _proposal(root, state):
    from .changes import propose

    return propose(root, state["plan"]["change_request"], _snapshot(root, state["plan"]))


def decide(root: Path, proposal_id: str, decision: str, reviewer: str, reason: str) -> dict:
    if decision not in {"approve", "deny"} or not reviewer.strip() or not reason.strip():
        raise ValueError("Approval requires approve/deny, reviewer and reason")
    root = root.resolve()
    with exclusive(root), Store(root) as store:
        state = store.read()
        if not state:
            raise ValueError("No runtime state")
        proposal = state.get("change")
        if state["state"] != "WAITING_APPROVAL" or not proposal or proposal_id != proposal["id"] or proposal.get("decision"):
            raise ValueError("No undecided proposal with that ID")
        if decision == "approve" and _proposal(root, state)["id"] != proposal_id:
            raise ValueError("Proposal inputs changed; resume to obtain a new approval ID")
        proposal["decision"] = {"action": decision, "reviewer": reviewer.strip(), "reason": reason.strip(), "at": now()}
        store.commit(state, f"approval_{decision}", {"proposal": proposal_id, **proposal["decision"]})
        return state


def cancel(root: Path, reason: str) -> dict:
    root = root.resolve()
    with exclusive(root), Store(root) as store:
        state = store.read()
        if not state:
            raise ValueError("No runtime state")
        if state["state"] in TERMINAL or state["state"] == "PASSED":
            raise ValueError(f'Cannot cancel {state["state"]}')
        store.commit(state, "cancelled", {"reason": reason}, transition="CANCELLED")
        return state


def resume(root: Path) -> dict:
    root = root.resolve()
    with exclusive(root), Store(root) as store:
        if (state := store.read()) is None:
            raise ValueError("No runtime state")
        store.project_views(state)
        return _drive(root, store, state)


def _budget_reason(state):
    plan = state["plan"]
    if state["calls"] >= plan["max_calls"]:
        return "task_call_budget"
    if state["spent_seconds"] >= plan["max_seconds"]:
        return "execution_time_budget"
    return None


def _record_dependency(state, task_id, task, fingerprint, inputs, missing, store):
    state["results"][task_id] = {"kind": task["kind"], "fingerprint": fingerprint, "inputs": inputs,
                                 "result": {"status": "blocked", "reason": "task dependencies did not pass",
                                            "missing_evidence": missing}}
    state["queue"].pop(0)
    store.commit(state, "dependency_blocked", {"task": task_id, "dependencies": missing})


def _drive(root, store, state):
    plan = state["plan"]
    phase = state["state"]
    if phase in TERMINAL:
        return state
    if phase == "CREATED":
        store.commit(state, "planned_after_recovery", transition="PLANNED")
        phase = "PLANNED"
    if phase == "PLANNED" and "change_request" in plan:
        state["change"] = _proposal(root, state)
        store.commit(state, "proposal", {"proposal": state["change"]}, transition="WAITING_APPROVAL")
        return state
    if phase == "WAITING_APPROVAL":
        proposal = state["change"]
        updated = _proposal(root, state)
        if not proposal.get("decision"):
            if updated["id"] != proposal["id"]:
                state["change"] = updated
                store.commit(state, "proposal_superseded", {"prior": proposal["id"], "proposal": updated})
            return state
        if proposal["decision"]["action"] == "deny":
            store.commit(state, "approval_rejected", {"proposal": proposal["id"]}, transition="PAUSED")
            return state
        if updated["id"] != proposal["id"]:
            state["change"] = updated
            store.commit(state, "approval_invalidated", {"prior": proposal["id"], "proposal": updated})
            return state
        from .changes import apply_approved

        effect = apply_approved(root, proposal)
        state["change_effect"] = effect
        state["working_copy"] = effect.get("working_copy")
        if effect["status"] == "blocked":
            store.commit(state, "operation_blocked", effect, transition="BLOCKED")
            return state
        store.commit(state, "approval_applied", effect, transition="RUNNING")
        phase = "APPROVED"
    if state.get("change") and (state["change"].get("decision") or {}).get("action") == "deny":
        return state
    if state.get("change_effect", {}).get("status") == "blocked":
        return state

    work = _working_root(root, state)
    current = _snapshot(work, plan)
    old = state["snapshot"]
    if phase in {"RUNNING", "VERIFYING"} and state["active"]:
        active = state["active"]
        state["active"] = None
        state["spent_seconds"] += active["reserved_seconds"]
        store.commit(state, "interrupted_attempt", active)
    changed = current != old
    changed_during_round = changed and phase in {"RUNNING", "VERIFYING"}
    if phase not in {"RUNNING", "VERIFYING"} or changed_during_round:
        if not changed:
            if phase == "PASSED":
                checked = acceptance(work, plan, state["results"])
                if any(check["status"] != "pass" for check in checked):
                    state["gates"] = checked
                    store.commit(state, "evidence_invalidated", {"gates": checked}, transition="PAUSED")
            if phase in {"REVISE", "BLOCKED", "FAILED"}:
                store.commit(state, "no_progress", {"reason": "unchanged inputs, tool and evidence"}, transition="PAUSED")
            return state
        if phase == "VERIFYING" and changed_during_round:
            store.commit(state, "verification_invalidated", transition="PAUSED")
            phase = "PAUSED"
        budget = _budget_reason(state)
        if state["round"] >= plan["max_rounds"] or budget:
            reason = budget or "round_budget"
            if phase == "PLANNED":
                store.commit(state, "started_empty_round", transition="RUNNING")
            store.commit(state, "budget_exhausted", {"reason": reason}, transition="BUDGET_EXHAUSTED")
            return state
        labels = _changed_labels(old, current)
        if state.get("change_effect") and not old:
            labels.update(state["change"].get("impact_labels", []))
        domains = impact(labels)
        state.update(snapshot=current, round=state["round"] + 1, attempts={}, regressions=domains,
                     queue=[t["id"] for t in plan["tasks"]], gates=[],
                     prior_results=state["results"].copy())
        store.commit(state, "round_started", {"round": state["round"], "impact_domains": domains,
                                               "input_hashes": current["files"]},
                     transition=None if phase in {"RUNNING", "APPROVED"} else "RUNNING")
        phase = "RUNNING"

    tasks = {t["id"]: t for t in plan["tasks"]}
    while phase == "RUNNING" and state["queue"]:
        task_id = state["queue"][0]
        task = tasks[task_id]
        kind = task["kind"]
        inputs = current["files"][kind]
        deps = [state["results"].get(dep, {}) for dep in task["depends_on"]]
        fingerprint = digest([kind, inputs, current["project"], [d.get("fingerprint") for d in deps],
                              current["tool"] if kind in {"pcb", "schematic"} else {}])
        if missing := [dep for dep, record in zip(task["depends_on"], deps)
                       if record.get("result", {}).get("status") != "pass"]:
            _record_dependency(state, task_id, task, fingerprint, inputs, missing, store)
            continue
        prior = state["prior_results"].get(task_id, {})
        if (prior.get("fingerprint") == fingerprint and prior["result"]["status"] == "pass"
                and task_id not in plan["critical"] and not TASK_DOMAINS[kind].intersection(state["regressions"])):
            state["queue"].pop(0)
            store.commit(state, "task_reused", {"task": task_id, "fingerprint": fingerprint})
            continue
        if reason := _budget_reason(state):
            store.commit(state, "budget_exhausted", {"reason": reason}, transition="BUDGET_EXHAUSTED")
            return state
        tries = state["attempts"].get(task_id, 0)
        if tries >= min(1 + plan["tool_retries"], plan["max_stalled"]):
            state["results"][task_id] = {"kind": kind, "fingerprint": fingerprint, "inputs": inputs,
                                         "result": {"status": "blocked", "reason": "retry_or_stall_budget"}}
            state["queue"].pop(0)
            store.commit(state, "task_stalled", {"task": task_id, "attempts": tries})
            continue
        left = plan["max_seconds"] - state["spent_seconds"]
        state["calls"] += 1
        state["attempts"][task_id] = tries + 1
        state["active"] = {"task": task_id, "fingerprint": fingerprint, "reserved_seconds": min(left, 120),
                           "attempt": tries + 1}
        store.commit(state, "task_started", state["active"])
        tick = monotonic()
        try:
            outcome = execute(work, kind, left)
        except (ValueError, KeyError, OSError, TypeError) as exc:
            outcome = {"status": "inconclusive", "reason": str(exc), "missing_evidence": ["valid task inputs"]}
        state["spent_seconds"] += monotonic() - tick
        state["active"] = None
        if outcome.get("retryable") and outcome["status"] == "blocked" and tries + 1 < min(1 + plan["tool_retries"], plan["max_stalled"]):
            store.commit(state, "task_retry", {"task": task_id, "reason": outcome.get("reason"),
                                              "artifacts": outcome.get("artifacts")})
            continue
        state["results"][task_id] = {"kind": kind, "fingerprint": fingerprint, "inputs": inputs, "result": outcome}
        state["queue"].pop(0)
        store.commit(state, "task_finished", {"task": task_id, "status": outcome["status"], "fingerprint": fingerprint})
    if phase == "RUNNING":
        store.commit(state, "verification_started", transition="VERIFYING")

    checked = acceptance(work, plan, state["results"])
    state["gates"] = checked
    current_p0 = _risks(state["results"], "P0")
    previous_p0 = _risks(state["prior_results"], "P0")
    newly_introduced = current_p0 - previous_p0 if state["round"] > 1 else set()
    state["edit_proposals"] = [
        {"task": task_id, "target": issue["scope"], "finding": issue["id"], "action": issue["recommended_action"],
         "verification": issue["verification_method"], "severity": issue["severity"],
         "rollback": "Retain the previous user-managed revision and original source hashes"}
        for task_id, record in sorted(state["results"].items())
        for issue in record["result"].get("findings", [])
    ]
    statuses = {row["status"] for row in checked}
    if newly_introduced:
        final = "FAILED"
    elif "fail" in statuses:
        final = "REVISE"
    elif "blocked" in statuses or any(row["status"] == "not_run" and row["id"] != "physical" for row in checked):
        final = "BLOCKED"
    elif statuses == {"pass"}:
        final = "PASSED"
    else:
        final = "PAUSED"
    store.commit(state, "verification_finished", {"gates": checked, "new_p0": sorted(newly_introduced)},
                 transition=final)
    return state


def _risks(records, severity):
    return {digest([name, issue["id"], issue["scope"]]) for name, record in records.items()
            for issue in record["result"].get("findings", []) if issue["severity"] == severity}
