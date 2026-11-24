"""Version-scoped EasyEDA Pro native checks on an isolated folder project."""
from dataclasses import asdict
import json
from pathlib import Path
import re
import shutil
from tempfile import mkdtemp
from time import monotonic, sleep

from . import easyeda, easyeda_exports
from .easyeda_source import Document, project_files, sheet_locations
from ..domain import finding, result, status_of
from ..io import digest, load, save, sha256
from ..models import Evidence

OPEN_DOCUMENT = """
const tab = await eda.dmt_EditorControl.openDocument(__CLI__.args.uuid);
const activated = tab ? await eda.dmt_EditorControl.activateDocument(tab) : false;
return {tab, activated};
"""
CURRENT_DOCUMENT = "return await eda.dmt_SelectControl.getCurrentDocumentInfo();"
CHECK_DOCUMENT = """
const expected = __CLI__.args;
const before = await eda.dmt_SelectControl.getCurrentDocumentInfo();
const matches = d => d && d.uuid === expected.uuid && d.documentType === expected.type &&
  d.tabId === expected.tab && d.parentProjectUuid === expected.project;
if (!matches(before)) return {before, checked: false};
const api = expected.type === 3 ? eda.pcb_Drc : eda.sch_Drc;
const passed = await api.check(true, false, false);
const details = await api.check(true, false, true);
const after = await eda.dmt_SelectControl.getCurrentDocumentInfo();
return {before, after, passed, details, checked: Boolean(matches(after))};
"""
DOCUMENT_TYPES = {"erc": 1, "drc": 3}
EXPORT_DOCUMENT = """
const t = __CLI__.args;
const matches = d => d && d.uuid === t.uuid && d.documentType === t.type &&
    d.tabId === t.tab && d.parentProjectUuid === t.project;
const before = await eda.dmt_SelectControl.getCurrentDocumentInfo();
if (!matches(before)) return {before, matched: false};
const capture = async f => {
    try { const v = await f(); return {text: typeof v === 'string' ? v : v && await v.text()}; }
    catch (e) { return {error: String(e)}; }
};
const netlist = await capture(() => t.type === 3 ? eda.pcb_Net.getNetlist('JLCEDA') :
    eda.sch_ManufactureData.getNetlistFile('ehe-netlist', 'JLCEDA'));
const bom = t.bom ? await capture(() => eda.pcb_ManufactureData.getBomFile('ehe-bom', 'csv')) : null;
const after = await eda.dmt_SelectControl.getCurrentDocumentInfo();
return {before, after, matched: Boolean(matches(after)), netlist, bom};
"""


def _enter(program, session, verb, code, arguments, deadline):
    remaining = deadline - monotonic()
    if remaining <= 1:
        return {"status": "blocked", "reason": "EasyEDA review time budget exhausted"}
    timeout = min(int(remaining), 30 if verb == "check" else 12)
    script = " ".join(line.strip() for line in code.strip().splitlines())
    return easyeda.call(program, ["invoke", "--session", session, "--ext-uuid", "eda", "--code", script,
                                  "--args", json.dumps(arguments), "--timeout", str(timeout * 1000)],
                        timeout=timeout + 3)


def _value(outcome):
    value = outcome.get("native", {}).get("value")
    return value if outcome.get("status") == "available" and isinstance(value, dict) else {}


def _native_message(outcome):
    error = outcome.get("native", {}).get("error") or {}
    return error.get("message") or outcome.get("reason") or "Expected editor document was not active"


def _request_timed_out(outcome):
    return (outcome.get("native", {}).get("error") or {}).get("code") == "REQUEST_TIMEOUT"


def _ready(value, document: Document, kind: str, project: str | None) -> bool:
    active = value.get("doc")
    return bool(isinstance(active, dict) and value.get("tab")
                and active.get("uuid") == document.id and active.get("documentType") == DOCUMENT_TYPES[kind]
                and active.get("tabId") == value["tab"] and active.get("parentProjectUuid")
                and (project is None or active["parentProjectUuid"] == project))


