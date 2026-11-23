"""Schema regressions for EasyEDA routing and native evidence admission (no editor mock claim)."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ehe.adapters import easyeda_native, easyeda_review, easyeda_source
from ehe.cli import dispatch, parser_for_cli
from ehe.io import save, sha256
from ehe.review import backend_for, full_review

PCB = "abc12345678900aa"
PCB2 = "abc12345678900bb"
BOARD = "abc12345678900cc"
SCHEMATIC = "abc12345678900dd"
PAGE = "abc12345678900ee"


def record(tag, body, ident=None, ticket=1):
    head = {"type": tag, "ticket": ticket}
    if ident:
        head["id"] = ident
    return json.dumps(head) + "||" + (json.dumps(body) if body is not None else "") + "|"


def fixture(root: Path) -> Path:
    source = root / "pcb"
    source.mkdir()
    index = root / "Test.eprj3"
    save(index, {"format": "folder", "name": "test profile", "profile": {
        "boards": {BOARD: {"uuid": BOARD, "title": "Main"}},
        "pcbs": {PCB: {"uuid": PCB, "title": "Control", "board": BOARD},
                 PCB2: {"uuid": PCB2, "title": "Power"}},
        "schematics": {SCHEMATIC: {"uuid": SCHEMATIC, "name": "SCH", "board": BOARD}},
        "sheets": {PAGE: {"uuid": PAGE, "title": "P1", "schematic_uuid": SCHEMATIC}},
    }})
    lines = (
        record("DOCHEAD", {"docType": "FOOTPRINT", "uuid": "an unrelated library document"}),
        record("COMPONENT", {"x": 1e7}, "ignore"),
        record("DOCHEAD", {"docType": "PCB", "uuid": PCB, "editVersion": "test"}),
        record("COMPONENT", {"x": 100, "y": 120, "layerId": 1}, "e1"),
        record("ATTR", {"key": "Designator", "value": "J1", "parentId": "e1"}, "e1-ref"),
        record("PAD_NET", {"padNet": "GND"}, json.dumps(["PAD_NET", "e1", "1", "pad"])),
        record("LINE", {"layerId": 1, "netName": "GND", "startX": 100, "startY": 120,
                        "endX": 250, "endY": 120, "width": 10}, "stale", ticket=5),
        record("LINE", None, "stale", ticket=6),
        record("POLY", {"polyType": "BOARD_OUTLINE", "layerId": 11,
                        "path": ["R", 0, 0, 800, 600, 0, 0]}, "edge")[:-1],
    )
    (source / "control.epcb2").write_text("\n".join(lines), encoding="utf-8")
    return index


class EasyEDASourceTests(unittest.TestCase):
    def test_multiple_pcbs_require_selection_and_link_schematic_by_board(self):
        with TemporaryDirectory() as folder:
            index = fixture(Path(folder))
            name, docs = easyeda_source.documents(index)
            self.assertEqual(name, "test profile")
            with self.assertRaisesRegex(ValueError, "Control.*Power"):
                easyeda_source.selection(docs, None)
            selected, pages = easyeda_source.selection(docs, "Control")
            self.assertEqual(selected.id, PCB)
            self.assertEqual([item.id for item in pages], [PAGE])
            _, free_pages = easyeda_source.selection(docs, PCB2)
            self.assertEqual(free_pages, [])
            self.assertEqual(backend_for(index.parent, "auto"), "easyeda")
            (index.parent / "other.kicad_pcb").touch()
            with self.assertRaisesRegex(ValueError, "explicitly"):
                backend_for(index.parent, "auto")

    def test_geometry_uses_matching_document_units_and_tombstones(self):
        with TemporaryDirectory() as folder:
            index = fixture(Path(folder))
            actual = easyeda_review.project_geometry(index, "Control")["canonical"]
            self.assertEqual(actual["components"][0]["reference"], "J1")
            self.assertEqual(actual["components"][0]["at_mm"], [2.54, 3.048])
            self.assertEqual(actual["pins"][0]["net"], "GND")
            self.assertEqual(actual["project"]["outline"]["metrics"]["width_mm"], 20.32)
            self.assertFalse(any(row["id"] in {"stale", "ignore"} for row in actual["geometry"]))

    def test_cli_offline_preserves_reporting_scope_and_source_hash(self):
        with TemporaryDirectory() as folder:
            index = fixture(Path(folder))
            before = sha256(index)
            args = parser_for_cli().parse_args(["review", "full", str(index), "--pcb", PCB, "--offline"])
            outcome = dispatch(args)
            self.assertEqual(outcome["backend"], "easyeda")
            self.assertEqual(outcome["target"]["pcb"]["uuid"], PCB)
            self.assertEqual(outcome["coverage"]["drc"]["status"], "not_run")
            self.assertEqual(outcome["coverage"]["return_path"]["status"], "not_run")
            self.assertEqual(outcome["coverage"]["board_outline"]["status"], "pass")
            self.assertEqual(outcome["status"], "inconclusive")
            self.assertTrue(Path(outcome["report_path"]).is_file())
            self.assertEqual(sha256(index), before)


class EasyEDAEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.expected = {"uuid": PCB, "type": 3, "tab": PCB + "@copy", "project": "copy", "name": "Control"}
        self.doc = {"uuid": PCB, "documentType": 3, "tabId": PCB + "@copy", "parentProjectUuid": "copy"}

    def _evaluate(self, value):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "raw.json"
            save(path, {"kind": "drc", "target": self.expected,
                        "execution": {"status": "available", "native": {"ok": True, "value": value}}})
            return easyeda_native._from_raw(path, "fixture")

    def test_groups_count_real_leaves_and_keep_location(self):
        item = {"errorType": "Connection Error", "net": "GND", "objs": ["pad1"],
                "explanation": {"errData": {"errorType": "No Connection", "layerIds": [1]}},
                "pos": {"x": 1.25, "y": 4.5}}
        outcome = self._evaluate({"before": self.doc, "after": self.doc, "checked": True, "passed": False,
                                  "details": [{"name": "Connections", "list": [{"name": "GND", "list": [item, item]}]}]})
        self.assertEqual(outcome["status"], "fail")
        self.assertEqual(outcome["metrics"]["native_violations"], 2)
        self.assertEqual(outcome["findings"][0]["scope"]["object_ids"], ["pad1"])
        self.assertEqual(outcome["findings"][0]["scope"]["net"], "GND")

    def test_wrong_active_document_cannot_pass(self):
        outcome = self._evaluate({"before": self.doc, "after": {**self.doc, "uuid": PCB2},
                                  "checked": True, "passed": True, "details": []})
        self.assertEqual(outcome["status"], "blocked")
        self.assertNotIn("evidence", outcome)

    def test_editor_rewrite_qualifies_native_outcome_without_losing_evidence(self):
        page = self._evaluate({"before": self.doc, "after": self.doc, "checked": True,
                               "passed": True, "details": []})
        check = easyeda_native._schematic_pages([page])
        easyeda_native._qualify_snapshot(check, ["project.eprj3"], Path("session.json"))
        self.assertEqual(check["status"], "inconclusive")
        self.assertEqual(check["checked_snapshot_status"], "pass")
        self.assertEqual(page["status"], "inconclusive")
        self.assertEqual(page["checked_snapshot_status"], "pass")
        self.assertEqual(page["session_receipt"], "session.json")
        self.assertEqual(len(check["evidence"]), 1)

    def test_index_digest_accepts_serialization_only(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "index.eprj3"
            path.write_text('{"board": 1, "profile": {}}', encoding="utf-8")
            first = easyeda_native._index_digest(path)
            path.write_text('{\n "profile": {}, "board":1\n}', encoding="utf-8")
            self.assertEqual(easyeda_native._index_digest(path), first)
            path.write_text('{"board": true, "profile": {}}', encoding="utf-8")
            self.assertNotEqual(easyeda_native._index_digest(path), first)
