"""ERC and DRC rule severities captured from a pinned project file (AGENTEE-189).

The synthetic repositories are original test data. The committed fome-mc record
is recaptured from the owner's local fome-mc object store at the pinned commit.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agent_ee import rule_severities
from agent_ee.rule_severities import capture, template, validate, verify
from agent_ee.storage import json_bytes, read_json
try:
    from toolchain import fome_mc_store
except ImportError:  # run as tests.test_rule_severities from the repository root
    from tests.toolchain import fome_mc_store

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / 'fixtures/fome-mc/rules'
BASE = ROOT / 'fixtures/netclass-completeness/native.kicad_pro'
COMMIT = 'df304f3321296f7e517e0d2e2aa52e1380d52993'
FOME_MC = Path(os.environ.get('AGENT_EE_FOME_MC_CHECKOUT', str(Path.home() / 'git/fome-mc')))


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, check=True).stdout


class CommittedRecordTests(unittest.TestCase):
    def setUp(self):
        self.record = read_json(RULES / 'severities.json')

    def test_committed_record_matches_the_pinned_fome_mc_blob(self):
        validate(self.record)
        self.assertEqual(self.record['source']['commit'], COMMIT)
        self.assertEqual(self.record['source']['path'], 'fome-mc.kicad_pro')
        status = verify(self.record, fome_mc_store(FOME_MC, COMMIT))
        self.assertEqual(status['status'], 'verified', status)
        self.assertEqual(status['detail'], 'Pinned commit/path/blob and SHA-256 verified offline')

    def test_record_carries_the_fome_mc_ignores_named_by_the_pod_criterion(self):
        ignored = {section: {k for k, v in self.record[section]['severities'].items() if v == 'ignore'}
                   for section in ('erc', 'drc')}
        self.assertLessEqual({'lib_symbol_mismatch', 'endpoint_off_grid'}, ignored['erc'])
        self.assertLessEqual({'missing_courtyard', 'solder_mask_bridge', 'footprint_filters_mismatch'},
                             ignored['drc'])
        # None of the six fab-derived DRC rule types is silenced.
        from agent_ee.fab_profile import NATIVE
        self.assertFalse({kind for _, kind, _ in NATIVE.values()} & ignored['drc'])

    def test_committed_template_is_the_owned_base_plus_the_record(self):
        expected = template(read_json(BASE), self.record)
        expected['meta']['filename'] = 'pod.kicad_pro'
        self.assertEqual((RULES / 'pod.kicad_pro').read_bytes(), json_bytes(expected))
        pod = read_json(RULES / 'pod.kicad_pro')
        self.assertEqual(pod['erc'], dict(rule_severities=self.record['erc']['severities']))
        self.assertEqual(pod['board']['design_settings'],
                         dict(rule_severities=self.record['drc']['severities']))

    def test_changed_record_fails_verification(self):
        fome_mc_store(FOME_MC, COMMIT)
        changed = copy.deepcopy(self.record)
        changed['drc']['severities']['clearance'] = 'warning'
        with self.assertRaisesRegex(ValueError, 'differ from the pinned project file'):
            verify(changed, FOME_MC)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-rule-severities-')
        self.repo = Path(self.tmp.name) / 'design'
        self.repo.mkdir()
        git(self.repo, 'init', '-q')
        git(self.repo, 'config', 'user.email', 'test@example.invalid')
        git(self.repo, 'config', 'user.name', 'test')
        self.project = dict(
            board=dict(design_settings=dict(drc_exclusions=[], rule_severities=dict(
                missing_courtyard='ignore', clearance='error', silk_overlap='warning'))),
            erc=dict(erc_exclusions=[], rule_severities=dict(lib_symbol_issues='ignore', pin_to_pin='warning')),
            meta=dict(filename='design.kicad_pro', version=3))
        self.commit = self.commit_project(self.project)

    def tearDown(self):
        self.tmp.cleanup()

    def commit_project(self, project, message='project'):
        (self.repo / 'design.kicad_pro').write_text(json.dumps(project))
        git(self.repo, 'add', 'design.kicad_pro')
        git(self.repo, 'commit', '-q', '-m', message)
        return git(self.repo, 'rev-parse', 'HEAD').strip()

    def capture(self, commit=None, path='design.kicad_pro'):
        return capture(self.repo, commit or self.commit, path, repository_label='owned-synthetic')

    def test_capture_reads_the_pinned_blob_not_the_working_tree(self):
        record = self.capture()
        (self.repo / 'design.kicad_pro').write_text(json.dumps(dict(erc=dict(rule_severities=dict(
            lib_symbol_issues='error')))))
        self.assertEqual(self.capture(), record)
        self.assertEqual(record['erc'], dict(locator='/erc/rule_severities', severities=dict(
            lib_symbol_issues='ignore', pin_to_pin='warning')))
        self.assertEqual(record['drc']['severities'], dict(clearance='error', missing_courtyard='ignore',
                                                           silk_overlap='warning'))
        self.assertEqual(record['source']['repository'], 'owned-synthetic')
        self.assertEqual(verify(record, self.repo)['status'], 'verified')

    def test_exclusions_are_waivers_and_are_refused(self):
        for section, keys in (('erc', ('erc', 'erc_exclusions')),
                              ('drc', ('board', 'design_settings', 'drc_exclusions'))):
            with self.subTest(section=section):
                project = copy.deepcopy(self.project)
                target = project
                for key in keys[:-1]:
                    target = target[key]
                target[keys[-1]] = [['clearance|1|2|3', 'accepted']]
                commit = self.commit_project(project, section)
                with self.assertRaisesRegex(ValueError, f'{section} exclusions are waivers'):
                    self.capture(commit)

    def test_unpinned_uncommitted_or_malformed_sources_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'full exact commit'):
            self.capture(self.commit[:12])
        with self.assertRaises(subprocess.CalledProcessError):
            self.capture('0' * 40)
        with self.assertRaisesRegex(ValueError, 'not one regular committed file'):
            self.capture(path='absent.kicad_pro')
        with self.assertRaisesRegex(ValueError, 'captured from a .kicad_pro'):
            self.capture(path='design.kicad_sch')
        cases = {'must be one of error, warning, ignore': lambda p: p['erc']['rule_severities'].update(
                     pin_to_pin='info'),
                 'drc rule severities must be a nonempty object': lambda p: p['board'].pop('design_settings')}
        for pattern, mutate in cases.items():
            with self.subTest(pattern=pattern):
                project = copy.deepcopy(self.project)
                mutate(project)
                with self.assertRaisesRegex(ValueError, pattern):
                    self.capture(self.commit_project(project, pattern))

    def test_record_shape_is_exact(self):
        record = self.capture()
        cases = {'exactly schema, source, erc and drc': lambda r: r.update(waivers=[]),
                 'pinned-external citation': lambda r: r['source'].update(kind='local'),
                 'locator must be /erc/rule_severities': lambda r: r['erc'].update(locator='/erc'),
                 'keys must be sorted': lambda r: r['drc'].update(severities=dict(
                     silk_overlap='warning', clearance='error')),
                 'must be one of': lambda r: r['drc']['severities'].update(clearance='off')}
        for pattern, mutate in cases.items():
            with self.subTest(pattern=pattern):
                changed = copy.deepcopy(record)
                mutate(changed)
                with self.assertRaisesRegex(ValueError, pattern):
                    validate(changed)

    def test_template_has_a_single_severity_authority(self):
        record = self.capture()
        base = read_json(BASE)
        produced = template(base, record)
        self.assertNotIn('erc', base)
        self.assertEqual(produced['board']['design_settings'], dict(rule_severities=record['drc']['severities']))
        for keys in (('erc',), ('board', 'design_settings')):
            with self.subTest(keys=keys):
                with self.assertRaisesRegex(ValueError, 'already carries'):
                    template(produced, record)
        excluded = copy.deepcopy(base)
        excluded['board']['design_settings'] = dict(drc_exclusions=[['clearance|1|2|3', '']])
        with self.assertRaisesRegex(ValueError, 'already carries drc'):
            template(excluded, record)

    def test_cli_writes_record_and_template_and_refuses_by_name(self):
        out = Path(self.tmp.name) / 'rules'
        argv = [sys.executable, '-m', 'agent_ee.rule_severities', '--repo', str(self.repo), '--commit',
                self.commit, '--path', 'design.kicad_pro', '--repository-label', 'owned-synthetic',
                '--base', str(BASE), '--out', str(out), '--name', 'pod']
        env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'))
        proc = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['status'], 'captured')
        self.assertEqual(read_json(out / 'severities.json'), self.capture())
        self.assertEqual(read_json(out / 'pod.kicad_pro')['meta']['filename'], 'pod.kicad_pro')
        # An existing output is never overwritten.
        proc = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)['status'], 'refused')
        argv[argv.index('--commit') + 1] = self.commit[:12]
        argv[argv.index('--out') + 1] = str(Path(self.tmp.name) / 'short')
        proc = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(proc.returncode, 2)
        self.assertFalse((Path(self.tmp.name) / 'short').exists())
        self.assertEqual(rule_severities.SCHEMA, json.loads(proc.stdout)['schema'])


if __name__ == '__main__':
    unittest.main()
