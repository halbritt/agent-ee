"""Retained build custody; native execution is covered by the public parts/build test."""
import contextlib
import copy
import fcntl
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from agent_ee import build_workflow as build, retained_run
from agent_ee.stage_receipts import tree_identity
from agent_ee.storage import digest, identity, read_json, write_json


class BuildReviewCustodyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.run = self.root / 'build'
        source = self.run / 'source'
        source.mkdir(parents=True)
        (self.run / '.lock').touch()
        (source / 'intent.txt').write_text('Synthetic custody fixture; no native acceptance claim.\n')
        completed = []
        for index, stage in enumerate(build.STAGES[:4]):
            directory = self.run / 'stages' / f'{index + 1:02d}-{stage}'
            candidate = directory / 'candidate'
            candidate.mkdir(parents=True)
            (candidate / 'native.txt').write_text(stage)
            if stage == 'scaffold':
                write_json(directory / 'product/scope.json', dict(schema='agent-ee.scope/3',
                    name='Synthetic custody fixture', checks=[], limitations=['Unqualified.']))
            completed.append(dict(stage=stage, directory=str(directory.relative_to(self.run)),
                candidate=str(candidate.relative_to(self.run)), tree=tree_identity(directory)))
        inputs = dict(plan_sha256='a' * 64, source=build._inventory(source),
                      runtime=dict(version='synthetic-old-builder'))
        self.report = dict(schema=build.REPORT_SCHEMA, identity=identity(inputs), inputs=inputs,
            completed=completed, candidate=completed[-1]['candidate'], status='interrupted', accepted=False)
        self.output = self.root / 'diagnostic'
        self.save()

    def save(self):
        write_json(self.run / 'build-report.json', self.report)

    def refused(self, message):
        with self.assertRaisesRegex(ValueError, message):
            build.review_build(self.run, self.output)
        self.assertFalse(self.output.exists())

    def test_last_completed_candidate_and_generated_scope_are_selected(self):
        unfinished = self.run / 'stages/05-placement-routing/candidate'
        unfinished.mkdir(parents=True)
        (unfinished / 'native.txt').write_text('UNFINISHED')
        before = tree_identity(self.run)
        selected = retained_run.review_source(self.run)
        candidate, scope, selection = selected.candidate, selected.scope, selected.selection
        self.assertEqual(candidate, self.run / self.report['candidate'])
        self.assertEqual((candidate / 'native.txt').read_text(), 'schematic')
        self.assertEqual(scope['limitations'], ['Unqualified.'])
        self.assertEqual(selection['selected'], self.report['completed'][-1])
        self.assertEqual(selection['build_runtime'], self.report['inputs']['runtime'])
        self.assertEqual(selection['build_report_sha256'], digest(self.run / 'build-report.json'))
        self.assertEqual(tree_identity(self.run), before)

    def test_stale_captured_source_or_any_completed_stage_refuses_before_output(self):
        for path, message in [(self.run / 'source/intent.txt', 'source identity changed'),
                              (self.run / self.report['completed'][0]['candidate'] / 'native.txt', 'stage identity changed'),
                              (self.run / self.report['candidate'] / 'native.txt', 'stage identity changed')]:
            original = path.read_bytes()
            path.write_bytes(original + b'changed')
            with self.subTest(path=path):
                self.refused(message)
            path.write_bytes(original)

    def test_incomplete_reordered_and_aliased_candidates_refuse(self):
        original = copy.deepcopy(self.report)
        cases = [
            (lambda r: r.update(completed=r['completed'][:3]), 'completed native schematic'),
            (lambda r: r['completed'][1].update(stage='schematic'), 'stage identity changed'),
            (lambda r: r.update(candidate=r['completed'][0]['candidate']), 'last completed stage'),
            (lambda r: r['completed'][-1].update(candidate=r['completed'][0]['candidate']), 'outside its completed stage'),
            (lambda r: r['completed'][-1].update(directory='../foreign'), 'relative path'),
            (lambda r: r.update(identity='b' * 64), 'source identity changed')]
        for mutate, message in cases:
            self.report = copy.deepcopy(original)
            mutate(self.report)
            self.save()
            with self.subTest(message=message):
                self.refused(message)

    def test_stage_link_cannot_redirect_review_outside_build(self):
        foreign = self.root / 'foreign'
        foreign.mkdir()
        (self.run / 'linked').symlink_to(foreign, target_is_directory=True)
        self.report['completed'][-1]['directory'] = 'linked'
        self.save()
        self.refused('escapes its run')

    def test_active_build_writer_prevents_review(self):
        with (self.run / '.lock').open('rb') as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.refused('another writer owns the build')
        # Failure releases the reader descriptor, without removing the lease.
        with (self.run / '.lock').open('rb') as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_output_cannot_change_build_or_overwrite_existing_evidence(self):
        with self.assertRaisesRegex(ValueError, 'outside the retained build'):
            build.review_build(self.run, self.run / 'diagnostic')
        self.assertFalse((self.run / 'diagnostic').exists())
        self.output.mkdir()
        marker = self.output / 'keep.txt'
        marker.write_bytes(b'original evidence')
        with self.assertRaises(FileExistsError):
            build.review_build(self.run, self.output)
        self.assertEqual(marker.read_bytes(), b'original evidence')

    def test_provenance_namespace_cannot_shadow_candidate_data(self):
        candidate = self.run / self.report['candidate']
        (candidate / 'build-review').mkdir()
        row = self.report['completed'][-1]
        row['tree'] = tree_identity(self.run / row['directory'])
        self.save()
        self.refused('reserved build-review')

    def test_timeout_rejects_booleans_and_unbounded_values(self):
        for value in [True, 0, -1, 86401, 1.5, float('inf')]:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'max-seconds'):
                build.review_build(self.run, self.output, max_seconds=value)
        self.assertFalse(self.output.exists())


