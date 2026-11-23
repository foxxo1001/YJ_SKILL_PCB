import os
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ehe.adapters.generic import ingest
from ehe.adapters.kicad import geometry, run
from ehe.adapters.sexpr import parse
from ehe.doctor import kicad_probe
from ehe.io import sha256
from ehe.project import source_manifest
from ehe.adapters.kicad import native_inputs

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "continuity"


class AdapterTests(unittest.TestCase):
    def test_export_bom_and_unstructured(self):
        with TemporaryDirectory() as folder:
            source = Path(folder, "altium.csv")
            source.write_text('Designator,Quantity,Footprint\n"R1,R2",2,0603\n', encoding="utf-8")
            result = ingest(source, "altium")
            self.assertEqual((result["status"], result["level"]), ("pass", "E1"))
            self.assertEqual(len(result["canonical"]["components"]), 2)
            image = Path(folder, "image.png")
            image.write_bytes(b"fixture")
            result = ingest(image)
            self.assertEqual(result["level"], "E0")
            self.assertEqual(result["canonical"]["nets"], [])

    def test_sexpr_rejects_non_tree(self):
        self.assertEqual(parse('(root (item "a (b)"))'), ["root", ["item", "a (b)"]])
        for malformed in ('(root', 'root', '(root) (sibling)', '(root "'):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                parse(malformed)

    def test_geometry_of_native_files(self):
        native = geometry(FIXTURE / "continuity.kicad_pcb")
        self.assertEqual(len(native["components"]), 2)
        self.assertEqual(len(native["pins"]), 2)
        self.assertEqual(len([e for e in native["geometry"] if e["kind"] == "segment"]), 1)
        self.assertEqual(native["nets"][0]["name"], "LINK")

    def test_real_clean_and_injected_erc_drc(self):
        if kicad_probe()["status"] != "available":
            if os.environ.get("EHE_REQUIRE_KICAD"):
                self.fail("KiCad is required for this integration run")
            self.skipTest("KiCad executable unavailable")
        with TemporaryDirectory(prefix="ehe-原生-") as storage:
            root = Path(storage, "设计 副本")
            shutil.copytree(FIXTURE, root, ignore=shutil.ignore_patterns(".ehe"))
            baseline = source_manifest(root, native_inputs(root))
            for kind in ("schematic", "pcb"):
                clean = run(root, kind)
                self.assertEqual(clean["status"], "pass", clean)
                self.assertEqual(clean["execution"]["exit_code"], 0)
                self.assertEqual(clean["raw_sha256"], sha256(Path(clean["execution"]["artifact"])))
            self.assertEqual(baseline, source_manifest(root, native_inputs(root)))
            from inject import disconnect
            disconnect(root)
            erc = run(root, "schematic")
            drc = run(root, "pcb")
            self.assertIn("pin_not_connected", {e["id"] for e in erc["findings"]}, erc)
            self.assertIn("unconnected_items", {e["id"] for e in drc["findings"]}, drc)
            self.assertEqual((erc["status"], drc["status"]), ("fail", "fail"))
            self.assertEqual((erc["execution"]["exit_code"], drc["execution"]["exit_code"]), (5, 5))
