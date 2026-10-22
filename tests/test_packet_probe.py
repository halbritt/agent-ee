"""Packet accounting on synthetic captured evidence; no pod qualification claim."""
import contextlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee.candidate import capture
from agent_ee.packet_probe import main, probe_candidates
from agent_ee.reviewers import build_packet
from agent_ee.storage import digest, read_json, write_json


class PacketProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / 'source'
        source.mkdir()
        (source / 'board').write_text('synthetic board input')
        (source / 'schematic').write_text('synthetic schematic input')
        (source / 'requirements').write_text('µ volts "quoted"\n')
        self.scope = {'schema': 'agent-ee.scope/1', 'name': 'synthetic packet test',
                      'board': 'design/board', 'schematic': 'design/schematic',
                      'inputs': [{'path': str(source), 'at': 'design'}],
                      'requirements': ['design/requirements'], 'packet_max_bytes': 1024,
                      'checks': [{'id': 'nets', 'kind': 'netlist'}, {'id': 'drc', 'kind': 'drc'},
                                 {'id': 'probe', 'kind': 'measurements'}]}
        write_json(self.root / 'scope.json', self.scope)
        self.run = self.root / 'captured'
        self.run.mkdir()
        self.manifest = capture(self.root / 'scope.json', self.run)
        evidence = {'nets': ('design.xml', '<export><components/><libparts/><nets/></export>'),
                    'drc': ('report.json', '{"violations":[]}'),
                    'probe': ('measurements.json', '{"volts":3.3}')}
        self.checks = []
        for cid, (name, content) in evidence.items():
            path = self.run / 'work' / cid / name
            path.parent.mkdir(parents=True)
            path.write_text(content)
            check = {'id': cid, 'kind': next(c['kind'] for c in self.scope['checks'] if c['id'] == cid),
                     'candidate_id': self.manifest['candidate_id'], 'execution': 'ok', 'outcome': 'pass',
                     'evidence': [path.relative_to(self.run).as_posix()]}
            self.checks.append(check)
            write_json(self.run / 'checks' / (cid + '.json'), check)
        self.save_result()

    def save_result(self):
        self.result = {'candidate_id': self.manifest['candidate_id'], 'run_id': 'synthetic-run',
                       'state': 'complete', 'checks': self.checks,
                       'manifest_sha256': digest(self.run / 'candidate.json'),
                       'artifact_hashes': self.inventory(self.run)}
        write_json(self.run / 'result.json', self.result)

    def inventory(self, root):
        return {p.relative_to(root).as_posix(): digest(p) for p in root.rglob('*') if p.is_file()}

    def probe(self, name='budget', candidates=None):
        return probe_candidates(candidates or {'empty': self.run}, self.root / name)

    def test_real_assembly_exact_sections_and_source_preservation_without_harness(self):
        before = self.inventory(self.run)
        with contextlib.ExitStack() as stack:
            calls = [stack.enter_context(patch(target, side_effect=AssertionError('inference forbidden')))
                     for target in ('agent_ee.harnesses.native_call', 'agent_ee.reviewers.claude_call',
                                    'agent_ee.reviewers.initial_reviews', 'agent_ee.reviewers.adjudicator_call',
                                    'subprocess.Popen')]
            report = self.probe(candidates={s: self.run for s in ('empty', 'schematic', 'placement', 'routing')})
            for call in calls:
                call.assert_not_called()
        self.assertEqual(before, self.inventory(self.run))
        self.assertFalse(report['inference']['invoked'])
        self.assertIsNone(report['model_call_seconds'])
        for candidate in report['candidates']:
            self.assertEqual(candidate['status'], 'measured')
            self.assertEqual(candidate['candidate_id'], self.manifest['candidate_id'])
            path = self.root / 'budget' / candidate['stage']
            packet = read_json(path / 'packet.json')
            # Independent encoding oracle, exactly the production reviewer payload encoding.
            self.assertEqual(candidate['packet_bytes'], len(json.dumps(packet).encode('utf-8')))
            self.assertEqual(sum(r['packet_bytes'] for r in candidate['sections']), candidate['packet_bytes'])
            self.assertTrue(all(r['packet_bytes'] > 0 for r in candidate['sections']))
            self.assertEqual(build_packet(path, self.checks, 'synthetic-run'), packet)

    def test_oversized_receipts_and_artifact_prefixes_are_separate(self):
        self.checks[1]['details'] = {'log': 'x' * 1_100_000}
        write_json(self.run / 'checks/drc.json', self.checks[1])
        (self.run / 'work/probe/measurements.json').write_text('a' * 200_000)
        self.save_result()
        candidate = self.probe()['candidates'][0]
        self.assertEqual(candidate['status'], 'measured')
        self.assertGreater(candidate['packet_bytes'], 1_000_000)
        self.assertGreater(next(r for r in candidate['check_receipts'] if r['id'] == 'drc')['packet_bytes'], 1_000_000)
        self.assertTrue(candidate['packet_exceeds_cap'])
        self.assertTrue(any(r['section'] == 'receipts' for r in candidate['overflow_sections']))
        self.assertEqual(sum(r['bytes_beyond_cap'] for r in candidate['overflow_sections']),
                         candidate['packet_overflow_bytes'])
        artifact = next(a for a in candidate['truncated_artifacts'] if a['section'] == 'probe_output')
        self.assertEqual(artifact['bytes_total'], 200_000)
        self.assertLessEqual(candidate['artifact_text_bytes'], 1024)

    def test_changed_evidence_and_source_are_unavailable_not_pass(self):
        for path in (self.run / 'inputs/design/board', self.run / 'work/drc/report.json',
                     self.run / 'checks/drc.json'):
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(b'{}')
                report = self.probe(name=path.name)
                self.assertEqual(report['candidates'][0]['status'], 'unavailable')
                self.assertNotIn('packet_bytes', report['candidates'][0])
                path.write_bytes(original)

    def test_missing_stage_and_failed_native_execution_stay_explicit(self):
        self.checks[1].update(execution='failed', outcome='unknown')
        write_json(self.run / 'checks/drc.json', self.checks[1])
        self.save_result()
        report = self.probe()
        self.assertEqual([r['status'] for r in report['candidates']],
                         ['measured', 'unavailable', 'unavailable', 'unavailable'])
        self.assertEqual(report['candidates'][0]['checks'][1]['execution'], 'failed')
        with contextlib.redirect_stdout(__import__('io').StringIO()):
            self.assertEqual(main(['--candidate', f'empty={self.run}', '--out', str(self.root / 'cli')]), 1)

    def test_runtime_guard_rejects_actual_process_attempt(self):
        def tries_process(*args):
            subprocess.run(['/bin/true'], check=True)
        with patch('agent_ee.packet_probe.build_packet', side_effect=tries_process):
            with self.assertRaisesRegex(RuntimeError, 'forbids process/network'):
                self.probe()
        self.assertFalse((self.root / 'budget/packet-budget.json').exists())

    def test_swallowed_process_attempt_still_refuses_report(self):
        def tries_process(*args):
            try:
                subprocess.run(['/bin/true'], check=True)
            except RuntimeError:
                pass
            return build_packet(*args)
        with patch('agent_ee.packet_probe.build_packet', side_effect=tries_process):
            with self.assertRaisesRegex(RuntimeError, 'attempted process/network'):
                self.probe()
        self.assertFalse((self.root / 'budget/packet-budget.json').exists())

    def test_immutable_baseline_packet_rebuild(self):
        from test_baseline import BaselineTests
        fixture = BaselineTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        write_json(fixture.run / 'result.json', {
            'candidate_id': fixture.manifest['candidate_id'], 'run_id': 'baseline-test',
            'manifest_sha256': digest(fixture.run / 'candidate.json'),
            'checks': [fixture.check], 'state': 'complete',
            'artifact_hashes': self.inventory(fixture.run)})
        candidate = self.probe(candidates={'empty': fixture.run})['candidates'][0]
        self.assertEqual(candidate['status'], 'measured')
        packet = read_json(self.root / 'budget/empty/packet.json')
        self.assertEqual(packet['baseline'], read_json(fixture.run / 'baseline.json'))

    def add_visual(self, rel='work/candidate.visuals/native/front/front.svg'):
        path = self.run / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        self.manifest['visuals'] = {'board_sha256': self.manifest['files']['design/board'],
                                    'images': [{'view': 'front', 'path': rel, 'sha256': digest(path)}]}
        write_json(self.run / 'candidate.json', self.manifest)
        self.save_result()
        return path

    def test_coordinator_images_are_copied_into_the_measured_packet(self):
        self.add_visual()
        candidate = self.probe()['candidates'][0]
        self.assertEqual(candidate['status'], 'measured')
        packet = read_json(self.root / 'budget/empty/packet.json')
        self.assertEqual(packet['candidate_visuals'], self.manifest['visuals'])
        self.assertTrue((self.root / 'budget/empty/work/candidate.visuals/native/front/front.svg').is_file())

    def test_changed_or_foreign_coordinator_image_is_unavailable(self):
        path = self.add_visual()
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><g/></svg>')
        self.assertEqual(self.probe(name='changed')['candidates'][0]['status'], 'unavailable')
        self.add_visual('work/drc/foreign.svg')
        candidate = self.probe(name='foreign')['candidates'][0]
        self.assertEqual(candidate['status'], 'unavailable')
        self.assertIn('non-coordinator candidate image', candidate['reason'])

    def test_output_overlap_and_reuse_refused(self):
        with self.assertRaisesRegex(ValueError, 'separate'):
            probe_candidates({'empty': self.run}, self.run / 'budget')
        self.probe()
        with self.assertRaises(FileExistsError):
            self.probe()

    def test_symlink_artifact_is_unavailable(self):
        path = self.run / 'work/drc/report.json'
        content = path.read_bytes()
        external = self.root / 'external'
        external.write_bytes(content)
        path.unlink()
        path.symlink_to(external)
        self.assertEqual(self.probe()['candidates'][0]['status'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
