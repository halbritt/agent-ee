"""Interval assertions over owned synthetic catalog claims and hand-computed references."""
import copy
import hashlib
import json
from fractions import Fraction
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from agent_ee.capabilities import KINDS, describe_check
from agent_ee.catalog import capture_catalog
from agent_ee.checks import CHECK_FUNCS, CheckError, Ctx, run_checks
from agent_ee.interval_assertions import (check_interval_assertions, enclose, evaluate_inputs,
                                          parse_expression)
from agent_ee.storage import canonical, digest, read_json, write_json

FIXTURE = TESTS.parent / 'fixtures/interval-assertions'
DOC = 'requirements/divider.assertions.json'
SYSTEM = 'requirements/system.md'


def fraction(text):
    return Fraction(text)


class Workspace:
    """An owned snapshot captured from the fixture CSV beside an editable assertion document."""

    def __init__(self, root):
        self.root = Path(root)
        source = self.root / 'owned.csv'
        shutil.copy(FIXTURE / 'catalog-source.csv', source)
        manifest = dict(schema='agent-ee.catalog-source/1', snapshot_date='2026-10-17',
                        source_url='https://example.invalid/owned', source_commit='owned-v1',
                        currency='USD', stale_before=None, volumes=[dict(path=source.name,
                        sha256=digest(source), size_bytes=source.stat().st_size, format='csv')])
        capture_catalog(manifest, self.root, self.root / 'snapshot')
        self.manifest_sha256 = digest(self.root / 'snapshot/manifest.json')
        shutil.copytree(FIXTURE / 'requirements', self.root / 'requirements')
        self.document = json.loads((FIXTURE / DOC).read_text())

    def params(self):
        return {'assertions': DOC, 'snapshot': 'snapshot', 'manifest_sha256': self.manifest_sha256}

    def write(self, document=None):
        (self.root / DOC).write_text(json.dumps(self.document if document is None else document, indent=1))

    def evaluate(self, document=None, params=None):
        self.write(document)
        return evaluate_inputs(self.root, params or self.params())


def assertion(report, aid):
    return next(a for a in report['assertions'] if a['id'] == aid)


class ArithmeticTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.ws = Workspace(scratch.name)

    def seeded(self, **lcsc):
        document = copy.deepcopy(self.ws.document)
        for name, value in lcsc.items():
            document['parameters'][name]['lcsc'] = value
        return document

    def test_divider_matches_hand_computed_reference(self):
        report = self.ws.evaluate()
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report)
        vout = assertion(report, 'vout-band')
        # v_min = 9.9 V * 9900 / (22220 + 9900); v_max = 10.1 V * 10100 / (21780 + 10100).
        low = Fraction(99, 10) * 9900 / (22220 + 9900)
        high = Fraction(101, 10) * 10100 / (21780 + 10100)
        self.assertEqual([fraction(x) for x in vout['computed']['rational']], [low, high])
        self.assertEqual(vout['computed']['rational'], ['891/292', '10201/3188'])
        self.assertEqual((vout['computed']['min'], vout['computed']['max']), ('3.05136986301369', '3.19981179422836'))
        self.assertTrue(vout['computed']['rounded_outward'])
        self.assertTrue(vout['exact'])
        self.assertEqual(vout['monotonicity'], {'r1': 'nonincreasing', 'r2': 'nondecreasing', 'vin': 'nondecreasing'})
        self.assertEqual(vout['target']['rational'], ['297/100', '363/100'])
        self.assertEqual(vout['verdict'], 'pass')
        # Each computed bound is attained at a named corner whose operands trace to origins.
        attained = vout['bounds']['min']['parameters']
        self.assertEqual({k: v['endpoint'] for k, v in attained.items()}, {'r1': 'max', 'r2': 'min', 'vin': 'min'})
        self.assertEqual(vout['operands']['vin']['requirement'], 'R-VIN')
        self.assertEqual(vout['operands']['r2']['lcsc'], 'C900002')
        self.assertEqual(vout['operands']['r2']['claim'], '10 kohm +/-1%')
        self.assertEqual(vout['operands']['r2']['catalog']['manifest_sha256'], self.ws.manifest_sha256)
        self.assertEqual(vout['operands']['r2']['row_provenance']['volume'], 'owned.csv')

    def test_seeded_tolerances_push_vout_outside_and_name_the_resistor(self):
        for lcsc, low in (('C900003', Fraction(99, 10) * 9500 / (22220 + 9500)),
                          ('C900004', Fraction(99, 10) * 8000 / (22220 + 8000))):
            with self.subTest(lcsc=lcsc):
                report = self.ws.evaluate(self.seeded(r2=lcsc))
                self.assertEqual((report['execution'], report['outcome']), ('ok', 'defect'))
                vout = assertion(report, 'vout-band')
                self.assertEqual(vout['verdict'], 'defect')
                self.assertEqual(fraction(vout['computed']['rational'][0]), low)
                self.assertEqual(fraction(vout['witness']['rational']), low)
                self.assertEqual(vout['requirement'], 'R-VOUT')
                top = vout['contributions'][0]
                self.assertEqual((top['parameter'], top['origin']['lcsc'], top['origin']['reference']),
                                 ('r2', lcsc, 'R2'))
                self.assertEqual(assertion(report, 'c1-voltage-rating')['verdict'], 'pass')

    def test_closed_target_boundary_passes(self):
        document = copy.deepcopy(self.ws.document)
        document['parameters']['r1'] = {'source': 'declared', 'requirement': 'R-VIN', 'unit': 'ohm',
                                        'nominal': '22 kohm', 'tolerance_percent': '5'}
        report = self.ws.evaluate(document)
        vout = assertion(report, 'vout-band')
        # 9.9 * 9900 / (23100 + 9900) is exactly the 2.97 V target minimum; closed bounds pass.
        self.assertEqual(vout['computed']['rational'][0], '297/100')
        self.assertEqual(vout['verdict'], 'pass')

    def test_capacitor_rating_below_rail_is_defect(self):
        report = self.ws.evaluate(self.seeded(c1_rating='C900007'))
        rating = assertion(report, 'c1-voltage-rating')
        self.assertEqual(rating['verdict'], 'defect')
        self.assertEqual(rating['computed']['rational'], ['-19/5', '-18/5'])
        self.assertEqual(rating['relation']['rhs']['rational'], ['63/10', '63/10'])
        self.assertEqual(rating['relation']['lhs']['rational'], ['99/10', '101/10'])
        self.assertEqual(rating['operands']['c1_rating']['claim'], '6.3 V')
        self.assertEqual(rating['operands']['vin']['requirement'], 'R-VIN')
        self.assertEqual(report['outcome'], 'defect')

    def test_missing_tolerance_and_unbounded_operands_are_unknown(self):
        report = self.ws.evaluate(self.seeded(r2='C900005'))
        vout = assertion(report, 'vout-band')
        self.assertEqual((vout['verdict'], vout['computed']), ('unknown', None))
        self.assertIn('no tolerance', vout['reasons'][0])
        self.assertEqual(report['outcome'], 'unknown')
        for spec, reason in (({'nominal': '10 V'}, 'no tolerance'), ({'min': '9 V'}, 'unbounded')):
            document = copy.deepcopy(self.ws.document)
            document['parameters']['vin'] = {'source': 'declared', 'requirement': 'R-VIN', 'unit': 'V', **spec}
            with self.subTest(spec=spec):
                report = self.ws.evaluate(document)
                self.assertEqual(report['outcome'], 'unknown')
                self.assertTrue(all(a['verdict'] == 'unknown' for a in report['assertions']))
                self.assertIn(reason, assertion(report, 'vout-band')['reasons'][0])
        report = self.ws.evaluate(self.seeded(r1='C999999'))
        self.assertIn('absent from the pinned catalog', assertion(report, 'vout-band')['reasons'][0])
        rating = copy.deepcopy(self.ws.document)
        rating['parameters']['c1_rating']['attribute'] = 'input_voltage'
        report = self.ws.evaluate(rating)
        self.assertIn('no interpretable input_voltage', assertion(report, 'c1-voltage-rating')['reasons'][0])

    def test_division_by_interval_containing_zero_is_unbounded_unknown(self):
        document = copy.deepcopy(self.ws.document)
        document['parameters']['offset'] = {'source': 'declared', 'requirement': 'R-VIN', 'unit': 'V',
                                            'min': '-0.1 V', 'max': '0.1 V'}
        document['assertions'].append({'id': 'gain', 'requirement': 'R-VOUT', 'expression': 'vin / offset',
                                       'unit': '1', 'target': {'max': '1000'}})
        report = self.ws.evaluate(document)
        gain = assertion(report, 'gain')
        self.assertEqual((gain['verdict'], gain['computed']), ('unknown', None))
        self.assertIn('division by an interval containing zero', gain['reasons'][0])
        self.assertEqual(report['outcome'], 'unknown')

    def test_unproven_correlation_never_becomes_a_defect(self):
        document = copy.deepcopy(self.ws.document)
        document['assertions'] = [{'id': 'unity', 'requirement': 'R-VOUT', 'expression': 'r2 / r2',
                                   'unit': '1', 'target': {'min': '1', 'max': '1'}}]
        report = self.ws.evaluate(document)
        unity = assertion(report, 'unity')
        self.assertEqual(unity['monotonicity'], {'r2': 'unproven'})
        self.assertEqual(unity['corner_hull']['rational'], ['1', '1'])
        self.assertFalse(unity['exact'])
        self.assertEqual(unity['verdict'], 'unknown')
        self.assertIn('no attained parameter corner violates', unity['reasons'][0])

    def test_malformed_equations_are_malformed_not_evaluated(self):
        cases = {'vin * (r2': 'unbalanced', 'vin ** r2': 'unexpected', 'vin r2': 'unexpected', 'R2 + vin': 'invalid parameter',
                 'vin + r1': 'different units', 'vin * r9 / (r1 + r2)': 'undeclared', '': 'nonempty', '1e999999 * vin': 'decimal'}
        for expression, message in cases.items():
            document = copy.deepcopy(self.ws.document)
            document['assertions'][0]['expression'] = expression
            with self.subTest(expression=expression):
                report = self.ws.evaluate(document)
                self.assertEqual((report['execution'], report['outcome']), ('malformed', 'unknown'))
                self.assertIn(message, report['error'])
                self.assertNotIn('assertions', report)
        document = copy.deepcopy(self.ws.document)
        document['correlation'] = 'fully-correlated'
        self.assertEqual(self.ws.evaluate(document)['execution'], 'malformed')
        document = copy.deepcopy(self.ws.document)
        document['parameters']['vin']['tolerance_percent'] = 1
        self.assertEqual(self.ws.evaluate(document)['execution'], 'malformed')

    def test_report_lists_input_hashes_and_is_deterministic(self):
        first = self.ws.evaluate()
        self.assertEqual(first['inputs']['assertions'], {
            'path': DOC, 'sha256': hashlib.sha256((self.ws.root / DOC).read_bytes()).hexdigest()})
        self.assertEqual(first['inputs']['catalog']['manifest_sha256'], self.ws.manifest_sha256)
        self.assertEqual(first['inputs']['catalog']['database_sha256'], digest(self.ws.root / 'snapshot/catalog.sqlite3'))
        self.assertEqual(first['parameters']['r1']['interval']['rational'], ['21780', '22220'])
        self.assertEqual(first['parameters']['c1_rating']['interval']['rational'], ['16', '16'])
        self.assertEqual(canonical(first), canonical(self.ws.evaluate()))

    def test_wrong_catalog_pin_fails_closed(self):
        report = self.ws.evaluate(params={**self.ws.params(), 'manifest_sha256': '0' * 64})
        self.assertEqual((report['execution'], report['outcome']), ('failed', 'unknown'))

    def test_parts_manifest_binds_part_ids_to_lcsc(self):
        self.ws.write()
        parts = parts_manifest(self.ws, {'R1': 'C900001', 'R2': 'C900003'})
        document = copy.deepcopy(self.ws.document)
        document['parameters']['r2'] = {'source': 'catalog', 'part': 'R2', 'attribute': 'resistance', 'role': 'toleranced'}
        report = self.ws.evaluate(document, {**self.ws.params(), 'parts_manifest': parts})
        vout = assertion(report, 'vout-band')
        self.assertEqual(vout['verdict'], 'defect')
        self.assertEqual((vout['operands']['r2']['part'], vout['operands']['r2']['lcsc']), ('R2', 'C900003'))
        self.assertEqual(vout['operands']['r2']['parts_manifest']['sha256'], digest(self.ws.root / parts))

    def test_copper_entries_do_not_enter_catalog_mapping(self):
        from tests.test_copper_features import fixture
        # The copper record is authored by the shared original fixture.
        copper_root = self.ws.root / 'copper'
        copper_root.mkdir()
        copper = fixture(self, copper_root)['parts'][0]
        copper['id'] = 'jumper'
        copper['instances'][0]['reference'] = 'JP1'
        path = parts_manifest(self.ws, {'R1': 'C900001', 'R2': 'C900003'})
        manifest = read_json(self.ws.root / path)
        manifest['parts'].append(copper)
        write_json(self.ws.root / path, manifest)
        params = {**self.ws.params(), 'parts_manifest': path}
        report = self.ws.evaluate(params=params)
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report)
        document = copy.deepcopy(self.ws.document)
        document['parameters']['r1'].pop('lcsc')
        document['parameters']['r1']['part'] = copper['id']
        report = self.ws.evaluate(document, params)
        self.assertEqual((report['execution'], report['outcome']), ('failed', 'unknown'))
        self.assertIn('absent from the parts manifest', report['error'])
        # Capability validation must reject the same attempt to buy a copper feature.
        self.ws.write(document)
        scope = scope_for(self.ws, params)
        files = {p.relative_to(self.ws.root).as_posix(): digest(p)
                 for p in self.ws.root.rglob('*') if p.is_file()}
        descriptor = describe_check(scope['checks'][0], check_specs=scope['checks'],
                                    files=files, scope=scope, inputs=self.ws.root)
        self.assertEqual(descriptor['status'], 'unsupported', descriptor)
        self.assertIn('absent from a captured parts manifest', descriptor['unsupported_reasons'][0]['message'])
        manifest['catalog'] = None
        manifest['selection'] = None
        manifest['parts'] = [copper]
        write_json(self.ws.root / path, manifest)
        report = self.ws.evaluate(params=params)
        self.assertEqual(report['error'], 'parts manifest has no catalog snapshot for interval assertions')


