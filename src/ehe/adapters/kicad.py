"""Read-only native KiCad checks in an isolated project copy."""
from dataclasses import asdict
import math
from pathlib import Path, PureWindowsPath
import os
import shutil
import subprocess
from tempfile import TemporaryDirectory
from uuid import uuid4

from ..doctor import kicad_probe, probe
from ..domain import finding, number
from ..io import atomic_write, inside, load, now, save, sha256
from ..models import CanonicalProject, Evidence
from ..project import manifest, output_path
from .sexpr import all_nodes, fields, parse, read, value

DOMAINS = {"schematic": ("sch", "erc", ".kicad_sch"), "pcb": ("pcb", "drc", ".kicad_pcb")}
NATIVE_SUFFIXES = {".kicad_sch", ".kicad_pcb", ".kicad_pro", ".kicad_dru", ".kicad_sym",
                   ".kicad_mod", ".kicad_wks", ".sch", ".lib", ".dcm"}
NATIVE_TABLES = {"fp-lib-table", "sym-lib-table"}
REMEDIATION = {
    "unconnected_items": ("比较两端网络、焊盘号和层，恢复该网断点处的连续铜连接",
                          "复跑 DRC，复核相连焊盘的网络及原理图一致性"),
    "pin_not_connected": ("查看该脚的手册定义与同网对象。把连接端准确接到引脚栅格，或按手册确认后标注 NC",
                          "复跑 ERC，逐一核对目标引脚与网络"),
    "shorting_items": ("定位报告中的两个网络和坐标，纠正错误的铜连接或层间过孔",
                        "复跑 DRC，检查两网络的间距、连通性和相邻回流"),
    "net_conflict": ("按位号与焊盘号对照原理图和 PCB。若原理图是当前设计依据，从原理图更新 PCB 并核对受影响布线",
                     "复跑一致性检查、DRC 与网络连通验证"),
    "clearance": ("按报告定位冲突对象，核对净距规则。调整相对坐标或走线，保留网类要求",
                  "复跑 DRC 并核对整改后的实际间距"),
}


def target_for(root: Path, domain: str) -> Path | None:
    suffix = DOMAINS[domain][2]
    if configured := manifest(root).get(domain):
        path = inside(root, configured)
        if path.suffix.lower() != suffix:
            raise ValueError(f"{domain} must refer to a {suffix} file")
        return path if path.is_file() else None
    candidates = sorted(root.glob("*" + suffix))
    if len(candidates) > 1:
        raise ValueError(f"Specify {domain} in project.json to disambiguate native files")
    return inside(root, candidates[0].name) if candidates else None


def native_inputs(root: Path) -> list[Path]:
    paths = []
    for folder, directories, filenames in os.walk(root, followlinks=False):
        location = Path(folder)
        directories[:] = [name for name in directories if not name.startswith(".")
                          and name.lower() not in {"build", "dist", "node_modules"}
                          and (location / name).resolve() == location / name]
        for name in filenames:
            path = location / name
            if path.suffix.lower() in NATIVE_SUFFIXES or name in NATIVE_TABLES:
                if not path.is_file() or path.resolve() != path:
                    raise ValueError(f"Linked native input unsupported: {path.relative_to(root)}")
                paths.append(path)
    if len(paths) > 2000 or sum(p.stat().st_size for p in paths) > 256 * 1024 * 1024:
        raise ValueError("Native project exceeds the isolated-copy file or size budget")
    return sorted(paths)


def check_references(path: Path) -> None:
    """Support KiCad project and standard KiCad library references."""
    if path.name in NATIVE_TABLES:
        for lib in all_nodes(read(path), "lib"):
            uri = value(lib, "uri", "").replace("\\", "/")
            allowed = ("${KIPRJMOD}/", "${KICAD10_SYMBOL_DIR}/", "${KICAD10_FOOTPRINT_DIR}/",
                       "${KICAD9_SYMBOL_DIR}/", "${KICAD9_FOOTPRINT_DIR}/",
                       "${KICAD8_SYMBOL_DIR}/", "${KICAD8_FOOTPRINT_DIR}/")
            if not uri.startswith(allowed) or ".." in uri.split("/") or ":" in uri:
                raise ValueError(f"External or unsupported library reference in {path.name}: {uri}")
    if path.suffix.lower() == ".kicad_sch":
        for sheet in all_nodes(read(path), "sheet"):
            for field in all_nodes(sheet, "property"):
                if len(field) >= 3 and str(field[1]).lower() == "sheetfile":
                    raw = PureWindowsPath(field[2])
                    if raw.drive or raw.root or ".." in raw.parts or "$" in field[2]:
                        raise ValueError(f"External subsheet reference in {path.name}: {field[2]}")


