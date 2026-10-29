"""Source-only public acquisition preparation; no native tools or stage execution."""
import contextlib
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import acquisition_prepare, build_workflow, requirement_map, spec_record

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
            (brief().replace(b'acquisition-control', b'another-reference'), 'unsupported_clause'),
        ]
        for raw, code in cases:
            with self.subTest(code=code, raw=raw):
                with self.assertRaises(acquisition_prepare.PreparationRefused) as caught:
                    acquisition_prepare.parse_brief(raw)
                self.assertEqual(caught.exception.report['findings'][0]['code'], code)


@unittest.skipUnless(PLAN.is_file() and CPL.is_dir() and TEXT.is_dir(),
                     'retained acquisition plan and additive captures required')
class AcquisitionPreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sources = {'cpl': CPL, 'manufacturer-text': TEXT}

    def prepare(self, raw, name='prepared', sources=None):
        path = self.root / (name + '.md')
        path.write_bytes(raw)
        out = self.root / name
        report = acquisition_prepare.prepare(path, PLAN, out,
            added_sources=self.sources if sources is None else sources)
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
            self.assertEqual(admitted[3]['placement'], original_plan['placement'])
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