class ExpressionTests(unittest.TestCase):
    def test_parse_and_correlated_enclosure(self):
        node = parse_expression('vin * r2 / (r1 + r2)')
        box = {'vin': (Fraction(9), Fraction(11)), 'r1': (Fraction(10), Fraction(11)), 'r2': (Fraction(10), Fraction(11))}
        (low, high), monotone = enclose(node, box)
        self.assertEqual((low, high), (Fraction(30, 7), Fraction(121, 21)))
        self.assertEqual(monotone, {'r1': 'nonincreasing', 'r2': 'nondecreasing', 'vin': 'nondecreasing'})
        # Wide boxes may leave a repeated parameter unproven; the enclosure stays sound, not exact.
        wide = {'vin': (Fraction(9), Fraction(11)), 'r1': (Fraction(1), Fraction(2)), 'r2': (Fraction(1), Fraction(2))}
        (low, high), monotone = enclose(node, wide)
        self.assertEqual(monotone['r2'], 'unproven')
        self.assertTrue(low <= Fraction(3) and Fraction(22, 3) <= high)
        self.assertEqual(parse_expression('-(a - 2.5)'), ('neg', ('-', ('sym', 'a'), ('num', Fraction(5, 2)))))
        with self.assertRaises(ValueError):
            parse_expression('(' * 80 + 'a' + ')' * 80)


