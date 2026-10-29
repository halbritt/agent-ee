"""Source-only public acquisition preparation; no native tools or stage execution."""
import contextlib
from decimal import Decimal, localcontext
from fractions import Fraction
import io
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import acquisition_prepare, build_workflow, requirement_map, spec_record, storage, interval_assertions

ROOT = Path(__file__).resolve().parents[1]
BASE = Path(os.environ.get('AGENT_EE_ACQUISITION_INPUTS', ROOT / 'build/acquisition-release/inputs'))
PLAN = Path(os.environ.get('AGENT_EE_ACQUISITION_PLAN', BASE.parent / 'build.json'))
CPL = Path(os.environ.get('AGENT_EE_ACQUISITION_CPL_INPUTS', ROOT / 'build/acquisition-release/cpl-inputs'))
TEXT = Path(os.environ.get('AGENT_EE_ACQUISITION_TEXT_INPUTS', ROOT / 'build/acquisition-release/manufacturer-text'))


def brief(cap='150'):
    return ('Use acquisition reference acquisition-control unchanged.\n'
            f'Total I2C line capacitance shall be at most {cap} pF per line, '
            'including board, host and cable.\n').encode()


class BriefTests(unittest.TestCase):
    def test_missing_ambiguous_contradictory_and_unsupported_requests_refuse(self):
        cases = [
            (b'', 'missing_reference_adoption'),
            (brief().split(b'\n', 1)[1], 'missing_reference_adoption'),
            ((acquisition_prepare.ADOPTION + '\n').encode(), 'missing_capacitance'),
            (brief() + b'Use 5 V push-pull I2C.\n', 'unsupported_clause'),
            (brief().replace(b'Total I2C', b'Cable I2C'), 'unsupported_clause'),
            (brief().replace(b'150 pF', b'100 or 150 pF'), 'unsupported_clause'),
            (brief() + brief('100').split(b'\n', 1)[1], 'contradictory_capacitance'),
            (brief('200'), 'unsupported_fixed_recipe_bound'),
            (brief('0'), 'unsupported_fixed_recipe_bound'),
            (brief('99.999'), 'unsupported_fixed_recipe_bound'),
            (brief('150.001'), 'unsupported_fixed_recipe_bound'),
            (brief('125') + brief('150').split(b'\n', 1)[1], 'contradictory_capacitance'),
            (brief().replace(b'acquisition-control', b'another-reference'), 'unsupported_clause'),
        ]
        for raw, code in cases:
            with self.subTest(code=code, raw=raw):
                with self.assertRaises(acquisition_prepare.PreparationRefused) as caught:
                    acquisition_prepare.parse_brief(raw)
                self.assertEqual(caught.exception.report['findings'][0]['code'], code)

    def test_range_precision_and_zero_padding_are_exact(self):
        for text in ('100', '125', '150', '000100.' + '0' * 80, '000150.' + '0' * 80,
                     '100.' + '0' * 24 + '1'):
            with self.subTest(text=text), localcontext() as context:
                context.prec = 6
                selected = acquisition_prepare.parse_brief(brief(text))
                self.assertEqual(selected['value'], Decimal(text))
                self.assertIn(text + ' pF', selected['quantity'])
        raw = brief('100.' + '0' * 25 + '1')
        with self.assertRaises(acquisition_prepare.PreparationRefused) as caught:
            acquisition_prepare.parse_brief(raw)
        report = caught.exception.report
        self.assertEqual(report['findings'][0]['code'], 'unsupported_fixed_recipe_bound')
        self.assertIn('precision', report['findings'][0]['message'])
        self.assertEqual(report['outcome'], 'refused')


@unittest.skipUnless(PLAN.is_file() and CPL.is_dir() and TEXT.is_dir(),
                     'retained acquisition plan and additive captures required')
class AcquisitionPreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sources = {'cpl': CPL, 'manufacturer-text': TEXT}

    def prepare(self, raw, name='prepared', sources=None, **context):
        path = self.root / (name + '.md')
        path.write_bytes(raw)
        out = self.root / name
        report = acquisition_prepare.prepare(path, PLAN, out,
            added_sources=self.sources if sources is None else sources, **context)
        return out, report

    def test_changed_brief_prepares_complete_typed_source_for_public_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'brief.md'
            source.write_bytes(brief())
            out = root / 'prepared'
            report = acquisition_prepare.prepare(source, PLAN, out,
                added_sources={'cpl': CPL, 'manufacturer-text': TEXT})
            self.assertEqual(report['outcome'], 'prepared')
            prepared = out / 'inputs'
            interval = json.loads((prepared / 'analysis/electrical-intervals.json').read_bytes())
            self.assertEqual(interval['parameters']['bus_c']['max'], '150e-12 F')
            self.assertIn(b'at most 150 pF total line capacitance', (prepared / 'spec.md').read_bytes())
            self.assertIn(b'150 pF per line', (prepared / 'OPERATING.md').read_bytes())
            record = spec_record.verify((prepared / 'spec-record.json').read_bytes(),
                                        (prepared / 'spec.md').read_bytes())
            admitted = build_workflow.preflight_build(out / 'build-plan.json', root / 'native-not-run')
            self.assertEqual(admitted[3]['schema'], 'agent-ee.build-plan/2')
            self.assertFalse((root / 'native-not-run').exists())
            self.assertEqual(record['spec']['sha256'], interval['requirement_sources']['ACQ-ELEC-5-PULLUPS']['sha256'])
            for name in ('circuit.json', 'layout.json', 'groups.json', 'ratings.json', 'adc-ties.json'):
                self.assertEqual((prepared / name).read_bytes(), (BASE / name).read_bytes(), name)
            original_parts = json.loads((BASE / 'parts.json').read_bytes())
            revised_parts = json.loads((prepared / 'parts.json').read_bytes())
            original_parts.pop('declaration_source')
            revised_parts.pop('declaration_source')
            self.assertEqual(revised_parts, original_parts)
            original_plan = json.loads(PLAN.read_bytes())
            self.assertEqual(admitted[3]['placement'],
                             {**original_plan['placement'], 'mechanics': 'placement-mechanics.json'})
            from agent_ee import acquisition_mechanics
            mechanics = acquisition_mechanics.load(prepared / 'placement-mechanics.json')
            self.assertEqual(mechanics['declaration']['sources'],
                             acquisition_mechanics.declaration(prepared)['sources'])
            self.assertEqual(report['inputs']['placement-mechanics.json'],
                             spec_record.sha256((prepared / 'placement-mechanics.json').read_bytes()))
            self.assertEqual(admitted[3]['verification_timeout_s'], original_plan['verification_timeout_s'])
            self.assertFalse((prepared / 'judgments').exists())
            self.assertEqual(report['qualification'], 'unverified')

    def test_control_and_changed_bound_keep_equations_limits_and_source_inventory(self):
        before = requirement_map.input_inventory(BASE)
        original = json.loads((BASE / 'analysis/electrical-intervals.json').read_bytes())
        for cap, expected in [('100', '1.88270060000E-7'), ('150', '2.82405090000E-7')]:
            with self.subTest(cap=cap):
                out, report = self.prepare(brief(cap), name='case-' + cap)
                inputs = out / 'inputs'
                intervals = json.loads((inputs / 'analysis/electrical-intervals.json').read_bytes())
                self.assertEqual({key:{k:v for k,v in row.items() if k != 'requirement'}
                                  for key,row in intervals['parameters'].items() if key != 'bus_c'},
                                 {key:{k:v for k,v in row.items() if k != 'requirement'}
                                  for key,row in original['parameters'].items() if key != 'bus_c'})
                self.assertEqual([{k:v for k,v in row.items() if k != 'requirement'} for row in intervals['assertions']],
                                 [{k:v for k,v in row.items() if k != 'requirement'} for row in original['assertions']])
                calculations = json.loads((inputs / 'engineering-calculations.json').read_bytes())
                self.assertEqual(Decimal(calculations['values']['i2c_rise_time']['value']), Decimal(expected))
                self.assertEqual((inputs / 'operating-supplement/ADS1115-OPERATING.md').read_bytes(),
                    (ROOT / 'fixtures/acquisition-typed/operating-supplement/ADS1115-OPERATING.md').read_bytes())
                self.assertEqual(report['accounting']['obligations']['judgment'], 14)
                self.assertEqual(report['accounting']['judgment_records_present'], [])
        self.assertEqual(requirement_map.input_inventory(BASE), before)

    def test_read_only_capture_prepares_without_changing_source_bytes_or_modes(self):
        capture = self.root / 'captured'
        capture.mkdir()
        source = capture / 'inputs'
        shutil.copytree(BASE, source)
        plan = capture / 'build.json'
        plan.write_bytes(PLAN.read_bytes())
        files = [p for p in source.rglob('*') if p.is_file()]
        directories = [source, *[p for p in source.rglob('*') if p.is_dir()]]
        for path in files:
            path.chmod(0o444)
        for path in directories:
            path.chmod(0o555)
        before = requirement_map.input_inventory(source)
        raw = self.root / 'readonly.md'
        raw.write_bytes(brief('150'))
        out = self.root / 'readonly-prepared'
        try:
            report = acquisition_prepare.prepare(raw, plan, out, added_sources=self.sources)
            self.assertEqual(report['outcome'], 'prepared')
            self.assertIn(b'at most 150 pF total line capacitance',
                          (out / 'inputs/spec.md').read_bytes())
            self.assertEqual(requirement_map.input_inventory(source), before)
            self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == 0o444 for p in files))
            self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == 0o555 for p in directories))
            self.assertEqual(plan.read_bytes(), PLAN.read_bytes())
        finally:
            for path in directories:
                path.chmod(0o700)

    def test_multibyte_non_equal_length_quantity_relocates_all_source_spans(self):
        raw = brief('150.00').replace(b'150.00 pF', '150.00\u00a0pF'.encode())
        out, _ = self.prepare(raw)
        inputs = out / 'inputs'
        spec = (inputs / 'spec.md').read_bytes()
        self.assertIn('150.00\u00a0pF'.encode(), spec)
        record = spec_record.verify((inputs / 'spec-record.json').read_bytes(), spec)
        obligations = requirement_map.load_obligations((inputs / 'obligations.json').read_bytes(), record, spec)
        declaration = requirement_map.map_declaration((inputs / 'requirement-map.json').read_bytes())
        _, rows = requirement_map.account_requirements(declaration, record, spec,
            files=requirement_map.input_inventory(inputs), inputs=inputs, obligations=obligations)
        for row in rows:
            locator = row['locator']
            if isinstance(locator, dict) and 'byte_start' in locator:
                quote = spec[locator['byte_start']:locator['byte_end']]
                self.assertEqual(spec_record.sha256(quote), row['text_sha256'])
                quote.decode('utf-8')
        crosswalk = json.loads((inputs / 'preparation/source-map.json').read_bytes())
        start, end = crosswalk['capacitance_span']
        self.assertEqual(raw[start:end], raw.splitlines()[1])
        intervals = json.loads((inputs / 'analysis/electrical-intervals.json').read_bytes())
        for source in intervals['requirement_sources'].values():
            locator = json.loads(source['locator'])
            self.assertEqual(source['sha256'], spec_record.sha256(spec))
            self.assertTrue(spec[locator['byte_start']:locator['byte_end']].decode())
        parts = json.loads((inputs / 'parts.json').read_bytes())
        self.assertEqual(parts['declaration_source']['sha256'], spec_record.sha256(spec))
        locality = json.loads((inputs / 'electrical-locality.json').read_bytes())
        self.assertEqual(locality['source_records']['spec.md'].encode(), spec)
        for clause in record['requirements']:
            if clause['id'] == 'ACQ-ELEC-6':
                old = next(r for r in json.loads((BASE / 'spec-record.json').read_bytes())['requirements']
                           if r['id'] == clause['id'])
                self.assertEqual(clause['locator']['byte_start'] - old['locator']['byte_start'], 4)

    def test_raw_brief_bytes_change_identity_and_build_input_binding(self):
        first, a = self.prepare(brief(), 'first')
        second, b = self.prepare(b'\n' + brief() + b'\n', 'second')
        self.assertNotEqual(a['identity'], b['identity'])
        self.assertEqual((first / 'inputs/spec.md').read_bytes(), (second / 'inputs/spec.md').read_bytes())
        self.assertNotEqual(a['inputs']['preparation/brief.md'], b['inputs']['preparation/brief.md'])

    def test_refusals_preserve_existing_outputs_and_publish_no_partial_bundle(self):
        source = self.root / 'brief.md'
        source.write_bytes(brief())
        out = self.root / 'existing'
        out.mkdir()
        (out / 'sentinel').write_bytes(b'keep')
        with self.assertRaises(ValueError):
            acquisition_prepare.prepare(source, PLAN, out, added_sources=self.sources)
        self.assertEqual((out / 'sentinel').read_bytes(), b'keep')
        with self.assertRaises(ValueError):
            acquisition_prepare.prepare(source, PLAN, BASE / 'nested-output', added_sources=self.sources)
        with self.assertRaises(ValueError):
            acquisition_prepare.prepare(source, PLAN, self.root / 'missing-cpl', added_sources={})
        self.assertFalse((self.root / 'missing-cpl').exists())
        self.assertEqual(list(self.root.glob('*.prepare-*')), [])
        for raw in [brief('200'), brief() + b'Add WiFi.\n']:
            source.write_bytes(raw)
            with self.assertRaises(acquisition_prepare.PreparationRefused):
                acquisition_prepare.prepare(source, PLAN, self.root / 'refused', added_sources=self.sources)
            self.assertFalse((self.root / 'refused').exists())

    def test_cli_consumes_brief_without_an_intermediate_engineering_script(self):
        source = self.root / 'brief.md'
        source.write_bytes(brief())
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = acquisition_prepare.main(['--brief', str(source), '--build-plan', str(PLAN),
                '--out', str(self.root / 'cli-output'), '--added-input', 'cpl=' + str(CPL),
                '--added-input', 'manufacturer-text=' + str(TEXT)])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue())['outcome'], 'prepared')
        self.assertTrue((self.root / 'cli-output/build-plan.json').is_file())
        source.write_bytes(brief() + 'Unknown μ directive.\n'.encode())
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = acquisition_prepare.main(['--brief', str(source), '--build-plan', str(PLAN),
                '--out', str(self.root / 'cli-refused')])
        diagnostic = json.loads(stdout.getvalue())
        self.assertEqual(status, 1)
        self.assertEqual(diagnostic['brief_sha256'], spec_record.sha256(source.read_bytes()))
        start = diagnostic['findings'][0]['byte_start']
        end = diagnostic['findings'][0]['byte_end']
        self.assertEqual(source.read_bytes()[start:end].decode(), 'Unknown μ directive.')
        self.assertFalse((self.root / 'cli-refused').exists())

    def test_late_filesystem_failure_removes_owned_staging_without_publishing(self):
        before = requirement_map.input_inventory(BASE)
        write = Path.write_bytes

        def full_disk(path, raw):
            if path.name == 'preparation.json':
                raise OSError('injected full disk at final report write')
            return write(path, raw)

        # The external filesystem is the fault boundary; all preparation runs for real.
        with patch.object(Path, 'write_bytes', full_disk):
            with self.assertRaisesRegex(OSError, 'full disk'):
                self.prepare(brief(), 'late-failure')
        self.assertFalse((self.root / 'late-failure').exists())
        self.assertEqual(list(self.root.glob('*.prepare-*')), [])
        self.assertEqual(requirement_map.input_inventory(BASE), before)

    def profile(self):
        path = self.root / 'profile.json'
        path.write_bytes((ROOT / 'profiles/pod-review.json').read_bytes())
        return path

    def test_requested_review_captures_exact_bytes_and_public_build_input_identity(self):
        profile = self.profile()
        ordinary, old = self.prepare(brief(), 'ordinary')
        out, report = self.prepare(brief(), 'reviewed', review_profile=profile)
        plan = json.loads((out / 'build-plan.json').read_bytes())
        captured = out / 'inputs' / plan['review_profile']
        self.assertEqual(captured.read_bytes(), profile.read_bytes())
        admitted = build_workflow.preflight_build(out / 'build-plan.json', self.root / 'native-not-run')
        self.assertEqual(admitted[-1][plan['review_profile']], spec_record.sha256(profile.read_bytes()))
        self.assertEqual(plan['verification_timeout_s'], 1800)
        self.assertEqual(report['recipe']['review_context']['verification_timeout_s'],
                         {'requested': None, 'effective': 1800})
        self.assertEqual(report['recipe']['helpers']['agent_ee.profiles'],
                         spec_record.sha256((ROOT / 'src/agent_ee/profiles.py').read_bytes()))
        ledger = json.loads((out / 'inputs/preparation/edit-ledger.json').read_bytes())
        self.assertEqual(ledger['review_context'], report['recipe']['review_context'])
        self.assertNotEqual(report['identity'], old['identity'])
        for name in ('spec.md', 'circuit.json', 'placement.json', 'layout.json', 'groups.json',
                     'analysis/electrical-intervals.json', 'analysis/geometry_model.py'):
            self.assertEqual((out / 'inputs' / name).read_bytes(), (ordinary / 'inputs' / name).read_bytes(), name)
        self.assertFalse((out / 'inputs/judgments').exists())
        self.assertEqual(report['qualification'], 'unverified')
        profile.write_bytes(profile.read_bytes() + b'\n')
        changed, second = self.prepare(brief(), 'changed-profile', review_profile=profile)
        self.assertNotEqual(report['identity'], second['identity'])
        other = build_workflow.preflight_build(changed / 'build-plan.json', self.root / 'also-not-run')
        self.assertNotEqual(storage.identity(admitted[-1]), storage.identity(other[-1]))
        self.assertFalse((self.root / 'native-not-run').exists())

    def test_explicit_review_timeout_is_bounded_context_without_automatic_increase(self):
        profile = self.profile()
        first, a = self.prepare(brief(), 'inherited', review_profile=profile)
        second, b = self.prepare(brief(), 'explicit', review_profile=profile, verification_timeout_s=1800)
        third, c = self.prepare(brief(), 'longer', review_profile=profile, verification_timeout_s=3600)
        self.assertNotEqual(a['identity'], b['identity'])
        self.assertEqual(b['recipe']['review_context']['verification_timeout_s'],
                         {'requested': 1800, 'effective': 1800})
        self.assertEqual(json.loads((third / 'build-plan.json').read_bytes())['verification_timeout_s'], 3600)
        self.assertNotEqual(b['identity'], c['identity'])
        for index, seconds in enumerate((0, 86401, True, 1.5)):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                self.prepare(brief(), 'invalid-timeout-' + str(index), review_profile=profile,
                             verification_timeout_s=seconds)
            self.assertFalse((self.root / ('invalid-timeout-' + str(index))).exists())
        with self.assertRaisesRegex(ValueError, 'review_profile'):
            self.prepare(brief(), 'timeout-only', verification_timeout_s=3600)
        self.assertFalse((self.root / 'timeout-only').exists())

    def test_bad_or_linked_profile_refuses_without_a_bundle(self):
        profile = self.profile()
        missing = self.root / 'missing-profile.json'
        file_link = self.root / 'profile-link.json'
        file_link.symlink_to(profile)
        folder = self.root / 'profile-folder'
        folder.mkdir()
        nested = folder / 'profile.json'
        nested.write_bytes(profile.read_bytes())
        directory_link = self.root / 'directory-link'
        directory_link.symlink_to(folder, target_is_directory=True)
        hard_link = self.root / 'hard-profile.json'
        hard_target = self.root / 'hard-target.json'
        hard_target.write_bytes(profile.read_bytes())
        os.link(hard_target, hard_link)
        malformed = self.root / 'malformed.json'
        malformed.write_bytes(b'{"schema":"agent-ee.profile/1","name":"bad","reviewers":[1,2]}')
        duplicate = self.root / 'duplicate.json'
        duplicate.write_bytes(b'{"schema":"agent-ee.profile/1","schema":"other"}')
        for index, path in enumerate((missing, file_link, directory_link / 'profile.json',
                                      hard_link, malformed, duplicate, folder)):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                self.prepare(brief(), 'bad-profile-' + str(index), review_profile=path)
            self.assertFalse((self.root / ('bad-profile-' + str(index))).exists())
        self.assertEqual(list(self.root.glob('*.prepare-*')), [])
        existing = self.root / 'existing-profile-output'
        existing.mkdir()
        (existing / 'sentinel').write_text('keep')
        with self.assertRaises(ValueError):
            self.prepare(brief(), existing.name, review_profile=profile)
        self.assertEqual((existing / 'sentinel').read_text(), 'keep')

    def test_profile_drift_at_publication_refuses_and_removes_owned_staging(self):
        profile = self.profile()
        write = Path.write_bytes
        for mode in ('bytes', 'replacement', 'capture'):
            def drift(path, raw):
                if path.name == 'preparation.json':
                    if mode == 'bytes':
                        write(profile, profile.read_bytes() + b'\n')
                    elif mode == 'replacement':
                        replacement = self.root / 'replacement.json'
                        write(replacement, profile.read_bytes())
                        replacement.replace(profile)
                    else:
                        captured = path.parent / 'inputs/preparation/review-profile.json'
                        write(captured, captured.read_bytes() + b'\n')
                return write(path, raw)
            with self.subTest(mode=mode), patch.object(Path, 'write_bytes', drift):
                with self.assertRaisesRegex(ValueError, 'profile.*changed'):
                    self.prepare(brief(), 'drift-' + mode, review_profile=profile)
            self.assertFalse((self.root / ('drift-' + mode)).exists())
            self.assertEqual(list(self.root.glob('*.prepare-*')), [])

    def test_cli_accepts_explicit_review_profile_and_verification_timeout(self):
        source = self.root / 'cli-brief.md'
        source.write_bytes(brief())
        profile = self.profile()
        out = self.root / 'cli-review'
        with contextlib.redirect_stdout(io.StringIO()):
            status = acquisition_prepare.main(['--brief', str(source), '--build-plan', str(PLAN),
                '--out', str(out), '--review-profile', str(profile), '--verification-timeout-s', '3600',
                '--added-input', 'cpl=' + str(CPL), '--added-input', 'manufacturer-text=' + str(TEXT)])
        self.assertEqual(status, 0)
        plan = json.loads((out / 'build-plan.json').read_bytes())
        self.assertEqual((out / 'inputs' / plan['review_profile']).read_bytes(), profile.read_bytes())
        self.assertEqual(plan['verification_timeout_s'], 3600)

    def test_public_range_drives_real_operands_prose_spans_and_interval_verdicts(self):
        original = json.loads((BASE / 'analysis/electrical-intervals.json').read_bytes())
        rise_source = next(a for a in original['assertions'] if a['id'] == 'i2c-rise')
        box = {'pullup_r': interval_assertions.declared_interval(
            {k: original['parameters']['pullup_r'][k] for k in ('min', 'max')}, 'ohm', 'pullups'),
            'bus_c': (Fraction(100, 10**12), Fraction(150, 10**12))}
        enclosure, monotonicity = interval_assertions.enclose(
            interval_assertions.parse_expression(rise_source['lhs']), box)
        self.assertEqual(monotonicity['bus_c'], 'nondecreasing')
        self.assertEqual(enclosure[1], Fraction(8473, 10000) * 2222 * Fraction(150, 10**12))
        self.assertLess(enclosure[1], Fraction(300, 10**9))
        identities = set()
        for cap in ('100', '125', '150'):
            raw = brief(cap + '.00').replace((cap + '.00 pF').encode(), (cap + '.00\u00a0pF').encode())
            out, report = self.prepare(raw, 'range-' + cap)
            identities.add(report['identity'])
            inputs = out / 'inputs'
            intervals = json.loads((inputs / 'analysis/electrical-intervals.json').read_bytes())
            self.assertEqual(intervals['parameters']['bus_c']['max'], cap + 'e-12 F')
            for parameter in ('pullup_r', 'rise_limit'):
                self.assertEqual({k:v for k,v in intervals['parameters'][parameter].items() if k != 'requirement'},
                                 {k:v for k,v in original['parameters'][parameter].items() if k != 'requirement'})
            # The requirements stage captures this exact spec before checks execute.
            provenance = inputs / 'requirements-map'
            provenance.mkdir()
            (provenance / 'spec.md').write_bytes((inputs / 'spec.md').read_bytes())
            evaluated = interval_assertions.evaluate_inputs(inputs, {'assertions': 'analysis/electrical-intervals.json'})
            self.assertEqual(evaluated['execution'], 'ok', evaluated)
            rise = next(a for a in evaluated['assertions'] if a['id'] == 'i2c-rise')
            self.assertEqual(rise['verdict'], 'pass')
            self.assertEqual([{k:v for k,v in a.items() if k != 'requirement'} for a in intervals['assertions']],
                             [{k:v for k,v in a.items() if k != 'requirement'} for a in original['assertions']])
            expected = Fraction(8473, 10000) * 2222 * Fraction(int(cap), 10**12)
            calculated = json.loads((inputs / 'engineering-calculations.json').read_bytes())
            self.assertEqual(Fraction(calculated['values']['i2c_rise_time']['value']), expected)
            self.assertIn((cap + '.00\u00a0pF').encode(), (inputs / 'spec.md').read_bytes())
            self.assertIn((cap + '.00\u00a0pF per line').encode(), (inputs / 'OPERATING.md').read_bytes())
            crosswalk = json.loads((inputs / 'preparation/source-map.json').read_bytes())
            self.assertEqual(crosswalk['capacitance_pf'], cap)
            self.assertEqual(crosswalk['operating_supplement']['status'],
                             'retained_bound' if cap == '100' else 'superseded_bound')
            spec = (inputs / 'spec.md').read_bytes()
            record = spec_record.verify((inputs / 'spec-record.json').read_bytes(), spec)
            for row in record['requirements'] + record['prose']:
                locator = row['locator']
                self.assertEqual(spec_record.sha256(spec[locator['byte_start']:locator['byte_end']]),
                                 row['text_sha256'])
            for name in ('circuit.json', 'layout.json', 'groups.json', 'analysis/geometry_model.py'):
                self.assertEqual((inputs / name).read_bytes(), (BASE / name).read_bytes())
        self.assertEqual(len(identities), 3)

    def test_canonical_precision_limit_does_not_round_calculation_or_source(self):
        cap = '100.' + '0' * 24 + '1'
        with localcontext() as context:
            context.prec = 6
            out, report = self.prepare(brief(cap), 'precision')
        inputs = out / 'inputs'
        operand = json.loads((inputs / 'analysis/electrical-intervals.json').read_bytes())['parameters']['bus_c']
        self.assertEqual(operand['max'], cap + 'e-12 F')
        expected = Fraction(8473, 10000) * 2222 * Fraction(Decimal(cap)) / 10**12
        calculated = json.loads((inputs / 'engineering-calculations.json').read_bytes())
        self.assertEqual(Fraction(calculated['values']['i2c_rise_time']['value']), expected)
        self.assertIn(cap.encode(), (inputs / 'spec.md').read_bytes())
        self.assertEqual(report['recipe']['helpers']['agent_ee.catalog'],
                         spec_record.sha256((ROOT / 'src/agent_ee/catalog.py').read_bytes()))


