"""Full engineering review with explicit coverage and a native design handoff check."""
from csv import Error as CsvError
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from uuid import uuid4

from . import advanced, reality
from .adapters import generic, kicad
from .doctor import kicad_probe
from .domain import CHECKS, result, run_domain, status_of
from .evalops.gate import native_gate
from .io import atomic_write, now, save, sha256
from .models import Evidence
from .project import engineering_path, output_path, source_manifest

ALL_CHECKS = {**CHECKS, **reality.CHECKS, **advanced.CHECKS}
TOPICS = {
    "rails": "电源与峰值负载", "pins": "引脚与电压域", "bom": "BOM 与位号", "energy": "能耗",
    "cost": "成本与交期", "mechanical": "装配、器件高度、线束与插拔", "thermal": "温升与降额",
    "transient": "最坏工况掉压", "tolerance": "器差与温漂", "optimization": "方案取舍",
    "analog": "模拟采样", "motor": "电机启动与堵转", "buck": "开关电源",
    "realtime": "实时性", "fault": "故障保护", "return_path": "参考面与正常回流",
    "emi_loop": "EMI 开关电流回路", "si": "信号完整性", "pdn": "去耦与电源完整性",
}
REGRESSIONS = {
    "power": ["电源轨", "启动及峰值负载", "温升", "去耦与回流"],
    "mechanical": ["板框", "固定点", "插拔与线束", "布局与 DRC"],
    "thermal": ["器件降额", "最坏损耗", "实物测温"],
    "return_path": ["参考面与换层", "信号完整性", "EMI"],
    "emi": ["开关电流环路", "模拟采样", "EMI"],
    "schematic": ["ERC", "引脚与电压域", "原理图与 PCB 一致性"],
    "pcb": ["DRC", "布局与回流", "原理图与 PCB 一致性"],
}


def _native_review(root: Path, domain: str, inputs: dict, *, parity=False) -> dict:
    try:
        review = kicad.run(root, domain, parity=parity)
        status, reason = native_gate(root, domain, review, inputs)
        return {**review, "status": status, "reason": reason,
                "verified_by": "ehe-independent-gate"}
    except (ValueError, OSError, KeyError) as exc:
        return {**result("inconclusive", missing=[str(exc)]), "reason": "native evidence unavailable"}


def _pcb_review(root: Path, inputs: dict) -> dict:
    combined = _native_review(root, "pcb", inputs, parity=True)
    if combined["status"] in {"pass", "fail"}:
        ordinary, parity = [], []
        for finding in combined["findings"]:
            scope = finding["scope"]["object"]
            (parity if scope == "schematic_parity" else ordinary).append(finding)
        return {
            key: {**combined, "topic": topic, "findings": findings, "status": "fail" if findings else "pass",
                  "reason": f"{len(findings)} scoped native rule violations"}
            for key, topic, findings in (("drc", "PCB DRC", ordinary),
                                         ("schematic_parity", "原理图与 PCB 网络及器件一致性", parity))
        }
    return {
        "drc": {"topic": "PCB DRC", **_native_review(root, "pcb", inputs)},
        "schematic_parity": {"topic": "原理图与 PCB 网络及器件一致性", **combined},
    }


def _board_outline(root: Path) -> dict:
    try:
        board = kicad.target_for(root, "pcb")
        if not board:
            return result("not_run", missing=["KiCad PCB file with Edge.Cuts"])
        canonical = kicad.geometry(board)
        outline = canonical["project"]["outline"]
        missing = [] if outline["status"] == "pass" else ["Complete measurable Edge.Cuts geometry"]
        return {**result(outline["status"], outline["metrics"], missing=missing),
                "scope": outline["scope"], "unsupported": outline["unsupported"],
                "evidence": canonical["evidence"], "level": "E3"}
    except (KeyError, TypeError, ValueError, OSError) as exc:
        return result("inconclusive", missing=[f"PCB geometry: {exc}"])


