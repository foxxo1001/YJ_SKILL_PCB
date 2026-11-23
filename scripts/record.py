"""Run a command and retain stdout, stderr and exit code."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command[0] == "PY":
        command[0] = sys.executable
    start = datetime.now(timezone.utc)
    result = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace",
                            env={**os.environ, "PYTHONUTF8": "1"})
    end = datetime.now(timezone.utc)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.write_text(
        f"start_utc={start.isoformat()}\nend_utc={end.isoformat()}\n"
        f"cwd={Path.cwd()}\nargv={json.dumps(command, ensure_ascii=False)}\n"
        f"exit_code={result.returncode}\n\n[stdout]\n{result.stdout}\n[stderr]\n{result.stderr}",
        encoding="utf-8",
    )
    print(f"{args.log}: exit {result.returncode}")
    if result.returncode:
        print(result.stderr[-4000:])
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
