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

class NativeEnvelopeTests(unittest.TestCase):
    """Production Ctx/executor/probe, without claiming unregistered scope/3 capture."""
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
        from agent_ee.checks import Ctx
        from agent_ee.envelope_check import check_board_envelope
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
        check_board_envelope(ctx)
        self.assertEqual((inputs / 'board.kicad_pcb').read_bytes(), pcb)
        self.assertEqual((inputs / 'envelope.json').read_bytes(), raw)
        return ctx

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


if __name__ == '__main__':
    unittest.main()
