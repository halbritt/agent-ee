"""RF geometry on the routed board: grounded CPW, stitching pitch, branch lengths and return guard, fail-closed."""
import copy
import json
import math
import tempfile
import time
import unittest
from pathlib import Path

from toolchain import require_toolchain

from agent_ee import candidate, checks
from agent_ee.baseline import freeze_evidence, validate_witnesses, verify_evidence
from agent_ee.capabilities import describe_check
from agent_ee.rf_geometry import (
    GUARD_LIMITATIONS, LIMITATIONS, evaluate, parse_declaration, predicates, probe_request,
)
from agent_ee.storage import digest, write_json

import rf_geometry_fixture as fx

TRUNK_MM = 10 + 2 * math.sqrt(2) + 12


def parse(doc, sources=None):
    def load(rel):
        if sources is None or rel not in sources:
            raise ValueError(f'source must be a captured input: {rel!r}')
        return {'document': sources[rel], 'sha256': '0' * 64}
    return parse_declaration(json.dumps(doc).encode(), load)[1]


class DeclarationTests(unittest.TestCase):
    def test_source_pointer_in_mil_resolves_the_captured_values_exactly(self):
        resolved = parse(fx.sourced_declaration(), {'project/rf-calculation.json': fx.CALCULATION})
        self.assertEqual((resolved['cpw']['width_mm'], resolved['cpw']['gap_mm']), (0.334416796875, 0.2032))
        self.assertEqual(resolved['source']['unit'], 'mil')

    def test_source_and_restated_targets_conflict_and_bad_units_or_pointers_refuse(self):
        sources = {'project/rf-calculation.json': fx.CALCULATION}
        restated = fx.sourced_declaration()
        restated['cpw']['width_mm'] = 0.3344
        cases = {'restated': restated,
                 'unit': {**fx.sourced_declaration(), 'source': {**fx.sourced_declaration()['source'],
                                                                 'unit': 'inch'}},
                 'pointer': {**fx.sourced_declaration(), 'source': {**fx.sourced_declaration()['source'],
                                                                    'gap_pointer': '/geometry_mil/D9'}},
                 'nonnumeric': {**fx.sourced_declaration(), 'source': {**fx.sourced_declaration()['source'],
                                                                       'gap_pointer': '/schema'}}}
        for name, doc in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                parse(doc, sources)
        with self.assertRaisesRegex(ValueError, 'captured input'):
            parse(fx.sourced_declaration())

    def test_structural_declaration_errors_refuse(self):
        stubs = fx.declaration()['topology']['stubs']
        cases = {
            'no sections': {k: v for k, v in fx.declaration().items() if k not in ('cpw', 'stitching', 'topology')},
            'same net': fx.declaration(ground_net=fx.RF_NET),
            'same layer': fx.declaration(reference_layer='F.Cu'),
            'bad layer': fx.declaration(reference_layer='In1'),
            'stitching without topology': {k: v for k, v in fx.declaration().items() if k != 'topology'},
            'duplicate terminal': fx.declaration(topology={'trunk': {'from': 'J2.1', 'to': 'U3.11',
                                                                     'max_length_mm': 30},
                                                           'stubs': [stubs[0], {**stubs[1], 'terminal': 'D3.1'}]}),
            'unknown parent': fx.declaration(topology={'trunk': {'from': 'J2.1', 'to': 'U3.11',
                                                                 'max_length_mm': 30},
                                                       'stubs': [{**stubs[0], 'from': 'nowhere'}]}),
            'bad terminal': fx.declaration(topology={'trunk': {'from': 'J2', 'to': 'U3.11', 'max_length_mm': 30},
                                                     'stubs': []}),
            'band': fx.declaration(stitching={'max_pitch_mm': 2.5, 'band_mm': [1.5, 0.5]}),
            'tolerance': fx.declaration(cpw={'width_mm': 0.33, 'gap_mm': 0.2, 'width_tolerance_mm': -1,
                                             'gap_tolerance_mm': 0.02}),
            'extra key': fx.declaration(impedance_ohm=50),
        }
        for name, doc in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                parse(doc)

    def test_null_bounds_are_not_predicates(self):
        full = predicates(parse(fx.declaration()))
        self.assertEqual(full, ['rf-geometry:cpw-structure', 'rf-geometry:cpw-reference', 'rf-geometry:cpw-width',
                                'rf-geometry:cpw-gap', 'rf-geometry:stitching-pitch', 'rf-geometry:branch-topology',
                                'rf-geometry:branch-length:trunk', 'rf-geometry:branch-length:esd',
                                'rf-geometry:branch-length:bias'])
        doc = fx.declaration(stitching={'max_pitch_mm': None, 'band_mm': [0.5, 1.5]})
        doc['cpw'] = {**doc['cpw'], 'gap_tolerance_mm': None}
        doc['topology'] = copy.deepcopy(doc['topology'])
        doc['topology']['trunk']['max_length_mm'] = None
        reduced = predicates(parse(doc))
        for missing in ('rf-geometry:cpw-gap', 'rf-geometry:stitching-pitch', 'rf-geometry:branch-length:trunk'):
            self.assertNotIn(missing, reduced)


