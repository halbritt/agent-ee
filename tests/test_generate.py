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
from agent_ee.storage import digest
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
            # stage_fab always records the sealed CPL rotation status; only an allowed one is done.
            state['fab_status'] = status
        status = 'ok'
        functions = {name: (lambda *a: None) for _, name in g.STAGES} | {'review': review, 'fab': fab}
        self.assertTrue(self.run_with(functions)['done'])
        shutil.rmtree(self.root / 'run')
        for status in ('PREVIEW_REQUIRED', None, 'pass'):
            record = self.run_with(functions)
            self.assertFalse(record['done'], status)
            self.assertTrue(record['fab_exports_exist'])
            self.assertIsNotNone(record['fab_rotation_blocker'])
            shutil.rmtree(self.root / 'run')
        status = 'ok'
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

    def test_read_catalog_four_column(self):
        _, raw_e7, source_e7 = g.capture_catalog(rev='e7f5ae3')
        _, raw_c5, source_c5 = g.capture_catalog(rev='c582b29')
        self.assertTrue(source_e7['checkout_head'].startswith('e7f5ae3'))
        self.assertTrue(source_c5['checkout_head'].startswith('c582b29'))
        rows_e7, schema_e7, cols_e7 = g.read_catalog_with_schema(raw_e7)
        rows_c5, schema_c5, cols_c5 = g.read_catalog_with_schema(raw_c5)
        self.assertEqual((schema_e7, cols_e7), ('four_column', g.CATALOG_COLUMNS_4))
        self.assertEqual((schema_c5, cols_c5), ('five_column', g.CATALOG_COLUMNS))
        self.assertEqual(g.read_catalog(raw_e7), rows_e7)
        for row in rows_e7:
            self.assertIn(':', row['Footprint'])
            self.assertEqual(row['Quantity'], str(len([d.strip() for d in row['Designator'].split(',') if d.strip()])))
            self.assertEqual(g.catalog_lcsc(row), row['LCSC'])
            self.assertEqual(row['LCSC Part #'], row['LCSC'])
        for value in ('10k', '1', '12k', '68k', '100k', '470k', '2.7k'):
            chosen_e7, _, default_e7 = g.select_part(rows_e7, 'Device:R', value)
            chosen_c5, _, default_c5 = g.select_part(rows_c5, 'Device:R', value)
            self.assertEqual(default_e7, default_c5)
            for key in ('footprint', 'lcsc', 'quantity', 'package', 'rank_key'):
                self.assertEqual(chosen_e7[key], chosen_c5[key], (value, key))
        for value in ('4.7u', '10u', '1n', '2.2u', '3.3n', '330p', '33n', '33p', '470n', '47p', '680p'):
            chosen_e7, _, default_e7 = g.select_part(rows_e7, 'Device:C', value)
            chosen_c5, _, default_c5 = g.select_part(rows_c5, 'Device:C', value)
            self.assertEqual(default_e7, default_c5)
            for key in ('footprint', 'lcsc', 'quantity', 'package', 'rank_key'):
                self.assertEqual(chosen_e7[key], chosen_c5[key], (value, key))
        chosen_c_e7, rejected_c_e7, default_c_e7 = g.select_part(rows_e7, 'Device:C', '100n')
        chosen_c_c5, rejected_c_c5, default_c_c5 = g.select_part(rows_c5, 'Device:C', '100n')
        self.assertEqual((chosen_c_e7['footprint'], chosen_c_e7['package'], default_c_e7),
                         (chosen_c_c5['footprint'], chosen_c_c5['package'], default_c_c5))
        # Converting c582b29 rows to 4-column library-less format yields identical R and C selections.
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=g.CATALOG_COLUMNS_4, lineterminator='\n')
        writer.writeheader()
        for row in csv.DictReader(io.StringIO(raw_c5.decode('utf-8'))):
            writer.writerow({
                'Comment': row['Comment'],
                'Designator': row['Designator'],
                'Footprint': row['Footprint'].partition(':')[2],
                'LCSC': row['LCSC Part #'],
            })
        rows_c5_as_4 = g.read_catalog(buf.getvalue().encode('utf-8'))
        self.assertEqual(g.select_part(rows_c5_as_4, 'Device:R', '10k'),
                         g.select_part(rows_c5, 'Device:R', '10k'))
        self.assertEqual(g.select_part(rows_c5_as_4, 'Device:C', '100n'),
                         g.select_part(rows_c5, 'Device:C', '100n'))
        if (g.STOCK / 'footprints/Resistor_SMD.pretty').is_dir():
            with tempfile.TemporaryDirectory() as tmp:
                stage = g.Stage(Path(tmp) / 'run', 2, 'parts')
                stage.start()
                state = {'params': g.parse_spec(SPEC.read_text())}
                g.stage_parts(stage, state, {'catalog_checkout': g.CATALOG_CHECKOUT})
                self.assertEqual(stage.receipt['details']['catalog']['header_schema'], 'four_column')
                self.assertEqual(stage.receipt['details']['catalog']['columns'], g.CATALOG_COLUMNS_4)

    def test_read_catalog_unknown_header_refused(self):
        for bad_header in (
            'Comment,Designator,Footprint,MPN',
            'Comment,Designator,Footprint,LCSC,Quantity',
            'Reference,Value,Footprint,LCSC',
        ):
            raw = (bad_header + '\n10k,R1,R_0402_1005Metric,C25744\n').encode('utf-8')
            with self.subTest(header=bad_header), self.assertRaises(g.StageFailure) as ctx:
                g.read_catalog(raw)
            self.assertIn(bad_header, str(ctx.exception))

    def test_library_less_footprint_unmapped_refused(self):
        header_4 = ','.join(g.CATALOG_COLUMNS_4) + '\n'
        raw = (header_4 + '10k,R1,R_0402_UnmappedCustomMetric,C25744\n').encode('utf-8')
        with self.assertRaises(g.StageFailure) as ctx:
            g.read_catalog(raw)
        self.assertIn('R_0402_UnmappedCustomMetric', str(ctx.exception))
        raw_known = (header_4 + '10k,R1,R_0402_1005Metric,C25744\n').encode('utf-8')
        with self.assertRaises(g.StageFailure) as ctx2:
            g.read_catalog(raw_known, footprint_map={})
        self.assertIn('R_0402_1005Metric', str(ctx2.exception))


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


