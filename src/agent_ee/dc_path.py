"""Qualified ideal one-dimensional DC conduction on endpoint-only planar copper."""

from .check_registry import register as _register_check
import math
from fractions import Fraction
from pathlib import Path

from .measurements import finite, evaluate_limits
from .storage import digest, identity, read_json, write_json

POLICY = 'endpoint-planar-dc/2'
RESIDUAL = 1e-9
TOOLS = {'/usr/bin/python3': 'e50d468e8b0adfb05733f5b87b3cff34829c4a8c1aea50c865aa8bdfe4bb150f',
         '/usr/lib/python3/dist-packages/pcbnew.py': '50740b1aeed153349ffe000c7e2c39d9fd75d1cdf49118897f5df5bf957b8458',
         '/usr/lib/python3/dist-packages/_pcbnew.so': 'ba821c4719fbb6e51c378ae98f7db4254a214eb9e39e4822bb88833c986513f8'}


def qualification(c=None):
    if c is not None and c.get('schema') == 'agent-ee.dc-path/2':
        from .dc_rail import qualification as rail_qualification
        return rail_qualification()
    return {'schema': 'agent-ee.dc-qualification/1', 'policy': POLICY,
            'pcbnew_version': '10.0.7', 'tools': TOOLS,
            'model_sha256': digest(__file__),
            'probe_sha256': digest(Path(__file__).with_name('dc_geometry_probe.py')),
            'max_edges': 128, 'max_nodes': 32, 'max_loads': 8,
            'solver': 'exact-rational-elimination/1', 'residual_relative': RESIDUAL, 'resistance_ratio_max': 1e6,
            'min_length_width_ratio': 10, 'junction_min_angle_degrees': 90,
            'physics': 'ideal rho*L/A; endpoint contacts; external temperature; fixed topology'}


def positive(value, label):
    if finite(value, label) <= 0:
        raise ValueError(label + ' must be positive')
    return value


def exact_fields(record, fields, label):
    if not isinstance(record, dict) or set(record) != set(fields.split()):
        raise ValueError('missing/unsupported ' + label + ' fields')


def validate_contract(c):
    if isinstance(c, dict) and c.get('schema') == 'agent-ee.dc-path/2':
        from .dc_rail import validate_contract as validate_rail
        return validate_rail(c)
    exact_fields(c, 'schema net layer stackup material temperature_c dimensions terminals loads', 'contract')
    if c['schema'] != 'agent-ee.dc-path/1' or c['layer'] != 'F.Cu' or not isinstance(c['net'], str) or not c['net']:
        raise ValueError('unsupported DC schema/net/layer')
    exact_fields(c['stackup'], 'layer thickness_m', 'stackup')
    if c['stackup']['layer'] != c['layer']:
        raise ValueError('stackup layer differs')
    positive(c['stackup']['thickness_m'], 'copper thickness')
    m = c['material']
    exact_fields(m, 'model rho_ref_ohm_m reference_c alpha_per_c valid_c', 'material')
    if m['model'] != 'linear-external-temperature/1':
        raise ValueError('unsupported temperature model')
    positive(m['rho_ref_ohm_m'], 'resistivity')
    finite(m['reference_c'], 'reference temperature')
    finite(m['alpha_per_c'], 'temperature coefficient')
    interval = m['valid_c']
    if not isinstance(interval, list) or len(interval) != 2:
        raise ValueError('explicit temperature range required')
    low, high = [finite(v, 'temperature bound') for v in interval]
    temperature = finite(c['temperature_c'], 'operating temperature')
    if not 20 <= low <= temperature <= high <= 80 or not low <= m['reference_c'] <= high:
        raise ValueError('outside qualified temperature domain 20..80 C')
    for t in (low, high):
        positive(m['rho_ref_ohm_m'] * (1 + m['alpha_per_c'] * (t - m['reference_c'])), 'corrected resistivity')
    exact_fields(c['dimensions'], 'width_factors thickness_factors length_factors', 'dimension')
    for key, interval in c['dimensions'].items():
        if not isinstance(interval, list) or len(interval) != 2:
            raise ValueError('explicit dimensional intervals required')
        lo, hi = [positive(v, key) for v in interval]
        if not .5 <= lo <= 1 <= hi <= 1.5:
            raise ValueError('outside qualified dimensional domain')
    if c['dimensions']['length_factors'] != [1, 1]:
        raise ValueError('length uncertainty changing endpoint topology is unqualified')
    terminals = c['terminals']
    if not isinstance(terminals, list) or len(terminals) != 2:
        raise ValueError('exactly two terminals required')
    for terminal in terminals:
        exact_fields(terminal, 'ref pad contact', 'terminal')
        if any(not isinstance(terminal[k], str) or not terminal[k] for k in ('ref', 'pad')):
            raise ValueError('explicit terminal reference/pad required')
        if terminal['contact'] != 'ideal-centered-round-smd/1':
            raise ValueError('unqualified terminal contact model')
    if terminals[0] == terminals[1]:
        raise ValueError('distinct terminal identities required')
    loads = c['loads']
    if not isinstance(loads, list) or not 1 <= len(loads) <= 8:
        raise ValueError('one to eight explicit loads required')
    names = set()
    for load in loads:
        exact_fields(load, 'id current_a max_drop_v max_loss_w', 'load')
        name = load['id']
        if not isinstance(name, str) or not name.isidentifier() or name in names:
            raise ValueError('unique load identifiers required')
        names.add(name)
        for key in ('current_a', 'max_drop_v', 'max_loss_w'):
            positive(load[key], key)
    return c