class GuardDeclarationTests(unittest.TestCase):
    def test_inline_width_needs_a_recorded_basis_and_offers_the_guard_predicate(self):
        resolved = parse(fx.declaration(guard={'width_mm': 1.0, 'basis': 'owned control'}))
        self.assertEqual(resolved['guard'], {'width_mm': 1.0, 'basis': 'owned control'})
        self.assertIn('rf-geometry:return-guard', predicates(resolved))

    def test_null_width_is_declared_unknown_and_not_a_predicate(self):
        resolved = parse(fx.declaration(guard={'width_mm': None, 'basis': 'width not yet determined'}))
        self.assertIsNone(resolved['guard']['width_mm'])
        self.assertNotIn('rf-geometry:return-guard', predicates(resolved))
        self.assertNotIn('rf-geometry:return-guard', predicates(parse(fx.declaration())))

    def test_width_by_pointer_into_a_captured_record_is_bound_to_its_hash(self):
        sources = {'project/guard.json': fx.GUARD_RECORD}
        resolved = parse(fx.declaration(guard={'source': {'path': 'project/guard.json', 'pointer': '/guard_mm'},
                                               'basis': 'captured record'}), sources)
        self.assertEqual(resolved['guard']['width_mm'], 1.0)
        self.assertEqual(resolved['guard']['source'], {'path': 'project/guard.json', 'sha256': '0' * 64,
                                                       'pointer': '/guard_mm', 'unit': 'mm'})
        mil = parse(fx.declaration(guard={'source': {'path': 'project/guard.json', 'pointer': '/guard_mm',
                                                     'unit': 'mil'}, 'basis': 'captured record'}), sources)
        self.assertEqual(mil['guard']['width_mm'], 0.0254)

    def test_guard_only_declaration_is_accepted(self):
        doc = {k: v for k, v in fx.declaration().items() if k not in ('cpw', 'stitching', 'topology')}
        doc['guard'] = {'width_mm': 1.0, 'basis': 'owned control'}
        self.assertEqual(predicates(parse(doc)), ['rf-geometry:return-guard'])

    def test_no_default_width_and_malformed_guards_refuse(self):
        sources = {'project/guard.json': fx.GUARD_RECORD}
        source = {'path': 'project/guard.json', 'pointer': '/guard_mm'}
        cases = {'empty': {}, 'no width': {'basis': 'x'}, 'no basis': {'width_mm': 1.0},
                 'empty basis': {'width_mm': 1.0, 'basis': ''}, 'both': {'width_mm': 1.0, 'source': source,
                                                                        'basis': 'x'},
                 'zero': {'width_mm': 0, 'basis': 'x'}, 'negative': {'width_mm': -1.0, 'basis': 'x'},
                 'text': {'width_mm': '1.19', 'basis': 'x'}, 'extra': {'width_mm': 1.0, 'basis': 'x', 'per_side': 1},
                 'unit': {'source': {**source, 'unit': 'inch'}, 'basis': 'x'},
                 'pointer': {'source': {**source, 'pointer': '/missing'}, 'basis': 'x'},
                 'nonnumeric': {'source': {**source, 'pointer': '/basis'}, 'basis': 'x'},
                 'null source': {'source': {**source, 'pointer': '/none'}, 'basis': 'x'}}
        for name, guard in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                parse(fx.declaration(guard=guard), {'project/guard.json': {**fx.GUARD_RECORD, 'none': None}})
        with self.assertRaisesRegex(ValueError, 'captured input'):
            parse(fx.declaration(guard={'source': source, 'basis': 'x'}))
        parse(fx.declaration(guard={'source': source, 'basis': 'x'}), sources)


