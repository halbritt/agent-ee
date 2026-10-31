"""Real review dispatch and Codex schema serialization, with a scripted process only.

No provider or credential is used. The existing subset matcher checks finding
structure; the real coordinator separately enforces exact citations and meaning.
"""
import copy
import json
import os
import time
import unittest
from unittest.mock import patch

from agent_ee import reviewers
from agent_ee.harnesses import strict_schema
from agent_ee.review_run import structured_call, verify_role_call
from agent_ee.storage import read_json, write_json
from tests import test_findings as fixture
from tests.test_physical_architect import conforms


class ReviewFindingTransportTests(unittest.TestCase):
    _finding = fixture.FindingsTests._finding
    _review = fixture.FindingsTests._review

    def setUp(self):
        fixture.FindingsTests.setUp(self)
        self.packet = dict(candidate_id=self.candidate_id, run_id=self.run_id,
                           packet_sha256=self.packet_sha256)
        write_json(self.run_dir / 'packet.json', self.packet)
        self.schema = copy.deepcopy(reviewers.REVIEW_SCHEMA)
        reviewers.bind_evidence_schema(self.schema, 'findings', self.run_dir,
            dict(citation_files=[dict(path='inputs/project/rc.kicad_sch', locator_kinds=['lines'])]),
            'circuit')
        self.auth = self.root / 'fake-auth'
        self.auth.mkdir()
        (self.auth / 'auth.json').write_text('{}')
        self.definition = dict(harness='codex', model='scripted-schema-test',
                               effort='high', tool_mode='fixed_packet')

    def finding(self, fid='ordinary', severity='major', **extra):
        item = self._finding(fid, assessment='concern', evidence=[
            dict(path='inputs/project/rc.kicad_sch', locator='synthetic schematic', lines=[1, 1])])
        return dict(item, severity=severity, **extra)

    def response(self, findings):
        return dict(self._review(findings=findings), schema='agent-ee.review/2')

    def launch(self, response, *, enforce_schema=True, validate=True):
        """Run reviewer_call -> dispatch -> native_call -> strict_schema unchanged."""
        self.wire_schema = None
        self.process_called = False

        def process(intent, *, work, **kwargs):
            self.process_called = True
            self.wire_schema = read_json(work / 'schema.json')
            if enforce_schema:
                self.assertEqual(conforms(response, self.wire_schema), [])
            events = [dict(type='thread.started', thread_id='8c631d26-38c0-4f7e-83f1-9ba46e75286a'),
                      dict(type='item.completed', item=dict(type='agent_message', text=json.dumps(response))),
                      dict(type='turn.completed', usage={})]
            self.stdout = ''.join(json.dumps(event) + '\n' for event in events)
            (work / 'stdout.log').write_text(self.stdout)
            return dict(execution='ok', exit_code=0)

        def call(prompt):
            return reviewers.reviewer_call(self.run_dir, 'circuit', self.definition, prompt,
                self.schema, 30, launch_context=dict(deadline=time.monotonic() + 30))

        with patch.dict(os.environ, {'AGENT_EE_CODEX_AUTH_HOME': str(self.auth)}), \
                patch('agent_ee.preflight.guard', return_value=(dict(result='ok'), {}, '/usr/bin/true')), \
                patch('agent_ee.native_run.run', side_effect=process) as run:
            if validate:
                result = structured_call(self.run_dir, 'circuit', 'Synthetic transport test.',
                    self.schema, call,
                    lambda value: reviewers.validate_fresh_review(value, self.packet, self.run_dir, 'circuit'))
            else:
                result = call('Synthetic transport test.')
            run.assert_called_once()
            return result

    def test_ordinary_and_purpose_findings_survive_real_codex_dispatch_and_replay(self):
        response = self.response([self.finding(), self.finding('purpose', 'minor', dimension='purpose_review')])
        original = copy.deepcopy(self.schema)
        record, accepted = self.launch(response)
        self.assertEqual(record['execution'], 'ok')
        self.assertEqual(accepted, response)
        self.assertEqual(self.schema, original)
        archive = self.run_dir / record['attempts'][0]['path']
        self.assertEqual(read_json(archive / 'schema.json'), original)
        self.assertEqual(read_json(archive / 'response.json'), response)
        self.assertEqual((archive / 'review-work/circuit/stdout.log').read_text(), self.stdout)
        with patch('agent_ee.preflight.verify_receipt', return_value='scripted-invocation'):
            self.assertEqual(verify_role_call(self.run_dir, record, {}, 'circuit', 'initial', limit=0),
                             {'scripted-invocation'})
        shape = self.wire_schema['properties']['findings']['items']
        for severity in ('blocking', 'major', 'minor', 'info'):
            item = self.finding(severity=severity)
            self.assertEqual(conforms(item, shape), [])
            self.assertNotIn('dimension', item)
        for branch in shape['anyOf']:
            self.assertEqual(set(branch['required']), set(branch['properties']))
            self.assertIs(branch['additionalProperties'], False)

    def test_null_and_prohibited_purpose_findings_are_never_provider_legal(self):
        self.launch(self.response([]))
        shape = self.wire_schema['properties']['findings']['items']
        for item in [self.finding(dimension=None), self.finding(dimension='purpose_review'),
                     self.finding(severity='blocking', dimension='purpose_review'),
                     dict(self.finding(severity='minor', dimension='purpose_review'), assessment='defect')]:
            with self.subTest(item=item):
                self.assertTrue(conforms(item, shape))
                with self.assertRaises(ValueError):
                    reviewers.validate_fresh_review(self.response([item]), self.packet, self.run_dir, 'circuit')

    def test_nonconforming_null_response_remains_malformed_and_retained(self):
        response = self.response([self.finding(dimension=None)])
        record, accepted = self.launch(response, enforce_schema=False)
        self.assertEqual(record['execution'], 'malformed')
        self.assertIsNone(accepted)
        self.assertIn('invalid dimension None', record['error'])
        archive = self.run_dir / record['attempts'][0]['path']
        self.assertEqual(read_json(archive / 'response.json'), response)
        self.assertEqual((archive / 'review-work/circuit/stdout.log').read_text(), self.stdout)

    def test_baseline_witnesses_and_rf_assessment_survive_transport(self):
        from agent_ee.rf_review import ASSESSMENT_KEY, TOPICS, assessment_schema
        item = self.schema['properties']['findings']['items']
        item['properties']['witnesses'] = copy.deepcopy(reviewers.WITNESS_SCHEMA)
        item['required'].append('witnesses')
        self.schema['properties'][ASSESSMENT_KEY] = assessment_schema('a' * 64)
        self.schema['required'].append(ASSESSMENT_KEY)
        response = self.response([self.finding(witnesses=[]),
            self.finding('purpose', 'info', dimension='purpose_review', witnesses=[])])
        response[ASSESSMENT_KEY] = dict(binding_sha256='a' * 64, qualification_claimed=False,
            external_qualification=[], topics=[dict(topic=topic, status='not_applicable',
                summary='Synthetic schema preservation test.', evidence=[], missing_evidence=[]) for topic in TOPICS])
        record, accepted = self.launch(response, validate=False)
        self.assertEqual(record['execution'], 'ok')
        self.assertEqual(accepted, response)
        self.assertEqual(self.wire_schema['properties'][ASSESSMENT_KEY],
                         strict_schema(self.schema)['properties'][ASSESSMENT_KEY])
        for finding in response['findings']:
            missing = {key: value for key, value in finding.items() if key != 'witnesses'}
            self.assertTrue(conforms(missing, self.wire_schema['properties']['findings']['items']))

    def test_exact_citation_membership_and_locator_rules_still_reject(self):
        for label, citation in [
                ('unknown', dict(path='inputs/project/unknown.kicad_sch', lines=[1, 1])),
                ('locator', dict(path='inputs/project/rc.kicad_sch', pointer=''))]:
            with self.subTest(label=label):
                response = self.response([dict(self.finding(), evidence=[dict(citation, locator='wrong')])])
                # Both violate the archived exact policy even if a provider sends them.
                with self.assertRaisesRegex(ValueError, 'frozen producer policy'):
                    reviewers.validate_evidence_paths(response, self.schema)

    def test_expanded_provider_schema_limits_refuse_before_process_launch(self):
        original = copy.deepcopy(self.schema)
        for name, change, message in [
                ('enum_count', dict(enum=[f'choice-{n}' for n in range(600)]), '1000'),
                ('string_count', dict(enum=[str(n).zfill(4) + 'x' * 241 for n in range(248)]), '120000'),
                ('wire_size', dict(description='x' * 70000), '120 KiB')]:
            with self.subTest(limit=name):
                self.schema = copy.deepcopy(original)
                self.schema['properties']['findings']['items']['properties']['title'].update(change)
                self.assertLess(len(json.dumps(strict_schema(self.schema)).encode()), 120 * 1024)
                with self.assertRaisesRegex(ValueError, message):
                    self.launch(self.response([]), validate=False)
                self.assertFalse(self.process_called)


if __name__ == '__main__':
    unittest.main()
