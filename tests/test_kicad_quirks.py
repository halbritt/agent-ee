"""KiCad report-format quirk guards (AGENTEE-66).

The ERC oracle pairs under fixtures/kicad-quirks/erc-scale/ were captured with
the released kicad-cli 10.0.6 and 10.0.7 over one original sheet; the text
report is the location oracle (see that directory's README). Native classes
run the real check and generator stage on the host toolchain.
"""
import copy
import json
import re
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from agent_ee import candidate, checks, executor, generate as g
from agent_ee.kicad_quirks import (VariantError, erc_coordinate_regime, erc_locations,
                                   schematic_variant_clauses, validate_bom_variants, variant_registry)

try:
    from toolchain import require_toolchain
except ImportError:  # run as tests.test_kicad_quirks from the repository root
    from tests.toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]
QUIRKS = ROOT / 'fixtures/kicad-quirks'
ERC = QUIRKS / 'erc-scale'
VARIANTS = QUIRKS / 'variants'
RPT_VIOLATION = re.compile(r'^\[([a-z_]+)\]: ')
RPT_ITEM = re.compile(r'^    @\((-?[0-9.]+) mm, (-?[0-9.]+) mm\): (.*)$')


def rpt_items(text):
    """[(type, x_mm, y_mm, description)] in report order from a kicad-cli ERC .rpt."""
    found, kind = [], None
    for line in text.splitlines():
        head = RPT_VIOLATION.match(line)
        if head:
            kind = head.group(1)
            continue
        item = RPT_ITEM.match(line)
        if item:
            found.append((kind, float(item.group(1)), float(item.group(2)), item.group(3)))
    return found


def located(locations, descriptions=True):
    return [(v['type'], *i['pos_mm'], i['description'] if descriptions else None)
            for v in locations for i in v['items']]


def positions(oracle):
    return [(kind, x, y, None) for kind, x, y, _ in oracle]


def report(version):
    return json.loads((ERC / f'erc-{version}.json').read_text())


class CoordinateRegimeTest(unittest.TestCase):
    def test_release_boundaries(self):
        for version, regime, scale in (('10.0.0', 'scale_x100', 100), ('10.0.5', 'scale_x100', 100),
                                       ('10.0.6', 'scale_x100', 100), ('10.0.7', 'native', 1),
                                       ('10.0.12', 'native', 1)):
            record = erc_coordinate_regime(version)
            self.assertEqual((record['regime'], record['scale'], record['version']), (regime, scale, version))
            self.assertNotIn('reason', record)

    def test_unclassifiable_versions_are_withheld_with_reason(self):
        for version in ('10.0.6-45-g0123abcd', '10.0.7-rc1', '10.0.0~rc1', '10.99.0', '9.0.4', '11.0.0',
                        '10.1.0', ' 10.0.7', '', None, 10.07, 'KiCad 10.0.7'):
            record = erc_coordinate_regime(version)
            self.assertEqual((record['regime'], record['scale']), ('withheld', None), version)
            self.assertTrue(record['reason'], version)


