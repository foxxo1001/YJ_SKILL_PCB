"""Goal validation, dependency ordering and impact classification."""
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
import re

from ..io import inside

KINDS = {"schematic", "pcb", "budget", "reality", "advanced"}
OPERATIONS = {"manual_remediation", "workcopy_replace", "original_write", "instrument", "manufacturing", "upload"}
IMPACTS = {
    "part": {"pin_map", "footprint", "electrical", "thermal", "procurement", "bom"},
    "board_outline": {"mechanical", "thermal", "harness"},
    "pwm": {"control", "energy", "emi", "thermal"},
    "power_architecture": {"power", "noise", "energy", "cost", "thermal"},
    "pcb": {"pcb", "footprint", "emi", "mechanical", "thermal"},
    "schematic": {"schematic", "electrical", "pin_map", "power"},
    "model": {"electrical", "mechanical", "thermal", "energy", "cost", "control"},
}
TASK_DOMAINS = {
    "schematic": {"schematic", "pin_map", "electrical", "power"},
    "pcb": {"pcb", "footprint", "mechanical"},
    "budget": {"electrical", "power", "pin_map", "bom", "cost", "energy", "procurement"},
    "reality": {"mechanical", "thermal", "energy", "cost", "harness", "power"},
    "advanced": {"emi", "noise", "control", "power", "electrical", "pin_map"},
}
DEFAULTS = {"max_rounds": 5, "max_stalled": 3, "tool_retries": 2, "max_calls": 30, "max_seconds": 600}


def impact(labels) -> list[str]:
    return sorted(set().union(*(IMPACTS.get(label, {label}) for label in labels)))


def _ident(identifier) -> bool:
    return isinstance(identifier, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,47}", identifier) is not None


def goal_plan(data: dict, root: Path) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Goal must be an object")
    allowed = {"name", "tasks", "required", "critical", "physical_required", "change_request", *DEFAULTS}
    if unknown := set(data) - allowed:
        raise ValueError(f"Unknown goal keys: {sorted(unknown)}")
    plan = {"name": data.get("name", "Engineering review"), "physical_required": data.get("physical_required", True)}
    if not isinstance(plan["physical_required"], bool) or not isinstance(plan["name"], str):
        raise ValueError("Goal name/physical_required has invalid type")
    for name, default in DEFAULTS.items():
        value = data.get(name, default)
        low = 0 if name in {"tool_retries", "max_calls", "max_seconds"} else 1
        ceiling = 86400 if name == "max_seconds" else 1000 if name == "max_calls" else 100
        if type(value) is not int or not low <= value <= ceiling:
            raise ValueError(f"{name} requires an integer in [{low}, {ceiling}]")
        plan[name] = value
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 40:
        raise ValueError("A goal requires 1 to 40 tasks")
    indexed = {}
    for task in tasks:
        if not isinstance(task, dict) or set(task) - {"id", "kind", "depends_on"}:
            raise ValueError("Task requires id, kind and optional depends_on")
        task_id, kind, deps = task["id"], task["kind"], task.get("depends_on", [])
        if not _ident(task_id) or task_id in indexed or kind not in KINDS:
            raise ValueError(f"Invalid or duplicate task: {task_id} ({kind})")
        if not isinstance(deps, list) or not all(_ident(dep) for dep in deps) or len(set(deps)) != len(deps):
            raise ValueError(f"Invalid task dependencies: {task_id}")
        indexed[task_id] = {"id": task_id, "kind": kind, "depends_on": deps}
    graph = {key: item["depends_on"] for key, item in indexed.items()}
    if unknown := set().union(*map(set, graph.values())) - indexed.keys():
        raise ValueError(f"Unknown dependency: {sorted(unknown)}")
    try:
        order = list(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        raise ValueError("Task graph contains a cycle") from exc
    plan["tasks"] = [indexed[task] for task in order]
    for key, default in (("required", order),
                         ("critical", [t for t in order if indexed[t]["kind"] in {"pcb", "schematic"}])):
        ids = data.get(key, default)
        if not isinstance(ids, list) or not all(_ident(t) and t in indexed for t in ids) or len(set(ids)) != len(ids):
            raise ValueError(f"Invalid {key} task IDs")
        plan[key] = ids
    if not plan["required"]:
        raise ValueError("At least one task must be required")
    if change := data.get("change_request"):
        if not isinstance(change, dict) or set(change) - {"operation", "target", "replacement", "description", "impact"}:
            raise ValueError("Unsupported change_request keys")
        operation = change.get("operation")
        if operation not in OPERATIONS or not change.get("description") or not isinstance(change["description"], str):
            raise ValueError("Proposed operation requires a supported kind and description")
        for key in ("target", "replacement"):
            if key in change:
                if not isinstance(change[key], str):
                    raise ValueError(f"Change {key} must be project-relative")
                inside(root, change[key])
        labels = change.get("impact", [])
        if not isinstance(labels, list) or not all(isinstance(key, str) and key in IMPACTS for key in labels):
            raise ValueError("Unknown change impact")
        plan["change_request"] = change
    return plan
