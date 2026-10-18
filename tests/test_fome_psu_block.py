"""The committed fome-mc PSU reference block (AGENTEE-106): capture, contract, review scope, adaptation."""
import copy
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import block_inventory_fixture as owned
from agent_ee.block_inventory import capture_inventory, validate_inventory
from agent_ee.reference_blocks import validate_block
from agent_ee.storage import digest, identity, read_json, write_json

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / 'fixtures/fome-mc/psu'
CAPTURE = FIXTURE / 'capture'
BLOCK = CAPTURE / 'psu'
COMMIT = 'df304f3321296f7e517e0d2e2aa52e1380d52993'
CAN = ['D4', 'D5', 'R10', 'R7', 'R8', 'R9', 'U4', 'U5']
# Ticket boundary: in /12V_RAW; out +5V, +3V3, sensor rails, VBOOST, power-good; plus the shared return.
BOUNDARY_NETS = ['/12V_RAW', '+5V', '+3V3', '/5V_SENSOR_1', '/5V_SENSOR_2', '/VBOOST',
                 '/mcu/5V_SENSOR_1_PG', '/mcu/5V_SENSOR_2_PG', 'GND']
KEY_VALUES = {'D1': 'SMCJ24A', 'U3': 'LMR16020PDDAR', 'R5': '12k', 'R6': '68k', 'C12': '10u'}
# The five known PSU findings, each a sourced requirement.
FINDINGS = {'psu-tvs-clamp': 'FOMEMC-7', 'psu-c12-part': 'FOMEMC-15', 'psu-c4-rating': 'FOMEMC-17',
            'psu-brownout-floor': 'FOMEMC-17', 'psu-reverse-block': None}
UNKNOWN = {'tvs-clamp', 'reverse-block', 'c12-part', 'c4-rating', 'brownout-floor', 'rail-regulation',
           'capacitor-ratings', 'input-filter'}


