"""Test inputs flow to product entry points. Expected values remain in the evaluator."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

from .. import advanced, domain, reality
from ..adapters import generic, kicad
from ..doctor import kicad_probe
from ..io import inside, load, save, sha256
from ..knowledge import admit, claims, holdout_registry, search
from ..project import source_manifest
from ..requirements import analyze
from ..runtime.engine import decide, resume, start
from ..runtime.plan import impact
from .release import BRAND, compare


def _new_model(data, path):
    keys = path.split("/")
    parent = data
    for key in keys[:-1]:
        parent = parent[int(key)] if isinstance(parent, list) else parent[key]
    return parent, int(keys[-1]) if isinstance(parent, list) else keys[-1]


def model_driver(data, assets, work):
    models = load(assets / "models.json")
    name = data["model"]
    entry = models[name]
    for path, value in data.get("set", {}).items():
        parent, key = _new_model(entry, path)
        parent[key] = value
    for path in data.get("remove", []):
        parent, key = _new_model(entry, path)
        del parent[key]
    if "append" in data:
        entry.append(data["append"])
    checks = domain.CHECKS | reality.CHECKS | advanced.CHECKS
    outcome = checks[name](entry)
    metrics = outcome["metrics"]
    flattened = {}
    for value in metrics.values():
        if isinstance(value, dict):
            for key, candidate in value.items():
                if isinstance(candidate, (int, float)):
                    flattened.setdefault(key, candidate)
    return {"status": outcome["status"], "findings": sorted(e["id"] for e in outcome["findings"]),
            "finding_status": sorted(e["status"] for e in outcome["findings"]),
            "missing": bool(outcome["missing_evidence"]), **flattened,
            **{key: value for key, value in metrics.items() if not isinstance(value, dict)},
            "late_rejected": metrics.get("rejected", {}).get("late", []),
            "unknown": list(metrics.get("unknown", {}))}


def requirements_driver(data, assets, work):
    result = analyze(inside(assets, data["fixture"]))
    rows = result["requirements"]
    first = rows[0] if rows else {}
    return {"status": result["status"], "conflicts": len(result["conflicts"]),
            "lower": first.get("lower"), "upper": first.get("upper"),
            "score": next((r["value"] for r in rows if r["category"] == "scoring"), None),
            "unquantified": any(r["unit"] is None for r in rows),
            "located": bool(rows) and all(r["locator"].startswith("line:") and r["evidence_ids"] for r in rows)}


def impact_driver(data, assets, work):
    return {"status": "calculated", "domains": impact(data["labels"])}


def _meta(**overrides):
    return {"source": "original local evaluation fixture", "revision": "1", "accessed_on": "2026-11-23",
            "rights": "original MIT eval text", "group": "case-counts", "year": 2026, "track": "demo",
            "role": "author_claim", "kind": "work", **overrides}


def claims_driver(data, assets, work):
    db = work / "evidence.sqlite3"
    for name, value, role in (
        ("official-fixture.md", 3.6, "official"),
        ("malicious-fixture.md" if data["tamper"] else "community-fixture.md", 13 if data["tamper"] else 7, "author_claim"),
    ):
        meta = _meta(role=role, kind="datasheet" if role == "official" else "work", group=name, source=name,
                     claims=[{"entity": "EHE-EXAMPLE", "parameter": "recommended input", "value": value,
                              "unit": "V", "conditions": "试验范围", "locator": "line:1"}])
        admit(db, assets / name, meta)
    result = claims(db, "EHE-EXAMPLE", "recommended input")
    hits = search(db, "EHE-EXAMPLE")["hits"]
    return {"status": result["status"],
            "preferred_first": result["claims"][0]["role"] == "official" and result["claims"][0]["value"] == 3.6,
            "locatable": all(row["locator"] == "line:1" for row in result["claims"]),
            "recheck": result["requires_applicable_official_recheck"],
            "inert": any("shell" in e["excerpt"] and e["handling"] == "untrusted_data" for e in hits),
            "claim_count": len(result["claims"])}


def isolation_driver(data, assets, work):
    db = work / "evidence.sqlite3"
    held = work / "private.md"
    shutil.copyfile(assets / "holdout-fixture.md", held)
    private = work / "private.json"
    save(private, [{"partition": "hidden_holdout", "group": "private-design-2026", "path": "private.md",
                    "answer": "answer must stay private"}])
    registry = work / "registry.json"
    fingerprint = holdout_registry(private)
    save(registry, fingerprint)
    same = admit(db, held, _meta(group="another-group"), registry)
    related = admit(db, assets / "problem.md", _meta(group="PRIVATE-design-2026"), registry)
    near = work / "article-copy.md"
    near.write_text(held.read_text(encoding="utf-8").replace("explicit low", "explicit  low") + "\n", encoding="utf-8")
    similar = admit(db, near, _meta(group="other-project"), registry)
    hashes_only = set(fingerprint["fingerprints"][0]) == {"sha256", "group_hash", "simhash", "tokens"}
    return {"status": same["status"], "exact_blocked": same.get("reason", "").endswith(":same_file"),
            "group_blocked": related.get("reason", "").endswith(":same_project_group"),
            "near_blocked": similar.get("reason", "").endswith(":near_duplicate"),
            "register_has_only_hashes": hashes_only and "answer" not in registry.read_text(encoding="utf-8")}


def native_driver(data, assets, work):
    if kicad_probe()["status"] != "available":
        return {"status": "not_run", "reason": "KiCad unavailable", "_skip": True}
    fixture = assets / "continuity"
    project = work / "working"
    shutil.copytree(fixture, project, ignore=shutil.ignore_patterns(".ehe"))
    baseline = source_manifest(project, kicad.native_inputs(project))
    clean = [kicad.run(project, kind) for kind in ("schematic", "pcb")]
    preserved = baseline == source_manifest(project, kicad.native_inputs(project))
    cases = clean
    if data.get("inject"):
        sch, pcb = project / "continuity.kicad_sch", project / "continuity.kicad_pcb"
        sch.write_text("\n".join(row for row in sch.read_text(encoding="utf-8").splitlines()
                                if not row.strip().startswith(("(wire ", "(label "))) + "\n", encoding="utf-8")
        layout = pcb.read_text(encoding="utf-8").splitlines()
        pcb.write_text("\n".join(row for row in layout if not row.strip().startswith("(segment ")) + "\n)\n",
                       encoding="utf-8")
        injected = source_manifest(project, kicad.native_inputs(project))
        bad = [kicad.run(project, kind) for kind in ("schematic", "pcb")]
        preserved = preserved and injected == source_manifest(project, kicad.native_inputs(project))
        cases = clean + bad
    else:
        bad = []
    detected = (bool(bad) and "pin_not_connected" in {x["id"] for x in bad[0]["findings"]}
                and "unconnected_items" in {x["id"] for x in bad[1]["findings"]})
    provenance = all(o.get("level") == "E2" and o.get("tool", {}).get("version") and o.get("execution", {}).get("inputs")
                     and o["raw_sha256"] == sha256(Path(o["execution"]["artifact"])) for o in cases)
    return {"status": "pass" if all(o["status"] == "pass" for o in clean) and provenance else "fail",
            "clean_exit": [o.get("execution", {}).get("exit_code") for o in clean],
            "bad_exit": [o.get("execution", {}).get("exit_code") for o in bad],
            "detected": bool(detected), "provenance": bool(provenance), "original_preserved": preserved}


def unavailable_driver(data, assets, work):
    project = work / "missing"
    shutil.copytree(assets / "continuity", project, ignore=shutil.ignore_patterns(".ehe"))
    with patch.dict(os.environ, {"EHE_KICAD_CLI": ""}):
        result = kicad.run(project, "schematic")
    return {"status": result["status"], "native_level": result["level"], "native_evidence": bool(result["evidence"])}


def image_driver(data, assets, work):
    import base64

    image = work / "board.png"
    sample = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLttAAAAABJRU5ErkJggg=="
    image.write_bytes(base64.b64decode(sample))
    result = generic.ingest(image)
    return {"status": result["status"], "level": result["level"], "geometry_count": len(result["canonical"]["geometry"]),
            "net_count": len(result["canonical"]["nets"])}


def loop_driver(data, assets, work):
    scenario = data["scenario"]
    goal = work / "goal.json"
    design = work / "engineering.json"
    model = load(assets / "models.json")
    if scenario == "stagnant":
        model["rails"][0]["peak_a"] = 2
    save(design, model)
    original = sha256(design)
    plan = {"name": "independent loop case", "tasks": [{"id": "budget", "kind": "budget"}], "physical_required": True}
    if scenario == "denied":
        plan["change_request"] = {"operation": "original_write", "target": design.name, "description": "test protected file"}
    save(goal, plan)
    if scenario == "crash":
        child = ("from pathlib import Path; import os,sys; from ehe.runtime import engine; "
                 "engine.execute=lambda *args:os._exit(77); engine.start(Path(sys.argv[1]),Path(sys.argv[2]))")
        result = subprocess.run([sys.executable, "-c", child, str(work), str(goal)], capture_output=True,
                                timeout=15)
        result_code = result.returncode
        final = resume(work)
    else:
        first = start(work, goal)
        if scenario == "denied":
            decide(work, first["change"]["id"], "deny", "gold reviewer", "test protected input")
        final = resume(work)
        result_code = None
    audit = [json.loads(line) for line in (work / ".ehe/events.jsonl").read_text(encoding="utf-8").splitlines()]
    events = [event["event"] for event in audit]
    physical = next((row["status"] for row in final["gates"] if row["id"] == "physical"), None)
    return {"status": final["state"], "calls": final["calls"], "round": final["round"],
            "interrupted": events.count("interrupted_attempt"), "child_exit": result_code,
            "physical": physical, "claims_measured": any(
                evidence["kind"] == "measured" for record in final["results"].values()
                for evidence in record["result"].get("evidence", [])),
            "first_state": first["state"] if result_code is None else None, "unchanged": sha256(design) == original,
            "no_repeat": result_code is None and final["calls"] == first["calls"],
            "reason": "no_progress" if "no_progress" in events else None}


def release_driver(data, assets, work):
    anchor = {"dataset_sha256": "fixture", "tools": {"kicad": "test input"}}
    baseline = {**anchor, "cases": [{"id": "known_critical_problem", "verdict": "pass", "critical": ["P0"]}]}
    candidate = {**anchor, "cases": [{"id": "known_critical_problem", "verdict": "fail", "critical": ["P0"]}]}
    return {**compare(baseline, candidate), "brand": BRAND}


DRIVERS = {"model": model_driver, "requirements": requirements_driver, "claims": claims_driver,
           "impact": impact_driver, "native": native_driver, "unavailable": unavailable_driver, "image": image_driver,
           "loop": loop_driver, "release": release_driver, "isolation": isolation_driver}
