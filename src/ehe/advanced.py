"""Bounded analytical screens for high-impact hardware domains."""
from math import log, pi

from .domain import finding, n, number, result
from .reality import vector


def assess(category, metrics, problems, missing=None):
    issues = [finding(code, category, mechanism, scope, action, verify, severity, status)
              for code, mechanism, scope, action, verify, severity, status in problems]
    decisive = any(row["status"] == "confirmed" for row in issues)
    return result("fail" if decisive else "inconclusive" if missing or issues else "pass",
                  metrics, issues, missing)


def positive(row, key):
    value = n(row, key)
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def precision_analog(data: dict) -> dict:
    """Headroom, a single-pole settling screen, sine slew and absolute error budget."""
    if not data.get("source") or not data.get("conditions"):
        return result("inconclusive", missing=["amplifier official revision, supply and output load conditions"])
    ranges = {}
    for name in ("common_mode", "output"):
        actual = data[name]
        bounds = [number(actual[key], key) for key in ("actual_min_v", "actual_max_v", "limit_min_v", "limit_max_v")]
        lo, hi, min_v, max_v = bounds
        if lo > hi or min_v > max_v:
            raise ValueError("Inverted analog voltage range")
        ranges[name] = min(lo - min_v, max_v - hi)
    f_hz = n(data, "signal_hz")
    amplitude = n(data, "amplitude_v")
    slew = 2 * pi * f_hz * amplitude
    bits = n(data, "adc_bits", 1, 32)
    if not bits.is_integer():
        raise ValueError("ADC bits must be an integer")
    min_settle = (bits + 1) * log(2) * n(data, "equivalent_rc_s")
    errors = data.get("absolute_errors_v", {})
    total_error = sum(number(value, name, 0) for name, value in errors.items())
    margin = {
        **{f"{k}_margin_v": value for k, value in ranges.items()},
        "slew_margin_v_per_s": n(data, "slew_v_per_s") - slew,
        "settling_margin_s": n(data, "acquisition_s") - min_settle,
        "gbw_margin_hz": n(data, "gbw_hz") - f_hz * positive(data, "noise_gain") * positive(data, "bandwidth_margin"),
        "error_margin_v": n(data, "max_error_v") - total_error,
    }
    problems = [("ANALOG-" + key.split("_margin")[0].upper(), "模拟工况超出声明的工作范围或误差预算",
                 key, "重选前端工作点、驱动与滤波", "按输入范围、负载和采样时序测量", "P1", "confirmed")
                for key, value in margin.items() if value < 0]
    return assess("analog", {**margin, "required_slew_v_per_s": slew, "half_lsb_settle_s": min_settle,
                              "total_abs_error_v": total_error}, problems, [] if errors else ["error terms"])


def motor(data: dict) -> dict:
    """Cold winding stall estimate against an explicit duration-specific SOA point."""
    if not data.get("source") or not data.get("soa_locator"):
        return result("inconclusive", missing=["applicable driver SOA and stall conditions"])
    stall = n(data, "max_supply_v") / positive(data, "min_winding_ohm")
    limit = n(data, "peak_limit_a")
    duration = n(data, "protection_clearing_s")
    soa_time = n(data, "soa_duration_s")
    loss = stall ** 2 * n(data, "hot_path_on_ohm")
    junction = number(data["ambient_c"], "ambient_c") + loss * n(data, "transient_theta_c_per_w")
    margins = {"stall_a": stall, "loss_w": loss, "tj_estimate_c": junction,
               "current_margin_a": limit - stall, "soa_time_margin_s": soa_time - duration,
               "thermal_margin_c": number(data["limit_tj_c"], "limit_tj_c") - junction}
    unsafe = any(margins[key] < 0 for key in ("current_margin_a", "soa_time_margin_s", "thermal_margin_c"))
    issues = [("MOTOR-STALL", "冷态堵转预计越过驱动峰值、允许持续时间或结温", "driver",
               "重核限流、关断和驱动裕量", "独立复核热模型和 SOA，按获批安全方案测量", "P0", "confirmed")] if unsafe else []
    return assess("motor", margins, issues)