def condition(c, load):
    return f'{POLICY};contract={identity(c)};load={load["id"]};I={load["current_a"]}A;T={c["temperature_c"]}C'


def limits(c):
    if c.get('schema') == 'agent-ee.dc-path/2':
        from .dc_rail import limits as rail_limits
        return rail_limits(c)
    return {f'{load["id"]}_{metric}': {'max': load[bound], 'unit': unit,
            'condition': condition(c, load)} for load in c['loads']
            for metric, bound, unit in (('drop', 'max_drop_v', 'V'), ('loss', 'max_loss_w', 'W'))}


def load_inputs(params, files, inputs):
    from .candidate import safe_relative
    exact_fields(params, 'contract qualification', 'check parameter')
    if inputs is None:
        raise ValueError('DC capability requires captured inputs')
    paths = [str(safe_relative(params[k])) for k in ('contract', 'qualification')]
    try:
        if any(p not in files or digest(Path(inputs) / p) != files[p] for p in paths):
            raise ValueError('DC contract/qualification must be unchanged captured inputs')
        c = validate_contract(read_json(Path(inputs) / paths[0]))
        if read_json(Path(inputs) / paths[1]) != qualification(c):
            raise ValueError('DC model/probe/tool identity requires explicit requalification')
    except OSError as exc:
        raise ValueError('DC captured dependency unavailable: ' + str(exc)) from exc
    return c, paths


def series_parallel(edges, terminals):
    """Reduce ALL branches; reject dangling islands and non-series-parallel graphs."""
    terminals = [tuple(t) for t in terminals]
    pairs = [tuple(sorted((tuple(e['start']), tuple(e['end'])))) for e in edges]
    while True:
        pairs = list(set(pairs))
        if len(pairs) == 1 and set(pairs[0]) == set(terminals):
            return
        nodes = {n for pair in pairs for n in pair}
        reduced = False
        for node in sorted(nodes - set(terminals)):
            adjacent = [p for p in pairs if node in p]
            if len(adjacent) == 2:
                ends = [p[0] if p[1] == node else p[1] for p in adjacent]
                if ends[0] == ends[1]:
                    raise ValueError('unmodeled loop')
                pairs = [p for p in pairs if p not in adjacent] + [tuple(sorted(ends))]
                reduced = True
                break
        if not reduced:
            raise ValueError('incomplete/disconnected or non-series-parallel conductor network')