class BuildFullReviewTests(unittest.TestCase):
    """Wiring and projection of the ordinary full review; reviewers are scripted, not launched."""
    save = BuildReviewCustodyTests.save

    def setUp(self):
        BuildReviewCustodyTests.setUp(self)
        self.profile = self.root / 'profile.json'
        self.profile.write_text('{"schema": "agent-ee.profile/1"}\n')
        self.calls = []
        self.scripted = dict(outcome='criteria_satisfied', executions=['ok', 'ok'], adjudicated=True)

    def fake_run_review(self, path, out, timeout, *, checks_only, profile, output_stream):
        self.calls.append(dict(checks_only=checks_only, profile=profile, timeout=timeout))
        scope = read_json(path)
        board = Path(next(row['path'] for row in scope['inputs'] if row['at'] == 'native.txt'))
        out.mkdir()
        write_json(out / 'candidate.json', dict(scope=dict(scope, board='native.txt'),
                                                files={'native.txt': digest(board)}))
        reviews = [] if checks_only else [dict(role='circuit'), dict(role='physical')]
        executions = [] if checks_only else [dict(role=r['role'], execution=e)
                                             for r, e in zip(reviews, self.scripted['executions'])]
        write_json(out / 'result.json', dict(candidate_id='c' * 64, checks=[], limitations=[],
            reviews=reviews, review_executions=executions, adjudicated=not checks_only and self.scripted['adjudicated'],
            outcome='incomplete' if checks_only else self.scripted['outcome']))

    def review(self, **kwargs):
        before = tree_identity(self.run)
        with mock.patch.object(retained_run, 'run_review', self.fake_run_review), \
                mock.patch.object(retained_run, 'verify', lambda review: read_json(review / 'result.json')):
            report = build.review_build(self.run, self.output, **kwargs)
        self.assertEqual(tree_identity(self.run), before)
        self.assertFalse(report['accepted'])
        qualified = read_json(self.output / 'qualification.json')
        self.assertFalse(qualified['accepted'])
        self.assertEqual(qualified['dimensions']['file_release']['status'], 'not_assessed')
        return report, qualified['dimensions']['engineering_review']

    def test_full_review_projects_actual_outcome_bound_to_selected_candidate(self):
        for outcome, status in [('criteria_satisfied', 'pass'), ('defects_or_unresolved', 'failed')]:
            self.scripted['outcome'] = outcome
            with self.subTest(outcome=outcome):
                report, row = self.review(profile=self.profile)
                self.assertEqual(self.calls[-1], dict(checks_only=False, profile=str(self.profile), timeout=900))
                self.assertEqual(report['review_profile'], str(self.profile))
                self.assertEqual(row['status'], status)
                candidate = self.run / self.report['candidate']
                self.assertEqual(row['binding'], dict(board_sha256=digest(candidate / 'native.txt'),
                    scope_sha256=report['source']['scope_sha256'], stage='schematic'))
                self.assertEqual(row['evidence'][0]['path'], 'review/result.json')
                shutil.rmtree(self.output)

    def test_failed_or_missing_reviewer_or_adjudication_stays_unassessed(self):
        for scripted in [dict(executions=['ok', 'failed']), dict(executions=['ok']),
                         dict(adjudicated=False), dict(outcome='incomplete')]:
            self.scripted = dict(outcome='criteria_satisfied', executions=['ok', 'ok'], adjudicated=True) | scripted
            with self.subTest(scripted=scripted):
                report, row = self.review(profile=self.profile)
                self.assertEqual(row['status'], 'not_assessed')
                self.assertNotEqual(report['qualification']['dimensions']['engineering_review'], 'pass')
                shutil.rmtree(self.output)

    def test_default_remains_checks_only_without_engineering_projection(self):
        report, row = self.review()
        self.assertEqual(self.calls, [dict(checks_only=True, profile=None, timeout=900)])
        self.assertNotIn('review_profile', report)
        self.assertEqual(row, dict(status='not_assessed', evidence=[],
                                   binding=dict(board_sha256=None, scope_sha256=None, stage=None)))

    def test_missing_profile_refuses_before_output(self):
        with self.assertRaisesRegex(ValueError, 'existing agent-ee.profile'):
            build.review_build(self.run, self.output, profile=self.root / 'absent.json')
        self.assertFalse(self.output.exists())
        self.assertEqual(self.calls, [])

    def test_cli_passes_explicit_profile_and_prints_engineering_dimension(self):
        import argparse
        parser = argparse.ArgumentParser()
        build.configure_parser(parser)
        args = parser.parse_args(['review', str(self.run), '--out', str(self.output),
                                  '--profile', str(self.profile), '--max-seconds', '60'])
        self.assertEqual(parser.parse_args(['review', str(self.run), '--out', str(self.output)]).profile, None)
        printed = io.StringIO()
        with mock.patch.object(retained_run, 'run_review', self.fake_run_review), \
                mock.patch.object(retained_run, 'verify', lambda review: read_json(review / 'result.json')), \
                contextlib.redirect_stdout(printed):
            status = build.execute(args)
        self.assertEqual(status, 0)
        self.assertEqual(self.calls, [dict(checks_only=False, profile=str(self.profile.resolve()), timeout=60)])
        shown = json.loads(printed.getvalue())
        self.assertFalse(shown['accepted'])
        self.assertEqual(shown['engineering_review'], 'pass')
