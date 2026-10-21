"""AGENTEE-92 placement checks on owned RC variants with actual kicad-cli DRC.

Each variant edits a copy of the captured RC fixture through pcbnew, is
captured as a scope/3 candidate and runs through `checks.run_checks`: a real
native DRC (placement projection and full) followed by placement checks that
consume its raw report and run only the sandboxed saved-board probe.
"""
import copy
import hashlib
import json
from itertools import combinations
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

from toolchain import require_toolchain

from agent_ee import candidate, checks
from agent_ee.baseline import freeze_evidence, validate_witnesses, verify_evidence
from agent_ee.placement_check import gate_findings
from agent_ee.placement_predicates import board_axis, connector_edge, envelope_declarations, native_rotation
from agent_ee.placement_probe import extract
from tests.test_placer import authored_source
from agent_ee.placement_predicates import exact_nm

REPO = Path(__file__).resolve().parents[1]
MM = 1_000_000
STOCK = [{'path': '/usr/share/kicad/symbols', 'at': 'stock/symbols',
          'include': ['Device.kicad_sym', 'power.kicad_sym', 'Connector_Generic.kicad_sym']},
         {'path': '/usr/share/kicad/footprints', 'at': 'stock/footprints',
          'include': ['Resistor_SMD.pretty/*', 'Capacitor_SMD.pretty/*', 'Connector_PinHeader_2.54mm.pretty/*']},
         {'path': '/usr/share/kicad/template', 'at': 'stock/template', 'include': ['sym-lib-table', 'fp-lib-table']}]
BOARD = 'project/rc_filter.kicad_pcb'


def placement(cid, criterion, drc='drc-placement', **params):
    if criterion == 'edge-clearance/1' and 'constraint_provenance' not in params:
        params['constraint_provenance'] = {'min_courtyard_edge_mm': authored_source(
            'tests/test_placement_gates_native.py: authored saved-board edge minimum',
            exact_nm(params['min_courtyard_edge_mm'], 'min_courtyard_edge_mm'))}
    return {'id': cid, 'kind': 'placement', 'params': {'criterion': criterion, 'drc_check': drc, **params}}


EDIT = """import json, sys
import pcbnew as k
path, edit = sys.argv[1], json.loads(sys.argv[2])
MM = 1000000
board = k.LoadBoard(path)
for fp in list(board.GetFootprints()):
    if fp.GetReference() in edit['move']:
        x, y = edit['move'][fp.GetReference()]
        fp.SetPosition(k.VECTOR2I(int(round(x * MM)), int(round(y * MM))))
    if fp.GetReference() in edit['no_courtyard']:
        for item in fp.GraphicalItems():
            if item.GetLayer() in (k.F_CrtYd, k.B_CrtYd):
                item.SetLayer(k.Dwgs_User)
for name, (x, y, w, h), layers, footprints in edit['areas']:
    zone = k.ZONE(board)
    zone.SetIsRuleArea(True)
    zone.SetZoneName(name)
    zone.SetDoNotAllowFootprints(footprints)
    zone.SetDoNotAllowTracks(not footprints)
    zone.SetDoNotAllowVias(False)
    zone.SetDoNotAllowPads(False)
    zone.SetDoNotAllowZoneFills(not footprints)
    layer_set = k.LSET()
    for layer in layers:
        layer_set.AddLayer({'F.Cu': k.F_Cu, 'B.Cu': k.B_Cu}[layer])
    zone.SetLayerSet(layer_set)
    outline = zone.Outline()
    outline.NewOutline()
    for px, py in ((x, y), (x + w, y), (x + w, y + h), (x, y + h)):
        outline.Append(k.VECTOR2I(int(round(px * MM)), int(round(py * MM))))
    board.Add(zone)
k.SaveBoard(path, board)
"""
# Separate interpreter pass: pcbnew 10.0.7 crashes walking a board after Remove().
UNROUTE = """import sys
import pcbnew as k
board = k.LoadBoard(sys.argv[1])
for track in list(board.GetTracks()):
    board.Remove(track)
k.SaveBoard(sys.argv[1], board)
"""
DRC_STAGE = {'id': 'drc-placement', 'kind': 'drc', 'params': {'projection': 'placement-stage/1'}}
DRC_FULL = {'id': 'drc-final', 'kind': 'drc'}