def solve(edges, terminals, current):
    positive(current, 'current')
    if not 1 <= len(edges) <= 128:
        raise ValueError('outside conductor budget')
    terminals = [tuple(t) for t in terminals]
    series_parallel(edges, terminals)
    nodes = sorted({tuple(e[k]) for e in edges for k in ('start', 'end')} - {terminals[1]})
    if not 1 <= len(nodes) <= 31 or terminals[0] not in nodes:
        raise ValueError('outside node budget or unresolved terminals')
    resistances = [positive(e['resistance_ohm'], 'edge resistance') for e in edges]
    if max(resistances) / min(resistances) > 1e6:
        raise ValueError('unqualified resistance conditioning')
    index = {n: i for i, n in enumerate(nodes)}
    matrix = [[Fraction(0)] * len(nodes) for _ in nodes]
    rhs = [Fraction(n == terminals[0]) for n in nodes]
    for edge in edges:
        a, b = (tuple(edge[k]) for k in ('start', 'end'))
        if a == b:
            raise ValueError('zero-length network edge')
        positive(1 / edge['resistance_ohm'], 'edge conductance')
        g = 1 / Fraction(edge['resistance_ohm'])
        for u, v in ((a, b), (b, a)):
            if u in index:
                matrix[index[u]][index[u]] += g
                if v in index:
                    matrix[index[u]][index[v]] -= g
    original = [[float(v) for v in row] for row in matrix]
    scale = max(abs(v) for row in matrix for v in row)
    for col in range(len(nodes)):
        pivot = max(range(col, len(nodes)), key=lambda r: abs(matrix[r][col]))
        if abs(matrix[pivot][col]) <= scale * Fraction('1e-12'):
            raise ValueError('singular/ill-conditioned conductance system')
        matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
        rhs[col], rhs[pivot] = rhs[pivot], rhs[col]
        for row in range(col + 1, len(nodes)):
            factor = matrix[row][col] / matrix[col][col]
            for j in range(col, len(nodes)):
                matrix[row][j] -= factor * matrix[col][j]
            rhs[row] -= factor * rhs[col]
    voltages = [Fraction(0)] * len(nodes)
    for row in reversed(range(len(nodes))):
        voltages[row] = (rhs[row] - sum(matrix[row][j] * voltages[j]
                         for j in range(row + 1, len(nodes)))) / matrix[row][row]
    potential = {n: float(voltages[i]) * current for n, i in index.items()}
    potential[terminals[1]] = 0.
    drop = potential[terminals[0]]
    flows = [(potential[tuple(e['start'])] - potential[tuple(e['end'])]) / e['resistance_ohm'] for e in edges]
    loss = sum(i * i * e['resistance_ohm'] for i, e in zip(flows, edges))
    kcl = max(abs(sum(row[j] * float(voltages[j]) for j in range(len(nodes))) - float(n == terminals[0]))
              for n, row in zip(nodes, original)) * current
    energy = abs(loss - current * drop)
    solution = {'resistance_ohm': drop / current, 'drop_v': drop, 'loss_w': loss,
                'kcl_residual_a': kcl, 'energy_residual_w': energy,
                'node_voltages_v': [{'position_nm': list(n), 'voltage_v': v} for n, v in potential.items()],
                'edge_currents_a': flows, 'conductance_matrix_s': original}
    validate_solution(solution, current)
    return solution


def validate_solution(solution, current):
    drop, loss = solution['drop_v'], solution['loss_w']
    kcl, energy = solution['kcl_residual_a'], solution['energy_residual_w']
    numbers = [drop, loss, kcl, energy, solution['resistance_ohm'], *solution['edge_currents_a']]
    if (not all(math.isfinite(v) for v in numbers) or min(drop, loss) <= 0 or min(kcl, energy) < 0 or
            kcl > current * RESIDUAL or energy > current * drop * RESIDUAL or
            abs(loss - current * drop) > current * drop * RESIDUAL):
        raise ValueError('DC solver residual/nonfinite qualification failed')