def _bom_export(root: Path, out: Path, sources: list[Path], inputs: dict) -> dict:
    schematic = kicad.target_for(root, "schematic")
    tool = kicad_probe()
    if schematic is None or tool["status"] != "available":
        return result("not_run", missing=["KiCad schematic and available KiCad CLI to export BOM"])
    if schematic not in sources:
        return result("blocked", missing=["Schematic unavailable in the isolated project inputs"])
    raw = out / "bom.csv"
    with TemporaryDirectory(prefix="ehe-design-bom-") as temp:
        project = Path(temp) / "project"
        hashes = kicad.copy_inputs(root, project, sources)
        if hashes != inputs:
            return result("inconclusive", missing=["Native inputs changed before BOM export"])
        destination = Path(temp) / "bom.csv"
        argv = [tool["path"], "sch", "export", "bom", "--fields", "Reference,Value,Footprint,MPN,QUANTITY",
                "--labels", "Reference,Value,Footprint,MPN,Qty", "--output", str(destination),
                schematic.relative_to(root).as_posix()]
        started = now()
        rc, stdout, stderr, error = kicad.capture(argv, project, 60)
        execution = {"argv": argv, "cwd": str(project), "started_at": started, "finished_at": now(),
                     "exit_code": rc, "tool_version": tool["version"], "inputs": hashes, "error": error}
        save(out / "bom-execution.json", execution)
        atomic_write(out / "bom-stdout.txt", stdout)
        atomic_write(out / "bom-stderr.txt", stderr)
        if rc != 0 or not destination.is_file():
            return {**result("blocked", missing=["Successful native KiCad BOM export"]), "execution": execution}
        shutil.copyfile(destination, raw)
    exported = generic.ingest(raw, "kicad")
    check = exported["evaluation"]
    missing_mpn = [ref for row in exported["rows"] if not row["mpn"] for ref in row["references"]]
    extra = result("inconclusive" if missing_mpn else "pass",
                   missing=[f"Manufacturer part number: {ref}" for ref in missing_mpn])
    return {**check, "status": status_of([check, extra]), "format": "KiCad CSV",
            "missing_evidence": check["missing_evidence"] + extra["missing_evidence"],
            "evidence": exported["canonical"]["evidence"], "artifact": str(raw), "execution": execution}


def _recommendations(coverage: dict) -> list[dict]:
    suggestions = []
    for check_name, checked in coverage.items():
        for finding in checked.get("findings", []):
            suggestions.append({
                "check": check_name, "finding": finding["id"], "severity": finding["severity"],
                "where": finding["scope"], "why": finding["mechanism"], "change": finding["recommended_action"],
                "affected_checks": REGRESSIONS.get(finding["category"], [checked["topic"]]),
                "verification": finding["verification_method"], "evidence_ids": finding["evidence_ids"],
                "finding_status": finding["status"], "change_status": "proposal",
            })
    return sorted(suggestions, key=lambda row: (row["severity"], row["check"], row["finding"]))


def full_review(root: Path, model: Path | None = None, *, design_verification=False) -> dict:
    root = root.resolve()
    model = model.resolve() if model is not None else engineering_path(root)
    profile = "design_verification" if design_verification else "full"
    sources = kicad.native_inputs(root)
    inputs = source_manifest(root, sources)
    original_model_hash = sha256(model) if model.is_file() else None
    out = output_path(root, f"reviews/{uuid4().hex}")
    coverage = {
        "erc": {"topic": "原理图 ERC", **_native_review(root, "schematic", inputs)},
        **_pcb_review(root, inputs),
        "board_outline": {"topic": "板框坐标外形与标称板厚", **_board_outline(root)},
    }
    try:
        analyzed = run_domain(model, ALL_CHECKS)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        analyzed = {"checks": {name: result("inconclusive", missing=[f"Invalid engineering input: {exc}"])
                               for name in ALL_CHECKS}, "evidence": []}
    coverage.update({name: {"topic": TOPICS[name], **check} for name, check in analyzed["checks"].items()})
    coverage["physical"] = {"topic": "实物验证（EMC、温升、掉压和装配等适用指标）",
                            **result("not_run", missing=["Physical measurements and independent acceptance evidence"])}
    if design_verification:
        try:
            bom = _bom_export(root, out, sources, inputs)
        except (ValueError, OSError, CsvError, KeyError) as exc:
            bom = result("inconclusive", missing=[f"Native BOM evidence: {exc}"])
        coverage["design_bom"] = {"topic": "KiCad BOM 与采购料号", **bom}
    consistent = (inputs == source_manifest(root, kicad.native_inputs(root))
                  and original_model_hash == (sha256(model) if model.is_file() else None))
    if not consistent:
        coverage["revision"] = {"topic": "审查版本一致性", **result(
            "inconclusive", missing=["Project or engineering input changed during review. Review the new revision."])}
    native_keys = ("erc", "drc", "schematic_parity")
    eda_status = status_of(coverage[name] for name in native_keys) if consistent else "inconclusive"
    handoff_status = (status_of(coverage[name] for name in (*native_keys, "board_outline", "design_bom"))
                      if consistent else "inconclusive") if design_verification else None
    report = {
        "status": status_of(coverage.values()), "profile": profile, "project": str(root), "eda_status": eda_status,
        **({"handoff_status": handoff_status} if design_verification else {}),
        "created_at": now(), "inputs": inputs, "model": str(model), "model_sha256": original_model_hash,
        "coverage": coverage, "recommendations": _recommendations(coverage),
        "evidence": analyzed["evidence"] + [e for row in coverage.values() for e in row.get("evidence", [])],
        "report_path": str(out / "review.json"),
    }
    save(out / "review.json", report)
    return report
