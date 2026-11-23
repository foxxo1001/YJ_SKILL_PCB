import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ehe.evalops.release import compare
from ehe.evalops.suite import score
from ehe.io import save
from ehe.knowledge import admit, holdout_registry, search
from ehe.requirements import analyze


def plain_pdf(path):
    """Independent minimal valid PDF with deterministic ASCII text."""
    content = b"BT /F1 12 Tf 30 200 Td (Input voltage: 3-5 V) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream",
    ]
    buffer, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(buffer))
        buffer += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(buffer)
    buffer += b"xref\n0 6\n0000000000 65535 f \n"
    for loc in offsets:
        buffer += f"{loc:010d} 00000 n \n".encode()
    buffer += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(buffer)


class KnowledgeTests(unittest.TestCase):
    def test_pdf_extraction_and_lookup(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            path, db = root / "paper.pdf", root / "knowledge.sqlite3"
            plain_pdf(path)
            result = analyze(path)
            self.assertEqual(result["requirements"][0]["locator"], "page:1:line:1")
            meta = {"source": "owned fixture", "revision": "test-1", "accessed_on": "2026-11-23",
                    "rights": "original", "group": "pdf-fixture", "year": 2026, "track": "demo",
                    "role": "official", "kind": "problem"}
            self.assertEqual(admit(db, path, meta)["status"], "imported")
            self.assertEqual(admit(db, path, meta)["status"], "duplicate")
            hit = search(db, "input voltage", year=2026)["hits"][0]
            self.assertEqual((hit["locator"], hit["revision"]), ("page:1:line:1", "test-1"))
            self.assertEqual(search(db, "input voltage", year=2025)["hits"], [])
            private = root / "private.json"
            save(private, [{"partition": "hidden_holdout", "path": "paper.pdf", "group": "different"}])
            with self.assertRaisesRegex(ValueError, "overlaps"):
                holdout_registry(private, db)
            with self.assertRaisesRegex(ValueError, "Only knowledge partition"):
                admit(db, path, {**meta, "partition": "hidden_holdout"})

    def test_score_and_dataset_mismatch(self):
        errors = score({"equals": {"a": 1}, "approx": {"b": 2}}, {"a": 0, "b": 3})
        self.assertEqual(len(errors), 2)
        a = {"dataset_sha256": "a", "tools": {}, "cases": []}
        self.assertEqual(compare(a, {**a, "dataset_sha256": "b"})["status"], "blocked")
