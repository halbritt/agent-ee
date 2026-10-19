"""AGENTEE-71: owned hierarchical designs through native networkless rendering."""
import copy
import importlib.util
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee.baseline import compile_baseline, evidence_seal
from agent_ee.candidate import capture
from agent_ee.capabilities import describe_check
from agent_ee.checks import run_checks
from agent_ee.findings import validate_review
from agent_ee.preflight import capture_expectation
from agent_ee.review_run import structured_call
from agent_ee.reviewers import build_packet
from agent_ee.schematic_render import (GRID_MM, PIXELS_PER_MM, compare, grid, parameters,
                                       png_bytes, read_ppm)
from agent_ee.storage import digest, identity, read_json, write_json
from tests.toolchain import require_toolchain

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('owned_lint_fixture', REPO / 'fixtures/schematic-lint/generate.py')
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


class ContractTests(unittest.TestCase):
    def test_registration_is_informational_and_baseline_is_immutable(self):
        check = {'id': 'visual', 'kind': 'schematic_render', 'params': {'baseline': 'visual-baseline'}}
        files = dict.fromkeys(['design.kicad_sch', 'req', 'target', 'ref',
                               'visual-baseline/manifest.json', 'visual-baseline/a.ppm',
                               'visual-baseline/a.png'], 'a' * 64)
        scope = {'schema': 'agent-ee.scope/2', 'schematic': 'design.kicad_sch', 'checks': [check],
                 'baseline': {'requirements': [{'id': 'R1', 'statement': 'Review image',
                      'source': {'path': 'req', 'locator': 'R1'}, 'checks': ['visual']}],
                      'targets': ['target'], 'reference_architectures': ['ref']}}
        descriptor = describe_check(check, check_specs=[check], files=files, scope=scope)
        self.assertEqual(descriptor['status'], 'supported', descriptor)
        self.assertFalse(descriptor['can_evaluate_bound'])
        self.assertEqual(descriptor['predicates'], [])
        baseline = compile_baseline(scope, files)
        self.assertEqual(set(descriptor['immutable_inputs']), {p for p in files if p.startswith('visual-baseline/')})
        self.assertTrue(set(descriptor['immutable_inputs']) <= baseline['files'].keys())
        changed = dict(files, **{'visual-baseline/a.ppm': 'b' * 64})
        self.assertNotEqual(compile_baseline(scope, changed)['baseline_id'], baseline['baseline_id'])
        scope['schema'] = 'agent-ee.scope/3'
        scope['baseline']['requirements'][0]['intent'] = 'acceptance'
        with self.assertRaisesRegex(ValueError, 'no executable bound'):
            compile_baseline(scope, files)
        result = {'run_id': 'run', 'policy_files': {}, 'runtime_files': {}}
        expected = capture_expectation(result, {'candidate_id': 'candidate', 'files': files,
            'scope': {'schema': 'agent-ee.scope/1', 'checks': [check]}}, [])
        self.assertNotIn('pdftoppm', expected['required_runtime'])
        self.assertIn('/usr/bin/python3', expected['required_runtime'])

    def test_unsupported_parameters_and_missing_baseline_fail_closed(self):
        for params in ({'threshold': 2}, {'baseline': '../outside'}, {'baseline': '/abs'},
                       {'baseline': 'a//b'}, {'baseline': True}, {'baseline': None}, {'baseline': 'missing'}):
            with self.subTest(params=params), self.assertRaises((ValueError, TypeError)):
                parameters(params, {})
        self.assertEqual(parameters({}, {}), [])
        self.assertEqual(compare(None, Path('/unused')), {'status': 'unknown', 'reason': 'no_baseline', 'sheets': {}})

    def test_fixed_grid_coordinates_and_deterministic_png(self):
        pixels = bytes([255]) * 3 * 210 * 210
        raster = grid(210, 210, pixels)
        self.assertEqual(png_bytes(210, 210, raster), png_bytes(210, 210, grid(210, 210, pixels)))
        self.assertEqual(raster[3 * (100 * 210 + 80):3 * (100 * 210 + 80) + 3], b'\xc0\xd8\xf0')
        self.assertEqual(100 / PIXELS_PER_MM, GRID_MM)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.ppm'
            for bad in (b'', b'P6\n2 1\n255\nabc', b'P6\n999999 999999\n255\n'):
                path.write_bytes(bad)
                with self.assertRaises(ValueError):
                    read_ppm(path)


class NativeRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True, tools=('pdftoppm',))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        root = gen.schematic([gen.sheet('Child', 'child.kicad_sch', 80, 80, 20, 15, page='4')], lib_names=[])
        self.project.joinpath('lint.kicad_sch').write_bytes(root)
        self.child('ORIGINAL')
        self.scope = {'schema': 'agent-ee.scope/1', 'name': 'Owned render hierarchy',
            'inputs': [{'path': str(self.project), 'at': 'design'}], 'schematic': 'design/lint.kicad_sch',
            'checks': [{'id': 'visual', 'kind': 'schematic_render'}]}

    def child(self, name):
        self.project.joinpath('child.kicad_sch').write_bytes(gen.schematic(
            [gen.label('label', name, 60, 60, 'owned-label')], file_uuid=gen.uid('child'), lib_names=[]))

    def run_capture(self, name, scope=None):
        path = self.root / (name + '.json')
        write_json(path, scope or self.scope)
        run = self.root / name
        run.mkdir()
        capture(path, run)
        checks = run_checks(run, time.monotonic() + 180)
        return run, checks

    def test_identical_png_sheet_local_drift_and_renderer_mismatch(self):
        first, checks = self.run_capture('first')
        self.assertEqual((checks[0]['execution'], checks[0]['outcome']), ('ok', 'info'), checks)
        second, repeated = self.run_capture('second')
        self.assertEqual(repeated[0]['outcome'], 'info', repeated)
        before, same = first / 'work/visual/render', second / 'work/visual/render'
        manifest = read_json(before / 'manifest.json')
        self.assertEqual({s['page'] for s in manifest['sheets'].values()}, {'1', '4'})
        for sheet in manifest['sheets'].values():
            self.assertEqual((before / sheet['png']).read_bytes(), (same / sheet['png']).read_bytes())
        self.assertTrue(all(s['drift_percent'] == 0 for s in compare(before, same)['sheets'].values()))
        self.child('EDITED')
        third, results = self.run_capture('changed')
        self.assertEqual(results[0]['outcome'], 'info', results)
        current = third / 'work/visual/render'
        drift = compare(before, current)
        self.assertEqual(drift['status'], 'compared', drift)
        self.assertEqual(drift['sheets']['/']['drift_percent'], 0)
        child = next(s for key, s in drift['sheets'].items() if key != '/')
        self.assertGreater(child['drift_percent'], 0)
        left, top, right, bottom = child['changed_bbox_px']
        self.assertTrue(58 <= left / PIXELS_PER_MM <= right / PIXELS_PER_MM <= 85, child)
        self.assertTrue(56 <= top / PIXELS_PER_MM <= bottom / PIXELS_PER_MM <= 62, child)
        self.assertNotEqual(child['baseline_source_sha256'], child['source_sha256'])
        changed = read_json(current / 'manifest.json')
        changed['renderer']['tools']['pdftoppm']['binary_sha256'] = '0' * 64
        changed['renderer_id'] = identity(changed['renderer'])
        write_json(current / 'manifest.json', changed)
        self.assertEqual(compare(before, current)['reason'], 'baseline_stale_renderer')
        self.assertEqual(compare(before, current)['status'], 'unknown')

    def test_captured_baseline_and_packet_seal_retain_visual_evidence(self):
        original, _ = self.run_capture('baseline')
        visual = self.root / 'visual-baseline'
        shutil.copytree(original / 'work/visual/render', visual)
        scope = copy.deepcopy(self.scope)
        scope['inputs'].append({'path': str(visual), 'at': 'baseline'})
        scope['checks'][0]['params'] = {'baseline': 'baseline'}
        self.project.joinpath('review.txt').write_text('Images support independent review; no verdict.')
        scope['schema'] = 'agent-ee.scope/2'
        scope['baseline'] = {'requirements': [{'id': 'R1', 'statement': 'Review image',
            'source': {'path': 'design/review.txt', 'locator': 'R1'}, 'checks': ['visual']}],
            'targets': ['design/review.txt'], 'reference_architectures': ['design/review.txt']}
        self.child('EDITED')
        run, checks = self.run_capture('candidate', scope)
        self.assertEqual(checks[0]['details']['visual_drift']['status'], 'compared', checks)
        packet = build_packet(run, checks, 'test-run')
        self.assertIn('png_sha256', str(packet['artifacts']))
        self.assertTrue(any(p['path'].endswith('.png') for p in packet['citation_files']))
        seal = evidence_seal(run, checks)['checks']['visual']['artifacts']
        image = next(p for p in seal if p.endswith('.png'))
        self.assertEqual(seal[image], digest(run / image))
        (run / image).write_bytes(b'corrupt')
        self.assertEqual(compare(visual, run / 'work/visual/render')['status'], 'unknown')

    def test_render_failure_records_missing_packet_evidence_and_review_continues(self):
        self.project.joinpath('child.kicad_sch').write_text('invalid schematic')
        run, checks = self.run_capture('failed')
        self.assertEqual((checks[0]['execution'], checks[0]['outcome']), ('failed', 'unknown'), checks)
        packet = build_packet(run, checks, 'test-run')
        self.assertIn('missing visual evidence', str(packet).lower())
        # A completed critique is validated by the existing review validator,
        # independently of optional image coverage. No provider call is made here.
        response = {'schema': 'agent-ee.review/2', 'candidate_id': packet['candidate_id'],
            'run_id': packet['run_id'], 'packet_sha256': packet['packet_sha256'], 'role': 'circuit',
            'coverage': ['native source'], 'limitations': ['missing visual evidence'], 'findings': []}
        def validate(value):
            return validate_review(value, packet['candidate_id'], packet['run_id'], packet['packet_sha256'], run)
        record, value = structured_call(run, 'circuit', 'independent critique', {},
            lambda prompt: ({'execution': 'ok'}, response), validate)
        self.assertEqual(record['execution'], 'ok')
        self.assertEqual(value['limitations'], ['missing visual evidence'])
        record, value = structured_call(run, 'physical', 'independent critique', {},
            lambda prompt: ({'execution': 'ok'}, {'symbol_overlaps': 'malformed critique'}), validate)
        self.assertEqual(record['execution'], 'malformed')
        self.assertIsNone(value)

    def test_reused_sheet_instances_and_corrupt_baselines_are_unknown(self):
        self.project.joinpath('lint.kicad_sch').write_bytes(gen.schematic([
            gen.sheet('Child', 'child.kicad_sch', 80, 80, 20, 15, page='4'),
            gen.sheet('Repeated', 'child.kicad_sch', 120, 80, 20, 15, page='9')], lib_names=[]))
        run, checks = self.run_capture('reused')
        self.assertEqual(checks[0]['outcome'], 'info', checks)
        current = run / 'work/visual/render'
        manifest = read_json(current / 'manifest.json')
        self.assertEqual(len(manifest['sheets']), 3)
        children = [s for key, s in manifest['sheets'].items() if key != '/']
        self.assertEqual(children[0]['source_sha256'], children[1]['source_sha256'])
        self.assertNotEqual(children[0]['png'], children[1]['png'])
        baseline = self.root / 'baseline-copy'
        shutil.copytree(current, baseline)
        for change in ('inventory', 'identity', 'dimensions', 'unsafe', 'missing', 'pixels', 'shape', 'source', 'candidate'):
            with self.subTest(change=change):
                modified = copy.deepcopy(manifest)
                sheet = modified['sheets']['/']
                if change == 'inventory':
                    modified['sheets'].pop(next(k for k in modified['sheets'] if k != '/'))
                elif change == 'identity':
                    modified['renderer_id'] = 'altered'
                elif change == 'dimensions':
                    sheet['width'] += 1
                elif change == 'unsafe':
                    sheet['png'] = '../outside.png'
                elif change == 'missing':
                    sheet['png'] = 'absent.png'
                elif change == 'shape':
                    modified['sheets'] = ['wrong inventory type']
                elif change == 'source':
                    modified['sources'] = {}
                elif change == 'candidate':
                    modified['candidate_id'] = None
                elif change == 'pixels':
                    image = baseline / sheet['png']
                    saved = image.read_bytes()
                    image.write_bytes(b'not a PNG')
                    sheet['png_sha256'] = digest(image)
                write_json(baseline / 'manifest.json', modified)
                self.assertEqual(compare(baseline, current)['status'], 'unknown')
                if change == 'pixels':
                    image.write_bytes(saved)
        write_json(baseline / 'manifest.json', manifest)
        self.assertEqual(compare(baseline, current)['status'], 'compared')

    def test_missing_export_output_and_duplicate_pages_are_unusable(self):
        # Synthetic boundary: a zero exit without a manifest cannot become info.
        with patch('agent_ee.checks.Ctx.execute'):
            _, checks = self.run_capture('missing-output')
        self.assertEqual((checks[0]['execution'], checks[0]['outcome']), ('malformed', 'unknown'))
        self.assertIn('was not written', checks[0]['summary'])
        self.project.joinpath('lint.kicad_sch').write_bytes(gen.schematic([
            gen.sheet('Child', 'child.kicad_sch', 80, 80, 20, 15, page='1')], lib_names=[]))
        _, checks = self.run_capture('duplicate-pages')
        self.assertEqual(checks[0]['outcome'], 'unknown')
        self.assertIn('duplicate', checks[0]['summary'])

    def test_missing_tool_and_failed_native_export_never_pass(self):
        from agent_ee.schematic_render import render
        with patch('agent_ee.schematic_render.shutil.which', return_value=None):
            manifest = render(self.project, 'lint.kicad_sch', self.root / 'missing', 'candidate')
        self.assertEqual(manifest['status'], 'missing')
        self.assertIn('unavailable', manifest['error'])
        # Shared hierarchy parsing succeeds; the real native exporter rejects the format.
        schematic = self.project / 'lint.kicad_sch'
        schematic.write_bytes(schematic.read_bytes().replace(b'(version 20260306)', b'(version invalid)'))
        run, checks = self.run_capture('bad-native')
        self.assertEqual(checks[0]['outcome'], 'unknown')
        self.assertNotEqual(checks[0]['execution'], 'ok')


if __name__ == '__main__':
    unittest.main()
