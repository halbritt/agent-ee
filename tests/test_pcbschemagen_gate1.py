"""Unit tests for pcbschemagen_gate1 (Gate 1 Topo Verifier Adapter)."""

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

from agent_ee.pcbschemagen_gate1 import (
    DEFAULT_PCBSCHEMAGEN_ROOT,
    PCBSCHEMAGEN_REPOSITORY,
    PCBSchemaGenInputError,
    PCBSchemaGenUnavailable,
    check_pcbschemagen_checkout,
    design_to_snapshot,
    filter_headers_from_snapshot,
    is_external_header,
    main,
    netlist_to_snapshot,
    resolve_canonical_part_id,
    verify_gate1,
)
from toolchain import require_pcbschemagen


# An owned stand-in for the external verifier: the files the adapter requires and
# framework.topo functions that report no errors. It tests the adapter's checkout handling
# and records, never PCBSchemaGen's verdicts. OWNED_VERIFIER_MOVE makes Phase 2 commit to the
# checkout while Gate 1 runs.
OWNED_VERIFIER = {
    'framework/topo/kg_loader.py': (
        'class KGStore:\n'
        '    def __init__(self, base_dir):\n'
        '        self.component_map = {}\n'
        '        self.kg_component_map = {}\n'),
    'framework/topo/build_topology.py': 'def augment_snapshot(snapshot, kg_store):\n    return snapshot\n',
    'framework/topo/complex_task_validator.py': (
        'def is_complex_task(task_id):\n    return False\n\n'
        'def validate_complex_task(snapshot, task_id, kg_store):\n    return True, [], []\n'),
    'framework/topo/error_types.py': 'def compute_reward_from_errors(**kwargs):\n    return 1.0\n',
    'framework/topo/match_skeleton.py': 'def check_graph_similarity(std, gen, task_id=None):\n    return []\n',
    'framework/topo/phase2_checks.py': (
        'import os\nimport subprocess\nfrom pathlib import Path\n\n'
        'def run_phase2_checks(snapshot, kg_store, task_id=None):\n'
        '    if os.environ.get("OWNED_VERIFIER_MOVE"):\n'
        '        subprocess.run(["git", "-C", str(Path(__file__).resolve().parents[2]),\n'
        '                        "-c", "user.name=fixture", "-c", "user.email=fixture@invalid",\n'
        '                        "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty",\n'
        '                        "-m", "Owned commit during verification"], check=True)\n'
        '    return []\n'),
    'framework/topo/rule_checker.py': 'def check_rules(snapshot, rules, task_id=None):\n    return []\n',
    'framework/task_config.yaml': '{}',
    'component.json': '{"components": []}',
    'kg_component.json': '{"components": []}',
    'kg/kg_open_schematics.json': '{"components": []}',
    'yaml.py': '# Owned availability fixture, not a YAML parser.\n',
    'networkx.py': '# Owned availability fixture, not a graph implementation.\n',
}


