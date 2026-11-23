"""Regression tests for complete coverage, native design validation and global launch."""
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ehe.adapters.kicad import geometry, native_inputs
from ehe.cli import dispatch, parser_for_cli
from ehe.doctor import kicad_probe
from ehe.io import save
from ehe.project import source_manifest
from ehe.review import full_review

ROOT = Path(__file__).resolve().parents[1]


def script_module(name):
    spec = spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CompleteCoverageTests(unittest.TestCase):
    def test_all_scope_declares_missing_models(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            save(root / "engineering.json", {
                "rails": [{"id": "3V3", "peak_a": 0.15, "limit_a": 0.3, "voltage_v": 3.3}],
                "not_applicable": {"motor": "Only passive signals and a regulated logic rail"},
            })
            args = parser_for_cli().parse_args(["budget", str(root), "--scope", "all"])
            outcome = dispatch(args)
        self.assertEqual(outcome["status"], "inconclusive")
        self.assertEqual(outcome["checks"]["rails"]["status"], "pass")
        for name in ("return_path", "emi_loop", "mechanical", "transient", "thermal"):
            self.assertEqual(outcome["checks"][name]["status"], "not_run")
            self.assertIn(f"#/{name}", outcome["checks"][name]["missing_evidence"][0])
        self.assertEqual(outcome["checks"]["motor"]["status"], "not_applicable")
        self.assertTrue(outcome["checks"]["motor"]["reason"])

    def test_empty_project_reports_required_rows(self):
        with TemporaryDirectory() as directory:
            outcome = full_review(Path(directory))
            self.assertTrue(Path(outcome["report_path"]).is_file())
        self.assertEqual(outcome["status"], "inconclusive")
        self.assertEqual(outcome["coverage"]["physical"]["status"], "not_run")
        self.assertEqual(outcome["coverage"]["emi_loop"]["status"], "not_run")
        self.assertEqual(outcome["coverage"]["board_outline"]["status"], "not_run")

    def test_edge_cuts_arc_includes_cardinal_extremes(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "test.kicad_pcb"
            source.write_text('(kicad_pcb (general (thickness 1.6)) '
                              '(gr_arc (start -3 4) (mid -4 -3) (end 3 -4) (layer "Edge.Cuts")))',
                              encoding="utf-8")
            outline = geometry(source)["project"]["outline"]
        self.assertEqual(outline["status"], "pass")
        self.assertEqual(outline["metrics"]["min_mm"], [-5.0, -5.0])
        self.assertEqual(outline["metrics"]["max_mm"], [3.0, 4.0])

    def test_native_board_and_schematic_parity_with_actionable_failure(self):
        if kicad_probe()["status"] != "available":
            if os.environ.get("EHE_REQUIRE_KICAD"):
                self.fail("KiCad unavailable")
            self.skipTest("KiCad unavailable")
        with TemporaryDirectory(prefix="ehe-从零验证-") as directory:
            root = Path(directory) / "blank"
            script_module("make_continuity").build(root)
            before = source_manifest(root, native_inputs(root))
            conflict = full_review(root)
            self.assertEqual(before, source_manifest(root, native_inputs(root)))
            self.assertEqual(conflict["coverage"]["erc"]["status"], "pass")
            self.assertEqual(conflict["coverage"]["drc"]["status"], "pass")
            self.assertEqual(conflict["coverage"]["schematic_parity"]["status"], "fail")
            proposal = next(row for row in conflict["recommendations"] if row["finding"] == "net_conflict")
            self.assertTrue(proposal["evidence_ids"])
            self.assertTrue(proposal["where"]["items"][0]["pos"])
            self.assertTrue(proposal["change"] and proposal["verification"] and proposal["affected_checks"])
            self.assertEqual(conflict["coverage"]["board_outline"]["metrics"]["width_mm"], 18)
            self.assertEqual(conflict["coverage"]["board_outline"]["metrics"]["height_mm"], 10)
            pcb = root / "continuity.kicad_pcb"
            pcb.write_text(pcb.read_text(encoding="utf-8").replace('"LINK"', '"/LINK"'), encoding="utf-8")
            fresh = source_manifest(root, native_inputs(root))
            after = full_review(root, design_verification=True)
            self.assertEqual(after["eda_status"], "pass")
            self.assertEqual(after["coverage"]["schematic_parity"]["status"], "pass")
            self.assertTrue(Path(after["coverage"]["design_bom"]["artifact"]).is_file())
            self.assertIn("Manufacturer part number: J1", after["coverage"]["design_bom"]["missing_evidence"])
            self.assertEqual(after["status"], "inconclusive")
            self.assertEqual(after["coverage"]["physical"]["status"], "not_run")
            self.assertEqual(fresh, source_manifest(root, native_inputs(root)))


class InstalledSkillTests(unittest.TestCase):
    def test_registered_runtime_from_a_different_working_directory(self):
        installer = script_module("install_skill")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            install_root = root / "installed"
            _, location = installer.install(install_root, "project", ["codex"])["codex"]
            unrelated = root / "another project"
            unrelated.mkdir()
            launch = [sys.executable, str(Path(location) / "scripts" / "run_ehe.py")]
            env = {**os.environ, "PATH": str(unrelated), "EHE_RUNTIME": "", "PYTHONUTF8": "1"}
            probe = subprocess.run([*launch, "--resolve"], cwd=unrelated, env=env,
                                   capture_output=True, encoding="utf-8", timeout=15)
            self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)
            self.assertEqual(json.loads(probe.stdout)["source"], "installed")
            audited = subprocess.run([*launch, "review", "full", str(unrelated)], cwd=unrelated, env=env,
                                     capture_output=True, encoding="utf-8", timeout=15)
            self.assertEqual(audited.returncode, 3, audited.stderr)
            self.assertIn("return_path", json.loads(audited.stdout)["coverage"])

    def test_upgrade_refuses_custom_edits(self):
        installer = script_module("install_skill")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _, location = installer.install(root, "project", ["codex"])["codex"]
            customized = Path(location) / "SKILL.md"
            changed = customized.read_text(encoding="utf-8") + "\nUser notes.\n"
            customized.write_text(changed, encoding="utf-8")
            with self.assertRaises(FileExistsError):
                installer.install(root, "project", ["codex"], upgrade=True)
            self.assertEqual(customized.read_text(encoding="utf-8"), changed)

    def test_zcode_install_targets_zcode_skill_root(self):
        installer = script_module("install_skill")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _, location = installer.install(root, "project", ["zcode"])["zcode"]
            installed = Path(location)
            self.assertEqual(installed, root.resolve() / ".zcode" / "skills" / "embedded-hardware-engineer")
            for name in ("SKILL.md", "runtime.json", "install.json"):
                self.assertTrue((installed / name).is_file(), name)

    def test_all_covers_every_host_and_both_keeps_two(self):
        installer = script_module("install_skill")
        with TemporaryDirectory() as directory:
            for option, expected in (("all", set(installer.HOSTS)), ("both", {"codex", "claude"})):
                project = Path(directory) / option
                project.mkdir()
                with patch.object(sys, "argv", ["install_skill.py", "--project", str(project), "--host", option]):
                    installer.main()
                installed = {host for host, folder in installer.HOSTS.items()
                             if (project / folder / "embedded-hardware-engineer" / "SKILL.md").is_file()}
                self.assertEqual(installed, expected)

    def test_zcode_probe_counts_only_the_probed_project(self):
        with patch.dict(sys.modules, {"install_skill": script_module("install_skill")}):
            probe_hosts = script_module("probe_hosts")
        user_level = {"name": "embedded-hardware-engineer", "path": "elsewhere/SKILL.md", "rootPath": "elsewhere"}
        with TemporaryDirectory() as directory:
            root = Path(directory) / "zcode"
            local_root = root / ".zcode" / "skills"
            local = {"name": "embedded-hardware-engineer", "path": str(local_root / "embedded-hardware-engineer" / "SKILL.md"),
                     "rootPath": str(local_root)}
            for skills, discovered in (([user_level, local], True), ([user_level], False)):
                listing = subprocess.CompletedProcess("zcode", 0, stdout=json.dumps({"skills": skills}))
                with patch.object(probe_hosts, "zcode_command", return_value=["zcode"]), \
                        patch.object(probe_hosts, "version", return_value="0.0.0"), \
                        patch.object(probe_hosts.subprocess, "run", return_value=listing):
                    outcome = probe_hosts.probe("zcode", root, 45)
                self.assertEqual(outcome["discovered"], discovered, skills)
                self.assertEqual(outcome["path"], local["path"] if discovered else None)
