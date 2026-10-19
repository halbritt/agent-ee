"""AGENTEE-256: a pod-shaped campaign pins its adopted binding-fill --ratings receipt.

Owned synthetic parts from the passive-rating fixture, never real pod parts. Scripted
subscription responses; the binding-fill, emitter and annotation steps, the receipt
executor and the assembly_readiness check run for real.
"""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_ee.storage import digest, read_json, write_json

SRC = Path(__file__).resolve().parents[1] / 'src'
SCH, BOARD = 'rated.kicad_sch', 'rated.kicad_pcb'
NAME = 'rating-provenance'
RECEIPT, REPORT = f'{NAME}/binding-fill.receipt.json', f'{NAME}/binding-fill.json'


def sha(path):
    """Hash recomputed here from the bytes, independently of storage.digest."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class RatingLineageCampaignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        cls.stack = contextlib.ExitStack()
        try:
            cls.prepare()
        except BaseException:
            cls.stack.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    @classmethod
    def prepare(cls):
        import pcbnew
        from agent_ee import binding_fill, schematic_stage
        from agent_ee.parts_manifest import qualify_manifest
        from test_assembly_readiness import _write_sqlite_catalog
        from test_passive_ratings import MPN, PassiveRatingTests
        helper = PassiveRatingTests()
        helper.setUp()
        cls.stack.callback(helper.doCleanups)
        cls.root = Path(cls.stack.enter_context(tempfile.TemporaryDirectory()))
        source = cls.source = helper.root
        # Pod shape: the writer owns the unbound intent; binding-fill owns the bound projection.
        for name, value in [('intent.unbound.json', helper.intent), ('intent.json', helper.intent),
                            ('parts.json', helper.parts), ('refs.json', helper.refs),
                            ('deviations.json', helper.deviations), ('ratings.json', helper.ratings)]:
            write_json(source / name, value)
        captures = source / 'captures'
        captures.mkdir()
        shutil.copyfile(source / 'Owned.kicad_sym', captures / 'Owned.kicad_sym')
        shutil.copytree(source / 'Owned.pretty', captures / 'Owned.pretty')
        write_json(source / 'layout.json', {'schema': 'agent-ee.schematic-layout/1', 'project': 'rated',
                                            'namespace': '6f0f7f0e-5c43-5b8e-9a51-2a3f2c1d0e41', 'transforms': {}})
        pcbnew.SaveBoard(str(source / BOARD), pcbnew.BOARD())
        (source / SCH).write_bytes(b'')
        (source / 'assembly-catalog').mkdir()
        _write_sqlite_catalog(source / 'assembly-catalog', [{'lcsc': 'C38695', 'mpn': MPN, 'stock': 1000}])
        cls.fill = binding_fill.configuration(SRC, intent='intent.unbound.json', output='intent.json',
            manifest='parts.json', part_refs='refs.json', deviations='deviations.json', captures='.',
            dependencies=sorted(qualify_manifest(helper.parts, source)['dependencies']), ratings='ratings.json')
        stage = schematic_stage.configuration(SRC, board=BOARD, schematic=SCH, intent='intent.json',
                                              layout='layout.json', captures='captures', footprints='cad.json')
        cls.steps = [cls.fill, *stage['implementation_steps']]
        # The first candidate is emitted from the unbound intent: no LCSC, part number or rating.
        cls.execute(source, stage['implementation_steps'], 'initial')
        definition = {'harness': 'codex', 'model': 'test', 'effort': 'high', 'timeout_s': 10}
        cls.profile = {'schema': 'agent-ee.profile/1', 'name': 'rating-lineage-test',
                       'reviewers': [{**definition, 'role': 'circuit'}, {**definition, 'role': 'physical'}],
                       'adjudicator': definition}
        cls.definition = definition
        cls.writer = None
        runtime = {name: {'path': '/usr/bin/true', 'sha256': digest('/usr/bin/true')}
                   for name in ('codex', 'codex_tool_host', 'bwrap', '/usr/bin/python3')}
        runtime['kicad-cli'] = {'path': '/usr/bin/kicad-cli', 'sha256': digest('/usr/bin/kicad-cli')}
        cls.stack.enter_context(patch('agent_ee.cli.runtime_files', return_value=runtime))
        cls.stack.enter_context(patch('agent_ee.reviewers.dispatch', side_effect=cls.agent))
        cls.status, cls.state = cls.campaign('bound', source)
        cls.campaign_root = cls.root / 'bound'

    @classmethod
    def execute(cls, design, steps, label):
        from agent_ee.stage_receipts import adopt, run_step
        receipts = cls.root / 'outside' / label
        for k, spec in enumerate(steps, 1):
            receipt = run_step(receipts / str(k), design, spec, {'implementation': 1, 'source_candidate_id': label,
                                                                 'receipt_index': k})
            if receipt['status'] != 'completed':
                raise AssertionError((receipts / str(k) / 'run/console.log').read_text())
            adopt(receipts / str(k), receipt, design)
        return receipts

    @classmethod
    def agent(cls, run, label, definition, prompt, schema, timeout, *, cancellation=None, executable=None):
        run = Path(run)
        if label == 'implementation':
            if cls.writer is not None:
                cls.writer(run / 'review-work/implementation/design')
            return {'execution': 'ok'}, {'summary': 'Declared steps cover the repair; no writer edit'}
        packet = read_json(run / 'packet.json')
        ident = {k: packet[k] for k in ('candidate_id', 'run_id')}
        if label == 'adjudicator':
            checks = [read_json(p) for p in (run / 'checks').glob('*.json')]
            return {'execution': 'ok'}, {'schema': 'agent-ee.decisions/2', **ident, 'decisions': [
                {'finding_id': 'check/' + c['id'], 'disposition': 'accepted', 'reason': 'assembly failed',
                 'evidence': [{'path': 'checks/' + c['id'] + '.json', 'locator': 'outcome', 'pointer': '/outcome'}]}
                for c in checks if c['outcome'] == 'defect']}
        return {'execution': 'ok'}, {'schema': 'agent-ee.review/2', **ident, 'packet_sha256': packet['packet_sha256'],
                                     'role': label, 'coverage': ['assembly'],
                                     'limitations': ['scripted test response'], 'findings': []}

    @classmethod
    def scope(cls, source):
        return {'schema': 'agent-ee.scope/2', 'name': 'rating lineage', 'scope': 'pod-shaped rating lineage test',
                'board': BOARD, 'schematic': SCH,
                'mutable_inputs': ['intent.unbound.json', 'intent.json', SCH, BOARD],
                'inputs': [{'path': str(p), 'at': p.name} for p in sorted(source.iterdir())],
                'review_profile': cls.profile,
                'checks': [{'id': 'assembly', 'kind': 'assembly_readiness', 'stage': 'schematic',
                            'params': {'catalog': 'assembly-catalog/catalog-manifest.json'}}],
                'baseline': {'requirements': [{'id': 'R-assembly', 'statement': 'Populated parts are assemblable',
                             'source': {'path': 'requirements.md', 'locator': 'line 1'}, 'checks': ['assembly']}],
                             'targets': ['selection-requirements.json'], 'reference_architectures': ['selection.json']},
                'stages': {'schema': 'agent-ee.stages/1', 'order': ['schematic'], 'finding_retry_limit': 3,
                           'subjects': {'schematic': {'path': SCH, 'kind': 'symbols', 'references': ['R1']}}}}

    @classmethod
    def campaign(cls, name, source, *, writer=None, **config):
        from agent_ee.campaign import run_campaign
        write_json(cls.root / f'{name}.scope.json', cls.scope(source))
        write_json(cls.root / f'{name}.json', {
            'schema': 'agent-ee.campaign/1', 'scope': f'{name}.scope.json', 'review_profile': cls.profile,
            'implementation': cls.definition, 'implementation_steps': cls.steps, 'rating_provenance': NAME,
            'writer_editable': ['intent.unbound.json'], 'max_iterations': 3, 'max_seconds': 900, **config})
        cls.writer = writer
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                status = run_campaign(cls.root / f'{name}.json', cls.root / name)
        finally:
            cls.writer = None
        return status, read_json(cls.root / name / 'state.json')

    # Shared helpers over the retained 'bound' campaign.

    def run_manifest(self, number):
        return read_json(self.campaign_root / 'runs' / str(number) / 'candidate.json')

    def adopted_dir(self):
        return self.campaign_root / 'implementations/1/receipts/1-binding-fill'

    def verify(self, number, manifest):
        from agent_ee.rating_lineage import verify_candidate
        state = read_json(self.campaign_root / 'state.json')
        verify_candidate(self.campaign_root, {**state, 'history': state['history'][:number - 1]},
                         read_json(self.campaign_root / 'config.json'), number, manifest)

    def declared(self, manifest, provenance, files):
        manifest = copy.deepcopy(manifest)
        manifest['scope']['checks'][0]['params']['rating_provenance'] = provenance
        manifest['files'] = {**{k: v for k, v in manifest['files'].items() if not k.startswith(NAME + '/')}, **files}
        return manifest

    @contextlib.contextmanager
    def tampered(self, path, change):
        original = path.read_bytes()
        try:
            path.write_bytes(change(original))
            yield
        finally:
            path.write_bytes(original)

    def evaluate(self, inputs, provenance):
        from agent_ee.assembly_readiness import evaluate_assembly_readiness
        return evaluate_assembly_readiness(inputs, self.campaign_root / 'runs/2/work/assembly/design.xml',
                                           {'catalog': 'assembly-catalog/catalog-manifest.json',
                                            'rating_provenance': provenance})

    def copied_inputs(self):
        target = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'inputs'
        shutil.copytree(self.campaign_root / 'runs/2/inputs', target)
        return target

    def refused(self, message):
        from agent_ee.campaign import verify_campaign
        with self.assertRaisesRegex(ValueError, message):
            verify_campaign(self.campaign_root)

    def test_adopted_receipt_binds_derived_scope_and_assembly_passes(self):
        from agent_ee.campaign import verify_campaign
        self.assertEqual((self.status, self.state['state']), (0, 'criteria_satisfied'), self.state.get('error'))
        root = self.campaign_root
        self.assertEqual([h['outcome'] for h in self.state['history']], ['defects_or_unresolved', 'criteria_satisfied'])
        first, second = self.run_manifest(1), self.run_manifest(2)
        # Run 1: no lineage receipt and no declaration; the unbound parts are determinate defects.
        self.assertNotIn('rating_provenance', first['scope']['checks'][0]['params'])
        self.assertFalse(any(rel.startswith(NAME + '/') for rel in first['files']))
        initial = read_json(root / 'runs/1/checks/assembly.json')
        self.assertEqual(initial['outcome'], 'defect')
        # Run 2 was captured from the new derived proposal scope; the sealed scope file is untouched.
        proposal = root / 'implementations/1/proposal.scope.json'
        self.assertEqual(second['scope_path'], str(proposal))
        self.assertEqual(read_json(self.root / 'bound.scope.json'), self.scope(self.source))
        adopted = self.adopted_dir()
        receipt = read_json(adopted / 'receipt.json')
        self.assertEqual((receipt['spec']['step'], receipt['status']), ('binding-fill', 'completed'))
        self.assertIn('--ratings', receipt['spec']['argv'])
        pinned = sha(adopted / 'receipt.json')
        self.assertEqual(second['scope']['checks'][0]['params']['rating_provenance'],
                         {'receipt': RECEIPT, 'receipt_sha256': pinned, 'report': REPORT})
        self.assertEqual({k: v for k, v in second['files'].items() if k.startswith(NAME + '/')},
                         {RECEIPT: pinned, REPORT: sha(adopted / 'run/out/binding-fill.json')})
        self.assertEqual((root / 'runs/2/inputs' / RECEIPT).read_bytes(), (adopted / 'receipt.json').read_bytes())
        self.assertEqual((root / 'runs/2/inputs' / REPORT).read_bytes(),
                         (adopted / 'run/out/binding-fill.json').read_bytes())
        check = read_json(root / 'runs/2/checks/assembly.json')
        self.assertEqual((check['execution'], check['outcome']), ('ok', 'pass'), check.get('findings'))
        report = read_json(root / 'runs/2/work/assembly/assembly-readiness.json')
        self.assertEqual([(c['reference'], c['rating_provenance'], c['rating_record']) for c in report['components']],
                         [('R1', 'source_backed', 'receipt_bound')])
        self.assertEqual(report['rating_provenance']['receipt_sha256'], pinned)
        self.assertEqual(report['rating_provenance']['report_sha256'], sha(root / 'runs/2/inputs' / REPORT))
        # The derived baseline differs from the sealed one only by the coordinator declaration.
        baseline = read_json(root / 'runs/2/baseline.json')
        self.assertNotEqual(baseline['baseline_id'], self.state['baseline']['baseline_id'])
        self.assertTrue({RECEIPT, REPORT} <= set(baseline['files']))
        self.assertEqual(verify_campaign(root)['state'], 'criteria_satisfied')

    def test_tampered_receipt_is_refused(self):
        from agent_ee.rating_lineage import proposal_scope
        forged = lambda data: json.dumps({**json.loads(data), 'status': 'completed', 'duration_s': 0}).encode()
        # The captured copy: the check refuses a receipt that differs from its pin (unknown, never pass).
        inputs = self.copied_inputs()
        (inputs / RECEIPT).write_bytes(forged((inputs / RECEIPT).read_bytes()))
        provenance = self.run_manifest(2)['scope']['checks'][0]['params']['rating_provenance']
        result = self.evaluate(inputs, provenance)
        self.assertEqual(result['outcome'], 'unknown')
        self.assertIn('differs from its pinned sha256', result['rating_provenance']['error'])
        # A candidate whose captured receipt is not the adopted bytes is refused by the lineage check.
        with self.assertRaisesRegex(ValueError, 'lineage'):
            self.verify(2, self.declared(self.run_manifest(2), provenance,
                                         {RECEIPT: sha(inputs / RECEIPT), REPORT: sha(inputs / REPORT)}))
        # The retained adopted receipt: the coordinator neither captures nor verifies an edited one.
        with self.tampered(self.adopted_dir() / 'receipt.json', forged):
            design = Path(self.enterContext(tempfile.TemporaryDirectory()))
            state = read_json(self.campaign_root / 'state.json')
            with self.assertRaisesRegex(ValueError, 'changed'):
                proposal_scope(self.run_manifest(1)['scope'], design, self.campaign_root / 'implementations/1',
                               self.campaign_root, state, read_json(self.campaign_root / 'config.json'))
            self.refused('stage receipt changed after indexing')

    def test_tampered_report_is_refused(self):
        from agent_ee.rating_lineage import adopted
        def forged(data):
            report = json.loads(data)
            report['ratings']['derived']['R1']['record']['limitations'] = []
            return json.dumps(report).encode()
        inputs = self.copied_inputs()
        (inputs / REPORT).write_bytes(forged((inputs / REPORT).read_bytes()))
        provenance = self.run_manifest(2)['scope']['checks'][0]['params']['rating_provenance']
        result = self.evaluate(inputs, provenance)
        self.assertEqual(result['outcome'], 'unknown')
        self.assertIn('not the receipted artifact', result['rating_provenance']['error'])
        with self.assertRaisesRegex(ValueError, 'lineage'):
            self.verify(2, self.declared(self.run_manifest(2), provenance,
                                         {RECEIPT: sha(inputs / RECEIPT), REPORT: sha(inputs / REPORT)}))
        with self.tampered(self.adopted_dir() / 'run/out/binding-fill.json', forged):
            state = read_json(self.campaign_root / 'state.json')
            with self.assertRaisesRegex(ValueError, 'artifacts changed|not the receipted artifact'):
                adopted(self.campaign_root, state, 1)
            self.refused('stage receipt artifacts changed')

    def test_stale_or_previous_candidate_receipt_is_refused(self):
        # A genuine adoptable binding-fill --ratings receipt from outside this candidate's lineage.
        stale_source = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'design'
        shutil.copytree(self.campaign_root / 'runs/1/inputs', stale_source)
        stale = self.execute(stale_source, [self.fill], 'stale') / '1'
        stale_sha = sha(stale / 'receipt.json')
        self.assertNotEqual(stale_sha, sha(self.adopted_dir() / 'receipt.json'))
        files = {RECEIPT: stale_sha, REPORT: sha(stale / 'run/out/binding-fill.json')}
        stale_provenance = {'receipt': RECEIPT, 'receipt_sha256': stale_sha, 'report': REPORT}
        with self.assertRaisesRegex(ValueError, 'lineage'):
            self.verify(2, self.declared(self.run_manifest(2), stale_provenance, files))
        # Run 1 has no lineage receipt: a declaration there is borrowed, not derived.
        current = self.run_manifest(2)['scope']['checks'][0]['params']['rating_provenance']
        with self.assertRaisesRegex(ValueError, 'lineage'):
            self.verify(1, self.declared(self.run_manifest(1), current,
                                         {k: v for k, v in self.run_manifest(2)['files'].items() if k.startswith(NAME)}))
        # A run not captured from this implementation's proposal inherits; it cannot take the new receipt.
        foreign = self.run_manifest(2)
        foreign['scope_path'] = str(self.root / 'elsewhere.scope.json')
        with self.assertRaisesRegex(ValueError, 'lineage'):
            self.verify(2, foreign)
        # The genuine binding still verifies.
        self.verify(2, self.run_manifest(2))
        self.verify(1, self.run_manifest(1))

    def test_implementation_authored_hash_is_refused(self):
        # Any pin other than the coordinator's hash of the adopted bytes is refused, including a hash the
        # tool itself recorded (the report's artifact hash) or a well-formed 64-hex value.
        manifest = self.run_manifest(2)
        provenance = manifest['scope']['checks'][0]['params']['rating_provenance']
        receipt = read_json(self.adopted_dir() / 'receipt.json')
        for value in (receipt['artifacts']['out/binding-fill.json'], receipt['identity'][-64:], 'a' * 64):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'lineage'):
                self.verify(2, self.declared(manifest, {**provenance, 'receipt_sha256': value},
                                             {k: v for k, v in manifest['files'].items() if k.startswith(NAME)}))
        # The check itself refuses a pin that is not the captured receipt's hash.
        result = self.evaluate(self.campaign_root / 'runs/2/inputs', {**provenance, 'receipt_sha256': 'a' * 64})
        self.assertEqual(result['outcome'], 'unknown')

    def test_writer_planted_provenance_is_refused(self):
        def plant(design):
            (design / NAME).mkdir()
            shutil.copyfile(self.adopted_dir() / 'receipt.json', design / RECEIPT)
            shutil.copyfile(self.adopted_dir() / 'run/out/binding-fill.json', design / REPORT)
        status, state = self.campaign('planted', self.source, writer=plant)
        self.assertEqual((status, state['state']), (1, 'incomplete'))
        self.assertIn('captured file inventory', state['error'])
        self.assertFalse((self.root / 'planted/implementations/1/receipts').exists())

    def test_unknown_when_binding_fill_did_not_run(self):
        # The initial schematic carries AGENT_EE_Ratings filled outside this campaign; no step of its
        # lineage ran, so no provenance is derived and the check stays unknown, never pass.
        source = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'design'
        shutil.copytree(self.source, source)
        self.execute(source, self.steps, 'unreceipted')
        self.assertIn('AGENT_EE_Ratings', (source / SCH).read_text())
        status, state = self.campaign('unrun', source)
        # An unknown check leaves the run incomplete: no repair turn, no derived provenance.
        self.assertEqual((status, state['state'], state.get('reason')), (1, 'incomplete', 'execution_incomplete'))
        self.assertEqual(state['history'][0]['outcome'], 'incomplete')
        self.assertFalse((self.root / 'unrun/implementations').exists())
        manifest = read_json(self.root / 'unrun/runs/1/candidate.json')
        self.assertNotIn('rating_provenance', manifest['scope']['checks'][0]['params'])
        check = read_json(self.root / 'unrun/runs/1/checks/assembly.json')
        self.assertEqual(check['outcome'], 'unknown')
        report = read_json(self.root / 'unrun/runs/1/work/assembly/assembly-readiness.json')
        self.assertEqual([f['code'] for f in report['findings'] if f['severity'] == 'unknown'],
                         ['undeclared_rating_provenance'])
        self.assertEqual(len(state['history']), 1)


class RatingLineageConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.scope = {'inputs': [{'path': '/x', 'at': 'design'}],
                      'checks': [{'id': 'assembly', 'kind': 'assembly_readiness', 'params': {'catalog': 'c.json'}}]}
        self.steps = [{'step': 'binding-fill', 'argv': ['python3', '--ratings', 'r.json']}]

    def test_opt_in_requires_assembly_check_rating_step_and_unsealed_provenance(self):
        from agent_ee.rating_lineage import validate_campaign
        config = {'rating_provenance': NAME}
        validate_campaign(config, self.scope, self.steps)
        validate_campaign({}, {'inputs': [], 'checks': []}, [])
        cases = [({**config, 'rating_provenance': '../x'}, self.scope, self.steps, 'top-level'),
                 (config, {**self.scope, 'checks': []}, self.steps, 'assembly_readiness check'),
                 (config, self.scope, [{'step': 'binding-fill', 'argv': ['python3']}], '--ratings'),
                 (config, {**self.scope, 'inputs': [{'path': '/x', 'at': NAME + '/old.json'}]}, self.steps, 'collides'),
                 (config, {**self.scope, 'checks': [{'id': 'assembly', 'kind': 'assembly_readiness', 'params': {
                     'catalog': 'c.json', 'rating_provenance': {'receipt': 'a', 'receipt_sha256': 'b' * 64,
                                                                'report': 'c'}}}]}, self.steps, 'sealed scope')]
        for config_, scope, steps, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                validate_campaign(config_, scope, steps)

    def test_sealed_view_and_delivered_view(self):
        from agent_ee.rating_lineage import declared, delivered_view, strip
        derived = copy.deepcopy(self.scope)
        provenance = {'receipt': RECEIPT, 'receipt_sha256': 'a' * 64, 'report': REPORT}
        derived['checks'][0]['params']['rating_provenance'] = provenance
        self.assertEqual(declared(derived), provenance)
        self.assertEqual(strip(derived), self.scope)
        self.assertIsNone(declared(self.scope))
        derived['checks'].append(copy.deepcopy(self.scope['checks'][0]))
        with self.assertRaisesRegex(ValueError, 'different'):
            declared(derived)
        captured = {'a': '1', RECEIPT: '2', REPORT: '3'}
        self.assertEqual(delivered_view({'rating_provenance': NAME}, captured, {'a': '1'}), {'a': '1'})
        self.assertEqual(delivered_view({}, captured, {'a': '1'}), captured)


if __name__ == '__main__':
    unittest.main()
