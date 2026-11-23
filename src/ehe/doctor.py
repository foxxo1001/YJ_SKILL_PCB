"""Read-only executable discovery and version probes."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__


def windows_install(pattern: str, executable: str) -> Path | None:
    if sys.platform != "win32":
        return None
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (0, winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                                    access=winreg.KEY_READ | view) as directory:
                    for index in range(winreg.QueryInfoKey(directory)[0]):
                        try:
                            with winreg.OpenKey(directory, winreg.EnumKey(directory, index)) as key:
                                name, _ = winreg.QueryValueEx(key, "DisplayName")
                                if re.search(pattern, str(name), re.I):
                                    folder, _ = winreg.QueryValueEx(key, "InstallLocation")
                                    path = Path(folder) / executable
                                    if folder and path.is_file():
                                        return path.resolve()
                        except OSError:
                            continue
            except OSError:
                continue
    return None


def locate(env: str, command: str, candidates: list[Path]) -> Path | None:
    configured = os.environ.get(env)
    if configured is not None:
        path = Path(configured).expanduser()
        return path.resolve() if path.is_file() else None
    which = shutil.which(command)
    if which:
        return Path(which).resolve()
    return next((path.resolve() for path in candidates if path.is_file()), None)


def kicad_path() -> Path | None:
    candidates = [
        Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"),
        *sorted(Path(os.environ.get("ProgramFiles", "C:/Program Files"), "KiCad").glob("*/bin/kicad-cli.exe"),
                reverse=True),
    ]
    if located := locate("EHE_KICAD_CLI", "kicad-cli", candidates):
        return located
    if "EHE_KICAD_CLI" not in os.environ:
        return windows_install(r"^KiCad(\s|$)", "bin/kicad-cli.exe")
    return None


def probe(path: Path | None, *args: str) -> dict:
    if not path:
        return {"status": "not_run", "reason": "executable_not_found"}
    command = [str(path), *args]
    try:
        run = subprocess.run(command, capture_output=True, encoding="utf-8",
                             errors="replace", timeout=8, cwd=path.parent)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"path": str(path), "status": "blocked", "reason": str(exc), "argv": command}
    return {"path": str(path), "status": "available" if run.returncode == 0 else "blocked",
            "exit_code": run.returncode, "argv": command, "stdout": run.stdout.strip(),
            "stderr": run.stderr.strip()}


def kicad_probe() -> dict:
    result = probe(kicad_path(), "version")
    if result["status"] == "available":
        version = re.search(r"\b\d+\.\d+\.\d+\b", result["stdout"])
        if version:
            result["version"] = version[0]
        else:
            result.update(status="blocked", reason="unrecognized_version")
    return result


def doctor() -> dict:
    easy_path = windows_install(r"嘉立创EDA.*专业版|EasyEDA Pro", "lceda-pro.exe")
    altium_path = windows_install(r"^Altium Designer", "X2.EXE")
    return {
        "ehe": __version__, "python": sys.version.split()[0], "platform": sys.platform,
        "kicad": kicad_probe(),
        "easyeda_desktop": {"path": str(easy_path) if easy_path else None,
                           "status": "installed_unverified" if easy_path else "not_run"},
        "altium_desktop": {"path": str(altium_path) if altium_path else None,
                          "status": "installed_unverified" if altium_path else "not_run"},
        "generic": {"status": "available", "level": "E1"},
    }
