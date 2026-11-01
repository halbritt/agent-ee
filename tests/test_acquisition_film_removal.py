"""Required independent-removal evidence stays complete and candidate-bound."""
import json
from hashlib import sha256
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from agent_ee import acquisition_film as film, acquisition_film_removal as removal, typed_overlay

CAPTURE = Path(os.environ.get('AGENT_EE_FILM_REMOVAL', '/nonexistent/film-removal'))
LOOP = Path(os.environ.get('AGENT_EE_FILM_LOOP', '/nonexistent/film-loop'))
RUN = Path(os.environ.get('AGENT_EE_FILM_RUN', '/nonexistent/film-run'))
BASE = Path(os.environ.get('AGENT_EE_FILM_BASE', '/nonexistent/film-base'))
MODEL = Path(os.environ.get('AGENT_EE_FILM_MODEL', '/nonexistent/film-model'))


@unittest.skipUnless((CAPTURE / 'analysis/film-removal/matrix-completion.json').is_file()
                     and (RUN / 'model-r1/execution.json').is_file(), 'actual complete removal matrix required')
class IndependentRemovalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        root = Path(cls.temporary.name)
        cls.before = root / 'before'
        cls.after = root / 'after'
        args = (BASE, RUN / 'selected', RUN / 'model-r1', MODEL)
        film.prepare(*args, cls.before, loop_evidence=LOOP)
        film.prepare(*args, cls.after, loop_evidence=LOOP, removal_evidence=CAPTURE)

    def test_independent_cases_add_a_required_check_without_changing_the_design_or_limits(self):
        before, after = self.before / 'inputs', self.after / 'inputs'
        for name in ('circuit.json', 'parts.json', 'spec.md', 'spec-record.json',
                     'electrical-locality.json', 'placement.json', 'groups.json',
                     'acquisition-block-source.json'):
            self.assertEqual((before / name).read_bytes(), (after / name).read_bytes(), name)
        mechanics = []
        for root in (before, after):
            document = json.loads((root / 'placement-mechanics.json').read_bytes())
            self.assertEqual(document['sources'].pop('requirement-map.json'),
                             sha256((root / 'requirement-map.json').read_bytes()).hexdigest())
            mechanics.append(document)
        self.assertEqual(*mechanics)
        old = typed_overlay._check_pool(json.loads((before / 'requirement-map.json').read_bytes()))
        new = typed_overlay._check_pool(json.loads((after / 'requirement-map.json').read_bytes()))
        self.assertEqual(set(new), set(old) | {removal.CHECK})
        for name, value in old.items():
            self.assertEqual(new[name], value, name)
        expected = json.loads(json.dumps(old['film-power-retained-model']['params']['limits']))
        for row in expected.values():
            row['condition'] = removal.CONDITION
        self.assertEqual(new[removal.CHECK]['params']['limits'], expected)
        document = json.loads((after / 'requirement-map.json').read_bytes())
        def check_mappings(value):
            if isinstance(value, dict):
                if 'checks' in value:
                    ids = [c['id'] for c in value['checks']]
                    if 'film-power-retained-model' in ids:
                        self.assertIn(removal.CHECK, ids)
                for child in value.values():
                    check_mappings(child)
            elif isinstance(value, list):
                for child in value:
                    check_mappings(child)
        check_mappings(document)
        self.assertFalse(json.loads((self.after / 'preparation.json').read_text())['accepted'])

    def test_changed_candidate_or_captured_trace_cannot_inherit_the_matrix(self):
        files, _ = film.sources(BASE, RUN / 'selected')
        film._power(files, RUN / 'model-r1', MODEL)
        for name in ('circuit.json', 'parts.json'):
            changed = dict(files)
            changed[name] += b'\n'
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'exact generated'):
                removal.attach(changed, CAPTURE)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'capture'
            shutil.copytree(CAPTURE, root)
            raw = root / 'analysis/film-removal/cases/corner-3/probe/high-z-removal.raw'
            with raw.open('ab') as stream:
                stream.write(b'\n')
            with self.assertRaises(ValueError):
                removal.attach(dict(files), root)

    def test_real_traces_replay_all_cases_and_post_adoption_drift_refuses(self):
        inputs = self.after / 'inputs'
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'measurements.json'
            command = [sys.executable, '-B', str(inputs / 'analysis/film-removal/replay.py'),
                       str(inputs), str(output)]
            result = subprocess.run(command, text=True, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            actual = json.loads(output.read_text())
            expected = json.loads((inputs / 'analysis/film-removal/summary.json').read_text())
            self.assertEqual(actual, expected)
            self.assertEqual(actual['case_count'], 8)
            self.assertFalse(actual['accepted'])
            # Restore the shared class fixture after exercising a real stale
            # retained-output refusal; no solver or measurement function is mocked.
            path = inputs / 'analysis/film-removal/cases/corner-3/probe/high-z-removal.log'
            original = path.read_bytes()
            try:
                path.write_bytes(original + b'\n')
                result = subprocess.run(command, text=True, capture_output=True, timeout=60)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('retained removal evidence changed', result.stderr)
            finally:
                path.write_bytes(original)


if __name__ == '__main__':
    unittest.main()