def _same_document(value, target) -> bool:
    return all(isinstance(value.get(when), dict) and value[when].get("uuid") == target["uuid"]
                    and value[when].get("documentType") == target["type"]
                    and value[when].get("tabId") == target["tab"]
                    and value[when].get("parentProjectUuid") == target["project"]
                    for when in ("before", "after"))


def _checked(value, target) -> bool:
    return (value.get("checked") is True and isinstance(value.get("passed"), bool)
            and isinstance(value.get("details"), list) and _same_document(value, target))


def _leaves(details):
    for row in details:
        if isinstance(row, dict) and isinstance(row.get("list"), list):
            yield from _leaves(row["list"])
        else:
            yield row


def _suggestion(kind: str, rule: str) -> tuple[str, str]:
    if kind == "drc" and rule in {"No Connection", "Connection Error"}:
        return ("核对所列焊盘和同网络走线，检查断点、换层过孔及铺铜。修复该网络的实际铜连接",
                "运行同一 PCB 的严格 DRC，核对该网络未连接项及相邻净距")
    return ("在嘉立创EDA中定位原生规则项及涉及对象，按项目规则与设计意图修正",
            "在同一 PCB 或原理图页重新运行嘉立创原生检查，并核对受影响对象")


def _from_raw(artifact: Path, version: str) -> dict:
    raw = load(artifact)
    kind, target = raw["kind"], raw["target"]
    executed = _value(raw["execution"])
    if not _checked(executed, target):
        return {**result("blocked", missing=[_native_message(raw["execution"])]), "artifact": str(artifact)}
    evidence = asdict(Evidence.file(artifact, f"EasyEDA:{kind}:{target['uuid']}", "tool_verified",
                                    "EasyEDA editor rule API", version))
    leaves = list(_leaves(executed["details"]))
    findings = []
    unmapped = 0
    for item in leaves:
        if not isinstance(item, dict):
            unmapped += 1
            continue
        explanation = item.get("explanation")
        error_data = explanation.get("errData", {}) if isinstance(explanation, dict) else {}
        error_data = error_data if isinstance(error_data, dict) else {}
        rule = error_data.get("errorType") or item.get("errorType") or item.get("type")
        if not isinstance(rule, str) or not rule:
            unmapped += 1
            continue
        action, verify = _suggestion(kind, rule)
        message = explanation.get("str") if isinstance(explanation, dict) else None
        row = finding(rule, "pcb" if kind == "drc" else "schematic",
                      message if isinstance(message, str) else rule,
                      target["name"], action, verify)
        position = item.get("pos") or error_data.get("position")
        row["scope"].update(document=target["uuid"], net=item.get("net") or error_data.get("net"),
                            object_ids=item.get("objs") or [error_data[key] for key in ("obj1", "obj2") if error_data.get(key)],
                            detail=item.get("obj1") or error_data.get("obj1Suffix"),
                            layer_ids=error_data.get("layerIds"), native_position=position, native_rule=rule)
        row["evidence_ids"] = [evidence["id"]]
        row["verified_by"] = f"EasyEDA Pro {version} native DRC"
        findings.append(row)
    passed = executed["passed"]
    status = "fail" if not passed else "inconclusive" if leaves else "pass"
    gaps = []
    if unmapped or not passed and not leaves:
        gaps.append("Some native diagnostics could not be localized. See the saved native result.")
    if status == "inconclusive":
        gaps.append("Native boolean and detailed rule outcomes disagree")
    return {**result(status, {"pages_checked": 1, "native_violations": len(leaves),
                             "localized_violations": len(findings)}, findings, gaps),
            "level": "E2", "evidence": [evidence], "artifact": str(artifact), "document": target["uuid"],
            "native_passed": passed}