def point_segment_distance(p, a, b):
    direction = [b[i] - a[i] for i in (0, 1)]
    fraction = max(0., min(1., sum((p[i] - a[i]) * direction[i] for i in (0, 1)) /
                           sum(v * v for v in direction)))
    return math.hypot(*(p[i] - a[i] - fraction * direction[i] for i in (0, 1)))


def cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segment_distance(a, b):
    p, q, r, s = a['start'], a['end'], b['start'], b['end']
    if cross(p, q, r) * cross(p, q, s) < 0 and cross(r, s, p) * cross(r, s, q) < 0:
        return 0.
    return min(point_segment_distance(p, r, s), point_segment_distance(q, r, s),
               point_segment_distance(r, p, q), point_segment_distance(s, p, q))


def separated(a, b, max_width_factor):
    common = {tuple(a[k]) for k in ('start', 'end')} & {tuple(b[k]) for k in ('start', 'end')}
    if common:
        if len(common) != 1 or a['native_class'] != 'PCB_TRACK' or b['native_class'] != 'PCB_TRACK':
            raise ValueError('overlap or arc junction is outside endpoint qualification')
        junction = next(iter(common))
        directions = [[v[i] - junction[i] for i in (0, 1)]
                      for e in (a, b) for v in (e['start'], e['end']) if tuple(v) != junction]
        if sum(directions[0][i] * directions[1][i] for i in (0, 1)) > 0:
            raise ValueError('overlap/acute endpoint junction outside qualification')
        return
    clearance = (a['width_nm'] + b['width_nm']) * max_width_factor / 2 + 2
    if a['native_class'] == b['native_class'] == 'PCB_TRACK':
        if segment_distance(a, b) <= clearance:
            raise ValueError('interior contact/crossing/width overlap outside qualification')
    else:
        # Bounding boxes are conservative for circular arcs. Never infer noncontact
        # from sampled curves; overlapping boxes are refused, even for disjoint arcs.
        boxes = [e['bbox_nm'] for e in (a, b)]
        expansion = clearance
        if not any(boxes[0][i + 2] + expansion < boxes[1][i] or
                   boxes[1][i + 2] + expansion < boxes[0][i] for i in (0, 1)):
            raise ValueError('unqualified possible arc contact')