def _point(values: list) -> list[float]:
    return [number(float(entry), "KiCad coordinate") for entry in values[:2]]


def _arc_extent(start, mid, end):
    x1, y1 = (b - a for a, b in zip(start, mid))
    x2, y2 = (b - a for a, b in zip(start, end))
    det = 2 * (x1 * y2 - y1 * x2)
    if det == 0:
        raise ValueError("Collinear Edge.Cuts arc")
    u, v = x1 * x1 + y1 * y1, x2 * x2 + y2 * y2
    cx = start[0] + (u * y2 - v * y1) / det
    cy = start[1] + (x1 * v - x2 * u) / det
    radius = math.hypot(start[0] - cx, start[1] - cy)
    a, b, c = (math.atan2(y - cy, x - cx) for x, y in (start, mid, end))
    direction = 1 if (b - a) % math.tau <= (c - a) % math.tau else -1
    sweep = (direction * (c - a)) % math.tau
    extrema = [(cx + radius, cy), (cx, cy + radius), (cx - radius, cy), (cx, cy - radius)]
    return [start, mid, end] + [
        pt for i, pt in enumerate(extrema)
        if (direction * (i * math.pi / 2 - a)) % math.tau <= sweep + 1e-12
    ]


def _outline(tree: list) -> dict:
    points, unsupported = [], []
    thickness = value(next(all_nodes(tree, "general"), []), "thickness")
    for node in tree:
        if not isinstance(node, list) or not node or value(node, "layer") != "Edge.Cuts":
            continue
        kind = node[0]
        try:
            if kind in {"gr_line", "gr_rect"}:
                points.extend([_point(fields(node, "start")), _point(fields(node, "end"))])
            elif kind == "gr_arc":
                points.extend(_arc_extent(*[_point(fields(node, tag)) for tag in ("start", "mid", "end")]))
            elif kind == "gr_circle":
                center, end = [_point(fields(node, tag)) for tag in ("center", "end")]
                r = math.dist(center, end)
                points.extend([[axis - r for axis in center], [axis + r for axis in center]])
            elif kind == "gr_poly":
                vertices = [_point(pair[1:]) for pair in all_nodes(fields(node, "pts"), "xy")]
                if len(vertices) < 3:
                    raise ValueError("Incomplete Edge.Cuts polygon")
                points.extend(vertices)
            else:
                unsupported.append(kind)
        except (TypeError, ValueError, IndexError):
            unsupported.append(kind)
    if any(value(shape, "layer") == "Edge.Cuts"
           for fp in all_nodes(tree, "footprint") for shape in fp
           if isinstance(shape, list)):
        unsupported.append("footprint Edge.Cuts")
    metrics = {}
    if points and all(len(point) == 2 for point in points):
        xs, ys = zip(*points)
        metrics = {"min_mm": [min(xs), min(ys)], "max_mm": [max(xs), max(ys)],
                   "width_mm": max(xs) - min(xs), "height_mm": max(ys) - min(ys)}
    if thickness is not None:
        metrics["nominal_board_thickness_mm"] = number(float(thickness), "board thickness", 0)
    measured = bool(metrics.get("width_mm") and metrics.get("height_mm")) and not unsupported
    return {"status": "pass" if measured else "inconclusive", "metrics": metrics, "unsupported": sorted(set(unsupported)),
            "scope": "Edge.Cuts coordinate envelope; contour validity is verified separately by DRC"}


