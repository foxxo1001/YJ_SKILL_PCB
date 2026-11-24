"""Bounded four-layer planner for flat low-voltage prototypes with two ground reference planes."""
from collections import defaultdict
from heapq import heappop, heappush
from itertools import pairwise
from math import ceil, floor, hypot

from ..domain import finding, number, result

FOIL = (1, 15, 16, 2)
ROUTING = (1, 2)
GRID = 0.2
EPS = 1e-7


def _snap(at):
    return tuple(round(value / GRID) for value in at)


def _mm(at):
    return tuple(round(value * GRID, 6) for value in at[:2])


def _box(a, b, rx, ry=None):
    ry = rx if ry is None else ry
    return (min(a[0], b[0]) - rx, min(a[1], b[1]) - ry,
            max(a[0], b[0]) + rx, max(a[1], b[1]) + ry)


def _overlap(a, b, gap=0):
    return not (a[2] + gap <= b[0] + EPS or b[2] + gap <= a[0] + EPS
                or a[3] + gap <= b[1] + EPS or b[3] + gap <= a[1] + EPS)


def _box_distance(a, b):
    return hypot(max(a[0] - b[2], b[0] - a[2], 0), max(a[1] - b[3], b[1] - a[3], 0))


def _blocked(obstacles, net, radius, gap):
    grid = {layer: set() for layer in FOIL}
    for owner, layers, (x0, y0, x1, y1) in obstacles:
        if owner == net:
            continue
        clearance = radius + gap
        cells = {(x, y)
                 for x in range(ceil((x0 - clearance) / GRID - EPS), floor((x1 + clearance) / GRID + EPS) + 1)
                 for y in range(ceil((y0 - clearance) / GRID - EPS), floor((y1 + clearance) / GRID + EPS) + 1)}
        for layer in layers:
            grid[layer].update(cells)
    return grid


def _route(start, goal, layers, blocked, via_blocked, bounds, via_budget):
    moves = ((1, 0), (-1, 0), (0, 1), (0, -1))
    source = (*_snap(start), 1, 0)
    gx, gy = _snap(goal)
    queue = [(0, 0, source)]
    cost, previous = {source: 0}, {}
    west, south, east, north = bounds
    for _ in range(180_000):
        if not queue:
            return []
        _, score, item = heappop(queue)
        if cost[item] != score:
            continue
        x, y, layer, turns = item
        if (x, y, layer) == (gx, gy, 1):
            path = [item]
            while item in previous:
                item = previous[item]
                path.append(item)
            return list(reversed(path))
        choices = [(x + dx, y + dy, layer, turns, 1) for dx, dy in moves]
        if turns < via_budget and (x, y) not in via_blocked and len(layers) > 1:
            choices.append((x, y, 2 if layer == 1 else 1, turns + 1, 15))
        for nx, ny, target, steps, step_cost in choices:
            if not west <= nx <= east or not south <= ny <= north or (nx, ny) in blocked[target]:
                continue
            neighbor = nx, ny, target, steps
            next_score = score + step_cost
            if next_score >= cost.get(neighbor, 10**12):
                continue
            cost[neighbor] = next_score
            previous[neighbor] = item
            heappush(queue, (next_score + abs(nx - gx) + abs(ny - gy), next_score, neighbor))
    return []


def _compact(nodes):
    if len(nodes) <= 2:
        return nodes
    compact = [nodes[0]]
    for before, node, after in zip(nodes, nodes[1:], nodes[2:]):
        same_layer = before[2] == node[2] == after[2]
        straight = before[0] == node[0] == after[0] or before[1] == node[1] == after[1]
        if not (same_layer and straight):
            compact.append(node)
    return [*compact, nodes[-1]]


def _plane_rectangles(width, height, edge, cutouts):
    right, top = width - edge, height - edge
    cutouts = [(max(edge, a), max(edge, b), min(right, c), min(top, d)) for a, b, c, d in cutouts
               if _overlap((edge, edge, right, top), (a, b, c, d))]
    xs = sorted({edge, right, *(x for r in cutouts for x in (r[0], r[2]))})
    bands = defaultdict(list)
    for left, end in pairwise(xs):
        spans = sorted((b, d) for a, b, c, d in cutouts if a < end - EPS and c > left + EPS)
        start = edge
        for b, d in [*spans, (top, top)]:
            if b > start + EPS:
                runs = bands[(start, b)]
                if runs and abs(runs[-1][1] - left) < EPS:
                    runs[-1] = (runs[-1][0], end)
                else:
                    runs.append((left, end))
            start = max(start, d)
    return [(x, y, end, top) for (y, top), spans in bands.items() for x, end in spans]