class ErcOracleTest(unittest.TestCase):
    def test_10_0_6_is_corrected_x100_and_matches_rpt_for_all_12_items(self):
        raw = report('10.0.6')
        oracle = rpt_items((ERC / 'erc-10.0.6.rpt').read_text())
        self.assertEqual(len(oracle), 12)
        # The uncorrected positions are the KiCad 25582 fault: 1/100 of the report.
        written = [(i['pos']['x'], i['pos']['y']) for s in raw['sheets'] for v in s['violations'] for i in v['items']]
        self.assertEqual([(round(x * 100, 4), round(y * 100, 4)) for x, y in written],
                         [(x, y) for _, x, y, _ in oracle])
        self.assertNotEqual(written, [(x, y) for _, x, y, _ in oracle])
        locations, coordinates = erc_locations(raw, erc_coordinate_regime('10.0.6'))
        self.assertEqual((coordinates['status'], coordinates['regime'], coordinates['scale']),
                         ('trusted', 'scale_x100', 100))
        self.assertEqual(located(locations, descriptions=False), positions(oracle))
        # 10.0.6 also scales lengths inside description text; they stay verbatim and are flagged.
        self.assertIn('uncorrected 1/100 scale', coordinates['limitation'])
        self.assertIn("Horizontal Wire, length 0.2540 mm", [d for *_, d in located(locations)])

    def test_10_0_7_is_unchanged_and_matches_rpt(self):
        raw = report('10.0.7')
        oracle = rpt_items((ERC / 'erc-10.0.7.rpt').read_text())
        self.assertEqual(len(oracle), 12)
        locations, coordinates = erc_locations(raw, erc_coordinate_regime('10.0.7'))
        self.assertEqual((coordinates['status'], coordinates['regime'], coordinates['scale']),
                         ('trusted', 'native', 1))
        self.assertNotIn('limitation', coordinates)
        self.assertEqual(located(locations), oracle)
        written = [[i['pos']['x'], i['pos']['y']] for s in raw['sheets'] for v in s['violations'] for i in v['items']]
        self.assertEqual([i['pos_mm'] for v in locations for i in v['items']], written)

    def test_both_releases_agree_after_correction(self):
        six, _ = erc_locations(report('10.0.6'), erc_coordinate_regime('10.0.6'))
        seven, _ = erc_locations(report('10.0.7'), erc_coordinate_regime('10.0.7'))
        self.assertEqual(located(six, descriptions=False), located(seven, descriptions=False))
        self.assertEqual([[i['uuid'] for i in v['items']] for v in six], [[i['uuid'] for i in v['items']] for v in seven])
        self.assertEqual(rpt_items((ERC / 'erc-10.0.6.rpt').read_text()),
                         rpt_items((ERC / 'erc-10.0.7.rpt').read_text()))

    def assertWithheld(self, raw, regime, reason):
        locations, coordinates = erc_locations(raw, regime)
        self.assertEqual((coordinates['status'], coordinates['scale']), ('withheld', None))
        self.assertIn(reason, coordinates['reason'])
        self.assertTrue(locations)
        for violation in locations:
            for item in violation['items']:
                self.assertNotIn('pos_mm', item)
                self.assertEqual(set(item), {'uuid', 'description'})

    def test_unknown_version_withholds_every_coordinate(self):
        raw = report('10.0.6')
        raw['kicad_version'] = '10.0.6-45-g0123abcd'
        self.assertWithheld(raw, erc_coordinate_regime('10.0.6-45-g0123abcd'), 'not a MAJOR.MINOR.PATCH release')

    def test_report_version_disagreeing_with_sealed_version_is_withheld(self):
        self.assertWithheld(report('10.0.6'), erc_coordinate_regime('10.0.7'),
                            "kicad_version '10.0.6' disagrees with the sealed kicad-cli version '10.0.7'")
        self.assertWithheld(report('10.0.7'), erc_coordinate_regime('10.0.6'), 'disagrees')

    def test_other_units_or_malformed_positions_are_withheld(self):
        raw = report('10.0.7')
        raw['coordinate_units'] = 'mils'
        self.assertWithheld(raw, erc_coordinate_regime('10.0.7'), "coordinate_units 'mils'")
        for damage in (lambda i: i.pop('pos'), lambda i: i['pos'].update(x='25.4'),
                       lambda i: i['pos'].update(y=float('nan')), lambda i: i['pos'].update(x=True)):
            raw = report('10.0.7')
            damage(raw['sheets'][0]['violations'][3]['items'][0])
            self.assertWithheld(raw, erc_coordinate_regime('10.0.7'), 'finite numeric x/y')
        raw = report('10.0.7')
        raw['sheets'][0]['violations'][0]['items'] = []
        self.assertWithheld(raw, erc_coordinate_regime('10.0.7'), 'has no located items')
        raw = report('10.0.7')
        raw['sheets'][0]['violations'][10]['items'].append('stray')
        self.assertWithheld(raw, erc_coordinate_regime('10.0.7'), 'non-object item')

    def test_inputs_are_not_mutated(self):
        raw = report('10.0.6')
        before = copy.deepcopy(raw)
        erc_locations(raw, erc_coordinate_regime('10.0.6'))
        self.assertEqual(raw, before)


class VariantRegistryTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='agent-ee-variants-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def project(self, body, schematic=None):
        pro = self.root / 'var.kicad_pro'
        pro.write_text(body if isinstance(body, str) else json.dumps(body))
        sch = self.root / 'var.kicad_sch'
        sch.write_bytes(schematic if schematic is not None else (VARIANTS / 'var.kicad_sch').read_bytes())
        return pro, sch

    def test_registered_variant_is_accepted(self):
        record = validate_bom_variants(VARIANTS / 'var.kicad_pro', [VARIANTS / 'var.kicad_sch'], ['LITE'])
        self.assertEqual(record, {'requested': ['LITE'], 'registered': ['LITE'],
                                  'schematic_clause_variants': ['LITE'], 'unregistered_clause_variants': []})

    def test_undefined_variant_is_refused(self):
        with self.assertRaisesRegex(VariantError, r"\['NOPE'\] are not registered in var.kicad_pro .*silently "
                                                  r"export the default"):
            validate_bom_variants(VARIANTS / 'var.kicad_pro', [VARIANTS / 'var.kicad_sch'], ['NOPE'])
        with self.assertRaisesRegex(VariantError, r"\['lite'\]"):
            validate_bom_variants(VARIANTS / 'var.kicad_pro', [VARIANTS / 'var.kicad_sch'], ['lite'])
        with self.assertRaisesRegex(VariantError, r"\['NOPE'\]"):
            validate_bom_variants(VARIANTS / 'var.kicad_pro', [VARIANTS / 'var.kicad_sch'], ['LITE', 'NOPE'])

    def test_variant_used_only_in_schematic_clauses_is_not_registered(self):
        pro, sch = self.project({'schematic': {'variants': []}})
        with self.assertRaisesRegex(VariantError, r"\['LITE'\] are not registered"):
            validate_bom_variants(pro, [sch], ['LITE'])
        pro, sch = self.project({'meta': {'version': 3}})
        with self.assertRaisesRegex(VariantError, r"\['LITE'\] are not registered .*registered: \[\]"):
            validate_bom_variants(pro, [sch], ['LITE'])

    def test_missing_or_malformed_registry_is_refused(self):
        with self.assertRaisesRegex(VariantError, 'unreadable'):
            validate_bom_variants(self.root / 'absent.kicad_pro', [VARIANTS / 'var.kicad_sch'], ['LITE'])
        for body, message in (('{', 'not valid JSON'), ([], 'no schematic settings'),
                              ({'schematic': []}, 'no schematic settings'),
                              ({'schematic': {'variants': {}}}, 'not a list'),
                              ({'schematic': {'variants': [{'description': 'x'}]}}, 'missing, empty or duplicate'),
                              ({'schematic': {'variants': [{'name': 'A'}, {'name': 'A'}]}}, 'duplicate')):
            with self.assertRaisesRegex(VariantError, message):
                variant_registry((body if isinstance(body, str) else json.dumps(body)).encode())

    def test_request_shape_is_checked(self):
        for requested in ([], [''], ['LITE', 'LITE'], 'LITE', [None]):
            with self.assertRaisesRegex(VariantError, 'distinct nonempty'):
                validate_bom_variants(VARIANTS / 'var.kicad_pro', [VARIANTS / 'var.kicad_sch'], requested)

    def test_exclude_from_bom_inside_a_variant_clause_is_refused(self):
        data = (VARIANTS / 'var.kicad_sch').read_bytes().replace(b'(dnp yes))', b'(exclude_from_bom yes))')
        self.assertIn(b'(exclude_from_bom yes)', data)
        names, problems = schematic_variant_clauses(data)
        self.assertEqual(names, {'LITE'})
        self.assertEqual(len(problems), 1)
        self.assertIn('exclude_from_bom, which kicad-cli cannot load', problems[0])
        pro, sch = self.project((VARIANTS / 'var.kicad_pro').read_text(), data)
        with self.assertRaisesRegex(VariantError, 'exclude_from_bom'):
            validate_bom_variants(pro, [sch], ['LITE'])

    def test_unrecognised_variant_tokens_and_unreadable_schematics_are_refused(self):
        data = (VARIANTS / 'var.kicad_sch').read_bytes().replace(b'(dnp yes))', b'(dnp yes) (populate no))')
        self.assertEqual(schematic_variant_clauses(data)[1], ["variant 'LITE' has unrecognised token 'populate'"])
        with self.assertRaisesRegex(VariantError, 'unreadable'):
            schematic_variant_clauses(b'(unterminated (variant')


