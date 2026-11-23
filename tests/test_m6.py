import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ehe.adapters import altium, easyeda
from ehe.doctor import kicad_probe
from ehe.io import load, save, sha256
from ehe.runtime.changes import apply_approved
from ehe.runtime.engine import decide, resume, start
from inject import disconnect
from test_m2 import FIXTURE


class OptionalAdaptersAndChanges(unittest.TestCase):
    def test_altium_project_metadata_and_export(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "Prototype.PrjPcb"
            path.write_text("[Document1]\nDocumentPath=Layout.PcbDoc\n"
                            "[Document2]\nDocumentPath=..\\Shared.SchDoc\n", encoding="utf-16")
            evidence = altium.project_inventory(path)
            self.assertEqual([item["in_project"] for item in evidence["documents"]], [True, False])
            self.assertEqual((evidence["level"], evidence["native_rule_checks"]), ("E0", "not_run"))
            source = root / "BOM.csv"
            source.write_text('Designator,Quantity,Footprint\n"J1; J2",2,TestPad\n', encoding="utf-8")
            bill = altium.exported(source)
            self.assertEqual((bill["status"], bill["level"]), ("pass", "E1"))

    def test_easyeda_unavailable_does_not_claim_execution(self):
        with patch.dict(os.environ, {"EHE_EASYEDA_CLI": ""}):
            self.assertEqual(easyeda.detect()["status"], "not_run")

    def _proposal(self, root):
        shutil.copytree(FIXTURE, root, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".ehe"))
        disconnect(root)
        replacement = root / "proposals" / "fixed.kicad_pcb"
        replacement.parent.mkdir()
        shutil.copyfile(FIXTURE / "continuity.kicad_pcb", replacement)
        goal = {"name": "approved fixture correction", "physical_required": False, "required": ["drc"],
                "tasks": [{"id": "drc", "kind": "pcb"}], "change_request": {
                    "operation": "workcopy_replace", "target": "continuity.kicad_pcb",
                    "replacement": "proposals/fixed.kicad_pcb", "description": "restore one track"}}
        save(root / "patch-goal.json", goal)
        return start(root, root / "patch-goal.json"), replacement

    def test_stale_approval_rejected(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            state, replacement = self._proposal(root)
            old_id = state["change"]["id"]
            with replacement.open("a", encoding="utf-8") as stream:
                stream.write("\n")
            with self.assertRaisesRegex(ValueError, "inputs changed"):
                decide(root, old_id, "approve", "test reviewer", "change revision")
            self.assertNotEqual(resume(root)["change"]["id"], old_id)
            self.assertFalse((root / ".ehe/work").exists())

    def test_approved_board_replacement_is_idempotent_and_rechecked(self):
        if kicad_probe()["status"] != "available":
            if os.environ.get("EHE_REQUIRE_KICAD"):
                self.fail("KiCad unavailable")
            self.skipTest("KiCad unavailable")
        with TemporaryDirectory() as folder:
            root = Path(folder)
            state, replacement = self._proposal(root)
            original = root / "continuity.kicad_pcb"
            original_hash = sha256(original)
            proposal = state["change"]
            self.assertEqual(state["state"], "WAITING_APPROVAL")
            self.assertEqual(apply_approved(root, proposal)["status"], "blocked")
            self.assertIn("(segment", proposal["preview"])
            approved = decide(root, proposal["id"], "approve", "fixture reviewer", "reviewed sole copper restoration")
            effect = apply_approved(root, approved["change"])
            self.assertEqual(effect["status"], "applied", effect)
            # Runtime has not recorded the side effect, simulating a crash after atomic publication.
            finished = resume(root)
            self.assertEqual(finished["state"], "PASSED", finished["gates"])
            self.assertEqual(finished["change_effect"], effect)
            work = root / ".ehe" / effect["working_copy"]
            self.assertEqual(sha256(work / original.name), sha256(replacement))
            self.assertEqual(load(work / "change-receipt.json"), effect)
            self.assertEqual(sha256(original), original_hash)
            self.assertEqual(resume(root)["calls"], 1)
            self.assertEqual(len(list((root / ".ehe/work").glob("chg-*"))), 1)
