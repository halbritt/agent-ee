"""Purpose navigation and advisory retention, without simulated model judgment."""
import io
import json
import shutil
import time
import unittest
from unittest.mock import patch

from agent_ee.candidate import capture
from agent_ee.findings import adjudicate, collect_findings, validate_review
from agent_ee.reviewers import adjudicator_call, build_packet, initial_reviews
from agent_ee.storage import digest, identity, read_json, write_json
from tests import test_findings as fixture
from tests.toolchain import toolchain_test


class PurposeReviewTests(unittest.TestCase):
    setUp = fixture.FindingsTests.setUp
    _review = fixture.FindingsTests._review

    def packet(self, reserve=600):
        # Original illustrative declarations, not imported pod design or native evidence.
        write_json(self.source / 'arrangement.json', {
            'policy': {'obstacles': [{'side': 'B', 'purpose': 'card insertion',
                                     'area_mm2': reserve}], 'fixed_refs': ['J1']},
            'arrangement': {'J1': {'side': 'B', 'pose': 'inboard' if reserve else 'edge'}}})
        self.scope_data['packet_max_bytes'] = 0
        for definition in self.scope_data['review_profile']['reviewers']:
            definition['lens'] = 'Only a custom emphasis.'
        write_json(self.scope_path, self.scope_data)
        self.run_dir = self.root / f'purpose-{reserve}'
        self.run_dir.mkdir()
        capture(self.scope_path, self.run_dir)
        shutil.copytree(self.root / 'run1/checks', self.run_dir / 'checks')
        shutil.copytree(self.root / 'run1/work', self.run_dir / 'work')
        # A drawn illustration, explicitly not a KiCad render qualification.
        image = self.run_dir / 'work/illustration/bottom.svg'
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect width="20" height="30"/></svg>')
        return build_packet(self.run_dir, [self.erc_check, self.rc_check], self.run_id)

    def test_complete_sources_and_images_survive_zero_excerpt_budget(self):
        packet = self.packet()
        purpose = packet['purpose_review']
        self.assertEqual(purpose['authority'], 'advisory')
        self.assertEqual(purpose['empty_field_threshold']['enforcement'], 'advisory')
        self.assertIn('agent-chosen', purpose['empty_field_threshold']['provenance'])
        self.assertEqual(purpose['empty_field_threshold']['area_mm2'], 100)
        sources = {row['path']: row for row in purpose['candidate_sources']}
        captured = read_json(self.run_dir / 'candidate.json')['files']
        self.assertEqual(set(sources), {'inputs/' + rel for rel in captured})
        path = 'inputs/project/arrangement.json'
        self.assertEqual(sources[path]['sha256'], digest(self.run_dir / path))
        self.assertEqual(read_json(self.run_dir / path)['policy']['obstacles'][0]['area_mm2'], 600)
        self.assertIn('work/illustration/bottom.svg', purpose['images'])
        self.assertEqual(len(purpose['questions']), 4)
        self.assertTrue(all(a['bytes_included'] == 0 for a in packet['artifacts']))
        self.assertEqual(packet['checks'], [self.erc_check, self.rc_check])
        self.assertEqual(purpose['measurements'][1]['outcome'], 'defect')
        self.assertEqual(packet['packet_sha256'], identity({k: v for k, v in packet.items() if k != 'packet_sha256'}))
        old_digest = packet['packet_sha256']
        alternative = self.packet(reserve=0)
        self.assertNotEqual(old_digest, alternative['packet_sha256'])
        self.assertNotEqual(sources[path]['sha256'], next(row['sha256'] for row in
                            alternative['purpose_review']['candidate_sources'] if row['path'] == path))

    def test_missing_and_linked_images_are_not_visual_coverage_and_peers_are_absent(self):
        private = self.run_dir / 'review-work/physical/experiments/private.svg'
        private.parent.mkdir(parents=True)
        private.write_text('<svg/>')
        linked = self.run_dir / 'work/rc_transient/link.svg'
        linked.symlink_to(private)
        packet = build_packet(self.run_dir, [self.rc_check], self.run_id)
        purpose = packet['purpose_review']
        self.assertEqual(purpose['images'], [])
        self.assertIn('visual purpose coverage is missing', purpose['limitations'][0])
        self.assertNotIn('review-work/', json.dumps(purpose))
        self.assertNotIn(str(linked.relative_to(self.run_dir)), purpose['images'])
        self.assertTrue(any('symlink' in limit for limit in packet['packet_limits']))

    def test_all_initial_roles_receive_mandatory_purpose_brief_even_with_custom_lens(self):
        packet = self.packet()
        prompts = {}

        def transport(run, role, definition, prompt, schema, timeout, **kwargs):
            prompts[role] = prompt
            return {'execution': 'ok'}, {
                'schema': 'agent-ee.review/2', 'candidate_id': packet['candidate_id'],
                'run_id': packet['run_id'], 'packet_sha256': packet['packet_sha256'],
                'role': role, 'coverage': [], 'limitations': ['Transport contract test only'], 'findings': []}

        with patch('agent_ee.reviewers.reviewer_call', side_effect=transport):
            reviews, records = initial_reviews(self.run_dir, packet, time.monotonic() + 30)
        self.assertEqual(len(reviews), 2, records)
        for prompt in prompts.values():
            self.assertEqual(json.loads(prompt.split('PACKET:\n', 1)[1]), packet)
            for phrase in ('EVERY declared obstacle', 'EVERY empty field', 'EVERY part',
                           'single change', 'estimated saving', 'assessment=concern',
                           'card insertion', 'probe access', 'renders', 'Only a custom emphasis.'):
                self.assertIn(phrase, prompt)
            self.assertNotIn('initial_reviews', prompt)

    def test_failed_measurement_without_output_remains_failed_unknown(self):
        failed = {**self.rc_check, 'execution': 'failed', 'outcome': 'unknown',
                  'evidence': ['work/missing/result.json']}
        packet = build_packet(self.run_dir, [failed], self.run_id)
        indexed = packet['purpose_review']['measurements'][0]
        self.assertEqual((indexed['execution'], indexed['outcome']), ('failed', 'unknown'))
        self.assertEqual(indexed['evidence'], [])
        self.assertEqual(packet['checks'], [failed])
        self.assertTrue(any('artifact unavailable' in limit for limit in packet['packet_limits']))

    def test_adjudicator_receives_savings_retention_and_correctness_preservation_rules(self):
        packet = build_packet(self.run_dir, [], self.run_id)
        prompts = []

        def transport(run, role, definition, prompt, schema, timeout, **kwargs):
            prompts.append(prompt)
            return {'execution': 'ok'}, {'schema': 'agent-ee.decisions/2',
                'candidate_id': packet['candidate_id'], 'run_id': packet['run_id'], 'decisions': []}

        with patch('agent_ee.reviewers.reviewer_call', side_effect=transport):
            record, decisions = adjudicator_call(self.run_dir, packet, [], [], time.monotonic() + 30)
        self.assertIsNotNone(decisions, record)
        self.assertIn('estimated saving, basis and uncertainty', prompts[0])
        self.assertIn('Missing evidence for a purpose saving is a coverage limitation', prompts[0])
        self.assertIn('Never promote an estimated saving', prompts[0])
        self.assertIn('established correctness defect', prompts[0])

    def test_advisory_saving_is_retained_beside_defect_without_weakening_it(self):
        packet = self.packet()
        self.candidate_id, self.packet_sha256 = packet['candidate_id'], packet['packet_sha256']
        evidence = [{'path': 'inputs/project/arrangement.json', 'locator': 'illustrative reserve declaration',
                     'pointer': '/policy/obstacles/0'}]
        waste = {'id': 'purpose-area', 'title': 'Purpose alternative',
                 'location': 'Illustrative J1 bottom insertion reserve',
                 'condition': 'Declared inboard pose compared with the edge alternative',
                 'mechanism': 'Moving insertion off the edge could recover the declared reserve',
                 'assessment': 'concern', 'severity': 'info', 'evidence': evidence,
                 'reasoning': 'Illustrative saving: up to 600 mm2 from the declared reserve; '
                              'edge alternative; unqualified estimate without native or enclosure validation.'}
        review = validate_review({**self._review(findings=[waste]), 'schema': 'agent-ee.review/2'},
                                 self.candidate_id, self.run_id, self.packet_sha256, self.run_dir)
        findings = collect_findings([review], [self.rc_check])
        decisions = {'schema': 'agent-ee.decisions/2', 'candidate_id': self.candidate_id,
                     'run_id': self.run_id, 'decisions': [
                         {'finding_id': 'circuit/purpose-area', 'disposition': 'noted',
                          'reason': 'Illustrative advisory with stated estimate', 'evidence': evidence},
                         {'finding_id': 'check/rc_transient', 'disposition': 'accepted',
                          'reason': 'Failing measured criterion', 'evidence': [
                              {'path': 'checks/rc_transient.json', 'locator': 'failed response', 'pointer': '/details/v_1ms'}]}]}
        retained = {f['id']: f for f in adjudicate(findings, decisions, self.run_dir)}
        self.assertEqual(retained['circuit/purpose-area']['reasoning'], waste['reasoning'])
        self.assertEqual(retained['circuit/purpose-area']['status'], 'noted')
        self.assertEqual(retained['check/rc_transient']['assessment'], 'defect')
        self.assertEqual(retained['check/rc_transient']['status'], 'accepted')
        decisions['decisions'][1]['disposition'] = 'noted'
        with self.assertRaisesRegex(ValueError, "cannot be 'noted'"):
            adjudicate(findings, decisions, self.run_dir)