def qualify_substrate(geo, c):
    """Qualify only four-segment rectangles; boxes conservatively enclose copper."""
    boundaries = geo['substrate']['boundaries']
    if not 4 <= len(boundaries) <= 128:
        raise ValueError('missing substrate outline or boundary budget exceeded')
    adjacency = {}
    segments = set()
    for edge in boundaries:
        if edge['native_class'] != 'PCB_SHAPE' or edge.get('shape') != 0:
            raise ValueError('unsupported substrate boundary class/curve')
        a, b = tuple(edge['start_nm']), tuple(edge['end_nm'])
        if a == b or (a[0] != b[0] and a[1] != b[1]):
            raise ValueError('substrate boundary must be axis aligned')
        segment = tuple(sorted((a, b)))
        if segment in segments:
            raise ValueError('duplicate substrate boundary')
        segments.add(segment)
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)
    if any(len(neighbors) != 2 for neighbors in adjacency.values()):
        raise ValueError('open or ambiguous substrate boundary')
    remaining = set(adjacency)
    rectangles = []
    while remaining:
        component, pending = set(), [next(iter(remaining))]
        while pending:
            node = pending.pop()
            if node in component:
                continue
            component.add(node)
            pending.extend(adjacency[node])
        remaining -= component
        xs, ys = sorted({p[0] for p in component}), sorted({p[1] for p in component})
        if len(component) != 4 or len(xs) != 2 or len(ys) != 2:
            raise ValueError('substrate boundary is not a four-segment rectangle')
        rectangles.append([xs[0], ys[0], xs[1], ys[1]])

    def contains(outer, inner):
        return all(outer[i] < inner[i] and inner[i + 2] < outer[i + 2] for i in (0, 1))

    def disjoint(a, b):
        return any(a[i + 2] < b[i] or b[i + 2] < a[i] for i in (0, 1))

    outers = [r for r in rectangles if all(r == other or contains(r, other) for other in rectangles)]
    if len(outers) != 1:
        raise ValueError('ambiguous outer substrate rectangle')
    outer = outers[0]
    cutouts = [r for r in rectangles if r != outer]
    for i, hole in enumerate(cutouts):
        if any(not disjoint(hole, other) for other in cutouts[i + 1:]):
            raise ValueError('overlapping or nested substrate cutouts')
    envelopes = []
    for track in geo['tracks']:
        if track['native_class'] not in ('PCB_TRACK', 'PCB_ARC'):
            raise ValueError('unmodeled conductor class')
        # Native bbox already includes nominal width; extra maximum radius is
        # conservative for arcs and all dimensional cases, including round caps.
        radius = track['width_nm'] * c['dimensions']['width_factors'][1] / 2 + 2
        box = track['bbox_nm']
        envelopes.append([box[0] - radius, box[1] - radius, box[2] + radius, box[3] + radius])
    for pad in geo['pads']:
        x, y = pad['position_nm']
        rx, ry = (v / 2 + 2 for v in pad['size_nm'])
        envelopes.append([x - rx, y - ry, x + rx, y + ry])
    for box in envelopes:
        if not contains(outer, box) or any(not disjoint(box, hole) for hole in cutouts):
            raise ValueError('copper envelope intersects substrate boundary/cutout or lies outside board')
    return {'outer_nm': outer, 'cutouts_nm': cutouts, 'copper_envelopes_nm': envelopes,
            'native_boundaries': len(boundaries), 'policy': 'rectangular-substrate/1'}


def extract_network(geo, c):
    validate_contract(c)
    if geo['schema'] != 'agent-ee.dc-geometry/2' or geo['pcbnew_version'] != '10.0.7':
        raise ValueError('unqualified native geometry version')
    if geo['zones'] or geo['copper_graphics']:
        raise ValueError('unmodeled zones or copper graphics')
    if len(set(geo['footprint_refs'])) != len(geo['footprint_refs']):
        raise ValueError('ambiguous native footprint references')
    substrate = qualify_substrate(geo, c)
    tracks = geo['tracks']
    if not 1 <= len(tracks) <= 128:
        raise ValueError('incomplete conductor inventory/budget')
    ids = [t['uuid'] for t in tracks] + [p['uuid'] for p in geo['pads']]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate native identity')
    edges = []
    for track in tracks:
        if track['net'] != c['net'] or track['layer'] != c['layer']:
            raise ValueError('other net/layer conductors are not qualified for isolation')
        if track['native_class'] not in ('PCB_TRACK', 'PCB_ARC'):
            raise ValueError('unmodeled via or native conductor class')
        a, b = tuple(track['start_nm']), tuple(track['end_nm'])
        if a == b:
            raise ValueError('degenerate/incomplete conductor')
        width = positive(track['width_nm'], 'native width')
        if track['native_class'] == 'PCB_TRACK':
            length = math.dist(a, b)
        else:
            radius = positive(track['radius_nm'], 'arc radius')
            sweep = abs(finite(track['sweep_rad'], 'arc sweep'))
            if not 0 < sweep <= math.pi or radius < 10 * width * c['dimensions']['width_factors'][1]:
                raise ValueError('outside qualified circular arc domain')
            length = radius * sweep
        if abs(length - finite(track['native_length_nm'], 'native length')) > 2:
            raise ValueError('native length inconsistent with exact geometry')
        if length < 10 * width * c['dimensions']['width_factors'][1]:
            raise ValueError('outside qualified slender-conductor domain')
        edges.append({**track, 'start': list(a), 'end': list(b), 'length_m': length * 1e-9,
                      'width_m': width * 1e-9})
    for i, edge in enumerate(edges):
        for other in edges[i + 1:]:
            separated(edge, other, c['dimensions']['width_factors'][1])
    if len(geo['pads']) != 2:
        raise ValueError('unmodeled pad contacts; exactly two native terminal pads required')
    terminals, resolved = [], []
    for declaration in c['terminals']:
        matches = [p for p in geo['pads'] if (p['ref'], p['pad']) == (declaration['ref'], declaration['pad'])]
        if len(matches) != 1:
            raise ValueError('unresolved/ambiguous native terminal')
        pad = matches[0]
        anchor = tuple(pad['position_nm'])
        attached = [e for e in edges if list(anchor) in (e['start'], e['end'])]
        if not attached or pad['net'] != c['net']:
            raise ValueError('disconnected terminal or wrong net')
        if (pad['attribute'] != 'SMD' or pad['shape'] != 0 or pad['offset_nm'] != [0, 0] or
                pad['drill_nm'] != [0, 0] or pad['copper_layers'] != [c['layer']] or pad['size_nm'][0] != pad['size_nm'][1] or
                not 0 < pad['size_nm'][0] <= min(e['width_nm'] for e in attached) * c['dimensions']['width_factors'][0]):
            raise ValueError('unqualified ideal centered round terminal pad')
        terminals.append(anchor)
        resolved.append({**declaration, **pad})
    if terminals[0] == terminals[1]:
        raise ValueError('coincident terminal anchors')
    series_parallel(edges, terminals)
    return {'policy': POLICY, 'substrate': substrate, 'edges': edges, 'terminals_nm': [list(t) for t in terminals],
            'terminal_resolution': resolved, 'coverage': {'native_tracks': len(tracks),
            'modeled_edges': len(edges), 'pads': 2, 'excluded_conductors': [], 'complete': True}}


