import contextlib
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee.cli import main, runtime_files
from agent_ee.preflight import runtime_drift


class DoctorTests(unittest.TestCase):
    """Doctor reports each probed tool's version and the hash of the binary it relies on."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.binaries = {}
        for name in ("kicad-cli", "ngspice", "bwrap", "/usr/bin/python3", "claude", "_pcbnew.so", "agent-ee-placement"):
            path = self.root / Path(name).name
            path.write_bytes(f"binary bytes of {name}".encode())
            self.binaries[name] = path
        self.binaries["agent-ee-placement"].chmod(0o755)
        self.versions = {"kicad-cli": "10.0.7", "ngspice": "** ngspice-42 : Circuit level simulation program",
                         "bwrap": "bubblewrap 0.9.0", "claude": "2.1.302 (Claude Code)",
                         "agent-ee-placement": "agent-ee-placement 0.1.0"}
        self.missing = set()
        self.pcbnew_fails = False
        self.calls = []
        environment = patch.dict("os.environ", {"AGENT_EE_KICAD_HAPPY": "",
                                                 "AGENT_EE_RUST_PLANNER": str(self.binaries["agent-ee-placement"])})
        environment.start()
        self.addCleanup(environment.stop)

    def which(self, name):
        return None if name in self.missing else str(self.binaries[name]) if name in self.binaries else None

    def run_probe(self, command, **kwargs):
        self.calls.append(command)
        if command[0] in self.missing:
            raise FileNotFoundError(2, "No such file or directory", command[0])
        if command[0] == str(self.binaries["agent-ee-placement"]):
            return subprocess.CompletedProcess(command, 0, self.versions["agent-ee-placement"] + "\n", "")
        if command[0] == "/usr/bin/python3" and command[1] == "--version":
            return subprocess.CompletedProcess(command, 0, "Python 3.12.3\n", "")
        if command[0] == "/usr/bin/python3":
            if self.pcbnew_fails:
                return subprocess.CompletedProcess(command, 1, "", "ModuleNotFoundError: No module named 'pcbnew'\n")
            if "_pcbnew.__file__" in command[2]:
                return subprocess.CompletedProcess(command, 0, f"{self.binaries['_pcbnew.so']}\n", "")
            return subprocess.CompletedProcess(command, 0, "10.0.7-10.0.7~ubuntu24.04.1\n", "")
        return subprocess.CompletedProcess(command, 0, self.versions[command[0]] + "\n", "")

    def doctor(self, *args):
        out = io.StringIO()
        with patch("agent_ee.cli.shutil.which", side_effect=self.which), \
                patch("agent_ee.cli.subprocess.run", side_effect=self.run_probe), \
                contextlib.redirect_stdout(out):
            code = main(["doctor", *args])
        return code, json.loads(out.getvalue())

    def sha(self, name):
        return hashlib.sha256(self.binaries[name].read_bytes()).hexdigest()

    def test_reports_version_and_hash_of_every_relied_on_binary(self):
        code, report = self.doctor()
        self.assertEqual(code, 0)
        probes = report["prerequisites"]
        self.assertEqual(set(probes), {"kicad-cli", "ngspice", "bwrap", "python3", "pcbnew", "claude",
                                       "agent-ee-placement"})
        for name in ("kicad-cli", "ngspice", "bwrap", "claude", "agent-ee-placement"):
            self.assertEqual(probes[name]["binary"], str(self.binaries[name]))
            self.assertEqual(probes[name]["sha256"], self.sha(name))
            self.assertEqual(probes[name]["version_output"], self.versions[name])
        self.assertEqual(probes["python3"]["version_output"], "Python 3.12.3")
        self.assertEqual(probes["python3"]["sha256"], self.sha("/usr/bin/python3"))
        # pcbnew is imported through system Python; the relied-on binary is its native extension.
        self.assertEqual(probes["pcbnew"]["path"], str(self.binaries["/usr/bin/python3"]))
        self.assertEqual(probes["pcbnew"]["binary"], str(self.binaries["_pcbnew.so"]))
        self.assertEqual(probes["pcbnew"]["sha256"], self.sha("_pcbnew.so"))
        self.assertNotIn("skipped", report)
        self.assertEqual(probes["kicad-cli"]["erc_json_coordinates"],
                         {"version": "10.0.7", "regime": "native", "scale": 1})

    def planner_receipt(self, binary_sha256=None):
        path = self.binaries["agent-ee-placement"].with_name("agent-ee-placement.json")
        path.write_text(json.dumps({"schema": "agent-ee.placement-engine-install/1",
                                   "binary_sha256": binary_sha256 or self.sha("agent-ee-placement")}))
        return path

    def runtime(self):
        with patch("agent_ee.cli.shutil.which", side_effect=self.which), \
                patch("agent_ee.harnesses.executable_for", return_value=None), \
                patch("agent_ee.cli.pcbnew_extension", return_value=(str(self.binaries["_pcbnew.so"]), "")):
            return runtime_files()

    def test_planner_override_is_required_even_with_checks_only(self):
        for unavailable in ("missing", "not_executable"):
            with self.subTest(unavailable=unavailable):
                if unavailable == "missing":
                    with patch.dict("os.environ", {"AGENT_EE_RUST_PLANNER": str(self.root / "missing")}):
                        code, report = self.doctor("--checks-only")
                else:
                    self.binaries["agent-ee-placement"].chmod(0o644)
                    code, report = self.doctor("--checks-only")
                self.assertEqual(code, 1)
                self.assertIn("not executable", report["prerequisites"]["agent-ee-placement"]["error"])

    def test_planner_receipt_is_optional_but_must_match_when_present(self):
        code, report = self.doctor("--checks-only")
        self.assertEqual(code, 0)
        self.assertNotIn("build_receipt", report["prerequisites"]["agent-ee-placement"])
        receipt = self.planner_receipt()
        code, report = self.doctor("--checks-only")
        self.assertEqual(code, 0)
        self.assertEqual(report["prerequisites"]["agent-ee-placement"]["build_receipt"],
                         {"path": str(receipt), "sha256": hashlib.sha256(receipt.read_bytes()).hexdigest()})
        self.planner_receipt("0" * 64)
        code, report = self.doctor("--checks-only")
        self.assertEqual(code, 1)
        self.assertIn("differs from its build receipt", report["prerequisites"]["agent-ee-placement"]["error"])
        receipt.write_text("not JSON")
        code, report = self.doctor("--checks-only")
        self.assertEqual(code, 1)
        self.assertIn("error", report["prerequisites"]["agent-ee-placement"])

    def test_runtime_records_gate_planner_and_receipt_drift(self):
        absent = self.runtime()
        self.assertEqual(absent["agent-ee-placement"],
                         {"path": str(self.binaries["agent-ee-placement"]), "sha256": self.sha("agent-ee-placement")})
        self.assertEqual(absent["agent-ee-placement-build"], {"path": None, "sha256": None})
        receipt = self.planner_receipt()
        captured = self.runtime()
        self.assertEqual(captured["agent-ee-placement-build"],
                         {"path": str(receipt), "sha256": hashlib.sha256(receipt.read_bytes()).hexdigest()})
        self.binaries["agent-ee-placement"].write_bytes(b"different planner")
        changed = self.runtime()
        self.assertEqual(runtime_drift(captured, changed, [])['gated'], ['agent-ee-placement'])
        receipt.write_text(receipt.read_text() + "\n")
        self.assertEqual(runtime_drift(changed, self.runtime(), [])['gated'], ['agent-ee-placement-build'])
        self.binaries["agent-ee-placement"].unlink()
        unavailable = self.runtime()
        self.assertIsNone(unavailable["agent-ee-placement"]["sha256"])
        self.assertIn("not executable", unavailable["agent-ee-placement"]["diagnostic"])
        self.assertEqual(runtime_drift(changed, unavailable, [])['gated'],
                         ['agent-ee-placement', 'agent-ee-placement-build'])

    def test_kicad_cli_records_the_erc_coordinate_regime(self):
        for version, regime in (("10.0.6", "scale_x100"), ("10.0.7", "native"),
                                ("10.0.6-45-g0123abcd", "withheld"), ("9.0.4", "withheld")):
            self.versions["kicad-cli"] = version
            code, report = self.doctor("--checks-only")
            self.assertEqual(code, 0)
            record = report["prerequisites"]["kicad-cli"]["erc_json_coordinates"]
            self.assertEqual((record["version"], record["regime"]), (version, regime))
            self.assertEqual("reason" in record, regime == "withheld")

    def test_optional_pdf_extractor_is_identified_when_installed(self):
        tool = self.root / "pdftotext"
        tool.write_bytes(b"installed extractor")
        self.binaries["pdftotext"] = tool
        self.versions["pdftotext"] = "pdftotext version 24.02.0"
        code, report = self.doctor("--checks-only")
        self.assertEqual(code, 0)
        self.assertEqual(report["prerequisites"]["pdftotext"]["sha256"], self.sha("pdftotext"))

    def test_declared_optional_analyzer_is_hashed_or_reports_failure(self):
        installed = {"version": "2.3.1", "sha256": "a" * 64, "files": {"analyzer.py": "b" * 64}}
        with patch.dict("os.environ", {"AGENT_EE_KICAD_HAPPY": "/installed"}), \
                patch("agent_ee.kicad_happy.installation", return_value=installed):
            code, report = self.doctor("--checks-only")
        self.assertEqual(code, 0)
        self.assertEqual(report["prerequisites"]["kicad-happy"]["files"], installed["files"])
        with patch.dict("os.environ", {"AGENT_EE_KICAD_HAPPY": "/absent"}), \
                patch("agent_ee.kicad_happy.installation", side_effect=ValueError("missing analyzer")):
            code, report = self.doctor("--checks-only")
        self.assertEqual(code, 1)
        self.assertIn("error", report["prerequisites"]["kicad-happy"])

    def test_missing_tool_fails_without_a_hash(self):
        self.missing.add("ngspice")
        code, report = self.doctor()
        self.assertEqual(code, 1)
        self.assertIn("error", report["prerequisites"]["ngspice"])
        self.assertNotIn("sha256", report["prerequisites"]["ngspice"])
        self.assertEqual(report["prerequisites"]["kicad-cli"]["sha256"], self.sha("kicad-cli"))

    def test_unimportable_pcbnew_has_no_binary_identity(self):
        self.pcbnew_fails = True
        code, report = self.doctor()
        self.assertEqual(code, 1)
        pcbnew = report["prerequisites"]["pcbnew"]
        self.assertEqual(pcbnew["exit_code"], 1)
        self.assertIsNone(pcbnew["binary"])
        self.assertIsNone(pcbnew["sha256"])
        self.assertIn("No module named 'pcbnew'", pcbnew["diagnostics"])

    def test_unhashable_binary_fails_even_when_version_probe_exits_zero(self):
        self.binaries["bwrap"].unlink()
        self.binaries["bwrap"].mkdir()
        code, report = self.doctor()
        self.assertEqual(code, 1)
        bwrap = report["prerequisites"]["bwrap"]
        self.assertEqual(bwrap["exit_code"], 0)
        self.assertIsNone(bwrap["sha256"])
        self.assertIn("hash_error", bwrap)

    def test_checks_only_names_skipped_harness_and_still_requires_native_tools(self):
        self.missing.add("claude")
        code, report = self.doctor("--checks-only")
        self.assertEqual(code, 0)
        self.assertNotIn("claude", report["prerequisites"])
        self.assertIn("claude", report["skipped"])
        self.assertNotIn(["claude", "--version"], self.calls)
        self.missing.add("kicad-cli")
        code, report = self.doctor("--checks-only")
        self.assertEqual(code, 1)
        self.assertIn("error", report["prerequisites"]["kicad-cli"])

    def test_default_mode_still_fails_on_missing_harness(self):
        self.missing.add("claude")
        code, report = self.doctor()
        self.assertEqual(code, 1)
        self.assertIn("error", report["prerequisites"]["claude"])

    def test_package_interpreter_failure_is_not_hidden_by_working_system_python(self):
        self.missing.add('/venv/bin/python')
        with patch('agent_ee.cli.sys.executable', '/venv/bin/python'):
            code, report = self.doctor('--checks-only')
        self.assertEqual(code, 1)
        self.assertEqual(report['runtime']['executable'], '/venv/bin/python')
        self.assertFalse(report['runtime']['uses_system_python'])
        self.assertIn('error', report['prerequisites']['pcbnew'])
        self.assertIn('error', report['prerequisites']['python3'])

    def test_requested_router_checkout_is_required_and_reports_its_pin(self):
        code, report = self.doctor('--checks-only', '--router-checkout', str(self.root / 'missing-router'))
        self.assertEqual(code, 1)
        router = report['prerequisites']['kicad-route']
        self.assertEqual(len(router['pin']), 40)
        self.assertTrue(router['problems'])
        self.assertIsNone(router['sha256'])


if __name__ == "__main__":
    unittest.main()
