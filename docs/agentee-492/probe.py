"""Bounded diagnostic of the frozen RF checker; never used for acceptance."""
import copy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, 'tests')
import pcbnew as k
import rf_geometry_fixture as fx
from agent_ee import rf_geometry as rf

root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
out = {'pcbnew_version': k.Version(), 'cases': {}, 'parser': {}}
doc = {key: value for key, value in fx.declaration().items()
       if key not in ('stitching', 'topology')}
doc['cpw'].update(width_mm=.293)
for value in [0, -1, True, float('nan')]:
    changed = copy.deepcopy(doc)
    changed['cpw']['gap_tolerance_mm'] = value
    try:
        rf.parse_declaration(json.dumps(changed).encode())
    except ValueError as exc:
        out['parser'][repr(value)] = str(exc)
    else:
        out['parser'][repr(value)] = 'accepted'
resolved = rf.parse_declaration(json.dumps(doc).encode())[1]
for name, kwargs in {'nominal': {}, 'narrow': {'width': .292}, 'wide': {'width': .294},
                     'gap_below': {'gap': .18}, 'gap_above': {'gap': .23},
                     'missing_flanks': {'ground_signal': False},
                     'absent_reference': {'reference': False}, 'curve': {'arc_bend': True},
                     'disconnected_reference': {}}.items():
    path = root / (name + '.kicad_pcb')
    fx.make_board(path, **{'width': .293, **kwargs})
    if name == 'disconnected_reference':
        board = k.LoadBoard(str(path))
        # Keep the saved filled plane polygons, but remove every through-layer connector.
        # Fixture pads are F.Cu SMD, so In1 now has no physical connection to a terminal.
        for item in list(board.GetTracks()):
            if item.GetClass() == 'PCB_VIA':
                board.Remove(item)
        k.SaveBoard(str(path), board)
    native = rf.extract(path, rf.probe_request(resolved))
    (root / (name + '.native.json')).write_text(json.dumps(native, indent=2))
    positive = rf.evaluate(resolved, native)
    zero = copy.deepcopy(resolved)
    zero['cpw'].update(width_tolerance_mm=0, gap_tolerance_mm=0)
    # Deliberately bypass the refusing parser ONLY to expose evaluator behavior.
    result = rf.evaluate(zero, native)
    samples = {}
    ground = rf.EdgeSet([native['copper']['ground_signal']])
    for track in native['tracks']:
        points, _ = rf.track_polyline(track)
        kind = 'arc' if track['kind'] == 'arc' else ('axis' if
            track['start_nm'][0] == track['end_nm'][0] or track['start_nm'][1] == track['end_nm'][1]
            else 'diagonal')
        mid = list(rf._samples(points, 50000))[len(list(rf._samples(points, 50000))) // 2]
        _, p, normal = mid
        vals = samples.setdefault(kind, [])
        for sign in (-1, 1):
            edge = tuple(p[i] + sign * normal[i] * track['width_nm'] / 2 for i in (0, 1))
            distance = ground.distance(edge, 1000000)
            vals.append(None if distance is None else distance - 203200)
    out['cases'][name] = {'board_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'max_error_nm': native['max_error_nm'], 'sample_gap_delta_from_nominal_nm': samples,
        'positive_tolerance': positive, 'zero_parser_bypass': result,
        'via_count': len(native['vias']), 'reference_polygon_count': len(native['copper']['ground_reference'] or [])}
wrong = copy.deepcopy(resolved)
wrong['net'] = '/WRONG'
try:
    rf.evaluate(wrong, native)
except ValueError as exc:
    out['wrong_net_evaluate'] = str(exc)
else:
    out['wrong_net_evaluate'] = 'not refused: native schema has no net-name binding'
(root / 'result.json').write_text(json.dumps(out, indent=2))
print(json.dumps({'parser': out['parser'], 'wrong_net_evaluate': out['wrong_net_evaluate'], 'cases': {
    n: {'max_error_nm': c['max_error_nm'], 'samples': c['sample_gap_delta_from_nominal_nm'],
        'zero': {key: value['status'] for key, value in c['zero_parser_bypass']['sections']['cpw']['items'].items()},
        'positive_reference': c['positive_tolerance']['sections']['cpw']['items']['reference']['status'],
        'vias': c['via_count'], 'reference_polygons': c['reference_polygon_count']}
    for n, c in out['cases'].items()}}, indent=2))