def case_progress(network, c):
    """Yield retained progress after each completed load, including partial cases."""
    m = c['material']
    rho = m['rho_ref_ohm_m'] * (1 + m['alpha_per_c'] * (c['temperature_c'] - m['reference_c']))
    dims = c['dimensions']
    factors = [('nominal', 1., 1.), ('lower', dims['width_factors'][1], dims['thickness_factors'][1]),
               ('upper', dims['width_factors'][0], dims['thickness_factors'][0])]
    for case_id, width_factor, thickness_factor in factors:
        edges = [{**e, 'rho_ohm_m': rho, 'case_width_m': e['width_m'] * width_factor,
                  'thickness_m': c['stackup']['thickness_m'] * thickness_factor,
                  'resistance_ohm': rho * e['length_m'] /
                    (e['width_m'] * width_factor * c['stackup']['thickness_m'] * thickness_factor)}
                 for e in network['edges']]
        case = {'id': case_id, 'edges': edges, 'solutions': {}}
        for load in c['loads']:
            case['solutions'][load['id']] = solve(edges, network['terminals_nm'], load['current_a'])
            yield case


def measurements(analyses, c):
    metrics = {}
    for load in c['loads']:
        for name, key, unit in (('drop', 'drop_v', 'V'), ('loss', 'loss_w', 'W')):
            values = [a['solutions'][load['id']][key] for a in analyses]
            metrics[f'{load["id"]}_{name}'] = {'value': values[0], 'lower': values[1] * (1 - RESIDUAL),
                'upper': values[2] * (1 + RESIDUAL), 'unit': unit, 'condition': condition(c, load),
                'lower_witness': 'lower', 'upper_witness': 'upper', 'envelope_policy': POLICY}
    return metrics


