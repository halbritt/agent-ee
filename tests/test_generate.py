"""Rung-1 generation driver: receipts, stage ordering, spec parsing, parts and scope compilation.

The integration test runs stages 1-5 and 7 with --skip-review on the real toolchain;
stage 6 (subscription inference) is never run here.
"""
import csv
import io
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from agent_ee import generate as g
try:
    from toolchain import require_kicad_route, require_toolchain
except ImportError:  # run as tests.test_generate from the repository root
    from tests.toolchain import require_kicad_route, require_toolchain

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / 'fixtures/rung1/spec.md'
HEADER = 'Comment,Designator,Footprint,LCSC Part #,Quantity\n'
# Owned synthetic catalog rows (test values, not supplier data).
ROWS = [
    '10k,"R5,R6",Resistor_SMD:R_0603_1608Metric,C900001,2',
    '10k,R7,Resistor_SMD:R_0402_1005Metric,C900002,9',
    '10k,RN1,Resistor_SMD:R_Array_Convex_4x0603,C900003,1',
    '10k,R8,Resistor_SMD:R_1206_3216Metric,,1',
    '100n,"C5,C6",Capacitor_SMD:C_0603_1608Metric,C900010,2',
    '100n,"C7,C8,C9",Capacitor_SMD:C_0603_1608Metric,C900011,3',
    '100n,C10,Capacitor_SMD:C_1206_3216Metric,C900012,40',
    '4.7u,C11,Capacitor_SMD:C_0805_2012Metric,C900013,1',
]


def rows_of(lines):
    return g.read_catalog((HEADER + '\n'.join(lines) + '\n').encode())


def git_catalog(root, body):
    """A throwaway git checkout holding a catalog at the fome-mc path."""
    (root / 'export').mkdir(parents=True)
    (root / g.CATALOG_PATH).write_text(body)
    env = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.invalid',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.invalid'}
    for argv in (['init', '-q'], ['remote', 'add', 'origin', 'https://example.invalid/catalog.git'],
                 ['add', g.CATALOG_PATH], ['commit', '-q', '-m', 'catalog']):
        subprocess.run(['git', '-C', str(root), *argv], check=True, env=env, capture_output=True)
    return root


class ReceiptTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='agent-ee-generate-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_receipt_records_files_commands_and_validates(self):
        stage = g.Stage(self.root, 2, 'parts')
        stage.start()
        (stage.dir / 'a.txt').write_text('a')
        stage.input(stage.dir / 'a.txt', 'example')
        stage.run(['/bin/sh', '-c', 'echo out; echo err >&2; exit 3'], label='probe')
        receipt = g.validate_receipt(stage.finish('failed', 'probe exited 3'))
        self.assertEqual(receipt['schema'], 'agent-ee.rung1-stage-receipt/0-experimental')
        self.assertEqual(receipt['qualification'], 'experimental-unqualified')
        self.assertEqual(receipt['inputs'][0]['path'], 'stages/2-parts/a.txt')
        command = receipt['commands'][0]
        self.assertEqual((command['exit_code'], command['execution']), (3, 'failed'))
        self.assertEqual((self.root / command['stdout']).read_text(), 'out\n')
        self.assertEqual(json.loads((stage.dir / 'receipt.json').read_text()), receipt)

    def test_receipt_refuses_unknown_status_and_unexplained_failure(self):
        stage = g.Stage(self.root, 1, 'requirements')
        stage.start()
        with self.assertRaises(ValueError):
            stage.finish('passed')
        with self.assertRaises(ValueError):
            g.validate_receipt(stage.finish('failed'))


class OrderingTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='agent-ee-generate-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def run_with(self, functions, **options):
        return g.generate(SPEC, self.root / 'run', skip_review=True, functions=functions, **options)

    def test_failed_stage_stops_every_later_stage(self):
        called = []
        def ok(stage, state, options):
            called.append(stage.name)
        def fail(stage, state, options):
            called.append(stage.name)
            raise g.StageFailure('parts unavailable')
        functions = {name: ok for _, name in g.STAGES} | {'parts': fail}
        record = self.run_with(functions)
        self.assertEqual(called, ['requirements', 'parts'])
        self.assertEqual([s['status'] for s in record['stages']],
                         ['ok', 'failed'] + ['not_reached'] * 5)
        self.assertEqual(record['stages'][1]['error'], 'parts unavailable')
        self.assertFalse(record['done'])
        for item in record['stages']:
            receipt = json.loads((self.root / 'run' / item['receipt']).read_text())
            g.validate_receipt(receipt)
            if item['status'] == 'not_reached':
                self.assertEqual(receipt['details']['blocked_by'], '2-parts')
                self.assertIsNone(receipt['started'])

    def test_unexpected_exception_is_a_failure_with_traceback(self):
        def boom(stage, state, options):
            raise KeyError('missing')
        functions = {name: (lambda *a: None) for _, name in g.STAGES} | {'routing': boom}
        record = self.run_with(functions)
        self.assertEqual(record['stages'][4]['status'], 'failed')
        self.assertIn('KeyError', record['stages'][4]['error'])
        self.assertEqual([s['status'] for s in record['stages'][5:]], ['not_reached'] * 2)

    def test_missing_dependency_blocks_without_stand_in(self):
        def missing(stage, state, options):
            raise g.DependencyMissing('agent_ee.schematic_emit (G1) is not in this checkout')
        functions = {name: (lambda *a: None) for _, name in g.STAGES} | {'schematic': missing}
        record = self.run_with(functions)
        self.assertEqual([s['status'] for s in record['stages']][2:],
                         ['not_reached:dependency_missing'] + ['not_reached'] * 4)

    def test_done_requires_satisfied_verdict_and_existing_exports(self):
        def review(stage, state, options):
            state['verdict'] = 'criteria_satisfied'
            return 'ok'
        def fab(stage, state, options):
            package = stage.dir / 'package'
            package.mkdir()
            (package / 'bom.csv').write_text('x\n')
            manifest = {'files': [{'path': 'bom.csv', 'sha256': g.sha256_bytes(b'x\n'), 'bytes': 2}]}
            (stage.dir / 'fab-manifest.json').write_text(json.dumps(manifest))
            state['fab_manifest'] = stage.dir / 'fab-manifest.json'
        functions = {name: (lambda *a: None) for _, name in g.STAGES} | {'review': review, 'fab': fab}
        self.assertTrue(self.run_with(functions)['done'])
        shutil.rmtree(self.root / 'run')
        empty = {'fab': lambda stage, state, options: fab(stage, state, options) or
                 (stage.dir / 'package' / 'bom.csv').write_text('')}
        self.assertFalse(self.run_with(functions | empty)['done'])
        shutil.rmtree(self.root / 'run')
        escalated = {'review': lambda stage, state, options: state.update(verdict='escalated')}
        self.assertFalse(self.run_with(functions | escalated)['done'])

    def test_existing_run_directory_and_missing_profile_are_refused(self):
        (self.root / 'run').mkdir()
        with self.assertRaises(FileExistsError):
            self.run_with({})
        with self.assertRaises(ValueError):
            g.generate(SPEC, self.root / 'other')


class SpecTest(unittest.TestCase):
    def test_fixture_spec_parameters(self):
        p = g.parse_spec(SPEC.read_text())
        self.assertEqual(p['header'], {'ref': 'J1', 'pins': 3, 'nets': {'1': '/IN', '2': '/OUT', '3': 'GND'},
                                       'assembly': 'not_assembled',
                                       'assembly_reason': 'through-hole header, hand-soldered'})
        self.assertEqual((p['resistor']['value'], p['capacitor']['value']), ('10k', '100n'))
        self.assertEqual(p['response'], {'net': '/OUT', 'at_ms': 1.0, 'min_v': 0.62, 'max_v': 0.65})
        self.assertEqual((p['step_v'], p['min_track_width_mm'], p['tau_ms']), (1.0, 0.2, 1.0))
        self.assertAlmostEqual(p['target_v'], 0.632120558, places=8)
        source = p['sources']['response']
        flat = ' '.join(SPEC.read_text().split())
        self.assertEqual(flat[source['start']:source['end']], source['text'])

    def test_ambiguous_missing_or_inconsistent_spec_is_refused(self):
        text = SPEC.read_text()
        cases = {'duplicate': text + ' A 10k resistor R2 connects /IN to /OUT.',
                 'missing width': text.replace('at least 0.20 mm wide', 'wide enough'),
                 'topology': text.replace('C1 connects /OUT to GND', 'C1 connects /IN to GND'),
                 'header ref': text.replace('J1 on the left edge', 'J2 on the left edge')}
        for name, variant in cases.items():
            with self.subTest(name), self.assertRaises(g.StageFailure):
                g.parse_spec(variant)


