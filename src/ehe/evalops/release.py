"""Version comparison and independent release policy."""
BRAND = "ehe-independent-evalops"


def compare(baseline: dict, candidate: dict) -> dict:
    if baseline["dataset_sha256"] != candidate["dataset_sha256"] or baseline["tools"] != candidate["tools"]:
        return {"status": "blocked", "critical_regression": False, "regressions": [],
                "reason": "Baseline and candidate must use the same test inputs and tool versions"}
    previous = {row["id"]: row for row in baseline["cases"]}
    current = {row["id"]: row for row in candidate["cases"]}
    regressed = [name for name, before in previous.items()
                 if before["verdict"] == "pass" and current.get(name, {}).get("verdict") != "pass"]
    critical = any(previous[name].get("critical") for name in regressed)
    return {"status": "blocked" if regressed else "pass", "critical_regression": critical,
            "regressions": sorted(regressed)}


def quality(report: dict, baseline: dict | None = None) -> dict:
    diff = compare(baseline, report) if baseline else None
    counts = report["counts"]
    reasons = []
    if counts["fail"] or counts["not_run"] or not counts["pass"]:
        reasons.append("Gold or evidence check incomplete")
    if report["kpi"]["critical_missed"]:
        reasons.append("Critical detection regression")
    if report.get("variation", {}).get("different_cases"):
        reasons.append("Observed repeat-run semantic drift")
    if diff and diff["status"] != "pass":
        reasons.append("Version comparison did not pass")
    return {"status": "blocked" if reasons else "pass", "verified_by": BRAND,
            "reasons": reasons, "comparison": diff}


def release_gate(report: dict, readiness: dict) -> dict:
    if report.get("quality_gate", {}).get("status") != "pass":
        return {"status": "blocked", "verified_by": BRAND, "reason": "Independent quality evaluation failed"}
    incomplete = sorted(key for key, val in readiness["core_readiness"].items() if val != "implemented")
    if incomplete or report.get("suite") != "core":
        return {"status": "blocked", "verified_by": BRAND, "profile": "read-only-preview",
                "incomplete": incomplete, "reason": "PRD core readiness conditions remain open"}
    return {"status": "inconclusive", "verified_by": BRAND,
            "reason": "Independent human release review and release identity are required"}
