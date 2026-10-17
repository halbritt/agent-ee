import contextlib
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee.cli import main


class DoctorTests(unittest.TestCase):
    """Doctor reports each probed tool's version and the hash of the binary it relies on."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.binaries = {}
        for name in ("kicad-cli", "ngspice", "bwrap", "/usr/bin/python3", "claude", "_pcbnew.so"):
            path = self.root / Path(name).name
            path.write_bytes(f"binary bytes of {name}".encode())
            self.binaries[name] = path
        self.versions = {"kicad-cli": "10.0.7", "ngspice": "** ngspice-42 : Circuit level simulation program",
                         "bwrap": "bubblewrap 0.9.0", "claude": "2.1.302 (Claude Code)"}
        self.missing = set()
        self.pcbnew_fails = False
        self.calls = []

    def which(self, name):
        return None if name in self.missing else str(self.binaries[name]) if name in self.binaries else None

    def run_probe(self, command, **kwargs):
        self.calls.append(command)
        if command[0] in self.missing:
            raise FileNotFoundError(2, "No such file or directory", command[0])
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
        self.assertEqual(set(probes), {"kicad-cli", "ngspice", "bwrap", "python3", "pcbnew", "claude"})
        for name in ("kicad-cli", "ngspice", "bwrap", "claude"):
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


if __name__ == "__main__":
    unittest.main()
