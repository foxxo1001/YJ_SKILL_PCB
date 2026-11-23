"""Read-only native KiCad checks in an isolated project copy."""
from dataclasses import asdict
from pathlib import Path, PureWindowsPath
import json
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


def geometry(path: Path) -> dict:
    tree = read(path)
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
        project={"path": str(path), "sha256": sha256(path), "eda": "KiCad", "source_version": value(tree, "version")},
        components=components, pins=pins, nets=[{"id": name, "name": name} for name in sorted(names) if name],
        layers=layers, geometry=geometries,
        evidence=[asdict(Evidence.file(path, "kicad_pcb", "documented", "native_sexpr"))],
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


def run(root: Path, domain: str, timeout: float = 120) -> dict:
    root = root.resolve()
    subcommand, check, _ = DOMAINS[domain]
    target = target_for(root, domain)
    outcome = {"status": "not_run", "domain": domain, "check": check, "level": "E0",
               "evidence": [], "findings": [], "missing_evidence": []}
    if target is None:
        return {**outcome, "reason": "native_input_missing", "missing_evidence": [domain]}
    installed = kicad_probe()
    outcome["tool"] = installed
    if installed["status"] != "available":
        return {**outcome, "status": installed["status"], "reason": installed.get("reason", "tool_unavailable")}
    executable = Path(installed["path"])
    features = probe(executable, subcommand, check, "--help")
    if features["status"] != "available" or not all(flag in features.get("stdout", "") for flag in
                                                    ("--format", "--exit-code-violations", "--severity-error", "--severity-warning")):
        return {**outcome, "status": "blocked", "reason": "native CLI options unavailable", "help_probe": features}
    inputs = native_inputs(root)
    if target not in inputs:
        return {**outcome, "status": "blocked", "reason": "target excluded from safe native inputs"}
    identity = uuid4().hex
    artifacts = output_path(root, f"eda/{identity}")
    artifacts.mkdir(parents=True, exist_ok=False)
    raw = artifacts / f"{check}.json"
    hashes = {}
    started = now()
    with TemporaryDirectory(prefix="ehe-kicad-") as folder:
        isolated = Path(folder) / "project"
        for input_file in inputs:
            relative = input_file.relative_to(root)
            duplicate = isolated / relative
            duplicate.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(input_file, duplicate)
            hashes[relative.as_posix()] = sha256(duplicate)
            check_references(duplicate)
        native_output = Path(folder) / f"{check}.json"
        relative_target = target.relative_to(root).as_posix()
        argv = [str(executable), subcommand, check, "--format", "json", "--units", "mm",
                "--severity-error", "--severity-warning", "--exit-code-violations",
                "--output", str(native_output), relative_target]
        rc, stdout, stderr, error = None, "", "", None
        try:
            completed = subprocess.run(argv, cwd=isolated, capture_output=True, encoding="utf-8",
                                       errors="replace", timeout=timeout)
            rc, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or b"").decode("utf-8", "replace")
            stderr = (exc.stderr or b"").decode("utf-8", "replace")
            error = "timeout"
        except OSError as exc:
            error = str(exc)
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
        issue = finding(row["type"], domain, row["description"], row["scope"],
                        "定位对象并根据项目规则修正", "对变更版本重新运行原生规则检查",
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
