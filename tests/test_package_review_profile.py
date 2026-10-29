"""A plan-requested full package review: pinned profile, stage binding and combined acceptance."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from agent_ee import build_plan, build_stages, build_workflow as build, qualification, retained_run
from agent_ee.retained_run import CheckedReview
from agent_ee.storage import digest, identity, read_json, write_json
from tests import test_qualification
from tests.test_build_plan import declaration

PROFILE = (Path(__file__).resolve().parents[1] / 'profiles/claude.json').read_bytes()


def typed(**extra):
    plan = declaration()
    plan['schema'] = build_plan.TYPED_SCHEMA
    plan['requirements']['obligations'] = 'obligations.json'
    plan.update(extra)
    return plan


class PlanProfileTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / 'inputs/review').mkdir(parents=True)
        (self.root / 'inputs/review/profile.json').write_bytes(PROFILE)

    def admit(self, plan):
        write_json(self.root / 'plan.json', plan)
        return build_plan.admit_source(self.root / 'plan.json', self.root / 'run')

    def test_profile_is_a_typed_ordinary_input_outside_reserved_namespaces(self):
        build_plan.validate_plan(typed(review_profile='review/profile.json'))
        with self.assertRaisesRegex(ValueError, 'typed build-plan/2'):
            build_plan.validate_plan(dict(declaration(), review_profile='review/profile.json'))
        for value, message in [(None, 'source-relative path'), ('../profile.json', ''), ('/abs/profile.json', ''),
                               ('build-generated/p.json', 'generated namespace'),
                               ('requirements-map/p.json', 'generated namespace')]:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, message):
                build_plan.validate_plan(typed(review_profile=value))
        with self.assertRaisesRegex(ValueError, 'judgments directory'):
            build_plan.validate_plan(typed(judgments='judgments', review_profile='judgments/profile.json'))

    def test_admission_validates_the_pinned_profile_bytes(self):
        files = self.admit(typed(review_profile='review/profile.json'))[-1]
        self.assertEqual(files['review/profile.json'], digest(self.root / 'inputs/review/profile.json'))
        before = identity(files)
        changed = json.loads(PROFILE)
        changed['adjudicator']['timeout_s'] = changed['adjudicator']['timeout_s'] + 1
        write_json(self.root / 'inputs/review/profile.json', changed)
        # Changed profile bytes change the source pin, so resume refuses this invocation identity.
        self.assertNotEqual(identity(self.admit(typed(review_profile='review/profile.json'))[-1]), before)
        for mutate, message in [
                (lambda p: p.write_text('{"schema": "agent-ee.profile/1"}'), ''),
                (lambda p: p.unlink(), 'not a captured regular source file'),
                (lambda p: (p.unlink(), p.symlink_to('../../plan.json')), 'links or special files')]:
            path = self.root / 'inputs/review/profile.json'
            if path.exists() or path.is_symlink():
                path.unlink()
            path.write_bytes(PROFILE)
            mutate(path)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.admit(typed(review_profile='review/profile.json'))

    def test_package_reviews_with_the_retained_profile_only_when_requested(self):
        out = self.root / 'run'
        calls = []
        attempt = SimpleNamespace(name='package', directory=out / 'stages/07-package', root=out,
                                  capture=lambda previous: previous)
        with mock.patch.object(retained_run, 'scope', return_value=SimpleNamespace(document={})), \
                mock.patch.object(retained_run, 'accounting', return_value=dict(requirements=[])), \
                mock.patch.object(build_stages, '_package', lambda *args: calls.append(args[5:])):
            for plan in (typed(), typed(review_profile='review/profile.json')):
                build_stages.execute(attempt, plan, self.root / 'candidate')
        self.assertEqual(calls, [(None,), (str(out / 'source/review/profile.json'),)])
        reviewed = []
        with mock.patch.object(retained_run, 'review_candidate',
                               lambda scope, directory, timeout, profile=None: reviewed.append(profile) or {}), \
                mock.patch.object(qualification, 'manufacturing_coverage'), \
                mock.patch.object(qualification, 'package_artifacts', return_value={}):
            stage = self.root / 'stage'
            stage.mkdir()
            write_json(stage / 'judgment-inventory.json', {})
            (self.root / 'candidate').mkdir()
            build_stages._package(typed(), self.root / 'candidate', stage, {}, [])
            build_stages._package(typed(), self.root / 'candidate', stage, {}, [], 'profile.json')
        self.assertEqual(reviewed, [None, 'profile.json'])


class RequestedReviewQualificationTests(unittest.TestCase):
    setUp = test_qualification.QualificationTests.setUp
    save = test_qualification.QualificationTests.save
    complete_control = test_qualification.QualificationTests.complete_control

    def request(self, profile=True):
        self.complete_control(typed=True)
        if profile:
            (self.root / 'source/profile.json').write_bytes(PROFILE)
            plan = read_json(self.root / 'build-plan.json')
            plan['review_profile'] = 'profile.json'
            write_json(self.root / 'build-plan.json', plan)
            self.report['inputs']['plan_sha256'] = digest(self.root / 'build-plan.json')
            self.report['inputs']['source'] = build._inventory(self.root / 'source')
            self.report['identity'] = identity(self.report['inputs'])
        self.save()

    def review(self, outcome='criteria_satisfied', executions=('ok', 'ok'), adjudicated=True, profile=PROFILE,
               stage='package'):
        directory = self.root / 'scripted-review'
        write_json(directory / 'review/candidate.json', dict(profile_sha256=hashlib.sha256(profile).hexdigest()))
        result = dict(checks=[], outcome=outcome, adjudicated=adjudicated,
                      reviews=[dict(role='circuit'), dict(role='physical')],
                      review_executions=[dict(execution=e) for e in executions])
        write_json(directory / 'review/result.json', result)
        return CheckedReview(directory, result, dict(checks=[]),
                             dict(board_sha256='b' * 64, scope_sha256='s' * 64, stage=stage), digest(directory / 'review/result.json'))

    def assess(self, review, *, file_release='pass'):
        real = qualification._record

        def record(mode, dimensions, **kwargs):
            # Stand-in for separately tested, successful native file evidence.
            dimensions['file_release'] = qualification._dimension(file_release)
            return real(mode, dimensions, **kwargs)
        with mock.patch.object(retained_run, 'checked_review', return_value=review), \
                mock.patch.object(retained_run, 'verify_review'), \
                mock.patch.object(qualification, '_record', record):
            return build._assess_report(self.root, copy.deepcopy(self.report), typed=True)

    def test_default_file_only_acceptance_is_unchanged(self):
        self.request(profile=False)
        report = self.assess(None)
        self.assertTrue(report['accepted'])
        self.assertEqual(report['status'], 'accepted')
        self.assertNotIn('required', report['qualification'])
        self.assertEqual(report['qualification']['dimensions']['engineering_review'], 'not_assessed')

    def test_requested_complete_review_is_required_for_acceptance(self):
        self.request()
        report = self.assess(self.review())
        self.assertTrue(report['accepted'])
        self.assertEqual(report['status'], 'accepted')
        self.assertEqual(report['qualification']['required'], ['file_release', 'engineering_review'])
        row = read_json(self.root / 'qualification.json')['dimensions']['engineering_review']
        self.assertEqual((row['status'], row['binding']['stage']), ('pass', 'package'))

    def test_defect_incomplete_or_absent_review_withholds_acceptance_but_keeps_file_release(self):
        self.request()
        for review, status in [(self.review('defects_or_unresolved'), 'failed'),
                               (self.review(executions=('ok', 'failed'), outcome='incomplete'), 'not_assessed'),
                               (self.review(adjudicated=False), 'not_assessed'),
                               (self.review(executions=('ok',)), 'not_assessed'),
                               (self.review(stage='artwork'), 'not_assessed'),
                               (None, 'not_assessed')]:
            with self.subTest(status=status, review=review and review.result['outcome']):
                report = self.assess(review)
                self.assertFalse(report['accepted'])
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(report['reason'], 'Requested engineering review is ' + status)
                dimensions = report['qualification']['dimensions']
                self.assertEqual((dimensions['file_release'], dimensions['engineering_review']), ('pass', status))
                self.assertEqual((dimensions['supplier'], dimensions['physical']), ('pending', 'pending'))
        report = self.assess(self.review(), file_release='pending')
        self.assertEqual((report['accepted'], report['status']), (False, 'pending'))

    def test_review_under_another_profile_is_refused(self):
        self.request()
        report = self.assess(self.review(profile=PROFILE + b' '))
        self.assertFalse(report['accepted'])
        self.assertIn('package review profile differs from the captured build profile', report['reason'])
