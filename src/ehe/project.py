"""Project manifests and packaged starter assets."""
from importlib.resources import files
from pathlib import Path

from .io import inside, load, sha256


def init_project(root: Path) -> dict:
    assets = files("ehe").joinpath("data")
    templates = {
        "project.json": "project.json",
        "goal.yaml": "goal.json",
        "engineering.json": "engineering.json",
        "acceptance.md": "acceptance.md",
    }
    root.mkdir(exist_ok=True, parents=True)
    targets = {inside(root, name): asset for name, asset in templates.items()}
    if any(path.exists() for path in targets):
        raise FileExistsError("One or more project template files already exist")
    for dest, source in targets.items():
        with dest.open("xb") as stream:
            stream.write(assets.joinpath(source).read_bytes())
    return {"status": "created", "project": str(root.resolve()), "files": list(templates)}


def manifest(root: Path) -> dict:
    if not root.is_dir():
        raise ValueError(f"Project directory does not exist: {root}")
    path = inside(root, "project.json")
    data = load(path) if path.is_file() else {}
    if not isinstance(data, dict):
        raise ValueError("project.json must contain an object")
    for key in ("schematic", "pcb", "engineering"):
        if key in data:
            inside(root, data[key])
    return data


def engineering_path(root: Path) -> Path:
    return inside(root, manifest(root).get("engineering", "engineering.json"))


def source_manifest(root: Path, paths: list[Path]) -> dict[str, str]:
    resolved = root.resolve()
    return {
        path.resolve().relative_to(resolved).as_posix(): sha256(path)
        for path in sorted(set(paths)) if path.is_file()
    }


def output_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    base = root / '.ehe'
    path = base / relative
    if not path.is_relative_to(base) or path.resolve() != path:
        raise ValueError('Runtime outputs require a project-local, unlinked .ehe path')
    return path
