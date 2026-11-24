"""Regressions for flat multi-page exports and source-level four-layer acceptance."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ehe.adapters import easyeda_circuit, easyeda_exports, easyeda_source
from ehe.io import save
from ehe.review import full_review

SPEC = Path(__file__).resolve().parents[1] / "examples" / "motor-sense-easyeda.yaml"


def export_fixture(path, nets):
    components = {ref: {"props": {"Designator": ref, "Footprint": "fixture"},
                        "pinInfoMap": {str(i): {"net": net} for i, net in enumerate(pin_nets, 1)}}
                  for ref, pin_nets in nets.items()}
    text = json.dumps({"version": "2.0.0", "projectId": "owned-copy", "components": components})
    value = {"netlist": {"text": text}}
    save(path, {"target": {"project": "owned-copy"}, "version": "contract", "document": path.stem,
                "identity_verified": True, "execution": {"status": "available", "native": {"value": value}}})
    return {"export_artifact": str(path), "document": path.stem}


class SchematicEvidenceTests(unittest.TestCase):
    def test_complete_exports_find_cross_page_fault_and_localize(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            good = {"U1": ["SHARED", "GND"], "U2": ["SHARED", "GND"]}
            page_a = export_fixture(root / "Power.json", good)
            page_b = export_fixture(root / "Logic.json", good)
            pcb = export_fixture(root / "PCB.json", {"U1": ["SHARED", "GND"], "U2": ["GND", "SHARED"]})
            index = {"locations": {"U1": {"page": "Power"}, "U2": {"page": "Logic"}}, "unsupported": []}
            check = easyeda_exports.parity(pcb, [page_a, page_b], index)
            self.assertEqual(check["status"], "fail")
            self.assertEqual(check["metrics"]["pages_exported"], 2)
            self.assertTrue(any(f["scope"]["schematic_location"]["page"] == "Logic" for f in check["findings"]))
            incomplete = export_fixture(root / "Logic.json", {"U2": good["U2"]})
            self.assertEqual(easyeda_exports.parity(pcb, [page_a, incomplete], index)["status"], "inconclusive")

    def test_software_distinguishes_a_label_change_from_a_short(self):
        graph_a = ({"R1": "footprint", "R2": "footprint"}, {("R1", "1"): "OUT", ("R2", "1"): "OUT"})
        graph_b = (graph_a[0], {("R1", "1"): "NEW", ("R2", "1"): "NEW"})
        renamed = easyeda_exports.compare_graphs(graph_a, graph_b, {}, [])
        self.assertEqual(renamed["metrics"]["connectivity_mismatches"], 0)
        self.assertEqual({f["id"] for f in renamed["findings"]}, {"easyeda_net_name_changed"})


class CircuitArtifactTests(unittest.TestCase):
    def test_four_layer_native_generation_and_controlled_source_faults(self):
        with TemporaryDirectory() as directory:
            target = Path(directory).resolve() / "MotorSense"
            authored = easyeda_circuit.prepare(SPEC, target)
            self.assertEqual(authored["status"], "created")
            project = Path(authored["project"])
            groups = easyeda_source.documents(project)[1]
            selected, pages = easyeda_source.selection(groups, None)
            self.assertEqual([p.name for p in pages], ["Power", "Logic"])
            locations = easyeda_source.sheet_locations(target, pages, easyeda_source.project_files(project))
            self.assertEqual(locations["locations"]["U1"]["page"], "Logic")
            self.assertEqual(locations["locations"]["U2"]["page"], "Power")
            self.assertEqual(locations["unsupported"], [])
            report = full_review(project, pcb=selected.id, offline=True, design_verification=True)
            self.assertEqual(report["coverage"]["multilayer_layout"]["status"], "pass")
            self.assertEqual(report["coverage"]["drc"]["status"], "not_run")
            plan = json.loads((target / "layout-plan.json").read_text(encoding="utf-8"))
            pcb = target / "pcb" / "Board.epcb2"
            original = pcb.read_text(encoding="utf-8")
            return_id = "via" + str(next(i for i, row in enumerate(plan["vias"], 1) if row["role"] == "return"))
            thermal_id = "via" + str(next(i for i, row in enumerate(plan["vias"], 1) if row["role"] == "thermal"))
            output = []
            for raw in original.splitlines():
                head, _, body = raw.partition("||")
                record = json.loads(head)
                if record.get("id") in {return_id, thermal_id, "copper1"}:
                    continue
                if record.get("id") == "trace1":
                    payload = json.loads(body[:-1])
                    payload["width"] /= 2
                    raw = head + "||" + json.dumps(payload) + "|"
                output.append(raw)
            pcb.write_text("\n".join(output) + "\n", encoding="utf-8")
            damaged = full_review(project, offline=True)
            ids = {f["id"] for f in damaged["coverage"]["multilayer_layout"]["findings"]}
            self.assertTrue({"return_via", "thermal_via", "copper_geometry", "route_width"} <= ids)
            pcb.write_text(original, encoding="utf-8")
            self.assertEqual(full_review(project, offline=True)["coverage"]["multilayer_layout"]["status"], "pass")
