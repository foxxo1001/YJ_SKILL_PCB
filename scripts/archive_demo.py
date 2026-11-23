"""Copy portable native transcripts and audit files into the deliverable."""
from pathlib import Path
import argparse
import shutil

from ehe.io import load, save


def native(project, dest):
    files = project / ".ehe" / "eda"
    results = []
    for run in sorted(files.iterdir()):
        execution = load(run / "execution.json")
        name = Path(execution["artifact"]).name
        output = dest / run.name
        output.mkdir(parents=True, exist_ok=False)
        for artifact in run.iterdir():
            if artifact.is_file():
                shutil.copyfile(artifact, output / artifact.name)
        results.append({"target": execution["target"], "exit_code": execution["exit_code"],
                        "tool_version": execution["tool_version"],
                        "report": f"{dest.name}/{run.name}/{name}", "inputs": execution["inputs"]})
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("demo", type=Path)
    args = parser.parse_args()
    base = args.demo.resolve()
    project = base / "design"
    results = native(project, base / "native")
    companion = base / "external_design"
    if companion.is_dir():
        results.extend(native(companion, base / "reference-native"))
    for name in ("events.jsonl", "artifacts.json", "run.json"):
        shutil.copyfile(project / ".ehe" / name, base / name)
    save(base / "artifact-index.json", results)
    print(f"Archived {len(results)} native execution transcripts")


if __name__ == "__main__":
    main()