def _failure(message, **details):
    return {"status": "blocked", "reason": message, **details}


def _merge_tracks(tracks):
    collinear, stubs = defaultdict(list), {}
    for trace in tracks:
        ax, ay = trace["start"]
        bx, by = trace["end"]
        line = trace["net"], trace["layer"], trace["width_mm"]
        if ax == bx:
            collinear[(*line, 0, ax)].append(tuple(sorted((ay, by))))
        elif ay == by:
            collinear[(*line, 1, ay)].append(tuple(sorted((ax, bx))))
        else:
            stubs[(*line, tuple(sorted((trace["start"], trace["end"]))))] = trace
    result = list(stubs.values())
    for (net, layer, width, axis, coordinate), spans in collinear.items():
        merged = []
        for a, b in sorted(spans):
            if merged and a <= merged[-1][1] + EPS:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        for a, b in merged:
            start, end = ((coordinate, a), (coordinate, b)) if axis == 0 else ((a, coordinate), (b, coordinate))
            result.append({"net": net, "layer": layer, "start": start, "end": end, "width_mm": width})
    return result


def plan(spec, connections):
    config = spec["layout"]
    width, height = (number(v, "board size", 15, 90) for v in config["size_mm"])
    gap = number(config["clearance_mm"], "copper clearance", 0.125, 1)
    edge = number(config["edge_mm"], "edge clearance", 0.4, 2)
    via_size = number(config["via_outer_mm"], "via diameter", 0.48, 1)
    via_drill = number(config["via_drill_mm"], "drill", 0.2, via_size - 0.23)
    ground = config["ground_net"]
    if ground not in spec["nets"] or set(config["rules"]) != set(spec["nets"]):
        raise ValueError("Declare an actual ground net and explicit rules for each circuit net")
    holes = config.get("holes", [])
    leaveouts = config.get("keepouts", [])
    obstacles, pads, tracks, vias, copper, pad_holes = [], {}, [], [], [], []
    for x, y, diameter in holes:
        keep = diameter / 2 + gap
        box = _box((x, y), (x, y), keep)
        obstacles.append((None, FOIL, box))
        pad_holes.append(box)
    for keep in leaveouts:
        if keep["kind"] == "copper":
            obstacles.append((None, tuple(keep["layers"]), tuple(keep["box"])))
    bodies = {}
    positions = {ref: spec["parts"][ref]["at_mm"] for ref in spec["parts"]}
    for ref, part in spec["parts"].items():
        package = spec["packages"][part["package"]]
        center = positions[ref]
        bx, by = package["body_mm"]
        body = _box(center, center, bx / 2, by / 2)
        if any(_overlap(body, other) for other in bodies.values()):
            return _failure("Component body overlaps another component", part=ref)
        if any(_overlap(body, tuple(keep["box"])) for keep in leaveouts if keep["kind"] == "component"):
            return _failure("Component overlaps a connector insertion or assembly keepout", part=ref)
        if body[0] < 0 or body[1] < 0 or body[2] > width or body[3] > height:
            return _failure("Component body overhang was not authorized", part=ref)
        bodies[ref] = body
        for pin, data in package["pads"].items():
            at = tuple(round(a + b, 6) for a, b in zip(center, data["xy"]))
            size = data["size"]
            pad_box = _box(at, at, size[0] / 2, size[1] / 2)
            if pad_box[0] < edge or pad_box[1] < edge or pad_box[2] > width - edge or pad_box[3] > height - edge:
                return _failure("Footprint pad violates copper-to-edge clearance", pad=[ref, pin])
            net = connections.get((ref, pin))
            layers = FOIL if data.get("drill") else (1,)
            pads[ref, pin] = {**data, "at": at, "box": pad_box}
            if layers == FOIL and net != ground:
                pad_holes.append(_box(at, at, size[0] / 2 + gap + .03, size[1] / 2 + gap + .03))
            if any(set(layers) & set(other_layers) and net != other_net
                   and _overlap(pad_box, other_box, gap) for other_net, other_layers, other_box in obstacles):
                return _failure("Foreign pad, hole or keepout violates planned clearance", pad=[ref, pin])
            obstacles.append((net, layers, pad_box))

    def track(net, layer, a, b, size):
        if hypot(b[0] - a[0], b[1] - a[1]) < EPS:
            return
        tracks.append({"net": net, "layer": layer, "start": a, "end": b, "width_mm": size})
        obstacles.append((net, (layer,), _box(a, b, size / 2)))

    def via(net, at, role, **fields):
        vias.append({"net": net, "at": at, "outer_mm": via_size, "drill_mm": via_drill, "role": role,
                     "layers": FOIL, **fields})
        obstacles.append((net, FOIL, _box(at, at, via_size / 2)))
        if net != ground:
            pad_holes.append(_box(at, at, via_size / 2 + gap + .03))

    def ground_site(at, *, handoff=None, allow_pad=None, connect=False):
        candidates = sorted({(round(at[0] + dx * GRID, 6), round(at[1] + dy * GRID, 6))
                             for dx in range(-9, 10) for dy in range(-9, 10)},
                            key=lambda p: (hypot(p[0] - at[0], p[1] - at[1]), p))
        for target in candidates:
            area = _box(target, target, via_size / 2)
            if any(a < edge for a in area[:2]) or area[2] > width - edge or area[3] > height - edge:
                continue
            if any(_overlap(area, other, gap) for n, _, other in obstacles if n != ground):
                continue
            if connect and any(_overlap(_box(at, target, .09), other, gap)
                               for n, layers, other in obstacles if n != ground and 1 in layers):
                continue
            if any(_overlap(area, pad["box"], .03) for key, pad in pads.items() if key != allow_pad):
                continue
            if any(hypot(v["at"][0] - target[0], v["at"][1] - target[1]) < via_size + .15 for v in vias):
                continue
            if handoff and not 0 < hypot(target[0] - at[0], target[1] - at[1]) <= handoff:
                continue
            return target
        return None

    for ref, pin in spec["nets"][ground]:
        pad = pads[ref, pin]
        if pad.get("drill"):
            continue
        at = pad["at"]
        thermal = pad.get("thermal")
        target = ground_site(at, allow_pad=(ref, pin) if thermal else None, connect=True)
        if target is None:
            return _failure("No ground or thermal via placement satisfies clearance", pad=[ref, pin])
        via(ground, target, "thermal" if thermal else "power_ground", endpoint=[ref, pin])
        track(ground, 1, at, target, .18)

    order = list(config.get("priority", []))
    order.extend(net for net in spec["nets"] if net not in order)
    checks = {}
    for net in order:
        if net == ground:
            continue
        rule = config["rules"][net]
        trace = number(rule["width_mm"], "trace width", .15, 2.5)
        maximum = number(rule["max_branch_mm"], "maximum branch length", 1, 220)
        layers = tuple(rule["layers"])
        if layers not in ((1,), ROUTING):
            raise ValueError("Route classes may use L1 or L1/L4, with L2/L3 kept as reference planes")
        branches = []
        endpoints = spec["nets"][net]
        for i, (left, right) in enumerate(pairwise(endpoints), 1):
            a, b = pads[tuple(left)]["at"], pads[tuple(right)]["at"]
            net_keepouts = [(None, layers, tuple(box)) for box in rule.get("keepout_boxes_mm", [])]
            blocked = _blocked([*obstacles, *net_keepouts], net, trace / 2, gap)
            all_blocked = _blocked([*obstacles, *net_keepouts], net, via_size / 2 + .02, gap)
            via_blocked = set().union(*(all_blocked[layer] for layer in FOIL))
            for pad in pads.values():
                x0, y0, x1, y1 = pad["box"]
                radius = via_size / 2 + .04
                via_blocked.update((x, y) for x in range(ceil((x0-radius)/GRID), floor((x1+radius)/GRID)+1)
                                   for y in range(ceil((y0-radius)/GRID), floor((y1+radius)/GRID)+1))
            radius = edge + max(trace, via_size) / 2
            bounds = (ceil(radius / GRID), ceil(radius / GRID), floor((width-radius)/GRID), floor((height-radius)/GRID))
            nodes = _route(a, b, layers, blocked, via_blocked, bounds, 2)
            if not nodes:
                return _failure("No constrained route found", net=net, branch=[left, right], layers=layers)
            nodes = _compact(nodes)
            points = [(*a, 1), *( (*_mm(p), p[2]) for p in nodes), (*b, 1)]
            length = sum(hypot(q[0]-p[0],q[1]-p[1]) for p,q in pairwise(points) if p[2] == q[2])
            if length > maximum + EPS:
                return _failure("Route exceeds the branch length budget", net=net, actual_mm=length, limit_mm=maximum)
            obstacles_before = len(obstacles)
            for p, q in pairwise(points):
                if p[2] == q[2]:
                    track(net, p[2], p[:2], q[:2], trace)
                else:
                    via(net, p[:2], "signal", branch=i)
            stitches = [v for v in vias if v["net"] == net and v["role"] == "signal" and v.get("branch") == i]
            for signal in stitches:
                limit = number(rule.get("return_via_max_mm", 1.7), "return via offset", via_size + gap, 3)
                at = signal["at"]
                target = ground_site(at, handoff=limit)
                if target is None:
                    return _failure("Layer change is missing feasible return stitching", net=net, at=at)
                via(ground, target, "return", signal_net=net, signal_at=at, limit_mm=limit)
            branches.append({"endpoints": [left, right], "length_mm": round(length, 5), "limit_mm": maximum,
                             "min_width_mm": trace, "via_count": len(stitches), "layers": sorted({p[2] for p in points}),
                             "copper_objects": len(obstacles) - obstacles_before})
        checks[net] = branches

    for layer in (15, 16):
        cutouts = pad_holes + [tuple(keep["box"]) for keep in leaveouts if keep["kind"] == "copper"
                              and layer in keep["layers"]]
        rectangles = _plane_rectangles(width, height, edge, cutouts)
        copper.extend({"net": ground, "layer": layer, "box": box} for box in rectangles)
    connector_rooms = []
    for keep in leaveouts:
        if keep["kind"] == "component":
            distance, ref = min((_box_distance(keep["box"], body), ref) for ref, body in bodies.items())
            connector_rooms.append({"reservation": keep["name"], "nearest_component": ref,
                                    "nominal_xy_gap_mm": round(distance, 4)})
    return {"status": "pass", "size": [width, height], "placement": positions, "tracks": _merge_tracks(tracks), "vias": vias,
            "copper": copper, "holes": holes, "keepouts": leaveouts, "checks": checks,
            "assembly_xy": connector_rooms,
            "reference_planes": {str(layer): ground for layer in (15, 16)},
            "limitations": ["Axis-aligned limited routing on L1/L4. Geometric branch lengths exclude via delay.",
                            "Internal ground is constructed copper with geometric cutouts. Native DRC still required.",
                            "Connector envelopes are declared 2D keepouts. Full assembly and thermal tests remain separate."]}


