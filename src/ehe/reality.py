"""Declared-condition mechanical, thermal, supply and optimization models."""
from itertools import combinations


from .domain import cost, energy, finding, n, number, result


def vector(row, key, length, minimum=None):
    data = row[key]
    if not isinstance(data, list) or len(data) != length:
        raise ValueError(f"{key}: expected {length} coordinates")
    return [number(value, key, minimum) for value in data]


def aabb(row, expand=True):
    low, high = vector(row, "min_mm", 3), vector(row, "max_mm", 3)
    if any(a >= b for a, b in zip(low, high)):
        raise ValueError("AABB must have positive extents")
    tol = vector(row, "tolerance_mm", 3, 0) if "tolerance_mm" in row else [0.0] * 3
    scale = 1 if expand else -1
    low = [value - scale * delta for value, delta in zip(low, tol)]
    high = [value + scale * delta for value, delta in zip(high, tol)]
    if any(a >= b for a, b in zip(low, high)):
        raise ValueError("Tolerance collapses AABB")
    return low, high


def overlaps(a, b) -> bool:
    return all(max(lo, lo2) < min(hi, hi2) for lo, hi, lo2, hi2 in zip(*a, *b))


def enclosure(data: dict) -> dict:
    """World-coordinate AABB screening at declared worst tolerances."""
    missing = [name for name in ("enclosure", "board", "source", "coordinate_frame") if not data.get(name)]
    if missing:
        return result("inconclusive", missing=missing)
    usable = aabb(data["enclosure"], expand=False)
    board = aabb(data["board"])
    groups = {"board": [("board", board, None)]}
    for kind in ("parts", "connector_sweep", "harness", "tool_access"):
        if kind not in data:
            missing.append(f"{kind} inventory or explicit empty list")
        groups[kind] = [(row["id"], aabb(row), row.get("owner")) for row in data.get(kind, [])]
    ids = [ident for group in groups.values() for ident, _, _ in group]
    if len(ids) != len(set(ids)):
        raise ValueError("Mechanical object IDs must be unique")
    dimensions = [b - a for a, b in zip(*board)]
    findings, margins = [], {}
    verify = "复核公差叠加并在实物上执行装配和插拔检查"
    for group, entries in groups.items():
        for ident, obj, _ in entries:
            margin = min(min(b - a, d - c) for a, b, c, d in zip(usable[0], obj[0], obj[1], usable[1]))
            margins[ident] = margin
            if margin < 0:
                code = {"parts": "MECH-HEIGHT" if obj[0][2] < usable[0][2] or obj[1][2] > usable[1][2]
                        else "MECH-PART", "connector_sweep": "MECH-CONNECTOR"}.get(group, "MECH-ENVELOPE")
                findings.append(finding(code, "mechanical", f"{group} 包络在公差下越过可用空间",
                                        ident, "调整坐标或外壳边界", verify))
    parts = groups["parts"]
    access = groups["connector_sweep"] + groups["harness"] + groups["tool_access"]
    pairs = list(combinations(parts, 2)) + [(moving, part) for moving in access for part in parts]
    for (one, box, owner), (two, other, _) in pairs:
        if owner != two and overlaps(box, other):
            findings.append(finding("MECH-COLLISION", "mechanical", "声明的实体/可达包络相交",
                                    f"{one}/{two}", "调整布置以及配接或装配顺序", verify))
    for keepout in data.get("keepouts", []):
        box = aabb(keepout)
        for group in keepout.get("applies_to", list(groups)):
            for ident, obj, _ in groups[group]:
                if overlaps(obj, box):
                    findings.append(finding("MECH-KEEPOUT", "mechanical", "对象进入声明的禁布空间",
                                            f'{ident}/{keepout["id"]}', "迁移对象或检查实际禁区", verify))
    metrics = {"board_envelope_mm": dimensions, "board_box_mm3": dimensions[0] * dimensions[1] * dimensions[2],
               "enclosure_usable_mm": [b - a for a, b in zip(*usable)],
               "connector_sweep_boxes_mm": {name: box for name, box, _ in groups["connector_sweep"]},
               "min_clearance_mm": margins, "model": "axis-aligned envelopes"}
    if "mass_g" in data and "limit_mass_g" in data:
        metrics["mass_margin_g"] = n(data, "limit_mass_g") - n(data, "mass_g")
        if metrics["mass_margin_g"] < 0:
            findings.append(finding("MECH-MASS", "mechanical", "总质量超过限制",
                                    "assembly", "重新分配质量", "称量完整含线束和紧固件的装配体"))
    return result("fail" if findings else "inconclusive" if missing else "pass", metrics, findings, missing)