class NativeBase(unittest.TestCase):
    """In-process pcbnew facts from owned boards, evaluated by the coordinator code."""
    cache = {}

    def setUp(self):
        require_toolchain(native=True)

    def evaluate(self, doc=None, **board):
        resolved = parse(doc or fx.declaration())
        return evaluate(resolved, self.native(resolved, **board))

    def native(self, resolved, **board):
        from agent_ee.rf_geometry import extract
        request = probe_request(resolved)
        rules = board.pop('rules', None)
        key = json.dumps([board, request, rules], sort_keys=True)
        if key not in self.cache:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'board.kicad_pcb'
                fx.make_board(path, **board)
                if rules is not None:
                    (Path(tmp) / 'board.kicad_dru').write_text(rules)
                self.cache[key] = json.dumps(extract(path, request))
        return json.loads(self.cache[key])

    @staticmethod
    def cpw(out):
        return {k: (v['status'], v.get('reason')) for k, v in out['sections']['cpw']['items'].items()}

    @staticmethod
    def branches(out):
        return {b['name']: (b['status'], b.get('length_mm')) for b in out['sections']['topology']['branches']}


class NativeEvaluationTests(NativeBase):
    def test_compliant_board_passes_every_section(self):
        out = self.evaluate()
        self.assertEqual(out['status'], 'pass', json.dumps(out)[:3000])
        self.assertEqual({k: s for k, (s, _) in self.cpw(out).items()},
                         {'width': 'pass', 'layer': 'pass', 'structure': 'pass', 'gap': 'pass', 'reference': 'pass'})
        gap = out['sections']['cpw']['items']['gap']
        self.assertGreater(gap['graded_edge_samples'], 500)
        for entry in gap['minimum']:
            self.assertAlmostEqual(entry['min_gap_mm'], 0.2032, delta=0.0254)
        self.assertEqual(self.branches(out), {'trunk': ('pass', round(TRUNK_MM, 6)), 'esd': ('pass', 3.0),
                                              'bias': ('pass', 3.0)})
        for branch in out['sections']['stitching']['branches']:
            self.assertEqual(branch['status'], 'pass')
            self.assertLessEqual(max(s['max_gap_mm'] for s in branch['sides'].values()), 2.5)

    def test_gap_outside_tolerance_on_either_side_is_a_gap_defect(self):
        for gap, reason in ((0.3, 'gap_above_tolerance'), (0.15, 'gap_below_tolerance')):
            with self.subTest(gap=gap):
                out = self.evaluate(gap=gap)
                items = out['sections']['cpw']['items']
                self.assertEqual((out['status'], items['gap']['status'], items['structure']['status']),
                                 ('defect', 'defect', 'pass'))
                self.assertEqual({v['reason'] for v in items['gap']['violations']}, {reason})

    def test_width_outside_tolerance_is_a_width_defect(self):
        items = self.evaluate(width=0.25)['sections']['cpw']['items']
        self.assertEqual(items['width']['status'], 'defect')
        self.assertEqual(items['width']['measured_mm'], [0.25])

    def test_missing_coplanar_ground_is_absent_structure_never_microstrip_pass(self):
        out = self.evaluate(ground_signal=False)
        structure = out['sections']['cpw']['items']['structure']
        self.assertEqual((out['status'], structure['status'], structure['reason']),
                         ('defect', 'defect', 'coplanar_ground_absent'))
        self.assertIn('not graded as microstrip', structure['statement'])
        self.assertNotEqual(out['sections']['cpw']['items']['gap']['status'], 'pass')

    def test_reference_void_and_absent_reference_are_defects(self):
        self.assertEqual(self.cpw(self.evaluate(reference_slot=True))['reference'], ('defect', 'reference_void'))
        self.assertEqual(self.cpw(self.evaluate(reference=False))['reference'], ('defect', 'reference_absent'))

    def test_foreign_copper_in_the_gap_is_a_defect(self):
        gap = self.evaluate(foreign=True)['sections']['cpw']['items']['gap']
        self.assertEqual(gap['status'], 'defect')
        self.assertIn('foreign_copper_in_gap', {v['reason'] for v in gap['violations']})

    def test_unfilled_zones_leave_cpw_unknown_never_pass(self):
        out = self.evaluate(fill=False)
        cpw = self.cpw(out)
        self.assertEqual({k: cpw[k][0] for k in ('structure', 'gap', 'reference')},
                         {'structure': 'unknown', 'gap': 'unknown', 'reference': 'unknown'})
        self.assertEqual(out['status'], 'unknown')

    def test_null_tolerances_pitch_and_length_bounds_are_unknown_never_pass(self):
        doc = fx.declaration(stitching={'max_pitch_mm': None, 'band_mm': [0.5, 1.5]})
        doc['cpw'] = {**doc['cpw'], 'width_tolerance_mm': None, 'gap_tolerance_mm': None}
        doc['topology'] = copy.deepcopy(doc['topology'])
        doc['topology']['stubs'][0]['max_length_mm'] = None
        out = self.evaluate(doc)
        cpw = self.cpw(out)
        self.assertEqual((cpw['width'][0], cpw['gap'][0]), ('unknown', 'unknown'))
        self.assertEqual({b['status'] for b in out['sections']['stitching']['branches']}, {'unknown'})
        self.assertEqual(self.branches(out)['esd'], ('unknown', 3.0))
        self.assertEqual(out['status'], 'unknown')

    def test_gap_tolerance_within_native_polygon_error_is_unknown(self):
        doc = fx.declaration()
        doc['cpw'] = {**doc['cpw'], 'gap_tolerance_mm': 0.000001}
        gap = self.evaluate(doc)['sections']['cpw']['items']['gap']
        self.assertEqual(gap['status'], 'unknown')
        self.assertIn('native polygon error', gap['reason'])

    def test_missing_stitching_vias_exceed_the_pitch_with_located_intervals(self):
        out = self.evaluate(drop_vias=(4, 6, 8, 10))
        trunk = out['sections']['stitching']['branches'][0]
        self.assertEqual((trunk['branch'], trunk['status']), ('trunk', 'defect'))
        violations = [v for side in trunk['sides'].values() for v in side.get('violations', [])]
        self.assertTrue(violations)
        self.assertTrue(all(v['gap_mm'] > 2.5 for v in violations))
        everywhere = self.evaluate(vias=False)['sections']['stitching']
        self.assertEqual({b['status'] for b in everywhere['branches']}, {'defect'})
        self.assertEqual(everywhere['stitching_vias_considered'], 0)

    def test_branch_length_bound_is_exact_per_branch(self):
        long = self.evaluate(d3_stub=[(8.0, 8.0), (8.0, 6.5), (6.0, 6.5), (6.0, 5.0), (8.0, 5.0)])
        self.assertEqual(self.branches(long)['esd'], ('defect', 7.0))
        self.assertEqual(self.branches(long)['bias'], ('pass', 3.0))
        doc = fx.declaration()
        doc['topology'] = copy.deepcopy(doc['topology'])
        doc['topology']['stubs'][0]['max_length_mm'] = 3.0
        doc['topology']['stubs'][1]['max_length_mm'] = 2.999
        self.assertEqual(self.branches(self.evaluate(doc))['esd'][0], 'pass')
        self.assertEqual(self.branches(self.evaluate(doc))['bias'][0], 'defect')

    def test_arc_bend_length_is_the_arc_not_the_chord(self):
        trunk = self.branches(self.evaluate(arc_bend=True))['trunk']
        self.assertAlmostEqual(trunk[1], 10 + math.pi + 12, delta=0.001)

    def test_topology_must_match_the_declared_tree(self):
        dangling = self.evaluate(dangling=True)['sections']['topology']
        self.assertEqual([(f['reason'], f['status']) for f in dangling['findings']], [('undeclared_copper', 'defect')])
        extra = self.evaluate(undeclared_pad=True)['sections']['topology']
        self.assertIn(('undeclared_terminal', 'defect'), [(f['reason'], f['status']) for f in extra['findings']])
        self.assertEqual(extra['findings'][0]['terminals'], ['C9.1'])

    def test_unrouted_branch_is_unknown_not_pass(self):
        out = self.evaluate(unrouted_l3=True)
        self.assertEqual(self.branches(out)['bias'], ('unknown', None))
        self.assertEqual(out['sections']['stitching']['branches'][2]['status'], 'unknown')
        self.assertEqual(out['status'], 'unknown')

    def test_layer_hop_is_a_layer_defect_and_counts_via_transitions(self):
        out = self.evaluate(b_cu_hop=True)
        self.assertEqual(self.cpw(out)['layer'][0], 'defect')
        trunk = out['sections']['topology']['branches'][0]
        self.assertEqual((trunk['status'], trunk['via_transitions']), ('pass', 2))

    def test_absent_net_is_unknown(self):
        out = self.evaluate(fx.declaration(net='/NOT_ON_BOARD'))
        self.assertEqual(out['sections']['cpw']['items']['structure'],
                         {'status': 'unknown', 'reason': 'declared RF net absent from board'})
        self.assertNotEqual(out['status'], 'pass')

    def test_malformed_native_report_refuses(self):
        resolved = parse(fx.declaration())
        good = self.native(resolved)
        evaluate(resolved, copy.deepcopy(good))
        mutations = {'schema': lambda n: n.update(schema='other'),
                     'copper': lambda n: n['copper'].pop('ground_reference'),
                     'track': lambda n: n['tracks'][0].update(width_nm=0),
                     'via uuid': lambda n: n['vias'][0].pop('uuid'),
                     'null copper': lambda n: n['copper'].update(ground_signal=None),
                     'pads': lambda n: n['all_rf_net_pads'].append('X9.9')}
        for name, mutate in mutations.items():
            native = copy.deepcopy(good)
            mutate(native)
            with self.subTest(name=name), self.assertRaises(ValueError):
                evaluate(resolved, native)