def _rounded(values):
    return tuple(round(v, 4) for v in values)


def _at_inside(at, box):
    return box[0] - EPS <= at[0] <= box[2] + EPS and box[1] - EPS <= at[1] <= box[3] + EPS


def _connected_rectangles(boxes):
    if not boxes:
        return 0
    unseen = set(range(1, len(boxes)))
    queue = [0]
    while queue:
        i = queue.pop()
        x0, y0, x1, y1 = boxes[i]
        connected = set()
        for j in unseen:
            a, b, c, d = boxes[j]
            dx, dy = min(x1, c) - max(x0, a), min(y1, d) - max(y0, b)
            if dx >= -EPS and dy >= -EPS and max(dx, dy) > EPS:
                connected.add(j)
        unseen.difference_update(connected)
        queue.extend(connected)
    return len(boxes) - len(unseen)


def verify(canonical, baseline):
    """Check authored constraints against current native objects, without assuming their planner output."""
    shapes = canonical["geometry"]
    evidence = canonical["evidence"]
    found = []

    def issue(code, name, message, **scope):
        row = finding(code, "pcb", message, name, "在工作副本恢复约束要求的铜、走线或过孔。",
                      "用当前填铜和过孔几何复查，再复跑嘉立创原生 DRC。")
        row["scope"].update(scope)
        row["evidence_ids"] = [item["id"] for item in evidence]
        found.append(row)

    def route_key(net, layer, start, end):
        return net, str(layer), tuple(sorted((_rounded(start), _rounded(end))))

    track_map = {route_key(r["net"], r["layer"], r["start_mm"], r["end_mm"]): r
                 for r in shapes if r["kind"] == "segment"}
    via_map = {(r["net"], _rounded(r["at_mm"])): r for r in shapes if r["kind"] == "via"}
    copper_map = {(r["net"], r["layer"], _rounded(r["box_mm"])): r
                  for r in shapes if r["kind"] == "copper_fill_rect"}
    active_layers = {layer["id"] for layer in canonical["layers"]}
    if not {str(layer) for layer in FOIL} <= active_layers:
        issue("stack_layers", "叠层", "缺少设计规定的四个铜层", active_layers=sorted(active_layers))
    placements = {row["reference"]: row for row in canonical["components"]}
    placement_changed = False
    for ref, at in baseline["placement"].items():
        current = placements.get(ref)
        if not current or _rounded(current["at_mm"]) != _rounded(at):
            placement_changed = True
            issue("placement", ref, "器件位置已偏离预留空间和走线的设计基准", expected_mm=at,
                  actual_mm=current["at_mm"] if current else None)
    for wanted in baseline["tracks"]:
        net, layer, start, end = (wanted[key] for key in ("net", "layer", "start", "end"))
        actual = track_map.get(route_key(net, layer, start, end))
        if not actual:
            issue("route_geometry", net, "当前板缺少规划的线段", layer=layer, start_mm=start, end_mm=end)
        elif actual["width_mm"] < wanted["width_mm"] - 1e-5:
            issue("route_width", net, "实际线宽低于规划值", layer=layer, locator=actual["locator"],
                  actual_mm=actual["width_mm"], min_mm=wanted["width_mm"])
    planes = {layer: [key[2] for key in copper_map if key[:2] == (net, layer)]
              for layer, net in baseline["reference_planes"].items()}
    for layer, boxes in planes.items():
        if not boxes or _connected_rectangles(boxes) != len(boxes):
            issue("reference_plane_disconnected", baseline["reference_planes"][layer], "参考层铜区缺失或存在断开的区域",
                  layer=layer, zones=len(boxes), connected=_connected_rectangles(boxes))
    for zone in baseline["copper"]:
        key = zone["net"], str(zone["layer"]), _rounded(zone["box"])
        if key not in copper_map:
            issue("copper_geometry", zone["net"], "设计要求的实心铜区在当前板缺失或变形",
                  layer=zone["layer"], box_mm=zone["box"])
    for wanted in baseline["vias"]:
        key = wanted["net"], _rounded(wanted["at"])
        actual = via_map.get(key)
        if (not actual or not actual["through"] or actual["excluded_layers"]
                or actual["size_mm"] + 1e-5 < wanted["outer_mm"]
                or abs(actual["drill_mm"] - wanted["drill_mm"]) > 1e-5):
            issue(wanted["role"] + "_via", wanted["net"], "指定过孔缺失、孔径改变或没有跨设计要求的铜层",
                  at_mm=wanted["at"], endpoint=wanted.get("endpoint"), signal_net=wanted.get("signal_net"))
            continue
        if wanted["role"] != "signal":
            for layer, boxes in planes.items():
                if not any(_at_inside(actual["at_mm"], box) for box in boxes):
                    issue("via_plane_contact", wanted["net"], "地孔没有接入实际参考铜区",
                          at_mm=actual["at_mm"], layer=layer, role=wanted["role"], endpoint=wanted.get("endpoint"))
    return {**result("fail" if found else "pass", {"copper_layers": sorted(active_layers & {"1", "15", "16", "2"}),
                     "branches": sum(len(branches) for branches in baseline["checks"].values()),
                     "native_tracks": len(track_map), "native_vias": len(via_map), "native_solid_copper": len(copper_map)},
                     found),
            "declared_assembly_xy": baseline.get("assembly_xy", []) if not placement_changed else [],
            "evidence": evidence, "scope": "Expected route geometry and widths, rectangular ground continuity, "
            "specified return and thermal vias on the generated four-layer profile"}
