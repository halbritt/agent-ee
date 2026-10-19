"""AGENTEE-219: pinned kicad-happy 2.3.1 PP-001/PR-001 false positives on an owned reproducer.

The pinned analyzer's false positives are recorded known limitations, never a
pass: the real severity gate still reports them as errors (a defect). The
proposed correction (an exact commit in the kicad-happy object store, not
adopted) must turn exactly those errors into evidence-bearing info notes, never
silence, while the real violations and both agent-469 adversarial controls
keep failing.
"""
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agent_ee import candidate, checks
from agent_ee.happy_severity_gate import KIND, SCHEMA
from agent_ee.kicad_happy import installation
from agent_ee.storage import read_json, write_json
from toolchain import require_toolchain

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / 'fixtures/kicad-happy/ac4-false-positives'
BOARD = REPO / 'fixtures/kicad-happy/severity-gate/warning/gate.kicad_pcb'
PINNED = Path(os.environ.get('AGENT_EE_KICAD_HAPPY', str(Path.home() / 'git/kicad-happy')))
EXPECTED = read_json(FIXTURE / 'expected.json')
LIMITATIONS = {e['finding_id'] for e in EXPECTED['known_analyzer_limitations']}
VIOLATIONS = {e['finding_id'] for e in EXPECTED['real_violations']}


def missing_input(reason):
    if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1':
        raise AssertionError('AGENT_EE_REQUIRE_TOOLCHAIN=1: ' + reason)
    raise unittest.SkipTest(reason)


