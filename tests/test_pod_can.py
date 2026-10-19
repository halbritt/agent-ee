"""Pinned CAN reference sets, native composition and fail-closed provenance."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

from agent_ee.baseline import freeze_evidence, verify_evidence
from agent_ee.block_adaptation import instantiate
from agent_ee.candidate import capture
from agent_ee.checks import run_checks
from agent_ee.executor import sandbox_argv
from agent_ee.hardware_interface import read_inventory
from agent_ee.reference_blocks import validate_block
from agent_ee.schematic_emit import format_sexpr, parse_quoted
from agent_ee.storage import digest, read_json
from agent_ee.symbol_library import children, field
from tests.test_block_adaptation import fixture
from tests.toolchain import require_toolchain

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / 'fixtures/fome-mc/pod-can'
BLOCK = FIXTURE / 'block'
spec = importlib.util.spec_from_file_location('pod_can_audit', FIXTURE / 'audit.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in Path(root).rglob('*') if p.is_file()}


class CaptureTests(unittest.TestCase):
    def test_pinned_reference_sets_parts_origin_departure_and_unknowns(self):
        record = read_json(BLOCK / 'block.json')
        self.assertEqual(record['source']['commit'], audit.COMMIT)
        self.assertEqual(record['source']['repository'], 'fome-mc')
        self.assertEqual([i['references'] for i in record['instances']],
                         [['C308', 'U307'], ['D4', 'R9']])
        self.assertEqual(set(record['source']['paths']),
                         {'sheets/wbo1.kicad_sch', 'sheets/psu.kicad_sch'})
        for path, expected in record['files'].items():
            self.assertEqual(digest(BLOCK / path), expected, path)
        self.assertEqual(read_json(BLOCK / 'block-contract.json')['source'], record['source'])
        for token in ('T9', 'T8', 'T3', 'T6', 'ca2adce', 'b3176cb', 'ddcf0f7'):
            self.assertIn(token, record['source']['upstream_origin'])
        for token in ('MIT', 'Matthew Kennedy', 'No licence file', 'CC-BY-SA-4.0'):
            self.assertIn(token, record['source']['upstream_license'])
        requirement = next(r for r in record['requirements'] if r['id'] == 'pod-can-interface')
        self.assertIn('composed from two sheets', requirement['statement'])
        self.assertIn('2026-10-18', requirement['statement'])
        parts = {p['reference']: p for p in read_json(BLOCK / 'parts.json')['parts']}
        self.assertEqual({r: p['lcsc'] for r, p in parts.items()},
                         {'C308': 'C1525', 'U307': 'C38695', 'D4': 'C907839', 'R9': 'C22787'})
        self.assertEqual({r: p['value'] for r, p in parts.items()},
                         {'C308': '100n', 'U307': 'TJA1051T-3', 'D4': 'PESD1CAN-UX', 'R9': '120'})
        report = validate_block(BLOCK)
        self.assertEqual(report['status'], 'unverified')
        self.assertEqual(report['runs'], [])
        self.assertTrue(report['draft_parts'])
        self.assertEqual(report['pending_requirements'], ['pod-can-standalone'])
        self.assertTrue(report['undeclared_ports'])
        self.assertEqual(report['unresolved_libraries'], [])

    def test_offline_git_capture_twice_matches_every_committed_byte(self):
        require_toolchain()
        repository = Path(os.environ.get('AGENT_EE_FOME_MC_CHECKOUT', Path.home() / 'git/fome-mc'))
        self.assertTrue((repository / '.git').exists(), 'local pinned fome-mc object store required')
        code = '''from pathlib import Path
from agent_ee.reference_blocks import capture_block
from agent_ee.storage import read_json
root = Path('/candidate/block')
request = read_json(root / 'block.json')
supplements = {p: (root / p).read_bytes() for p in request['files']
               if p not in request['source']['paths'] and p != 'block-contract.json'}
for name in ('one', 'two'):
    report = capture_block(request, '/donor', request['source']['commit'], Path('/work') / name,
                           supplements=supplements, repository_label='fome-mc')
    assert report['status'] == 'unverified'
'''
        with tempfile.TemporaryDirectory() as tmp:
            command = sandbox_argv(FIXTURE, tmp, ['/usr/bin/python3', '-B', '-c', code],
                                   extra_ro=[(repository, '/donor')])
            self.assertIn('--unshare-net', command)
            completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            for name in ('one', 'two'):
                self.assertEqual(files(Path(tmp) / name), files(BLOCK))

    def test_changed_secondary_source_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'block'
            shutil.copytree(BLOCK, target)
            with (target / 'sheets/psu.kicad_sch').open('ab') as stream:
                stream.write(b'\n')
            with self.assertRaisesRegex(ValueError, 'inventory/hash differs'):
                validate_block(target)

    def test_multiple_sources_refused_before_any_adaptation_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scope, request = fixture(root)
            shutil.copytree(BLOCK, root / 'blocks/pod-can')
            request['block'] = 'blocks/pod-can'
            original = (root / request['sheet']).read_bytes()
            with self.assertRaisesRegex(ValueError, 'multiple source instances are not supported'):
                instantiate(root, scope, request)
            self.assertEqual((root / request['sheet']).read_bytes(), original)
            self.assertFalse((root / request['record']).exists())


class NativeCompositionTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-pod-can-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = audit.prepare(self.root / 'candidate')

    def checks(self):
        run = self.root / 'run'
        run.mkdir()
        capture(self.scope, run)
        checks = run_checks(run, time.monotonic() + 180)
        freeze_evidence(run, checks)
        verify_evidence(run, checks, required=True)
        by_id = {c['id']: c for c in checks}
        self.assertEqual((by_id['netlist']['execution'], by_id['netlist']['outcome']),
                         ('ok', 'info'), by_id['netlist']['summary'])
        self.assertEqual(by_id['interface']['execution'], 'ok', by_id['interface']['summary'])
        return by_id['interface'], read_inventory(run / 'work/netlist/design.xml')

    def test_native_can_joins_values_supply_domains_and_source_s_strap(self):
        result, native = self.checks()
        self.assertEqual(result['outcome'], 'pass', result['summary'])
        self.assertTrue(all(i['status'] == 'satisfied' for i in result['details']['invariants']))
        self.assertEqual(len(native['components']), 97)  # Whole-sheet audit, not a four-part pod block.
        self.assertEqual(len(native['sheets']), 3)

    def test_vio_on_5v_fails_with_native_supply_witness(self):
        path = self.scope.parent / 'project/wbo1.kicad_sch'
        tree = parse_quoted(path.read_bytes())
        # Disconnect only U307:5 from its original supply wire, then join +5V.
        wires = [w for w in children(tree, 'wire')
                 if any(p[1:] == ['198.12', '167.64'] for p in children(children(w, 'pts')[0], 'xy'))]
        self.assertEqual(len(wires), 1)
        self.assertEqual(field(wires[0], 'uuid', 1), ['ecbc2e5f-a1f7-5e5e-9d1f-70d3a519cff3'])
        tree.remove(wires[0])
        label = parse_quoted(b'''(global_label "+5V" (shape input) (at 198.12 167.64 0)
            (effects (font (size 1 1))) (uuid "ae175000-0000-4000-8000-000000000005"))''')
        tree.append(label)
        path.write_text('\n'.join(format_sexpr(tree)) + '\n')
        result, native = self.checks()
        self.assertEqual(result['outcome'], 'defect', result['summary'])
        self.assertIn('U307-5-net', result['details']['violations'])
        self.assertIn('supply-domains-separate', result['details']['violations'])
        self.assertEqual(native['nets'][native['memberships'][('U307', '5')]], '+5V')

    def test_disconnected_wbo_bus_fails_cross_sheet_joins(self):
        path = self.scope.parent / 'project/audit.kicad_sch'
        raw = path.read_text()
        self.assertEqual(raw.count('(label "CAN+"'), 2)
        path.write_text(raw.replace('(label "CAN+"', '(label "DISCONNECTED_CAN+"', 1))
        result, _ = self.checks()
        self.assertEqual(result['outcome'], 'defect')
        self.assertIn('D4-2-join', result['details']['violations'])
        self.assertIn('R9-1-join', result['details']['violations'])


if __name__ == '__main__':
    unittest.main()