def parts_manifest(ws, lcsc):
    blob = {'path': DOC, 'sha256': '0' * 64}
    catalog = read_json(ws.root / 'snapshot/manifest.json')
    document = {
        'schema': 'agent-ee.parts/1', 'declaration_source': {**blob, 'locator': 'owned'},
        'catalog': {'snapshot': 'snapshot', 'manifest_sha256': ws.manifest_sha256,
                    'database_sha256': catalog['database']['sha256'], 'snapshot_date': '2026-10-17'},
        'selection': {'requirements': blob, 'result': blob},
        'cad': {'manifest': blob, 'assignments': blob, 'members_sha256': '0' * 64},
        'property_fields': {'lcsc': 'LCSC', 'mpn': 'MPN', 'manufacturer': 'Manufacturer'},
        'population': 'native-dnp-optional-only/1',
        'parts': [{'id': pid, 'role': 'divider', 'required': True, 'instances': [{'sheet': '/', 'reference': pid}],
                   'selection_id': pid, 'lib_id': 'Device:R', 'footprint': 'Resistor_SMD:R_0402_1005Metric',
                   'lcsc': value, 'mpn': 'AE-IA-' + value[1:], 'manufacturer': 'Owned fixture', 'stock': 5000,
                   'datasheet': {'status': 'unavailable', 'required': False, 'reason': 'owned synthetic part'}}
                  for pid, value in sorted(lcsc.items())]}
    write_json(ws.root / 'parts.json', document)
    return 'parts.json'


