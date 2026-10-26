"""The committed fome-mc PSU reference block (AGENTEE-106): capture, contract, review scope, adaptation."""
import copy
import hashlib
import importlib.util
import io
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from tests import block_inventory_fixture as owned
from agent_ee.block_inventory import capture_inventory, validate_inventory
from agent_ee.reference_blocks import validate_block
from agent_ee.storage import digest, identity, read_json, write_json

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / 'fixtures/fome-mc/psu'
CAPTURE = FIXTURE / 'capture'
BLOCK = CAPTURE / 'psu'
COMMIT = 'df304f3321296f7e517e0d2e2aa52e1380d52993'
# Planning documents are cited by pinned identity (fome-mc df304f3), never captured.
DOCUMENTS = {'docs/DESIGN.md': ('53b60f15a26a5e0db05d2168a93a84df65188adb',
                                '2313a4fb7835d0621c45fe01b9acbd193ad22dcd95d25bc2b70706f614e026ae'),
             'docs/BLOCK_LIBRARY.md': ('ffa17817a39b5ce12f3c071230d2a395a95f0efd',
                                       '9544f3b3eda941cf79ad69f574832dd7844685fbc1e837baaadfaaded257859a')}
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


class CommittedCaptureTests(unittest.TestCase):
    def setUp(self):
        self.record = read_json(BLOCK / 'block.json')

    def test_review_receipt_cannot_substitute_for_the_host_only_run(self):
        receipt = REPO / 'evidence/fome-block-review-runs/receipts/psu'
        result = read_json(receipt / 'result.json')
        baseline = read_json(receipt / 'baseline.json')
        manifest = read_json(receipt / 'candidate.json')
        ref, = self.record['runs']
        self.assertEqual((ref['run_id'], ref['candidate_id'], ref['baseline_id']),
                         (result['run_id'], result['candidate_id'], baseline['baseline_id']))
        for path, captured in ref['files'].items():
            self.assertEqual(self.record['files'][path], manifest['files'][captured])
        self.assertEqual(result['state'], 'incomplete')
        self.assertFalse(result['adjudicated'])
        self.assertEqual(validate_block(BLOCK)['runs'],
                         [dict(run_id=ref['run_id'], status='unavailable')])
        with self.assertRaisesRegex(ValueError, 'candidate snapshot changed'):
            validate_block(BLOCK, {ref['run_id']: receipt})

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
        # The source repository is a label, and no planning document is captured.
        self.assertEqual(self.record['source']['repository'], 'fome-mc')
        inventory_block, = read_json(FIXTURE / 'inventory.json')['blocks']
        self.assertEqual(self.record['source']['upstream_origin'], inventory_block['origin'])
        self.assertEqual(self.record['source']['upstream_license'], inventory_block['upstream_license'])
        for token in ('T8', 'T4', 'T6', 'b3176cb'):
            self.assertIn(token, self.record['source']['upstream_origin'])
        self.assertIn('CC-BY-SA-4.0', self.record['source']['upstream_license'])
        self.assertEqual(read_json(BLOCK / 'block-contract.json')['source'], self.record['source'])
        self.assertFalse(set(self.record['source']['paths'].values()) & set(DOCUMENTS))
        self.assertFalse([p for p in tree(CAPTURE) if '/sources/' in p or p.endswith('.md')])
        for path, data in tree(FIXTURE).items():
            if '__pycache__' in path:
                continue
            with self.subTest(file=path):
                self.assertNotIn(b'/home/', data)

    def test_requirement_sources_are_pinned_external_citations(self):
        cited = [r['source'] for r in self.record['requirements'] + self.record['pending_requirements']]
        cited += [i['source'] for i in read_json(BLOCK / 'interface.json')['invariants']]
        for source in cited:
            self.assertEqual((source['kind'], source['repository'], source['commit']),
                             ('pinned-external', 'fome-mc', COMMIT))
            self.assertEqual((source['blob'], source['sha256']), DOCUMENTS[source['path']])
            self.assertTrue(source['locator'].strip())
        report = validate_block(BLOCK)
        self.assertEqual(sorted(c['requirement'] for c in report['external_sources']),
                         sorted(r['id'] for r in self.record['requirements'] + self.record['pending_requirements']))
        # The label resolves only through an explicit local mapping, so a citation is never verified here.
        self.assertEqual({c['status'] for c in report['external_sources']}, {'unverified'})
        self.assertEqual(report['status'], 'unverified')
        missing = validate_block(BLOCK, repository_locations={'fome-mc': str(REPO / 'no-such-repository')})
        self.assertEqual({c['status'] for c in missing['external_sources']}, {'unverified'})

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
        for rid, finding in FINDINGS.items():
            with self.subTest(requirement=rid):
                self.assertEqual(pending[rid]['source']['path'], 'docs/DESIGN.md')
                self.assertTrue(pending[rid]['source']['locator'].strip())
                if finding:
                    self.assertTrue(any(finding in c for c in pending[rid]['conditions']))
        scope = read_json(FIXTURE / 'review.scope.json')
        self.assertEqual(scope['schema'], 'agent-ee.scope/2')
        self.assertFalse([i for i in scope['inputs'] if 'sources' in i['path']])
        baseline = {r['id']: r for r in scope['baseline']['requirements']}
        contract = read_json(BLOCK / 'block-contract.json')
        # A cited block requirement is bound through the captured contract that preserves the citation.
        for key in ('requirements', 'pending_requirements'):
            for req in self.record[key]:
                with self.subTest(requirement=req['id']):
                    bound = baseline[req['id']]
                    locator = key + '/' + req['id'] + '/source'
                    self.assertEqual((bound['statement'], bound['source']),
                                     (req['statement'], {'path': 'block-contract.json', 'locator': locator}))
                    self.assertEqual({r['id']: r for r in contract[key]}[req['id']]['source'], req['source'])
                    if key == 'requirements':
                        self.assertEqual(bound['checks'], req['checks'])
                    else:
                        self.assertTrue(set(bound['checks']) <= UNKNOWN)
        # The board contract is the block contract with the board-side source.
        board = read_json(FIXTURE / 'board/interface.json')
        block = read_json(BLOCK / 'interface.json')
        self.assertEqual([{k: v for k, v in i.items() if k != 'source'} for i in board['invariants']],
                         [{k: v for k, v in i.items() if k != 'source'} for i in block['invariants']])
        self.assertEqual({json.dumps(i['source'], sort_keys=True) for i in board['invariants']},
                         {json.dumps(baseline['psu-interface']['source'], sort_keys=True)})

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
        from tests.toolchain import require_toolchain
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


class NativeReviewScopeTests(unittest.TestCase):
    """The committed scope/2 grades the captured block natively; unavailable claims stay unknown."""

    @classmethod
    def setUpClass(cls):
        from tests.toolchain import require_toolchain
        require_toolchain(native=True)
        from agent_ee.run_record import run_review
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
        from tests.toolchain import require_toolchain
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
