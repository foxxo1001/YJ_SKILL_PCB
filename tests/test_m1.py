import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ehe.domain import bom, energy, pins, rails
from ehe.project import init_project
from ehe.requirements import analyze, trace


class CoreTests(unittest.TestCase):
    def test_requirements_and_conflict(self):
        with TemporaryDirectory() as folder:
            path = Path(folder, "题目.md")
            path.write_text("# 基本要求\n输入电压：3–5 V；必须保留复位键\n"
                            "输入电压：不低于 6 V\n# 评分细则\n基础功能 20 分\n", encoding="utf-8")
            observed = analyze(path)
        self.assertEqual(observed["status"], "fail")
        self.assertEqual(observed["requirements"][0]["lower"], 3)
        self.assertIsNone(observed["requirements"][1]["unit"])
        self.assertEqual(observed["requirements"][3]["value"], 20)
        self.assertEqual(observed["conflicts"][0]["requirements"], ["RQ-001", "RQ-003"])
        self.assertEqual(trace(observed["requirements"], {})[0]["status"], "uncovered")

    def test_template_is_non_destructive(self):
        with TemporaryDirectory() as folder:
            root = Path(folder, "中文 竞赛")
            init_project(root)
            file = root / "project.json"
            before = file.read_bytes()
            with self.assertRaises(FileExistsError):
                init_project(root)
            self.assertEqual(before, file.read_bytes())

    def test_rail_peak_limit_boundary(self):
        row = {"id": "3V3", "voltage_v": 3.3, "peak_a": 1, "limit_a": 1}
        self.assertEqual(rails([row])["status"], "pass")
        self.assertEqual(rails([{**row, "peak_a": 1.01}])["findings"][0]["id"], "RAIL-PEAK")
        for value in (False, float("nan"), -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rails([{**row, "peak_a": value}])

    def test_pin_and_bom_faults(self):
        row = {"device": "U1", "pin": "PA0", "signal_v": 5, "allowed_v": 3.6, "source": "declared fixture"}
        self.assertEqual({f["id"] for f in pins([row, row])["findings"]}, {"PIN-DUP", "PIN-VOLTAGE"})
        self.assertEqual({f["id"] for f in bom([{"references": ["R1", "r1"], "quantity": 1}])["findings"]},
                         {"BOM-DUP", "BOM-QTY"})
        self.assertEqual(pins([{**row, "source": ""}])["status"], "inconclusive")
        self.assertEqual(energy({})["status"], "inconclusive")
