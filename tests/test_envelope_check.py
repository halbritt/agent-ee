"""Exact native-envelope field coverage and failure boundaries."""
import json
import unittest
from pathlib import Path

from agent_ee.board_envelope import validate, derive, obligation_coverage
from agent_ee.envelope_check import compare_saved, saved_structure

ROOT = Path(__file__).resolve().parents[1]
RAW = (ROOT / 'fixtures/board-envelope/rc.envelope.json').read_bytes()
PCB = (ROOT / 'fixtures/rc/control/rc_filter.kicad_pcb').read_bytes()


class SavedEnvelopeTests(unittest.TestCase):
    def test_exact_outline_and_layers(self):
        record = validate(RAW)
        facts = saved_structure(PCB)
        self.assertEqual(compare_saved('outline/1', record, facts)['status'], 'pass')
        self.assertEqual(compare_saved('layers/1', record, facts)['status'], 'pass')

    def test_one_exact_field_per_witness_and_ounce_seam(self):
        artifacts = derive(RAW, source_path='requirements/envelope.json')
        record, requirements = json.loads(artifacts['envelope-validation.json']), json.loads(artifacts['envelope-requirements.json'])
        checks, mapped = [], []
        for item in requirements['requirements']:
            field = item['source']['locator']
            if field not in ('/outline', '/layer_count', '/stackup'):
                continue
            criterion = {'/outline': 'outline/1', '/layer_count': 'layers/1', '/stackup': 'stackup/1'}[field]
            cid = criterion.split('/')[0]
            checks.append({'id': cid, 'kind': 'board_envelope',
                           'params': {'criterion': criterion, 'envelope': 'requirements/envelope.json'}})
            mapped.append({k: item[k] for k in ('id', 'statement', 'source', 'intent')} | {'checks': [cid]})
        scope = {'schema': 'agent-ee.scope/3', 'checks': checks, 'baseline': {'requirements': mapped}}
        files = {'requirements/envelope.json': record['envelope_sha256']}
        report = obligation_coverage(scope, requirements, record, files)
        self.assertEqual([r['status'] for r in report[:3]], ['bound', 'bound', 'unknown'])
        self.assertIn('copper_weight_oz', report[2]['reason'])
        checks[1]['params']['criterion'] = 'outline/1'
        with self.assertRaisesRegex(ValueError, 'exact criterion'):
            obligation_coverage(scope, requirements, record, files)