def buck(data: dict) -> dict:
    """Ideal continuous-conduction Buck ripple at high VIN, low L and minimum f."""
    if not data.get("source"):
        return result("inconclusive", missing=["inductor tolerances and switch limits at conditions"])
    vin, vout = positive(data, "vin_max_v"), positive(data, "vout_v")
    if vout >= vin:
        raise ValueError("Ideal Buck model requires 0 < VOUT < VIN")
    ripple = vout * (1 - vout / vin) / (positive(data, "min_h") * positive(data, "min_hz"))
    load = n(data, "load_a")
    peak = load + ripple / 2
    margin = min(n(data, "inductor_sat_a"), n(data, "switch_limit_a")) - peak
    issues = [("BUCK-PEAK", "估算峰值超过限流或电感饱和指标", "inductor-switch",
               "核对最小电感和峰值电流", "在输入电压极值测电感波形及电源裕量", "P0", "confirmed")] if margin < 0 else []
    return assess("power_electronics", {"ripple_pp_a": ripple, "peak_a": peak, "peak_margin_a": margin,
                                       "ideal_duty": vout / vin}, issues,
                  ["continuous conduction assumption violated"] if load <= ripple / 2 else [])


def realtime(data: dict) -> dict:
    """Sequential worst-case latency budget and optionally a link utilization check."""
    stages = data.get("stages", [])
    if not stages or not data.get("source"):
        return result("inconclusive", missing=["documented stage deadlines and worst execution timing"])
    latency = sum(n(row, "worst_us") + n(row, "jitter_us") for row in stages)
    late = latency > n(data, "deadline_us")
    metrics = {"worst_latency_us": latency, "deadline_margin_us": data["deadline_us"] - latency}
    if "link" in data:
        link = data["link"]
        used = n(link, "frames_per_s") * n(link, "encoded_bits")
        capacity = positive(link, "bits_per_s")
        metrics["link_utilization"] = used / capacity
        late = late or used > capacity
    issues = [("RT-DEADLINE", "最坏延迟或编码吞吐超过预算", "control-loop",
               "调整采样触发顺序、负载和任务调度", "记录 GPIO/DMA 时间戳与高负载抖动", "P1", "confirmed")] if late else []
    return assess("realtime", metrics, issues)


def fault(data: dict) -> dict:
    """DC prospective fault current and constant-current I²t envelope."""
    needed = {"source", "curve_locator", "clearing_max_s", "clearing_current_min_a", "max_i2t_a2s",
              "supply_max_v", "supply_min_v", "path_min_ohm", "path_max_ohm", "safe_peak_a"}
    if needed - data.keys() or not data.get("source") or not data.get("curve_locator"):
        return result("inconclusive", missing=sorted(needed - data.keys()) or ["applicable protection curve"])
    low_v, high_v = n(data, "supply_min_v"), n(data, "supply_max_v")
    r_low, r_high = positive(data, "path_min_ohm"), positive(data, "path_max_ohm")
    if low_v > high_v or r_low > r_high:
        raise ValueError("Inverted fault voltage or resistance range")
    maximum, minimum = high_v / r_low, low_v / r_high
    i2t = maximum ** 2 * positive(data, "clearing_max_s")
    limits = {"prospective_min_a": minimum, "prospective_max_a": maximum, "i2t_bound_a2s": i2t}
    problems = []
    if minimum < n(data, "clearing_current_min_a"):
        problems.append(("FAULT-TIME", "最小故障电流无法证明保护在限定时间内动作", "protection",
                         "调整保护阈值或扩大曲线证据", "由独立人员核对适用曲线与容差", "P0", "confirmed"))
    if maximum > n(data, "safe_peak_a") or i2t > n(data, "max_i2t_a2s"):
        problems.append(("FAULT-STRESS", "估算短路峰值或 I²t 超过受保护路径限制", "fault-current-path",
                         "核对限流、连接器和 PCB 约束", "独立复核源模型、保护曲线和故障条件", "P0", "confirmed"))
    return assess("fault", limits, problems)


