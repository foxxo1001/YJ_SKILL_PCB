"""Fixed read-only operations using the EasyEDA Pro native CLI."""
import json
from pathlib import Path
import re
import subprocess

from ..doctor import locate, windows_install
from ..io import load, now, sha256

SCRIPT = (
    "const p = await eda.dmt_Project.getCurrentProjectInfo(); "
    "const d = await eda.dmt_SelectControl.getCurrentDocumentInfo(); "
    "const v = await eda.sys_Environment.getEditorCurrentVersion(); "
    "const pcbUuid = __CLI__.args?.pcbUuid; "
    "const pcb = pcbUuid ? await eda.dmt_Pcb.getPcbInfo(pcbUuid) : null; "
    "return {editorVersion: v, project: p ? {uuid: p.uuid, name: p.name} : null, "
    "document: d ? {uuid: d.uuid, documentType: d.documentType} : null, "
    "indexedPcb: pcb ? {uuid: pcb.uuid, title: pcb.title ?? pcb.name} : null};"
)


def executable() -> Path | None:
    installed = windows_install(r"嘉立创EDA.*专业版|EasyEDA Pro", "lceda-pro.exe")
    return locate("EHE_EASYEDA_CLI", "easyeda-pro", [installed] if installed else [])


def call(program: Path, arguments: list[str], timeout=12) -> dict:
    argv = [str(program), *arguments]
    provenance = {"argv": argv, "cwd": str(program.parent), "started_at": now()}
    try:
        completed = subprocess.run(argv, cwd=program.parent, capture_output=True,
                                   encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "blocked", "reason": str(exc),
                "execution": {**provenance, "finished_at": now(), "exit_code": None}}
    execution = {**provenance, "finished_at": now(), "exit_code": completed.returncode, "stdout": completed.stdout,
                 "stderr": completed.stderr}
    try:
        envelope = json.loads(completed.stdout)
    except ValueError:
        return {"status": "blocked", "reason": "CLI returned no native JSON envelope", "execution": execution}
    if not isinstance(envelope, dict) or not envelope.get("ok") or completed.returncode != 0:
        return {"status": "blocked", "reason": "Native CLI rejected the operation",
                "execution": execution, "native": envelope}
    return {"status": "available", "native": envelope, "execution": execution}


def detect() -> dict:
    program = executable()
    if not program:
        return {"status": "not_run", "reason": "Native executable not located", "level": "E0"}
    outcome = call(program, ["doctor"])
    details = outcome.get("native", {}).get("value", {})
    if not isinstance(details, dict):
        return {"status": "blocked", "reason": "Unexpected native doctor value", "probe": outcome}
    version = details.get("bridgeVersion")
    ready = details.get("connected") is True and details.get("versionMatch") is True
    return {"status": "available" if outcome["status"] == "available" and ready else "blocked",
            "path": str(program), "version": version, "level": "E0", "probe": outcome,
            "native_rule_checks": "not_run", "native_writeback": False}


def session_list() -> dict:
    tool = detect()
    if tool["status"] != "available":
        return tool
    result = call(Path(tool["path"]), ["session", "list"])
    return {**result, "tool_version": tool["version"], "level": "E0"}


def inspect_session(session: str, expected_project: Path | None = None, *, opened: dict | None = None) -> dict:
    if re.fullmatch(r"(?:[0-9]+|[a-fA-F0-9-]{20,64})", session) is None:
        raise ValueError("Session must be a native numeric render ID or UUID")
    sessions = session_list()
    if sessions["status"] != "available":
        return sessions
    values = sessions["native"]["value"]
    candidate = next((item for item in values if session in {str(item.get("sessionId")), str(item.get("renderId"))}), None)
    if not candidate or candidate.get("status") != "alive":
        return {"status": "blocked", "reason": "Matching alive session unavailable", "level": "E0"}
    native_path = candidate.get("path")
    if not native_path and opened and opened.get("sessionId") == candidate.get("sessionId"):
        native_path = opened.get("path")
    correct_path = (native_path and expected_project and
                    Path(native_path).resolve() == expected_project.resolve())
    if native_path and not correct_path or not native_path and expected_project:
        return {"status": "blocked", "reason": "Expected project does not match the specified session", "level": "E0"}
    pcbs = []
    if expected_project and expected_project.suffix.lower() == ".eprj3":
        pcbs = [entry["id"] for entry in project_inventory(expected_project)["documents"] if entry["kind"] == "pcbs"]
    pcb_uuid = pcbs[0] if pcbs else None
    program = executable()
    outcome = call(program, ["invoke", "--session", str(candidate["sessionId"]), "--ext-uuid", "eda",
                             "--timeout", "8000", "--code", SCRIPT, "--args", json.dumps({"pcbUuid": pcb_uuid})])
    value = outcome.get("native", {}).get("value")
    if outcome["status"] == "available" and isinstance(value, dict) and isinstance(value.get("editorVersion"), str):
        indexed = value.get("indexedPcb")
        matched = isinstance(indexed, dict) and pcb_uuid and indexed.get("uuid") == pcb_uuid
        inspected = bool(matched or value.get("project"))
        return {**outcome, "status": "inspected" if inspected else "inconclusive",
                "level": "E1" if matched else "E0", "tool_version": sessions["tool_version"],
                "reason": "" if inspected else "No focused design or matching PCB metadata returned"}
    return {**outcome, "status": "blocked", "level": "E0",
            "reason": outcome.get("reason", "Read-only API returned no versioned project metadata")}


def project_inventory(path: Path) -> dict:
    if path.suffix.lower() != '.eprj3':
        raise ValueError('Expected an EasyEDA Pro eprj3 project index')
    data = load(path)
    if data.get('format') != 'folder' or not isinstance(data.get('profile'), dict):
        raise ValueError('Unsupported eprj3 project index')
    documents = []
    for kind in ('boards', 'schematics', 'sheets', 'pcbs', 'panels'):
        group = data['profile'].get(kind, {})
        if not isinstance(group, dict):
            raise ValueError(f'Invalid eprj3 profile group {kind}')
        for key, doc in group.items():
            if re.fullmatch(r'[0-9a-fA-F]{16,32}', key) is None or doc.get('uuid', key) != key:
                raise ValueError(f'Invalid eprj3 document identity in {kind}')
            documents.append({'id': key, 'kind': kind, 'name': doc.get('title', doc.get('name')),
                              'locator': f'/profile/{kind}/{key}'})
    return {'status': 'parsed' if documents else 'inconclusive', 'level': 'E1', 'native_rule_checks': 'not_run',
            'evidence_kind': 'documented', 'source': str(path.resolve()), 'sha256': sha256(path),
            'documents': documents, 'project_name': data.get('name'), 'nets': [], 'geometry': []}