def native_bom(root, argv):
    """Run kicad-cli BOM export over the variant fixture inside the sandbox; return the CSV text."""
    work = root / 'work'
    record = executor.execute(VARIANTS, work, argv, timeout=120)
    assert record['execution'] == 'ok' and record['exit_code'] == 0, record
    return (work / 'bom.csv').read_text()


class NativeVariantTest(unittest.TestCase):
    """The silent default fallback the guard exists for, observed on the pinned kicad-cli."""

    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)

    def bom(self, *variant):
        with tempfile.TemporaryDirectory(prefix='agent-ee-variant-bom-') as tmp:
            return native_bom(Path(tmp), ['kicad-cli', 'sch', 'export', 'bom', *variant, '--exclude-dnp',
                                          '-o', '/work/bom.csv', '/candidate/var.kicad_sch'])

    def test_registered_variant_applies_and_undefined_one_silently_falls_back(self):
        default, lite, undefined = self.bom(), self.bom('--variant', 'LITE'), self.bom('--variant', 'NOPE')
        self.assertIn('"R1"', default)
        self.assertNotIn('"R1"', lite)
        self.assertIn('"R2"', lite)
        # kicad-cli exits 0 with the default BOM: only the pre-run guard can refuse it.
        self.assertEqual(undefined, default)
        with self.assertRaises(VariantError):
            validate_bom_variants(VARIANTS / 'var.kicad_pro', [VARIANTS / 'var.kicad_sch'], ['NOPE'])


class NativeErcCheckTest(unittest.TestCase):
    """The real erc check records the pinned-version regime and the oracle locations."""

    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.run_dir = Path(tempfile.mkdtemp(prefix='agent-ee-erc-scale-'))
        candidate.capture(QUIRKS / 'erc-scale.scope.json', cls.run_dir)
        cls.result = checks.run_checks(cls.run_dir, time.monotonic() + 600)[0]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.run_dir, ignore_errors=True)

    def test_regime_is_recorded_from_the_sealed_version_run(self):
        r = self.result
        self.assertEqual((r['execution'], r['outcome']), ('ok', 'defect'), r['summary'])
        details = r['details']
        version_record = json.loads((self.run_dir / 'work/erc/kicad-version/execution.json').read_text())
        self.assertEqual(version_record['argv'], ['kicad-cli', 'version'])
        sealed = (self.run_dir / 'work/erc/kicad-version/stdout.log').read_text().strip()
        for name in ('execution.json', 'stdout.log'):
            self.assertIn(f'work/erc/kicad-version/{name}', r['evidence'])
        from agent_ee.fab_consistency import pinned_kicad_cli
        self.assertEqual(details['kicad_cli'], {'path': pinned_kicad_cli()[0], 'sha256': pinned_kicad_cli()[1],
                                                'version': sealed})
        expected = erc_coordinate_regime(sealed)
        coordinates = details['erc_coordinates']
        self.assertEqual((coordinates['version'], coordinates['regime']), (sealed, expected['regime']))
        self.assertEqual(coordinates['report_kicad_version'], details['kicad_version'])
        if expected['regime'] == 'withheld':
            self.assertEqual(coordinates['status'], 'withheld')
            self.assertTrue(any('withheld (info)' in limit for limit in r['limitations']))
        else:
            self.assertEqual(coordinates['status'], 'trusted')
            self.assertEqual(located(details['locations']), rpt_items((ERC / 'erc-10.0.7.rpt').read_text()))
            self.assertEqual(r['limitations'], [])

    def test_violation_counts_are_unchanged(self):
        details = self.result['details']
        self.assertEqual(details['violations'], 11)
        self.assertEqual(sum(len(v['items']) for v in details['locations']), 12)
        self.assertEqual(details['tool']['argv'][:3], ['kicad-cli', 'sch', 'erc'])


