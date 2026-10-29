"""Source-only public acquisition preparation; no native tools or stage execution."""
import contextlib
from decimal import Decimal, localcontext
from fractions import Fraction
import io
import json
import os
from pathlib import Path
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