class PartsTest(unittest.TestCase):
    def test_selection_is_deterministic_and_records_rejections(self):
        expected = g.select_part(rows_of(ROWS), 'Device:R', '10k')
        for seed in range(5):
            shuffled = list(ROWS)
            random.Random(seed).shuffle(shuffled)
            chosen, rejected, default = g.select_part(rows_of(shuffled), 'Device:R', '10k')
            self.assertEqual(chosen['lcsc'], expected[0]['lcsc'])
            self.assertEqual(sorted(r['lcsc'] for r in rejected), sorted(r['lcsc'] for r in expected[1]))
        chosen, rejected, default = expected
        # 0603 is one size from the 0805 default; 0402 is two; the array and no-LCSC rows are rejected.
        self.assertEqual((chosen['lcsc'], chosen['package'], default), ('C900001', '0603', '0805'))
        reasons = {r['lcsc']: r['reason'] for r in rejected}
        self.assertIn('two-terminal chip', reasons['C900003'])
        self.assertEqual(reasons[''], 'no LCSC part number')
        self.assertEqual(reasons['C900002'], 'ranked below the chosen row')

    def test_equal_distance_prefers_larger_then_more_used(self):
        chosen, rejected, _ = g.select_part(rows_of(ROWS), 'Device:C', '100n')
        self.assertEqual(chosen['lcsc'], 'C900012')  # 1206 beats 0603 at equal distance from 0805
        chosen, _, _ = g.select_part(rows_of(ROWS[4:6]), 'Device:C', '100n')
        self.assertEqual(chosen['lcsc'], 'C900011')  # same package: higher quantity wins

    def test_no_matching_value_is_refused_not_guessed(self):
        with self.assertRaises(g.StageFailure):
            g.select_part(rows_of(ROWS), 'Device:C', '100nF')

    def test_catalog_is_the_committed_blob_never_working_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = HEADER + '\n'.join(ROWS) + '\n'
            checkout = git_catalog(Path(tmp), body)
            path, raw, source = g.capture_catalog(checkout)
            self.assertEqual((raw.decode(), source['sha256']), (body, g.sha256_bytes(raw)))
            self.assertEqual(len(source['commit']), 40)
            self.assertFalse(source['working_tree_differs'])
            (checkout / g.CATALOG_PATH).write_text(HEADER + ROWS[0] + '\n')
            path, raw, source = g.capture_catalog(checkout)
            self.assertEqual(raw.decode(), body)
            self.assertTrue(source['working_tree_differs'])
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(g.StageFailure):
            g.capture_catalog(tmp)  # not a git checkout: no provenance

    def test_not_assembled_rule_in_manifest(self):
        if not (g.STOCK / 'footprints/Resistor_SMD.pretty').is_dir():
            require_toolchain(native=True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkout = git_catalog(root / 'fome', HEADER + '\n'.join(ROWS) + '\n')
            stage = g.Stage(root / 'run', 2, 'parts')
            stage.start()
            state = {'params': g.parse_spec(SPEC.read_text())}
            g.stage_parts(stage, state, {'catalog_checkout': checkout})
            manifest = json.loads(state['parts'].read_text())
            self.assertEqual(manifest['schema'], 'agent-ee.rung1-parts-manifest/0-experimental')
            missing = {'Datasheet': None, 'datasheet_obligation': 'missing:catalog_has_no_datasheet_column'}
            self.assertEqual(manifest['parts']['J1'], {
                'Footprint': 'Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical',
                'JLCPCB_Assembly': 'not_assembled', **missing})
            selection = manifest['selections']['R1']
            self.assertEqual(selection['selection_id'], g.sha256_bytes(g.canonical({
                'catalog_sha256': manifest['catalog']['sha256'], 'catalog_line': 2, 'lib_id': 'Device:R',
                'value': '10k', 'reference': 'R1'})))
            self.assertEqual(len({s['selection_id'] for s in manifest['selections'].values()}), 3)
            self.assertEqual(manifest['assembly_exclusions'], [{'ref': 'J1', 'rule': 'JLCPCB_Assembly=not_assembled',
                                                                'reason': 'through-hole header, hand-soldered'}])
            self.assertEqual(manifest['parts']['R1'], {'Footprint': 'Resistor_SMD:R_0603_1608Metric', 'LCSC': 'C900001',
                                                       **missing})
            self.assertNotIn('MPN', manifest['parts']['R1'])
            # The capture phase runs in stage 2 and keeps every URL-less part as an explicit missing record.
            capture = json.loads((stage.dir / 'datasheets' / 'manifest.json').read_text())
            self.assertEqual((capture['schema'], capture['outcome']), ('agent-ee.datasheet-capture/1', 'missing'))
            self.assertEqual([(p['id'], p['url'], p['missing'], p['fetched_at'], p['sha256'], p['reason'])
                              for p in capture['parts']],
                             [(ref, None, True, None, None, 'no datasheet URL') for ref in ('C1', 'J1', 'R1')])
            self.assertEqual(sorted(p.name for p in (stage.dir / 'datasheets').iterdir()), ['manifest.json'])
            details = stage.receipt['details']['datasheets']
            self.assertEqual((details['manifest'], details['outcome'], details['captured']),
                             ('stages/2-parts/datasheets/manifest.json', 'missing', {}))
            self.assertEqual(details['missing'], dict.fromkeys(('C1', 'J1', 'R1'), 'no datasheet URL'))
            self.assertIn('stages/2-parts/datasheets/manifest.json', [o['path'] for o in stage.receipt['outputs']])
            self.assertTrue(any('datasheet_facts' in item for item in stage.receipt['limitations']))
            self.assertEqual(state['datasheets'], stage.dir / 'datasheets')
            self.assertEqual((stage.dir / 'catalog' / 'BOM-fome-mc.csv').read_text(), HEADER + '\n'.join(ROWS) + '\n')
            # A header with a catalog row cannot be silently excluded.
            shutil.rmtree(root / 'fome')
            checkout = git_catalog(root / 'fome', HEADER + '\n'.join(ROWS + ['Conn_01x03,J9,X:Y,,1']) + '\n')
            stage = g.Stage(root / 'run2', 2, 'parts')
            stage.start()
            with self.assertRaises(g.StageFailure):
                g.stage_parts(stage, {'params': state['params']}, {'catalog_checkout': checkout})



class ScopeTest(unittest.TestCase):
    def test_compiled_scope_points_at_generated_project_with_spec_parameters(self):
        template = json.loads(g.SCOPE_TEMPLATE.read_text())
        scope = g.compile_scope(template, g.parse_spec(SPEC.read_text()), 'rung1_rc')
        self.assertEqual(scope['schema'], 'agent-ee.scope/2')
        self.assertEqual([c['id'] for c in scope['checks']], ['erc', 'drc', 'netlist', 'geometry', 'rc-step'])
        self.assertIn({'path': '../design', 'at': 'project'}, scope['inputs'])
        self.assertIn({'path': '../requirements', 'at': 'requirements'}, scope['inputs'])
        self.assertEqual((scope['schematic'], scope['board']), ('project/rung1_rc.kicad_sch', 'project/rung1_rc.kicad_pcb'))
        step = scope['checks'][4]['params']
        self.assertEqual((step['input_net'], step['output_net'], step['ground_net'], step['step_v'], step['stop_ms']),
                         ('/IN', '/OUT', 'GND', 1.0, 5.0))
        self.assertEqual(step['measurements'][0], {'name': 'vout_1ms', 'at_ms': 1.0, 'min': 0.62, 'max': 0.65})
        mapped = {c for r in scope['baseline']['requirements'] for c in r['checks']}
        self.assertEqual(mapped, {c['id'] for c in scope['checks']})
        self.assertEqual([r['id'] for r in scope['baseline']['requirements']], ['R-native', 'R-width', 'R-response'])
        self.assertEqual(template, json.loads(g.SCOPE_TEMPLATE.read_text()))  # template untouched

    def test_requirement_files_carry_locators(self):
        files = g.requirement_files(g.parse_spec(SPEC.read_text()), '0' * 64)
        for locator in ('R-native:', 'R-width:', 'R-response:'):
            self.assertIn(locator, files['system.md'])
        self.assertIn('0.6321 V', files['targets.md'])


FAKE_AGENT_EE = r"""
import json, sys
from pathlib import Path
command, args = sys.argv[1], sys.argv[2:]
if command == 'campaign':
    out = Path(args[args.index('--out') + 1])
    run = out / 'runs' / '1'
    (run / 'inputs' / 'project').mkdir(parents=True)
    (out / 'state.json').write_text(json.dumps({'state': STATE, 'diagnostic': {'state': STATE}}))
    (run / 'result.json').write_text(json.dumps({'state': 'complete', 'outcome': STATE,
        'checks': [{'id': 'erc', 'execution': 'ok', 'outcome': 'pass', 'summary': 'zero violations'}],
        'findings': [{'id': 'physical/PHY-1', 'severity': 'minor', 'assessment': 'concern',
                      'disposition': 'noted', 'disposition_reason': 'kept verbatim'}]}))
    print(STATE)
    raise SystemExit(0 if STATE == 'criteria_satisfied' else 1)
if command == 'verify-campaign':
    print(json.loads((Path(args[0]) / 'state.json').read_text())['state'])
"""


class CampaignStageTest(unittest.TestCase):
    """Stage 6 parsing against a scripted agent-ee; no inference."""

    def run_stage(self, verdict):
        tmp = tempfile.TemporaryDirectory(prefix='agent-ee-generate-')
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        fake = root / 'fake_agent_ee.py'
        fake.write_text(FAKE_AGENT_EE.replace('STATE', repr(verdict)))
        scope = root / 'run' / 'scope' / 'rung1.scope.json'
        scope.parent.mkdir(parents=True)
        scope.write_text(json.dumps({'schematic': 'project/rung1_rc.kicad_sch', 'board': 'project/rung1_rc.kicad_pcb'}))
        profile = ROOT / 'profiles/claude-baseline.json'
        stage = g.Stage(root / 'run', 6, 'review')
        stage.start()
        state = {'scope': scope, 'name': 'rung1_rc'}
        with mock.patch.object(g, 'agent_ee_argv', lambda *args: [g.sys.executable, str(fake), *map(str, args)]):
            try:
                status = g.stage_review(stage, state, {'profile': str(profile), 'max_seconds': 2400})
                error = None
            except g.StageFailure as exc:
                status, error = 'failed', str(exc)
        return stage, state, status, error

    def test_satisfied_campaign_is_ok_and_findings_are_verbatim(self):
        stage, state, status, error = self.run_stage('criteria_satisfied')
        self.assertEqual((status, error), (None, None))
        details = stage.receipt['details']
        self.assertEqual((details['verdict'], details['verify_exit']), ('criteria_satisfied', 0))
        self.assertIn('criteria_satisfied', details['verify_stdout'])
        self.assertEqual(details['runs'][0]['findings'][0]['disposition_reason'], 'kept verbatim')
        self.assertEqual(state['reviewed_inputs'], stage.dir / 'campaign' / 'runs' / '1' / 'inputs')
        config = json.loads((stage.dir / 'campaign.json').read_text())
        self.assertEqual((config['max_iterations'], config['max_seconds']), (3, 2400))
        self.assertEqual(config['implementation']['model'], config['review_profile']['adjudicator']['model'])

    def test_escalated_campaign_fails_the_stage(self):
        stage, state, status, error = self.run_stage('escalated')
        self.assertEqual(status, 'failed')
        self.assertIn('escalated', error)
        self.assertEqual(state['verdict'], 'escalated')


class FabRewriteTest(unittest.TestCase):
    def test_cpl_uses_jlcpcb_columns_and_drops_not_assembled(self):
        kicad = ('Ref,Val,Package,PosX,PosY,Rot,Side\n"C1","100n","C_0603",110.0,100.0,90.0,top\n'
                 '"J1","Conn","PinHeader",102.5,112.5,0.0,top\n"R1","10k","R_0402",105.0,100.0,180.0,bottom\n')
        text, kept, dropped = g.jlcpcb_cpl(kicad, {'J1'})
        self.assertEqual(list(csv.reader(io.StringIO(text))), [
            ['Designator', 'Mid X', 'Mid Y', 'Layer', 'Rotation'],
            ['C1', '110.0mm', '100.0mm', 'Top', '90.0'], ['R1', '105.0mm', '100.0mm', 'Bottom', '180.0']])
        self.assertEqual((kept, dropped), (['C1', 'R1'], ['J1']))

    def test_bom_drops_excluded_refs_and_requires_lcsc_for_the_rest(self):
        kicad = ('"Comment","Designator","Footprint","LCSC Part #"\n"10k","R1","R_0402","C25744"\n'
                 '"Conn_01x03","J1","PinHeader",""\n')
        text, kept, dropped = g.jlcpcb_bom(kicad, {'J1'})
        self.assertEqual((kept, dropped), (['R1'], ['J1']))
        self.assertNotIn('J1', text)
        with self.assertRaises(g.StageFailure):
            g.jlcpcb_bom(kicad, set())

    def test_copper_layers_from_board_layer_block(self):
        board = '(kicad_pcb\n\t(layers\n\t\t(0 "F.Cu" signal)\n\t\t(2 "B.Cu" signal)\n\t\t(25 "Edge.Cuts" user)\n\t)\n)'
        self.assertEqual(g.copper_layers(board), ['F.Cu', 'B.Cu'])


class IntegrationTest(unittest.TestCase):
    """Stages 1-5 and 7 on the real toolchain; stage 6 runs checks only (no inference)."""

    def test_skip_review_run(self):
        require_toolchain(native=True)
        # Stage 5 runs `kicad-route` from PATH: the pinned checkout's launcher.
        route = require_kicad_route()
        path = os.pathsep.join([str(route / 'bin'), os.environ.get('PATH', '')])
        with tempfile.TemporaryDirectory(prefix='agent-ee-rung1-') as tmp, \
                mock.patch.dict(os.environ, {'PATH': path}), mock.patch.dict(g._TOOLS, clear=True):
            catalog = git_catalog(Path(tmp) / 'catalog', HEADER + '\n'.join(ROWS) + '\n')
            run = Path(tmp) / 'run'
            record = g.generate(SPEC, run, skip_review=True, catalog_checkout=catalog)
            statuses = {s['name']: s['status'] for s in record['stages']}
            self.assertEqual(statuses, {'requirements': 'ok', 'parts': 'ok', 'schematic': 'ok', 'placement': 'ok',
                                        'routing': 'ok', 'review': 'not_reached:review_skipped', 'fab': 'ok'},
                             [s['error'] for s in record['stages']])
            routing = json.loads((run / record['stages'][4]['receipt']).read_text())
            self.assertEqual(routing['tools']['kicad-route']['resolved'], str(route / 'bin/kicad-route'))
            self.assertFalse(record['done'])
            self.assertTrue(record['fab_exports_exist'])
            for item in record['stages']:
                g.validate_receipt(json.loads((run / item['receipt']).read_text()))
            fab = json.loads((run / 'stages/7-fab/fab-manifest.json').read_text())
            self.assertEqual(fab['excluded_from_bom'], ['J1'])
            self.assertEqual(sorted(fab['cpl_designators']), ['C1', 'R1'])


if __name__ == '__main__':
    unittest.main()
