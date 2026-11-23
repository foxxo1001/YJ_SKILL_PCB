import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from ehe.doctor import kicad_probe
from ehe.evalops.gate import acceptance
from ehe.io import load, save, sha256
from ehe.runtime.engine import decide, resume, start, status
from ehe.runtime.plan import goal_plan
from ehe.runtime.store import Store, exclusive
from inject import disconnect
from test_m2 import FIXTURE


class LoopTests(unittest.TestCase):
    def setup_budget(self, root, **options):
        goal = {"tasks": [{"id": "power", "kind": "budget"}], **options}
        save(root / "goal.json", goal)
        return root / "goal.json"

    def test_cycle_rejected(self):
        goal = {"tasks": [{"id": "a", "kind": "budget", "depends_on": ["b"]},
                          {"id": "b", "kind": "budget", "depends_on": ["a"]}]}
        with self.assertRaisesRegex(ValueError, "cycle"):
            goal_plan(goal, Path.cwd())

    def test_budget_exhaustion_before_effect(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            state = start(root, self.setup_budget(root, max_calls=0))
            self.assertEqual((state["state"], state["calls"]), ("BUDGET_EXHAUSTED", 0))
            self.assertEqual(state, resume(root))

    def test_unauthorized_original_and_rejection(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            original = root / "engineering.json"
            save(original, {"rails": []})
            before = sha256(original)
            state = start(root, self.setup_budget(root, change_request={
                "operation": "original_write", "target": original.name, "description": "request original edit"}))
            self.assertEqual(state["state"], "WAITING_APPROVAL")
            self.assertEqual(resume(root)["calls"], 0)
            decide(root, state["change"]["id"], "deny", "fixture reviewer", "protected source")
            paused = resume(root)
            self.assertEqual((paused["state"], paused["calls"], sha256(original)), ("PAUSED", 0, before))
            self.assertEqual(paused, resume(root))

    def test_lock_and_after_process_crash(self):
        program = (
            "import os,sys; from pathlib import Path; from ehe.runtime import engine; "
            "engine.execute = lambda *a: os._exit(77); engine.start(Path(sys.argv[1]),Path(sys.argv[2]))"
        )
        with TemporaryDirectory() as folder:
            root = Path(folder)
            broken = subprocess.run([sys.executable, "-c", program, folder, str(self.setup_budget(root))],
                                    timeout=10, capture_output=True)
            self.assertEqual(broken.returncode, 77, broken.stderr)
            self.assertEqual(status(root)["active"]["attempt"], 1)
            with exclusive(root), self.assertRaisesRegex(ValueError, "owns"):
                resume(root)
            saved = resume(root)
            self.assertEqual((saved["round"], saved["calls"], saved["active"]), (1, 2, None))
            self.assertEqual(saved["gates"][-1]["status"], "not_run")
            events = [json.loads(line) for line in (root / ".ehe/events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([e["event"] for e in events].count("interrupted_attempt"), 1)
            self.assertEqual(saved, load(root / ".ehe/run.json"))
            with Store(root) as store, self.assertRaisesRegex(ValueError, "Illegal"):
                store.commit(saved, "forged", transition="CREATED")

    def test_actual_readonly_repair_and_gate_tamper(self):
        if kicad_probe()["status"] != "available":
            if os.environ.get("EHE_REQUIRE_KICAD"):
                self.fail("KiCad unavailable")
            self.skipTest("KiCad unavailable")
        with TemporaryDirectory() as folder:
            root = Path(folder, "run")
            shutil.copytree(FIXTURE, root, ignore=shutil.ignore_patterns(".ehe"))
            disconnect(root)
            start_bad = start(root, root / "goal.yaml")
            self.assertEqual(start_bad["state"], "REVISE")
            self.assertEqual({item["status"] for item in start_bad["gates"]}, {"fail"})
            self.assertEqual(resume(root)["state"], "PAUSED")
            self.assertEqual(resume(root)["calls"], 2)
            for suffix in ("sch", "pcb"):
                name = f"continuity.kicad_{suffix}"
                shutil.copyfile(FIXTURE / name, root / name)
            clean = resume(root)
            self.assertEqual((clean["state"], clean["round"], clean["calls"]), ("PASSED", 2, 4))
            raw = Path(clean["results"]["erc"]["result"]["execution"]["artifact"])
            raw.write_text("{}", encoding="utf-8")
            self.assertEqual(acceptance(root, clean["plan"], clean["results"])[0]["status"], "inconclusive")
            self.assertEqual(resume(root)["state"], "PAUSED")