def _rect_intersects(start, end, box):
    """Slab intersection including a tangent or an endpoint within the gap."""
    start_t, end_t = 0.0, 1.0
    for first, last, low, high in zip(start, end, box[:2], box[2:]):
        if low >= high:
            raise ValueError("Gap rectangle has nonpositive area")
        delta = last - first
        if delta == 0:
            if not low <= first <= high:
                return False
            continue
        a, b = sorted(((low - first) / delta, (high - first) / delta))
        start_t, end_t = max(start_t, a), min(end_t, b)
    return start_t <= end_t


def return_path(data: dict) -> dict:
    """Visible traces versus declared reference gaps and layer-change coupling."""
    if not data.get("geometry_source") or not data.get("stackup_source") or data.get("plane_model_complete") is not True:
        return result("inconclusive", missing=["layer stack, routed geometry and covered reference-plane model"])
    problems, missing = [], []
    routes = data.get("routes", [])
    if not routes:
        return result("inconclusive", missing=["electrically identified routes"])
    for route in routes:
        net = route["id"]
        if not route.get("segments"):
            missing.append(f"{net}: route geometry")
        for pos, segment in enumerate(route.get("segments", [])):
            plane = segment.get("reference")
            if not plane or plane not in data.get("gaps_by_reference", {}):
                missing.append(f"{net}: reference plane coverage at segment {pos}")
                continue
            start, end = vector(segment, "start_mm", 2), vector(segment, "end_mm", 2)
            for index, gap in enumerate(data["gaps_by_reference"][plane]):
                rect = vector({"rect": gap}, "rect", 4)
                if _rect_intersects(start, end, rect):
                    problems.append(("RETURN-GAP", f"走线中心跨越参考面 {plane} 的已声明开缝",
                                     f"{net}/segment:{pos}/gap:{index}", "移至参考连续区或提供已验证回流路径",
                                     "复核原生铜面、净距和该处回流", "P1", "confirmed"))
        if route.get("transition_inventory_complete") is not True:
            missing.append(f"{net}: layer transition inventory")
        for pos, transition in enumerate(route.get("transitions", [])):
            if not transition.get("from_reference") or not transition.get("to_reference"):
                missing.append(f"{net}: reference before/after layer change {pos}")
                continue
            if transition.get("effective_coupling") is True and transition.get("source"):
                continue
            mode = "同参考面的相邻过孔" if transition["from_reference"] == transition["to_reference"] else "参考面切换耦合"
            if transition.get("effective_coupling") is False and transition.get("source"):
                problems.append(("RETURN-SWITCH", f"{mode}缺失或在工作频段无效", f"{net}/transition:{pos}",
                                 "按实际参考面提供近邻低阻返回路径", "核对几何和回流耦合参考条件",
                                 "P1", "confirmed"))
            else:
                missing.append(f"{net}: effective return coupling at transition {pos}")
    return assess("return_path", {"routes_checked": len(routes), "model": "trace-center / rectangular gaps"},
                  problems, missing)


