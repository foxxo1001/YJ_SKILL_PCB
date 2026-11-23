"""Prove a real rule regression blocks release using an expendable source copy."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    with TemporaryDirectory(prefix="ehe-regression-") as folder:
        scope = Path(folder)
        source = scope / "ehe"
        shutil.copytree(ROOT / "src/ehe", source, ignore=shutil.ignore_patterns("__pycache__", ".ehe"))
        target = source / "domain.py"
        code = target.read_text(encoding="utf-8")
        old, new = "if peak > limit:", "if peak > 2 * limit:"
        if code.count(old) != 1:
            raise ValueError("Fault injection requires the current rail detector")
        target.write_text(code.replace(old, new), encoding="utf-8")
        argv = [sys.executable, "-m", "ehe.cli", "eval", "run", "--suite", "core", "--baseline",
                str(args.baseline.resolve()), "--output", str(out)]
        run = subprocess.run(argv, cwd=scope, env={**os.environ, "PYTHONPATH": str(scope), "PYTHONUTF8": "1"},
                             capture_output=True, encoding="utf-8", errors="replace", timeout=100)
        if not out.exists():
            print(run.stderr, file=sys.stderr)
            return 1
    data = json.loads(out.read_text(encoding="utf-8"))
    comparison = data["quality_gate"]["comparison"]
    passed = (run.returncode == 1 and data["release_gate"]["status"] == "blocked"
              and comparison["critical_regression"] and "AT08" in comparison["regressions"]
              and "AT08/RAIL-PEAK" in data["kpi"]["critical_missed"])
    print(json.dumps({"status": "pass" if passed else "fail", "candidate_exit_code": run.returncode,
                      "comparison": comparison, "output": str(out)}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
