"""Locate a compatible EHE runtime from any project, using only the Python standard library."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

MIN_VERSION = (0, 1, 1)
CONFIG = Path(__file__).resolve().parents[1] / "runtime.json"


def _candidates():
    override = os.environ.get("EHE_RUNTIME")
    if override:
        exe = Path(override).expanduser()
        if not exe.is_absolute():
            raise ValueError("EHE_RUNTIME must point to an absolute executable path")
        yield "override", [str(exe)]
        return
    search = [entry for entry in os.get_exec_path() if entry and Path(entry).is_absolute()]
    discovered = shutil.which("ehe", path=os.pathsep.join(search)) if search else None
    if discovered and Path(discovered).resolve().parent != Path.cwd().resolve():
        yield "PATH", [str(Path(discovered).resolve())]
    if CONFIG.is_file():
        configured = json.loads(CONFIG.read_text(encoding="utf-8")).get("python")
        if configured:
            python = Path(configured)
            if not python.is_absolute():
                raise ValueError("Installed runtime.json must point to an absolute Python executable")
            yield "installed", [str(python), "-I", "-m", "ehe.cli"]
    yield "current Python", [sys.executable, "-I", "-m", "ehe.cli"]


def resolve():
    attempts, seen = [], set()
    for source, command in _candidates():
        key = tuple(command)
        if key in seen:
            continue
        seen.add(key)
        executable = Path(command[0])
        if not executable.is_file() or (sys.platform == "win32" and executable.suffix.lower() != ".exe"):
            attempts.append({"source": source, "reason": "executable unavailable or not a native Windows executable"})
            continue
        try:
            tested = subprocess.run([*command, "--version"], cwd=executable.parent,
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            attempts.append({"source": source, "reason": str(exc)})
            continue
        version = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", tested.stdout.strip())
        if tested.returncode == 0 and version and tuple(map(int, version.groups())) >= MIN_VERSION:
            return {"status": "available", "source": source, "argv": command, "version": version[0]}
        attempts.append({"source": source, "reason": "requires EHE 0.1.1 or newer",
                         "version": version[0] if version and tested.returncode == 0 else None})
    return {"status": "blocked", "reason": "No compatible EHE runtime found", "attempts": attempts}


def main():
    try:
        resolved = resolve()
    except (OSError, TypeError, ValueError, AttributeError) as exc:
        resolved = {"status": "blocked", "reason": str(exc)}
    args = sys.argv[1:]
    if args == ["--resolve"] or resolved["status"] != "available":
        print(json.dumps(resolved, ensure_ascii=True))
        return 0 if resolved["status"] == "available" else 3
    try:
        return subprocess.run([*resolved["argv"], *args]).returncode
    except OSError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
