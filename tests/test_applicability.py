"""Exact inventories are intentionally independent of design topology."""
import unittest

from agent_ee.applicability import design_inputs


class InventoryTests(unittest.TestCase):
    def test_only_validated_closure_is_excluded(self):
        files = {'board': 'a', 'model': 'b', 'empirical/extra': 'c',
                 'empirical/calibration.json': 'd', 'empirical/artifacts/raw': 'e'}
        self.assertEqual(design_inputs(files, {'model': 'b'},
                         {'empirical/calibration.json', 'empirical/artifacts/raw'}),
                         {'board': 'a', 'empirical/extra': 'c'})

import copy
import tempfile
import time
from pathlib import Path

from agent_ee.applicability import ApplicabilityUnavailable, derivation, obligation, evaluate
from agent_ee.baseline import compile_baseline
from agent_ee.checks import Ctx, CheckError
from agent_ee.empirical import calibration_for, retain_artifact
from agent_ee.measurements import finish
from agent_ee.storage import digest, identity, read_json, write_json


class ExactCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name)
        self.inputs = self.run / 'inputs'
        self.inputs.mkdir()
        for path in ('board', 'schematic', 'library', 'misc', 'model', 'requirements', 'targets', 'reference'):
            (self.inputs / path).write_text(path)
        self.scope = {'schema': 'agent-ee.scope/2', 'empirical': 'empirical/calibration.json',
            'board': 'board', 'schematic': 'schematic',
            'checks': [{'id': 'thermal', 'kind': 'rc_transient', 'params': {'calibration_inputs': ['model']}}],
            'baseline': {'requirements': [{'id': 'R1', 'statement': 'Bounded output',
                'source': {'path': 'requirements', 'locator': 'R1'}, 'checks': ['thermal']}],
                'targets': ['targets'], 'reference_architectures': ['reference']}}
        source_scope = {k: v for k, v in self.scope.items() if k != 'empirical'}
        self.files = {p.name: digest(p) for p in self.inputs.iterdir()}
        self.baseline = compile_baseline(self.scope, self.files)
        inventory = design_inputs(self.files, self.baseline['files'], set())
        source_id = identity({'scope': source_scope, 'files': self.files})
        sample = {'check_id': 'thermal', 'metric': 'peak', 'unit': 'degC', 'condition': 'load',
                  'value': 106, 'uncertainty': 2}
        hashes = {}
        out = self.inputs / 'empirical'
        out.mkdir()
        raw = self.run / 'raw.json'
        write_json(raw, {'samples': [sample]})
        raw_ref = retain_artifact(raw, out, hashes)
        report = self.run / 'report.json'
        write_json(report, {'schema': 'agent-ee.bench/1', 'candidate_id': source_id,
            'prototype': 'synthetic', 'instrument': 'synthetic', 'instrument_calibration': 'synthetic',
            'procedure': 'synthetic coordinator test', 'observed_utc': '2026-10-17', 'observations': [
            {**sample, 'artifact': 'raw.json', 'locator': '0'}],
            'artifacts': [{'path': 'raw.json', 'sha256': digest(raw)}]})
        report_ref = retain_artifact(report, out, hashes)
        source = self.run / 'source.json'
        write_json(source, {'schema': 'agent-ee.predictions/2', 'candidate_id': source_id,
            'scope': source_scope, 'files': self.files, 'baseline_id': self.baseline['baseline_id'],
            'baseline_files': self.baseline['files'], 'criteria': self.baseline['criteria'],
            'empirical': None, 'design_inputs': inventory, 'derivation': derivation(),
            'source_result_sha256': 'retained-source-result', 'checks': [{'id': 'thermal',
                'execution': 'ok', 'details': {'metrics': {'peak': {
                    'value': 95, 'unit': 'degC', 'condition': 'load'}}}}]})
        source_ref = retain_artifact(source, out, hashes)
        observation = {**sample, 'artifact': raw_ref, 'locator': '0', 'report': report_ref,
            'source_result': source_ref, 'candidate_id': source_id, 'prediction': 95,
            'residual_lower': 9, 'residual_upper': 13, 'model_inputs': {'model': self.files['model']},
            'derivation': derivation(), 'applicability': {'policy': 'exact-design/1',
                'design_inputs': inventory, 'dependency_inventory_id': identity(inventory)}}
        self.bundle = {'schema': 'agent-ee.calibration/2', 'candidate_id': source_id,
            'baseline_id': self.baseline['baseline_id'], 'source_result_sha256': 'retained-source-result',
            'parent': None, 'source': source_ref, 'report': report_ref, 'artifacts': hashes,
            'model_inputs': {'model': self.files['model']}, 'observations': [observation],
            'obligations': [obligation(observation)]}
        self.save()

    def save(self):
        write_json(self.inputs / 'empirical/calibration.json', self.bundle)
        self.files = {p.relative_to(self.inputs).as_posix(): digest(p)
                      for p in self.inputs.rglob('*') if p.is_file()}
        write_json(self.run / 'baseline.json', self.baseline)
        write_json(self.run / 'candidate.json', {'scope': self.scope, 'files': self.files})

    def test_unchanged_design_replays_exact_interval(self):
        ctx = Ctx(self.run, {'scope': self.scope, 'files': self.files, 'candidate_id': 'current'},
                  {'id': 'thermal', 'kind': 'rc_transient'}, time.monotonic() + 10)
        finish(ctx, {'peak': {'value': 95, 'unit': 'degC', 'condition': 'load'}},
               {'peak': {'unit': 'degC', 'condition': 'load', 'max': 110}})
        self.assertEqual(ctx.result['outcome'], 'pass')
        metric = ctx.result['details']['metrics']['peak']
        self.assertEqual((metric['lower'], metric['upper']), (104, 108))
        self.assertEqual(ctx.result['details']['margins']['peak']['max'], 2)

    def test_every_design_dependency_addition_and_removal_is_unavailable(self):
        for path in ('board', 'schematic', 'library', 'misc', 'empirical/unclassified'):
            for action in (('change',) if path == 'empirical/unclassified' else ('change', 'remove')):
                with self.subTest(path=path, action=action):
                    files = {**self.files, path: 'changed-bytes'}
                    if action == 'remove':
                        files.pop(path)
                    with self.assertRaises(ApplicabilityUnavailable) as caught:
                        calibration_for(self.run, self.scope, files)
                    self.assertIn(path, [d['path'] for d in caught.exception.diagnostic['differences']])

    def test_unavailable_retains_nominal_prediction_and_structured_request(self):
        ctx = Ctx(self.run, {'scope': self.scope, 'files': {**self.files, 'board': 'new'},
                            'candidate_id': 'changed'}, {'id': 'thermal', 'kind': 'rc_transient'},
                  time.monotonic() + 10)
        with self.assertRaises(CheckError) as caught:
            finish(ctx, {'peak': {'value': 95, 'unit': 'degC', 'condition': 'load'}},
                   {'peak': {'unit': 'degC', 'condition': 'load', 'max': 110}})
        self.assertEqual(caught.exception.execution, 'unavailable')
        self.assertEqual(ctx.result['details']['metrics']['peak']['value'], 95)
        self.assertNotIn('margins', ctx.result['details'])
        request = read_json(ctx.work / 'recalibration-request.json')
        self.assertEqual(request['candidate_id'], 'changed')
        self.assertEqual(request['required_observations'], [{'check_id': 'thermal', 'metric': 'peak',
                         'unit': 'degC', 'condition': 'load'}])
        self.assertEqual(request['differences'][0]['path'], 'board')

    def test_omitted_observation_or_forged_exclusion_fails_closed(self):
        for mutation in ('omit', 'extra', 'model', 'metric', 'unit', 'condition', 'baseline', 'prediction'):
            with self.subTest(mutation=mutation):
                original = copy.deepcopy(self.bundle)
                if mutation == 'omit':
                    self.bundle['observations'] = []
                elif mutation == 'extra':
                    self.bundle['artifacts']['board'] = self.files['board']
                elif mutation == 'model':
                    self.bundle['model_inputs'] = {}
                elif mutation == 'baseline':
                    self.bundle['baseline_id'] = 'other'
                elif mutation == 'prediction':
                    self.bundle['observations'][0]['prediction'] = 94
                else:
                    self.bundle['observations'][0][mutation] = 'other'
                self.save()
                with self.assertRaises(ValueError):
                    calibration_for(self.run, self.scope, self.files)
                self.bundle = original
                self.save()

    def test_tampered_raw_and_changed_model_fail_closed(self):
        for path in ('model', self.bundle['observations'][0]['artifact']):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    calibration_for(self.run, self.scope, {**self.files, path: 'tampered'})
        raw = self.inputs / self.bundle['observations'][0]['artifact']
        write_json(raw, {'samples': []})
        with self.assertRaises(ValueError):
            calibration_for(self.run, self.scope, self.files)

    def test_legacy_bundle_does_not_gain_strict_qualification(self):
        legacy = {k: v for k, v in self.bundle.items() if k not in
                  ('parent', 'source', 'report', 'obligations')}
        legacy['schema'] = 'agent-ee.calibration/1'
        write_json(self.inputs / 'empirical/calibration.json', legacy)
        self.assertEqual(calibration_for(self.run, self.scope, self.files)['schema'],
                         'agent-ee.calibration/1')
        with self.assertRaisesRegex(ValueError, 'migration'):
            calibration_for(self.run, {**self.scope, 'schema': 'agent-ee.scope/3'}, self.files)

    def test_metric_identity_and_model_cannot_be_relabelled(self):
        for field in ('unit', 'condition'):
            ctx = Ctx(self.run, {'scope': self.scope, 'files': self.files, 'candidate_id': 'current'},
                      {'id': 'thermal', 'kind': 'rc_transient'}, time.monotonic() + 10)
            metric = {'value': 95, 'unit': 'degC', 'condition': 'load', field: 'other'}
            with self.assertRaisesRegex(ValueError, 'unit|condition'):
                finish(ctx, {'peak': metric}, {'peak': {'unit': 'degC', 'condition': 'load', 'max': 110}})

    def test_source_prediction_report_and_duplicate_correction_fail_closed(self):
        for key in ('source', 'report'):
            path = self.inputs / self.bundle[key]
            original = path.read_bytes()
            path.write_text('{}')
            with self.assertRaises(ValueError):
                calibration_for(self.run, self.scope, self.files)
            path.write_bytes(original)
        self.bundle['observations'].append(copy.deepcopy(self.bundle['observations'][0]))
        self.save()
        with self.assertRaises(ValueError):
            calibration_for(self.run, self.scope, self.files)

    def test_runtime_and_parser_identity_are_captured(self):
        provenance = self.bundle['observations'][0]['derivation']
        self.assertEqual(provenance['executables']['python3']['sha256'], digest('/usr/bin/python3'))
        self.assertIn('checks.py', provenance['coordinator'])
        self.assertIn('ngspice', provenance['executables'])

    def test_old_derivation_or_executable_identity_cannot_gain_current_applicability(self):
        for section in ('coordinator', 'executables'):
            with self.subTest(section=section):
                historical = copy.deepcopy(self.bundle)
                historical['observations'][0]['derivation'][section] = {}
                with self.assertRaisesRegex(ValueError, 'derivation identity changed'):
                    evaluate(historical, self.files, self.baseline, self.scope['empirical'])
