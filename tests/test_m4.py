import copy
from pathlib import Path
import unittest

from ehe.advanced import buck, emi_loop, fault, motor, pdn, precision_analog, realtime, return_path, signal
from ehe.domain import cost, energy
from ehe.io import load
from ehe.reality import enclosure, junction, pareto, tolerance, transient

FIXTURE = Path(__file__).resolve().parents[1] / "examples/engineering-model/engineering.json"


class EngineeringModels(unittest.TestCase):
    def setUp(self):
        self.models = load(FIXTURE)

    def test_enclosure_height_and_connector_boundaries(self):
        data = self.models["mechanical"]
        self.assertEqual(enclosure(data)["status"], "pass")
        data["parts"][1]["max_mm"][2] = 12
        data["connector_sweep"][0]["max_mm"][0] = 51
        self.assertEqual({f["id"] for f in enclosure(data)["findings"]}, {"MECH-HEIGHT", "MECH-CONNECTOR"})
        del data["enclosure"]
        self.assertEqual(enclosure(data)["status"], "inconclusive")

    def test_heat_sag_and_tolerance(self):
        thermal = self.models["thermal"]
        self.assertAlmostEqual(junction(thermal)["metrics"]["Q1"]["junction_estimate_c"], 77)
        thermal["devices"][0]["ambient_max_c"] = 105
        self.assertEqual(junction(thermal)["findings"][0]["id"], "THERMAL-TJ")
        data = self.models["transient"]
        self.assertAlmostEqual(transient(data)["metrics"]["estimated_min_v"], 3.348)
        data["battery_max_ohm"] = 0.5
        self.assertEqual(transient(data)["findings"][0]["id"], "BATTERY-SAG")
        data = self.models["tolerance"]
        self.assertEqual(tolerance(data)["status"], "pass")
        data["delta_temp_c"] = 1000
        self.assertEqual(tolerance(data)["status"], "fail")

    def test_energy_and_quote_cases(self):
        data = self.models["energy"]
        good = energy(data)
        self.assertEqual(good["status"], "pass")
        data["states"][4]["peripheral_a"] += 0.3
        self.assertLess(energy(data)["metrics"]["runtime_estimate_h"], good["metrics"]["runtime_estimate_h"])
        data["states"][4]["duty"] += 0.01
        with self.assertRaisesRegex(ValueError, "sum"):
            energy(data)
        data = self.models["cost"]
        self.assertEqual(cost(data)["metrics"]["total"], 21)
        data["items"][0]["lead_days"] = 8
        self.assertEqual(cost(data)["status"], "fail")

    def test_pareto_filters_and_excludes_dominated(self):
        data = self.models["optimization"]
        baseline = pareto(data)
        self.assertEqual(baseline["metrics"]["pareto"], ["A", "B"])
        self.assertEqual(baseline["metrics"]["rejected"], {"late": ["lead_days"]})
        del data["candidates"][0]["sources"]["power_w"]
        self.assertEqual(pareto(data)["status"], "inconclusive")

    def test_analog_motor_buck_and_timing_normal_and_fault(self):
        for name, func, key, bad, bug in (
            ("analog", precision_analog, "acquisition_s", 1e-8, "ANALOG-SETTLING"),
            ("motor", motor, "min_winding_ohm", 0.5, "MOTOR-STALL"),
            ("buck", buck, "inductor_sat_a", 0.8, "BUCK-PEAK"),
            ("realtime", realtime, "deadline_us", 20, "RT-DEADLINE"),
        ):
            with self.subTest(name=name):
                data = self.models[name]
                self.assertEqual(func(data)["status"], "pass")
                self.assertIn(bug, {issue["id"] for issue in func({**data, key: bad})["findings"]})

    def test_return_path_and_emi_geometry(self):
        data = self.models["return_path"]
        self.assertEqual(return_path(data)["status"], "pass")
        data["gaps_by_reference"]["GND1"] = [[3, -1, 4, 1]]
        self.assertEqual(return_path(data)["findings"][0]["id"], "RETURN-GAP")
        data["plane_model_complete"] = False
        self.assertEqual(return_path(data)["status"], "inconclusive")
        data = self.models["emi_loop"]
        self.assertAlmostEqual(emi_loop(data)["metrics"]["area_mm2"], 4)
        self.assertEqual(emi_loop({**data, "target_max_mm2": 1})["findings"][0]["status"], "suspected")

    def test_fault_protection_and_si_pi(self):
        data = self.models["fault"]
        self.assertAlmostEqual(fault(data)["metrics"]["i2t_bound_a2s"], 0.5)
        self.assertEqual(fault({**data, "curve_locator": ""})["status"], "inconclusive")
        data = self.models["si"]
        self.assertEqual(signal(data)["metrics"]["gamma_load"], 0)
        self.assertEqual(signal({**data, "load_ohm": 1000})["findings"][0]["id"], "SI-REFLECTION")
        data = self.models["pdn"]
        self.assertEqual(pdn({**data, "allowed_ripple_v": 0.001})["findings"][0]["id"], "PI-IMPEDANCE")

    def test_battery_sag_monotonicity(self):
        data = self.models["transient"]
        samples = [transient({**data, "battery_max_ohm": i / 20})["metrics"]["estimated_min_v"] for i in range(10)]
        self.assertTrue(all(a > b for a, b in zip(samples, samples[1:])))
