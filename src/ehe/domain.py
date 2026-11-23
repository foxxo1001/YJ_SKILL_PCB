"""Deterministic electrical and budget checks over declared SI-unit inputs."""
import math
from collections import Counter
from dataclasses import asdict
from datetime import date
from pathlib import Path

from .io import load, sha256
from .models import Evidence, Finding

POWER_STATES = {"startup", "peak", "typical", "idle", "sleep", "recovery"}
COST_GROUPS = {"components", "pcb", "assembly", "shipping", "test_loss", "rework"}


def number(value, field="value", minimum=None, maximum=None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError(f"{field} outside [{minimum}, {maximum}]: {value}")
    return float(value)


def n(row: dict, key: str, minimum=0, maximum=None) -> float:
    return number(row[key], key, minimum, maximum)


def result(status: str, metrics: dict | None = None, findings: list | None = None,
           missing: list | None = None) -> dict:
    return {"status": status, "metrics": metrics or {}, "findings": findings or [],
            "missing_evidence": missing or []}


def finding(code: str, category: str, mechanism: str, scope: str, action: str,
            verify: str, severity="P1", status="confirmed") -> dict:
    return asdict(Finding(code, category, severity, status, {"object": scope}, mechanism, action, verify))


def rails(rows: list[dict]) -> dict:
    if not rows:
        return result("inconclusive", missing=["rail peak/continuous loads and ratings"])
    findings, margins = [], {}
    for row in rows:
        name = row["id"]
        if name in margins:
            raise ValueError(f"Duplicate rail: {name}")
        peak = n(row, "peak_a")
        limit = n(row, "limit_a")
        voltage = n(row, "voltage_v")
        expected = n(row, "typical_a") if "typical_a" in row else peak
        if expected > peak:
            raise ValueError(f"{name}: typical_a exceeds peak_a")
        margins[name] = {"peak_margin_a": limit - peak, "peak_load_w": voltage * peak,
                         "typical_load_w": voltage * expected}
        if peak > limit:
            findings.append(finding("RAIL-PEAK", "power", "峰值负载超过电源额定能力", name,
                                    "核对瞬态限流并增加裕量", "测量最坏工况下电流与轨电压", "P0"))
    return result("fail" if findings else "pass", margins, findings)


def pins(rows: list[dict]) -> dict:
    if not rows:
        return result("inconclusive", missing=["MCU/FPGA pin map and voltage limits"])
    assignments = Counter((row["device"], str(row["pin"])) for row in rows)
    findings, missing = [], []
    for (device, pin), count in assignments.items():
        if count > 1:
            findings.append(finding("PIN-DUP", "pin_map", "同一物理引脚被多次分配", f"{device}:{pin}",
                                    "核对复用、分配及板上连接", "对照封装 pin map 和固件配置"))
    for row in rows:
        if "signal_v" not in row:
            continue
        scope = f'{row["device"]}:{row["pin"]}'
        if "allowed_v" not in row or not row.get("source"):
            missing.append(f"{scope}: applicable input limit and source")
        elif n(row, "signal_v") > n(row, "allowed_v"):
            findings.append(finding("PIN-VOLTAGE", "pin_map", "信号电压超过所填工况输入限制", scope,
                                    "核对适用输入电压并加入电平转换", "复核数据手册工作条件与上电顺序", "P0"))
    return result("fail" if findings else "inconclusive" if missing else "pass",
                  {"assignments": len(assignments)}, findings, missing)


def bom(rows: list[dict]) -> dict:
    if not rows:
        return result("inconclusive", missing=["BOM with reference designators"])
    refs, findings = Counter(), []
    for i, row in enumerate(rows, 1):
        designators = row["references"]
        if not isinstance(designators, list) or not designators or any(not isinstance(x, str) or not x.strip() for x in designators):
            raise ValueError(f"BOM row {i}: references must be a nonempty list of designators")
        refs.update(ref.strip().upper() for ref in designators)
        quantity = n(row, "quantity")
        if quantity != len(designators):
            findings.append(finding("BOM-QTY", "bom", "数量与位号数不一致", ",".join(designators),
                                    "修正 BOM 数量", "比较网表与 BOM 位号"))
    for ref, count in refs.items():
        if count > 1:
            findings.append(finding("BOM-DUP", "bom", "位号在 BOM 重复出现", ref,
                                    "合并或修正位号", "按位号与设计交叉核对"))
    return result("fail" if findings else "pass", {"unique_refs": len(refs)}, findings)


def energy(data: dict) -> dict:
    states = data.get("states", [])
    by_id = {s["id"]: s for s in states}
    if len(by_id) != len(states):
        raise ValueError("Duplicate power state")
    if missing := POWER_STATES - by_id.keys():
        return result("inconclusive", missing=[f"power state: {key}" for key in sorted(missing)])
    duties = [n(s, "duty", 0, 1) for s in states]
    if not math.isclose(sum(duties), 1.0, abs_tol=1e-7):
        raise ValueError("Power state duty fractions must sum to 1")
    powers = {}
    for row in states:
        efficiency = n(row, "efficiency", 0, 1)
        if not efficiency:
            raise ValueError("Power-state efficiency must be positive")
        powers[row["id"]] = (n(row, "voltage_v") * (n(row, "load_a") + n(row, "peripheral_a"))
                             / efficiency + n(row, "input_idle_w"))
    average = sum(duty * powers[row["id"]] for duty, row in zip(duties, states))
    metrics = {"average_input_w": average, "state_input_w": powers, "runtime_estimate_h": None}
    battery = data.get("battery", {})
    keys = {"usable_wh", "derating", "temperature_c", "cutoff_v", "source", "load_profile"}
    if not keys <= battery.keys() or not battery["source"] or not battery["load_profile"] or average <= 0:
        return result("inconclusive", metrics, missing=["usable battery energy at temperature, load and cutoff"])
    metrics["runtime_estimate_h"] = n(battery, "usable_wh") * n(battery, "derating", 0, 1) / average
    number(battery["temperature_c"], "temperature_c")
    n(battery, "cutoff_v")
    if "min_runtime_h" in data:
        metrics["runtime_margin_h"] = metrics["runtime_estimate_h"] - n(data, "min_runtime_h")
        if metrics["runtime_margin_h"] < 0:
            return result("fail", metrics, [finding("ENERGY-RUNTIME", "energy", "负载模型估算续航低于目标",
                                                   "battery", "减少状态功耗或调整电源", "按完整负载谱实测续航")])
    return result("pass", metrics)


def cost(data: dict) -> dict:
    items = data.get("items", [])
    categories = {row["category"] for row in items}
    missing = sorted(COST_GROUPS - categories)
    if not items or not data.get("currency") or not data.get("basis"):
        return result("inconclusive", missing=["itemized cost, currency and tax/shipping basis"])
    total, lead = 0.0, 0.0
    for index, row in enumerate(items, 1):
        label = row.get("id", index)
        if not row.get("source") or not row.get("supplier") or not row.get("quote_date"):
            missing.append(f"quote provenance: {label}")
        else:
            date.fromisoformat(row["quote_date"])
        quantity = n(row, "quantity")
        order_quantity = max(quantity, n(row, "min_order") if "min_order" in row else 0)
        if row.get("tiers"):
            eligible = [tier for tier in row["tiers"] if n(tier, "min_qty") <= order_quantity]
            if not eligible:
                raise ValueError(f"No eligible price tier: {label}")
            price = n(max(eligible, key=lambda tier: tier["min_qty"]), "unit_price")
        else:
            price = n(row, "unit_price")
        total += price * order_quantity
        lead = max(lead, n(row, "lead_days"))
        if "stock" in row and n(row, "stock") < order_quantity:
            missing.append(f"stock coverage: {label}")
    metrics = {"total": total, "currency": data["currency"], "lead_days": lead, "basis": data["basis"]}
    findings = []
    for key, value in (("max_total", total), ("max_lead_days", lead)):
        if key in data:
            metrics[f"{key}_margin"] = n(data, key) - value
            if value > data[key]:
                findings.append(finding(f"COST-{key.upper()}", "cost", "报价或交期超出约束", key,
                                        "重选方案或核对可交付批量", "复核供应商报价、库存和到货承诺"))
        else:
            missing.append(key)
    return result("fail" if findings else "inconclusive" if missing else "pass", metrics, findings, missing)


CHECKS = {"rails": rails, "pins": pins, "bom": bom, "energy": energy, "cost": cost}


def status_of(checks) -> str:
    statuses = {check["status"] for check in checks}
    for status in ("fail", "blocked", "inconclusive", "not_run"):
        if status in statuses:
            return status
    return "pass" if statuses == {"pass"} else "inconclusive"


def run_domain(path: Path, checks=CHECKS, *, include_missing=True) -> dict:
    if not path.is_file():
        return {"status": "inconclusive", "checks": {}, "evidence": [], "findings": [],
                "missing_evidence": [f"engineering input: {path}"]}
    content = sha256(path)
    data = load(path)
    if content != sha256(path):
        raise ValueError("Engineering model changed while reading")
    if not isinstance(data, dict):
        raise ValueError("Engineering input must be an object")
    results, evidence = {}, []
    for name, function in checks.items():
        if not include_missing and name not in data:
            continue
        try:
            outcome = function(data.get(name, [] if name in {"rails", "pins", "bom"} else {}))
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError) as exc:
            outcome = result("inconclusive", missing=[f"{name}: {type(exc).__name__}: {exc}"])
        ev = asdict(Evidence.file(path, f"/{name}", "derived", f"ehe.{name}", content_sha256=content))
        for issue in outcome["findings"]:
            issue["evidence_ids"] = [ev["id"]]
        outcome["evidence_ids"] = [ev["id"]]
        evidence.append(ev)
        results[name] = outcome
    return {"status": status_of(results.values()), "checks": results, "evidence": evidence,
            "findings": [issue for check in results.values() for issue in check["findings"]],
            "missing_evidence": [item for check in results.values() for item in check["missing_evidence"]]}