class ExactBoundaryTests(unittest.TestCase):
    def compare(self, criterion, pcb):
        return compare_saved(criterion, validate(RAW), saved_structure(pcb))

    def test_outline_extra_duplicate_reversed_and_unsupported(self):
        extra = b'(gr_line (start 0 0) (end 1 1) (layer "Edge.Cuts"))'
        cases = [(PCB[:-2] + extra + b')', 'defect'),
                 (PCB.replace(b'(end 116 108)', b'(end 116 107)'), 'defect'),
                 (PCB.replace(b'(start 96 96)', b'(start 116 108)').replace(
                     b'(end 116 108)', b'(end 96 96)'), 'pass'),
                 (PCB[:-2] + b'(footprint "extra" (layer "F.Cu") (at 0 0) '
                  b'(property "Reference" "X1") (fp_line (start 0 0) (end 1 1) '
                  b'(layer "Edge.Cuts"))) )', 'unknown'),
                 (PCB[:-2] + b'(gr_arc (start 0 0) (mid 1 1) (end 2 0) '
                  b'(layer "Edge.Cuts")))', 'unknown')]
        for pcb, status in cases:
            with self.subTest(status=status, tail=pcb[-90:]):
                self.assertEqual(self.compare('outline/1', pcb)['status'], status)

    def test_malformed_reordered_duplicate_and_four_layer_table(self):
        for before, after, status in [
                (b'(2 "B.Cu" signal)', b'(4 "In1.Cu" signal) (6 "In2.Cu" signal) (2 "B.Cu" signal)', 'defect'),
                (b'(2 "B.Cu" signal)', b'(0 "B.Cu" signal)', 'unknown'),
                (b'(2 "B.Cu" signal)', b'(2 "F.Cu" signal)', 'unknown'),
                (b'(0 "F.Cu" signal)', b'(0 "F.Cu" signal) (4 "In2.Cu" signal) (6 "In1.Cu" signal)', 'unknown'),
                (b'(0 "F.Cu" signal)', b'(0 "F.Cu" bogus)', 'unknown')]:
            with self.subTest(after=after):
                self.assertEqual(PCB.count(before), 1)
                self.assertEqual(self.compare('layers/1', PCB.replace(before, after))['status'], status)

    def test_stackup_saved_structure_never_infers_ounces(self):
        pcb = (ROOT / 'tests/fixtures/envelope/rc-explicit-stackup.kicad_pcb').read_bytes()
        raw = json.loads(RAW)
        raw['stackup']['layers'][1]['name'] = 'dielectric 1'
        record = validate(json.dumps(raw).encode())
        control = compare_saved('stackup/1', record, saved_structure(pcb))
        self.assertEqual(control['status'], 'unknown')
        self.assertEqual(control['defects'], [])
        self.assertEqual(control['saved'], {'thickness_mm': '1.6', 'layers': [
            {'name': 'F.Cu', 'kind': 'copper', 'thickness_mm': '0.035'},
            {'name': 'dielectric 1', 'kind': 'dielectric', 'thickness_mm': '1.53'},
            {'name': 'B.Cu', 'kind': 'copper', 'thickness_mm': '0.035'}]})
        for before, after in [(b'(thickness 1.6)', b'(thickness 1.600001)'),
                              (b'(thickness 0.035)', b'(thickness 0.035001)'),
                              (b'(layer "F.Cu"', b'(layer "In1.Cu"'),
                              (b'(type "core")', b'(type "copper")')]:
            with self.subTest(after=after):
                result = compare_saved('stackup/1', record, saved_structure(pcb.replace(before, after, 1)))
                self.assertEqual(result['status'], 'unknown')
                self.assertTrue(result['defects'])
                self.assertTrue(result['unknown'])
        self.assertIn('missing', compare_saved('stackup/1', record, saved_structure(PCB))['unknown'][0])

    def test_parameters_are_closed(self):
        from agent_ee.envelope_check import parameters
        for params in ({}, {'criterion': [], 'envelope': 'e.json'},
                       {'criterion': 'layers/1', 'envelope': '../e.json'},
                       {'criterion': 'layers/1', 'envelope': 'e.json', 'pass_all': True}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                parameters(params)


class EnvelopeRegistrationTests(unittest.TestCase):
    def describe(self, criterion, *, params=None, files=None, board='board.kicad_pcb'):
        from agent_ee.capabilities import describe_check
        spec = {'id': 'mechanics', 'kind': 'board_envelope', 'params': params if params is not None else
                {'criterion': criterion, 'envelope': 'envelope.json'}}
        return describe_check(spec, check_specs=[spec], scope={'board': board},
                              files=files if files is not None else {'board.kicad_pcb': 'board', 'envelope.json': 'envelope'})

    def test_outline_and_layers_have_distinct_executable_capabilities(self):
        for criterion in ('outline/1', 'layers/1'):
            with self.subTest(criterion=criterion):
                descriptor = self.describe(criterion)
                self.assertEqual(descriptor['status'], 'supported')
                self.assertTrue(descriptor['can_evaluate_bound'])
                self.assertEqual(descriptor['predicates'], ['envelope:' + criterion])
                self.assertEqual(descriptor['immutable_inputs'], ['envelope.json'])
                self.assertEqual(descriptor['prerequisites'], [])

    def test_stackup_cannot_supply_acceptance_coverage(self):
        descriptor = self.describe('stackup/1')
        self.assertEqual(descriptor['status'], 'unsupported')
        self.assertFalse(descriptor['can_evaluate_bound'])
        self.assertEqual(descriptor['predicates'], [])
        self.assertIn('copper_weight_oz', str(descriptor['unsupported_reasons']))

    def test_launch_expectation_requires_native_pcbnew(self):
        from agent_ee.preflight import capture_expectation
        result = {'run_id': 'owned-native-expectation', 'policy_files': {}, 'runtime_files': {}}
        manifest = {'candidate_id': 'owned-native-envelope', 'files': {},
                    'scope': {'schema': 'agent-ee.scope/1', 'checks': [
                        {'id': 'outline', 'kind': 'board_envelope',
                         'params': {'criterion': 'outline/1', 'envelope': 'envelope.json'}}]}}
        expected = capture_expectation(result, manifest, [])
        self.assertEqual(expected['required_runtime'], ['/usr/bin/python3', 'bwrap', 'pcbnew'])

    def test_capability_refuses_missing_inputs_and_invalid_contracts(self):
        cases = [{'files': {'envelope.json': 'hash'}}, {'files': {'board.kicad_pcb': 'hash'}},
                 {'board': None}, {'params': {}},
                 {'params': {'criterion': 'holes/1', 'envelope': 'envelope.json'}},
                 {'params': {'criterion': 'layers/1', 'envelope': '../envelope.json'}},
                 {'params': {'criterion': 'layers/1', 'envelope': 'envelope.json', 'allow_missing': True}}]
        for case in cases:
            with self.subTest(case=case):
                descriptor = self.describe('layers/1', **case)
                self.assertEqual(descriptor['status'], 'unsupported')
                self.assertFalse(descriptor['can_evaluate_bound'])
                self.assertTrue(descriptor['unsupported_reasons'])


class NativeEnvelopeTests(unittest.TestCase):
    """Registered coordinator execution; complete scope/3 capture still needs stackup authority."""
    def setUp(self):
        import tempfile
        from toolchain import require_toolchain
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.count = 0

    def run_native(self, criterion, pcb=PCB, raw=RAW, deadline=None):
        import time
        from agent_ee.checks import Ctx, CHECK_FUNCS
        from agent_ee.storage import digest
        self.count += 1
        run = self.root / str(self.count)
        inputs = run / 'inputs'
        inputs.mkdir(parents=True)
        (inputs / 'board.kicad_pcb').write_bytes(pcb)
        (inputs / 'envelope.json').write_bytes(raw)
        manifest = {'candidate_id': 'owned-native-envelope-boundary',
                    'scope': {'board': 'board.kicad_pcb'},
                    'files': {p.name: digest(p) for p in inputs.iterdir()}}
        spec = {'id': 'envelope', 'kind': 'board_envelope',
                'params': {'criterion': criterion, 'envelope': 'envelope.json'}}
        ctx = Ctx(run, manifest, spec, deadline if deadline is not None else time.monotonic() + 60)
        self.last_ctx = ctx
        CHECK_FUNCS[spec['kind']](ctx)
        self.assertEqual((inputs / 'board.kicad_pcb').read_bytes(), pcb)
        self.assertEqual((inputs / 'envelope.json').read_bytes(), raw)
        return ctx

    def test_registered_dispatcher_retains_native_result_and_unknown_stackup(self):
        import time
        from agent_ee.checks import run_checks
        from agent_ee.storage import digest, read_json, write_json
        inputs = self.root / 'inputs'
        inputs.mkdir()
        (inputs / 'board.kicad_pcb').write_bytes(PCB)
        (inputs / 'envelope.json').write_bytes(RAW)
        checks = [{'id': criterion.split('/')[0], 'kind': 'board_envelope',
                   'params': {'criterion': criterion, 'envelope': 'envelope.json'}}
                  for criterion in ('outline/1', 'layers/1', 'stackup/1')]
        write_json(self.root / 'candidate.json', {'candidate_id': 'owned-dispatch-envelope',
                   'scope': {'board': 'board.kicad_pcb', 'checks': checks},
                   'files': {p.name: digest(p) for p in inputs.iterdir()}})
        results = run_checks(self.root, time.monotonic() + 60)
        self.assertEqual([(r['execution'], r['outcome']) for r in results],
                         [('ok', 'pass'), ('ok', 'pass'), ('unsupported', 'unknown')])
        for result in results:
            self.assertEqual(read_json(self.root / 'checks' / (result['id'] + '.json')), result)
            self.assertIn('work/' + result['id'] + '/envelope-judgment.json', result['evidence'])
            self.assertEqual(result['details']['board_sha256'], digest(inputs / 'board.kicad_pcb'))
        self.assertIn('copper_weight_oz', results[-1]['summary'])

    def test_actual_native_outline_and_layers_controls(self):
        for criterion in ('outline/1', 'layers/1'):
            with self.subTest(criterion=criterion):
                ctx = self.run_native(criterion)
                self.assertEqual((ctx.result['execution'], ctx.result['outcome']), ('ok', 'pass'))
                self.assertEqual(ctx.result['details']['pcbnew_version'], '10.0.7')
                self.assertIn('work/envelope/envelope-probe.json', ctx.result['evidence'])
                self.assertEqual(ctx.result['details']['board_sha256'], ctx.manifest['files']['board.kicad_pcb'])
                self.assertEqual(ctx.result['details']['envelope']['sha256'], ctx.manifest['files']['envelope.json'])
                self.assertEqual(json.loads((ctx.work / 'execution.json').read_text())['exit_code'], 0)

    def test_native_exact_outline_defect_and_unsupported_arc(self):
        from agent_ee.checks import CheckError
        # Move the shared corner on both incident segments, preserving a closed contour.
        changed = PCB.replace(b'116 108', b'116.000001 108')
        ctx = self.run_native('outline/1', changed)
        self.assertEqual(ctx.result['outcome'], 'defect')
        with self.assertRaises(CheckError) as raised:
            self.run_native('outline/1', PCB[:-2] + b'(gr_arc (start 96 96) (mid 95 97) '
                            b'(end 96 98) (stroke (width 0.05) (type default)) (layer "Edge.Cuts")))')
        self.assertEqual(raised.exception.execution, 'unsupported')
        self.assertEqual(self.last_ctx.result['outcome'], 'unknown')

    def test_native_explicit_stackup_proves_facts_but_never_ounces(self):
        from agent_ee.checks import CheckError
        pcb = (ROOT / 'tests/fixtures/envelope/rc-explicit-stackup.kicad_pcb').read_bytes()
        raw = json.loads(RAW)
        raw['stackup']['layers'][1]['name'] = 'dielectric 1'
        with self.assertRaises(CheckError) as raised:
            self.run_native('stackup/1', pcb, json.dumps(raw).encode())
        self.assertEqual(raised.exception.execution, 'unsupported')
        ctx = self.last_ctx
        result = json.loads((ctx.work / 'envelope-judgment.json').read_text())
        native = json.loads((ctx.work / 'envelope-probe.json').read_text())
        self.assertIs(native['has_stackup'], True)
        self.assertEqual(native['thickness_nm'], 1600000)
        self.assertEqual(result['saved']['layers'][1],
                         {'name': 'dielectric 1', 'kind': 'dielectric', 'thickness_mm': '1.53'})
        self.assertEqual(result['defects'], [])
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(len(result['unknown']), 1)
        self.assertIn('copper_weight_oz', result['unknown'][0])
        self.assertEqual(ctx.result['outcome'], 'unknown')

    def test_native_tool_failure_and_exhausted_deadline_do_not_pass(self):
        from agent_ee.checks import CheckError
        for pcb, deadline, execution in [(PCB.replace(b'(version 20260206)', b'(version 99999999)'), None, 'failed'),
                                          (PCB, 0, 'interrupted')]:
            with self.subTest(execution=execution), self.assertRaises(CheckError) as raised:
                self.run_native('layers/1', pcb, deadline=deadline)
            self.assertEqual(raised.exception.execution, execution)
            self.assertEqual(self.last_ctx.result['outcome'], 'unknown')


class PodEnvelopeAcceptanceTests(unittest.TestCase):
    """Real capture and sandboxed native checks on an original scaffold with two connectors."""
    def setUp(self):
        import shutil
        import tempfile
        from toolchain import require_toolchain
        from agent_ee.board_envelope import source_inventory
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-envelope-profile-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        shutil.copytree(ROOT / 'fixtures/telemetry-pod', self.source)
        self.envelope = json.loads((self.source / 'envelope.json').read_text())
        # This is a scoped contract fixture, not acceptance of the complete pod.
        self.envelope['obligations'] = []
        self.envelope['fixed_footprints'] = [dict(reference=ref, side='front', edge='left',
            position_mm=pos, inset_mm=0, rotation_deg=0, insertion='from_top', tolerance_mm=0)
            for ref, pos in (('AMPSEAL', 10), ('SMA', 30))]
        self.count = 0
        self.save_envelope()
        record = validate((self.source / 'envelope.json').read_bytes(),
                          profile=(self.source / 'fab-profile.json').read_bytes(),
                          captured=source_inventory(self.source))
        from agent_ee.scaffold import board
        raw = board(record, 'owned-ee161-contract')
        footprints = ''.join(f'(footprint "Owned:Connector" (layer "F.Cu") (at 0 {50-pos}) '
                            f'(property "Reference" "{ref}"))'
                            for ref, pos in (('AMPSEAL', 10), ('SMA', 30)))
        (self.source / 'board.kicad_pcb').write_bytes(raw.rstrip()[:-1] + footprints.encode() + b')\n')
        shutil.copyfile(ROOT / 'fixtures/rc/control/rc_filter.kicad_sch', self.source / 'board.kicad_sch')
        shutil.copyfile(ROOT / 'fixtures/rc/control/rc_filter.kicad_pro', self.source / 'board.kicad_pro')
        (self.source / 'targets.md').write_text('Owned envelope contract targets.\n')
        (self.source / 'reference.md').write_text('Owned envelope contract reference.\n')

    def save_envelope(self):
        (self.source / 'envelope.json').write_text(json.dumps(self.envelope, indent=2) + '\n')

    def save_profile(self, profile):
        from agent_ee.storage import digest
        (self.source / 'fab-profile.json').write_text(json.dumps(profile, indent=2) + '\n')
        self.envelope['fab_profile']['sha256'] = digest(self.source / 'fab-profile.json')
        self.save_envelope()

    def scope(self):
        from agent_ee.board_envelope import derive_requirements, source_inventory
        record = validate((self.source / 'envelope.json').read_bytes(),
                          profile=(self.source / 'fab-profile.json').read_bytes(),
                          captured=source_inventory(self.source))
        requirements = derive_requirements(record, 'envelope.json')['requirements']
        checks = [{'id': 'drc', 'kind': 'drc'},
                  {'id': 'fab', 'kind': 'fab_profile', 'params': {'fab_profile': 'fab-profile.json'}}]
        checks += [dict(id=field.replace('_', '-'), kind='board_envelope',
                        params=dict(criterion=criterion, envelope='envelope.json'))
                   for field, criterion in (('outline', 'outline/1'), ('layer_count', 'layers/1'),
                                            ('stackup', 'stackup/1'))]
        checks += [dict(id=ref.lower(), kind='placement', params=dict(criterion='connector-edge/1',
                   reference=ref, drc_check='drc', envelope='envelope.json')) for ref in ('AMPSEAL', 'SMA')]
        for r in requirements:
            field = r['source']['locator'].split('/')[1]
            cid = ('fab' if field == 'fab_profile' else r['id'].rsplit('-', 1)[1].lower()
                   if field == 'fixed_footprints' else field.replace('_', '-') if field in ('outline', 'layer_count', 'stackup')
                   else 'drc')
            r['checks'] = [cid]
            del r['check_kinds']
            del r['field_sha256']
        requirements.append(dict(id='native', statement='Native DRC runs.', intent='acceptance',
                                 source=dict(path='targets.md', locator='line 1'), checks=['drc']))
        return dict(schema='agent-ee.scope/3', name='owned-ee161', scope='Envelope acceptance contract',
                    inputs=[dict(path=str(p), at=p.relative_to(self.source).as_posix())
                            for p in sorted(self.source.rglob('*')) if p.is_file()],
                    schematic='board.kicad_sch', board='board.kicad_pcb', checks=checks,
                    envelope=dict(path='envelope.json', profile='fab-profile.json', dialog=None),
                    baseline=dict(requirements=requirements, targets=['targets.md'],
                                  reference_architectures=['reference.md']))

    def capture(self, scope=None):
        from agent_ee.candidate import capture
        from agent_ee.storage import write_json
        self.count += 1
        run = self.root / f'run-{self.count}'
        run.mkdir()
        write_json(self.root / 'scope.json', scope if scope is not None else self.scope())
        manifest = capture(self.root / 'scope.json', run)
        return run, manifest

    def stackup_result(self):
        import time
        from agent_ee.checks import Ctx, check_board_envelope
        run, manifest = self.capture()
        spec = next(c for c in manifest['scope']['checks'] if c['id'] == 'stackup')
        ctx = Ctx(run, manifest, spec, time.monotonic() + 60)
        check_board_envelope(ctx)
        return ctx.result

    def test_stackup_bound_to_profile(self):
        result = self.stackup_result()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'))
        self.assertEqual(result['details']['fab_profile']['stackup']['execution'], 'ready')
        self.assertEqual(result['details']['saved']['layers'][1]['thickness_mm'], '0.2104')
        self.envelope['stackup']['layers'][0]['copper_weight_oz'] = 2
        self.save_envelope()
        result = self.stackup_result()
        self.assertEqual(result['outcome'], 'defect')
        self.assertEqual(result['details']['defects'],
                         ['envelope layer F.Cu copper_weight_oz differs from profile'])

    def test_saved_layer_outside_profile_tolerance_is_native_defect(self):
        board = self.source / 'board.kicad_pcb'
        board.write_bytes(board.read_bytes().replace(b'(thickness 0.035)',
                                                     b'(thickness 0.035001)', 1))
        result = self.stackup_result()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'))
        self.assertEqual(result['details']['defects'],
                         ['saved layer F.Cu thickness exceeds profile tolerance'])
        self.assertEqual(result['details']['unknown'], [])

    def test_unverified_stackup_source_stays_unknown_at_native_execution(self):
        import shutil
        import time
        from agent_ee.checks import CheckError, Ctx, check_board_envelope
        from agent_ee.storage import digest, read_json
        profile = json.loads((self.source / 'fab-profile.json').read_text())
        profile['stackup']['source']['quote'] = 'absent authority quote'
        self.save_profile(profile)
        with self.assertRaisesRegex(ValueError, 'copper_weight_oz'):
            self.capture()
        # Exercise runtime refusal independently of the capture gate, using real
        # captured bytes and the native sandbox, without an acceptance baseline.
        run = self.root / 'runtime-unknown'
        inputs = run / 'inputs'
        shutil.copytree(self.source, inputs)
        manifest = dict(candidate_id='owned-ee161-unknown-authority',
                        scope=dict(board='board.kicad_pcb', envelope=dict(
                            path='envelope.json', profile='fab-profile.json', dialog=None)),
                        files={p.relative_to(inputs).as_posix(): digest(p)
                               for p in inputs.rglob('*') if p.is_file()})
        spec = dict(id='stackup', kind='board_envelope',
                    params=dict(criterion='stackup/1', envelope='envelope.json'))
        ctx = Ctx(run, manifest, spec, time.monotonic() + 60)
        with self.assertRaises(CheckError) as raised:
            check_board_envelope(ctx)
        self.assertEqual(raised.exception.execution, 'unsupported')
        judgment = read_json(ctx.work / 'envelope-judgment.json')
        self.assertEqual(judgment['status'], 'unknown')
        self.assertEqual(judgment['fab_profile']['stackup']['execution'], 'unsupported')
        self.assertIn('exact source quote absent', judgment['fab_profile']['stackup']['reason'])
        self.assertTrue(read_json(ctx.work / 'envelope-probe.json')['has_stackup'])

    def test_stackup_without_profile_unknown(self):
        from agent_ee.envelope_check import captured_record
        from agent_ee.board_envelope import source_inventory
        scope = self.scope()
        scope['envelope']['profile'] = None
        files, inputs = source_inventory(self.source)
        record, _ = captured_record(scope, dict(envelope='envelope.json'), files, inputs)
        result = compare_saved('stackup/1', record, saved_structure((self.source / 'board.kicad_pcb').read_bytes()))
        self.assertEqual(result['status'], 'unknown')
        with self.assertRaisesRegex(ValueError, 'declare its captured path'):
            self.capture(scope)
        for field, value in (('quote', 'absent quote'), ('sha256', '0' * 64),
                             ('page', 2), ('as_of', '2026-10-17')):
            with self.subTest(field=field):
                profile = json.loads((ROOT / 'fixtures/telemetry-pod/fab-profile.json').read_text())
                profile['stackup']['source'][field] = value
                self.save_profile(profile)
                files, inputs = source_inventory(self.source)
                record, _ = captured_record(self.scope(), dict(envelope='envelope.json'), files, inputs)
                self.assertEqual(compare_saved('stackup/1', record,
                    saved_structure((self.source / 'board.kicad_pcb').read_bytes()))['status'], 'unknown')
                with self.assertRaisesRegex(ValueError, 'copper_weight_oz'):
                    self.capture()

    def test_two_fixed_footprints_each_graded(self):
        import time
        from agent_ee.checks import run_checks
        board = self.source / 'board.kicad_pcb'
        board.write_bytes(board.read_bytes().replace(b'(at 0 20)', b'(at 1 20)'))
        run, _ = self.capture()
        results = {r['id']: r for r in run_checks(run, time.monotonic() + 120)}
        for cid in ('outline', 'layer-count', 'stackup', 'ampseal'):
            self.assertEqual((results[cid]['execution'], results[cid]['outcome']),
                             ('ok', 'pass'), results[cid])
        self.assertEqual((results['sma']['execution'], results['sma']['outcome']), ('ok', 'defect'))
        self.assertEqual([d['reference'] for d in results['sma']['details']['defects']], ['SMA'])
        self.assertEqual([d['reference'] for d in results['ampseal']['details']['connectors']], ['AMPSEAL'])
        # A missing SMA never makes the AMPSEAL witness unknown.
        board.write_bytes(board.read_bytes().replace(b'(property "Reference" "SMA")',
                                                     b'(property "Reference" "OTHER")'))
        run, _ = self.capture()
        results = {r['id']: r for r in run_checks(run, time.monotonic() + 120)}
        self.assertEqual(results['ampseal']['outcome'], 'pass')
        self.assertEqual((results['sma']['execution'], results['sma']['outcome']), ('unsupported', 'unknown'))
        self.assertEqual([d['reference'] for d in results['sma']['details']['unknown']], ['SMA'])

    def test_capture_lists_each_bound_obligation_and_refuses_holes_keepouts(self):
        from agent_ee.storage import read_json
        run, manifest = self.capture()
        baseline = read_json(run / 'baseline.json')
        self.assertEqual(len(baseline['envelope']['coverage']), 6)
        self.assertEqual({c['status'] for c in baseline['envelope']['coverage']}, {'bound'})
        self.assertEqual(baseline['coverage']['checks']['ampseal']['predicates'],
                         ['placement:connector-edge/1:AMPSEAL'])
        for path in ('envelope.json', 'fab-profile.json', 'sources/jlcpcb-stackup.txt',
                     'sources/jlcpcb-thickness.txt'):
            self.assertEqual(baseline['files'][path], manifest['files'][path])
        self.envelope['mounting_holes'] = [dict(id='H1', at=[5, 5], drill_mm=3, plated=False)]
        self.save_envelope()
        with self.assertRaisesRegex(ValueError, 'R-envelope-telemetry-pod-hole-H1'):
            self.capture()
        self.envelope['mounting_holes'] = []
        self.envelope['keepouts'] = [dict(id='antenna', polygon=[[1, 1], [2, 1], [2, 2], [1, 2]],
                                        layers=['F.Cu'], forbid=['footprints'])]
        self.save_envelope()
        with self.assertRaisesRegex(ValueError, 'R-envelope-telemetry-pod-keepout-antenna'):
            self.capture()

    def test_profile_thickness_boundary_and_copper_identity_are_exact(self):
        from agent_ee.board_envelope import source_inventory
        from agent_ee.envelope_check import captured_record
        profile = json.loads((self.source / 'fab-profile.json').read_text())
        profile['stackup']['layer_thickness_tolerance_mm'] = 0.000001
        self.save_profile(profile)
        files, inputs = source_inventory(self.source)
        record, _ = captured_record(self.scope(), dict(envelope='envelope.json'), files, inputs)
        pcb = (self.source / 'board.kicad_pcb').read_bytes()
        for thickness, status in ((b'0.035001', 'pass'), (b'0.034999', 'pass'),
                                  (b'0.035002', 'defect'), (b'0.034998', 'defect')):
            with self.subTest(thickness=thickness):
                facts = saved_structure(pcb.replace(b'(thickness 0.035)', b'(thickness ' + thickness + b')', 1))
                self.assertEqual(compare_saved('stackup/1', record, facts)['status'], status)
        facts = saved_structure(pcb.replace(b'(layer "F.Cu"', b'(layer "In3.Cu"', 1))
        self.assertEqual(compare_saved('stackup/1', record, facts)['status'], 'defect')
        facts = saved_structure(pcb.replace(b'(thickness 0.035)', b'(thickness nope)', 1))
        self.assertEqual(compare_saved('stackup/1', record, facts)['status'], 'unknown')

    def test_connector_scope_rejects_cross_reference_shared_or_omitted_selectors(self):
        scope = self.scope()
        for c in scope['checks']:
            if c['id'] == 'sma':
                c['params']['reference'] = 'AMPSEAL'
        with self.assertRaisesRegex(ValueError, 'does not cover SMA'):
            self.capture(scope)
        scope = self.scope()
        for c in scope['checks']:
            if c['id'] in ('sma', 'ampseal'):
                del c['params']['reference']
        with self.assertRaisesRegex(ValueError, 'explicit reference'):
            self.capture(scope)
        scope = self.scope()
        for r in scope['baseline']['requirements']:
            if r['id'].endswith('fixed-SMA'):
                r['checks'] = ['ampseal']
        scope['checks'] = [c for c in scope['checks'] if c['id'] != 'sma']
        with self.assertRaisesRegex(ValueError, 'also mapped by'):
            self.capture(scope)


if __name__ == '__main__':
    unittest.main()
