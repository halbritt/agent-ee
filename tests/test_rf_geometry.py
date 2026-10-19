"""RF geometry on the routed board: grounded CPW, stitching pitch and branch lengths, fail-closed."""
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
    LIMITATIONS, evaluate, margin_nm, parse_declaration, predicates,
)
from agent_ee.storage import digest, write_json

import rf_geometry_fixture as fx

TRUNK_MM = 10 + 2 * math.sqrt(2) + 12
POD = Path(__file__).resolve().parents[1] / 'fixtures/telemetry-pod'
SOLVER = POD / 'rf-w2-calc-4-result.json'
SOLVER_SHA256 = 'd05e15b93f02b3cc12b9b967c093503c60a3c72338a2f35ddccd96d4998b89af'
ERR_EFF_POINTER = '/impedance_calc_result/dErEff'


def bound_declaration(path='project/rf-w2-calc-4-result.json', sha256=SOLVER_SHA256, pointer=ERR_EFF_POINTER,
                      **overrides):
    """Owned declaration whose effective permittivity is read from the captured W2 solver record."""
    doc = fx.declaration(**overrides)
    doc['electrical_length'] = {'effective_permittivity_source': {'path': path, 'sha256': sha256,
                                                                  'pointer': pointer},
                                'frequency_hz': 1575420000, 'basis': 'captured W2 solver ErEff, reported only'}
    return doc


def parse_bound(doc, document=None):
    """Parse against the committed solver record (or an altered document), hashed as captured."""
    def load(rel):
        if rel != 'project/rf-w2-calc-4-result.json':
            raise ValueError(f'source must be a captured input: {rel!r}')
        if document is None:
            return {'document': json.loads(SOLVER.read_bytes()), 'sha256': digest(SOLVER)}
        return {'document': document, 'sha256': SOLVER_SHA256}
    return parse_declaration(json.dumps(doc).encode(), load)[1]


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

    def test_permittivity_binds_the_captured_solver_record_by_sha256_and_pointer(self):
        electrical = parse_bound(bound_declaration())['electrical_length']
        self.assertEqual(electrical['effective_permittivity'], 2.94025199985)
        self.assertEqual(electrical['source'], {'path': 'project/rf-w2-calc-4-result.json',
                                                'sha256': SOLVER_SHA256, 'pointer': ERR_EFF_POINTER})
        self.assertNotIn('status', electrical)
        declared = fx.declaration(electrical_length={'effective_permittivity': 3.1, 'frequency_hz': 1575420000,
                                                     'basis': 'declared ErEff'})
        self.assertEqual(parse(declared)['electrical_length'],
                         {'frequency_hz': 1575420000, 'basis': 'declared ErEff', 'effective_permittivity': 3.1})

    def test_permittivity_hash_mismatch_and_malformed_bindings_refuse(self):
        with self.assertRaisesRegex(ValueError, 'not the declared ' + '0' * 64):
            parse_bound(bound_declaration(sha256='0' * 64))
        both = bound_declaration()
        both['electrical_length']['effective_permittivity'] = 2.94
        neither = bound_declaration()
        del neither['electrical_length']['effective_permittivity_source']
        cases = {'both': both, 'neither': neither,
                 'short hash': bound_declaration(sha256=SOLVER_SHA256[:63]),
                 'upper hash': bound_declaration(sha256=SOLVER_SHA256.upper()),
                 'pointer syntax': bound_declaration(pointer='impedance_calc_result/dErEff'),
                 'nonnumeric': bound_declaration(pointer='/impedance_calc_mark'),
                 'nonpositive': bound_declaration(pointer='/impedance_calc_status'),
                 'uncaptured': bound_declaration(path='project/elsewhere.json')}
        for name, doc in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                parse_bound(doc)
        with self.assertRaisesRegex(ValueError, 'requires captured inputs'):
            parse_declaration(json.dumps(bound_declaration()).encode())

    def test_permittivity_absent_at_the_pointer_is_unknown(self):
        recorded = json.loads(SOLVER.read_bytes())
        nulled = copy.deepcopy(recorded)
        nulled['impedance_calc_result']['dErEff'] = None
        for name, (doc, document) in {'missing key': (bound_declaration(pointer='/impedance_calc_result/dEr'), None),
                                      'missing parent': (bound_declaration(pointer='/result/dErEff'), None),
                                      'null value': (bound_declaration(), nulled)}.items():
            with self.subTest(name=name):
                electrical = parse_bound(doc, document)['electrical_length']
                self.assertEqual((electrical['effective_permittivity'], electrical['status']), (None, 'unknown'))
                self.assertIn('absent', electrical['reason'])

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