class PurposeFollowupTests(unittest.TestCase):
    """AGENTEE-385: per-side render coverage, advisory purpose status, recorded coverage."""
    setUp = fixture.FindingsTests.setUp
    _review = fixture.FindingsTests._review
    packet = PurposeReviewTests.packet

    def visuals(self, views, status='available'):
        # Record shape of AGENTEE-382 coordinator imaging (agent-ee.candidate-visuals/1); drawn bytes only.
        images = []
        for view in views:
            path = self.run_dir / 'work/candidate.visuals/native' / f'{view}.svg'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
            images.append({'view': view, 'path': path.relative_to(self.run_dir).as_posix(),
                           'sha256': digest(path), 'layers': None})
        return {'schema': 'agent-ee.candidate-visuals/1', 'status': status, 'images': images}

    def dimension(self, visuals):
        from agent_ee.purpose_review import dimension
        packet = build_packet(self.run_dir, [], self.run_id)
        return dimension(read_json(self.run_dir / 'candidate.json'), packet['citation_files'], [], visuals)

    def test_schematic_or_unlabelled_image_is_not_board_render_coverage(self):
        packet = self.packet()
        purpose = packet['purpose_review']
        self.assertIn('work/illustration/bottom.svg', purpose['images'])
        self.assertEqual(purpose['board_renders'], {'F': [], 'B': []})
        self.assertEqual(len(purpose['limitations']), 2)
        self.assertTrue(all('visual purpose coverage is missing' in text for text in purpose['limitations']))

    def test_board_render_coverage_is_keyed_per_side(self):
        visuals = self.visuals(['top', 'bottom', 'front', 'back', 'In1.Cu', 'In2.Cu'])
        purpose = self.dimension(visuals)
        root = 'work/candidate.visuals/native/'
        self.assertEqual(purpose['board_renders'], {'F': [root + 'front.svg', root + 'top.svg'],
                                                    'B': [root + 'back.svg', root + 'bottom.svg']})
        self.assertEqual(purpose['limitations'], [])
        for views, missing in ((['top', 'front', 'In1.Cu'], 'B'), (['back'], 'F'),
                               (['In1.Cu', 'In2.Cu', 'isometric'], 'FB')):
            for image in (self.run_dir / 'work/candidate.visuals').rglob('*.svg'):
                image.unlink()
            purpose = self.dimension(self.visuals(views))
            self.assertEqual({side for side, paths in purpose['board_renders'].items() if not paths}, set(missing))
            self.assertEqual(len(purpose['limitations']), len(missing))
            for side in missing:
                self.assertTrue(any(f'missing for {side}.' in text for text in purpose['limitations']))

    def test_unavailable_or_unadmitted_renders_cover_no_side(self):
        visuals = self.visuals(['top', 'bottom'])
        self.assertEqual(self.dimension({**visuals, 'status': 'skipped'})['board_renders'], {'F': [], 'B': []})
        private = self.run_dir / 'review-work/physical/experiments/top.svg'
        private.parent.mkdir(parents=True)
        private.write_text('<svg/>')
        peer = {**visuals, 'images': [{'view': 'top', 'path': 'review-work/physical/experiments/top.svg'},
                                      visuals['images'][1]]}
        purpose = self.dimension(peer)
        self.assertEqual(purpose['board_renders']['F'], [])
        self.assertNotIn('review-work/', json.dumps(purpose))
        self.assertEqual(self.dimension(None)['board_renders'], {'F': [], 'B': []})

    def purpose_finding(self, **changes):
        evidence = [{'path': 'inputs/project/arrangement.json', 'locator': 'illustrative reserve declaration',
                     'pointer': '/policy/obstacles/0'}]
        return {'id': 'purpose-area', 'title': 'Purpose alternative', 'location': 'Illustrative J1 reserve',
                'condition': 'Inboard pose compared with the edge alternative',
                'mechanism': 'Edge insertion could recover the declared reserve',
                'assessment': 'concern', 'severity': 'info', 'evidence': evidence, 'dimension': 'purpose_review',
                'reasoning': 'Illustrative saving up to 600 mm2; unqualified estimate.', **changes}

    def validated(self, findings):
        if not hasattr(self, 'sealed'):
            self.sealed = self.packet()
        packet = self.sealed
        self.candidate_id, self.packet_sha256 = packet['candidate_id'], packet['packet_sha256']
        return validate_review({**self._review(findings=findings), 'schema': 'agent-ee.review/2'},
                               self.candidate_id, self.run_id, self.packet_sha256, self.run_dir)

    def test_purpose_dimension_is_limited_to_info_or_minor_concerns(self):
        review = self.validated([self.purpose_finding(severity='minor')])
        self.assertEqual(review['findings'][0]['dimension'], 'purpose_review')
        for changes, message in (({'assessment': 'defect'}, 'must be concern'),
                                 ({'severity': 'major'}, 'must be concern'),
                                 ({'severity': 'blocking'}, 'must be concern'),
                                 ({'dimension': 'correctness'}, 'invalid dimension')):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, message):
                self.validated([self.purpose_finding(**changes)])
            with self.subTest(collect=changes), self.assertRaisesRegex(ValueError, message):
                collect_findings([self._review(findings=[self.purpose_finding(**changes)])], [])

    def adjudicated(self, extra_findings, decisions):
        review = self.validated([self.purpose_finding(), *extra_findings])
        findings = collect_findings([review], [self.rc_check])
        return findings, adjudicate(findings, {'schema': 'agent-ee.decisions/2', 'candidate_id': self.candidate_id,
                                               'run_id': self.run_id, 'decisions': decisions}, self.run_dir)

    def test_deferred_purpose_concern_is_recorded_advisory_and_cannot_hold_outcome(self):
        from agent_ee.cli import outcome, render
        check = [{'finding_id': 'check/rc_transient', 'disposition': 'accepted', 'reason': 'Failing criterion',
                  'evidence': [{'path': 'checks/rc_transient.json', 'locator': 'failed response',
                                'pointer': '/details/v_1ms'}]}]
        deferred = {'finding_id': 'circuit/purpose-area', 'disposition': 'deferred',
                    'reason': 'Saving needs enclosure measurement', 'evidence': []}
        _, retained = self.adjudicated([], [deferred, *check])
        retained = {f['id']: f for f in retained}
        purpose = retained['circuit/purpose-area']
        self.assertEqual((purpose['status'], purpose['purpose_status']), ('deferred', 'advisory'))
        self.assertEqual(purpose['events'][-1]['purpose_status'], 'advisory')
        self.assertNotIn('purpose_status', retained['check/rc_transient'])
        result = {'state': 'complete', 'adjudicated': True,
                  'checks': [{'id': 'erc', 'execution': 'ok', 'outcome': 'pass', 'summary': 'clean'}],
                  'review_executions': [{'execution': 'ok'}, {'execution': 'ok'}], 'reviews': [{}, {}],
                  'findings': [purpose]}
        self.assertEqual(outcome(result), 'criteria_satisfied')
        # Correctness keeps blocking exactly as before: an accepted defect or any other deferral.
        self.assertEqual(outcome({**result, 'findings': [purpose, retained['check/rc_transient']]}),
                         'defects_or_unresolved')
        self.assertEqual(outcome({**result, 'checks': [{'id': 'rc', 'execution': 'ok', 'outcome': 'defect'}]}),
                         'defects_or_unresolved')
        plain = {k: v for k, v in purpose.items() if k not in ('purpose_status', 'dimension')}
        self.assertEqual(outcome({**result, 'findings': [plain]}), 'defects_or_unresolved')
        unresolved = collect_findings([self.validated([self.purpose_finding()])], [])[0]
        self.assertEqual(outcome({**result, 'findings': [unresolved]}), 'defects_or_unresolved')
        run = self.root / 'rendered'
        run.mkdir()
        render(run, {**result, 'outcome': outcome(result), 'run_id': self.run_id, 'reviews': [], 'review_executions': [],
                     'purpose_coverage': [{'role': 'circuit', 'status': 'unknown'}]})
        text = (run / 'REPORT.md').read_text()
        self.assertIn('Purpose status: advisory', text)
        self.assertIn('circuit purpose coverage: unknown', text)

    def test_correctness_concern_cannot_be_duplicated_into_purpose_finding(self):
        concern = {**self.purpose_finding(id='clearance', severity='minor'), 'title': 'Clearance concern'}
        concern.pop('dimension')
        decisions = [{'finding_id': 'circuit/purpose-area', 'disposition': 'deferred', 'reason': 'Unmeasured',
                      'evidence': []},
                     {'finding_id': 'circuit/clearance', 'disposition': 'duplicate', 'reason': 'Same area',
                      'evidence': [], 'duplicate_of': 'circuit/purpose-area'}]
        with self.assertRaisesRegex(ValueError, 'advisory purpose finding'):
            self.adjudicated([concern], decisions)
        decisions[1].update(disposition='deferred')
        decisions[1].pop('duplicate_of')
        _, retained = self.adjudicated([concern], decisions)
        self.assertNotIn('purpose_status', {f['id']: f for f in retained}['circuit/clearance'])

    def test_purpose_coverage_is_recorded_per_review_and_never_inferred(self):
        from agent_ee.purpose_review import coverage, coverage_limitations
        silent = {'role': 'circuit', 'coverage': ['Checked purpose of R1 value'], 'limitations': [], 'findings': []}
        prefixed = {'role': 'physical', 'coverage': ['purpose_review: J1 reserve justified by card insertion'],
                    'limitations': [], 'findings': []}
        limited = {'role': 'rf', 'coverage': [], 'limitations': ['Purpose_Review: no B render'], 'findings': []}
        finding = {'role': 'layout', 'coverage': [], 'limitations': [],
                   'findings': [{'id': 'purpose-area', 'dimension': 'purpose_review'}]}
        records = coverage([silent, prefixed, limited, finding])
        self.assertEqual([r['status'] for r in records], ['unknown', 'addressed', 'addressed', 'addressed'])
        self.assertEqual(records[3]['findings'], ['purpose-area'])
        [limit] = coverage_limitations(records)
        self.assertTrue(limit.startswith('circuit: purpose coverage unknown'))
        self.assertIn('not passed', limit)
        from agent_ee.purpose_review import record_coverage
        result = {'reviews': [silent, prefixed], 'limitations': ['Declared scope limitation']}
        record_coverage(record_coverage(result))
        self.assertEqual(result['purpose_coverage'], records[:2])
        self.assertEqual(result['limitations'], ['Declared scope limitation', limit])

    def test_brief_and_adjudicator_prompt_state_the_advisory_contract(self):
        from agent_ee.purpose_review import BRIEF
        self.assertIn('dimension=purpose_review', BRIEF)
        self.assertIn("'purpose_review:'", BRIEF)
        packet = build_packet(self.run_dir, [], self.run_id)
        prompts = []

        def transport(run, role, definition, prompt, schema, timeout, **kwargs):
            prompts.append(prompt)
            return {'execution': 'ok'}, {'schema': 'agent-ee.decisions/2',
                'candidate_id': packet['candidate_id'], 'run_id': packet['run_id'], 'decisions': []}

        with patch('agent_ee.reviewers.reviewer_call', side_effect=transport):
            adjudicator_call(self.run_dir, packet, [], [], time.monotonic() + 30)
        self.assertIn('advisory purpose status', prompts[0])
        self.assertIn('no correctness finding may be duplicated', prompts[0])


