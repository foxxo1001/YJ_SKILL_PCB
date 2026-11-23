import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jsonschema import Draft202012Validator

from ehe.adapters.kicad import geometry
from ehe.io import load, sha256
from ehe.requirements import analyze
from test_m2 import FIXTURE

ROOT = Path(__file__).resolve().parents[1]


class InterchangeTests(unittest.TestCase):
    def test_actual_outputs_match_normalized_schema(self):
        schema = load(ROOT / "schemas/ehe.schema.json")
        Draft202012Validator.check_schema(schema)

        def validate(kind, data):
            Draft202012Validator({**schema, "$ref": f"#/$defs/{kind}"}).validate(data)

        validate("Goal", load(FIXTURE / "goal.yaml"))
        validate("CanonicalProject", geometry(FIXTURE / "continuity.kicad_pcb"))
        draft = analyze(ROOT / "src/ehe/data/gold/problem.md")
        for row in draft["requirements"]:
            validate("Requirement", row)
        for item in draft["evidence"]:
            validate("Evidence", item)

    def test_wheel_gold_design_matches_public_example(self):
        packaged = ROOT / "src/ehe/data/gold/continuity"
        for name in ("continuity.kicad_pcb", "continuity.kicad_sch", "continuity.kicad_pro", "fp-lib-table", "sym-lib-table"):
            self.assertEqual(sha256(FIXTURE / name), sha256(packaged / name))