class NativePlacementGates(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-placement-gates-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.count = self.runs = 0

    def source(self, unroute=True, move=None, no_courtyard=(), areas=(), extra=None):
        """Owned RC variant: pcbnew edits of a copied fixture, saved and reloaded."""
        self.count += 1
        source = self.root / f'source-{self.count}'
        (source / 'project').mkdir(parents=True)
        for name in ('rc_filter.kicad_pcb', 'rc_filter.kicad_sch', 'rc_filter.kicad_pro', 'fp-lib-table',
                     'sym-lib-table'):
            shutil.copy(REPO / 'fixtures/rc/control' / name, source / 'project' / name)
        path = source / 'project/rc_filter.kicad_pcb'
        edit = {'move': move or {}, 'no_courtyard': list(no_courtyard), 'areas': list(areas)}
        for script in [EDIT] + ([UNROUTE] if unroute else []):
            process = subprocess.run(['/usr/bin/python3', '-c', script, str(path), json.dumps(edit)],
                                     capture_output=True, text=True, timeout=120)
            self.assertEqual(process.returncode, 0, process.stderr[-2000:])
        (source / 'requirements').mkdir()
        (source / 'requirements/placement.md').write_text('Owned AGENTEE-92 placement requirements.\n')
        for rel, data in (extra or {}).items():
            (source / rel).parent.mkdir(parents=True, exist_ok=True)
            (source / rel).write_bytes(data(path) if callable(data) else data)
        return source

    def run_scope(self, source, specs, schema='agent-ee.scope/3', informational=()):
        requirements = [{'id': f"R-{spec['id']}", 'statement': f"Owned requirement for {spec['id']}",
                         'source': {'path': 'requirements/placement.md', 'locator': spec['id']},
                         'checks': [spec['id']], **({'intent': 'informational' if spec['id'] in informational
                                                     else 'acceptance'} if schema.endswith('/3') else {})}
                        for spec in specs]
        inputs = [{'path': str(source / 'project'), 'at': 'project'},
                  {'path': str(source / 'requirements'), 'at': 'requirements'}, *STOCK]
        if (source / 'routing').is_dir():
            inputs.append({'path': str(source / 'routing'), 'at': 'routing'})
        scope = {'schema': schema, 'inputs': inputs, 'schematic': 'project/rc_filter.kicad_sch', 'board': BOARD,
                 'requirements': ['requirements/placement.md'], 'checks': specs,
                 'baseline': {'requirements': requirements, 'targets': ['requirements/placement.md'],
                              'reference_architectures': ['requirements/placement.md']}}
        self.runs += 1
        path = self.root / f'scope-{self.runs}.json'
        path.write_text(json.dumps(scope))
        run = self.root / f'run-{self.runs}'
        run.mkdir()
        candidate.capture(path, run)
        results = checks.run_checks(run, time.monotonic() + 600)
        return run, {r['id']: r for r in results}

    def assertStatus(self, result, execution, outcome):
        self.assertEqual((result['execution'], result['outcome']), (execution, outcome), result)

    def test_unrouted_rc_advances_placement_but_fails_final_full_drc(self):
        source = self.source()
        specs = [DRC_STAGE, placement('courtyard', 'courtyard-overlap/1'),
                 placement('containment', 'outline-containment/1'),
                 placement('edge', 'edge-clearance/1', min_courtyard_edge_mm=0.25),
                 placement('keepout', 'footprint-keepout/1'), DRC_FULL]
        run, results = self.run_scope(source, specs)
        stage, final = results['drc-placement'], results['drc-final']
        self.assertStatus(stage, 'ok', 'pass')
        self.assertStatus(final, 'ok', 'defect')
        raw = json.loads((run / 'work/drc-placement/drc.json').read_text())
        self.assertGreater(len(raw['unconnected_items']), 0)
        self.assertEqual(stage['details']['ungraded'], {'unconnected_items': len(raw['unconnected_items'])})
        self.assertEqual(final['details']['unconnected_items'], len(raw['unconnected_items']))
        coverage = json.loads((run / 'baseline.json').read_text())['coverage']['checks']
        self.assertEqual(coverage['drc-placement']['predicates'], ['native:drc:placement-stage/1'])
        self.assertEqual(coverage['drc-final']['predicates'], ['native:drc:violations'])
        self.assertEqual(coverage['courtyard']['prerequisites'], [{'check_id': 'drc-placement', 'kind': 'drc'}])
        for cid in ('courtyard', 'containment', 'edge', 'keepout'):
            with self.subTest(check=cid):
                self.assertStatus(results[cid], 'ok', 'pass')
                self.assertIn('work/drc-placement/drc.json', results[cid]['evidence'])
                self.assertIn(f'work/{cid}/placement-probe.json', results[cid]['evidence'])
                self.assertEqual(results[cid]['details']['tool']['argv'][:4],
                                 ['/usr/bin/python3', '-B', '-m', 'agent_ee.placement_probe'])
                self.assertEqual(results[cid]['details']['drc']['check_id'], 'drc-placement')
        self.assertEqual(results['keepout']['details']['rule_areas'], [])
        freeze_evidence(run, list(results.values()))
        verify_evidence(run, list(results.values()), required=True)

    def test_overlapping_rc_courtyards_defect_cites_the_native_item(self):
        run, results = self.run_scope(self.source(move={'C1': (108, 100)}),
                                      [DRC_STAGE, placement('courtyard', 'courtyard-overlap/1'), DRC_FULL])
        self.assertStatus(results['drc-placement'], 'ok', 'defect')
        courtyard = results['courtyard']
        self.assertStatus(courtyard, 'ok', 'defect')
        defect, = courtyard['details']['defects']
        self.assertEqual(sorted(defect['refs']), ['C1', 'R1'])
        self.assertEqual(courtyard['details']['gate_findings'],
                         [{'check_id': 'courtyard', 'entity': 'C1+R1', 'kind': 'courtyards_overlap'}])
        raw = json.loads((run / 'work/drc-placement/drc.json').read_text())
        native, = [v for v in raw['violations'] if v['type'] == 'courtyards_overlap']
        self.assertEqual(defect['native']['items'], [{k: i.get(k) for k in ('uuid', 'description', 'pos')}
                                                    for i in native['items']])
        probe = json.loads((run / 'work/courtyard/placement-probe.json').read_text())
        uuids = {f['ref']: f['uuid'] for f in probe['footprints']}
        self.assertEqual({i['uuid'] for i in native['items']}, {uuids['C1'], uuids['R1']})
        self.assertEqual(courtyard['details']['drc']['drc_sha256'],
                         hashlib.sha256((run / 'work/drc-placement/drc.json').read_bytes()).hexdigest())
        values = list(results.values())
        freeze_evidence(run, values)
        witnesses = [{'requirement_id': 'R-courtyard', 'check_id': 'courtyard'}]
        citations = [{'path': 'checks/courtyard.json', 'locator': 'details.defects'}]
        validate_witnesses(run, witnesses, citations)
        with self.assertRaisesRegex(ValueError, 'witness does not trace'):
            validate_witnesses(run, [{'requirement_id': 'R-drc-final', 'check_id': 'courtyard'}], citations)
        (run / 'work/drc-placement/drc.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'tool evidence changed'):
            validate_witnesses(run, witnesses, citations)

    def test_missing_courtyard_is_unknown_for_geometric_predicates(self):
        run, results = self.run_scope(self.source(no_courtyard=('C1',)), [
            DRC_STAGE, placement('courtyard', 'courtyard-overlap/1'),
            placement('containment', 'outline-containment/1'),
            placement('edge', 'edge-clearance/1', min_courtyard_edge_mm=0.25), DRC_FULL])
        for cid in ('courtyard', 'containment', 'edge'):
            with self.subTest(check=cid):
                self.assertStatus(results[cid], 'unsupported', 'unknown')
                self.assertIn('C1', json.dumps(results[cid]['details']['unknown']))
                self.assertEqual(results[cid]['details']['defects'], [])
        freeze_evidence(run, list(results.values()))
        with self.assertRaisesRegex(ValueError, 'freshly failed'):
            validate_witnesses(run, [{'requirement_id': 'R-courtyard', 'check_id': 'courtyard'}],
                               [{'path': 'checks/courtyard.json', 'locator': 'details.unknown'}])

    def test_native_footprint_keepout_semantics(self):
        # R1 courtyard spans x 103.32..106.68 mm, y 99.05..100.95 mm on F.Cu.
        variants = {'forbid-front': ([('K', (104, 99.5, 1, 1), ['F.Cu'], True)], 'defect'),
                    'forbid-back-only': ([('K', (104, 99.5, 1, 1), ['B.Cu'], True)], 'pass'),
                    'copper-only': ([('K', (104, 99.5, 1, 1), ['F.Cu'], False)], 'pass'),
                    'boundary-touch': ([('K', (106.68, 99.5, 1, 1), ['F.Cu'], True)], 'pass')}
        for name, (areas, outcome) in variants.items():
            with self.subTest(variant=name):
                run, results = self.run_scope(self.source(areas=areas),
                                              [DRC_STAGE, placement('keepout', 'footprint-keepout/1'), DRC_FULL])
                keepout = results['keepout']
                self.assertStatus(keepout, 'ok', outcome)
                inventory, = keepout['details']['rule_areas']
                self.assertEqual(inventory['forbids_footprints'], areas[0][3])
                if outcome == 'defect':
                    defect, = keepout['details']['defects']
                    self.assertEqual((defect['ref'], defect['helper_areas']), ('R1', [inventory['uuid']]))
                    self.assertEqual(defect['native'][0]['type'], 'items_not_allowed')

    def test_footprint_without_courtyard_inside_keepout_is_unknown(self):
        run, results = self.run_scope(self.source(no_courtyard=('R1',),
                                                  areas=[('K', (104, 99.5, 1, 1), ['F.Cu'], True)]),
                                      [DRC_STAGE, placement('keepout', 'footprint-keepout/1'), DRC_FULL])
        raw = json.loads((run / 'work/drc-placement/drc.json').read_text())
        self.assertFalse([v for v in raw['violations'] if v['type'] == 'items_not_allowed'])
        self.assertStatus(results['keepout'], 'unsupported', 'unknown')
        self.assertEqual(results['keepout']['details']['unknown'][0]['ref'], 'R1')

    def test_courtyard_edge_clearance_exact_native_boundary(self):
        # C1 courtyard reaches +1.7 mm in x; the right board edge is x = 116 mm.
        run, results = self.run_scope(self.source(move={'C1': (114.05, 100)}), [
            DRC_STAGE, placement('edge-exact', 'edge-clearance/1', min_courtyard_edge_mm=0.25),
            placement('edge-over', 'edge-clearance/1', min_courtyard_edge_mm='0.250001'), DRC_FULL])
        self.assertStatus(results['edge-exact'], 'ok', 'pass')
        self.assertStatus(results['edge-over'], 'ok', 'defect')
        defect, = results['edge-over']['details']['defects']
        self.assertEqual((defect['ref'], defect['distance_mm'], defect['minimum_mm']), ('C1', 0.25, 0.250001))
        self.assertEqual(results['edge-over']['details']['gate_findings'],
                         [{'check_id': 'edge-over', 'entity': 'C1', 'kind': 'courtyard_edge_clearance'}])
        self.assertNotIn('gate_findings', results['edge-exact']['details'])

    def test_unsourced_saved_board_edge_limit_never_emits_gate_finding(self):
        run, results = self.run_scope(self.source(move={'C1': (114.05, 100)}), [
            DRC_STAGE, placement('edge-advisory', 'edge-clearance/1',
                                 min_courtyard_edge_mm='0.250001', constraint_provenance={}),
            placement('edge-sourced', 'edge-clearance/1', min_courtyard_edge_mm='0.250001'), DRC_FULL])
        self.assertStatus(results['edge-advisory'], 'ok', 'info')
        advisory = results['edge-advisory']['details']
        self.assertEqual((advisory['defects'], advisory['unknown']), ([], []))
        self.assertEqual(advisory['advisories'][0]['kind'], 'unsourced_limit')
        self.assertNotIn('gate_findings', advisory)
        self.assertStatus(results['edge-sourced'], 'ok', 'defect')
        self.assertEqual(results['edge-sourced']['details']['gate_findings'],
                         [{'check_id': 'edge-sourced', 'entity': 'C1', 'kind': 'courtyard_edge_clearance'}])

    def test_ratsnest_byte_identical_oracle_and_declared_maxima(self):
        source = self.source()
        facts = extract(source / 'project/rc_filter.kicad_pcb')
        expected = 0
        for net in sorted({pad['net'] for fp in facts['footprints'] for pad in fp['pads'] if pad['net']}):
            points = [tuple(pad['position_nm']) for fp in facts['footprints'] for pad in fp['pads']
                      if pad['net'] == net]
            expected += oracle(points)
        specs = [DRC_STAGE, placement('ratsnest-info', 'ratsnest-mst/1'),
                 placement('ratsnest-fit', 'ratsnest-mst/1', max_manhattan_mm=expected / MM),
                 placement('ratsnest-tight', 'ratsnest-mst/1', max_manhattan_mm=(expected - 1) / MM), DRC_FULL]
        run, results = self.run_scope(source, specs, informational=('ratsnest-info',))
        self.assertStatus(results['ratsnest-info'], 'ok', 'info')
        self.assertStatus(results['ratsnest-fit'], 'ok', 'pass')
        self.assertStatus(results['ratsnest-tight'], 'ok', 'defect')
        self.assertEqual(results['ratsnest-fit']['details']['totals']['manhattan_nm'], expected)
        self.assertEqual(results['ratsnest-tight']['details']['margins'],
                         {'ratsnest_manhattan_total': {'max': -1 / MM}})
        coverage = json.loads((run / 'baseline.json').read_text())['coverage']
        self.assertFalse(coverage['checks']['ratsnest-info']['can_evaluate_bound'])
        again, _ = self.run_scope(source, specs, informational=('ratsnest-info',))
        for cid in ('ratsnest-info', 'ratsnest-fit'):
            self.assertEqual((run / f'work/{cid}/ratsnest.json').read_bytes(),
                             (again / f'work/{cid}/ratsnest.json').read_bytes())

    def test_routing_feedback_stays_advisory(self):
        def route(reference):
            digests = {'rc_filter.kicad_pcb': hashlib.sha256(reference).hexdigest()}
            fingerprint = hashlib.sha256(''.join(f'{n}\0{d}\n' for n, d in sorted(digests.items())).encode())
            # Owned record in kicad-route 08179cd's result.json shape; not a routing run.
            return json.dumps({'kind': 'route', 'input': {'board': 'rc_filter.kicad_pcb',
                                                          'fingerprint': fingerprint.hexdigest()},
                               'routing': {'status': 'INCOMPLETE', 'blocked_by': {'method': 'owned example', 'top': [
                                   {'id': 'footprint:R1', 'category': 'footprint', 'kind': 'footprint',
                                    'label': 'R1', 'blocks': 1, 'blocks_any_layer': 1}]}}}).encode()
        feedback = {'result': 'routing/result.json',
                    'reference_files': {'rc_filter.kicad_pcb': 'routing/reference/rc_filter.kicad_pcb'}}
        specs = [DRC_STAGE, placement('courtyard', 'courtyard-overlap/1', routing_feedback=feedback), DRC_FULL]
        source = self.source(extra={'routing/result.json': lambda board: route(board.read_bytes()),
                                    'routing/reference/rc_filter.kicad_pcb': lambda board: board.read_bytes()})
        run, results = self.run_scope(source, specs)
        courtyard = results['courtyard']
        self.assertStatus(courtyard, 'ok', 'pass')
        advisory = courtyard['details']['routing_feedback']
        self.assertEqual((advisory['status'], advisory['authority']), ('applicable', 'advisory'))
        self.assertEqual(advisory['blockers'][0]['reference'], 'R1')
        self.assertIn('routing/result.json', json.loads((run / 'baseline.json').read_text())['files'])
        earlier = (REPO / 'fixtures/rc/control/rc_filter.kicad_pcb').read_bytes()
        stale = self.source(extra={'routing/result.json': route(earlier),
                                   'routing/reference/rc_filter.kicad_pcb': earlier})
        _, results = self.run_scope(stale, specs)
        self.assertStatus(results['courtyard'], 'ok', 'pass')
        self.assertEqual(results['courtyard']['details']['routing_feedback']['status'], 'stale')

    def test_prerequisite_drc_binding_and_no_second_native_run(self):
        from agent_ee.placement_check import check_placement
        run, results = self.run_scope(self.source(), [DRC_STAGE, placement('courtyard', 'courtyard-overlap/1'),
                                                      DRC_FULL])
        self.assertStatus(results['courtyard'], 'ok', 'pass')
        manifest = json.loads((run / 'candidate.json').read_text())
        spec = manifest['scope']['checks'][1]
        prior_path, raw_path = run / 'checks/drc-placement.json', run / 'work/drc-placement/drc.json'
        prior, raw = json.loads(prior_path.read_text()), json.loads(raw_path.read_text())

        def attempt(prior_edit=None, raw_edit=None, raw_text=None):
            edited_prior, edited_raw = copy.deepcopy(prior), copy.deepcopy(raw)
            (prior_edit or (lambda r: None))(edited_prior)
            (raw_edit or (lambda r: None))(edited_raw)
            prior_path.write_text(json.dumps(edited_prior))
            raw_path.write_text(raw_text if raw_text is not None else json.dumps(edited_raw))
            ctx = checks.Ctx(run, manifest, spec, time.monotonic() + 120)
            calls, execute = [], ctx.execute

            def spy(argv, timeout, **kwargs):
                calls.append(argv)
                return execute(argv, timeout, **kwargs)
            ctx.execute = spy
            try:
                check_placement(ctx)
                error = None
            except checks.CheckError as exc:
                error = exc.execution
            self.assertFalse([c for c in calls if 'kicad-cli' in c[0]], calls)
            return error, [c[3] for c in calls], ctx.result

        error, calls, result = attempt()
        self.assertEqual((error, calls, result['outcome']), (None, ['agent_ee.placement_probe'], 'pass'))
        cases = {
            'failed DRC': (dict(prior_edit=lambda r: r.update(execution='failed', outcome='unknown')), 'not_run'),
            'wrong candidate': (dict(prior_edit=lambda r: r.update(candidate_id='other')), 'malformed'),
            'wrong board hash': (dict(prior_edit=lambda r: r['details'].update(board_sha256='b' * 64)), 'malformed'),
            'raw not retained': (dict(prior_edit=lambda r: r.update(evidence=[])), 'malformed'),
            'corrupt raw JSON': (dict(raw_text='{"violations": ['), 'malformed'),
            'missing raw list': (dict(raw_edit=lambda r: r.pop('unconnected_items')), 'malformed'),
            'partial severities': (dict(raw_edit=lambda r: r.update(included_severities=['error'])), 'malformed'),
            'other board file': (dict(raw_edit=lambda r: r.update(source='other.kicad_pcb')), 'malformed'),
            'version drift': (dict(raw_edit=lambda r: r.update(kicad_version='9.0.0')), 'malformed')}
        for name, (edit, execution) in cases.items():
            with self.subTest(case=name):
                error, calls, result = attempt(**edit)
                self.assertEqual((error, result['outcome']), (execution, 'unknown'))
                self.assertEqual(calls, [])
        error, calls, _ = attempt(prior_edit=lambda r: r['details'].update(kicad_version='9.0.0'),
                                  raw_edit=lambda r: r.update(kicad_version='9.0.0'))
        self.assertEqual((error, calls), ('malformed', ['agent_ee.placement_probe']))
        error, _, result = attempt(prior_edit=lambda r: r.update(outcome='defect'))
        self.assertEqual((error, result['outcome']), (None, 'pass'))
        prior_path.unlink()
        ctx = checks.Ctx(run, manifest, spec, time.monotonic() + 120)
        with self.assertRaises(checks.CheckError) as caught:
            check_placement(ctx)
        self.assertEqual(caught.exception.execution, 'not_run')

    def test_connector_edge_on_rc_header(self):
        """Moving J1 off its declared left-edge anchor names the edge and the distance."""
        record = rc_validation()
        at_edge = extract(self.source() / 'project/rc_filter.kicad_pcb')
        self.assertEqual(connector_edge(at_edge, envelope_declarations(record))['status'], 'pass')
        moved = extract(self.source(move={'J1': (103, 100)}) / 'project/rc_filter.kicad_pcb')
        result = connector_edge(moved, envelope_declarations(record))
        violation, = result['defects'][0]['violations']
        self.assertEqual((violation['edge'], violation['distance_mm'], violation['inset_mm']), ('left', 3.0, 7.0))
        # The declaration-derived move hint, applied to a fresh capture, clears its finding.
        finding, = gate_findings('connector', result['defects'])
        self.assertEqual((finding['entity'], finding['kind'], finding['suggested_fix']['op']),
                         ('J1', 'connector_off_declared_edge_position', 'move'))
        target = finding['suggested_fix']['params']
        fixed = extract(self.source(move={'J1': (target['x_mm'], target['y_mm'])}) / 'project/rc_filter.kicad_pcb')
        self.assertEqual(connector_edge(fixed, envelope_declarations(record))['status'], 'pass')
        envelope = (REPO / 'tests/fixtures/placement/rc.envelope.json').read_bytes()
        source = self.source(move={'J1': (103, 100)}, extra={'project/rc.envelope.json': envelope})
        spec = placement('connector', 'connector-edge/1', envelope='project/rc.envelope.json')
        try:
            import agent_ee.board_envelope  # noqa: F401  AGENTEE-85 lands separately.
        except ImportError:
            _, results = self.run_scope(source, [DRC_STAGE, spec, DRC_FULL], schema='agent-ee.scope/2')
            self.assertStatus(results['connector'], 'unsupported', 'unknown')
            self.assertIn('AGENTEE-85', results['connector']['summary'])
        else:
            # AGENTEE-85 integrated: a captured envelope must be declared with every obligation
            # bound, so this generic-requirement scope is refused. The declared native connector
            # run is test_board_envelope.EnvelopeNativeBaselineTests.
            with self.assertRaisesRegex(ValueError, 'without a scope envelope declaration'):
                self.run_scope(source, [DRC_STAGE, spec, DRC_FULL])


class NativeTransformQualification(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)

    def test_board_axis_matches_pcbnew_pad_transforms(self):
        import pcbnew as k
        tmp = tempfile.TemporaryDirectory(prefix='agent-ee-transform-')
        self.addCleanup(tmp.cleanup)
        board = k.BOARD()
        for order in ('flip-then-rotate', 'rotate-then-flip'):
            for side in ('F', 'B'):
                for rotation in (0, 90, 180, 270):
                    fp = k.FOOTPRINT(board)
                    fp.SetReference(f'{order}-{side}-{rotation}')
                    board.Add(fp)
                    for number, (x, y) in (('x+', (1, 0)), ('y+', (0, 1))):
                        pad = k.PAD(fp)
                        pad.SetNumber(number)
                        pad.SetAttribute(k.PAD_ATTRIB_SMD)
                        pad.SetSize(k.VECTOR2I(200000, 200000))
                        layers = k.LSET()
                        layers.AddLayer(k.F_Cu)
                        pad.SetLayerSet(layers)
                        fp.Add(pad)
                        pad.SetFPRelativePosition(k.VECTOR2I(x * MM, y * MM))
                    fp.SetPosition(k.VECTOR2I(10 * MM, 10 * MM))
                    if order == 'rotate-then-flip':
                        fp.SetOrientationDegrees(rotation)
                    if side == 'B':
                        fp.Flip(k.VECTOR2I(10 * MM, 10 * MM), False)
                    if order == 'flip-then-rotate':
                        fp.SetOrientationDegrees(rotation)
        path = Path(tmp.name) / 'transform.kicad_pcb'
        k.SaveBoard(str(path), board)
        facts = extract(path)
        self.assertEqual(len(facts['footprints']), 16)
        for fp in facts['footprints']:
            for pad in fp['pads']:
                offset = [round((pad['position_nm'][i] - fp['position_nm'][i]) / MM) for i in (0, 1)]
                observed = {(1, 0): 'x+', (-1, 0): 'x-', (0, 1): 'y+', (0, -1): 'y-'}[tuple(offset)]
                with self.subTest(footprint=fp['ref'], pad=pad['number']):
                    self.assertEqual(board_axis(pad['number'], native_rotation(fp['rotation_deg']), fp['side']),
                                     observed)


def oracle(points):
    """Independent brute-force Manhattan MST total over every spanning edge subset."""
    if len(points) < 2:
        return 0
    best = None
    pairs = list(combinations(range(len(points)), 2))
    for subset in combinations(pairs, len(points) - 1):
        groups = [{i} for i in range(len(points))]
        for a, b in subset:
            ga = next(g for g in groups if a in g)
            gb = next(g for g in groups if b in g)
            if ga is not gb:
                groups.remove(gb)
                ga |= gb
        if len(groups) == 1:
            total = sum(abs(points[a][0] - points[b][0]) + abs(points[a][1] - points[b][1]) for a, b in subset)
            best = total if best is None else min(best, total)
    return best


def rc_validation():
    """AGENTEE-85 e08673c validate() output for tests/fixtures/placement/rc.envelope.json."""
    return {'schema': 'agent-ee.envelope-validation/1', 'envelope_id': 'rc-filter',
            'envelope_sha256': hashlib.sha256((REPO / 'tests/fixtures/placement/rc.envelope.json').read_bytes())
            .hexdigest(),
            'outline': {'edges': [
                {'name': 'top', 'start_mm': ['96', '96'], 'end_mm': ['116', '96'], 'outward': 'y-'},
                {'name': 'right', 'start_mm': ['116', '96'], 'end_mm': ['116', '108'], 'outward': 'x+'},
                {'name': 'bottom', 'start_mm': ['116', '108'], 'end_mm': ['96', '108'], 'outward': 'y+'},
                {'name': 'left', 'start_mm': ['96', '108'], 'end_mm': ['96', '96'], 'outward': 'x-'}]},
            'fixed_footprints': [{'reference': 'J1', 'side': 'front', 'layer': 'F.Cu', 'edge': 'left',
                                  'position_mm': '8', 'inset_mm': '4', 'anchor_mm': ['100', '100'],
                                  'rotation_deg': '0', 'insertion': 'from_top', 'mating_axis': 'z-',
                                  'tolerance_mm': '0'}]}


if __name__ == '__main__':
    unittest.main()
