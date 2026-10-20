"""Generation done gate (AGENTEE-94): fab_package check, capture refusal and campaign artifact hashes.

Native cases run the real fab_export producer, consistency gate and evidence seal
on the owned `fixtures/fab-export` board. Fault cases damage the producer's
staging between export and gate, as the AGENTEE-73 tests do, so every tool run is
real. The campaign case scripts reviewer responses only; checks, capture, the
evidence seal, convergence and verify-campaign are production code.
"""
import contextlib
import copy
import csv
import io
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agent_ee import cli, executor, fab_export, fab_package
from agent_ee import rung1_campaign as rc
from agent_ee.baseline import compile_baseline, freeze_evidence, load_baseline, verify_evidence
from agent_ee.campaign import fab_artifacts, run_campaign, verify_campaign
from agent_ee.candidate import capture
from agent_ee.capabilities import describe_check
from agent_ee.checks import run_checks
from agent_ee.fab_package import (PREVIEW_ACTION, artifact_record, preview_obligations, rotation_blocker,
                                   rotation_record)
from agent_ee.storage import digest, read_json, write_json
try:
    from toolchain import require_toolchain
except ImportError:  # run as tests.test_fab_package from the repository root
    from tests.toolchain import require_toolchain

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / 'fixtures/fab-export'
CPL_FIXTURE = REPO / 'fixtures/cpl-rotation'
MARKER = {'schema': 'agent-ee.generation/1', 'done_check': 'fab'}
NO_OBLIGATIONS = 'fab: cpl rotation PREVIEW_REQUIRED: no preview obligations recorded'


def cpl_obligations(path):
    """Every row of a corrected CPL file as a (designator, side) obligation, naturally sorted."""
    with Path(path).open() as stream:
        rows = list(csv.DictReader(stream))
    return [{'designator': r['Designator'], 'side': r['Layer']}
            for r in sorted(rows, key=lambda r: fab_export.natural_key(r['Designator']))]


def generation_scope(schema='agent-ee.scope/2'):
    """The owned fab-export control as a generation scope ending with the done gate."""
    scope = read_json(FIXTURE / 'control.scope.json')
    scope['schema'] = schema
    scope['inputs'][0]['path'] = str(FIXTURE)
    scope['checks'] = [{'id': 'netlist', 'kind': 'netlist'}, {'id': 'geometry', 'kind': 'geometry'},
                       {'id': 'fab', 'kind': 'fab_package', 'params': {'netlist_check': 'netlist'}}]
    scope['generation'] = dict(MARKER)
    req = scope['baseline']['requirements'][0]
    req['checks'] = ['fab']
    native = {**req, 'id': 'I-native', 'statement': 'Retain native board and schematic observations',
              'checks': ['netlist', 'geometry']}
    scope['baseline']['requirements'].append(native)
    if schema == 'agent-ee.scope/3':
        req['intent'] = 'acceptance'
        native['intent'] = 'informational'
    return scope


def rotation_scope(schema='agent-ee.scope/2'):
    """The owned CPL control as a generation scope: fab-export, cpl_rotation, then the done gate."""
    scope = read_json(CPL_FIXTURE / 'control.scope.json')
    scope['schema'] = schema
    scope['inputs'][0]['path'] = str(CPL_FIXTURE)
    for spec in scope['checks']:
        if spec['id'] == 'fab':
            spec['id'] = 'fab-export'
        if spec['kind'] == 'cpl_rotation':
            spec['params']['export_check'] = 'fab-export'
    scope['checks'].append({'id': 'fab', 'kind': 'fab_package', 'params': {'netlist_check': 'netlist'}})
    scope['generation'] = dict(MARKER)
    req = scope['baseline']['requirements'][0]
    req['checks'] = ['netlist', 'fab-export', 'geometry', 'cpl', 'fab']
    if schema == 'agent-ee.scope/3':
        req['intent'] = 'acceptance'
    return scope


def captured_files(scope):
    return {f"design/{name}": digest(FIXTURE / name) for name in scope['inputs'][0]['include']}


