"""Install the shared Skill and register the runtime for independent sessions."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
SKILL = "embedded-hardware-engineer"
HOSTS = {"codex": ".agents/skills", "claude": ".claude/skills", "zcode": ".zcode/skills"}
# "both" predates ZCode and keeps its original two hosts; "all" covers every host.
GROUPS = {"both": ("codex", "claude"), "all": tuple(HOSTS)}
GENERATED = {"runtime.json", "install.json"}


def manifest(path):
    return {entry.relative_to(path).as_posix(): hashlib.sha256(entry.read_bytes()).hexdigest()
            for entry in path.rglob("*") if entry.is_file()
            and "__pycache__" not in entry.relative_to(path).parts
            and entry.relative_to(path).as_posix() not in GENERATED}


def runtime_python() -> str | None:
    env_dir = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    for candidate in (Path(sys.executable).absolute(), ROOT / ".venv" / env_dir):
        if not candidate.is_file():
            continue
        try:
            tested = subprocess.run([str(candidate), "-I", "-m", "ehe.cli", "--version"],
                                    cwd=ROOT, capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if tested.returncode == 0:
            return str(candidate)
    return None


def read_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _known_install(target: Path, current: dict) -> bool:
    receipt = read_json(target / "install.json", {})
    if receipt.get("skill") == SKILL and receipt.get("files") == current:
        return True
    legacy = read_json(ROOT / "scripts/legacy-skill-0.1.0.json", {})
    return legacy == current


def _replace(source, target, expected, runtime):
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".ehe-skill-install-", dir=parent) as temporary:
        stage = Path(temporary) / "new"
        old = Path(temporary) / "previous"
        shutil.copytree(source, stage, ignore=shutil.ignore_patterns("__pycache__"))
        write_json(stage / "runtime.json", {"python": runtime})
        write_json(stage / "install.json", {"skill": SKILL, "files": expected})
        if target.exists():
            target.rename(old)
        try:
            stage.rename(target)
        except OSError:
            if old.exists():
                old.rename(target)
            raise


def install(project: Path, scope: str, hosts: list[str], *, upgrade=False, codex_home=False):
    if codex_home and (scope != "user" or "codex" not in hosts):
        raise ValueError("--codex-home requires a user-level Codex install")
    root = (Path.home() if scope == "user" else project).resolve()
    source = ROOT / "skills" / SKILL
    expected = manifest(source)
    if "SKILL.md" not in expected:
        raise ValueError(f"Skill source missing: {source}")
    runtime = runtime_python()
    result, staged = {}, {}
    for host in hosts:
        # A user-level skills directory can be an existing junction. Keep project installs inside their root.
        host_root = root / HOSTS[host]
        if host == "codex" and codex_home:
            host_root = Path(os.environ.get("CODEX_HOME") or root / ".codex").expanduser() / "skills"
        parent = host_root.resolve()
        target = parent / SKILL
        if (scope == "project" and not parent.is_relative_to(root)) or target.resolve() != target:
            raise ValueError(f"Linked {host} Skill install path: {target}")
        if target.exists() and not target.is_dir():
            raise FileExistsError(f"Existing {host} Skill path is not a directory: {target}")
        if target.exists():
            current = manifest(target)
            if current != expected and (not upgrade or not _known_install(target, current)):
                raise FileExistsError(f"Existing {host} Skill is customized or requires --upgrade: {target}")
            old_runtime = read_json(target / "runtime.json", {})
            if current == expected and old_runtime == {"python": runtime} and (target / "install.json").is_file():
                result[host] = ("already_installed", str(host_root / SKILL))
                continue
        staged[host] = (target, target.exists(), str(host_root / SKILL))
    for host, (target, already_present, display_path) in staged.items():
        _replace(source, target, expected, runtime)
        result[host] = ("upgraded" if already_present else "installed", display_path)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--scope", choices=("project", "user"), default="project")
    parser.add_argument("--host", choices=(*HOSTS, *GROUPS), default="both")
    parser.add_argument("--upgrade", action="store_true", help="Replace a previously installed, unmodified EHE Skill")
    parser.add_argument("--codex-home", action="store_true",
                        help="Use the Codex compatibility root when .agents/skills is externally managed")
    args = parser.parse_args()
    hosts = list(GROUPS.get(args.host, (args.host,)))
    installed = install(args.project, args.scope, hosts, upgrade=args.upgrade, codex_home=args.codex_home)
    for host, (status, location) in installed.items():
        print(f"{host} {status} {location}")


if __name__ == "__main__":
    main()