def geometry(path: Path) -> dict:
    content = sha256(path)
    tree = read(path)
    if content != sha256(path):
        raise ValueError("KiCad layout changed while reading")
    if not tree or tree[0] != "kicad_pcb":
        raise ValueError("Input is not a native KiCad PCB")
    nets_by_id = {node[1]: node[2] for node in all_nodes(tree, "net") if len(node) == 3}

    def net_for(node):
        name = value(node, "net")
        return nets_by_id.get(name, name)

    def identity(node, fallback):
        return value(node, "uuid", value(node, "tstamp", fallback))

    components, pins, geometries = [], [], []
    for i, node in enumerate(all_nodes(tree, "footprint")):
        properties = {p[1]: p[2] for p in all_nodes(node, "property") if len(p) >= 3}
        fp_id = identity(node, f"fp-{i}")
        components.append({"id": fp_id, "reference": properties.get("Reference"), "footprint": node[1],
                           "layer": value(node, "layer"), "at_mm": _point(fields(node, "at"))})
        for j, pad in enumerate(all_nodes(node, "pad")):
            pins.append({"id": identity(pad, f"{fp_id}-pad-{j}"), "component_id": fp_id, "number": pad[1],
                         "net": net_for(pad), "layers": fields(pad, "layers"),
                         "local_at_mm": _point(fields(pad, "at"))})
    for kind in ("segment", "arc", "via", "zone", "gr_line", "gr_rect", "gr_arc", "gr_circle", "gr_poly"):
        for i, node in enumerate(all_nodes(tree, kind)):
            layer = value(node, "layer")
            if kind.startswith("gr_") and layer != "Edge.Cuts":
                continue
            obj = {"id": identity(node, f"{kind}-{i}"), "kind": kind, "layer": layer,
                   "layers": fields(node, "layers"), "net": net_for(node)}
            for tag in ("start", "mid", "end", "at", "center"):
                if coordinates := fields(node, tag):
                    obj[f"{tag}_mm"] = _point(coordinates)
            for tag in ("width", "size", "drill"):
                if coordinate := value(node, tag):
                    obj[f"{tag}_mm"] = number(float(coordinate), tag, 0)
            polygons = []
            for poly in [*all_nodes(node, "polygon"), *all_nodes(node, "filled_polygon"), node]:
                points = next(all_nodes(poly, "pts"), [])
                if points:
                    polygons.append({"kind": poly[0], "layer": value(poly, "layer", layer),
                                     "points_mm": [_point(pair[1:]) for pair in all_nodes(points, "xy")]})
            if polygons:
                obj["polygons"] = polygons
            geometries.append(obj)
    layers = [{"id": item[0], "name": item[1], "type": item[2]}
              for item in fields(tree, "layers") if isinstance(item, list) and len(item) >= 3]
    names = set(nets_by_id.values())
    names.update(entry["net"] for entry in pins + geometries if entry["net"])
    return CanonicalProject(
        project={"path": str(path), "sha256": content, "eda": "KiCad", "source_version": value(tree, "version"),
                 "outline": _outline(tree)},
        components=components, pins=pins, nets=[{"id": name, "name": name} for name in sorted(names) if name],
        layers=layers, geometry=geometries,
        evidence=[asdict(Evidence.file(path, "kicad_pcb", "documented", "native_sexpr", content_sha256=content))],
        limitations=["E3 subset: no full layer-stack material model or resolved multi-polygon connectivity",
                     "Pads in footprint-local coordinates. No 3D bodies or coupled electromagnetic analysis."],
    ).to_dict()


def read_native_report(data: dict, check: str) -> list[dict]:
    schema = f"https://schemas.kicad.org/{check}.v1.json"
    if not isinstance(data, dict) or data.get("$schema") != schema:
        raise ValueError(f"Unexpected KiCad {check.upper()} report schema")
    if check == "erc":
        if not isinstance(data["sheets"], list) or not data["sheets"]:
            raise ValueError("Missing KiCad schematic sheets")
        groups = [(sheet["path"], sheet["violations"]) for sheet in data["sheets"]]
    else:
        groups = [(key, data[key]) for key in ("violations", "unconnected_items", "schematic_parity")]
    rows = []
    for scope, entries in groups:
        if not isinstance(entries, list):
            raise ValueError("Invalid KiCad violation list")
        for entry in entries:
            if not isinstance(entry, dict) or not all(entry.get(key) for key in ("type", "description", "severity")):
                raise ValueError("Malformed KiCad violation")
            rows.append({**entry, "scope": scope})
    return rows


def copy_inputs(root: Path, destination: Path, sources: list[Path]) -> dict[str, str]:
    hashes = {}
    for path in sources:
        relative = path.relative_to(root)
        copy = destination / relative
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, copy)
        hashes[relative.as_posix()] = sha256(copy)
        check_references(copy)
    return hashes


def capture(argv: list[str], cwd: Path, timeout: float):
    try:
        run = subprocess.run(argv, cwd=cwd, capture_output=True, encoding="utf-8",
                             errors="replace", timeout=timeout)
        return run.returncode, run.stdout, run.stderr, None
    except subprocess.TimeoutExpired as exc:
        return None, (exc.stdout or b"").decode("utf-8", "replace"), (exc.stderr or b"").decode("utf-8", "replace"), "timeout"
    except OSError as exc:
        return None, "", "", str(exc)


