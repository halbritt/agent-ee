"""Offline JLCPCB cost estimate (AGENTEE-77): strict table, pure arithmetic and the real native pipeline."""
import copy
import json
import shutil
import tempfile
import time
import unittest
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

from agent_ee.assembly_readiness import load_catalog_snapshot
from agent_ee.jlc_cost import (designator_joints, entry_freshness, estimate, load_pricing_table,
                               read_pcb_summary, validate_params)
from agent_ee.storage import digest, read_json, write_json
from toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'fixtures' / 'jlc-cost'
TABLE = (FIXTURE / 'jlc-pricing-owned.json').read_bytes()
CATALOG = read_json(FIXTURE / 'catalog.json')
PARAMS = {'export_check': 'fab', 'catalog': 'cost/catalog.json', 'pricing_table': 'cost/jlc-pricing-owned.json',
          'assembly_tier': 'standard', 'boards': 5}


def table_bytes(**changes):
    doc = json.loads(TABLE)
    doc.update(changes)
    return json.dumps(doc).encode()


def entries(**overrides):
    """Table entries with per-id field overrides; an override of None drops that entry."""
    result = []
    for entry in json.loads(TABLE)['entries']:
        change = overrides.get(entry['id'], {})
        if change is not None:
            result.append({**entry, **change})
    return result


def catalog(tmp, parts):
    path = Path(tmp) / 'catalog.json'
    write_json(path, {**CATALOG, 'parts': parts})
    return load_catalog_snapshot(Path(tmp), 'catalog.json')


def footprint(ref, pads, layer='F.Cu'):
    """Pads are (number, kind) or (number, kind, extra native children such as a pad property)."""
    body = ''.join(f'(pad "{number}" {kind} rect (at 0 0) (size 1 1) {extra[0] if extra else ""}(layers "{layer}"))'
                   for number, kind, *extra in pads)
    return (f'(footprint "lib:FP" (layer "{layer}") (property "Reference" "{ref}" (at 0 0 0)) '
            f'(property "Value" "v" (at 0 0 0)) {body})')


def joint(smt, tht, excluded=(), unclassified=()):
    return {'smt': smt, 'tht': tht, 'tht_excluded': list(excluded), 'tht_unclassified': list(unclassified)}


def via(pad, count, prop):
    return {'pad': pad, 'count': count, 'property': prop,
            'reason': f'native {prop} via in SMD pad {pad}; not a soldered THT joint'}


