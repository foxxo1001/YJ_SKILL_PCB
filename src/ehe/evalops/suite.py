"""Independent machine scoring, provenance and repeat-run comparison."""
from collections import Counter
from importlib.resources import files
import math
from pathlib import Path
import platform
import subprocess
from tempfile import TemporaryDirectory

from .. import __version__
from ..doctor import kicad_probe
from ..io import digest, load, now, sha256
from .drivers import DRIVERS
from .release import quality, release_gate


def _files(root):
    return {path.relative_to(root).as_posix(): sha256(path) for path in sorted(root.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts and ".ehe" not in path.parts}


def _revision():
    code = Path(__file__).resolve().parents[1]
    result = {"source_sha256": digest({path.relative_to(code).as_posix(): sha256(path)
                                       for path in sorted(code.rglob("*.py"))}),
              "git_commit": None, "git_dirty": None}
    checkout = code.parents[1]
    if not (checkout / ".git").exists():
        return result
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=checkout, capture_output=True, timeout=5, text=True)
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=checkout,
                               capture_output=True, timeout=5, text=True)
    except (OSError, subprocess.TimeoutExpired):
        return result
    result.update(git_commit=commit.stdout.strip() if commit.returncode == 0 else None,
                  git_dirty=bool(dirty.stdout.strip()) if dirty.returncode == 0 else None)
    return result


def score(expected, observed):
    errors = []
    for key, value in expected["equals"].items():
        if key not in observed or observed[key] != value:
            errors.append(f"{key}: expected {value!r}, received {observed.get(key)!r}")
    for key, value in expected.get("approx", {}).items():
        actual = observed.get(key)
        if not isinstance(actual, (float, int)) or not math.isclose(actual, value, rel_tol=1e-8, abs_tol=1e-12):
            errors.append(f"{key}: expected numeric {value!r}, received {actual!r}")
    return errors


def _cases(suite, assets):
    for case in suite["cases"]:
        with TemporaryDirectory(prefix=f'ehe-gold-{case["id"]}-') as folder:
            work = Path(folder)
            expected = case["expected"]
            try:
                observed = DRIVERS[case["driver"]](case["input"], assets, work)
                errors = score(expected, observed)
            except Exception as exc:
                observed = {"status": "driver_exception", "exception": type(exc).__name__, "message": str(exc)}
                errors = [f"Evaluator driver raised {type(exc).__name__}"]
            verdict = "not_run" if observed.pop("_skip", False) else "fail" if errors else "pass"
            yield {"id": case["id"], "title": case["title"], "verdict": verdict, "critical": expected.get("critical", []),
                   "errors": errors, "observed": observed, "input_sha256": digest(case["input"]), "expected": expected}


def _wilson(success, total):
    if not total:
        return None
    z = 1.959963984540054
    p = success / total
    middle = (p + z*z / (2 * total)) / (1 + z*z / total)
    width = z * math.sqrt(p*(1-p)/total + z*z/(4*total*total)) / (1 + z*z/total)
    return [max(0.0, middle-width), min(1.0, middle+width)]


def _metrics(rows):
    found = truth = diagnoses_true = diagnoses_predicted = numeric = numeric_ok = 0
    missed = []
    for row in rows:
        expected = row["expected"]
        observed = Counter(row["observed"].get("findings", []))
        critical = Counter(expected.get("critical", []))
        found += sum((critical & observed).values())
        truth += sum(critical.values())
        missed.extend(f'{row["id"]}/{finding}' for finding in (critical - observed).elements())
        if "findings" in expected["equals"]:
            diagnoses_predicted += sum(observed.values())
            diagnoses_true += sum((Counter(expected["equals"]["findings"]) & observed).values())
        if expected.get("approx"):
            numeric += len(expected["approx"])
            failures = score({"equals": {}, "approx": expected["approx"]}, row["observed"])
            numeric_ok += len(expected["approx"]) - len(failures)
    return {"critical_missed": missed, "critical_detected": found, "critical_truth": truth,
            "critical_recall_wilson_95": _wilson(found, truth),
            "diagnosis_precision": diagnoses_true / diagnoses_predicted if diagnoses_predicted else None,
            "precision_numerator": diagnoses_true, "precision_denominator": diagnoses_predicted,
            "numeric_correct": numeric_ok, "numeric_checks": numeric,
            "scope": "Only enumerated gold observations; physical acceptance and population performance unmeasured"}


def run_suite(suite_file: Path | None = None, baseline: dict | None = None, repeat=1) -> dict:
    if type(repeat) is not int or not 1 <= repeat <= 3:
        raise ValueError("Repeat count must be 1–3")
    packaged = Path(str(files("ehe").joinpath("data")))
    assets = packaged / "gold"
    case_file = suite_file.resolve() if suite_file else assets / "core.json"
    suite = load(case_file)
    if suite.get("protocol") != 1 or not isinstance(suite.get("cases"), list) or not suite["cases"]:
        raise ValueError("Invalid evaluator suite protocol")
    ids = [case["id"] for case in suite["cases"]]
    if len(ids) != len(set(ids)) or any(not identifier.isascii() or not identifier.replace("-", "").isalnum() for identifier in ids):
        raise ValueError("Invalid or duplicate evaluator case ID")
    asset_hashes = _files(assets)
    started = now()
    records = list(_cases(suite, assets))
    changes = set()
    for _ in range(repeat - 1):
        another = list(_cases(suite, assets))
        changes.update(item["id"] for original, item in zip(records, another) if original != item)
    tool = kicad_probe()
    counts = Counter(row["verdict"] for row in records)
    counts = {name: counts[name] for name in ("pass", "fail", "not_run")}
    result = {
        "status": "pass" if not counts["fail"] and not counts["not_run"] and not changes else "fail" if counts["fail"] or changes else "not_run",
        "suite": suite["suite"] if suite_file is None else f'external:{suite["suite"]}', "started_at": started, "finished_at": now(),
        "dataset_sha256": digest({"suite": sha256(case_file), "assets": asset_hashes}), "input_hashes": asset_hashes,
        "version": __version__, **_revision(), "python": platform.python_version(), "model": "deterministic_no_model",
        "tools": {"kicad": tool.get("version"), "kicad_status": tool["status"]},
        "config": {"repeat": repeat, "seed": None}, "counts": counts, "cases": records, "kpi": _metrics(records),
        "variation": {"repeats": repeat, "different_cases": sorted(changes)},
        "physical_acceptance": "not_run",
    }
    result["quality_gate"] = quality(result, baseline)
    result["release_gate"] = release_gate(result, load(packaged / "readiness.json"))
    if result["quality_gate"]["status"] == "blocked" and result["status"] == "pass":
        result["status"] = "fail"
    return result