def check_dc_path(ctx):
    from .checks import CheckError
    receipt = {'schema': 'agent-ee.dc-analysis/1', 'candidate_id': ctx.manifest['candidate_id'],
               'complete': False, 'qualification': qualification()}
    write_json(ctx.work / 'analysis.json', receipt)
    ctx.evidence('analysis.json')
    try:
        c, _ = load_inputs(ctx.params, ctx.manifest['files'], ctx.inputs)
        if ctx.scope.get('empirical'):
            raise ValueError('empirical transfer to dimensional DC envelopes is unqualified')
        for path, sha in TOOLS.items():
            if digest(path) != sha:
                raise ValueError('DC native tool identity requires requalification: ' + path)
        board = ctx.candidate_path(ctx.scope['board'])
        if digest(ctx.inputs / ctx.scope['board']) != ctx.manifest['files'][ctx.scope['board']]:
            raise ValueError('native board differs from captured candidate')
        receipt.update(contract=c, board_sha256=ctx.manifest['files'][ctx.scope['board']],
                       qualification=qualification(c))
        write_json(ctx.work / 'analysis.json', receipt)
        if c['schema'] == 'agent-ee.dc-path/2':
            from .dc_rail import check
            return check(ctx, c, receipt)
        ctx.execute(['/usr/bin/python3', '-B', '-m', 'agent_ee.dc_geometry_probe',
                     board, '--out', '/work/geometry.json'], timeout=60)
        ctx.evidence('geometry.json')
        for path, sha in TOOLS.items():
            if digest(path) != sha:
                raise ValueError('DC native tool changed during extraction: ' + path)
        receipt['contract'] = c
        receipt['board_sha256'] = ctx.manifest['files'][ctx.scope['board']]
        network = extract_network(ctx.load_json('geometry.json', 'native DC geometry'), c)
        write_json(ctx.work / 'network.json', network)
        ctx.evidence('network.json')
        receipt['network'] = network
        write_json(ctx.work / 'analysis.json', receipt)
        receipt['analyses'] = []
        for analysis in case_progress(network, c):
            if receipt['analyses'] and receipt['analyses'][-1]['id'] == analysis['id']:
                receipt['analyses'][-1] = analysis
            else:
                receipt['analyses'].append(analysis)
            write_json(ctx.work / 'analysis.json', receipt)
        metrics = measurements(receipt['analyses'], c)
        receipt.update(complete=True, metrics=metrics)
        write_json(ctx.work / 'analysis.json', receipt)
        margins = evaluate_limits(metrics, limits(c))
        ctx.result['details'].update(metrics=metrics, margins=margins, analysis=receipt,
            producer={'domain': 'electrical', 'policy': POLICY, 'qualification': qualification()})
        failed = any(v < 0 for bound in margins.values() for v in bound.values())
        ctx.result.update(execution='ok', outcome='defect' if failed else 'pass',
                          summary='qualified DC path ' + ('violates limits' if failed else 'meets limits'))
        ctx.result['limitations'].append('ideal 1D copper only; ideal endpoint/pad contacts; externally declared temperature; no via, zone, contact spreading, self-heating, RLC or hardware validation')
    except CheckError as exc:
        receipt.update(error=str(exc), execution=exc.execution)
        write_json(ctx.work / 'analysis.json', receipt)
        raise
    except (ValueError, KeyError, TypeError, OSError, OverflowError, ZeroDivisionError) as exc:
        receipt['error'] = str(exc)
        write_json(ctx.work / 'analysis.json', receipt)
        raise CheckError('unsupported', str(exc)) from exc
    finally:
        ctx.evidence('geometry.json', 'stdout.log', 'stderr.log')


def describe_dc_path(ctx):
    from .capabilities import bounds
    ctx.captured(ctx.scope.get('board'))
    contract, dependencies = load_inputs(ctx.params, ctx.files, ctx.inputs)
    if contract['schema'] == 'agent-ee.dc-path/2':
        source = ctx.prerequisite(contract['drc_check'], ('drc',))
        if source.get('params', {}).get('projection'):
            raise ValueError('whole-net DC requires full DRC, not a projection')
    for path in dependencies:
        ctx.captured(path, immutable=True)
    ctx.result['predicates'] = bounds(limits(contract))


if __name__ != '__main__':
    _register_check('dc_path', describe_dc_path, check_dc_path)
