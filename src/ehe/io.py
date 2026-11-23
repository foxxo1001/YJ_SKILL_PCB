"""Reproducible data and crash-safe file writes."""
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def format_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def load(path: Path):
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError(f"Structured input exceeds 16 MiB: {path}")
    text = path.read_text(encoding="utf-8-sig")
    return yaml.safe_load(text) if path.suffix.lower() in {".yml", ".yaml"} else json.loads(text)


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save(path: Path, value: object) -> None:
    atomic_write(path, format_json(value))


def inside(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if Path(relative).is_absolute() or not candidate.is_relative_to(root.resolve()):
        raise ValueError(f"Input outside project: {relative}")
    return candidate
