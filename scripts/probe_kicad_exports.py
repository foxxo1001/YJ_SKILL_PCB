"""Verify E1 imports against actual KiCad-generated exports."""
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from ehe.adapters.generic import ingest
from ehe.doctor import kicad_probe
from ehe.io import sha256


def main():
    source = Path(__file__).resolve().parents[1] / "examples" / "continuity"
    tool = kicad_probe()
    if tool["status"] != "available":
        print(json.dumps(tool, ensure_ascii=False))
        return 3
    transcript = []
    with TemporaryDirectory(prefix="ehe-ki-exports-") as folder:
        project = Path(folder) / "fixture"
        shutil.copytree(source, project, ignore=shutil.ignore_patterns(".ehe"))
        sch = project / "continuity.kicad_sch"
        before = sha256(sch)
        for mode, flags, suffix in [("netlist", ["--format", "kicadxml"], "xml"), ("bom", [], "csv")]:
            output = Path(folder) / f"export.{suffix}"
            argv = [tool["path"], "sch", "export", mode, *flags, "-o", str(output), sch.name]
            run = subprocess.run(argv, capture_output=True, encoding="utf-8", errors="replace", cwd=project, timeout=45)
            evidence = {"argv": argv, "exit_code": run.returncode, "stdout": run.stdout, "stderr": run.stderr}
            if not run.returncode and output.exists():
                artifact = ingest(output, "kicad")
                evidence["import"] = artifact
            transcript.append(evidence)
        valid = (before == sha256(sch) and all(row.get("import", {}).get("level") == "E1" for row in transcript)
                 and transcript[0]["import"]["canonical"]["nets"][0]["name"] == "/LINK"
                 and transcript[1]["import"]["evaluation"]["metrics"]["unique_refs"] == 2)
    print(json.dumps({"status": "pass" if valid else "fail", "tool": tool["version"],
                      "execution": transcript, "source_sha256": before}, ensure_ascii=False, indent=2))
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