def scope_for(ws, params=None, bind=('R-VIN', 'R-VOUT', 'R-C1-RATING')):
    def requirement(rid):
        return {'id': rid, 'statement': rid, 'source': {'path': DOC, 'locator': rid}, 'checks': ['intervals'],
                'intent': 'informational' if rid == 'R-VIN' else 'acceptance'}
    return {'schema': 'agent-ee.scope/3', 'requirements': [DOC, SYSTEM],
            'checks': [{'id': 'intervals', 'kind': 'interval_assertions', 'params': params or ws.params()}],
            'baseline': {'requirements': [requirement(rid) for rid in bind],
                         'targets': [SYSTEM], 'reference_architectures': [SYSTEM]}}


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.ws = Workspace(scratch.name)
        self.ws.write()

    def scope(self, params=None, bind=('R-VIN', 'R-VOUT', 'R-C1-RATING')):
        return scope_for(self.ws, params, bind)

    def describe(self, scope):
        files = {str(p.relative_to(self.ws.root)): digest(p) for p in self.ws.root.rglob('*') if p.is_file()}
        return describe_check(scope['checks'][0], check_specs=scope['checks'], files=files,
                              scope=scope, inputs=self.ws.root)

    def test_registration_and_predicates(self):
        self.assertIn('interval_assertions', KINDS)
        self.assertIs(CHECK_FUNCS['interval_assertions'], check_interval_assertions)
        descriptor = self.describe(self.scope())
        self.assertEqual(descriptor['status'], 'supported', descriptor)
        self.assertEqual(descriptor['predicates'], ['interval-assertion:vout-band', 'interval-assertion:c1-voltage-rating'])
        self.assertEqual(descriptor['immutable_inputs'], sorted([DOC, 'snapshot/catalog.sqlite3', 'snapshot/manifest.json']))
        self.assertTrue(descriptor['can_evaluate_bound'])

    def test_refusals(self):
        broken = copy.deepcopy(self.ws.document)
        broken['assertions'][0]['expression'] = 'vin * (r2'
        self.ws.write(broken)
        descriptor = self.describe(self.scope())
        self.assertEqual(descriptor['status'], 'unsupported')
        self.assertIn('unbalanced', descriptor['unsupported_reasons'][0]['message'])
        self.ws.write()
        for scope, message in ((self.scope(bind=('R-VIN', 'R-VOUT')), 'R-C1-RATING'),
                               (self.scope({'assertions': DOC}), 'pinned catalog snapshot'),
                               (self.scope({**self.ws.params(), 'extra': 1}), 'needs assertions'),
                               (self.scope({**self.ws.params(), 'assertions': 'missing.json'}), 'must be captured')):
            with self.subTest(message=message):
                descriptor = self.describe(scope)
                self.assertEqual(descriptor['status'], 'unsupported')
                self.assertIn(message, descriptor['unsupported_reasons'][0]['message'])


