"""Sync original redistributable engineering fixtures into package data for wheel-based evals."""
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
dest = root / "src/ehe/data/gold"
shutil.copyfile(root / "examples/engineering-model/engineering.json", dest / "models.json")
shutil.copytree(root / "examples/continuity", dest / "continuity", dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(".ehe", "README.md"))
