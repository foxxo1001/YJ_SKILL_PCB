"""Source-indexed PDF, text and explicitly configured OCR extraction."""
import re
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

from .io import sha256
from .models import Evidence

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}
TEXT_EXTENSIONS = {".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".log", ".c", ".h", ".py"}
UNIT = r"(?:mV|kV|V|mA|µA|uA|A|mW|W|Wh|mAh|Ah|MHz|kHz|Hz|ms|µs|us|ns|s|mm|cm|kg|g|℃|°C|%|元|分)"
NUM = r"-?\d+(?:\.\d+)?"
RANGE = re.compile(rf"({NUM})\s*(?:~|～|–|—|至|到|(?<=\d)-)\s*({NUM})\s*({UNIT})(?![A-Za-z])")
BOUND = re.compile(rf"(不低于|不少于|至少|大于等于|不得低于|不高于|不超过|不得超过|至多|小于等于|[<>]=|[≤≥<>])\s*({NUM})\s*({UNIT})(?![A-Za-z])")
SCORE = re.compile(rf"({NUM})\s*(分)(?!钟)")
LOWER = {"不低于", "不少于", "至少", "大于等于", "不得低于", ">", ">=", "≥"}
SECTIONS = {
    "基本要求": "basic", "发挥": "extension", "评分": "scoring", "测试条件": "conditions",
    "测量条件": "conditions", "硬性限制": "hard_limit", "器件": "parts",
    "交付": "deliverables", "时间": "time",
}


def read_pages(path: Path, ocr_lang: str | None = None) -> tuple[list[tuple[str, str]], list[str]]:
    if path.stat().st_size > 64 * 1024 * 1024:
        return [], ["input exceeds 64 MiB"]
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError

        try:
            reader = PdfReader(path)
            if reader.is_encrypted:
                return [], ["encrypted PDF requires an unencrypted local copy"]
            total_pages = len(reader.pages)
            pages = [(f"page:{i}", page.extract_text() or "")
                     for i, page in enumerate(reader.pages[:500], 1)]
        except PyPdfError as exc:
            return [], [f"PDF parser could not extract text: {type(exc).__name__}"]
        missing = [f"{loc}: scanned or no extractable text" for loc, body in pages if not body.strip()]
        if total_pages > 500:
            missing.append("extraction limited to the first 500 pages")
        return pages, missing
    if suffix in TEXT_EXTENSIONS:
        return [("", path.read_text(encoding="utf-8-sig"))], []
    if suffix in IMAGE_EXTENSIONS:
        binary = shutil.which("tesseract")
        if not binary or not ocr_lang:
            return [], ["OCR requires Tesseract and an explicit --ocr-lang"]
        completed = subprocess.run([binary, str(path.resolve()), "stdout", "-l", ocr_lang],
                                   capture_output=True, timeout=45, encoding="utf-8", errors="replace")
        if completed.returncode:
            return [], [f"OCR exited {completed.returncode}: {completed.stderr.strip()}"]
        return [("image:1", completed.stdout)], ["OCR transcription requires human confirmation"]
    return [], [f"no text parser for {suffix}"]


def analyze(path: Path, ocr_lang: str | None = None) -> dict:
    content = sha256(path)
    pages, missing = read_pages(path, ocr_lang)
    if content != sha256(path):
        raise ValueError("Requirement input changed during extraction")
    rows, evidence = [], {}
    section = "unclassified"
    for page, body in pages:
        for line_no, raw in enumerate(body.splitlines(), 1):
            clean = re.sub(r"^\s*(?:#+\s*|[-*]\s+|\d+[.、)]\s*)", "", raw).strip()
            if not clean:
                continue
            heading = clean.rstrip("：:")
            if len(heading) <= 22 and not re.search(r"\d|[。；]", heading):
                found = next((kind for key, kind in SECTIONS.items() if key in heading), None)
                if found and (raw.lstrip().startswith("#") or heading.endswith(("要求", "细则", "条件", "限制", "物"))):
                    section = found
                    continue
            clauses = [part.strip() for part in re.split(r"[；;。]", clean) if part.strip()]
            for clause in clauses:
                matches = sorted(
                    [(match, "range") for match in RANGE.finditer(clause)]
                    + [(match, "bound") for match in BOUND.finditer(clause)]
                    + [(match, "score") for match in SCORE.finditer(clause)],
                    key=lambda item: item[0].start(),
                )
                if not matches and section == "unclassified" and not re.search(r"必须|不得|应|禁止|限", clause):
                    continue
                locator = f"{page}:line:{line_no}" if page else f"line:{line_no}"
                if locator not in evidence:
                    evidence[locator] = asdict(Evidence.file(path, locator, "documented", "text_extraction",
                                                             content_sha256=content))
                for match, kind in matches or [(None, "unquantified")]:
                    lower = upper = unit = None
                    lower_inclusive = upper_inclusive = True
                    if kind == "range":
                        lower, upper, unit = float(match[1]), float(match[2]), match[3]
                    if kind == "bound":
                        op, value, unit = match.groups()
                        if op in LOWER:
                            lower, lower_inclusive = float(value), op != ">"
                        else:
                            upper, upper_inclusive = float(value), op != "<"
                    if kind == "score":
                        unit = "分"
                    metric = clause[:match.start()].strip(" ：:,，") if match else clause
                    metric = re.split(r"[，,]", metric)[-1].strip() or clause
                    rows.append({
                        "id": f"RQ-{len(rows)+1:03d}", "metric": metric, "source_text": raw,
                        "clause": clause, "locator": locator, "category": "scoring" if kind == "score" else section,
                        "value": float(match[1]) if kind == "score" else None, "unit": unit,
                        "lower": lower, "upper": upper, "lower_inclusive": lower_inclusive,
                        "upper_inclusive": upper_inclusive,
                        "constraint": "soft" if section in {"extension", "scoring"} or kind == "score" else "hard"
                        if section in {"basic", "hard_limit"} or re.search(r"必须|不得|禁止", clause)
                        else "unknown",
                        "verification_method": "measurement" if kind in {"range", "bound"} else "manual_review",
                        "risk": "需要复核原文、适用工况与规则版本",
                        "unknowns": ["规则版本", "确认条款作用域", *([] if unit else ["量值与单位"])],
                        "evidence_ids": [evidence[locator]["id"]], "evidence_status": "documented",
                    })
    conflicts = find_conflicts(rows)
    return {"status": "fail" if conflicts else "inconclusive" if not rows or missing else "parsed",
            "requirements": rows, "conflicts": conflicts, "evidence": list(evidence.values()),
            "missing_evidence": missing or ([] if rows else ["no recognizable requirement clauses"])}


def find_conflicts(rows: list[dict]) -> list[dict]:
    groups = {}
    for row in rows:
        if row["unit"] and row["constraint"] == "hard":
            groups.setdefault((row["category"], row["metric"], row["unit"]), []).append(row)
    conflicts = []
    for (_, metric, unit), terms in groups.items():
        lowers = [(row["lower"], row["lower_inclusive"]) for row in terms if row["lower"] is not None]
        uppers = [(row["upper"], row["upper_inclusive"]) for row in terms if row["upper"] is not None]
        if lowers and uppers:
            low, upper = max(lowers, key=lambda x: (x[0], not x[1])), min(uppers)
            if low[0] > upper[0] or low[0] == upper[0] and not (low[1] and upper[1]):
                conflicts.append({"metric": metric, "unit": unit, "requirements": [t["id"] for t in terms],
                                  "reason": "empty_interval; verify original applicability"})
    return conflicts


def trace(rows: list[dict], mappings: dict) -> list[dict]:
    return [
        {"requirement": row["id"], "design_elements": record.get("design_elements", []),
         "verification_method": record.get("verification_method", row["verification_method"]),
         "evidence_ids": record.get("evidence_ids", []),
         "status": "unverified" if record.get("design_elements") else "uncovered"}
        for row in rows for record in [mappings.get(row["id"], {})]
    ]