class GeneratorBomVariantTest(unittest.TestCase):
    """Stage 7 refuses an unregistered BOM variant before any kicad-cli command."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='agent-ee-fab-variant-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        source = self.root / 'reviewed'
        (source / 'project').mkdir(parents=True)
        (source / 'stock/template').mkdir(parents=True)
        for table in ('sym-lib-table', 'fp-lib-table'):
            (source / 'stock/template' / table).write_text(f'({table.replace("-", "_")})\n')
        shutil.copyfile(ROOT / 'fixtures/fab-export/fab.kicad_pcb', source / 'project/fab.kicad_pcb')
        shutil.copyfile(ROOT / 'fixtures/fab-export/fab.kicad_sch', source / 'project/fab.kicad_sch')
        (source / 'project/fab.kicad_pro').write_text(json.dumps(
            {'meta': {'filename': 'fab.kicad_pro', 'version': 3},
             'schematic': {'variants': [{'name': 'FULL', 'description': 'every part'}]}}))
        parts = self.root / 'parts.json'
        parts.write_text(json.dumps({'assembly_exclusions': []}))
        self.state = {'name': 'fab', 'reviewed_inputs': source, 'parts': parts}

    def stage_fab(self, variant):
        stage = g.Stage(self.root / 'run', 7, 'fab')
        stage.start()
        try:
            g.stage_fab(stage, self.state, {'bom_variant': variant})
            return stage, None
        except g.StageFailure as exc:
            return stage, str(exc)

    def test_undefined_variant_fails_before_kicad_cli_runs(self):
        stage, error = self.stage_fab('NOPE')
        self.assertIn("BOM variant 'NOPE' refused before kicad-cli ran", error)
        self.assertIn("['NOPE'] are not registered in fab.kicad_pro", error)
        self.assertEqual(stage.receipt['commands'], [])
        self.assertFalse((stage.dir / 'steps').exists())
        self.assertNotIn('fab_manifest', self.state)

    def test_missing_project_file_fails_before_kicad_cli_runs(self):
        (self.state['reviewed_inputs'] / 'project/fab.kicad_pro').unlink()
        stage, error = self.stage_fab('FULL')
        self.assertIn('unreadable', error)
        self.assertEqual(stage.receipt['commands'], [])

    def test_registered_variant_reaches_the_native_bom_export(self):
        require_toolchain(native=True)
        stage, error = self.stage_fab('FULL')
        self.assertIsNone(error)
        self.assertEqual(stage.receipt['details']['bom_variant']['registered'], ['FULL'])
        bom = next(c for c in stage.receipt['commands'] if c['label'] == 'bom')
        self.assertEqual(bom['argv'][-3:], ['--variant', 'FULL', '/candidate/project/fab.kicad_sch'])
        self.assertEqual(bom['exit_code'], 0)
        self.assertTrue(any(i['role'] == 'reviewed project file (variant registry)' for i in stage.receipt['inputs']))


if __name__ == '__main__':
    unittest.main()