class GenerationMarkerTests(unittest.TestCase):
    """Baseline compilation (capture) refuses a generation scope without its done gate."""

    def compile(self, scope):
        return compile_baseline(scope, captured_files(scope))

    def test_generation_scope_binds_its_marker_into_the_baseline(self):
        self.assertEqual(self.compile(generation_scope())['generation'], MARKER)
        plain = generation_scope()
        del plain['generation']
        self.assertNotIn('generation', self.compile(plain))
        self.assertNotEqual(self.compile(plain)['baseline_id'], self.compile(generation_scope())['baseline_id'])

    def test_removing_the_fab_package_requirement_is_refused(self):
        scope = generation_scope()
        scope['checks'].pop()
        scope['baseline']['requirements'] = scope['baseline']['requirements'][1:]
        with self.assertRaisesRegex(ValueError, 'fab_package done check last'):
            self.compile(scope)
        scope = generation_scope()
        scope['baseline']['requirements'][1]['checks'].append('fab')
        scope['baseline']['requirements'] = scope['baseline']['requirements'][1:]
        self.compile(scope)  # still mapped through another requirement
        scope = generation_scope()
        scope['baseline']['requirements'] = scope['baseline']['requirements'][1:]
        with self.assertRaisesRegex(ValueError, 'trace to a baseline requirement'):
            self.compile(scope)

    def test_scope3_informational_mapping_is_not_an_acceptance_requirement(self):
        scope = generation_scope('agent-ee.scope/3')
        scope['baseline']['requirements'][0]['intent'] = 'informational'
        with self.assertRaisesRegex(ValueError, 'acceptance requirement mapped to fab_package'):
            self.compile(scope)

    def test_done_check_must_be_last_and_named_by_a_well_formed_marker(self):
        scope = generation_scope()
        scope['checks'].insert(0, scope['checks'].pop())
        with self.assertRaisesRegex(ValueError, 'done check last'):
            self.compile(scope)
        for marker in ({'schema': 'agent-ee.generation/1'}, {**MARKER, 'extra': 1},
                       {**MARKER, 'schema': 'agent-ee.generation/0'}, {**MARKER, 'done_check': 'netlist'}, True):
            with self.subTest(marker=marker):
                scope = generation_scope()
                scope['generation'] = marker
                with self.assertRaises(ValueError):
                    self.compile(scope)
        scope = generation_scope()
        scope['checks'][-1]['kind'] = 'fab_export'
        with self.assertRaisesRegex(ValueError, 'done check last'):
            self.compile(scope)
        scope = generation_scope()
        scope['schema'] = 'agent-ee.scope/1'
        with self.assertRaisesRegex(ValueError, 'generation scope requires'):
            self.compile(scope)

    def test_capture_refuses_a_generation_scope_without_the_done_gate(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-fab-package-capture-') as tmp:
            root = Path(tmp)
            scope = generation_scope()
            scope['checks'].pop()
            scope['baseline']['requirements'] = scope['baseline']['requirements'][1:]
            write_json(root / 'scope.json', scope)
            (root / 'run').mkdir()
            with self.assertRaisesRegex(ValueError, 'fab_package done check last'):
                capture(root / 'scope.json', root / 'run')
            self.assertFalse((root / 'run' / 'baseline.json').exists())

    def test_capability_requires_last_position_netlist_and_geometry(self):
        scope = generation_scope('agent-ee.scope/3')
        files = captured_files(scope)

        def describe(specs):
            return describe_check(specs[-1] if specs[-1]['kind'] == 'fab_package' else
                                  next(s for s in specs if s['kind'] == 'fab_package'),
                                  check_specs=specs, files=files, scope={**scope, 'checks': specs})
        good = describe(scope['checks'])
        self.assertEqual(good['status'], 'supported', good)
        self.assertIn('fab-package:sealed-artifacts', good['predicates'])
        self.assertIn('fab-package:designator-parity', good['predicates'])
        self.assertIn('fab-export:jlcpcb-package/1', good['predicates'])
        netlist, geometry, fab = scope['checks']
        for name, specs in (('not last', [netlist, fab, geometry]), ('no geometry', [netlist, fab]),
                            ('no netlist', [geometry, fab]),
                            ('extra param', [netlist, geometry, {**fab, 'params': {'netlist_check': 'netlist',
                                                                                 'layers': []}}])):
            with self.subTest(case=name):
                self.assertEqual(describe(specs)['status'], 'unsupported')

    def test_model_receipt_must_be_captured_and_immutable(self):
        scope = generation_scope('agent-ee.scope/3')
        receipt = 'models/.agent-ee-models.json'
        scope['checks'][-1]['params']['model_libraries'] = {'KICAD10_3DMODEL_DIR': receipt}
        files = captured_files(scope)
        def describe():
            return describe_check(scope['checks'][-1], check_specs=scope['checks'], files=files, scope=scope)
        self.assertEqual(describe()['status'], 'unsupported')
        files[receipt] = digest(FIXTURE / 'requirements.md')
        described = describe()
        self.assertEqual(described['status'], 'supported', described)
        self.assertIn(receipt, described['immutable_inputs'])
        scope['checks'][-1]['params']['model_libraries'] = {'KICAD10_3DMODEL_DIR': 'models/other.json'}
        self.assertEqual(describe()['status'], 'unsupported')

    def test_delegated_params_keep_their_original_validation(self):
        for extra in ({'layers': []}, {'assembly_exclusion': 'Value=x'}, {'bom_variant': ''},
                      {'model_libraries': {}}, {'model_libraries': {'KIPRJMOD': 'x'}},
                      {'model_libraries': {'KICAD10_3DMODEL_DIR': '../.agent-ee-models.json'}}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                fab_package.validate_params({'netlist_check': 'netlist', **extra})


class NativeRun(unittest.TestCase):
    """Shared harness: the real export, gate and seal of one captured scope per case."""

    def setUp(self):
        require_toolchain(native=True)
        tmp = tempfile.TemporaryDirectory(prefix='agent-ee-fab-package-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runs = 0

    def run_case(self, *, mutate=None, scope=None):
        self.runs += 1
        case = self.root / str(self.runs)
        case.mkdir()
        write_json(case / 'scope.json', scope or generation_scope('agent-ee.scope/3'))
        self.run_dir = case / 'run'
        self.run_dir.mkdir()
        capture(case / 'scope.json', self.run_dir)
        producer = fab_export.check_fab_export

        def damaged(ctx):
            producer(ctx)
            mutate(ctx.work / 'fab')
            # A deleted file is one the producer never wrote, so it is not cited either.
            ctx.result['evidence'] = [e for e in ctx.result['evidence'] if (ctx.run_dir / e).is_file()]
        with mock.patch.object(fab_export, 'check_fab_export', damaged if mutate else producer):
            self.results = run_checks(self.run_dir, time.monotonic() + 300)
        freeze_evidence(self.run_dir, self.results)
        verify_evidence(self.run_dir, self.results, required=True)
        return self.results[-1]

    @staticmethod
    def rehash(stage):
        manifest = read_json(stage / 'manifest.json')
        manifest['files'] = {rel: digest(stage / rel) for rel in manifest['files'] if (stage / rel).is_file()}
        write_json(stage / 'manifest.json', manifest)


class NativeFabPackageTests(NativeRun):
    """The real export, gate and seal; fault injection between the producer and the gate."""

    def test_declared_custom_copper_jumper_composes_with_native_bom_variant(self):
        from tests.test_fab_export import copper_fixture, variant_schematic
        source = self.root / 'source'
        shutil.copytree(FIXTURE, source)
        copper_fixture(source, custom=True)
        schematic = source / 'fab.kicad_sch'
        schematic.write_text(variant_schematic(schematic.read_text()))
        write_json(source / 'fab.kicad_pro', {'meta': {'version': 3},
                   'schematic': {'variants': [{'name': 'ALT'}, {'name': 'LITE'}]}})
        scope = generation_scope('agent-ee.scope/3')
        scope['inputs'][0]['path'] = str(source)
        scope['inputs'][0]['include'].append('fab.kicad_pro')
        scope['checks'][-1]['params'].update(copper_features=['JP1'], bom_variant='ALT')
        result = self.run_case(scope=scope)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertEqual(result['details']['consistency']['copper_features'], ['JP1'])
        for key in ('bom', 'cpl', 'assembled', 'native_pos'):
            self.assertNotIn('JP1', result['details']['designators'][key])

    def test_delegated_variant_exclusion_models_and_board_only_holes(self):
        from tests.test_fab_export import RULE, mark_board, mark_schematic, variant_schematic
        from tests.test_fab_consistency import stock_library, STOCK_RECEIPT
        source = self.root / 'source'
        shutil.copytree(FIXTURE, source)
        sch = source / 'fab.kicad_sch'
        sch.write_text(mark_schematic(variant_schematic(sch.read_text()), 'J1', unbind=True))
        write_json(source / 'fab.kicad_pro', {'meta': {'version': 3},
                   'schematic': {'variants': [{'name': 'ALT'}, {'name': 'LITE'}]}})
        board = source / 'fab.kicad_pcb'
        text = mark_board(board.read_text(), 'J1')
        text = text.replace('(attr smd)', '(attr smd) (model "${KICAD10_3DMODEL_DIR}/Synth.3dshapes/owned.step")', 1)
        # Original mechanical fixture: no schematic part, copper or supplier claim.
        holes = ''.join(f'''(footprint "Owned:Mount" (layer "F.Cu") (at {105+i*3} 105)
            (property "Reference" "H{i}" (at 0 0) (layer "F.Fab"))
            (attr board_only exclude_from_bom exclude_from_pos_files)
            (pad "" np_thru_hole circle (at 0 0) (size 2 2) (drill 2) (layers "*.Cu" "*.Mask")))'''
            for i in range(1, 5))
        board.write_text(text.rstrip()[:-1] + holes + ')')
        for rel, data in stock_library(['Synth.3dshapes/owned.step']).items():
            (source / rel).parent.mkdir(parents=True, exist_ok=True)
            (source / rel).write_bytes(data)
        scope = generation_scope('agent-ee.scope/3')
        scope['inputs'][0].update(path=str(source), include=[p.relative_to(source).as_posix()
                                                            for p in source.rglob('*') if p.is_file()])
        params = scope['checks'][-1]['params']
        params.update(bom_variant='ALT', assembly_exclusion=RULE,
                      model_libraries={'KICAD10_3DMODEL_DIR': 'design/' + STOCK_RECEIPT})
        result = self.run_case(scope=scope)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertEqual(result['details']['designators']['excluded'], ['J1'])
        self.assertTrue(result['details']['designators']['agree'])
        self.assertFalse(set(result['details']['designators']['cpl']) & {'J1', 'H1', 'H2', 'H3', 'H4'})
        self.assertTrue({'H1', 'H2', 'H3', 'H4'} <= set(result['details']['designators']['board_non_dnp']))
        self.assertEqual(result['details']['consistency']['designators']['parity_violations'], [])
        self.assertEqual(result['details']['consistency']['models']['records'][0]['status'], 'resolved')
        baseline = load_baseline(self.run_dir)
        self.assertIn('design/' + STOCK_RECEIPT, baseline['files'])
        self.assertIn('design/' + STOCK_RECEIPT, baseline['coverage']['checks']['fab']['immutable_inputs'])
        self.assertEqual(result['details']['bom_variant']['name'], 'ALT')
        params['bom_variant'] = 'LITE'
        refused = self.run_case(scope=scope)
        self.assertEqual((refused['execution'], refused['outcome']), ('unsupported', 'unknown'), refused)
        self.assertIn('changes the populated set', refused['summary'])
        params['bom_variant'] = 'ALT'
        params.pop('assembly_exclusion')
        refused = self.run_case(scope=scope)
        self.assertEqual((refused['execution'], refused['outcome']), ('unsupported', 'unknown'), refused)
        self.assertIn('no matching assembly_exclusion', refused['summary'])

    def test_passing_package_seals_and_records_its_artifacts(self):
        result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        artifacts = result['details']['artifacts']
        self.assertEqual(artifacts, artifact_record(self.run_dir, result))
        self.assertEqual(artifacts['candidate_id'], read_json(self.run_dir / 'candidate.json')['candidate_id'])
        names = sorted(Path(p).name for p in artifacts['files'])
        self.assertEqual(names, ['bom.csv', 'cpl.csv', 'fab-NPTH-drl_map.gbr', 'fab-NPTH.drl',
                                 'fab-PTH-drl_map.gbr', 'fab-PTH.drl', 'gerbers.zip', 'manifest.json'])
        seal = read_json(self.run_dir / 'work/verification/evidence.json')['checks']['fab']['artifacts']
        for path, sha in artifacts['files'].items():
            self.assertEqual(seal[path], sha)
            self.assertEqual(digest(self.run_dir / path), sha)
        self.assertEqual(result['details']['consistency']['designators']['parity_violations'], [])
        self.assertTrue((self.run_dir / 'work/fab/fab-consistency.json').is_file())
        predicates = load_baseline(self.run_dir)['coverage']['checks']['fab']['predicates']
        self.assertIn('fab-package:done-gate', predicates)
        self.assertFalse(any(t.startswith('pass means the exports exist') for t in result['limitations']))

        # Without a declared cpl_rotation check the native CPL is sealed and nothing blocks done.
        self.assertEqual(artifacts['rotation'], {'status': 'not_declared'})
        self.assertIn('work/fab/fab/cpl.csv', artifacts['files'])
        self.assertIsNone(rotation_blocker(artifacts['rotation']['status']))

        bom = self.run_dir / 'work/fab/fab/bom.csv'
        bom.write_bytes(bom.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'inventory mismatch'):
            artifact_record(self.run_dir, result)
        with self.assertRaisesRegex(ValueError, 'did not pass'):
            artifact_record(self.run_dir, {**result, 'outcome': 'defect'})

    def test_missing_empty_or_unparseable_export_file_is_never_a_pass(self):
        def removed(name, rehash=True):
            def mutate(stage):
                (stage / name).unlink()
                if rehash:
                    self.rehash(stage)
            return mutate

        def replaced(name, data):
            def mutate(stage):
                (stage / name).write_bytes(data)
                self.rehash(stage)
            return mutate

        def drill(stage):
            next((stage / 'drill').glob('*-PTH.drl')).unlink()
            self.rehash(stage)
        cases = {
            'missing bom': removed('bom.csv'),
            'missing cpl unlisted': removed('cpl.csv', rehash=False),
            'missing gerbers.zip': removed('gerbers.zip'),
            'missing manifest': removed('manifest.json', rehash=False),
            'missing PTH drill': drill,
            'empty cpl': replaced('cpl.csv', b''),
            'empty gerbers.zip': replaced('gerbers.zip', b''),
            'unparseable gerbers.zip': replaced('gerbers.zip', b'not a zip archive'),
            'unparseable bom': replaced('bom.csv', b'"broken'),
            'unparseable summary': replaced('pcb_summary.json', b'{'),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                result = self.run_case(mutate=mutate)
                self.assertNotEqual(result['outcome'], 'pass', result)
                self.assertIn(result['outcome'], ('unknown', 'defect'))
                if result['outcome'] == 'unknown':
                    self.assertNotEqual(result['execution'], 'ok')
                self.assertNotIn('artifacts', result['details'])
                with self.assertRaises(ValueError):
                    artifact_record(self.run_dir, result)

    def test_consistency_defect_is_a_done_gate_defect(self):
        def drop_cpl_row(stage):
            lines = (stage / 'cpl.csv').read_text().splitlines(keepends=True)
            (stage / 'cpl.csv').write_text(''.join(lines[:1] + lines[2:]))
            self.rehash(stage)
        result = self.run_case(mutate=drop_cpl_row)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertIn('designator_parity', {d['code'] for d in result['details']['consistency']['defects']})
        self.assertTrue(result['summary'].startswith('fab package done gate failed'))
        self.assertNotIn('artifacts', result['details'])

    def test_empty_native_output_is_failed(self):
        native_execute = executor.execute

        def empty_output(inputs, work, argv, **kwargs):
            record = native_execute(inputs, work, argv, **kwargs)
            if argv[:4] == ['kicad-cli', 'pcb', 'export', 'drill']:
                next((Path(work) / 'out').glob('*-NPTH.drl')).write_bytes(b'')
            return record
        with mock.patch.object(executor, 'execute', side_effect=empty_output):
            result = self.run_case()
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'), result)
        self.assertFalse((self.run_dir / 'work/fab/fab/manifest.json').exists())

    def test_not_last_is_unsupported_at_run_time(self):
        scope = generation_scope()
        del scope['generation']
        scope['checks'].append({'id': 'tail', 'kind': 'netlist'})
        scope['baseline']['requirements'][1]['checks'].append('tail')
        self.run_case(scope=scope)
        result = next(r for r in self.results if r['id'] == 'fab')
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)


class RotationCompositionTests(NativeRun):
    """AGENTEE-126: the done gate consumes a declared cpl_rotation check on the owned CPL fixture."""

    def test_done_gate_seals_the_corrected_cpl_and_records_preview_required(self):
        result = self.run_case(scope=rotation_scope('agent-ee.scope/3'))
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        artifacts = result['details']['artifacts']
        self.assertEqual(artifacts, artifact_record(self.run_dir, result))
        self.assertIn('work/cpl/cpl/cpl.csv', artifacts['files'])
        self.assertNotIn('work/fab/fab/cpl.csv', artifacts['files'])
        rotation = artifacts['rotation']
        self.assertEqual(result['details']['rotation'], rotation)
        self.assertEqual((rotation['status'], rotation['check_id']), ('PREVIEW_REQUIRED', 'cpl'))
        self.assertEqual(rotation['policy_sha256'], digest(CPL_FIXTURE / 'jlc-cpl-policy.json'))
        self.assertEqual(rotation['native_cpl_sha256'], digest(self.run_dir / 'work/fab/fab/cpl.csv'))
        self.assertEqual(digest(self.run_dir / 'work/fab-export/fab/cpl.csv'), rotation['native_cpl_sha256'])
        self.assertNotEqual(rotation['cpl_sha256'], rotation['native_cpl_sha256'])
        self.assertIn('cpl rotation PREVIEW_REQUIRED', result['summary'])
        seal = read_json(self.run_dir / 'work/verification/evidence.json')['checks']['fab']['artifacts']
        self.assertEqual(seal['work/cpl/cpl/cpl.csv'], rotation['cpl_sha256'])
        self.assertIn('fab-package:done-gate', load_baseline(self.run_dir)['coverage']['checks']['fab']['predicates'])
        # AGENTEE-151: PREVIEW_REQUIRED is done only as the recorded operator obligation, every corrected
        # CPL designator with its side, bound to the policy; the status itself is never upgraded.
        obligations = rotation['preview_obligations']
        self.assertEqual(obligations, {'status': 'PREVIEW_REQUIRED', 'policy_sha256': rotation['policy_sha256'],
                                       'action': PREVIEW_ACTION,
                                       'designators': cpl_obligations(self.run_dir / 'work/cpl/cpl/cpl.csv')})
        self.assertIn({'designator': 'JB0', 'side': 'Bottom'}, obligations['designators'])
        self.assertIn({'designator': 'U1', 'side': 'Top'}, obligations['designators'])
        self.assertIsNone(rotation_blocker(rotation['status'], obligations, rotation['policy_sha256']))
        self.assertIn(f"{len(obligations['designators'])} preview obligations", result['summary'])
        notice = next(t for t in result['limitations'] if t.startswith('operator obligation before ordering'))
        self.assertIn('JB0 (Bottom)', notice)
        self.assertEqual(rotation_blocker(rotation['status']), NO_OBLIGATIONS)
        self.assertIsNone(rotation_blocker('ok'))
        self.assertIsNone(rotation_blocker('not_declared'))
        self.assertIn("'pass' is not a done status", rotation_blocker('pass'))
        self.assertIn('None', rotation_blocker(None))
        self.assertIn('carries no preview obligations', rotation_blocker('ok', obligations, rotation['policy_sha256']))
        designators = obligations['designators']
        malformed = {
            'not bound to the sealed rotation policy': {**obligations, 'policy_sha256': '0' * 64},
            'list no designator': {**obligations, 'designators': []},
            'not a designator with its side': {**obligations, 'designators': [
                {**designators[0], 'side': 'Middle'}, *designators[1:]]},
            'not unique and sorted': {**obligations, 'designators': designators[::-1]},
            'another status or action': {**obligations, 'action': 'none'},
            'no preview obligations recorded': {**obligations, 'extra': 1}}
        for reason, bad in malformed.items():
            self.assertIn(reason, rotation_blocker('PREVIEW_REQUIRED', bad, rotation['policy_sha256']), reason)
        self.assertIn('not bound', rotation_blocker('PREVIEW_REQUIRED', obligations, None))

        # The obligations are recomputed from the sealed report and the corrected CPL; they must agree.
        report_data = read_json(self.run_dir / 'work/cpl/cpl-rotation.json')
        cpl_text = (self.run_dir / 'work/cpl/cpl/cpl.csv').read_text()
        self.assertEqual(preview_obligations('PREVIEW_REQUIRED', rotation['policy_sha256'], report_data, cpl_text),
                         obligations)
        self.assertIsNone(preview_obligations('ok', rotation['policy_sha256'], report_data, cpl_text))
        dropped = {**report_data, 'designators': report_data['designators'][1:]}
        flipped = {**report_data, 'designators': [{**report_data['designators'][0], 'layer': 'Other'},
                                                  *report_data['designators'][1:]]}
        lines = cpl_text.splitlines(keepends=True)
        for report_bad, text in ((dropped, cpl_text), (flipped, cpl_text), (report_data, ''.join(lines[:-1])),
                                 (report_data, cpl_text + lines[-1])):
            with self.assertRaises(ValueError):
                preview_obligations('PREVIEW_REQUIRED', rotation['policy_sha256'], report_bad, text)
        with self.assertRaisesRegex(ValueError, 'policy sha256'):
            preview_obligations('PREVIEW_REQUIRED', None, report_data, cpl_text)

        # The correction must be of exactly this package's native CPL, and remain as sealed.
        scope = read_json(self.run_dir / 'candidate.json')['scope']
        manifest = read_json(self.run_dir / 'work/fab/fab/manifest.json')
        other = {**manifest, 'files': {**manifest['files'], 'cpl.csv': '0' * 64}}
        with self.assertRaisesRegex(ValueError, "did not correct this package's native cpl.csv"):
            rotation_record(self.run_dir, scope, result['candidate_id'], other)
        with self.assertRaisesRegex(ValueError, "did not correct this package's native cpl.csv"):
            rotation_record(self.run_dir, scope, result['candidate_id'], {**manifest, 'board_sha256': '0' * 64})
        corrected = self.run_dir / 'work/cpl/cpl/cpl.csv'
        original = corrected.read_bytes()
        corrected.write_bytes(original + b'\n')
        with self.assertRaisesRegex(ValueError, 'corrected CPL differs from its report'):
            artifact_record(self.run_dir, result)
        corrected.write_bytes(original)
        report = self.run_dir / 'work/cpl/cpl-rotation.json'
        data = read_json(report)
        write_json(report, {**data, 'status': 'ok', 'physical_validation': True})
        with self.assertRaisesRegex(ValueError, 'report differs from its check result'):
            artifact_record(self.run_dir, result)

    def test_a_declared_correction_without_a_pass_fails_the_done_gate(self):
        scope = rotation_scope('agent-ee.scope/3')
        cpl = next(c for c in scope['checks'] if c['kind'] == 'cpl_rotation')
        cpl['params']['supplier_footprints'] = cpl['params']['supplier_footprints'][1:]
        result = self.run_case(scope=scope)
        prior = next(r for r in self.results if r['id'] == 'cpl')
        self.assertEqual((prior['execution'], prior['outcome']), ('unsupported', 'unknown'), prior['summary'])
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        self.assertIn("declared cpl_rotation 'cpl' has no passing result", result['summary'])
        self.assertNotIn('artifacts', result['details'])

    def test_capability_refuses_two_cpl_rotation_checks(self):
        scope = rotation_scope('agent-ee.scope/3')
        cpl = next(c for c in scope['checks'] if c['kind'] == 'cpl_rotation')
        scope['checks'].insert(-1, {**copy.deepcopy(cpl), 'id': 'cpl-2'})
        scope['baseline']['requirements'][0]['checks'].append('cpl-2')
        files = {f"design/{name}": digest(CPL_FIXTURE / name) for name in scope['inputs'][0]['include']}
        described = describe_check(scope['checks'][-1], check_specs=scope['checks'], files=files, scope=scope)
        self.assertEqual(described['status'], 'unsupported', described)
        self.assertIn('at most one cpl_rotation', json.dumps(described['unsupported_reasons']))
        good = rotation_scope('agent-ee.scope/3')
        described = describe_check(good['checks'][-1], check_specs=good['checks'], files=files, scope=good)
        self.assertEqual(described['status'], 'supported', described)
        self.assertIn({'check_id': 'cpl', 'kind': 'cpl_rotation'}, described['prerequisites'])


class GenerationCampaignFixture(unittest.TestCase):
    """A one-iteration generation campaign with scripted reviews; everything else is production code."""

    def setUp(self):
        require_toolchain(native=True)
        tmp = tempfile.TemporaryDirectory(prefix='agent-ee-fab-package-campaign-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        definition = {'harness': 'codex', 'model': 'test', 'effort': 'high', 'timeout_s': 10}
        profile = {'schema': 'agent-ee.profile/1', 'name': 'protocol-test',
                   'reviewers': [{**definition, 'role': 'circuit'}, {**definition, 'role': 'physical'}],
                   'adjudicator': definition}
        self.profile, self.definition = profile, definition
        self.write_campaign(generation_scope(), ['design/fab.kicad_pcb'])
        self.addCleanup(mock.patch.stopall)
        mock.patch('agent_ee.reviewers.dispatch', side_effect=self.agent).start()
        mock.patch('agent_ee.cli.runtime_files', return_value={
            name: {'path': '/usr/bin/true', 'sha256': digest('/usr/bin/true')}
            for name in ('codex', 'codex_tool_host', 'bwrap', '/usr/bin/python3', 'kicad-cli', 'pcbnew')}).start()

    def write_campaign(self, scope, mutable):
        scope.update(review_profile=self.profile, mutable_inputs=mutable)
        write_json(self.root / 'scope.json', scope)
        write_json(self.root / 'campaign.json', {
            'schema': 'agent-ee.campaign/1', 'scope': 'scope.json', 'review_profile': self.profile,
            'implementation': self.definition, 'max_iterations': 2, 'max_seconds': 600})

    def agent(self, run, label, definition, prompt, schema, timeout, *, cancellation=None, executable=None):
        packet = read_json(Path(run) / 'packet.json')
        identity = {k: packet[k] for k in ('candidate_id', 'run_id')}
        if label == 'adjudicator':
            return {'execution': 'ok'}, {'schema': 'agent-ee.decisions/2', **identity, 'decisions': []}
        return {'execution': 'ok'}, {'schema': 'agent-ee.review/2', **identity,
                                     'packet_sha256': packet['packet_sha256'], 'role': label,
                                     'coverage': ['protocol'], 'limitations': ['scripted test response'],
                                     'findings': []}

    def verify_cli(self, root):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(['verify-campaign', str(root)])
        return code, out.getvalue()

class GenerationCampaignTests(GenerationCampaignFixture):
    def test_final_state_lists_fab_hashes_that_verify_campaign_recomputes(self):
        root = self.root / 'campaign'
        with contextlib.redirect_stdout(io.StringIO()):
            status = run_campaign(self.root / 'campaign.json', root)
        state = read_json(root / 'state.json')
        self.assertEqual((status, state['state']), (0, 'criteria_satisfied'), state.get('error'))
        final = Path(state['history'][-1]['run'])
        check = read_json(final / 'checks/fab.json')
        self.assertEqual((check['execution'], check['outcome']), ('ok', 'pass'))
        self.assertEqual(state['fab_artifacts'], check['details']['artifacts'])
        self.assertEqual(state['fab_artifacts'], fab_artifacts(final))
        self.assertEqual(verify_campaign(root)['fab_artifacts'], state['fab_artifacts'])

        code, stdout = self.verify_cli(root)
        self.assertEqual(code, 0)
        recomputed = rc.recomputed_artifacts(stdout, str(final))
        self.assertEqual(recomputed['files'], state['fab_artifacts']['files'])
        self.assertTrue(rc.artifacts_agree(state['fab_artifacts'], recomputed))

        # The RC campaign record consumes exactly this verify-campaign output.
        def in_process(argv, log_dir, label, timeout, source=None):
            log_dir.mkdir(parents=True, exist_ok=True)
            code, out = self.verify_cli(argv[-1])
            (log_dir / f'{label}.stdout.log').write_text(out)
            return {'argv': [str(a) for a in argv], 'exit_code': code, 'stdout': f'{label}.stdout.log',
                    'stdout_tail': out[-2000:], 'stderr_tail': ''}
        rundir = self.root / 'rung1'
        rundir.mkdir()

        def record_for(current, scaffold=None):
            # campaign_record names RUNDIR/campaign; this campaign root stays where it ran. This campaign
            # has no rung-1 scaffold step, so the scaffold receipt binding is stubbed (default: bound).
            with mock.patch.object(rc, 'run_logged', side_effect=in_process), \
                    mock.patch.object(rc, 'campaign_summary', return_value=(current, [], [])), \
                    mock.patch.object(rc, 'first_capture_binding',
                                      return_value=scaffold or {'bound': True, 'differences': []}), \
                    mock.patch.object(rc, 'agent_ee_command', side_effect=lambda *a: ['agent-ee', str(root)]):
                return rc.campaign_record(rundir, {}, None)
        record = record_for(state)
        self.assertEqual(record['fab_artifacts'], state['fab_artifacts'])
        self.assertEqual(record['fab_artifacts_recomputed_by_verify_campaign']['files'],
                         state['fab_artifacts']['files'])
        self.assertTrue(record['fab_artifacts_verified'])
        self.assertEqual((record['fab_rotation'], record['fab_rotation_blocker']), ({'status': 'not_declared'}, None))
        self.assertTrue(record['done'])
        self.assertIn('fab_rotation not_declared policy None\n', self.verify_cli(root)[1])
        # Verified fab hashes do not make the record done when the scaffold receipt is unbound.
        unbound = record_for(state, {'bound': False, 'differences': ['project/rung1_rc.kicad_dru']})
        self.assertTrue(unbound['fab_artifacts_verified'])
        self.assertFalse(unbound['done'])

        # A changed sealed artifact or a forged state hash fails verify-campaign closed.
        cpl = final / 'work/fab/fab/cpl.csv'
        original = cpl.read_bytes()
        cpl.write_bytes(original + b'\n')
        with self.assertRaises(ValueError):
            verify_campaign(root)
        cpl.write_bytes(original)
        verify_campaign(root)
        forged = copy.deepcopy(state)
        forged['fab_artifacts']['files'][next(iter(forged['fab_artifacts']['files']))] = '0' * 64
        write_json(root / 'state.json', forged)
        with self.assertRaisesRegex(ValueError, 'fab artifact hashes differ'):
            verify_campaign(root)
        dropped = copy.deepcopy(state)
        del dropped['fab_artifacts']
        write_json(root / 'state.json', dropped)
        with self.assertRaisesRegex(ValueError, 'fab artifact hashes differ'):
            verify_campaign(root)
        record = record_for(dropped)
        self.assertEqual(record['verdict'], 'unverified:criteria_satisfied')
        self.assertFalse(record['fab_artifacts_verified'])
        self.assertFalse(record['done'])

    def in_process(self, argv, log_dir, label, timeout, source=None):
        log_dir.mkdir(parents=True, exist_ok=True)
        code, out = self.verify_cli(argv[-1])
        (log_dir / f'{label}.stdout.log').write_text(out)
        return {'argv': [str(a) for a in argv], 'exit_code': code, 'stdout': f'{label}.stdout.log',
                'stdout_tail': out[-2000:], 'stderr_tail': ''}

    def test_declared_cpl_correction_is_sealed_and_preview_required_is_done_with_obligations(self):
        self.write_campaign(rotation_scope(), ['design/cpl.kicad_pcb'])
        root = self.root / 'campaign'
        with contextlib.redirect_stdout(io.StringIO()):
            status = run_campaign(self.root / 'campaign.json', root)
        state = read_json(root / 'state.json')
        self.assertEqual((status, state['state']), (0, 'criteria_satisfied'), state.get('error'))
        final = Path(state['history'][-1]['run'])
        artifacts = state['fab_artifacts']
        self.assertEqual(artifacts, fab_artifacts(final))
        self.assertEqual(artifacts['rotation']['status'], 'PREVIEW_REQUIRED')
        self.assertEqual(artifacts['rotation']['policy_sha256'], digest(CPL_FIXTURE / 'jlc-cpl-policy.json'))
        self.assertIn('work/cpl/cpl/cpl.csv', artifacts['files'])
        self.assertNotIn('work/fab/fab/cpl.csv', artifacts['files'])
        obligations = artifacts['rotation']['preview_obligations']
        self.assertEqual(obligations['designators'], cpl_obligations(final / 'work/cpl/cpl/cpl.csv'))

        code, stdout = self.verify_cli(root)
        self.assertEqual(code, 0)
        self.assertIn(f"fab_rotation PREVIEW_REQUIRED policy {artifacts['rotation']['policy_sha256']}\n", stdout)
        self.assertIn(f"fab_preview_obligations {len(obligations['designators'])} policy "
                      f"{obligations['policy_sha256']}: {PREVIEW_ACTION}\n", stdout)
        self.assertIn('fab_preview_obligation JB0 Bottom\n', stdout)
        self.assertNotIn('fab: cpl rotation', stdout)
        recomputed = rc.recomputed_artifacts(stdout, str(final))
        self.assertEqual(recomputed['preview_obligations'], obligations)
        self.assertTrue(rc.artifacts_agree(artifacts, recomputed))
        # Obligations verify-campaign did not print (or printed differently) are not verified.
        self.assertFalse(rc.artifacts_agree(artifacts, {**recomputed, 'preview_obligations': None}))
        shorter = stdout.replace('fab_preview_obligation JB0 Bottom\n', '')
        self.assertIsNone(rc.recomputed_artifacts(shorter, str(final)))

        rundir = self.root / 'rung1'
        rundir.mkdir()

        def record_for(current):
            with mock.patch.object(rc, 'run_logged', side_effect=self.in_process), \
                    mock.patch.object(rc, 'campaign_summary', return_value=(current, [], [])), \
                    mock.patch.object(rc, 'first_capture_binding', return_value={'bound': True, 'differences': []}), \
                    mock.patch.object(rc, 'agent_ee_command', side_effect=lambda *a: ['agent-ee', str(root)]):
                return rc.campaign_record(rundir, {}, None)
        record = record_for(state)
        self.assertTrue(record['fab_artifacts_verified'])
        self.assertEqual(record['fab_rotation']['status'], 'PREVIEW_REQUIRED')
        self.assertIsNone(record['fab_rotation_blocker'])
        self.assertEqual(record['preview_obligations'], obligations)
        self.assertTrue(record['done'])
        self.assertIn('preview_obligations verify-campaign recomputed', record['done_rule'])
        # The owner decision is one constant: without PREVIEW_REQUIRED in it this record is not done.
        with mock.patch.object(fab_package, 'DONE_ROTATION_STATUSES', frozenset({'not_declared', 'ok'})):
            self.assertFalse(record_for(state)['done'])

        # Tampered obligations in the campaign state fail verify-campaign closed and are never done.
        tampered = copy.deepcopy(state)
        tampered['fab_artifacts']['rotation']['preview_obligations']['designators'].pop()
        write_json(root / 'state.json', tampered)
        with self.assertRaisesRegex(ValueError, 'fab artifact hashes differ'):
            verify_campaign(root)
        record = record_for(tampered)
        self.assertEqual((record['verdict'], record['done']), ('unverified:criteria_satisfied', False))
        self.assertIsNone(record['preview_obligations'])
        write_json(root / 'state.json', state)
        verify_campaign(root)

        # The sealed corrected CPL is a done-gate artifact: changing it fails verify-campaign closed.
        corrected = final / 'work/cpl/cpl/cpl.csv'
        original = corrected.read_bytes()
        corrected.write_bytes(original + b'\n')
        with self.assertRaises(ValueError):
            verify_campaign(root)
        corrected.write_bytes(original)
        forged = copy.deepcopy(state)
        forged['fab_artifacts']['rotation']['status'] = 'ok'
        write_json(root / 'state.json', forged)
        with self.assertRaisesRegex(ValueError, 'fab artifact hashes differ'):
            verify_campaign(root)



class DeclaredFabCampaignTests(GenerationCampaignFixture):
    """D/F1/G1: a routed candidate must request, run and grade its declared fab turn."""

    def agent(self, run, label, definition, prompt, schema, timeout, **kwargs):
        if label == 'implementation':
            return {'execution': 'ok'}, {'summary': 'Coordinator owns fab; leave the design unchanged.'}
        return super().agent(run, label, definition, prompt, schema, timeout, **kwargs)

    def test_d_f1_g1_reach_done_through_a_fab_turn_on_unchanged_board_bytes(self):
        from tests.test_fab_stage import declared_fab_inputs, declared_fab_scope
        from tests.toolchain import require_kicad_route
        from agent_ee import fab_stage, rung1_qualified as q
        from agent_ee.stage_receipts import validate_receipt
        checkout = require_kicad_route()
        inputs = declared_fab_inputs(self.root / 'inputs')
        scope = declared_fab_scope(inputs)
        campaign_step = next(s for s in rc.step_specs(rc.PACKAGE_ROOT, rc.STEPS_DIR, checkout) if s['stage'] == 'fab')
        qualified_step = next(s for s in q.step_specs(checkout, fab=True) if s['stage'] == 'fab')
        for shape, step in [('D', fab_stage.step(rc.PACKAGE_ROOT, scope['board'], scope['schematic'])),
                            ('F1', campaign_step), ('G1', qualified_step)]:
            with self.subTest(shape=shape):
                self.write_campaign(scope, [scope['board']])
                config = read_json(self.root / 'campaign.json')
                config['implementation_steps'] = [step]
                write_json(self.root / 'campaign.json', config)
                root = self.root / shape
                with contextlib.redirect_stdout(io.StringIO()):
                    status = run_campaign(self.root / 'campaign.json', root)
                state = read_json(root / 'state.json')
                self.assertEqual((status, state['state']), (0, 'criteria_satisfied'), state)
                self.assertEqual(len(state['history']), 2)
                first, final = [Path(h['run']) for h in state['history']]
                self.assertEqual(state['history'][0]['earliest_failing_stage'], 'fab')
                self.assertEqual(state['history'][0]['outcome'], 'incomplete')
                self.assertEqual(state['history'][0]['design_id'], state['history'][1]['design_id'])
                for check in read_json(first / 'result.json')['checks'][2:]:
                    self.assertEqual((check['execution'], check['outcome']), ('not_run', 'unknown'))
                checks = read_json(final / 'result.json')['checks']
                self.assertTrue(all(c['execution'] == 'ok' and c['outcome'] in ('pass', 'info') for c in checks), checks)
                producer = state['stage_done']['paths'][scope['board']]['producer']
                self.assertEqual((producer['stage'], producer['step']), ('fab', 'export-fab'))
                receipt_dir = root / 'implementations/1/receipts/1-export-fab'
                self.assertEqual(validate_receipt(receipt_dir)['changed'], [])
                self.assertEqual(state['fab_artifacts']['rotation']['status'], 'PREVIEW_REQUIRED')
                self.assertEqual(verify_campaign(root)['state'], 'criteria_satisfied')
                retained = final / 'work/fab/fab-step.json'
                original = retained.read_bytes()
                retained.write_bytes(original + b'\n')
                with self.assertRaises(ValueError):
                    verify_campaign(root)
                retained.write_bytes(original)
                verify_campaign(root)


if __name__ == '__main__':
    unittest.main()