class FabStageEndToEndTest(unittest.TestCase):
    """Stage 7 through generate() on the owned CPL fixture (fixtures/cpl-rotation): the real
    receipted export, fab_export and cpl_rotation checks and the corrected package. The real
    RC parts have no owned supplier captures, so the RC run above stops at the CPL policy."""

    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        try:
            from test_fab_stage import candidate
        except ImportError:  # run as tests.test_generate from the repository root
            from tests.test_fab_stage import candidate
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-generate-fab-')
        cls.root = Path(cls.tmp.name)
        cls.inputs = candidate(cls.root / 'inputs')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def generate(self, name, policy_dir):
        def prepare(stage, state, options):
            state.update(name='cpl', reviewed_inputs=self.inputs, verdict='criteria_satisfied',
                         cpl_inputs=policy_dir)
        functions = {stage: (lambda *args: None) for _, stage in g.STAGES}
        functions.update(requirements=prepare, fab=g.stage_fab)
        return g.generate(SPEC, self.root / name, skip_review=True, functions=functions)

    def test_fab_stage_exports_a_complete_corrected_package(self):
        record = self.generate('run', self.inputs / 'generation/cpl')
        fab = record['stages'][-1]
        self.assertEqual((fab['name'], fab['status']), ('fab', 'PREVIEW_REQUIRED'), fab['error'])
        self.assertTrue(record['fab_exports_exist'])
        self.assertEqual(record['fab_status'], 'PREVIEW_REQUIRED')
        self.assertFalse(record['done'])
        self.assertEqual(record['fab_rotation_blocker'],
                         'fab: cpl rotation PREVIEW_REQUIRED (bottom-side transform unvalidated)')
        run = self.root / 'run'
        for item in record['stages']:
            g.validate_receipt(json.loads((run / item['receipt']).read_text()))
        stage = run / 'stages/7-fab'
        manifest = json.loads((stage / 'fab-manifest.json').read_text())
        listed = {f['path'] for f in manifest['files']}
        self.assertLessEqual({'gerbers.zip', 'bom.csv', 'cpl.csv', 'cpl-rotation.json'}, listed)
        self.assertTrue(any(p.startswith('drill/') and p.endswith('-PTH.drl') for p in listed), listed)
        self.assertTrue(any(p.startswith('drill/') and p.endswith('-NPTH.drl') for p in listed), listed)
        for entry in manifest['files']:
            self.assertGreater((stage / 'package' / entry['path']).stat().st_size, 0, entry)
        with (stage / 'package/cpl.csv').open() as stream:
            rows = {r['Designator']: r for r in csv.DictReader(stream)}
        self.assertEqual(sorted(rows), sorted(manifest['cpl_designators']))
        self.assertLessEqual(set(manifest['bom_designators']), set(rows))
        self.assertEqual(rows['U1']['Rotation'], '0.000000')
        self.assertEqual(manifest['rotation']['policy']['sha256'],
                         digest(self.inputs / 'generation/cpl/jlc-cpl-policy.json'))
        receipt = json.loads((stage / 'receipt.json').read_text())
        self.assertEqual(receipt['details']['rotation'], manifest['rotation'])

    def test_fab_stage_refuses_a_policy_other_than_the_stage_one_capture(self):
        other = self.root / 'other-capture'
        shutil.copytree(self.inputs / 'generation/cpl', other)
        policy = json.loads((other / 'jlc-cpl-policy.json').read_text())
        policy['policy_id'] = 'another-policy'
        (other / 'jlc-cpl-policy.json').write_text(json.dumps(policy))
        record = self.generate('mismatch', other)
        fab = record['stages'][-1]
        self.assertEqual(fab['status'], 'failed')
        self.assertIn('policy other than the one stage 1 captured', fab['error'])
        self.assertFalse(record['done'])


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
                                        'routing': 'ok', 'review': 'not_reached:review_skipped', 'fab': 'failed'},
                             [s['error'] for s in record['stages']])
            routing = json.loads((run / record['stages'][4]['receipt']).read_text())
            self.assertEqual(routing['tools']['kicad-route']['resolved'], str(route / 'bin/kicad-route'))
            self.assertFalse(record['done'])
            self.assertFalse(record['fab_exports_exist'])
            for item in record['stages']:
                g.validate_receipt(json.loads((run / item['receipt']).read_text()))
            console = (run / 'stages/7-fab/export-fab/run/console.log').read_text()
            self.assertIn('CPL rotation policy is not captured', console)


if __name__ == '__main__':
    unittest.main()
