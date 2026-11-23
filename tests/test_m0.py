import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ehe.doctor import kicad_probe
from ehe.io import inside, load, save, sha256


class BaselineTests(unittest.TestCase):
    def test_missing_tool(self):
        with patch.dict(os.environ, {"EHE_KICAD_CLI": ""}):
            self.assertEqual(kicad_probe()["status"], "not_run")

    def test_file_evidence_and_path_scope(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            evidence = inside(root, "nested/证据.json")
            save(evidence, {"status": "not_run"})
            self.assertEqual(load(evidence), {"status": "not_run"})
            self.assertEqual(len(sha256(evidence)), 64)
            with self.assertRaises(ValueError):
                inside(root, "../outside")