def run(root: Path, domain: str, timeout: float = 120, *, parity: bool = False) -> dict:
    root = root.resolve()
    subcommand, check, _ = DOMAINS[domain]
    target = target_for(root, domain)
    outcome = {"status": "not_run", "domain": domain, "check": check, "level": "E0",
               "evidence": [], "findings": [], "missing_evidence": []}
    if target is None:
        return {**outcome, "reason": "native_input_missing", "missing_evidence": [domain]}
    if parity and (domain != "pcb" or target_for(root, "schematic") != target.with_suffix(".kicad_sch")
                   or not target.with_suffix(".kicad_pro").is_file()):
        return {**outcome, "reason": "parity_inputs_missing",
                "missing_evidence": ["matching KiCad .kicad_sch/.kicad_pcb/.kicad_pro files"]}
    installed = kicad_probe()
    outcome["tool"] = installed
    if installed["status"] != "available":
        return {**outcome, "status": installed["status"], "reason": installed.get("reason", "tool_unavailable")}
    executable = Path(installed["path"])
    features = probe(executable, subcommand, check, "--help")
    required_flags = ["--format", "--exit-code-violations", "--severity-error", "--severity-warning"]
    if parity:
        required_flags.append("--schematic-parity")
    if features["status"] != "available" or not all(flag in features.get("stdout", "") for flag in required_flags):
        return {**outcome, "status": "blocked", "reason": "native CLI options unavailable", "help_probe": features}
    inputs = native_inputs(root)
    if target not in inputs:
        return {**outcome, "status": "blocked", "reason": "target excluded from safe native inputs"}
    identity = uuid4().hex
    artifacts = output_path(root, f"eda/{identity}")
    artifacts.mkdir(parents=True, exist_ok=False)
    raw = artifacts / f"{check}.json"
    started = now()
    with TemporaryDirectory(prefix="ehe-kicad-") as folder:
        isolated = Path(folder) / "project"
        hashes = copy_inputs(root, isolated, inputs)
        native_output = Path(folder) / f"{check}.json"
        relative_target = target.relative_to(root).as_posix()
        argv = [str(executable), subcommand, check, "--format", "json", "--units", "mm",
                "--severity-error", "--severity-warning", "--exit-code-violations",
                *(["--schematic-parity"] if parity else []), "--output", str(native_output), relative_target]
        rc, stdout, stderr, error = capture(argv, isolated, timeout)
        if native_output.is_file():
            shutil.copyfile(native_output, raw)
        execution = {"argv": argv, "cwd": str(isolated), "started_at": started, "finished_at": now(),
                     "exit_code": rc, "tool_version": installed["version"], "inputs": hashes,
                     "target": relative_target, "error": error, "artifact": str(raw)}
    save(artifacts / "execution.json", execution)
    atomic_write(artifacts / "stdout.txt", stdout)
    atomic_write(artifacts / "stderr.txt", stderr)
    outcome.update(execution=execution, artifacts=str(artifacts), retryable=True)
    if rc not in {0, 5} or not raw.is_file():
        return {**outcome, "status": "blocked", "reason": error or "native execution or output failed"}
    try:
        data = load(raw)
        violations = read_native_report(data, check)
        if (data.get("kicad_version") != installed["version"] or data.get("source") != target.name
                or set(data.get("included_severities", ())) != {"error", "warning"}
                or data.get("coordinate_units") != "mm" or (rc == 5) != bool(violations)):
            raise ValueError("Report source, version, severity, coordinate units or exit status disagrees")
        if any(item["severity"] not in {"error", "warning"} for item in violations):
            raise ValueError("Report includes a violation with unexpected severity")
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return {**outcome, "status": "inconclusive", "reason": str(exc)}
    evidence = asdict(Evidence.file(raw, f"{check}/violations", "tool_verified", f"kicad-cli {check}", installed["version"]))
    findings = []
    for row in violations:
        action, verify = REMEDIATION.get(row["type"], ("定位对象并根据项目规则修正",
                                                     "对变更版本重新运行原生规则检查"))
        issue = finding(row["type"], domain, row["description"], row["scope"], action, verify,
                        "P0" if row["severity"] == "error" else "P1")
        issue["evidence_ids"] = [evidence["id"]]
        issue["scope"]["items"] = row.get("items", [])
        issue["verified_by"] = f"kicad-cli {installed['version']}"
        findings.append(issue)
    outcome.update(status="fail" if violations else "pass", level="E2", evidence=[evidence],
                   findings=findings, retryable=False, raw_sha256=evidence["sha256"],
                   ignored_checks=data.get("ignored_checks", []))
    save(artifacts / "result.json", outcome)
    return outcome