class PipelineTests(unittest.TestCase):
    """Capture, immutable baseline and sandboxed evaluation through run_checks."""

    def setUp(self):
        from toolchain import require_toolchain
        require_toolchain()
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.ws = Workspace(scratch.name)

    def run_scope(self, document=None, params=None):
        self.ws.write(document)
        scope = scope_for(self.ws, params)
        scope['inputs'] = [{'path': p, 'at': p} for p in ('requirements', 'snapshot')]
        write_json(self.ws.root / 'scope.json', scope)
        run = self.ws.root / f'run-{time.monotonic_ns()}'
        run.mkdir()
        from agent_ee.candidate import capture
        capture(self.ws.root / 'scope.json', run)
        return run, run_checks(run, time.monotonic() + 120)[0]

    def test_control_and_seeded_defect_through_the_pipeline(self):
        run, control = self.run_scope()
        self.assertEqual((control['execution'], control['outcome']), ('ok', 'pass'), control['summary'])
        baseline = read_json(run / 'baseline.json')
        self.assertTrue({DOC, 'snapshot/catalog.sqlite3', 'snapshot/manifest.json'} <= baseline['files'].keys())
        self.assertIn('work/intervals/intervals.json', control['evidence'])
        report = read_json(run / 'work/intervals/intervals.json')
        self.assertEqual(report['inputs']['assertions']['sha256'], digest(run / 'inputs' / DOC))
        self.assertEqual(report['inputs']['catalog']['manifest_sha256'], self.ws.manifest_sha256)
        self.assertEqual(assertion(report, 'vout-band')['computed']['rational'], ['891/292', '10201/3188'])
        self.assertEqual(control['details']['requirements'], {'R-VOUT': ['pass'], 'R-C1-RATING': ['pass']})

        seeded = copy.deepcopy(self.ws.document)
        seeded['parameters']['r2']['lcsc'] = 'C900003'
        _, defect = self.run_scope(seeded)
        self.assertEqual((defect['execution'], defect['outcome']), ('ok', 'defect'))
        self.assertIn('vout-band (R-VOUT)', defect['summary'])
        self.assertIn('R2, C900003, resistance', defect['summary'])

        untoleranced = copy.deepcopy(self.ws.document)
        untoleranced['parameters']['r2']['lcsc'] = 'C900005'
        _, unknown = self.run_scope(untoleranced)
        self.assertEqual((unknown['execution'], unknown['outcome']), ('ok', 'unknown'))

    def test_unverifiable_catalog_pin_is_failed_unknown(self):
        _, result = self.run_scope(params={**self.ws.params(), 'manifest_sha256': '0' * 64})
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'))
        self.assertIn('snapshot manifest hash mismatch', result['summary'])

    def test_malformed_equation_reaching_the_check_is_execution_malformed(self):
        run, _ = self.run_scope()
        broken = copy.deepcopy(self.ws.document)
        broken['assertions'][0]['expression'] = 'vin * (r2'
        (run / 'inputs' / DOC).write_text(json.dumps(broken))
        result = run_checks(run, time.monotonic() + 60)[0]
        self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'))
        self.assertIn('unbalanced', result['summary'])
        manifest = read_json(run / 'candidate.json')
        with self.assertRaises(CheckError) as raised:
            check_interval_assertions(Ctx(run, manifest, manifest['scope']['checks'][0], time.monotonic() + 30))
        self.assertEqual(raised.exception.execution, 'malformed')


if __name__ == '__main__':
    unittest.main()
