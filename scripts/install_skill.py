"""Install the shared Skill into project-local or user-level host locations."""
import argparse
import hashlib
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
SKILL = "embedded-hardware-engineer"
HOSTS = {"codex": ".agents/skills", "claude": ".claude/skills"}


def manifest(path):
    return {entry.relative_to(path).as_posix(): hashlib.sha256(entry.read_bytes()).hexdigest()
            for entry in path.rglob("*") if entry.is_file()}


def install(project: Path, scope: str, hosts: list[str]):
    root = (Path.home() if scope == "user" else project).resolve()
    source = ROOT / "skills" / SKILL
    expected = manifest(source)
    if "SKILL.md" not in expected:
        raise ValueError(f"Skill source missing: {source}")
    targets = {host: root / HOSTS[host] / SKILL for host in hosts}
    for host, target in targets.items():
        if target.resolve() != target:
            raise ValueError(f"Linked {host} Skill install path: {target}")
        if target.exists() and (not target.is_dir() or manifest(target) != expected):
            raise FileExistsError(f"Existing {host} Skill differs: {target}")
    result = {}
    for host, target in targets.items():
        if target.exists():
            result[host] = ("already_installed", str(target))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target)
            result[host] = ("installed", str(target))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--scope", choices=("project", "user"), default="project")
    parser.add_argument("--host", choices=(*HOSTS, "both"), default="both")
    args = parser.parse_args()
    for host, (status, location) in install(args.project, args.scope, list(HOSTS) if args.host == "both" else [args.host]).items():
        print(f"{host} {status} {location}")


if __name__ == "__main__":
    main()