class PricingTableTests(unittest.TestCase):
    def test_owned_fixture_is_versioned_dated_and_cited(self):
        table = load_pricing_table(TABLE)
        self.assertEqual((table['table_id'], table['version'], table['captured_on'], table['stale_before']),
                         ('agent-ee-owned-jlc-pricing', '1', date(2026, 10, 17), date(2026, 7, 1)))
        self.assertEqual(table['provenance']['values'], 'owned-test-values')
        self.assertTrue(table['provenance']['source_url'].startswith('https://'))
        joint = next(e for e in table['entries'] if e['id'] == 'standard-smt-joint')
        self.assertEqual(joint['value_usd'], Decimal('0.0020'))

    def test_malformed_tables_are_refused(self):
        bad_entry = {'id': 'x', 'kind': 'assembly_setup', 'tier': 'standard', 'value_usd': '1',
                     'observed_on': '2026-10-17'}
        cases = {
            'unknown top key': table_bytes(extra=1),
            'missing top key': json.dumps({k: v for k, v in json.loads(TABLE).items() if k != 'stale_before'}).encode(),
            'schema': table_bytes(schema='agent-ee.jlc-pricing/2'),
            'currency': table_bytes(currency='EUR'),
            'stale_before after captured_on': table_bytes(stale_before='2026-10-18'),
            'provenance values': table_bytes(provenance={**json.loads(TABLE)['provenance'], 'values': 'guessed'}),
            'empty entries': table_bytes(entries=[]),
            'unknown entry kind': table_bytes(entries=[{**bad_entry, 'kind': 'shipping'}]),
            'extra entry key': table_bytes(entries=[{**bad_entry, 'note': 'x'}]),
            'numeric value': table_bytes(entries=[{**bad_entry, 'value_usd': 1.0}]),
            'negative value': table_bytes(entries=[{**bad_entry, 'value_usd': '-1'}]),
            'non-decimal value': table_bytes(entries=[{**bad_entry, 'value_usd': 'NaN'}]),
            'unknown tier': table_bytes(entries=[{**bad_entry, 'tier': 'premium'}]),
            'observed after captured': table_bytes(entries=[{**bad_entry, 'observed_on': '2026-10-18'}]),
            'duplicate id': table_bytes(entries=entries(**{'standard-setup': {'id': 'standard-stencil'}})),
            'duplicate selector': table_bytes(entries=entries(**{'standard-setup': {'kind': 'assembly_stencil'}})),
            'zero step area': table_bytes(entries=entries(**{'pcb-2l-area': {'step_area_mm2': '0'}})),
            'bad max_sides': table_bytes(entries=entries(**{'economic-tier': {'max_sides': 3}})),
            'boolean layers': table_bytes(entries=entries(**{'pcb-2l-base': {'layers': True}})),
            'NaN constant': TABLE.replace(b'"layers": 2,', b'"layers": NaN,', 1),
            'duplicate JSON key': b'{"schema": "agent-ee.jlc-pricing/1", "schema": "agent-ee.jlc-pricing/1"}',
            'not JSON': b'pricing',
        }
        for name, data in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                load_pricing_table(data)

    def test_freshness_follows_stale_before_max_age_and_evaluation_date(self):
        table = load_pricing_table(TABLE)
        self.assertFalse(any(entry_freshness(table, date(2026, 10, 17), None).values()))
        aged = entry_freshness(table, date(2027, 1, 30), 90)
        self.assertTrue(all('pricing_max_age_days 90' in r[0] for r in aged.values()))
        future = entry_freshness(table, date(2026, 10, 1), None)
        self.assertTrue(all('after the evaluation date' in r[0] for r in future.values()))
        old = load_pricing_table(table_bytes(entries=entries(**{'standard-setup': {'observed_on': '2026-06-30'}})))
        stale = entry_freshness(old, date(2026, 10, 17), None)
        self.assertEqual([k for k, v in stale.items() if v], ['standard-setup'])
        unpolicied = entry_freshness(load_pricing_table(table_bytes(stale_before=None)), date(2026, 10, 17), None)
        self.assertTrue(all(r == ['no staleness policy: the table has no stale_before and no '
                                  'pricing_max_age_days'] for r in unpolicied.values()))

    def test_params_are_exact(self):
        self.assertEqual(validate_params(PARAMS)['catalog_sha256'], None)
        for change in ({'extra': 1}, {'assembly_tier': 'premium'}, {'boards': 0}, {'boards': True},
                       {'boards': 1.5}, {'as_of': '2026/10/17'}, {'pricing_max_age_days': -1},
                       {'catalog_sha256': 'ABC'}, {'export_check': ''}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_params({**PARAMS, **change})
        for key in ('export_check', 'catalog', 'pricing_table', 'assembly_tier', 'boards'):
            with self.subTest(missing=key), self.assertRaises(ValueError):
                validate_params({k: v for k, v in PARAMS.items() if k != key})


class InputReaderTests(unittest.TestCase):
    def test_joints_count_numbered_smd_and_thru_hole_pads_only(self):
        board = ('(kicad_pcb (version 20240108) '
                 + footprint('U1', [('1', 'smd'), ('2', 'smd'), ('9', 'smd'), ('', 'smd')])
                 + footprint('J1', [('1', 'thru_hole'), ('2', 'thru_hole'), ('', 'np_thru_hole')])
                 + footprint('P1', [('1', 'connect')]) + ')')
        self.assertEqual(designator_joints(board.encode()),
                         {'U1': joint(3, 0), 'J1': joint(0, 2), 'P1': joint(0, 0)})
        with self.assertRaises(ValueError):
            designator_joints(('(kicad_pcb ' + footprint('U1', []) + footprint('U1', []) + ')').encode())
        with self.assertRaises(ValueError):
            designator_joints(b'(kicad_pcb (footprint "x" (layer "F.Cu"))')

    def test_thermal_and_mechanical_vias_in_smd_pads_are_excluded_and_recorded(self):
        # AGENTEE-228: U1-style heatsink vias, J2-style mechanical vias, a mixed footprint and unmarked holes.
        heat, mech = '(property pad_prop_heatsink) ', '(property pad_prop_mechanical) '
        board = ('(kicad_pcb (version 20241229) '
                 + footprint('U1', [('1', 'smd'), ('2', 'smd'), ('41', 'smd')] + [('41', 'thru_hole', heat)] * 3)
                 + footprint('J2', [('1', 'smd'), ('2', 'smd')] + [('2', 'thru_hole', mech)] * 2)
                 + footprint('J3', [('A1', 'smd'), ('9', 'smd'), ('9', 'thru_hole', heat), ('9', 'thru_hole'),
                                    ('SH', 'thru_hole', mech), ('SH', 'thru_hole', mech), ('', 'np_thru_hole')])
                 + footprint('U5', [(n, 'smd') for n in '123456789'] + [('9', 'thru_hole')] * 8)
                 + footprint('Q1', [('1', 'thru_hole'), ('2', 'thru_hole'), ('3', 'thru_hole', heat)])
                 + footprint('U2', [('1', 'smd'), ('1', 'thru_hole', '(property pad_prop_castellated) '),
                                    ('1', 'thru_hole', heat + mech)])
                 + ')')
        joints = designator_joints(board.encode())
        self.assertEqual(joints['U1'], joint(3, 0, excluded=[via('41', 3, 'pad_prop_heatsink')]))
        self.assertEqual(joints['J2'], joint(2, 0, excluded=[via('2', 2, 'pad_prop_mechanical')]))
        # A same-numbered heatsink via is excluded; the unmarked hole and the anchors stay counted and reported.
        j3 = joints['J3']
        self.assertEqual((j3['smt'], j3['tht']), (2, 3))
        self.assertEqual(j3['tht_excluded'], [via('9', 1, 'pad_prop_heatsink')])
        self.assertEqual([(g['pad'], g['count']) for g in j3['tht_unclassified']], [('9', 1), ('SH', 2)])
        self.assertIn('lacks native pad_prop_heatsink or pad_prop_mechanical', j3['tht_unclassified'][0]['reason'])
        self.assertIn('no SMD pad SH', j3['tht_unclassified'][1]['reason'])
        # Unpropertied vias (AGENTEE-221 pre-patch U5) stay conservative THT joints, reported.
        self.assertEqual((joints['U5']['smt'], joints['U5']['tht'], joints['U5']['tht_excluded']), (9, 8, []))
        self.assertEqual([(g['pad'], g['count']) for g in joints['U5']['tht_unclassified']], [('9', 8)])
        # A property without a same-numbered SMD pad is not trusted as a via.
        self.assertEqual((joints['Q1']['tht'], joints['Q1']['tht_excluded']), (3, []))
        self.assertEqual([(g['pad'], g['count']) for g in joints['Q1']['tht_unclassified']], [('3', 1)])
        # Another property, or more than one, is not the thermal-via contract: counted.
        self.assertEqual((joints['U2']['smt'], joints['U2']['tht'], joints['U2']['tht_excluded']), (1, 2, []))
        self.assertEqual(joints['U2']['tht_unclassified'][0]['count'], 2)

    def test_pcb_summary_reads_native_units_and_refuses_gaps(self):
        native = {'board': {'has_outline': True, 'width': '36.0000 mm', 'height': '20.0000 mm',
                            'area': '720.00 mm²'}}
        self.assertEqual(read_pcb_summary(json.dumps(native).encode()),
                         {'width_mm': Decimal('36.0000'), 'height_mm': Decimal('20.0000'),
                          'native_area_mm2': Decimal('720.00')})
        for board in ({**native['board'], 'has_outline': False}, {**native['board'], 'width': '36 in'},
                      {**native['board'], 'height': '0 mm'}, {k: v for k, v in native['board'].items()
                                                              if k != 'area'}):
            with self.subTest(board=board), self.assertRaises(ValueError):
                read_pcb_summary(json.dumps({'board': board}).encode())
        with self.assertRaises(ValueError):
            read_pcb_summary(b'{"metadata": {}}')


SUMMARY = {'width_mm': Decimal('36'), 'height_mm': Decimal('20'), 'native_area_mm2': Decimal('720')}
BINDINGS = {'C1': 'C900001', 'C2': 'C900001', 'C10': 'C900001', 'J1': 'C900030', 'R1': 'C900010',
            'R2': 'C900047'}
SIDES = {'C1': 'Top', 'C2': 'Top', 'C10': 'Top', 'J1': 'Top', 'R1': 'Top', 'R2': 'Bottom'}
JOINTS = {**{ref: joint(2, 0) for ref in ('C1', 'C2', 'C10', 'R1', 'R2')}, 'J1': joint(0, 3)}


class EstimateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_case(self, table=TABLE, parts=None, tier='standard', boards=5, summary=SUMMARY, sides=SIDES,
                 layers=2, joints=JOINTS):
        table = load_pricing_table(table)
        snapshot = catalog(self.tmp.name, CATALOG['parts'] if parts is None else parts)
        fields, est = estimate(table, entry_freshness(table, date(2026, 10, 17), None), tier=tier, boards=boards,
                               layers=layers, summary=summary, bindings=BINDINGS, sides=sides, joints=joints,
                               catalog=snapshot)
        return fields, est, est.totals(('pcb', 'assembly', 'components'))

    def test_hand_computed_standard_estimate_cites_every_entry(self):
        fields, est, totals = self.run_case()
        # 4.00 pcb + 2x25 setup + 2x7.50 stencil + 50x0.002 smt + 15x0.035 tht + 2x3.00 extended + 0.653 parts
        self.assertEqual(totals['total'], {'min_usd': '76.2780', 'max_usd': '76.2780', 'complete': True})
        self.assertEqual(fields['class_counts'], {'basic': 1, 'preferred': 1, 'extended': 2, 'unknown': 0})
        self.assertEqual(fields['joints']['smt'], 50)
        ids = {e['id'] for e in load_pricing_table(TABLE)['entries']}
        for line in est.lines:
            self.assertTrue(line.get('entry') in ids or line.get('catalog_row') in BINDINGS.values(), line)
            self.assertEqual(Decimal(line['amount_usd']), Decimal(line['quantity']) * Decimal(line['unit_usd']))

    def test_excluded_vias_are_recorded_and_not_priced(self):
        # AGENTEE-228: excluded via pads change no THT line; unclassified holes stay priced.
        joints = {**JOINTS, 'C1': joint(2, 0, excluded=[via('2', 4, 'pad_prop_heatsink')]),
                  'J1': joint(0, 3, unclassified=[{'pad': '1', 'count': 1, 'reason': 'r'}])}
        fields, est, totals = self.run_case(joints=joints)
        self.assertEqual(totals['total'], {'min_usd': '76.2780', 'max_usd': '76.2780', 'complete': True})
        recorded = fields['joints']
        self.assertEqual((recorded['tht'], recorded['tht_per_board']), (15, 3))
        self.assertEqual((recorded['tht_excluded_per_board'], recorded['tht_unclassified_per_board']), (4, 1))
        self.assertEqual(recorded['per_designator']['C1']['tht_excluded'], [via('2', 4, 'pad_prop_heatsink')])
        tht = [line for line in est.lines if line['item'].startswith('tht solder joints')]
        self.assertEqual([Decimal(line['quantity']) for line in tht], [15])

    def test_extended_count_equals_snapshot_flags_and_classless_part_is_unknown_not_basic(self):
        parts = copy.deepcopy(CATALOG['parts'])
        for part in parts:
            if part['lcsc'] == 'C900030':
                del part['is_basic'], part['is_preferred']
            if part['lcsc'] == 'C900010':
                del part['is_basic']  # is_preferred=1 alone cannot exclude Basic
        fields, est, totals = self.run_case(parts=parts)
        self.assertEqual(fields['class_counts'], {'basic': 1, 'preferred': 0, 'extended': 1, 'unknown': 2})
        classes = {p['lcsc']: (p['class'], p['class_source']) for p in fields['parts']}
        self.assertEqual(classes['C900030'], ('unknown', 'catalog row lacks is_basic/is_preferred class flags'))
        self.assertEqual(classes['C900047'], ('extended', 'catalog snapshot flags'))
        gap = next(g for g in est.unresolved if g['item'] == 'unknown-class part loading')
        self.assertEqual((gap['min_usd'], gap['max_usd']), ('0', '6.00'))
        self.assertEqual(totals['total'], {'min_usd': '73.2780', 'max_usd': '79.2780', 'complete': False})
        # Without a Basic fee entry the unknown-class range is no longer bounded by the other fees.
        _, est, _ = self.run_case(parts=parts, table=table_bytes(entries=entries(**{'standard-basic-fee': None})))
        gap = next(g for g in est.unresolved if g['item'] == 'unknown-class part loading')
        self.assertEqual((gap['min_usd'], gap['max_usd']), ('0', None))
        # is_basic=1 alone decides Basic.
        parts = [{**p, 'is_basic': 1} if p['lcsc'] == 'C900047' else p for p in CATALOG['parts']]
        parts = [{k: v for k, v in p.items() if not (p['lcsc'] == 'C900047' and k == 'is_preferred')}
                 for p in parts]
        self.assertEqual(self.run_case(parts=parts)[0]['class_counts']['basic'], 2)

    def test_part_absent_from_catalog_is_unknown_class_and_unbounded_component_cost(self):
        parts = [p for p in CATALOG['parts'] if p['lcsc'] != 'C900047']
        fields, est, totals = self.run_case(parts=parts)
        self.assertEqual(fields['class_counts']['unknown'], 1)
        self.assertEqual(next(p for p in fields['parts'] if p['lcsc'] == 'C900047')['class_source'],
                         'absent from the catalog snapshot')
        self.assertEqual(totals['components']['max_usd'], None)
        self.assertEqual(totals['total'], {'min_usd': '73.2605', 'max_usd': None, 'complete': False})

    def test_area_surcharge_steps_and_out_of_box_board(self):
        big = {'width_mm': Decimal('100'), 'height_mm': Decimal('60'), 'native_area_mm2': Decimal('6000')}
        _, est, totals = self.run_case(summary=big, boards=10)  # 60000 mm2 ordered, 10000 above included
        line = next(l for l in est.lines if l.get('entry') == 'pcb-2l-area')
        self.assertEqual((line['quantity'], line['amount_usd']), ('1', '1.50'))
        _, est, _ = self.run_case(summary=big, boards=11)  # 16000 above: two started steps
        self.assertEqual(next(l for l in est.lines if l.get('entry') == 'pcb-2l-area')['quantity'], '2')
        rotated = {'width_mm': Decimal('60'), 'height_mm': Decimal('100'), 'native_area_mm2': Decimal('6000')}
        self.assertTrue(self.run_case(summary=rotated)[2]['pcb']['complete'])
        too_big = {'width_mm': Decimal('101'), 'height_mm': Decimal('20'), 'native_area_mm2': Decimal('2020')}
        _, est, totals = self.run_case(summary=too_big)
        self.assertEqual(totals['pcb'], {'min_usd': '0', 'max_usd': None, 'complete': False})
        self.assertIn('exceeds pcb-2l-base', est.unresolved[0]['reason'])

    def test_missing_entries_are_unresolved_never_filled_in(self):
        table = table_bytes(entries=entries(**{'standard-tht-joint': None, 'pcb-2l-base': None}))
        _, est, totals = self.run_case(table=table)
        self.assertEqual(sorted(g['item'] for g in est.unresolved), ['pcb', 'tht joints'])
        self.assertIsNone(totals['total']['max_usd'])
        _, est, _ = self.run_case(layers=6)
        self.assertIn('no pcb_base entry for 6 layers', est.unresolved[0]['reason'])

    def test_economic_tier_side_limit_and_missing_summary(self):
        _, est, totals = self.run_case(tier='economic')
        self.assertEqual([g['item'] for g in est.unresolved], ['assembly'])
        self.assertIn('economic-tier assembles at most 1 side', est.unresolved[0]['reason'])
        top = {ref: 'Top' for ref in SIDES}
        _, est, totals = self.run_case(tier='economic', sides=top, table=table_bytes(entries=entries(
            **{'standard-tht-joint': {'tier': 'economic'}})))
        self.assertTrue(totals['total']['complete'])
        _, est, totals = self.run_case(summary=None)
        self.assertEqual(est.unresolved[0]['group'], 'pcb')
        self.assertFalse(totals['pcb']['complete'])


# ------------------------------------------------------------- native pipeline

class NativeJlcCostTests(unittest.TestCase):
    """Real kicad-cli netlist and fab export of the owned fab-export board, then the estimates."""

    @classmethod
    def setUpClass(cls):
        require_toolchain(tools=('kicad-cli',))
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-jlc-cost-')
        root = cls.root = Path(cls.tmp.name)
        scope = read_json(FIXTURE / 'control.scope.json')
        scope['inputs'][0]['path'] = str(ROOT / 'fixtures' / 'fab-export')
        scope['inputs'][1]['path'] = str(FIXTURE)
        cls.scope = scope
        write_json(root / 'scope.json', scope)
        cls.rundir = root / 'run'
        cls.rundir.mkdir()
        capture(root / 'scope.json', cls.rundir)
        cls.manifest = read_json(cls.rundir / 'candidate.json')
        cls.results = {r['id']: r for r in run_checks(cls.rundir, time.monotonic() + 900)}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def report(self, rundir=None, cid='cost'):
        return read_json((rundir or self.rundir) / 'work' / cid / 'jlc-cost-estimate.json')

    def rerun(self, mutate, params=None):
        """Copy the sealed run, mutate the fab staging, then run one estimate through the dispatcher."""
        from agent_ee.checks import CHECK_FUNCS, CheckError, Ctx
        case = Path(tempfile.mkdtemp(dir=self.root))
        rundir = case / 'run'
        shutil.copytree(self.rundir, rundir, symlinks=True)
        shutil.rmtree(rundir / 'work' / 'cost')
        mutate(rundir / 'work' / 'fab' / 'fab')
        spec = {'id': 'cost', 'kind': 'jlc_cost_estimate', 'params': {**PARAMS, **(params or {})}}
        ctx = Ctx(rundir, self.manifest, spec, time.monotonic() + 120)
        try:
            CHECK_FUNCS['jlc_cost_estimate'](ctx)
        except CheckError as exc:
            ctx.result.update(execution=exc.execution, outcome='unknown', summary=str(exc))
        return rundir, ctx.result

    def test_prerequisites_ran_natively(self):
        for cid in ('netlist', 'fab'):
            self.assertEqual(self.results[cid]['execution'], 'ok', self.results[cid])
        self.assertEqual(self.results['fab']['outcome'], 'pass')

    def test_estimate_is_info_with_table_version_date_and_input_hashes(self):
        result = self.results['cost']
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'info'), result)
        self.assertIn('not a quote', result['summary'])
        report = self.report()
        self.assertTrue(report['not_a_quote'])
        table = report['pricing_table']
        self.assertEqual((table['table_id'], table['version'], table['captured_on'], table['stale_before']),
                         ('agent-ee-owned-jlc-pricing', '1', '2026-10-17', '2026-07-01'))
        self.assertEqual(table['sha256'], self.manifest['files']['cost/jlc-pricing-owned.json'])
        self.assertEqual(table['stale_entries'], {})
        staged = read_json(self.rundir / 'work' / 'fab' / 'fab' / 'manifest.json')['files']
        inputs = report['inputs']
        self.assertEqual(inputs['export']['bom_sha256'], staged['bom.csv'])
        self.assertEqual(inputs['export']['cpl_sha256'], staged['cpl.csv'])
        self.assertEqual(inputs['export']['pcb_summary_sha256'], staged['pcb_summary.json'])
        self.assertEqual(inputs['export']['manifest_sha256'],
                         digest(self.rundir / 'work' / 'fab' / 'fab' / 'manifest.json'))
        self.assertEqual(inputs['catalog']['sha256'], self.manifest['files']['cost/catalog.json'])
        self.assertEqual(inputs['board']['sha256'], self.manifest['files']['design/fab.kicad_pcb'])
        self.assertEqual(report['totals']['total'], {'min_usd': '76.2780', 'max_usd': '76.2780', 'complete': True})
        self.assertEqual(report['pcb']['copper_layers'], 2)
        self.assertEqual((report['pcb']['width_mm'], report['pcb']['height_mm']), ('36.0000', '20.0000'))
        self.assertEqual(report['sides'], ['Bottom', 'Top'])
        self.assertEqual((report['joints']['smt_per_board'], report['joints']['tht_per_board']), (10, 3))
        self.assertEqual(report['selection_objective'], {'unique_extended_parts': 2, 'unique_unknown_class_parts': 0})
        cited = {l['entry'] for l in report['line_items'] if 'entry' in l}
        self.assertEqual(cited, set(table['cited_entries']))
        self.assertIn('work/cost/jlc-cost-estimate.json', result['evidence'])
        self.assertIn('work/fab/fab/pcb_summary.json', result['evidence'])

    def test_economic_two_sided_board_is_info_with_unresolved_assembly(self):
        result = self.results['cost_economic']
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'info'), result)
        report = self.report(cid='cost_economic')
        self.assertEqual(report['totals']['assembly']['max_usd'], None)
        self.assertIn('at least', result['summary'])

    def test_evidence_seals_and_estimates_never_grade(self):
        from agent_ee.baseline import freeze_evidence, verify_evidence
        results = list(self.results.values())
        freeze_evidence(self.rundir, results)
        verify_evidence(self.rundir, results, required=True)
        for cid in ('cost', 'cost_economic'):
            self.assertNotIn(self.results[cid]['outcome'], ('pass', 'defect'))

    def test_missing_pcb_summary_gives_unknown_with_the_class_breakdown_kept(self):
        def drop(stage):
            (stage / 'pcb_summary.json').unlink()
            manifest = read_json(stage / 'manifest.json')
            del manifest['files']['pcb_summary.json']
            write_json(stage / 'manifest.json', manifest)
        rundir, result = self.rerun(drop)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'unknown'), result)
        report = self.report(rundir)
        self.assertEqual(report['pcb_summary_problem'], 'export manifest lists no pcb_summary.json')
        self.assertIsNone(report['pcb'])
        self.assertEqual(report['class_counts']['extended'], 2)
        self.assertFalse(report['totals']['total']['complete'])
        self.assertIsNone(report['inputs']['export']['pcb_summary_sha256'])

        def unlink_only(stage):
            (stage / 'pcb_summary.json').unlink()
        _, result = self.rerun(unlink_only)
        self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'), result)

        def corrupt(stage):
            (stage / 'pcb_summary.json').write_text('{"board": {"has_outline": false}}')
            manifest = read_json(stage / 'manifest.json')
            manifest['files']['pcb_summary.json'] = digest(stage / 'pcb_summary.json')
            write_json(stage / 'manifest.json', manifest)
        rundir, result = self.rerun(corrupt)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'unknown'), result)
        self.assertIn('no board outline', self.report(rundir)['pcb_summary_problem'])

    def test_tampered_staging_or_bom_cpl_disagreement_is_malformed(self):
        def tamper(stage):
            (stage / 'bom.csv').write_text((stage / 'bom.csv').read_text().replace('C900047', 'C900048'))
        _, result = self.rerun(tamper)
        self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'))
        self.assertIn('manifest inventory mismatch', result['summary'])

        def drop_cpl_row(stage):
            text = (stage / 'cpl.csv').read_text()
            (stage / 'cpl.csv').write_text(''.join(l for l in text.splitlines(True) if not l.startswith('R2,')))
            manifest = read_json(stage / 'manifest.json')
            manifest['files']['cpl.csv'] = digest(stage / 'cpl.csv')
            write_json(stage / 'manifest.json', manifest)
        _, result = self.rerun(drop_cpl_row)
        self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'))
        self.assertIn('differ from CPL designators', result['summary'])

    def test_stale_table_is_reported_and_the_outcome_stays_info(self):
        rundir, result = self.rerun(lambda stage: None, {'as_of': '2027-06-01', 'pricing_max_age_days': 90})
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'info'), result)
        report = self.report(rundir)
        self.assertEqual(sorted(report['pricing_table']['stale_entries']), report['pricing_table']['cited_entries'])
        self.assertTrue(all(line['entry_stale'] for line in report['line_items'] if 'entry' in line))
        self.assertIn('stale pricing entries', result['summary'])
        self.assertEqual(report['totals']['total']['min_usd'], '76.2780')

    def test_unknown_parameters_and_uncaptured_table_are_refused(self):
        _, result = self.rerun(lambda stage: None, {'boards': 0})
        self.assertEqual(result['execution'], 'unsupported')
        _, result = self.rerun(lambda stage: None, {'pricing_table': 'cost/absent.json'})
        self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'))

    def test_scope3_coverage_is_informational_only(self):
        from agent_ee.baseline import compile_baseline
        from agent_ee.capabilities import compile_coverage, describe_check
        files, inputs = self.manifest['files'], self.rundir / 'inputs'
        spec = self.scope['checks'][2]
        descriptor = describe_check(spec, check_specs=self.scope['checks'], files=files, scope=self.scope,
                                    inputs=inputs)
        self.assertEqual(descriptor['status'], 'supported', descriptor)
        self.assertEqual((descriptor['predicates'], descriptor['can_evaluate_bound']), ([], False))
        self.assertEqual(descriptor['prerequisites'], [{'check_id': 'fab', 'kind': 'fab_export'}])
        self.assertEqual(descriptor['immutable_inputs'], ['cost/catalog.json', 'cost/jlc-pricing-owned.json'])
        baseline = compile_baseline(self.scope, files, inputs)
        self.assertIn('cost/jlc-pricing-owned.json', baseline['files'])
        self.assertIn('cost/catalog.json', baseline['files'])
        acceptance = copy.deepcopy(self.scope)
        acceptance['baseline']['requirements'][1]['intent'] = 'acceptance'
        acceptance['baseline']['requirements'][1]['checks'] = ['cost']
        with self.assertRaisesRegex(ValueError, 'no executable bound coverage'):
            compile_coverage(acceptance, files, inputs)
        scope2 = copy.deepcopy(self.scope)
        scope2['schema'] = 'agent-ee.scope/2'
        for requirement in scope2['baseline']['requirements']:
            del requirement['intent']
        self.assertIn('cost/jlc-pricing-owned.json', compile_baseline(scope2, files, inputs)['files'])
        wrong = copy.deepcopy(self.scope)
        wrong['checks'][2]['params']['export_check'] = 'netlist'
        bad = describe_check(wrong['checks'][2], check_specs=wrong['checks'], files=files, scope=wrong,
                             inputs=inputs)
        self.assertEqual(bad['status'], 'unsupported')

    def test_registered_and_needs_no_native_tool(self):
        from agent_ee.capabilities import KINDS
        from agent_ee.checks import CHECK_FUNCS
        from agent_ee.preflight import capture_expectation
        self.assertIn('jlc_cost_estimate', KINDS)
        self.assertIn('jlc_cost_estimate', CHECK_FUNCS)
        only = copy.deepcopy(self.manifest)
        only['scope']['checks'] = [c for c in only['scope']['checks'] if c['kind'] == 'jlc_cost_estimate']
        expectation = capture_expectation({'run_id': 'r', 'policy_files': {}, 'runtime_files': {}},
                                          {**only, 'scope': {**only['scope'], 'schema': 'agent-ee.scope/1'}}, [],
                                          inputs=self.rundir / 'inputs')
        self.assertEqual(expectation['required_runtime'], ['/usr/bin/python3', 'bwrap'])


