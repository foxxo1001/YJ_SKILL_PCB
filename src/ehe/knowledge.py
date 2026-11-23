"""Local provenance-aware knowledge index. Documents and claims are inert data."""
from collections import Counter
from contextlib import closing, nullcontext
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import unicodedata

from .adapters import generic, kicad
from .adapters.sexpr import all_nodes, read
from .io import digest, format_json, load, now, sha256
from .requirements import read_pages

ROLES = {"official", "author_claim", "independently_reviewed", "unverified"}
KINDS = {"problem", "rules", "work", "datasheet", "errata", "application_note", "schematic",
         "pcb", "bom", "measurement", "source", "mechanical", "retrospective"}
LEXICON = {
    "return_path": ("回流", "参考平面", "地缝", "return path", "reference plane"),
    "power_integrity": ("去耦", "电源完整性", "负载瞬态", "pdn", "decoupling", "power integrity"),
    "energy": ("休眠", "续航", "低功耗", "通电时间", "sleep", "battery life", "low power"),
    "energy_conversion": ("降压", "开关环路", "buck", "switching loop"),
    "analog": ("运放", "共模", "采样", "adc", "op amp", "common mode"),
    "assembly": ("外壳", "插拔", "装配", "enclosure", "clearance", "assembly"),
}
ADMIN_KEYS = {"source", "revision", "accessed_on", "rights", "group", "year", "track", "role", "kind"}


