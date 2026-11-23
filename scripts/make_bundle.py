"""Build and verify the complete source/evidence/wheel archive."""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ("docs", "examples", "logs", "schemas", "scripts", "skills", "src", "tests")
FILES = (".gitignore", "AGENTS.md", "CHANGELOG.md", "LICENSE", "README.md", "pyproject.toml")
SKIP = {"__pycache__", ".ehe", ".git", ".venv", "external_design"}


def included(relative):
    return not any(part in SKIP or part.endswith(".egg-info") for part in relative.parts)


def bundle(output: Path, wheel: Path):
    sources = [ROOT / name for name in FILES]
    for name in DIRECTORIES:
        sources.extend(path for path in (ROOT / name).rglob("*")
                       if path.is_file() and included(path.relative_to(ROOT)))
    sources = sorted(sources)
    entries = [(path, f"ehe/{path.relative_to(ROOT).as_posix()}") for path in sources]
    entries.append((wheel, f"ehe/dist/{wheel.name}"))
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "x", compression=ZIP_DEFLATED, compresslevel=8) as archive:
        SHA = {}
        for source, archive_path in entries:
            raw = source.read_bytes()
            SHA[archive_path] = hashlib.sha256(raw).hexdigest()
            archive.writestr(archive_path, raw)
        archive.writestr("ehe/BUNDLE-MANIFEST.json", json.dumps(SHA, sort_keys=True, indent=2) + "\n")
    with ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP CRC verification failed")
        for name, expected in SHA.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError(f"Invalid bundle entry {name}")
    with output.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    with output.with_suffix(".zip.sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {output.name}\n")
    print(json.dumps({"file": str(output), "sha256": digest, "verified_files": len(SHA),
                      "bytes": output.stat().st_size}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    args = parser.parse_args()
    bundle(args.output.resolve(), args.wheel.resolve())