def fixture_git(root, *args):
    return subprocess.run(['git', '-C', str(root), '-c', 'user.name=fixture', '-c', 'user.email=fixture@invalid',
                           '-c', 'commit.gpgsign=false', *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def owned_verifier(root):
    """Write and commit the owned verifier at root; return its commit."""
    root = Path(root)
    for name, contents in OWNED_VERIFIER.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    fixture_git(root, 'init', '-q')
    fixture_git(root, 'add', '.')
    fixture_git(root, 'commit', '-q', '-m', 'Owned verifier fixture')
    return fixture_git(root, 'rev-parse', 'HEAD')


def run_isolated(script, *args, env=None):
    """Run script in a new interpreter, so owned framework.topo modules stay out of this process."""
    return subprocess.run([sys.executable, '-c', textwrap.dedent(script), *map(str, args)],
                          env=env, capture_output=True, text=True, timeout=60)


RECORD_SCRIPT = '''
    import json
    import sys
    sys.path.insert(0, sys.argv[1])   # the owned availability modules (yaml, networkx)
    from agent_ee import pcbschemagen_gate1 as gate1
    report = gate1.verify_gate1(json.loads(sys.argv[2]), task_id=6, pcbschemagen_root=sys.argv[1]).report
    print(json.dumps(report["verifier"]))
'''


class FixtureKGStore:
    """Owned pin metadata for translation tests; no external verification behavior."""

    def __init__(self):
        self.component_map = {
            'TPS54302': {'pins': [dict(num=str(n), name=name) for n, name in
                                enumerate(('GND', 'SW', 'VIN', 'FB', 'EN', 'BOOT'), 1)]},
            'ADS1115IDGS': {'pins': [dict(num='3', name='GND'), dict(num='8', name='VDD')]},
        }

    def get_component_info(self, part_id):
        return self.component_map.get(part_id)

    def has_component(self, part_id):
        return part_id in self.component_map


def _make_valid_tps54302_design():
    return {
        "schema": "agent-ee.design/1",
        "parts": {
            "U1": {
                "value": "TPS54302",
                "lib_id": "Power_Management:TPS54302DDR",
                "footprint": "Package_SO:SOT-23-6",
                "group": "regulator",
            },
            "C1": {
                "value": "10u",
                "lib_id": "Device:C",
                "footprint": "Capacitor_SMD:C_0805_2012Metric",
            },
            "C2": {
                "value": "100n",
                "lib_id": "Device:C",
                "footprint": "Capacitor_SMD:C_0603_1608Metric",
            },
            "C3": {
                "value": "22u",
                "lib_id": "Device:C",
                "footprint": "Capacitor_SMD:C_0805_2012Metric",
            },
            "L1": {
                "value": "4.7u",
                "lib_id": "Device:L",
                "footprint": "Inductor_SMD:L_0630",
            },
            "R1": {
                "value": "100k",
                "lib_id": "Device:R",
                "footprint": "Resistor_SMD:R_0603_1608Metric",
            },
            "R2": {
                "value": "32.4k",
                "lib_id": "Device:R",
                "footprint": "Resistor_SMD:R_0603_1608Metric",
            },
            "J1": {
                "value": "PinHeader_1x02",
                "lib_id": "Connector_Generic:Conn_01x02",
                "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
            },
            "J2": {
                "value": "PinHeader_1x02",
                "lib_id": "Connector_Generic:Conn_01x02",
                "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
            },
        },
        "nets": {
            "VIN": ["J1.1", "U1.3", "C1.1"],
            "GND": ["J1.2", "J2.2", "U1.1", "C1.2", "R2.2", "C3.2"],
            "SW": ["U1.2", "L1.1", "C2.2"],
            "BOOT": ["U1.6", "C2.1"],
            "VOUT": ["L1.2", "R1.1", "C3.1", "J2.1"],
            "FB": ["U1.4", "R1.2", "R2.1"],
        },
        "no_connects": ["U1.5"],
        "ground": "GND",
        "power_flags": ["VIN", "GND"],
    }



class TestPCBSchemaGenGate1Translation(unittest.TestCase):
    """Test translation from agent-ee.design/1 and KiCad XML netlist into framework/topo snapshots."""

    def setUp(self):
        self.kg = FixtureKGStore()

    def test_translation_from_design_statement(self):
        design = _make_valid_tps54302_design()
        snapshot = design_to_snapshot(design, kg_store=self.kg)

        self.assertIn("components", snapshot)
        self.assertIn("nets", snapshot)

        comps = {c["ref"]: c for c in snapshot["components"]}
        self.assertIn("U1", comps)
        u1 = comps["U1"]
        self.assertEqual(u1["part_id"], "TPS54302")

        # Crucial requirement: pins must have both pin_id and pin_name
        pin_map = {p["pin_id"]: p for p in u1["pins"]}
        self.assertIn("3", pin_map)
        self.assertEqual(pin_map["3"]["pin_name"], "VIN")
        self.assertEqual(pin_map["3"]["net"], "VIN")

        self.assertIn("1", pin_map)
        self.assertEqual(pin_map["1"]["pin_name"], "GND")
        self.assertEqual(pin_map["1"]["net"], "GND")

        self.assertIn("5", pin_map)
        self.assertEqual(pin_map["5"]["pin_name"], "EN")
        self.assertEqual(pin_map["5"]["net"], "NC")

        # Connectors identified
        self.assertTrue(is_external_header("J1", comps["J1"]["part_id"]))
        self.assertTrue(is_external_header("J2", comps["J2"]["part_id"]))

        # Check nets endpoints have both pin_id and pin_name
        nets = {n["name"]: n for n in snapshot["nets"]}
        self.assertIn("VIN", nets)
        vin_eps = {ep["ref"]: ep for ep in nets["VIN"]["endpoints"]}
        self.assertIn("U1", vin_eps)
        self.assertEqual(vin_eps["U1"]["pin_id"], "3")
        self.assertEqual(vin_eps["U1"]["pin_name"], "VIN")

    def test_translation_from_kicad_xml_netlist(self):
        sample_netlist = Path(__file__).parent / 'fixtures/pcbschemagen-adapter.xml'

        snapshot = netlist_to_snapshot(sample_netlist, kg_store=self.kg)
        comps = {c["ref"]: c for c in snapshot["components"]}

        self.assertIn("U2", comps)
        u2 = comps["U2"]
        # Symbol prefix Analog_ADC: should be stripped and mapped to ADS1115IDGS
        self.assertEqual(u2["part_id"], "ADS1115IDGS")

        pin_map = {p["pin_id"]: p for p in u2["pins"]}
        self.assertIn("8", pin_map)
        self.assertEqual(pin_map["8"]["pin_name"], "VDD")
        self.assertIn("3", pin_map)
        self.assertEqual(pin_map["3"]["pin_name"], "GND")

        # Passives have "~" pin_name
        c1 = comps["C1"]
        self.assertEqual(c1["part_id"], "C")
        for p in c1["pins"]:
            self.assertEqual(p["pin_name"], "~")

    def test_canonical_part_id_resolution(self):
        self.assertEqual(
            resolve_canonical_part_id("U1", "TPS54302DDR", lib_id="Power_Management:TPS54302DDR", kg_store=self.kg),
            "TPS54302",
        )
        self.assertEqual(
            resolve_canonical_part_id("R1", "10k", lib_id="Device:R", kg_store=self.kg),
            "R",
        )
        self.assertEqual(
            resolve_canonical_part_id("J1", "Conn_01x02", lib_id="Connector_Generic:Conn_01x02", kg_store=self.kg),
            "PinHeader_1x02",
        )

    def test_filter_headers_from_snapshot(self):
        raw_snap = {
            "components": [
                {"ref": "U1", "part_id": "TPS54302"},
                {"ref": "C1", "part_id": "C"},
                {"ref": "J1", "part_id": "PinHeader_1x02"},
            ],
            "nets": [{"name": "VIN", "endpoints": [{"ref": "U1"}, {"ref": "J1"}]}],
        }
        filtered = filter_headers_from_snapshot(raw_snap)
        refs = [c["ref"] for c in filtered["components"]]
        self.assertEqual(refs, ["U1", "C1"])
        self.assertEqual(len(filtered["nets"]), 1)



class TestPCBSchemaGenGate1Verification(unittest.TestCase):
    """Test Gate 1 verification execution, rule checks, and error reporting."""

    def setUp(self):
        require_pcbschemagen()

    def test_valid_circuit_passes_gate1(self):
        design = _make_valid_tps54302_design()
        result = verify_gate1(design_input=design, task_id=6, task_name="BUCK_AUX_LOGIC")

        self.assertTrue(result.passed)
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.reward, 1.0)
        self.assertEqual(len(result.errors), 0)
        self.assertEqual(result.report["schema"], "agent-ee.pcbschemagen-gate1/1")
        self.assertEqual(result.report["stats"]["critical_errors"], 0)
        # The report names the commit the real checkout is at, read here independently.
        head = subprocess.run(['git', '-C', str(DEFAULT_PCBSCHEMAGEN_ROOT), 'rev-parse', 'HEAD'],
                              check=True, capture_output=True, text=True).stdout.strip()
        self.assertEqual(result.report["verifier"], {"repository": PCBSCHEMAGEN_REPOSITORY, "commit": head})

    def test_missing_required_connection_fails_gate1(self):
        # Case A: floating VIN (U1.3 removed from VIN net and not in no_connects)
        design_floating_vin = _make_valid_tps54302_design()
        design_floating_vin["nets"]["VIN"] = ["J1.1", "C1.1"]  # U1.3 missing

        result = verify_gate1(design_input=design_floating_vin, task_id=6)
        self.assertFalse(result.passed)
        self.assertEqual(result.status, "FAIL")
        self.assertLess(result.reward, 1.0)
        self.assertGreater(len(result.errors), 0)
        categories = [e["category"] for e in result.report["errors"]]
        self.assertIn("supply_missing", categories)

        # Case B: missing SW connection
        design_floating_sw = _make_valid_tps54302_design()
        design_floating_sw["nets"]["SW"] = ["L1.1", "C2.2"]  # U1.2 missing
        result_sw = verify_gate1(design_input=design_floating_sw, task_id=6)
        self.assertFalse(result_sw.passed)
        self.assertEqual(result_sw.status, "FAIL")
        sw_cats = [e["category"] for e in result_sw.report["errors"]]
        self.assertTrue("unconnected_pin" in sw_cats or "supply_missing" in sw_cats)

    def test_pin_role_mismatch_fails_gate1(self):
        # Case A: Supply short (VIN and GND on same net)
        design_shorted = _make_valid_tps54302_design()
        design_shorted["nets"]["GND"].append("U1.3")  # Connect VIN to GND net too
        design_shorted["nets"]["VIN"].remove("U1.3")

        result = verify_gate1(design_input=design_shorted, task_id=6)
        self.assertFalse(result.passed)
        self.assertEqual(result.status, "FAIL")
        categories = [e["category"] for e in result.report["errors"]]
        self.assertIn("supply_short", categories)

        # Case B: SW swapped with GND pin role
        # Normal: U1.1=GND, U1.2=SW. Swapped: U1.1=SW, U1.2=GND.
        design_swapped = _make_valid_tps54302_design()
        design_swapped["nets"]["GND"].remove("U1.1")
        design_swapped["nets"]["SW"].remove("U1.2")
        design_swapped["nets"]["GND"].append("U1.2")  # SW pin connected to GND net
        design_swapped["nets"]["SW"].append("U1.1")   # GND pin connected to SW net

        # With standard bootstrap and bypass rules, swapped SW and GND violates C_DIRECT
        rules = [
            {
                "rule_type": "C_DIRECT",
                "endpoint_a": {"component_part_id": "TPS54302", "pin_role": "buck_boot"},
                "endpoint_b": {"component_part_id": "TPS54302", "pin_role": "buck_sw"},
            },
            {
                "rule_type": "C_DIRECT",
                "endpoint_a": {"component_part_id": "TPS54302", "pin_role": "buck_vin"},
                "endpoint_b": {"component_part_id": "TPS54302", "pin_role": "buck_gnd"},
            },
        ]
        result_swapped = verify_gate1(design_input=design_swapped, task_id=6, rules=rules)
        self.assertFalse(result_swapped.passed)
        self.assertEqual(result_swapped.status, "FAIL")
        categories = [e["category"] for e in result_swapped.report["errors"]]
        self.assertIn("cap_direct_missing", categories)

    def test_external_headers_do_not_trigger_component_count_mismatch(self):
        # Create design with 5 external pin headers
        design_with_headers = _make_valid_tps54302_design()
        for idx in range(3, 7):
            ref = f"J{idx}"
            design_with_headers["parts"][ref] = {
                "value": "PinHeader_1x02",
                "lib_id": "Connector_Generic:Conn_01x02",
                "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
            }
            design_with_headers["nets"]["GND"].append(f"{ref}.1")
            design_with_headers["nets"]["VIN"].append(f"{ref}.2")

        # Standard reference snapshot with 0 pin headers
        std_snapshot = {
            "components": [
                {
                    "ref": "U1",
                    "part_id": "TPS54302",
                    "pins": [
                        {"pin_id": "1", "pin_name": "GND", "net": "GND"},
                        {"pin_id": "2", "pin_name": "SW", "net": "SW"},
                        {"pin_id": "3", "pin_name": "VIN", "net": "VIN"},
                        {"pin_id": "4", "pin_name": "FB", "net": "FB"},
                        {"pin_id": "5", "pin_name": "EN", "net": "NC"},
                        {"pin_id": "6", "pin_name": "BOOT", "net": "BOOT"},
                    ],
                },
                {"ref": "C1", "part_id": "C", "pins": [{"pin_id": "1", "net": "VIN"}, {"pin_id": "2", "net": "GND"}]},
                {"ref": "C2", "part_id": "C", "pins": [{"pin_id": "1", "net": "BOOT"}, {"pin_id": "2", "net": "SW"}]},
                {"ref": "C3", "part_id": "C", "pins": [{"pin_id": "1", "net": "VOUT"}, {"pin_id": "2", "net": "GND"}]},
                {"ref": "L1", "part_id": "L", "pins": [{"pin_id": "1", "net": "SW"}, {"pin_id": "2", "net": "VOUT"}]},
                {"ref": "R1", "part_id": "R", "pins": [{"pin_id": "1", "net": "VOUT"}, {"pin_id": "2", "net": "FB"}]},
                {"ref": "R2", "part_id": "R", "pins": [{"pin_id": "1", "net": "FB"}, {"pin_id": "2", "net": "GND"}]},
            ],
            "nets": [
                {"name": "VIN", "endpoints": [{"ref": "U1", "pin_id": "3", "pin_name": "VIN"}]},
                {"name": "GND", "endpoints": [{"ref": "U1", "pin_id": "1", "pin_name": "GND"}]},
                {"name": "SW", "endpoints": [{"ref": "U1", "pin_id": "2", "pin_name": "SW"}]},
                {"name": "BOOT", "endpoints": [{"ref": "U1", "pin_id": "6", "pin_name": "BOOT"}]},
                {"name": "FB", "endpoints": [{"ref": "U1", "pin_id": "4", "pin_name": "FB"}]},
                {"name": "VOUT", "endpoints": [{"ref": "L1", "pin_id": "2", "pin_name": "2"}]},
            ],
        }

        result = verify_gate1(
            design_input=design_with_headers,
            task_id=6,
            std_snapshot=std_snapshot,
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.status, "PASS")
        count_errors = [
            e for e in result.report["errors"] if e.get("category") == "component_count_mismatch"
        ]
        self.assertEqual(len(count_errors), 0)

    def test_cli_invocation(self):
        design = _make_valid_tps54302_design()
        with tempfile.TemporaryDirectory() as tmpdir:
            design_path = Path(tmpdir) / "design.json"
            out_path = Path(tmpdir) / "gate1_report.json"
            design_path.write_text(json.dumps(design), encoding="utf-8")

            # 1. Valid invocation returns 0
            rc = main(["--design", str(design_path), "--task-id", "6", "--out", str(out_path), "--quiet"])
            self.assertEqual(rc, 0)
            self.assertTrue(out_path.exists())
            report = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "PASS")

            # 2. Missing args returns 2
            rc_bad = main([])
            self.assertEqual(rc_bad, 2)