def normalized(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def tokens(text: str) -> list[str]:
    normalized_text = normalized(text)
    latin = re.findall(r"[\w-]+", normalized_text, re.ASCII)
    cjk = re.findall(r"[\u3400-\u9fff]+", normalized_text)
    return latin + [s[i:i + 2] for s in cjk for i in range(max(1, len(s) - 1))]


def concepts(text: str) -> set:
    content = normalized(text)
    return {label for label, aliases in LEXICON.items()
            if any(alias in content if not alias.isascii()
                   else re.search(rf"\b{re.escape(alias)}\b", content) for alias in aliases)}


def fingerprint(content: str, raw_hash: str, group: str) -> dict:
    words = tokens(content)[:100_000]
    shingles = Counter(zip(words, words[1:], words[2:]))
    weights = [0] * 64
    for term, count in shingles.items():
        bits = int.from_bytes(hashlib.blake2b(" ".join(term).encode("utf-8"), digest_size=8).digest())
        for bit in range(64):
            weights[bit] += (1 if bits >> bit & 1 else -1) * min(count, 3)
    signature = sum(1 << i for i, votes in enumerate(weights) if votes > 0)
    return {"sha256": raw_hash, "group_hash": digest(normalized(group).strip()),
            "simhash": f"{signature:016x}", "tokens": len(words)}


def overlap(a: dict, b: dict) -> str | None:
    if a["sha256"] == b["sha256"]:
        return "same_file"
    if a["group_hash"] == b["group_hash"]:
        return "same_project_group"
    if min(a["tokens"], b["tokens"]) >= 20 and 0.75 <= a["tokens"] / b["tokens"] <= 1.34:
        if (int(a["simhash"], 16) ^ int(b["simhash"], 16)).bit_count() <= 5:
            return "near_duplicate"
    return None


def _connect(path: Path, read_only=False):
    if read_only:
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        db.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS docs (
                id TEXT PRIMARY KEY, source_path TEXT NOT NULL, metadata TEXT NOT NULL,
                fingerprint TEXT NOT NULL, added TEXT NOT NULL, year INTEGER NOT NULL,
                track TEXT NOT NULL, kind TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS passage (
                doc TEXT NOT NULL REFERENCES docs(id), locator TEXT NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY(doc, locator)
            );
            CREATE INDEX IF NOT EXISTS doc_filters ON docs(year, track, kind);
        """)
    db.row_factory = sqlite3.Row
    return db


def _segments(path, kind, ocr_lang):
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv"} and kind == "bom":
        rows, _, outcome = generic.import_bom(path)
        return [(row["locator"], f'{", ".join(row["references"])} {row["mpn"]} {row["value"]} {row["footprint"]}')
                for row in rows], outcome["missing_evidence"]
    if suffix == ".kicad_pcb":
        model = kicad.geometry(path)
        return [(f'footprint:{row["id"]}', f'{row["reference"]} {row["footprint"]} {row["layer"]}')
                for row in model["components"]], model["limitations"]
    if suffix == ".kicad_sch":
        symbols = []
        for row in all_nodes(read(path), "symbol"):
            fields = {p[1]: p[2] for p in all_nodes(row, "property") if len(p) >= 3}
            if ref := fields.get("Reference"):
                symbols.append((f"symbol:{len(symbols)+1}/{ref}", " ".join(fields.values())))
        return symbols, ["root symbols only; no connectivity inference"]
    pages, missing = read_pages(path, ocr_lang)
    segments = [(f"{page}:line:{index}" if page else f"line:{index}", line.strip())
                for page, body in pages for index, line in enumerate(body.splitlines(), 1) if line.strip()]
    if len(segments) > 10_000:
        missing.append("only first 10000 text passages indexed")
    return segments[:10_000], missing


def _validate(meta):
    if not isinstance(meta, dict) or not ADMIN_KEYS <= meta.keys():
        raise ValueError("Missing source, rights, revision, access date, year, track, role, kind or group")
    for key in ADMIN_KEYS - {"year"}:
        if not isinstance(meta[key], str) or not meta[key].strip():
            raise ValueError(f"{key} requires nonempty provenance")
    if type(meta["year"]) is not int or not 1900 <= meta["year"] <= 2200:
        raise ValueError("Invalid source year")
    date.fromisoformat(meta["accessed_on"])
    if meta["kind"] not in KINDS or meta["role"] not in ROLES:
        raise ValueError("Unknown source kind or role")
    if meta.get("partition", "knowledge") != "knowledge":
        raise ValueError("Only knowledge partition may be ingested")
    claims = meta.get("claims", [])
    if not isinstance(claims, list) or len(claims) > 1000:
        raise ValueError("Expected up to 1000 structured claims")
    for claim in claims:
        if not {"entity", "parameter", "value", "unit", "conditions", "locator"} <= claim.keys():
            raise ValueError("A structured claim requires entity, parameter, value, unit, conditions and locator")
        if not all(isinstance(claim[key], str) and claim[key].strip()
                   for key in ("entity", "parameter", "unit", "conditions", "locator")):
            raise ValueError("Claim provenance and conditions must be nonempty")
    return {"partition": "knowledge", **meta}


def _read_registry(registry: Path | None):
    if registry is None:
        return []
    data = load(registry)
    if data.get("format") != "ehe-holdout-fingerprints-v1" or not isinstance(data.get("fingerprints"), list):
        raise ValueError("Invalid isolated holdout registry")
    for item in data["fingerprints"]:
        if set(item) != {"sha256", "group_hash", "simhash", "tokens"}:
            raise ValueError("Registry contains fields beyond the permitted fingerprints")
        for key, length in (("sha256", 64), ("group_hash", 64), ("simhash", 16)):
            if not isinstance(item[key], str) or re.fullmatch(rf"[0-9a-f]{{{length}}}", item[key]) is None:
                raise ValueError("Malformed holdout fingerprint")
        if type(item["tokens"]) is not int or not 0 <= item["tokens"] <= 100_000:
            raise ValueError("Malformed holdout token count")
    return data["fingerprints"]


def admit(db_path: Path, path: Path, meta: dict, registry: Path | None = None, ocr_lang: str | None = None):
    meta = _validate(meta)
    if path.resolve() == db_path.resolve():
        raise ValueError("Database cannot itself be imported")
    raw_hash = sha256(path)
    passages, limitations = _segments(path, meta["kind"], ocr_lang)
    # Import the original immutable content identity, even when OCR/text is only partial.
    if raw_hash != sha256(path):
        raise ValueError("Input changed during parsing")
    content = "\n".join(text for _, text in passages)
    sign = fingerprint(content, raw_hash, meta["group"])
    for held in _read_registry(registry):
        if reason := overlap(sign, held):
            return {"status": "blocked", "reason": f"holdout_isolation:{reason}"}
    locators = {locator for locator, _ in passages}
    if any(claim["locator"] not in locators for claim in meta.get("claims", [])):
        raise ValueError("Structured claim locator must exist in parsed passages")
    metadata = {**meta, "parse_limitations": limitations, "indexed_passages": len(passages)}
    doc_id = "kb-" + raw_hash
    with closing(_connect(db_path)) as db, db:
        inserted = db.execute("INSERT OR IGNORE INTO docs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                              (doc_id, str(path.resolve()), format_json(metadata), format_json(sign), now(),
                               meta["year"], meta["track"], meta["kind"]))
        if not inserted.rowcount:
            return {"status": "duplicate", "id": doc_id, "sha256": raw_hash}
        db.executemany("INSERT INTO passage VALUES (?, ?, ?)",
                       ((doc_id, locator, body[:16_000]) for locator, body in passages))
    return {"status": "imported" if passages and not limitations else "partial",
            "id": doc_id, "sha256": raw_hash, "passages": len(passages), "limitations": limitations}


def holdout_registry(manifest_path: Path, db_path: Path | None = None) -> dict:
    from .io import inside

    manifest = load(manifest_path)
    if not isinstance(manifest, list) or not manifest:
        raise ValueError("Private manifest requires a list of hidden_holdout files")
    entries = []
    with closing(_connect(db_path, read_only=True)) if db_path else nullcontext() as db:
        existing = [json.loads(row["fingerprint"]) for row in db.execute("SELECT fingerprint FROM docs")] if db else []
    for item in manifest:
        if item.get("partition") != "hidden_holdout" or not item["group"].strip():
            raise ValueError("Private manifest requires explicit hidden_holdout partition and group")
        path = inside(manifest_path.parent.resolve(), item["path"])
        passages, _ = _segments(path, item.get("kind", "work"), None)
        sign = fingerprint("\n".join(text for _, text in passages), sha256(path), item["group"])
        if any(overlap(sign, found) for found in existing):
            raise ValueError("Holdout overlaps admitted knowledge; isolate by project and verify near duplicates")
        entries.append(sign)
    return {"format": "ehe-holdout-fingerprints-v1", "fingerprints": entries}


def search(db_path: Path, query: str, *, year=None, track=None, kind=None, limit=12) -> dict:

    keywords, topics = set(tokens(query)), concepts(query)
    if not keywords or not 1 <= limit <= 200:
        raise ValueError("Search requires terms and a limit from 1 to 200")
    clauses, params = [], []
    for name, expected in (("year", year), ("track", track), ("kind", kind)):
        if expected is not None:
            clauses.append(f"d.{name}=?")
            params.append(expected)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with closing(_connect(db_path, read_only=True)) as db:
        rows = db.execute("SELECT d.id, d.source_path, d.metadata, d.fingerprint, p.locator, p.body "
                          "FROM docs d JOIN passage p ON d.id = p.doc" + where, params)
        matches = []
        for row in rows:
            lexical = len(keywords.intersection(tokens(row["body"]))) / len(keywords)
            semantic = len(topics.intersection(concepts(row["body"])))
            score = lexical + semantic * .6 + (.5 if normalized(query) in normalized(row["body"]) else 0)
            if score:
                matches.append((score, row))
    matches.sort(key=lambda x: (-x[0], x[1]["id"], x[1]["locator"]))
    output = []
    for score, row in matches[:limit]:
        metadata = json.loads(row["metadata"])
        output.append({"id": row["id"], "sha256": json.loads(row["fingerprint"])["sha256"],
                       "source": metadata["source"], "revision": metadata["revision"], "accessed_on": metadata["accessed_on"],
                       "local_path": row["source_path"], "locator": row["locator"], "excerpt": row["body"],
                       "role": metadata["role"], "kind": metadata["kind"], "year": metadata["year"], "track": metadata["track"],
                       "score": score, "handling": "untrusted_data", "requires_applicable_official_recheck": True})
    return {"status": "retrieved", "hits": output}


def claims(db_path: Path, entity: str, parameter: str) -> dict:

    records = []
    with closing(_connect(db_path, read_only=True)) as db:
        for row in db.execute("SELECT id, metadata FROM docs"):
            meta = json.loads(row["metadata"])
            for claim in meta.get("claims", []):
                if normalized(claim["entity"]) == normalized(entity) and normalized(claim["parameter"]) == normalized(parameter):
                    records.append({**claim, "document": row["id"], "role": meta["role"], "source": meta["source"],
                                    "revision": meta["revision"], "accessed_on": meta["accessed_on"]})
    records.sort(key=lambda row: (row["role"] != "official", row["document"], row["locator"]))
    by_conditions = {}
    for row in records:
        by_conditions.setdefault((normalized(row["unit"]), normalized(row["conditions"])), set()).add(digest(row["value"]))
    conflict = any(len(values) > 1 for values in by_conditions.values())
    return {"status": "conflicting" if conflict else "retrieved" if records else "inconclusive",
            "claims": records, "preferred": [row["document"] for row in records if row["role"] == "official"],
            "requires_applicable_official_recheck": True}