class PurposeDowngradeTests(unittest.TestCase):
    """AGENTEE-385 H2: a validator-downgraded defect never becomes an advisory purpose finding."""
    setUp = PurposeFollowupTests.setUp
    _review = PurposeFollowupTests._review
    packet = PurposeFollowupTests.packet
    purpose_finding = PurposeFollowupTests.purpose_finding
    validated = PurposeFollowupTests.validated

    def test_two_validation_downgrade_cannot_acquire_purpose_status(self):
        from agent_ee.cli import outcome
        raw = self.purpose_finding(assessment='defect', evidence=[], title='Connector enclosure clearance unknown',
                                   mechanism='Mating connector collides with enclosure; functional fit is uncertain')
        raw.pop('dimension')
        downgraded = self.validated([raw])['findings'][0]
        self.assertEqual((downgraded['assessment'], downgraded['downgraded_from']), ('concern', 'defect'))
        relabelled = {**downgraded, 'dimension': 'purpose_review'}
        with self.assertRaisesRegex(ValueError, 'downgraded defect'):
            self.validated([relabelled])
        with self.assertRaisesRegex(ValueError, 'downgraded defect'):
            collect_findings([self._review(findings=[relabelled])], [])
        # A forged collected projection is refused at adjudication, before any status is granted.
        [finding] = collect_findings([self._review(findings=[downgraded])], [])
        decisions = {'schema': 'agent-ee.decisions/2', 'candidate_id': self.candidate_id, 'run_id': self.run_id,
                     'decisions': [{'finding_id': finding['id'], 'disposition': 'deferred',
                                    'reason': 'Need measurement', 'evidence': []}]}
        with self.assertRaisesRegex(ValueError, 'downgraded defect'):
            adjudicate([{**finding, 'dimension': 'purpose_review'}], decisions, self.run_dir)
        [kept] = adjudicate([finding], decisions, self.run_dir)
        self.assertNotIn('purpose_status', kept)
        result = {'state': 'complete', 'adjudicated': True,
                  'checks': [{'id': 'erc', 'execution': 'ok', 'outcome': 'pass', 'summary': 'clean'}],
                  'review_executions': [{'execution': 'ok'}, {'execution': 'ok'}], 'reviews': [{}, {}]}
        self.assertEqual(outcome({**result, 'findings': [kept]}), 'defects_or_unresolved')
        forged = {**kept, 'dimension': 'purpose_review', 'purpose_status': 'advisory'}
        self.assertEqual(outcome({**result, 'findings': [forged]}), 'defects_or_unresolved')
        for key, value in (('severity', 'major'), ('assessment', 'defect'), ('source', 'check')):
            with self.subTest(key=key):
                self.assertEqual(outcome({**result, 'findings': [{**forged, key: value}]}),
                                 'defects_or_unresolved')