class TestVerifierBoundary(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.temp = Path(scratch.name)

    def test_any_clean_commit_is_accepted_and_its_commit_recorded(self):
        root = self.temp / 'verifier'
        first = owned_verifier(root)
        design = json.dumps(_make_valid_tps54302_design())
        result = run_isolated(RECORD_SCRIPT, root, design)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'repository': PCBSCHEMAGEN_REPOSITORY, 'commit': first})
        # A later upstream commit with changed knowledge graph data is used and recorded as such.
        (root / 'kg_component.json').write_text('{"components": [], "revision": 2}')
        fixture_git(root, 'commit', '-q', '-am', 'Owned knowledge graph update')
        second = fixture_git(root, 'rev-parse', 'HEAD')
        self.assertNotEqual(second, first)
        result = run_isolated(RECORD_SCRIPT, root, design)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['commit'], second)

    def test_dirty_tree_is_refused_with_its_changes(self):
        root = self.temp / 'verifier'
        commit = owned_verifier(root)
        self.assertEqual(check_pcbschemagen_checkout(root).commit, commit)
        (root / 'component.json').write_text('{"components": [], "edited": true}')
        with self.assertRaisesRegex(PCBSchemaGenInputError, 'must be clean.*' + commit + '(.|\\n)*component.json'):
            check_pcbschemagen_checkout(root)
        fixture_git(root, 'add', 'component.json')   # staged only: still not the commit's bytes
        with self.assertRaisesRegex(PCBSchemaGenInputError, 'must be clean'):
            check_pcbschemagen_checkout(root)

    def test_non_git_directories_are_refused_clearly(self):
        plain = self.temp / 'plain'
        for name, contents in OWNED_VERIFIER.items():
            (plain / name).parent.mkdir(parents=True, exist_ok=True)
            (plain / name).write_text(contents)
        with self.assertRaisesRegex(PCBSchemaGenInputError, 'is not a Git checkout'):
            check_pcbschemagen_checkout(plain)
        # A copy inside another repository: that repository's commit says nothing about the verifier.
        outer = self.temp / 'outer'
        owned_verifier(outer)
        nested = outer / 'vendored-verifier'
        for name, contents in OWNED_VERIFIER.items():
            (nested / name).parent.mkdir(parents=True, exist_ok=True)
            (nested / name).write_text(contents)
        fixture_git(outer, 'add', '.')
        fixture_git(outer, 'commit', '-q', '-m', 'Owned nested copy')
        with self.assertRaisesRegex(PCBSchemaGenInputError, 'is not a Git checkout: it is a directory inside'):
            check_pcbschemagen_checkout(nested)

    def test_code_imported_at_one_commit_is_not_recorded_as_another(self):
        root = self.temp / 'verifier'
        first = owned_verifier(root)
        script = RECORD_SCRIPT + """
    import subprocess
    subprocess.run(['git', '-C', sys.argv[1], '-c', 'user.name=fixture', '-c', 'user.email=fixture@invalid',
                    '-c', 'commit.gpgsign=false', 'commit', '-q', '--allow-empty', '-m', 'Owned move'], check=True)
    try:
        gate1.verify_gate1(json.loads(sys.argv[2]), task_id=6, pcbschemagen_root=sys.argv[1])
    except gate1.PCBSchemaGenInputError as exc:
        print('refused:', exc)
    else:
        print('accepted')
"""
        result = run_isolated(script, root, json.dumps(_make_valid_tps54302_design()))
        self.assertEqual(result.returncode, 0, result.stderr)
        recorded, outcome = result.stdout.split('\n', 1)
        self.assertEqual(json.loads(recorded)['commit'], first)
        self.assertIn('refused: PCBSchemaGen code in this process was imported at commit ' + first, outcome)
        self.assertIn('start a new process', outcome)

    def test_checkout_moved_during_verification_is_refused(self):
        root = self.temp / 'verifier'
        owned_verifier(root)
        env = dict(os.environ, OWNED_VERIFIER_MOVE='1')
        result = run_isolated(RECORD_SCRIPT, root, json.dumps(_make_valid_tps54302_design()), env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('during verification; rerun Gate 1', result.stderr)
        self.assertEqual(result.stdout, '')

    def test_bytecode_imports_allow_repeat_loads_but_tracked_edits_are_refused(self):
        root = self.temp / 'verifier'
        commit = owned_verifier(root)
        script = '''
            import sys
            import unittest
            from pathlib import Path
            from agent_ee import pcbschemagen_gate1 as adapter
            root = Path(sys.argv[1])
            assert not sys.dont_write_bytecode, 'bytecode must be enabled'
            sys.path.insert(0, str(root))
            for _ in range(2):
                assert adapter._ensure_topo_imports(root).commit == sys.argv[2]
                adapter.Gate1KGStore(root)
            assert list(root.glob('framework/topo/__pycache__/*.pyc')), 'no bytecode written'
            (root / 'component.json').write_text('{"components": [], "modified": true}')
            with unittest.TestCase().assertRaisesRegex(adapter.PCBSchemaGenInputError, 'must be clean'):
                adapter.check_pcbschemagen_checkout(root)
        '''
        env = {k: v for k, v in os.environ.items()
               if k not in ('PYTHONDONTWRITEBYTECODE', 'PYTHONPYCACHEPREFIX')}
        result = run_isolated(script, root, commit, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_checkout_is_explicit_even_after_a_verifier_was_loaded(self):
        missing = self.temp / 'absent'
        with self.assertRaisesRegex(PCBSchemaGenUnavailable, 'PCBSchemaGen verifier missing'):
            verify_gate1(_make_valid_tps54302_design(), pcbschemagen_root=missing)
        with contextlib.redirect_stderr(io.StringIO()) as error:
            rc = main(['--design', 'unused.json', '--pcbschemagen-root', str(missing)])
        self.assertEqual(rc, 2)
        self.assertIn('clone ' + PCBSCHEMAGEN_REPOSITORY, error.getvalue())
        self.assertIn('PCBSCHEMAGEN_ROOT', error.getvalue())