def junction(data: dict) -> dict:
    """Steady-state junction estimate with declared effective thermal boundary."""
    rows = data.get("devices", [])
    if not rows:
        return result("inconclusive", missing=["device loss, ambient and effective thermal model"])
    findings, metrics, missing = [], {}, []
    for row in rows:
        ident = row["id"]
        if not row.get("source") or not row.get("conditions"):
            missing.append(f"{ident}: thermal source and boundary conditions")
            continue
        loss = n(row, "dissipation_w")
        if "rds_on_hot_ohm" in row:
            loss += n(row, "rms_a") ** 2 * n(row, "rds_on_hot_ohm") + n(row, "switching_loss_w")
        theta = n(row, "theta_effective_c_per_w")
        if theta <= 0:
            raise ValueError("Effective thermal resistance must be positive")
        temp = number(row["ambient_max_c"], "ambient_max_c") + loss * theta
        margin = number(row["limit_tj_c"], "limit_tj_c") - temp
        metrics[ident] = {"junction_estimate_c": temp, "margin_c": margin, "dissipation_w": loss,
                          "sensitivity_c_per_w": theta, "conditions": row["conditions"]}
        if margin < 0:
            findings.append(finding("THERMAL-TJ", "thermal", "最坏环境下结温估算超过工作上限",
                                    ident, "降低损耗或改善真实热路径", "在指定环境下测温并校验有效热阻", "P0"))
    return result("fail" if findings else "inconclusive" if missing else "pass", metrics, findings, missing)


def transient(data: dict) -> dict:
    """Thevenin source plus explicitly declared incremental capacitor droop."""
    required = {"source", "conditions", "open_circuit_min_v", "battery_max_ohm",
                "harness_max_ohm", "connector_max_ohm", "peak_input_a", "required_min_v"}
    missing = sorted(required - data.keys())
    if missing or not data.get("source") or not data.get("conditions"):
        return result("inconclusive", missing=missing or ["battery source and low-charge conditions"])
    resistance = sum(n(data, key) for key in ("battery_max_ohm", "harness_max_ohm", "connector_max_ohm"))
    current = n(data, "peak_input_a")
    drop = current * resistance
    extra = 0.0
    if "incremental_cap_branch" in data:
        capacitor = data["incremental_cap_branch"]
        cap = n(capacitor, "min_f")
        if cap <= 0:
            raise ValueError("Capacitance must be positive")
        delta = n(capacitor, "delta_a")
        extra = delta * (n(capacitor, "max_esr_ohm") + n(capacitor, "hold_time_s") / cap)
    min_v = n(data, "open_circuit_min_v") - drop - extra
    margin = min_v - n(data, "required_min_v")
    metrics = {"estimated_min_v": min_v, "margin_v": margin, "resistive_drop_v": drop,
               "additional_droop_v": extra, "sensitivity_v_per_ohm": -current, "conditions": data["conditions"]}
    findings = []
    if margin < 0:
        findings.append(finding("BATTERY-SAG", "power", "峰值电流与等效串联阻抗使最小输入电压不足",
                                "input", "降低供电路径损耗、峰值负载或选用适当电源",
                                "按低电量、低温与峰值负载同步测输入和复位", "P0"))
    return result("fail" if findings else "pass", metrics, findings)