def generator():
    spec = importlib.util.spec_from_file_location('fome_psu_board', FIXTURE / 'board/generate.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(Path(root).rglob('*')) if p.is_file()}


def checkout():
    """The fome-mc repository holding the pinned commit, or None (only its object store is read)."""
    path = Path(os.environ.get('AGENT_EE_FOME_MC_CHECKOUT', Path.home() / 'git/fome-mc'))
    probe = subprocess.run(['git', '-C', str(path), 'cat-file', '-e', COMMIT + '^{commit}'], capture_output=True)
    return path if probe.returncode == 0 else None


class CommittedCaptureTests(unittest.TestCase):
    def setUp(self):
        self.record = read_json(BLOCK / 'block.json')

    def test_capture_records_pinned_commit_and_payload_hashes(self):
        self.assertEqual(self.record['source']['commit'], COMMIT)
        report = read_json(CAPTURE / 'inventory-report.json')
        self.assertEqual((report['commit'], report['inventory_sha256']), (COMMIT, identity(read_json(FIXTURE / 'inventory.json'))))
        block, = report['blocks']
        self.assertEqual((block['block_sha256'], block['contract_sha256']),
                         (digest(BLOCK / 'block.json'), digest(BLOCK / 'block-contract.json')))
        payload = {p: hashlib.sha256(data).hexdigest() for p, data in tree(BLOCK).items() if p != 'block.json'}
        self.assertEqual(payload, self.record['files'])
        self.assertEqual(self.record['source']['paths']['sheets/psu.kicad_sch'], 'sheets/psu.kicad_sch')

    def test_block_is_the_power_section_and_stays_unverified(self):
        instance, = self.record['instances']
        self.assertEqual((instance['path'], instance['uuid_path']), ('/psu/', '/00000000-0000-0000-0000-00005da72eff/'))
        self.assertEqual(len(instance['references']), 31)
        self.assertFalse(set(CAN) & set(instance['references']))
        report = validate_block(BLOCK)
        self.assertEqual(report['status'], 'unverified')
        self.assertTrue(report['draft_parts'])
        self.assertEqual(report['unresolved_libraries'], [])
        self.assertEqual(sorted(report['pending_requirements']), sorted(r['id'] for r in self.record['pending_requirements']))
        parts = {p['reference']: p for p in read_json(BLOCK / 'parts.json')['parts']}
        self.assertEqual({r: parts[r]['lcsc'] for r in ('D1', 'Q1', 'U3', 'L1', 'C6', 'U6')},
                         {'D1': 'C224045', 'Q1': 'C51499', 'U3': 'C190006', 'L1': 'C75646', 'C6': 'C454346', 'U6': 'C485517'})

    def test_contract_has_boundary_and_key_value_invariants(self):
        contract = read_json(BLOCK / 'interface.json')
        named = {i['names'][0] for i in contract['invariants'] if i['kind'] == 'named_net'}
        self.assertEqual(named, set(BOUNDARY_NETS))
        values = {i['component']: i['equals'] for i in contract['invariants'] if i['kind'] == 'value'}
        self.assertEqual({r: values[r] for r in KEY_VALUES}, KEY_VALUES)
        self.assertTrue(all(i['requirement'] == 'psu-interface' for i in contract['invariants']))

    def test_known_findings_are_sourced_requirements_bound_in_the_scope(self):
        pending = {r['id']: r for r in self.record['pending_requirements']}
        files = self.record['files']
        for rid, finding in FINDINGS.items():
            with self.subTest(requirement=rid):
                self.assertIn(pending[rid]['source']['path'], files)
                self.assertTrue(pending[rid]['source']['locator'].strip())
                if finding:
                    self.assertTrue(any(finding in c for c in pending[rid]['conditions']))
        scope = read_json(FIXTURE / 'review.scope.json')
        self.assertEqual(scope['schema'], 'agent-ee.scope/2')
        baseline = {r['id']: r for r in scope['baseline']['requirements']}
        for req in self.record['requirements']:
            self.assertEqual(baseline[req['id']], {k: req[k] for k in ('id', 'statement', 'source', 'checks')})
        for req in self.record['pending_requirements']:
            bound = baseline[req['id']]
            self.assertEqual((bound['statement'], bound['source']), (req['statement'], req['source']))
            self.assertTrue(set(bound['checks']) <= UNKNOWN)

    def test_board_generator_reproduces_committed_files(self):
        for name, text in generator().files().items():
            with self.subTest(file=name):
                self.assertEqual((FIXTURE / 'board' / name).read_text(), text)

    def test_unassigned_whole_instances(self):
        inventory = read_json(FIXTURE / 'inventory.json')
        validate_inventory(inventory)
        whole = [u['instance'] for u in inventory['unassigned'] if u['references'] == 'all']
        self.assertEqual(len(whole), 20)
        self.assertNotIn('/psu/', whole)


class UnassignedAllCaptureTests(unittest.TestCase):
    """An unassigned entry may name a whole sheet occurrence; assignment stays exhaustive."""

    def setUp(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-106-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'design').mkdir()
        self.commit = owned.write_repo(self.root / 'design')

    def single(self, entry=None):
        inventory = owned.inventory(self.commit)
        inventory['blocks'] = inventory['blocks'][:1]
        if entry is not None:
            inventory['unassigned'].append(entry)
        return inventory

    def test_whole_instance_is_accounted_and_refusals_publish_nothing(self):
        reason = 'Outside this single-block capture'
        report = capture_inventory(self.single(dict(instance='/CH2/', references='all', reason=reason)),
                                   self.root / 'design', self.root / 'one')
        self.assertEqual([b['directory'] for b in report['blocks']], ['ch1'])
        cases = {
            'unknown instance': dict(instance='/CH9/', references='all', reason=reason),
            'block instance': dict(instance='/CH1/', references='all', reason=reason),
        }
        for name, entry in cases.items():
            with self.subTest(case=name):
                inventory = self.single(entry)
                if name == 'block instance':
                    inventory['unassigned'].append(dict(instance='/CH2/', references='all', reason=reason))
                with self.assertRaises(ValueError):
                    capture_inventory(inventory, self.root / 'design', self.root / name.replace(' ', '-'))
                self.assertFalse((self.root / name.replace(' ', '-')).exists())
        with self.assertRaisesRegex(ValueError, 'without a block'):
            capture_inventory(self.single(), self.root / 'design', self.root / 'missing')


@unittest.skipIf(checkout() is None, 'no local fome-mc object store holding the pinned commit '
                 '(set AGENT_EE_FOME_MC_CHECKOUT)')
class RecaptureTests(unittest.TestCase):
    """Same commit, same bytes: recapture from the object store reproduces the committed fixture."""

    def test_recapture_reproduces_committed_capture(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        repo = checkout()
        with tempfile.TemporaryDirectory(prefix='agent-ee-106-recapture-') as tmp:
            out = Path(tmp) / 'capture'
            capture_inventory(read_json(FIXTURE / 'inventory.json'), repo, out)
            fresh, committed = tree(out), tree(CAPTURE)
            self.assertEqual(sorted(fresh), sorted(committed))
            recorded = self.record_repository()
            for path in committed:
                with self.subTest(file=path):
                    if str(repo.resolve()) == recorded or not path.endswith('.json'):
                        self.assertEqual(fresh[path], committed[path])
                    else:
                        # Another checkout path changes only the recorded local repository.
                        self.assertEqual(self.normal(fresh[path], str(repo.resolve())),
                                         self.normal(committed[path], recorded))

    @staticmethod
    def record_repository():
        return read_json(BLOCK / 'block.json')['source']['repository']

    @staticmethod
    def normal(data, repository):
        value = json.loads(data)
        text = json.dumps(value, sort_keys=True).replace(json.dumps(repository)[1:-1], 'REPOSITORY')
        value = json.loads(text)
        for block in value.get('blocks', []) if isinstance(value.get('blocks'), list) else []:
            block.pop('block_sha256', None)
            block.pop('contract_sha256', None)
        return value


class NativeReviewScopeTests(unittest.TestCase):
    """The committed scope/2 grades the captured block natively; unavailable claims stay unknown."""

    @classmethod
    def setUpClass(cls):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        from agent_ee.cli import run_review
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-106-review-')
        cls.run_dir = Path(cls.tmp.name) / 'run'
        cls.status = run_review(FIXTURE / 'review.scope.json', cls.run_dir, 600, checks_only=True,
                                output_stream=io.StringIO())
        cls.result = read_json(cls.run_dir / 'result.json')
        cls.checks = {c['id']: c for c in cls.result['checks']}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_native_checks_pass_and_unavailable_checks_are_unknown(self):
        self.assertEqual(self.status, 1, self.result['outcome'])
        self.assertEqual(set(self.checks), {'erc', 'netlist', 'interface'} | UNKNOWN)
        self.assertEqual((self.checks['erc']['execution'], self.checks['erc']['outcome']), ('ok', 'pass'), self.checks['erc'])
        self.assertEqual(self.checks['netlist']['execution'], 'ok')
        interface = self.checks['interface']
        self.assertEqual((interface['execution'], interface['outcome']), ('ok', 'pass'), interface['summary'])
        self.assertTrue(all(i['status'] == 'satisfied' for i in interface['details']['invariants']))
        for cid in UNKNOWN:
            with self.subTest(check=cid):
                self.assertEqual((self.checks[cid]['execution'], self.checks[cid]['outcome']), ('unsupported', 'unknown'))

    def test_block_run_reference_binds_exact_bytes_and_stays_unverified(self):
        from agent_ee.baseline import load_baseline
        baseline = load_baseline(self.run_dir)
        with tempfile.TemporaryDirectory(prefix='agent-ee-106-block-') as tmp:
            block = Path(tmp) / 'psu'
            shutil.copytree(BLOCK, block)
            record = read_json(block / 'block.json')
            record['runs'] = [dict(run_id=self.result['run_id'], candidate_id=self.result['candidate_id'],
                                   baseline_id=baseline['baseline_id'], files={p: p for p in record['files']})]
            write_json(block / 'block.json', record)
            report = validate_block(block, {self.result['run_id']: self.run_dir})
            # Checks-only leaves the review incomplete: no requirement is credited.
            self.assertEqual((report['status'], report['runs'][0]['status'], report['covered_requirements']),
                             ('unverified', 'unverified', []))
            changed = copy.deepcopy(record)
            changed['runs'][0]['baseline_id'] = '0' * 64
            write_json(block / 'block.json', changed)
            with self.assertRaisesRegex(ValueError, 'identity differs'):
                validate_block(block, {self.result['run_id']: self.run_dir})


class AdaptationSmokeTests(unittest.TestCase):
    """block_adaptation instantiates the PSU block on the owned standalone board."""

    def setUp(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-106-adapt-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        gen = generator()
        board = self.root / 'board'
        board.mkdir()
        (board / 'fome-mc-psu.kicad_sch').write_text(gen.root_schematic('psu.kicad_sch'))
        (board / gen.STUB_FILE).write_text(gen.stub_schematic())
        (board / 'fome-mc-psu.kicad_pcb').write_text(gen.board())
        (board / 'fome-mc-psu.kicad_pro').write_text(gen.project())
        (board / 'psu.kicad_sch').write_text('(kicad_sch (version 20250114) (generator "eeschema") '
                                            '(uuid "8f0d1f6e-0c1b-4f3a-9a52-6d7f3b2f1a01") (paper "A4") (lib_symbols))\n')
        shutil.copytree(BLOCK, self.root / 'blocks/psu')
        (self.root / 'requirements.md').write_text('Adapted PSU board: fresh native checks only.\n')
        self.record = read_json(BLOCK / 'block.json')
        scope = {'schema': 'agent-ee.scope/2', 'name': 'PSU adaptation smoke', 'scope': 'Owned board',
                 'schematic': 'board/fome-mc-psu.kicad_sch', 'board': 'board/fome-mc-psu.kicad_pcb',
                 'inputs': [{'path': 'board', 'at': 'board'}, {'path': 'blocks', 'at': 'blocks'},
                            {'path': 'requirements.md', 'at': 'requirements.md'},
                            {'path': 'instance.json', 'at': 'instance.json'}],
                 'requirements': ['requirements.md'],
                 'mutable_inputs': ['board/fome-mc-psu.kicad_sch', 'board/psu.kicad_sch'],
                 'checks': [{'id': 'erc', 'kind': 'erc'}, {'id': 'netlist', 'kind': 'netlist'}],
                 'baseline': {'requirements': [{'id': 'native', 'statement': 'Export the adapted board natively',
                                                'source': {'path': 'requirements.md', 'locator': 'board'},
                                                'checks': ['netlist']}],
                              'targets': ['requirements.md'], 'reference_architectures': ['requirements.md']}}
        # The board binds every port to the net the pinned design names it.
        request = {'id': 'psu1', 'block': 'blocks/psu', 'sheet': 'board/psu.kicad_sch',
                   'instance_path': '/' + gen.PSU_SHEET_UUID + '/',
                   'bindings': {p['name']: p['net_name'] for p in self.record['boundary_ports']},
                   'requirements': {'psu-native': ['erc', 'netlist'], 'psu-interface': ['netlist']},
                   'record': 'instance.json', 'provenance_check': 'provenance', 'netlist_check': 'netlist'}
        from agent_ee.block_adaptation import instantiate
        self.scope = instantiate(self.root, scope, request)

    def run_candidate(self, name, mutate=None):
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        if mutate:
            mutate(self.root / 'board/psu.kicad_sch')
        write_json(self.root / 'scope.json', self.scope)
        run = self.root / name
        run.mkdir()
        capture(self.root / 'scope.json', run)
        return {c['id']: c for c in run_checks(run, time.monotonic() + 300)}

    def test_instance_is_byte_exact_with_bound_boundary_and_fresh_checks(self):
        self.assertEqual((self.root / 'board/psu.kicad_sch').read_bytes(), (BLOCK / 'sheets/psu.kicad_sch').read_bytes())
        mapped = {r['id']: r['checks'] for r in self.scope['baseline']['requirements']}
        self.assertEqual((mapped['psu1-psu-native'], mapped['psu1-boundary']), (['erc', 'netlist'], ['provenance']))
        checks = self.run_candidate('run1')
        provenance = checks['provenance']
        self.assertEqual((provenance['execution'], provenance['outcome']), ('ok', 'pass'), provenance['summary'])
        self.assertTrue(provenance['details']['same_bytes'])
        ports = {p['port']: p for p in provenance['details']['boundary']}
        self.assertEqual(set(ports), {p['name'] for p in self.record['boundary_ports']})
        self.assertTrue(all(p['status'] == 'satisfied' for p in ports.values()), ports)
        self.assertEqual((checks['erc']['execution'], checks['erc']['outcome']), ('ok', 'pass'), checks['erc'])

    def test_adapted_feedback_divider_is_a_recorded_change(self):
        # Retune the buck divider (R6 68k -> 75k), an adaptation parameter of the block.
        def retune(path):
            text = path.read_text()
            marker = '"Reference" "R6"'
            at = text.index(marker)
            start = text.rindex('(symbol\n', 0, at)
            symbol = text[start:text.index('\n\t)\n', at)]
            path.write_text(text.replace(symbol, symbol.replace('"68k"', '"75k"'), 1))
        checks = self.run_candidate('run2', retune)
        provenance = checks['provenance']
        self.assertEqual(provenance['execution'], 'ok', provenance['summary'])
        self.assertFalse(provenance['details']['same_bytes'])
        changed = [c for c in provenance['details']['component_changes']
                   if (c['before'] or {}).get('value') != (c['after'] or {}).get('value')]
        self.assertEqual([(c['before']['value'], c['after']['value']) for c in changed], [('68k', '75k')])


if __name__ == '__main__':
    unittest.main()