class NativeGuardTests(NativeBase):
    """The return-corridor guard on owned boards."""
    FOREIGN = (11.5, 6.95)  # /OTHER via 1.05 mm from the trunk centreline: outside the CPW strip, inside 1 mm guard

    def guard(self, width=1.0, doc=None, **board):
        doc = doc or fx.declaration(guard={'width_mm': width, 'basis': 'owned control'})
        out = self.evaluate(doc, **board)
        return out, out['sections']['guard']

    def test_compliant_board_guard_passes_and_leaves_other_sections_unchanged(self):
        out, guard = self.guard()
        self.assertEqual((out['status'], guard['status']), ('pass', 'pass'), json.dumps(guard)[:2000])
        self.assertEqual((guard['uncovered_mm2'], guard['corridor_tracks'], guard['width_mm']), (0.0, 5, 1.0))
        self.assertIn('not RF qualification', guard['scope'])
        # Same native facts: declaring a guard changes no other section.
        resolved = parse(fx.declaration(guard={'width_mm': 1.0, 'basis': 'owned control'}))
        native = self.native(resolved)
        plain = evaluate(parse(fx.declaration()), copy.deepcopy(native))
        self.assertEqual({k: v for k, v in evaluate(resolved, native)['sections'].items() if k != 'guard'},
                         plain['sections'])
        self.assertNotIn('guard', self.native(parse(fx.declaration())))

    def test_foreign_antipad_inside_the_grown_corridor_is_a_defect_the_cpw_strip_misses(self):
        out, guard = self.guard(foreign_via=self.FOREIGN)
        self.assertEqual((guard['status'], guard['reasons']), ('defect', ['foreign_antipad']))
        self.assertEqual([(f['kind'], f['net']) for f in guard['foreign']], [('via', '/OTHER')])
        self.assertGreater(guard['uncovered_mm2'], 0.1)
        self.assertEqual(guard['unattributed_mm2'], 0.0)
        self.assertEqual(self.cpw(out)['reference'], ('pass', None))

    def test_the_declared_width_decides_the_corridor(self):
        _, narrow = self.guard(0.3, foreign_via=self.FOREIGN)
        self.assertEqual((narrow['status'], narrow['foreign']), ('pass', []))
        _, wide = self.guard(0.5, foreign_via=self.FOREIGN)
        self.assertEqual(wide['status'], 'defect')

    def test_plane_slot_rf_via_antipads_and_fill_keepout_are_defects(self):
        _, slot = self.guard(reference_slot=True)
        self.assertEqual((slot['status'], slot['reasons']), ('defect', ['void']))
        self.assertGreater(slot['unattributed_mm2'], 1.0)
        _, hop = self.guard(b_cu_hop=True)
        self.assertEqual((hop['status'], hop['reasons'], len(hop['rf_vias'])), ('defect', ['rf_via_antipad'], 2))
        self.assertEqual(len(hop['ungraded_tracks']), 1)
        _, keepout = self.guard(reference_keepout=(5.0, 9.0, 6.0, 9.5))
        self.assertEqual((keepout['status'], keepout['reasons']), ('defect', ['rule_area']))

    def test_own_through_hole_launch_cut_is_excused_as_reference_continuity_excuses_it(self):
        out, guard = self.guard(tht_launch=True)
        self.assertEqual((guard['status'], guard['uncovered_mm2']), ('pass', 0.0))
        # The launch really cuts the plane: the CPW reference strip, which excuses nothing, reports it.
        self.assertEqual(self.cpw(out)['reference'], ('defect', 'reference_void'))
        _, foreign = self.guard(tht_launch=True, foreign_via=self.FOREIGN)
        self.assertEqual(foreign['status'], 'defect')

    def test_missing_width_unfilled_plane_absent_ground_and_custom_rules_are_unknown(self):
        _, null = self.guard(None)
        self.assertEqual((null['status'], null['reason']), ('unknown', 'guard width not declared'))
        _, unfilled = self.guard(fill=False)
        self.assertEqual(unfilled['status'], 'unknown')
        _, ground = self.guard(doc=fx.declaration(ground_net='/NO_GND', guard={'width_mm': 1.0, 'basis': 'x'}))
        self.assertEqual((ground['status'], ground['reason']), ('unknown', 'declared ground net absent from board'))
        out, rules = self.guard(foreign_via=self.FOREIGN,
                                rules='(version 1)\n(rule c (constraint clearance (min 0.3mm)))\n')
        self.assertEqual((rules['status'], rules['reason']), ('unknown', 'the plane clearance is not evaluable'))
        self.assertNotEqual(out['status'], 'pass')
        _, net = self.guard(doc=fx.declaration(net='/NOT_ON_BOARD', guard={'width_mm': 1.0, 'basis': 'x'}))
        self.assertEqual((net['status'], net['reason']), ('unknown', 'declared RF net absent from board'))

    def test_absent_reference_plane_is_a_defect(self):
        _, guard = self.guard(reference=False)
        self.assertEqual((guard['status'], guard['reasons']), ('defect', ['void']))
        # Only the flashed GND stitching via pads remain on In1.Cu.
        self.assertGreater(guard['uncovered_mm2'], 0.8 * guard['corridor_mm2'])

    def test_malformed_guard_facts_refuse(self):
        resolved = parse(fx.declaration(guard={'width_mm': 1.0, 'basis': 'x'}))
        good = self.native(resolved, foreign_via=self.FOREIGN)
        evaluate(resolved, copy.deepcopy(good))
        mutations = {'missing': lambda n: n.pop('guard'),
                     'width': lambda n: n['guard'].update(guard_nm=1_190_000),
                     'tracks': lambda n: n['guard']['corridor_tracks'].pop(),
                     'polygon': lambda n: n['guard']['uncovered_nm'][0]['outline'].append([1.5, 2]),
                     'attribution': lambda n: n['guard']['attributions'][0].update(overlap_nm2=0.0),
                     'problems': lambda n: n['guard'].update(problems=None)}
        for name, mutate in mutations.items():
            native = copy.deepcopy(good)
            mutate(native)
            with self.subTest(name=name), self.assertRaises(ValueError):
                evaluate(resolved, native)