def tolerance(data: dict) -> dict:
    """Worst-case resistor-divider interval with linear temperature-coefficient bounds."""
    missing = [name for name in ("upper", "lower", "source", "v_in_min", "v_in_max", "delta_temp_c",
                                 "required_min_v", "required_max_v") if name not in data]
    if missing or not data.get("source"):
        return result("inconclusive", missing=missing or ["tolerance evidence source"])
    def limits(row):
        nominal = n(row, "ohm")
        spread = n(row, "tolerance", 0, 1) + n(row, "tempco_abs_ppm_per_c") * n(data, "delta_temp_c") / 1e6
        if nominal <= 0 or spread >= 1:
            raise ValueError("Resistance tolerance predicts nonpositive resistance")
        return nominal * (1 - spread), nominal * (1 + spread)
    r1_low, r1_high = limits(data["upper"])
    r2_low, r2_high = limits(data["lower"])
    vin_low, vin_high = n(data, "v_in_min"), n(data, "v_in_max")
    lo, hi = n(data, "required_min_v"), n(data, "required_max_v")
    if vin_low > vin_high or lo > hi:
        raise ValueError("Inverted voltage range")
    out_min = vin_low * r2_low / (r1_high + r2_low)
    out_max = vin_high * r2_high / (r1_low + r2_high)
    metrics = {"min_v": out_min, "max_v": out_max, "lower_margin_v": out_min - lo,
               "upper_margin_v": hi - out_max}
    findings = []
    if out_min < lo or out_max > hi:
        findings.append(finding("TOL-DIVIDER", "tolerance", "阻值公差和温漂使输出区间落在规格之外",
                                "divider", "调整标称比或降低公差", "对温度和输入变化做测量或校准"))
    return result("fail" if findings else "pass", metrics, findings)


def pareto(data: dict) -> dict:
    """Filter hard requirements, then compute non-dominance over comparable objectives."""
    objectives = data.get("objectives", {})
    candidates = data.get("candidates", [])
    constraints = data.get("hard_constraints", {})
    if not objectives or not candidates or not constraints:
        return result("inconclusive", missing=["objectives, hard constraints and candidate data"])
    if any(direction not in {"min", "max"} for direction in objectives.values()):
        raise ValueError("Objective direction must be min or max")
    viable, rejected, unknown, margins = {}, {}, {}, {}
    for row in candidates:
        ident = row["id"]
        if ident in margins:
            raise ValueError("Duplicate candidate")
        metrics = row["metrics"]
        margins[ident], failed, missing = {}, [], set()
        for metric, bounds in constraints.items():
            if not bounds or set(bounds) - {"min", "max"}:
                raise ValueError(f"Invalid hard constraint: {metric}")
            if metric not in metrics or not row.get("sources", {}).get(metric):
                missing.add(metric)
                continue
            value = number(metrics[metric], metric)
            entry = {}
            if "min" in bounds:
                entry["min"] = value - number(bounds["min"], metric)
            if "max" in bounds:
                entry["max"] = number(bounds["max"], metric) - value
            if any(delta < 0 for delta in entry.values()):
                failed.append(metric)
            margins[ident][metric] = entry
        missing.update(metric for metric in objectives if metric not in metrics or not row.get("sources", {}).get(metric))
        if failed:
            rejected[ident] = failed
        elif missing:
            unknown[ident] = sorted(missing)
        else:
            viable[ident] = tuple(number(metrics[key], key) * (1 if direction == "min" else -1)
                                  for key, direction in objectives.items())
    def dominates(a, b):
        return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))
    front = [key for key, value in viable.items()
             if not any(dominates(other, value) for other in viable.values())]
    metrics = {"pareto": front, "feasible": list(viable), "rejected": rejected,
               "unknown": unknown, "constraint_margins": margins, "objectives": objectives}
    findings = [] if viable or unknown else [
        finding("NO-FEASIBLE-CANDIDATE", "optimization", "声明的候选都违反至少一项硬约束",
                "candidates", "建立满足所有硬约束的备选", "复核硬约束和来源")
    ]
    return result("inconclusive" if unknown else "pass" if viable else "fail", metrics, findings,
                  [f"{key}: {', '.join(value)}" for key, value in unknown.items()])


CHECKS = {"mechanical": enclosure, "thermal": junction, "transient": transient,
          "tolerance": tolerance, "energy": energy, "cost": cost, "optimization": pareto}