def corrected_checkout(destination, store=PINNED):
    """The proposed correction's exact bytes, read-only from the pinned store.

    AGENT_EE_KICAD_HAPPY_CORRECTED may name a checkout; otherwise the recorded
    commit is exported from the Git object store of ``store``
    (AGENT_EE_KICAD_HAPPY). Both are held to the recorded installation
    identity. A missing commit fails under AGENT_EE_REQUIRE_TOOLCHAIN=1.
    """
    explicit = os.environ.get('AGENT_EE_KICAD_HAPPY_CORRECTED')
    if explicit:
        return Path(explicit)
    corrected = EXPECTED['corrected']
    commit = corrected['commit']
    probe = subprocess.run(['git', '-C', str(store), 'cat-file', '-e', commit + '^{commit}'],
                           capture_output=True)
    if probe.returncode:
        missing_input(
            f"corrected kicad-happy commit {commit} (branch {corrected['branch']}) is not in the "
            f"Git object store of AGENT_EE_KICAD_HAPPY={store}. Fetch that commit into the store, "
            f"or set AGENT_EE_KICAD_HAPPY_CORRECTED to a checkout of exactly that commit; see "
            f"docs/happy-severity-gate.md#known-analyzer-limitations-pinned-231")
    archive = subprocess.run(['git', '-C', str(store), 'archive', '--format=tar', commit,
                              'skills', '.claude-plugin'], capture_output=True, check=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(destination, filter='data')
    return Path(destination)


def analyze(root, work):
    """Run the checkout's schematic analyzer on the reproducer with the gate's config."""
    config = work / 'config.json'
    write_json(config, {'version': 1, 'project': {}, 'suppressions': []})
    output = work / 'schematic.json'
    result = subprocess.run([sys.executable, '-B', str(root / 'skills/kicad/scripts/analyze_schematic.py'),
                             str(FIXTURE / 'reproducer.kicad_sch'), '--output', str(output),
                             '--config', str(config)],
                            capture_output=True, text=True, cwd=work, timeout=300)
    if result.returncode:
        raise AssertionError(f'analyzer failed ({result.returncode}): {result.stderr[-2000:]}')
    return read_json(output)


def errors(report):
    return {f['finding_id'] for f in report['findings'] if f['severity'] == 'error'}


class FixtureTests(unittest.TestCase):
    def test_reproducer_is_the_owned_generator_output(self):
        sys.path.insert(0, str(FIXTURE))
        try:
            import generate
        finally:
            sys.path.remove(str(FIXTURE))
        self.assertEqual(generate.schematic(), (FIXTURE / 'reproducer.kicad_sch').read_text())
        self.assertFalse(LIMITATIONS & VIOLATIONS)
        self.assertEqual(EXPECTED['pinned']['status'], 'known_analyzer_limitation')
        self.assertEqual(EXPECTED['corrected']['status'], 'proposed_not_adopted')
        self.assertNotIn(EXPECTED['corrected']['commit'],
                         {s['commit'] for s in EXPECTED['superseded']})
        notes = {n['finding_id'] for n in EXPECTED['corrected_notes']}
        self.assertFalse(notes & VIOLATIONS)
        self.assertEqual(set(EXPECTED['corrected_unresolved']) - VIOLATIONS, set())

    def test_missing_corrected_commit_fails_actionably_in_strict_mode(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-ac4-store-') as store:
            subprocess.run(['git', 'init', '-q', store], check=True)
            environment = {k: v for k, v in os.environ.items()
                           if k != 'AGENT_EE_KICAD_HAPPY_CORRECTED'}
            with mock.patch.dict(os.environ, dict(environment, AGENT_EE_REQUIRE_TOOLCHAIN='1'),
                                 clear=True):
                with self.assertRaises(AssertionError) as raised:
                    corrected_checkout(Path(store) / 'out', store=Path(store))
            message = str(raised.exception)
            for needed in (EXPECTED['corrected']['commit'], store, 'AGENT_EE_KICAD_HAPPY_CORRECTED',
                           'Fetch that commit'):
                self.assertIn(needed, message)
            environment.pop('AGENT_EE_REQUIRE_TOOLCHAIN', None)
            with mock.patch.dict(os.environ, environment, clear=True):
                with self.assertRaises(unittest.SkipTest):
                    corrected_checkout(Path(store) / 'out', store=Path(store))


class AnalyzerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (PINNED / '.claude-plugin/plugin.json').is_file():
            missing_input(f'pinned kicad-happy installation unavailable at {PINNED}')
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-ac4-')
        cls.root = Path(cls.tmp.name)
        cls.corrected = corrected_checkout(cls.root / 'corrected')
        for name, root in (('pinned', PINNED), ('corrected', cls.corrected)):
            if installation(root)['sha256'] != EXPECTED[name]['installation_sha256']:
                raise AssertionError(f'{name} kicad-happy bytes differ from the recorded identity: {root}')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def work(self, name):
        path = self.root / f'{name}-{time.monotonic_ns()}'
        path.mkdir()
        return path

    def test_pinned_false_positives_are_recorded_limitations_not_a_pass(self):
        report = analyze(PINNED, self.work('pinned'))
        self.assertEqual(errors(report), LIMITATIONS | VIOLATIONS)

    def test_corrected_states_evidence_and_keeps_real_violations_and_controls(self):
        report = analyze(self.corrected, self.work('corrected'))
        self.assertEqual(errors(report), VIOLATIONS)
        by_id = {f['finding_id']: f for f in report['findings']}
        # Each cleared limitation is an info note naming its evidence or
        # assumptions; nothing is cleared silently.
        for note in EXPECTED['corrected_notes']:
            finding = by_id.get(note['finding_id'])
            self.assertIsNotNone(finding, note['finding_id'])
            self.assertEqual((finding['severity'], finding['summary'], finding.get('assumptions')),
                             (note['severity'], note['summary'], note.get('assumptions')))
        for fid, unresolved in EXPECTED['corrected_unresolved'].items():
            self.assertEqual(by_id[fid].get('unresolved'), unresolved)
        # The I2C errors of the SPI-wired device are replaced by the SPI note.
        for fid in LIMITATIONS - {n['finding_id'] for n in EXPECTED['corrected_notes']}:
            self.assertNotIn(fid, by_id)

    def run_gate(self, root, dispositions):
        require_toolchain(native=True)
        work = self.work('gate')
        source = work / 'dispositions.json'
        write_json(source, {'schema': SCHEMA, 'dispositions': dispositions})
        scope = {'schema': 'agent-ee.scope/3', 'name': 'AGENTEE-219 reproducer',
                 'scope': 'Analyzer false-positive reproducer only',
                 'limitations': ['No electrical or EMC certification.'],
                 'inputs': [{'path': str(FIXTURE / 'reproducer.kicad_sch'), 'at': 'project/reproducer.kicad_sch'},
                            {'path': str(FIXTURE / 'reproducer.kicad_pro'), 'at': 'project/reproducer.kicad_pro'},
                            {'path': str(BOARD), 'at': 'project/reproducer.kicad_pcb'},
                            {'path': str(FIXTURE / 'expected.json'), 'at': 'requirements/expected.json'},
                            {'path': str(source), 'at': 'requirements/dispositions.json'}],
                 'schematic': 'project/reproducer.kicad_sch', 'board': 'project/reproducer.kicad_pcb',
                 'requirements': ['requirements/expected.json', 'requirements/dispositions.json'],
                 'mutable_inputs': ['project/reproducer.kicad_sch', 'project/reproducer.kicad_pcb'],
                 'checks': [{'id': 'severity', 'kind': KIND, 'params': {
                     'installation': str(root), 'installation_sha256': installation(root)['sha256'],
                     'dispositions': 'requirements/dispositions.json'}}],
                 'baseline': {'requirements': [{'id': 'R-severity', 'intent': 'acceptance',
                     'statement': 'No analyzer errors',
                     'source': {'path': 'requirements/expected.json', 'locator': '/real_violations'},
                     'checks': ['severity']}],
                     'targets': ['requirements/expected.json'],
                     'reference_architectures': ['requirements/expected.json']}}
        path = work / 'scope.json'
        write_json(path, scope)
        run = work / 'run'
        run.mkdir()
        candidate.capture(path, run)
        return checks.run_checks(run, time.monotonic() + 900)[0]

    def test_severity_gate_keeps_pinned_limitations_blocking_and_corrected_controls_failing(self):
        # A disposition naming a limitation never waives an error. The
        # corrected analyzer reuses PP-001 limitation ids for info notes (same
        # rule and component, new severity): a stale disposition must neither
        # attach to them nor count, so it is reported unused.
        waivers = [{'finding_id': fid, 'disposition': 'false_positive',
                    'rationale': 'AGENTEE-219 analyzer limitation; not a waiver',
                    'recorded_by': 'test author'} for fid in sorted(LIMITATIONS)]
        for root, expected in ((PINNED, LIMITATIONS | VIOLATIONS), (self.corrected, VIOLATIONS)):
            with self.subTest(root=str(root)):
                result = self.run_gate(root, waivers)
                self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
                self.assertEqual(result['details']['installation']['root'], str(root.resolve()))
                self.assertIn('skills/kicad/scripts/signal_detectors.py', result['details']['executed_files'])
                normalized = result['details']['normalized']
                schematic = {fid for fid in normalized['error_finding_ids'] if fid.startswith('schematic:')}
                self.assertEqual(schematic, expected)
                self.assertEqual(normalized['unused_disposition_ids'], sorted(LIMITATIONS))
                self.assertFalse([f['finding_id'] for f in normalized['findings'] if 'disposition' in f
                                  and f['finding_id'] in LIMITATIONS])


if __name__ == '__main__':
    unittest.main()