def _check_page(program, session, document: Document, kind, project, out, version, deadline, *, exports=False, bom=False):
    audit = {"kind": kind, "document": document.id, "activation_attempts": []}
    artifact = out / f"{kind}-{document.id}.json"
    for _ in range(8):
        activated = _enter(program, session, "open", OPEN_DOCUMENT, {"uuid": document.id}, deadline)
        audit["activation_attempts"].append(activated)
        value = _value(activated)
        if value.get("tab"):
            current = _enter(program, session, "current", CURRENT_DOCUMENT, {}, deadline)
            audit["activation_attempts"].append(current)
            value = {**value, "doc": _value(current)}
        save(artifact, audit)
        if _ready(value, document, kind, project):
            target = {"uuid": document.id, "name": document.name, "type": DOCUMENT_TYPES[kind],
                      "tab": value["tab"], "project": value["doc"]["parentProjectUuid"]}
            execution = _enter(program, session, "check", CHECK_DOCUMENT, target, deadline)
            save(artifact, {**audit, "target": target, "execution": execution})
            try:
                check = _from_raw(artifact, version)
            except (KeyError, TypeError, ValueError, OSError) as exc:
                check = {**result("inconclusive", missing=[f"Unreadable native evidence: {exc}"]),
                         "artifact": str(artifact)}
            if exports:
                exported = _enter(program, session, "check", EXPORT_DOCUMENT, {**target, "bom": bom}, deadline)
                evidence = out / f"export-{document.id}.json"
                captured = _value(exported)
                admitted = captured.get("matched") is True and _same_document(captured, target)
                save(evidence, {"document": document.id, "target": target, "version": version,
                                "identity_verified": admitted, "execution": exported})
                check["export_artifact"] = str(evidence)
            check["document"] = document.id
            check["editor_project"] = target["project"]
            return check
        if (project is not None and _request_timed_out(activated)) or deadline - monotonic() <= 2:
            break
        sleep(0.8)
    save(artifact, audit)
    return {**result("blocked", missing=[f"Could not activate EasyEDA {document.name} ({document.id})"]),
            "artifact": str(artifact)}


def _schematic_pages(results):
    if not results:
        return result("not_run", missing=["No schematic pages linked to the selected PCB in the project index"])
    return {**result(status_of(results), {"pages_selected": len(results),
                                        "pages_checked": sum(x.get("level") == "E2" for x in results)},
                     [issue for row in results for issue in row["findings"]],
                     [gap for row in results for gap in row["missing_evidence"]]),
            "pages": results, "evidence": [ev for row in results for ev in row.get("evidence", [])]}


def _disabled(status: str, reason: str, receipt_path: Path | None = None) -> dict:
    receipt = {"session_receipt": str(receipt_path)} if receipt_path else {}
    return {name: {**result(status, missing=[reason]), **receipt}
            for name in (*DOCUMENT_TYPES, "schematic_parity", "design_bom", "design_intent")}


def _qualify_snapshot(check: dict, changed: list[str], receipt_path: Path) -> None:
    check["session_receipt"] = str(receipt_path)
    if not check.get("evidence"):
        return
    check["checked_snapshot_status"] = check["status"]
    if changed:
        check["status"] = "inconclusive"
        check["missing_evidence"].append("嘉立创改写了检查副本中的文件，原生结果仅代表编辑器加载的副本。")
        for issue in check["findings"]:
            issue["scope"]["input_revision"] = "editor_copy"
    for page in check.get("pages", []):
        _qualify_snapshot(page, changed, receipt_path)


def _index_digest(index: Path):
    try:
        return digest(load(index))
    except (OSError, ValueError, TypeError):
        return None


