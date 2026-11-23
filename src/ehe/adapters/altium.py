"""Read-only Altium project inventory and generic export import."""
import configparser
from pathlib import Path, PureWindowsPath

from ..doctor import windows_install
from ..io import sha256
from .generic import ingest


def detect() -> dict:
    program = windows_install(r"^Altium Designer", "X2.EXE")
    return {"status": "installed_unverified" if program else "not_run",
            "path": str(program) if program else None, "native_rule_checks": "blocked",
            "level": "E1", "native_writeback": False,
            "reason": "No native scripting or validated licensing session is enabled"}


def project_inventory(path: Path) -> dict:
    if path.suffix.lower() != ".prjpcb" or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("Expected a text Altium PrjPcb project, up to 4 MiB")
    contents = path.read_bytes()
    encoding = "utf-16" if contents.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    try:
        text = contents.decode(encoding)
    except UnicodeDecodeError:
        text = contents.decode("cp1252")
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise ValueError(f"Cannot parse Altium project manifest: {exc}") from exc
    documents = []
    for section in parser.sections():
        if section.casefold().startswith("document") and parser.has_option(section, "DocumentPath"):
            name = parser.get(section, "DocumentPath").strip('"')
            entry = PureWindowsPath(name)
            documents.append({"locator": f"section:{section}/DocumentPath", "path": name,
                              "in_project": not entry.is_absolute() and not entry.drive and not entry.root and ".." not in entry.parts})
    return {"status": "parsed" if documents else "inconclusive", "level": "E0",
            "sha256": sha256(path), "source": str(path), "evidence_kind": "documented",
            "documents": documents, "native_rule_checks": "not_run", "geometry": [], "nets": []}


def exported(path: Path) -> dict:
    return ingest(path, "altium")