def emi_loop(data: dict) -> dict:
    """Polygon area threshold screening for declared switch-current paths."""
    if not data.get("geometry_source") or not data.get("topology_source") or not data.get("coupling_path"):
        return result("inconclusive", missing=["switch-state current path, geometry and coupling path"])
    vertices = data.get("path_mm", [])
    if (not data.get("source_node") or not data.get("victim_node") or
            not isinstance(vertices, list) or not 3 <= len(vertices) <= 100):
        return result("inconclusive", missing=["identified source/victim and complete loop geometry"])
    points = [vector({"p": entry}, "p", 2) for entry in vertices]
    area = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1]))) / 2
    if area <= 0:
        raise ValueError("Loop must be a nondegenerate simple polygon")
    margin = n(data, "target_max_mm2") - area
    problems = []
    if margin < 0:
        problems.append(("EMI-LOOP-AREA", "高 di/dt 环路超过声明的布局目标，可能增加耦合",
                         data["source_node"], "重新排列旁路和开关器件，缩小相邻参考面内回路",
                         "检查真实回路和受扰节点，在指定工况测近场与时域", "P1", "suspected"))
    return assess("emi", {"area_mm2": area, "layout_margin_mm2": margin,
                          "victim": data["victim_node"], "coupling_path": data["coupling_path"]}, problems)


def signal(data: dict) -> dict:
    """Lossless line flight time and first-load reflection for a resistive load."""
    if not data.get("source"):
        return result("inconclusive", missing=["routing and driver/load impedance source"])
    td = n(data, "length_m") / positive(data, "velocity_m_per_s")
    rise = positive(data, "rise_s")
    impedance = positive(data, "z0_ohm")
    load_z = n(data, "load_ohm")
    source_z = n(data, "source_ohm")
    gamma_l, gamma_s = (load_z - impedance) / (load_z + impedance), (source_z - impedance) / (source_z + impedance)
    first_error = n(data, "step_v") * impedance / (source_z + impedance) * abs(gamma_l)
    long = td >= rise * n(data, "max_flight_to_rise_ratio", 0, 1)
    problems = []
    if long and first_error > n(data, "max_first_reflection_v"):
        problems.append(("SI-REFLECTION", "飞行时间与边沿相当且负载反射幅度超出预算", data["net"],
                         "核对源端/负载端端接及布线阻抗", "在接收端测边沿、过冲和稳定时间", "P1", "suspected"))
    return assess("si", {"one_way_s": td, "gamma_load": gamma_l, "gamma_source": gamma_s,
                         "first_reflection_v": first_error, "electrically_long": long}, problems)


def pdn(data: dict) -> dict:
    """Sample impedance magnitude for parallel series-RLC branches and a source R+L."""
    if not data.get("source") or not data.get("branches") or not data.get("frequencies_hz"):
        return result("inconclusive", missing=["source, PDN branches and target frequency samples"])
    target = n(data, "allowed_ripple_v") / positive(data, "step_a")
    caps = []
    for branch in data["branches"]:
        caps.append((positive(branch, "cap_f"), positive(branch, "esr_ohm"), n(branch, "loop_esl_h")))
    if len(data["frequencies_hz"]) > 2000:
        raise ValueError("PDN admits at most 2000 samples")
    samples = {}
    for raw in data["frequencies_hz"]:
        f = number(raw, "pdn frequency", 1e-6)
        w = 2 * pi * f
        admittance = 1 / complex(positive(data, "source_ohm"), w * n(data, "source_h"))
        for cap, esr, esl in caps:
            admittance += 1 / complex(esr, w * esl - 1 / (w * cap))
        samples[str(f)] = abs(1 / admittance)
    peak_frequency = max(samples, key=samples.get)
    problems = []
    if samples[peak_frequency] > target:
        problems.append(("PI-IMPEDANCE", "采样频点 PDN 阻抗估算超出目标，含所填布局回路电感", "pdn",
                         "改善去耦到电源/地端的路径并核对阻尼",
                         "在负载处做瞬态与阻抗测量，覆盖未采样频段", "P1", "suspected"))
    return assess("pi", {"target_ohm": target, "worst_sample_hz": float(peak_frequency),
                         "worst_ohm": samples[peak_frequency], "sample_ohm": samples}, problems)


CHECKS = {"analog": precision_analog, "motor": motor, "buck": buck, "realtime": realtime,
          "fault": fault, "return_path": return_path, "emi_loop": emi_loop, "si": signal, "pdn": pdn}