@unittest.skipUnless(os.environ.get('AGENT_EE_ADDR_CONTEXT') and PLAN.is_file() and CPL.is_dir() and TEXT.is_dir(),
                     'explicit captured acquisition ADDR source/context required')
class AddrPreparationTests(unittest.TestCase):
    """Actual preparation and compiler calls; no native production/qualification."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='addr-source-pure-'); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.brief = self.root/'brief.md'; self.brief.write_bytes(brief('125'))
        self.context = Path(os.environ['AGENT_EE_ADDR_CONTEXT'])
        self.options = dict(added_sources={'cpl':CPL,'manufacturer-text':TEXT},
            review_profile=ROOT/'profiles/pod-review.json', alternative_context=self.context,
            alternative_source=Path(os.environ['AGENT_EE_ADDR_SOURCE']),
            alternative_context_sha256=storage.digest(self.context))
        self.before = requirement_map.input_inventory(BASE)
        self.seed = self.root/'seed'
        acquisition_prepare.prepare(self.brief,PLAN,self.seed,**self.options)
        self.replay = dict(evaluation_context=self.seed/'preparation.json',
                           evaluation_context_sha256=storage.digest(self.seed/'preparation.json'))

    def prepare(self,name,choice='GND',**options):
        out=self.root/name
        report=acquisition_prepare.prepare(self.brief,PLAN,out,
            **{**self.options,**self.replay,'addr_tie':choice,**options})
        return out,report

    def compiled(self,out,label):
        from agent_ee.baseline import compile_baseline
        source=self.root/('compiler-'+label); shutil.copytree(out/'inputs',source)
        raw={n:(source/n).read_bytes() for n in ('requirement-map.json','spec-record.json','spec.md','obligations.json')}
        template=requirement_map.map_declaration(raw['requirement-map.json'])['scope']
        # Declared-subject capability admission only: these are not emitted CAD.
        for key in ('board','schematic'):
            path=source/template[key]; path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b'Authored declared subject placeholder; no native qualification.\n')
        files=requirement_map.input_inventory(source)
        scope,receipt=requirement_map.compile_map(raw['requirement-map.json'],raw['spec-record.json'],raw['spec.md'],
            files=files,inputs=source,obligations_raw=raw['obligations.json'])
        provenance={'spec.md':raw['spec.md'],'spec-record.json':raw['spec-record.json'],
            'requirement-map.json':raw['requirement-map.json'],'obligations.json':raw['obligations.json']}
        files.update({'requirements-map/'+k:spec_record.sha256(v) for k,v in provenance.items()})
        with requirement_map.compilation_inputs(source,files,provenance) as captured:
            baseline=compile_baseline(scope,files,captured)
        self.assertEqual(receipt['baseline_id'],baseline['baseline_id'])
        self.assertFalse(set(baseline['files']) & {template['board'],template['schematic']})
        return scope,baseline

    def test_actual_variants_keep_complete_baseline_parts_and_only_one_authored_terminal_delta(self):
        import copy
        from agent_ee import parts_manifest,parts_stage
        ground,a=self.prepare('ground'); wrong,b=self.prepare('wrong','V3V3'); replay,c=self.prepare('replay')
        inventories=[requirement_map.input_inventory(p/'inputs') for p in (ground,wrong,replay)]
        self.assertEqual(inventories[0].keys(),inventories[1].keys())
        changed=sorted(n for n in inventories[0] if inventories[0][n]!=inventories[1][n])
        if os.environ.get('AGENT_EE_ADDR_EVIDENCE'):
            evidence=Path(os.environ['AGENT_EE_ADDR_EVIDENCE']);evidence.mkdir(parents=True,exist_ok=True)
            for name,path in (('prepared-ground',ground),('prepared-wrong',wrong),('prepared-replay',replay)):
                shutil.copytree(path,evidence/name)
        pairs=[self.compiled(ground,'ground'),self.compiled(wrong,'wrong')]
        if os.environ.get('AGENT_EE_ADDR_EVIDENCE'):
            evidence=Path(os.environ['AGENT_EE_ADDR_EVIDENCE']);evidence.mkdir(parents=True,exist_ok=True)
            for name,value in [('baseline-ground.json',pairs[0][1]),('baseline-wrong.json',pairs[1][1]),
                    ('inventory-ground.json',inventories[0]),('inventory-wrong.json',inventories[1]),
                    ('source-differences.json',changed),('preparation-ground.json',a),('preparation-wrong.json',b)]:
                storage.write_json(evidence/name,value)
        self.assertEqual(pairs[0][1],pairs[1][1])  # COMPLETE objects, not selected fields.
        self.assertEqual(pairs[0][0],pairs[1][0])
        self.assertEqual(changed,['circuit.json','overlay-manifest.json','preparation/addr-selection.json','preparation/edit-ledger.json'])
        deps=[parts_manifest.dependency_closure(parts_manifest.load_manifest(p/'inputs','parts.json'),p/'inputs')
              for p in (ground,wrong,replay)]
        self.assertEqual(deps[0],deps[1]);self.assertEqual(deps[0],deps[2])
        for name in deps[0]:
            self.assertEqual((ground/'inputs'/name).read_bytes(),(wrong/'inputs'/name).read_bytes(),name)
        plan=json.loads((ground/'build-plan.json').read_bytes())
        produced=[parts_stage.compile_parts(p/'inputs',**plan['parts']) for p in (ground,wrong)]
        self.assertEqual(produced[0][0]['bindings'],produced[1][0]['bindings'])
        self.assertEqual(produced[0][0]['named_nominals'],produced[1][0]['named_nominals'])
        self.assertEqual([n for n,v in produced[0][0]['dependencies'].items() if produced[1][0]['dependencies'][n]!=v],['circuit.json'])
        expected=copy.deepcopy(produced[0][1]);terminal={'component':'U2','pin':{'number':'1'}}
        next(n for n in expected['nets'] if n['id']=='GND')['terminals'].remove(terminal)
        next(n for n in expected['nets'] if n['id']=='V3V3')['terminals'].append(terminal)
        self.assertEqual(expected,produced[1][1])
        self.assertEqual((ground/'inputs/adc-ties.json').read_bytes(),(BASE/'adc-ties.json').read_bytes())
        self.assertEqual(a['recipe'],c['recipe']);self.assertEqual(a['identity'],c['identity'])
        repeated=sorted(n for n in inventories[0] if inventories[0][n]!=inventories[2][n])
        self.assertEqual(repeated,['overlay-manifest.json'])
        left=json.loads((ground/'inputs/overlay-manifest.json').read_bytes())
        right=json.loads((replay/'inputs/overlay-manifest.json').read_bytes())
        # Existing observation of the owned temporary selected-CPL directory.
        left['derived']['added_inputs']['cpl']['source']='<owned selected-CPL path>'
        right['derived']['added_inputs']['cpl']['source']='<owned selected-CPL path>'
        self.assertEqual(left,right)
        seed=json.loads((self.seed/'preparation.json').read_bytes())
        self.assertNotIn('addr_selection',seed);self.assertNotIn('addr_tie',seed['recipe'])
        self.assertEqual(requirement_map.input_inventory(BASE),self.before)

    def test_changed_or_ambiguous_original_addr_identity_refuses_without_writes(self):
        import copy
        original=json.loads((BASE/'circuit.json').read_bytes());terminal={'component':'U2','pin':{'number':'1'}}
        changes=[lambda d:d['components'].append(copy.deepcopy(next(c for c in d['components'] if c['id']=='U2'))),
            lambda d:next(c for c in d['components'] if c['id']=='U2').update(lib_id='Other:ADC'),
            lambda d:next(n for n in d['nets'] if n['id']=='GND')['terminals'].remove(terminal),
            lambda d:next(n for n in d['nets'] if n['id']=='V3V3')['terminals'].append(terminal),
            lambda d:next(n for n in d['nets'] if n['id']=='GND')['terminals'].append(terminal),
            lambda d:d['no_connects'].append(terminal)]
        for index,change in enumerate(changes):
            owned=self.root/str(index);owned.mkdir();doc=copy.deepcopy(original);change(doc)
            storage.write_json(owned/'circuit.json',doc);before=(owned/'circuit.json').read_bytes()
            with self.subTest(index=index),self.assertRaises(ValueError):acquisition_prepare._select_addr(owned,'V3V3')
            self.assertEqual((owned/'circuit.json').read_bytes(),before)
        with self.assertRaisesRegex(ValueError,'addr_tie'):self.prepare('unknown','SDA')
        self.assertFalse((self.root/'unknown').exists())

    def test_context_pin_recipe_and_anchor_refusals_publish_nothing(self):
        import copy
        for index,options in enumerate(({'evaluation_context_sha256':'0'*64},{'evaluation_context_sha256':None},
                {'alternative_context':None},{'review_profile':None})):
            with self.subTest(options=options),self.assertRaises(ValueError):self.prepare('bad-'+str(index),**options)
            self.assertFalse((self.root/('bad-'+str(index))).exists())
        prior=json.loads((self.seed/'preparation.json').read_bytes())
        for index,anchor in enumerate(('nonsense','2999-01-01T00:00:00+00:00','2026-10-29T03:10:37.069958+00:00')):
            report=copy.deepcopy(prior);report['recipe']['alternative_context']['evaluation_as_of']=anchor
            report['identity']=spec_record.sha256(storage.json_bytes(report['recipe']))
            context=self.root/('context-'+str(index)+'.json');storage.write_json(context,report)
            with self.subTest(anchor=anchor),self.assertRaises(ValueError):
                self.prepare('bad-anchor-'+str(index),evaluation_context=context,evaluation_context_sha256=storage.digest(context))
            self.assertFalse((self.root/('bad-anchor-'+str(index))).exists())
        self.assertEqual(list(self.root.glob('*.prepare-*')),[])

    def test_replayed_preparation_context_does_not_backdate_run_bound_stock_freshness(self):
        from agent_ee import assembly_readiness as stock
        from types import SimpleNamespace
        from datetime import datetime,timedelta
        out,report=self.prepare('run-time');source=out/'inputs'
        params=acquisition_prepare.acquisition_alternative._assembly_check(json.loads((source/'requirement-map.json').read_bytes()))['params']
        work=self.root/'stock-work';work.mkdir();run=self.root/'stock-run';run.mkdir()
        later=(datetime.fromisoformat(report['recipe']['alternative_context']['evaluation_as_of'])+timedelta(days=2)).isoformat()
        storage.write_json(run/'result.json',{'started_utc':later})
        ctx=SimpleNamespace(params=params,inputs=source,context={},run_dir=run,work=work,result={'details':{}},evidence=lambda p:None)
        actual=stock.stock_evaluation_params(ctx);self.assertEqual(actual['as_of'],later);self.assertLessEqual(actual['max_age_hours'],24)
        catalog=json.loads((source/actual['catalog']).read_bytes())
        info=stock._verify_jlc_capture(source,actual['catalog'],catalog)
        with self.assertRaisesRegex(ValueError,'stale'):stock._capture_gate(info,stock.validate_params(actual),set())

    def test_context_filesystem_drift_or_existing_output_preserves_originals(self):
        context=self.root/'owned-context.json';context.write_bytes((self.seed/'preparation.json').read_bytes())
        write=Path.write_bytes
        def drift(path,raw):
            if path.name=='preparation.json':write(context,context.read_bytes()+b' ')
            return write(path,raw)
        with patch.object(Path,'write_bytes',drift),self.assertRaisesRegex(ValueError,'context changed'):
            self.prepare('drift',evaluation_context=context,evaluation_context_sha256=storage.digest(context))
        self.assertFalse((self.root/'drift').exists());self.assertEqual(list(self.root.glob('*.prepare-*')),[])
        with self.assertRaises(ValueError):self.prepare('seed','V3V3')
        self.assertEqual(requirement_map.input_inventory(BASE),self.before)

    def test_compiled_baseline_detects_mutation_of_each_fixed_dependency_family(self):
        out,_=self.prepare('sensitivity');_,baseline=self.compiled(out,'sensitivity')
        paths=['adc-ties.json','analysis/electrical-intervals.json','preparation/alternative/decision.json']
        paths.append(next(p for p in baseline['files'] if p.endswith('.lib')))
        for index,name in enumerate(paths):
            path=out/'inputs'/name;before=path.read_bytes();path.write_bytes(before+b'\n')
            try:
                with self.subTest(path=name):
                    try:_,changed=self.compiled(out,'mutated-'+str(index))
                    except ValueError:pass
                    else:self.assertNotEqual(changed,baseline)
            finally:path.write_bytes(before)