def audit(index: Path, board: Document, pages: list[Document], sources: list[Path], inputs: dict, out: Path,
          *, offline=False, bom=False, intent=None) -> dict:
    if offline:
        return _disabled("not_run", "Native checks disabled by --offline")
    target_dir = out / "easyeda"
    target_dir.mkdir(parents=True, exist_ok=False)
    tool = easyeda.detect()
    receipt = {"tool": tool, "project": str(index), "pcb": board.id, "inputs": inputs}
    receipt_path = target_dir / "session.json"
    save(receipt_path, receipt)
    if tool["status"] != "available":
        return _disabled("blocked", tool.get("reason") or "EasyEDA Pro native bridge is not connected", receipt_path)
    program = Path(tool["path"])
    existing = easyeda.call(program, ["session", "list"])
    rows = existing.get("native", {}).get("value")
    if existing["status"] != "available" or not isinstance(rows, list):
        save(receipt_path, {**receipt, "session_inventory": existing})
        return _disabled("blocked", "Could not verify ownership of EasyEDA sessions", receipt_path)

    before = {row.get("sessionId") for row in rows if isinstance(row, dict)}
    folder = Path(mkdtemp(prefix="ehe-easyeda-review-")).resolve()
    copy = folder / "project"
    session = None
    may_be_open = False
    changed = []
    try:
        for path in sources:
            relative = path.relative_to(index.parent)
            destination = copy / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            if sha256(destination) != inputs[relative.as_posix()]:
                raise ValueError(f"EasyEDA input changed before snapshot: {relative}")
        staged = copy / index.name
        index_before = _index_digest(staged)
        may_be_open = True
        opened = easyeda.call(program, ["open", "--path", str(staged), "--headless", "true"], timeout=35)
        receipt["open"] = opened
        value = _value(opened)
        session_id, opened_path = value.get("sessionId"), value.get("path")
        if (not isinstance(session_id, str) or re.fullmatch(r"[a-f0-9-]{36}", session_id, re.I) is None
                or session_id in before or value.get("headless") is not True
                or not opened_path or Path(opened_path).resolve() != staged):
            if opened.get("execution", {}).get("exit_code") not in (None, 0) and not session_id:
                may_be_open = False
            return _disabled("blocked", f"Could not verify isolated EasyEDA session: {_native_message(opened)}",
                             receipt_path)
        session = session_id
        deadline = monotonic() + min(600, 140 + 30 * max(0, len(pages) - 1))
        # An empty folder project may have no focused sheet until the PCB opens.
        sleep(3)
        project = None
        sch = []
        for page in pages:
            observed = _check_page(program, session, page, "erc", project, target_dir, tool["version"],
                                   min(deadline, monotonic() + 46), exports=True)
            project = observed.get("editor_project") or project
            sch.append(observed)
        drc = _check_page(program, session, board, "drc", project, target_dir, tool["version"], deadline,
                          exports=bool(pages) or bom, bom=bom)
        snapshot_sources = project_files(staged)
        try:
            page_index = sheet_locations(copy, pages, snapshot_sources)
        except (OSError, TypeError, ValueError) as exc:
            page_index = {"locations": {}, "unsupported": [f"Schematic source inventory: {exc}"]}
        checks = {"drc": drc, "erc": _schematic_pages(sch),
                  "schematic_parity": easyeda_exports.parity(drc, sch, page_index)}
        if intent is not None:
            checks["design_intent"] = easyeda_exports.design_intent(drc, sch, intent, page_index)
        if bom:
            checks["design_bom"] = easyeda_exports.bom(drc, out)
        after = {path.relative_to(copy).as_posix(): sha256(path) for path in snapshot_sources}
        changed = sorted(name for name in inputs.keys() | after.keys() if inputs.get(name) != after.get(name))
        substantive = list(changed)
        if changed:
            receipt["modified_snapshot_files"] = changed
            receipt["snapshot_after_checks"] = after
            if index.name in changed and index_before and _index_digest(staged) == index_before:
                receipt["equivalent_index_json"] = index_before
                substantive.remove(index.name)
        for check in checks.values():
            _qualify_snapshot(check, substantive, receipt_path)
        return checks
    except (KeyError, OSError, TypeError, ValueError) as exc:
        return _disabled("inconclusive", str(exc), receipt_path)
    finally:
        if session is not None:
            closed = easyeda.call(program, ["session", "close", "--session", session, "--destroy"])
            receipt["close"] = closed
            may_be_open = closed["status"] != "available"
        if may_be_open or changed:
            receipt["retained_editor_copy"] = str(folder)
        else:
            try:
                shutil.rmtree(folder)
            except OSError as exc:
                receipt["cleanup_error"] = str(exc)
        save(receipt_path, receipt)
