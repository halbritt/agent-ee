"""AGENTEE-219: pinned kicad-happy 2.3.1 PP-001/PR-001 false positives on an owned reproducer.

The pinned analyzer's false positives are recorded known limitations, never a
pass: the real severity gate still reports them as errors (a defect). The
proposed correction (an exact, vendored MIT patch, not adopted) must turn
exactly those errors into evidence-bearing info notes, never silence, while the real violations and both agent-469 adversarial controls
keep failing.
"""
import importlib.util
import os
import subprocess
import sys
import shutil
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


def corrected_checkout(destination, store=PINNED, patch=FIXTURE / 'correction.patch'):
    """Reconstruct the proposed installation without a Git store or network.

    Check both identities: a cleanly applying patch alone does not prove the
    original installation or the resulting analyzer bytes are the qualified ones.
    git apply is available in the CI image and rejects missing/conflicting hunks.
    """
    if installation(store)['sha256'] != EXPECTED['pinned']['installation_sha256']:
        raise AssertionError(f'pinned kicad-happy bytes differ from the recorded identity: {store}')
    destination = Path(destination)
    shutil.copytree(store, destination,
                    ignore=shutil.ignore_patterns('.git', '__pycache__', '.pytest_cache'))
    patch = Path(patch).resolve()
    for flags in (['--check'], []):
        result = subprocess.run(['git', 'apply', *flags, str(patch)], cwd=destination,
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(f'kicad-happy correction patch failed ({patch}): '
                                 f'{result.stderr.strip()}')
    if installation(destination)['sha256'] != EXPECTED['corrected']['installation_sha256']:
        raise AssertionError(f'corrected kicad-happy bytes differ from the recorded identity: {destination}')
    return destination


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

    def test_gitless_installation_reconstructs_exact_correction(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-ac4-portable-') as tmp:
            root = Path(tmp)
            store = root / 'gitless'
            # Match the image: installed tree, no .git or corrected-checkout env.
            shutil.copytree(PINNED, store, ignore=shutil.ignore_patterns(
                '.git', '__pycache__', '.pytest_cache'))
            self.assertFalse((store / '.git').exists())
            corrected = corrected_checkout(root / 'out', store=store)
            self.assertEqual(installation(corrected)['sha256'],
                             EXPECTED['corrected']['installation_sha256'])
            self.assertEqual(installation(store)['sha256'],
                             EXPECTED['pinned']['installation_sha256'])

    def test_missing_or_invalid_patch_fails_loudly_even_without_strict_mode(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-ac4-patch-') as tmp:
            root = Path(tmp)
            invalid = root / 'invalid.patch'
            invalid.write_text('not a patch\n')
            conflict = root / 'conflict.patch'
            conflict.write_text((FIXTURE / 'correction.patch').read_text().replace(
                'diff --git a/skills/', 'diff --git a/absent/').replace(
                '--- a/skills/', '--- a/absent/').replace(
                '+++ b/skills/', '+++ b/absent/'))
            with mock.patch.dict(os.environ, {'AGENT_EE_REQUIRE_TOOLCHAIN': '0'}):
                for patch in (root / 'missing.patch', invalid, conflict):
                    with self.subTest(patch=patch.name):
                        with self.assertRaisesRegex(AssertionError, 'correction patch failed'):
                            corrected_checkout(root / patch.stem, patch=patch)

    def test_unqualified_base_or_result_fails_identity_check(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-ac4-identity-') as tmp:
            root = Path(tmp)
            for name in ('pinned', 'corrected'):
                with self.subTest(identity=name):
                    expected = {**EXPECTED, name: {**EXPECTED[name], 'installation_sha256': '0' * 64}}
                    with mock.patch.dict(EXPECTED, expected):
                        with self.assertRaisesRegex(AssertionError, name + ' kicad-happy bytes differ'):
                            corrected_checkout(root / name)


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

    def run_gate(self, root, dispositions, schematic=None):
        require_toolchain(native=True)
        work = self.work('gate')
        source = work / 'dispositions.json'
        write_json(source, {'schema': SCHEMA, 'dispositions': dispositions})
        scope = {'schema': 'agent-ee.scope/3', 'name': 'AGENTEE-219 reproducer',
                 'scope': 'Analyzer false-positive reproducer only',
                 'limitations': ['No electrical or EMC certification.'],
                 'inputs': [{'path': str(schematic or FIXTURE / 'reproducer.kicad_sch'), 'at': 'project/reproducer.kicad_sch'},
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
        # Error dispositions never waive errors. The proposed analyzer's
        # assumption-bearing info notes now require and accept exact IDs.
        # These dispositions cover this synthetic fixture only.
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
                assumption_ids = {f['finding_id'] for f in normalized['findings']
                                  if f['native'].get('assumptions') or f['native'].get('source_path') == 'assumed'}
                self.assertEqual(normalized['unused_disposition_ids'], sorted(LIMITATIONS - assumption_ids))
                self.assertEqual({f['finding_id'] for f in normalized['findings']
                                  if 'disposition' in f and f['finding_id'] in LIMITATIONS},
                                 assumption_ids & LIMITATIONS)

    def test_native_assumption_info_defect_exact_disposition_pass_and_mismatch_defect(self):
        # Author an isolated power_out case with the existing owned generator.
        # No real-pod disposition is authored here.
        spec = importlib.util.spec_from_file_location('ac4_generator', FIXTURE / 'generate.py')
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        generator.PARTS = [part for part in generator.PARTS if part[0] in ('U3', 'C2', 'U4')]
        schematic = self.work('isolated-assumption') / 'reproducer.kicad_sch'
        schematic.write_text(generator.schematic())
        fid = 'schematic:PP-001:u4'
        missing = self.run_gate(self.corrected, [], schematic)
        self.assertEqual((missing['execution'], missing['outcome']), ('ok', 'defect'), missing)
        normalized = missing['details']['normalized']
        self.assertEqual(normalized['error_finding_ids'], [])
        self.assertIn(fid, normalized['undispositioned_warning_ids'])
        native = next(f for f in normalized['findings'] if f['finding_id'] == fid)
        self.assertEqual(native['severity'], 'info')
        self.assertTrue(native['native']['assumptions'])
        # Supply policy entries for all observed warnings plus the one assumption.
        entries = [{'finding_id': i, 'disposition': 'accepted_risk',
                    'rationale': 'Owned synthetic policy regression only; no part qualification',
                    'recorded_by': 'test author'}
                   for i in normalized['undispositioned_warning_ids']]
        passed = self.run_gate(self.corrected, entries, schematic)
        self.assertEqual((passed['execution'], passed['outcome']), ('ok', 'pass'), passed)
        matched = next(f for f in passed['details']['normalized']['findings'] if f['finding_id'] == fid)
        self.assertEqual(matched['disposition']['finding_id'], fid)
        self.assertEqual(matched['disposition']['raw_output_path'], 'dispositions.json')
        wrong = [{**e, 'finding_id': fid + '-other'} if e['finding_id'] == fid else e for e in entries]
        failed = self.run_gate(self.corrected, wrong, schematic)
        self.assertEqual((failed['execution'], failed['outcome']), ('ok', 'defect'), failed)
        self.assertEqual(failed['details']['normalized']['undispositioned_warning_ids'], [fid])
        self.assertIn(fid + '-other', failed['details']['normalized']['unused_disposition_ids'])


if __name__ == '__main__':
    unittest.main()
