"""Human-readable workbench report from authoritative runtime state."""
from pathlib import Path

from .runtime.engine import status


def render(root: Path) -> str:
    state = status(root)
    run = state["run_id"]
    lines = [f'# {state["plan"]["name"]}', "", f'运行 {run} · {state["state"]} · 第 {state["round"]} 轮',
             "", f'工程 {state["project"]}', "", "## 验收", "",
             "| 项目 | 状态 | 证据 | 依据 |", "|---|---|---|---|"]
    for gate in state["gates"]:
        lines.append(f'| {gate["id"]} | {gate["status"]} | {", ".join(gate["evidence_ids"])} | {gate["reason"]} |')
        if gate["missing_evidence"]:
            lines.append(f'| {gate["id"]} 缺失证据 | | | {", ".join(gate["missing_evidence"])} |')
    if not state["gates"]:
        lines.append("| 验收 | not_run | | 等待执行 |")
    lines += ["", "## 工程检查与证据", ""]
    for task, record in state["results"].items():
        result = record["result"]
        lines += [f'### {task} · {result["status"]} · {result.get("level", "派生模型")}', ""]
        for group, check in result.get("checks", {}).items():
            lines.append(f'- {group} · {check["status"]} · {check["metrics"]}')
        for issue in result.get("findings", []):
            lines.append(f'- {issue["severity"]} {issue["id"]} · {issue["scope"]} · {issue["mechanism"]}。'
                         f'{issue["recommended_action"]}。验证方法为{issue["verification_method"]}。')
        for ev in result.get("evidence", []):
            lines.append(f'- {ev["id"]} · {ev["kind"]} · {ev["path"]} · {ev["locator"]} · SHA-256 {ev["sha256"]}')
        lines.append("")
    lines += ["## 整改与回归", "", f'影响域 {"、".join(state.get("regressions", [])) or "待定"}。',
              f'任务尝试 {state["calls"]} 次，执行预算已计 {state["spent_seconds"]:.3f} 秒。',
              "", "审计入口为 .ehe/run.json、.ehe/events.jsonl 和 .ehe/artifacts.json。"]
    if state.get("change"):
        decision = state["change"].get("decision", {})
        lines.append(f'变更 {state["change"]["id"]} · {state["change"]["description"]} · {decision or "等待审批"}')
    return "\n".join(lines) + "\n"
