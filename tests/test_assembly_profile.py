"""Explicit assembly-profile orientation assessment (AGENTEE-524 part B) on the owned CPL control."""
import copy
import json
import shutil
import time
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from agent_ee import checks, cpl_rotation, fab_package
from agent_ee.assembly_profile import (ASSESSMENT_SCHEMA, POSITION_CONVENTION, SCHEMA, mount_types,
                                       orientation_assessment, unassessed_record, validate_profile)
from agent_ee.baseline import compile_baseline, freeze_evidence, verify_evidence
from agent_ee.candidate import capture
from agent_ee.checks import run_checks
from agent_ee.storage import digest, read_json, write_json
from tests.receipt_diagnostics import ReceiptTemporaryDirectory
from tests.test_fab_package import CPL_FIXTURE, rotation_scope
from tests.toolchain import require_toolchain

PROFILE_REL = 'design/assembly-profile.json'


def profile_document(policy_sha256, sides=('Top', 'Bottom'), mount=('smd', 'through_hole'), **overrides):
    document = {'schema': SCHEMA, 'profile_id': 'jlcpcb-cpl-software/1', 'supplier': 'JLCPCB',
                'service': 'software CPL orientation assessment (test)', 'rotation_check': 'cpl',
                'policy_sha256': policy_sha256, 'applicability': {'sides': list(sides), 'mount': list(mount)},
                'position_convention': dict(POSITION_CONVENTION),
                'sources': ['docs/cpl-rotation.md', 'owned synthetic fixture pin-1 frames'],
                'rationale': 'ENGINEERING RATIONALE (test fixture): applicability chosen to exercise the record'}
    document.update(overrides)
    return document


class ProfileValidationTests(unittest.TestCase):
    """P3 (static): unknown, malformed or unsupported profiles refuse."""

    def setUp(self):
        self.good = profile_document('0' * 64)

    def test_valid_profile_passes_and_every_defect_names_itself(self):
        self.assertEqual(validate_profile(copy.deepcopy(self.good)), self.good)
        cases = {
            'unknown profile': ({'profile_id': 'jlcpcb-smt-cpl/1'}, 'unsupported assembly profile'),
            'wrong schema': ({'schema': 'agent-ee.assembly-profile/2'}, 'schema must be'),
            'bad sha': ({'policy_sha256': 'abc'}, 'policy_sha256'),
            'extra field': ({'extra': 1}, 'fields must be exactly'),
            'empty sides': ({'applicability': {'sides': [], 'mount': ['smd']}}, 'applicability.sides'),
            'unknown mount': ({'applicability': {'sides': ['Top'], 'mount': ['tht']}}, 'applicability.mount'),
            'duplicate side': ({'applicability': {'sides': ['Top', 'Top'], 'mount': ['smd']}}, 'applicability.sides'),
            'other convention': ({'position_convention': {**POSITION_CONVENTION, 'anchor': 'footprint_anchor'}},
                                 'position_convention'),
            'no sources': ({'sources': []}, 'sources'),
            'blank rationale': ({'rationale': ' '}, 'rationale'),
        }
        for name, (change, message) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(ValueError, message):
                validate_profile({**copy.deepcopy(self.good), **change})

    def test_mount_types_come_from_plated_pads_and_refuse_unknown_attributes(self):
        pads = [{'ref': 'R1', 'pad': '1', 'attribute': 'SMD'}, {'ref': 'J1', 'pad': '1', 'attribute': 'PTH'},
                {'ref': 'J1', 'pad': '2', 'attribute': 'SMD'}, {'ref': 'H1', 'pad': '', 'attribute': 'NPTH'}]
        self.assertEqual(mount_types(pads), {'R1': 'smd', 'J1': 'through_hole'})
        with self.assertRaisesRegex(ValueError, 'unknown attribute'):
            mount_types(pads + [{'ref': 'X1', 'pad': '1', 'attribute': 'CASTELLATED'}])


