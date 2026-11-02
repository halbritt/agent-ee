"""Actual retained AC evidence must stay reproducible and bound to its design."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from agent_ee.acquisition import film
from agent_ee import typed_overlay

CAPTURE = Path(os.environ.get('AGENT_EE_FILM_LOOP', '/nonexistent/film-loop'))
RUN = Path(os.environ.get('AGENT_EE_FILM_RUN', '/nonexistent/film-run'))
BASE = Path(os.environ.get('AGENT_EE_FILM_BASE', '/nonexistent/film-base'))
MODEL = Path(os.environ.get('AGENT_EE_FILM_MODEL', '/nonexistent/film-model'))


@unittest.skipUnless((CAPTURE / 'analysis/film-loop/comparison-exact/result.json').is_file()
                     and (RUN / 'model-r1/execution.json').is_file(), 'retained loop and film inputs required')
class LoopEvidenceTests(unittest.TestCase):
    def files(self):
        files, _ = film.sources(BASE, RUN / 'selected')
        film._power(files, RUN / 'model-r1', MODEL)
        return files

    def test_attachment_preserves_circuit_placement_and_every_native_check(self):
        before = self.files()
        after = dict(before)
        film._loop_evidence(after, CAPTURE)
        for path in ('circuit.json', 'parts.json', 'spec.md', 'electrical-locality.json',
                     'placement.json', 'groups.json', 'placement-mechanics.json'):
            self.assertEqual(before[path], after[path], path)
        film._requirements(before)
        film._requirements(after)
        self.assertEqual(typed_overlay._check_pool(json.loads(before['requirement-map.json'])),
                         typed_overlay._check_pool(json.loads(after['requirement-map.json'])))
        briefs = json.loads(after['preparation/judgment-briefs.json'])
        source = briefs['judgments/acq-elec-2-source-evaluation.json']['sources']
        self.assertIn('analysis/film-loop/comparison-exact/result.json', source)
        manifest = json.loads(after['analysis/film-loop/evidence-manifest.json'])
        self.assertFalse(manifest['accepted'])
        self.assertEqual(manifest['file_release'], 'not_assessed')

    def test_changed_target_or_native_output_cannot_inherit_evidence(self):
        for path in ('circuit.json', 'parts.json'):
            files = self.files()
            files[path] += b'\n'
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'exact generated'):
                film._loop_evidence(files, CAPTURE)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'capture'
            shutil.copytree(CAPTURE, root)
            raw = root / 'analysis/film-loop/comparison-exact/probe/loop-comparison.raw'
            raw.write_bytes(raw.read_bytes() + b'\n')
            with self.assertRaises(ValueError):
                film._loop_evidence(self.files(), root)

    def test_raw_data_reproduces_pilot_and_exact_comparison_without_native_rerun(self):
        # Execute the captured calculation over actual retained native output.
        # This is an offline replay, not a mock of the numerical solver.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'analysis'
            shutil.copytree(CAPTURE / 'analysis/film-loop', root)
            for script, report in (('loop_analysis.py', 'pilot/result.json'),
                                   ('analyze_comparison.py', 'comparison/result.json'),
                                   ('analyze_exact.py', 'comparison-exact/result.json')):
                expected = json.loads((root / report).read_bytes())
                result = subprocess.run([sys.executable, '-B', str(root / script)],
                                        cwd=root, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads((root / report).read_bytes()), expected)


if __name__ == '__main__':
    unittest.main()