if __name__ == '__main__':
    unittest.main()


class NativeThermalViaJointTests(unittest.TestCase):
    """AGENTEE-228 on real footprints: KiCad's installed library and the owned AGENTEE-221 power pad,
    placed and saved by pcbnew, then counted from the saved board bytes."""

    def test_saved_board_joints_match_pcbnew_pad_properties(self):
        require_toolchain(native=True)
        import pcbnew
        from test_footprint_library import HEATSINK, power_pad
        stock = Path('/usr/share/kicad/footprints')
        picks = {'U1': (stock / 'RF_Module.pretty', 'ESP32-S3-WROOM-1'),
                 'J2': (stock / 'Connector_Coaxial.pretty', 'SMA_Molex_73251-1153_EdgeMount_Horizontal'),
                 'J3': (stock / 'Connector_USB.pretty', 'USB_C_Receptacle_HRO_TYPE-C-31-M-12')}
        with tempfile.TemporaryDirectory() as tmp:
            owned = Path(tmp) / 'Owned.pretty'
            owned.mkdir()
            for name, extra in (('So8Bare', ''), ('So8Tv', HEATSINK)):
                (owned / f'{name}.kicad_mod').write_text(power_pad(extra).replace('"So8Tv"', f'"{name}"', 1))
            picks.update(U5=(owned, 'So8Bare'), U6=(owned, 'So8Tv'))
            board, expected = pcbnew.BOARD(), {}
            via_props = {pcbnew.PAD_PROP_HEATSINK: 'pad_prop_heatsink',
                         pcbnew.PAD_PROP_MECHANICAL: 'pad_prop_mechanical'}
            for ref, (lib, name) in picks.items():
                fp = pcbnew.FootprintLoad(str(lib), name)
                self.assertIsNotNone(fp, name)
                fp.SetReference(ref)
                board.Add(fp)
                pads = [p for p in fp.Pads() if p.GetNumber()]
                smd = {p.GetNumber() for p in pads if p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD}
                pth = [p for p in pads if p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH]
                vias = Counter((p.GetNumber(), via_props[p.GetProperty()]) for p in pth
                               if p.GetProperty() in via_props and p.GetNumber() in smd)
                expected[ref] = (len(pads) - len(pth), len(pth) - sum(vias.values()),
                                 sorted((pad, n, prop) for (pad, prop), n in vias.items()))
            path = Path(tmp) / 'thermal.kicad_pcb'
            self.assertTrue(pcbnew.SaveBoard(str(path), board))
            joints = designator_joints(path.read_bytes())
        found = {ref: (j['smt'], j['tht'], [(g['pad'], g['count'], g['property']) for g in j['tht_excluded']])
                 for ref, j in joints.items()}
        self.assertEqual(found, expected)
        # The pod cases: 12 heatsink vias in U1 pad 41, 2 mechanical vias in J2 pad 2, 8 in a patched power pad.
        self.assertEqual(found['U1'][1:], (0, [('41', 12, 'pad_prop_heatsink')]))
        self.assertEqual(found['J2'][1:], (0, [('2', 2, 'pad_prop_mechanical')]))
        self.assertEqual(found['U6'][1:], (0, [('9', 8, 'pad_prop_heatsink')]))
        # Unmarked power-pad vias and USB-C shield anchors (mechanical, no SMD pad SH) stay THT joints, reported.
        self.assertEqual(found['U5'][1:], (8, []))
        self.assertEqual(found['J3'][1:], (4, []))
        self.assertEqual([(g['pad'], g['count']) for g in joints['U5']['tht_unclassified']], [('9', 8)])
        self.assertEqual([(g['pad'], g['count']) for g in joints['J3']['tht_unclassified']], [('SH', 4)])
        for ref in ('U1', 'J2', 'U6'):
            self.assertEqual(joints[ref]['tht_unclassified'], [])