class NativeAssessmentTests(unittest.TestCase):
    """The real export, cpl_rotation and done gate on the owned CPL control, then the assessment."""

    def setUp(self):
        require_toolchain(native=True)
        tmp = ReceiptTemporaryDirectory(prefix='agent-ee-assembly-profile-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runs = 0

    def scope(self, profile=None, **params):
        """The owned CPL generation scope, with the profile captured beside the fixture when given."""
        source = self.root / f'source{self.runs}'
        shutil.copytree(CPL_FIXTURE, source)
        scope = rotation_scope('agent-ee.scope/3')
        scope['inputs'][0]['path'] = str(source)
        if profile is not None:
            (source / 'assembly-profile.json').write_text(json.dumps(profile, indent=1, sort_keys=True) + '\n')
            scope['inputs'][0]['include'].append('assembly-profile.json')
            scope['checks'][-1]['params']['assembly_profile'] = PROFILE_REL
        scope['checks'][-1]['params'].update(params)
        return scope

    def run_case(self, scope, *, after_rotation=None):
        self.runs += 1
        case = self.root / str(self.runs)
        case.mkdir()
        write_json(case / 'scope.json', scope)
        self.run_dir = case / 'run'
        self.run_dir.mkdir()
        capture(case / 'scope.json', self.run_dir)
        rotation = checks.CHECK_FUNCS['cpl_rotation']

        def tampered(ctx):
            rotation(ctx)
            after_rotation(ctx.work)
        with mock.patch.dict(checks.CHECK_FUNCS, {'cpl_rotation': tampered if after_rotation else rotation}):
            results = run_checks(self.run_dir, time.monotonic() + 300)
        freeze_evidence(self.run_dir, results)
        verify_evidence(self.run_dir, results, required=True)
        self.results = {r['id']: r for r in results}
        return self.results['fab']

    def policy_sha(self):
        return digest(CPL_FIXTURE / 'jlc-cpl-policy.json')

    def test_capability_requires_a_declared_rotation_and_captures_the_profile(self):
        scope = self.scope(profile_document(self.policy_sha()))
        files = {f"design/{name}": 'sha' for name in scope['inputs'][0]['include']}
        descriptor = compile_baseline(scope, files)['coverage']['checks']['fab']
        self.assertIn(PROFILE_REL, [c['path'] for c in descriptor.get('captured', [])] or [PROFILE_REL])
        without = copy.deepcopy(scope)
        without['checks'] = [c for c in without['checks'] if c['kind'] != 'cpl_rotation']
        for req in without['baseline']['requirements']:
            req['checks'] = [c for c in req['checks'] if c != 'cpl']
        with self.assertRaisesRegex(ValueError, 'requires a declared cpl_rotation'):
            compile_baseline(without, files)
        for bad in ('/abs/profile.json', 'design/../x.json', ''):
            with self.subTest(bad), self.assertRaises(ValueError):
                fab_package.validate_params({'netlist_check': 'netlist', 'assembly_profile': bad})

    def test_no_profile_leaves_every_row_unassessed_and_the_record_unchanged(self):
        """P2: the artifact record and id are exactly what they were; the assessment says unassessed."""
        result = self.run_case(self.scope())
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        assessment = result['details']['assembly_orientation']
        self.assertEqual((assessment['schema'], assessment['profile_id'], assessment['software_assessment']),
                         (ASSESSMENT_SCHEMA, None, 'unassessed'))
        self.assertEqual(assessment['supplier_confirmation'], 'pending_preview')
        native_rows = cpl_rotation.read_native_cpl((self.run_dir / 'work/fab/fab/cpl.csv').read_text())
        self.assertEqual([r['designator'] for r in assessment['rows']], [r[0] for r in native_rows])
        self.assertTrue(all(r['angle']['basis'] == 'unassessed' and r['anchor']['changed'] is None
                            for r in assessment['rows']))
        self.assertEqual({r['designator'] for r in assessment['unassessed_rows']}, {r[0] for r in native_rows})
        self.assertEqual(result['details']['artifacts'], fab_package.artifact_record(self.run_dir, result))
        self.assertNotIn('assembly_orientation', result['details']['artifacts'])
        self.assertIn('assembly orientation unassessed', result['summary'])

    def test_full_profile_assesses_every_row_with_separate_angle_and_anchor(self):
        """P4/P7: corrected vs native_supported from the real report; anchor change reported on its own."""
        result = self.run_case(self.scope(profile_document(self.policy_sha())))
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        assessment = result['details']['assembly_orientation']
        self.assertEqual((assessment['profile_id'], assessment['software_assessment'], assessment['unassessed_rows']),
                         ('jlcpcb-cpl-software/1', 'complete', []))
        self.assertEqual(assessment['profile_sha256'], digest(self.run_dir / 'inputs' / PROFILE_REL))
        self.assertEqual((assessment['rotation_check'], assessment['policy_sha256']), ('cpl', self.policy_sha()))
        self.assertEqual(assessment['supplier_confirmation'], 'pending_preview')
        self.assertEqual(assessment['preview_obligation_count'],
                         len(result['details']['rotation']['preview_obligations']['designators']))
        report = read_json(self.run_dir / 'work/cpl/cpl-rotation.json')
        records = {r['designator']: r for r in report['designators']}
        rows = {r['designator']: r for r in assessment['rows']}
        self.assertEqual(set(rows), set(records))
        for ref, row in rows.items():
            record = records[ref]
            with self.subTest(ref):
                self.assertEqual(row['applicability'], 'in_profile')
                self.assertEqual(row['angle']['basis'], 'corrected' if record.get('policy') else 'native_supported')
                self.assertEqual(row['angle']['exported_degrees'], record['cpl'][4])
                self.assertEqual(row['angle']['evidence']['offset_degrees'], record['pin1']['offset_degrees'])
                self.assertEqual(Decimal(row['angle']['delta_degrees']) % 360,
                                 Decimal(record['pin1']['offset_degrees']))
                self.assertEqual(row['anchor']['exported_mm'], record['cpl'][1:3])
                geometry = record['geometry']
                self.assertEqual(row['anchor']['changed'], geometry['anchor_nm'] != geometry['midpoint_nm'])
                self.assertEqual(row['anchor']['basis'], 'pad_box_centroid')
        self.assertEqual(rows['U1']['angle']['basis'], 'corrected')
        self.assertEqual(rows['U1']['angle']['evidence']['rule_id'], 'soic-8-3.9x4.9-p1.27')
        self.assertEqual(rows['R1']['angle']['basis'], 'native_supported')
        self.assertEqual(rows['R1']['angle']['delta_degrees'], '0')
        # Native angle kept, anchor moved: pin headers anchor on pin 1, the centroid is elsewhere.
        header = next(r for r in rows.values() if r['angle']['evidence']['footprint'].startswith('agent_cpl:PinHeader'))
        self.assertEqual((header['angle']['basis'], header['mount'], header['anchor']['changed']),
                         ('native_supported', 'through_hole', True))
        self.assertEqual({r['side'] for r in rows.values()}, {'Top', 'Bottom'})
        self.assertEqual(rows['D1']['angle']['evidence']['polarity']['board'],
                         rows['D1']['angle']['evidence']['polarity']['supplier'])
        self.assertEqual(result['details']['artifacts'], fab_package.artifact_record(self.run_dir, result))
        self.assertTrue(any('software assessment under jlcpcb-cpl-software/1 (complete)' in t
                            for t in result['limitations']))

    def test_smd_top_only_profile_lists_through_hole_and_bottom_rows_as_unassessed(self):
        """P3 (applicability): rows outside the profile are exposed unknowns, never supported."""
        result = self.run_case(self.scope(profile_document(self.policy_sha(), sides=('Top',), mount=('smd',))))
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        assessment = result['details']['assembly_orientation']
        self.assertEqual(assessment['software_assessment'], 'incomplete')
        outside = {r['designator'] for r in assessment['rows'] if r['applicability'] == 'outside_profile'}
        expected = {r['designator'] for r in assessment['rows'] if r['mount'] == 'through_hole' or r['side'] == 'Bottom'}
        self.assertTrue(outside)
        self.assertEqual(outside, expected)
        self.assertEqual({r['designator'] for r in assessment['unassessed_rows']}, outside)
        self.assertTrue(all(r['angle']['basis'] == 'outside_profile' for r in assessment['rows']
                            if r['designator'] in outside))
        self.assertEqual(assessment['rows'][0]['angle']['basis'] in ('native_supported', 'corrected', 'outside_profile'), True)
        self.assertTrue(any('unassessed rows' in t for t in result['limitations']))

    def test_unknown_profile_stale_policy_and_wrong_rotation_check_are_unsupported(self):
        """P3 (binding): a profile that does not bind to the sealed evidence holds the done gate."""
        cases = {'unknown profile': profile_document(self.policy_sha(), profile_id='jlcpcb-smt-cpl/1'),
                 'stale policy digest': profile_document('1' * 64),
                 'other rotation check': profile_document(self.policy_sha(), rotation_check='rotation')}
        for name, profile in cases.items():
            with self.subTest(name):
                result = self.run_case(self.scope(profile))
                self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
                self.assertIn('assembly profile design/assembly-profile.json', result['summary'])
                self.assertNotIn('assembly_orientation', result['details'])

    def test_changed_sealed_cpl_position_or_angle_refuses_before_assessment(self):
        """P6: the corrected CPL the package would seal differs from its report."""
        def move(work):
            path = work / 'cpl/cpl.csv'
            lines = path.read_text().splitlines(keepends=True)
            cells = lines[1].split(',')
            cells[1] = str(Decimal(cells[1]) + Decimal('0.05'))
            lines[1] = ','.join(cells)
            path.write_text(''.join(lines))
        result = self.run_case(self.scope(profile_document(self.policy_sha())), after_rotation=move)
        self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'), result)
        self.assertIn('corrected CPL differs from its report', result['summary'])
        self.assertNotIn('assembly_orientation', result['details'])

    def test_incomplete_report_coverage_cannot_assess(self):
        """P5: a report missing a row, or an unsupported row, never yields a complete assessment."""
        result = self.run_case(self.scope(profile_document(self.policy_sha())))
        rotation = result['details']['rotation']
        report = read_json(self.run_dir / 'work/cpl/cpl-rotation.json')
        native = (self.run_dir / 'work/fab/fab/cpl.csv').read_text()
        pads = read_json(self.run_dir / 'work/geometry/geometry.json')['pads']
        profile = read_json(self.run_dir / 'inputs' / PROFILE_REL)
        sha = digest(self.run_dir / 'inputs' / PROFILE_REL)
        complete = orientation_assessment(profile, sha, rotation, report, native, pads)
        self.assertEqual(complete['software_assessment'], 'complete')
        missing = copy.deepcopy(report)
        missing['designators'] = missing['designators'][1:]
        with self.assertRaisesRegex(ValueError, 'lacks CPL row'):
            orientation_assessment(profile, sha, rotation, missing, native, pads)
        incomplete = copy.deepcopy(report)
        incomplete['coverage'] = 'incomplete'
        with self.assertRaisesRegex(ValueError, 'passing complete'):
            orientation_assessment(profile, sha, rotation, incomplete, native, pads)
        unsupported = copy.deepcopy(report)
        unsupported['designators'][0].update(execution='unsupported', outcome='unknown')
        with self.assertRaisesRegex(ValueError, 'did not pass'):
            orientation_assessment(profile, sha, rotation, unsupported, native, pads)
        disagree = copy.deepcopy(report)
        disagree['designators'][0]['pin1']['offset_degrees'] = '90'
        with self.assertRaisesRegex(ValueError, 'does not support delta'):
            orientation_assessment(profile, sha, rotation, disagree, native, pads)
        self.assertEqual(unassessed_record(rotation, native, 'why')['software_assessment'], 'unassessed')


if __name__ == '__main__':
    unittest.main()