class NativeCheckTests(unittest.TestCase):
    """The registered check: sandboxed probe, candidate binding, sealed evidence."""

    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_board(self, doc=None, **board):
        source = self.root / 'source'
        source.mkdir()
        fx.make_board(source / 'board.kicad_pcb', **board)
        write_json(source / 'rf-calculation.json', fx.CALCULATION)
        write_json(source / 'guard-basis.json', fx.GUARD_RECORD)
        write_json(source / 'rf-geometry.json', doc or fx.sourced_declaration())
        (source / 'requirements.md').write_text('R-rf: owned grounded-CPW geometry control')
        scope = {'schema': 'agent-ee.scope/2', 'inputs': [{'path': str(source), 'at': 'project'}],
                 'board': 'project/board.kicad_pcb',
                 'checks': [{'id': 'rf', 'kind': 'rf_geometry',
                             'params': {'declaration': 'project/rf-geometry.json'}}],
                 'baseline': {'requirements': [{'id': 'R-rf', 'statement': 'Declared RF geometry',
                     'source': {'path': 'project/requirements.md', 'locator': 'R-rf'}, 'checks': ['rf']}],
                     'targets': ['project/requirements.md'],
                     'reference_architectures': ['project/requirements.md']}}
        (self.root / 'scope.json').write_text(json.dumps(scope))
        run = self.root / 'run'
        run.mkdir()
        candidate.capture(self.root / 'scope.json', run)
        [result] = checks.run_checks(run, time.monotonic() + 600)
        return run, result

    def test_compliant_board_passes_bound_to_candidate_and_source(self):
        run, result = self.run_board()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result['summary'])
        report = json.loads((run / 'work/rf/rf-geometry.json').read_text())
        manifest = json.loads((run / 'candidate.json').read_text())
        self.assertEqual(report['board_sha256'], manifest['files']['project/board.kicad_pcb'])
        self.assertEqual(report['candidate_id'], result['candidate_id'])
        self.assertEqual(report['resolved']['source']['sha256'], manifest['files']['project/rf-calculation.json'])
        self.assertEqual((report['resolved']['cpw']['width_mm'], report['resolved']['cpw']['gap_mm']),
                         (0.334416796875, 0.2032))
        self.assertEqual(result['limitations'], [LIMITATIONS])
        self.assertIn('not RF qualification', LIMITATIONS)
        self.assertEqual(result['details']['gate_findings'], [])
        self.assertLessEqual({'work/rf/rf-geometry.json', 'work/rf/rf-native.json', 'work/rf/rf-request.json'},
                             set(result['evidence']))
        freeze_evidence(run, [result])
        verify_evidence(run, [result], required=True)

    def test_violating_board_reports_defects_with_gate_identities(self):
        run, result = self.run_board(gap=0.3, drop_vias=(4, 6, 8, 10),
                                   d3_stub=[(8.0, 8.0), (8.0, 6.5), (6.0, 6.5), (6.0, 5.0), (8.0, 5.0)])
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result['summary'])
        kinds = {(g['entity'], g['kind']) for g in result['details']['gate_findings']}
        self.assertEqual(kinds, {(f'{fx.RF_NET}:cpw', 'rf-geometry/cpw-gap'),
                                 (f'{fx.RF_NET}:trunk', 'rf-geometry/stitching-pitch'),
                                 (f'{fx.RF_NET}:esd', 'rf-geometry/stitching-pitch'),
                                 (f'{fx.RF_NET}:esd', 'rf-geometry/branch-length')})
        freeze_evidence(run, [result])
        verify_evidence(run, [result], required=True)
        validate_witnesses(run, [{'requirement_id': 'R-rf', 'check_id': 'rf'}],
                           [{'path': 'checks/rf.json', 'locator': 'details.gate_findings'}])

    def test_unfilled_board_is_unsupported_unknown_and_cannot_witness(self):
        run, result = self.run_board(fill=False)
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'))
        freeze_evidence(run, [result])
        with self.assertRaisesRegex(ValueError, 'failed criterion'):
            validate_witnesses(run, [{'requirement_id': 'R-rf', 'check_id': 'rf'}],
                               [{'path': 'checks/rf.json', 'locator': 'outcome'}])

    def guarded(self):
        return fx.sourced_declaration(guard={'source': {'path': 'project/guard-basis.json', 'pointer': '/guard_mm'},
                                             'basis': 'owned control record'})

    def test_guard_from_a_captured_basis_passes_bound_to_candidate_and_record(self):
        run, result = self.run_board(self.guarded())
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result['summary'])
        report = json.loads((run / 'work/rf/rf-geometry.json').read_text())
        manifest = json.loads((run / 'candidate.json').read_text())
        self.assertEqual(report['resolved']['guard']['source']['sha256'], manifest['files']['project/guard-basis.json'])
        self.assertEqual(report['sections']['guard']['status'], 'pass')
        self.assertEqual(json.loads((run / 'work/rf/rf-request.json').read_text())['guard_nm'], 1_000_000)
        self.assertEqual(result['limitations'], [LIMITATIONS, GUARD_LIMITATIONS])
        self.assertIn('not RF qualification', GUARD_LIMITATIONS)
        freeze_evidence(run, [result])
        verify_evidence(run, [result], required=True)

    def test_foreign_antipad_in_the_guard_is_a_gate_finding(self):
        run, result = self.run_board(self.guarded(), foreign_via=NativeGuardTests.FOREIGN)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result['summary'])
        self.assertEqual([(g['entity'], g['kind']) for g in result['details']['gate_findings']],
                         [(f'{fx.RF_NET}:guard', 'rf-geometry/return-guard')])
        freeze_evidence(run, [result])
        verify_evidence(run, [result], required=True)

    def test_null_guard_width_is_unsupported_unknown(self):
        doc = fx.sourced_declaration(guard={'width_mm': None, 'basis': 'not yet determined'})
        run, result = self.run_board(doc)
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'))
        self.assertNotIn('guard_nm', json.loads((run / 'work/rf/rf-request.json').read_text()))

    def test_uncaptured_source_refuses_capture(self):
        with self.assertRaisesRegex(ValueError, 'source must be a captured input'):
            self.run_board(fx.sourced_declaration(path='project/missing.json'))