class PurposeIntegrityRunTests(unittest.TestCase):
    """AGENTEE-385 H1/H3: verify() re-derives purpose status and coverage from retained records."""

    @classmethod
    def setUpClass(cls):
        from test_concurrent_reviews import ConcurrentReviewTests
        cls.fixture = ConcurrentReviewTests

    def setUp(self):
        self.reviews = self.fixture('setUp')
        self.reviews.setUp()
        self.addCleanup(self.reviews.doCleanups)
        # Scheduling is not under test here; a loaded host must not turn a scripted role into a timeout.
        for definition in (*self.reviews.profile['reviewers'], self.reviews.profile['adjudicator']):
            definition['timeout_s'] = 120
        self.reviews.configure_coordinator_scope()
        self.reviews.scope['skip_visuals'] = True
        self.reviews.settings = {role: {'sleep': 0, 'fail': False} for role in ('circuit', 'physical')}

    def full_run(self, name, *, correctness):
        from test_concurrent_reviews import HARNESS
        from agent_ee.cli import run_review
        purpose = {'id': 'edge-move', 'title': 'Edge alternative', 'location': 'J1', 'condition': 'Placement',
                   'mechanism': 'Edge pose could free the inboard reserve', 'severity': 'info',
                   'assessment': 'concern', 'evidence': [], 'dimension': 'purpose_review',
                   'reasoning': 'Estimated saving unknown; needs enclosure measurement'}
        concern = {'id': 'correctness', 'title': 'Connector clearance uncertain', 'location': 'J1',
                   'condition': 'Mating', 'mechanism': 'Possible collision', 'severity': 'minor',
                   'assessment': 'concern', 'evidence': [], 'reasoning': 'Needs mechanical evidence'}
        addition = (f'\nif role == "physical":\n    response["findings"] = [{purpose!r}]\n'
                    + (f'if role == "circuit":\n    response["findings"] = [{concern!r}]\n' if correctness else ''))
        (self.reviews.root / 'source/harness.py').write_text(HARNESS.replace(
            'print(json.dumps(response), flush=True)', addition + 'print(json.dumps(response), flush=True)'))
        write_json(self.reviews.root / 'scope.json', self.reviews.scope)
        run = self.reviews.root / name

        def dispatch(*args, **kwargs):
            if args[1] != 'adjudicator':
                return self.reviews.harness(*args, **kwargs)
            packet = read_json(run / 'packet.json')
            decisions = [{'finding_id': 'physical/edge-move', 'disposition': 'deferred',
                          'reason': 'Saving needs enclosure measurement', 'evidence': []}]
            if correctness:
                decisions.append({'finding_id': 'circuit/correctness', 'disposition': 'deferred',
                                  'reason': 'Need mechanical measurement', 'evidence': []})
            return {'execution': 'ok'}, {'schema': 'agent-ee.decisions/2', 'candidate_id': packet['candidate_id'],
                                         'run_id': packet['run_id'], 'decisions': decisions}

        with patch('agent_ee.reviewers.dispatch', side_effect=dispatch):
            run_review(self.reviews.root / 'scope.json', run, 600, output_stream=io.StringIO())
        result = read_json(run / 'result.json')
        self.assertNotIn('error', result)
        self.assertEqual(result['state'], 'complete', (result['review_executions'], result['limitations']))
        return run, result

    def refused(self, run, original, mutate, message):
        import copy
        from agent_ee.cli import outcome, verify
        tampered = copy.deepcopy(original)
        mutate(tampered)
        tampered['outcome'] = outcome(tampered)
        write_json(run / 'result.json', tampered)
        try:
            with self.assertRaisesRegex(ValueError, message):
                verify(run)
        finally:
            write_json(run / 'result.json', original)
        return tampered['outcome']

    @toolchain_test
    def test_tampered_purpose_projection_and_coverage_are_refused(self):
        from agent_ee.cli import verify
        run, result = self.full_run('advisory', correctness=False)
        verified = verify(run)
        by_id = {f['id']: f for f in verified['findings']}
        self.assertEqual(by_id['physical/edge-move']['purpose_status'], 'advisory')
        self.assertEqual(verified['outcome'], 'criteria_satisfied')
        self.assertEqual([(r['role'], r['status']) for r in verified['purpose_coverage']],
                         [('circuit', 'unknown'), ('physical', 'addressed')])
        [unknown] = [text for text in verified['limitations'] if 'purpose coverage unknown' in text]
        self.assertTrue(unknown.startswith('circuit:'))

        def index(value, fid):
            return next(i for i, f in enumerate(value['findings']) if f['id'] == fid)

        def purpose(mutate_finding):
            def mutate(value):
                mutate_finding(value['findings'][index(value, 'physical/edge-move')])
            return mutate

        differs = 'purpose finding projection differs'
        for label, mutate in (
                ('major tag', purpose(lambda f: f.update(severity='major'))),
                ('defect tag', purpose(lambda f: f.update(assessment='defect'))),
                ('downgrade marker', purpose(lambda f: f.update(downgraded_from='defect'))),
                ('status removed', purpose(lambda f: f.pop('purpose_status'))),
                ('forged extra event', purpose(lambda f: f['events'].append({**f['events'][-1], 'seq': 2}))),
                ('forged event reason', purpose(lambda f: f['events'][-1].update(reason='Forged'))),
                ('event status removed', purpose(lambda f: f['events'][-1].pop('purpose_status'))),
                ('forged disposition', purpose(lambda f: f.update(disposition='noted', status='noted')))):
            with self.subTest(label):
                self.refused(run, result, mutate, differs)

        def drop_coverage(value):
            value['purpose_coverage'] = []
            value['limitations'] = [text for text in value['limitations'] if 'purpose coverage unknown' not in text]

        def forge_addressed(value):
            value['purpose_coverage'][0]['status'] = 'addressed'
            value['limitations'] = [text for text in value['limitations'] if 'purpose coverage unknown' not in text]

        for label, mutate, message in (
                ('coverage deleted', drop_coverage, 'purpose coverage differs'),
                ('coverage key removed', lambda value: value.pop('purpose_coverage'), 'purpose coverage differs'),
                ('forged addressed', forge_addressed, 'purpose coverage differs'),
                ('forged addressed, incomplete', lambda value: (forge_addressed(value),
                                                                value.update(state='incomplete')),
                 'purpose coverage differs'),
                ('unknown limitation removed', lambda value: value.update(limitations=[
                    text for text in value['limitations'] if 'purpose coverage unknown' not in text]),
                 'omits a purpose coverage unknown limitation')):
            with self.subTest(label):
                self.refused(run, result, mutate, message)
        # The restored result still verifies.
        self.assertEqual(verify(run)['outcome'], 'criteria_satisfied')

        # A correctness deferral holds the outcome; no projection edit can grant it purpose status.
        run2, held = self.full_run('held', correctness=True)
        self.assertEqual(verify(run2)['outcome'], 'defects_or_unresolved')

        def correctness(**fields):
            def mutate(value):
                value['findings'][index(value, 'circuit/correctness')].update(fields)
            return mutate

        def forge_event(value):
            finding = value['findings'][index(value, 'circuit/correctness')]
            finding['events'][-1]['purpose_status'] = 'advisory'
            finding.update(purpose_status='advisory', dimension='purpose_review', decision=finding['events'][-1])

        not_granted = 'purpose status not granted'
        for label, mutate, message in (
                ('purpose_status only', correctness(purpose_status='advisory'), not_granted),
                ('missing retained dimension', correctness(purpose_status='advisory', dimension='purpose_review'),
                 not_granted),
                ('info severity tag', correctness(purpose_status='advisory', dimension='purpose_review',
                                                  severity='info'), not_granted),
                ('forged correctness event', forge_event, not_granted)):
            with self.subTest(label):
                self.refused(run2, held, mutate, message)
        self.assertEqual(verify(run2)['outcome'], 'defects_or_unresolved')
