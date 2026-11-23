"""Contract tests for native data, auto routing and the approval boundary."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ehe.adapters import easyeda_design as design, easyeda_exports as exports, easyeda_source as source
from ehe.io import atomic_write, save, sha256
from ehe.runtime import easyeda_changes as changes

UUID = "abc12345678900aa"
SPEC = {"width_mm": 30, "height_mm": 20, "gap_mm": 0.3, "edge_mm": 0.8, "trace_mm": 0.3,
        "pad_mm": 1.6, "nets": {"GND": [[4, 15], [26, 15]], "SIGNAL": [[4, 5], [26, 5]]}}


class AuthoringTests(unittest.TestCase):
    def test_clearance_router_and_native_board_records(self):
        pcb = design._pcb(UUID, "5678", 12, design.layout(SPEC))
        with TemporaryDirectory() as folder:
            path = Path(folder) / "board.epcb2"
            atomic_write(path, pcb)
            records = list(source._records(path))
            actual = [body["netName"] for head, body, _ in records if head["type"] == "LINE"]
            self.assertEqual(actual, ["GND", "SIGNAL"])
            self.assertEqual(sum(head["type"] == "PAD" for head, _, _ in records), 4)
        crossing = {**SPEC, "nets": {"A": [[4, 10], [26, 10]], "B": [[16, 3], [16, 17]]}}
        with self.assertRaisesRegex(ValueError, "Cannot route B"):
            design.layout(crossing)

    def test_proposal_persists_candidate_and_blocks_missing_or_stale_approval(self):
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            path = root / "test.eprj3"
            save(path, {"format": "folder", "profile": {"pcbs": {UUID: {"uuid": UUID, "title": "Board"}}}})
            pcb = root / "pcb" / "Board.epcb2"
            atomic_write(pcb, design._pcb(UUID, "5678", 12, design.layout(SPEC)))
            before = sha256(pcb)
            trace = next(head["id"] for head, _, _ in source._records(pcb) if head["type"] == "LINE")
            edits = root / "widths.json"
            save(edits, {"trace_width_mm": {trace: 0.42}})
            proposal = changes.propose_edits(path, UUID, edits)
            ident = proposal["id"]
            self.assertEqual(sha256(pcb), before)
            self.assertEqual(changes.revise(path, ident)["status"], "blocked")
            candidate = Path(proposal["candidate"])
            _, edited = source._matching_records(candidate, source.documents(path)[1]["pcbs"][0])
            self.assertAlmostEqual(edited[("LINE", trace)][0]["width"] * source.MIL_MM, 0.42)
            decision = changes.decide(path, ident, "approve", "test reviewer", "test scope")
            self.assertEqual(decision["candidate_sha256"], sha256(candidate))
            atomic_write(candidate, candidate.read_text(encoding="utf-8") + "\n")
            with self.assertRaisesRegex(ValueError, "stale"):
                changes.revise(path, ident)
            self.assertEqual(sha256(pcb), before)


class NativeExportTests(unittest.TestCase):
    def _artifact(self, path, ref_net, bom=None, admitted=True):
        components = {ref: {"props": {"Designator": ref, "Footprint": "F1"},
                            "pinInfoMap": {"1": {"net": net}}} for ref, net in ref_net.items()}
        output = {"netlist": {"text": json.dumps({"version": "2.0.0", "projectId": "owned-copy", "components": components})},
                  "bom": {"text": bom}, "matched": True}
        save(path, {"document": path.stem, "version": "contract", "identity_verified": admitted,
                    "target": {"project": "owned-copy"},
                    "execution": {"status": "available", "native": {"value": output}}})
        return {"export_artifact": str(path)}

    def test_native_net_identity_pass_and_wrong_pin_fail(self):
        with TemporaryDirectory() as folder:
            out = Path(folder)
            pcb = self._artifact(out / "board.json", {"R1": "$1", "J1": "$1", "U1": "GND"})
            sheet = self._artifact(out / "sheet.json", {"R1": "$2", "J1": "$2", "U1": "GND"})
            self.assertEqual(exports.parity(pcb, [sheet])["status"], "pass")
            sheet = self._artifact(out / "sheet.json", {"R1": "$2", "J1": "$2", "U1": "VDD"})
            mismatch = exports.parity(pcb, [sheet])
            self.assertEqual(mismatch["status"], "fail")
            self.assertEqual(mismatch["findings"][0]["scope"]["pin"], "1")
            self.assertEqual(mismatch["findings"][0]["scope"]["object"], "U1")
            bad = self._artifact(out / "foreign.json", {"R1": "$2"}, admitted=False)
            self.assertEqual(exports.parity(pcb, [bad])["status"], "blocked")

    def test_bom_grouped_native_rows(self):
        with TemporaryDirectory() as folder:
            out = Path(folder)
            text = "No.\tQuantity\tDesignator\tManufacturer Part\tSupplier Part\n1\t2\tR1,R2\t\tC42\n"
            pcb = self._artifact(out / "board.json", {"R1": "", "R2": ""}, bom=text)
            one = exports.bom(pcb, out)
            self.assertEqual(one["status"], "pass")
            self.assertEqual(one["metrics"]["designators"], 2)
            self.assertEqual(Path(one["artifact"]).read_text(encoding="utf-8"), text)
            pcb = self._artifact(out / "board.json", {"R1": "", "R2": "", "J1": ""}, bom=text)
            self.assertEqual(exports.bom(pcb, out)["findings"][0]["scope"]["object"], "J1")