class NativeEvaluationTests(unittest.TestCase):
    """In-process pcbnew facts from owned boards, evaluated by the coordinator code."""
    cache = {}

    def setUp(self):
        require_toolchain(native=True)

    def evaluate(self, doc=None, **board):
        resolved = parse(doc or fx.declaration())
        return evaluate(resolved, self.native(resolved, **board))

    def native(self, resolved, **board):
        from agent_ee.rf_geometry import extract
        request = {k: resolved[k] for k in ('net', 'ground_net', 'signal_layer', 'reference_layer')}
        request['margin_nm'] = margin_nm(resolved)
        key = json.dumps([board, request], sort_keys=True)
        if key not in self.cache:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'board.kicad_pcb'
                fx.make_board(path, **board)
                self.cache[key] = json.dumps(extract(path, request))
        return json.loads(self.cache[key])

    @staticmethod
    def cpw(out):
        return {k: (v['status'], v.get('reason')) for k, v in out['sections']['cpw']['items'].items()}

    @staticmethod
    def branches(out):
        return {b['name']: (b['status'], b.get('length_mm')) for b in out['sections']['topology']['branches']}

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

    def test_bound_permittivity_reports_electrical_length_without_changing_verdicts(self):
        plain = self.evaluate()
        bound = evaluate(parse_bound(bound_declaration()), self.native(parse(fx.declaration())))
        self.assertEqual(bound['status'], plain['status'])
        self.assertEqual(self.branches(bound), self.branches(plain))
        for branch in bound['sections']['topology']['branches']:
            expected = 360 * branch['length_mm'] / 1000 * 1575420000 * math.sqrt(2.94025199985) / 299_792_458
            self.assertAlmostEqual(branch['electrical_length_deg'], expected, places=3)
        recorded = json.loads(SOLVER.read_bytes())
        del recorded['impedance_calc_result']['dErEff']
        absent = evaluate(parse_bound(bound_declaration(), recorded), self.native(parse(fx.declaration())))
        self.assertEqual(absent['status'], plain['status'])
        self.assertEqual(self.branches(absent), self.branches(plain))
        self.assertEqual({b['electrical_length_deg'] for b in absent['sections']['topology']['branches']}, {None})

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


class NativeCheckTests(unittest.TestCase):
    """The registered check: sandboxed probe, candidate binding, sealed evidence."""

    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_board(self, doc=None, solver=None, **board):
        source = self.root / 'source'
        source.mkdir()
        fx.make_board(source / 'board.kicad_pcb', **board)
        write_json(source / 'rf-calculation.json', fx.CALCULATION)
        if solver is not None:
            (source / 'rf-w2-calc-4-result.json').write_bytes(solver)
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

    def test_bound_permittivity_is_bound_to_the_captured_solver_record(self):
        doc = bound_declaration(**{k: v for k, v in fx.sourced_declaration().items() if k in ('source', 'cpw')})
        run, result = self.run_board(doc, solver=SOLVER.read_bytes())
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result['summary'])
        report = json.loads((run / 'work/rf/rf-geometry.json').read_text())
        manifest = json.loads((run / 'candidate.json').read_text())
        electrical = report['resolved']['electrical_length']
        self.assertEqual(electrical['source']['sha256'], manifest['files']['project/rf-w2-calc-4-result.json'])
        self.assertEqual(electrical['source']['sha256'], SOLVER_SHA256)
        self.assertEqual(electrical['effective_permittivity'], 2.94025199985)
        self.assertTrue(all(b['electrical_length_deg'] > 0 for b in report['sections']['topology']['branches']))

    def test_altered_solver_record_refuses_capture(self):
        altered = SOLVER.read_bytes().replace(b'2.94025199985', b'3.94025199985')
        with self.assertRaisesRegex(ValueError, 'effective_permittivity_source .* has sha256'):
            self.run_board(bound_declaration(), solver=altered)

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
            for params in ({'declaration': 'rf.json', 'gap_mm': 0.2}, {'declaration': 'absent.json'}, {}):
                spec['params'] = params
                self.assertEqual(describe_check(spec, check_specs=[spec], files=files, scope=scope,
                                                inputs=inputs)['status'], 'unsupported', params)

    def test_permittivity_source_is_an_immutable_input_and_mismatch_is_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp)
            (inputs / 'board.kicad_pcb').write_text('(kicad_pcb)')
            write_json(inputs / 'rf.json', bound_declaration(path='solver.json'))
            (inputs / 'solver.json').write_bytes(SOLVER.read_bytes())
            files = {name: digest(inputs / name) for name in ('board.kicad_pcb', 'rf.json', 'solver.json')}
            spec = {'id': 'rf', 'kind': 'rf_geometry', 'params': {'declaration': 'rf.json'}}
            scope = {'board': 'board.kicad_pcb', 'checks': [spec]}
            result = describe_check(spec, check_specs=[spec], files=files, scope=scope, inputs=inputs)
            self.assertEqual(result['status'], 'supported', result)
            self.assertEqual(result['immutable_inputs'], ['rf.json', 'solver.json'])
            write_json(inputs / 'rf.json', bound_declaration(path='solver.json', sha256='0' * 64))
            files['rf.json'] = digest(inputs / 'rf.json')
            result = describe_check(spec, check_specs=[spec], files=files, scope=scope, inputs=inputs)
            self.assertEqual(result['status'], 'unsupported', result)


class PodSolverCaptureTests(unittest.TestCase):
    def test_committed_solver_record_is_the_recorded_w2_capture(self):
        self.assertEqual(digest(SOLVER), SOLVER_SHA256)
        w2 = json.loads((POD / 'rf-source-capture.json').read_text())['w2_continuation']
        raw, = [c for c in w2['raw_captures'] if c['file'] == 'calc-4-result.json']
        committed, = w2['committed_decoded_results']
        self.assertEqual((raw['sha256'], raw['bytes']), (SOLVER_SHA256, len(SOLVER.read_bytes())))
        self.assertEqual((committed['path'], committed['raw_capture_file'], committed['sha256'], committed['bytes']),
                         (SOLVER.name, raw['file'], raw['sha256'], raw['bytes']))
        recorded = json.loads(SOLVER.read_bytes())
        self.assertEqual((recorded['impedance_calc_status'], recorded['impedance_calc_result']['dResultValid']),
                         (0, 1))
        self.assertEqual(recorded['uuid'], w2['calculation']['uuid'])
        self.assertEqual(recorded['impedance_calc_result']['jBackCalc'], w2['calculation']['geometry_mil'])


if __name__ == '__main__':
    unittest.main()