class CapabilityTests(unittest.TestCase):
    def test_declaration_and_source_are_immutable_inputs_with_bounded_predicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp)
            (inputs / 'board.kicad_pcb').write_text('(kicad_pcb)')
            write_json(inputs / 'rf.json', fx.sourced_declaration(path='calc.json'))
            write_json(inputs / 'calc.json', fx.CALCULATION)
            files = {name: digest(inputs / name) for name in ('board.kicad_pcb', 'rf.json', 'calc.json')}
            spec = {'id': 'rf', 'kind': 'rf_geometry', 'params': {'declaration': 'rf.json'}}
            scope = {'board': 'board.kicad_pcb', 'checks': [spec]}
            result = describe_check(spec, check_specs=[spec], files=files, scope=scope, inputs=inputs)
            self.assertEqual(result['status'], 'supported', result)
            self.assertEqual(result['immutable_inputs'], ['calc.json', 'rf.json'])
            self.assertIn('rf-geometry:cpw-gap', result['predicates'])
            write_json(inputs / 'guard.json', fx.GUARD_RECORD)
            files['guard.json'] = digest(inputs / 'guard.json')
            write_json(inputs / 'rf.json', fx.sourced_declaration(
                path='calc.json', guard={'source': {'path': 'guard.json', 'pointer': '/guard_mm'}, 'basis': 'x'}))
            files['rf.json'] = digest(inputs / 'rf.json')
            result = describe_check(spec, check_specs=[spec], files=files, scope=scope, inputs=inputs)
            self.assertEqual(result['immutable_inputs'], ['calc.json', 'guard.json', 'rf.json'])
            self.assertIn('rf-geometry:return-guard', result['predicates'])
            for params in ({'declaration': 'rf.json', 'gap_mm': 0.2}, {'declaration': 'absent.json'}, {}):
                spec['params'] = params
                self.assertEqual(describe_check(spec, check_specs=[spec], files=files, scope=scope,
                                                inputs=inputs)['status'], 'unsupported', params)


if __name__ == '__main__':
    unittest.main()
