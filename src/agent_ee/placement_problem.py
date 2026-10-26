"""One builder for the placement problem: a native board description plus placement intent (AGENTEE-538).

The placement job hands the Rust engine two documents: the problem it solves
(``capture``) and the saved board it measures (``evaluate``). Both are built
here from the same in-memory board description, so they share one role rule,
one weights table and one refusal path. This module never touches pcbnew;
``placement_job`` reads the native board into the description.

A board description is a plain dict:

* ``outline_rect_nm`` and ``rules`` (``copper_clearance_nm``,
  ``hole_to_hole_clearance_nm``, ``copper_edge_clearance_nm``,
  ``mask_min_web_nm``), the native settings the engine checks against;
* ``pair``: the native default differential pair ``width_nm`` and ``gap_nm``
  and the placement ``clearance_nm`` used for escape-space hints;
* ``footprints``: one entry per native footprint with ``uuid``, ``ref``,
  ``side``, ``locked``, ``position_nm``, ``rotation_deg``, ``quarter_turns``,
  the native classification ``silk_only`` and ``mark_eligible`` (pads, all
  unnetted, no body drawing), ``occupancy`` (record form, or None) with
  ``occupancy_local_nm``, ``occupancy_refusal`` and ``occupancy_diagnostics``,
  ``classified_layers``, ``copper_local_nm``, ``mask_local_nm``,
  ``holes_local_nm``, ``pads`` and ``seats_local_nm`` (``body:fab`` and
  ``body:silk`` rectangles, or None).

The Rust engine owns the contour limits, the occupancy rung names, the
escape-space label and the numeric edge rules: it validates every problem it
reads. This module emits those values once and does not repeat the checks.
"""
from collections import defaultdict
import hashlib

from .storage import canonical

INPUT_SCHEMA = 'agent-ee.placement-job-input/2'
OUTPUT_SCHEMA = 'agent-ee.placement-job-output/1'
NM = 1_000_000
WEIGHTS = {'wire': 1, 'group': 1, 'proximity': 4, 'link': 1, 'edge': 4, 'hard': 50, 'escape': 20}
SILK_ONLY = {'status': 'none', 'reason': 'silk-only non-electrical graphic'}
COPPER_MARK = {'status': 'none', 'reason': 'fixed copper mark: copper obstacle, not a body'}


class JobRefusal(ValueError):
    """The job input cannot be prepared honestly (fail closed)."""


def mm(value):
    return int(round(float(value) * NM))


def rule_mm(value, what):
    from .routing_rules import number
    try:
        return mm(number(value, what))
    except (ValueError, OverflowError) as exc:
        raise JobRefusal(f'{what}: {exc}') from exc


def quarter_turns(degrees, what):
    q = degrees / 90.0
    if abs(q - round(q)) > 1e-9:
        raise JobRefusal(f'{what}: orientation {degrees} is not a quarter turn')
    return int(round(q)) % 4


def identity(document):
    """problem_sha256: the canonical document without its evaluation rows or its own identity."""
    body = {k: v for k, v in document.items() if k not in ('evaluate', 'problem_sha256')}
    return hashlib.sha256(canonical(body)).hexdigest()


def check_output(output, problem, *, code=0, detail=''):
    """The engine's answer must carry the output schema and answer exactly this problem."""
    if code != 0 or not isinstance(output, dict) or output.get('schema') != OUTPUT_SCHEMA:
        error = output.get('error') if isinstance(output, dict) else None
        raise JobRefusal(f'placement engine failed (exit {code}): {error or detail}')
    if output.get('problem_sha256') != problem['problem_sha256']:
        raise JobRefusal('placement engine answered a different problem')
    return output


def role(footprint, declared):
    """The one role rule: a declared fixed pose or a native lock fixes a footprint.

    A fixed footprint that draws no body and has only unnetted pads is a copper
    mark (an obstacle, never a body) only when intent declares it; a native lock
    alone keeps its derived body.
    """
    if footprint['silk_only']:
        return 'silk_only'
    if declared and footprint['mark_eligible']:
        return 'fixed_copper_mark'
    return 'fixed_body' if declared or footprint['locked'] else 'movable'


def fixed_pose_owners(fixed_poses, footprints):
    """Resolve intent fixed poses to native UUIDs by reference base and exact pose."""
    owners = {}
    for index, row in enumerate(fixed_poses):
        base = row['ref'].split('~')[0]
        target = (mm(row['x']), mm(row['y']))
        matches = [f for f in footprints if f['ref'] == base and tuple(f['position_nm']) == target]
        if 'side' in row and matches and not any(f['side'] == row['side'] for f in matches):
            raise JobRefusal(f'intent.fixed_poses[{index}].side: differs from the fixed native side; '
                             'side changes are unsupported')
        matches = [f for f in matches if f['side'] == row.get('side', f['side'])]
        if len(matches) != 1:
            raise JobRefusal(f'intent.fixed_poses[{index}]: fixed pose {row["ref"]} '
                             f'matches {len(matches)} native footprints')
        if 'rot' in row and quarter_turns(row['rot'], row['ref']) != quarter_turns(
                matches[0]['rotation_deg'], row['ref']):
            raise JobRefusal(f'intent.fixed_poses[{index}].rot: differs from the fixed native rotation')
        if matches[0]['uuid'] in owners:
            raise JobRefusal(f'intent.fixed_poses[{index}].ref: duplicates a fixed native footprint')
        owners[matches[0]['uuid']] = row
    return owners


def edge_rules(declarations, footprints):
    """Declared edges by reference, converted to engine units; the engine validates the values."""
    edges = {}
    for row in declarations:
        ref = row['ref']
        if ref in edges or sum(f['ref'] == ref for f in footprints) != 1:
            raise JobRefusal(f'{ref}: edge connector must identify one native footprint')
        rule = {'edge': row.get('edge'), 'max_setback_nm': None}
        if row.get('max_setback_mm') is not None:
            rule['max_setback_nm'] = rule_mm(row['max_setback_mm'], f'{ref}: malformed edge setback')
        if 'overhang_mm' in row:
            # The shape is checked here because conversion would hide it; the engine checks the values.
            interval = row['overhang_mm']
            if not isinstance(interval, dict) or not interval or set(interval) - {'min', 'max'}:
                raise JobRefusal(f'{ref}: malformed overhang interval')
            rule['overhang_nm'] = {
                'min': rule_mm(interval.get('min', 0), f'{ref}: malformed overhang interval'),
                'max': rule_mm(interval['max'], f'{ref}: malformed overhang interval') if 'max' in interval else None}
        edges[ref] = rule
    return edges


def _seat(footprint, declaration, occupancy):
    """Native seat geometry, distinct from the packing reservation around it."""
    if declaration.get('class') == 'edge_receptacle' or declaration.get('context', {}).get('mount_mode') == 'edge_mount':
        for basis in ('body:fab', 'body:silk'):
            if footprint['seats_local_nm'].get(basis) is not None:
                return footprint['seats_local_nm'][basis], basis
    if 'box' not in occupancy:
        raise JobRefusal(f'{footprint["ref"]}: declared edge has no seat geometry')
    return footprint['occupancy_local_nm'], occupancy.get('rung', occupancy['status'])


def escape_hints(row, *, width, gap, clearance):
    """Infer local outward rectangles from captured pads and native pair sizes.

    One P and one N pad must share a row/column off the occupancy centre. The
    preference extends two pair pitches past the pad edge and allows a track
    half-width plus native clearance to either side. These dimensions are an
    agent-selected search heuristic, not an impedance or routability proof.
    Ambiguous, diagonal and multi-pad terminals get no inferred preference.
    """
    if width is None or gap is None or width <= 0 or gap <= 0:
        return []
    body = row['occupancy'].get('local_rect_nm')
    if body is None:
        return []
    nets = defaultdict(list)
    for pad in row['pads']:
        nets[pad['net']].append(pad)
    boxes = {c.get('geometry', {}).get('pad_uuid'): c['rect_nm']
             for c in row['copper_local_nm']}
    result = []
    for name in sorted(nets):
        suffix = next((s for s in ('_P', '+', 'P') if name.endswith(s)), None)
        if suffix is None:
            continue
        mate = name[:-len(suffix)] + {'_P': '_N', '+': '-', 'P': 'N'}[suffix]
        if len(nets[name]) != 1 or len(nets.get(mate, [])) != 1:
            continue
        p, n = nets[name][0], nets[mate][0]
        a, b = p['local_nm'], n['local_nm']
        normal = 0 if a[0] == b[0] and a[1] != b[1] else (
            1 if a[1] == b[1] and a[0] != b[0] else None)
        if normal is None or a[normal] * 2 == body[normal] + body[normal + 2]:
            continue
        if p['uuid'] not in boxes or n['uuid'] not in boxes:
            continue
        pa, pb = boxes[p['uuid']], boxes[n['uuid']]
        rect = [min(pa[0], pb[0]), min(pa[1], pb[1]), max(pa[2], pb[2]), max(pa[3], pb[3])]
        tangent = 1 - normal
        margin = clearance + (width + 1) // 2
        rect[tangent] -= margin
        rect[tangent + 2] += margin
        depth = 2 * (width + gap)
        if a[normal] * 2 > body[normal] + body[normal + 2]:
            rect[normal] = rect[normal + 2]
            rect[normal + 2] += depth
        else:
            rect[normal + 2] = rect[normal]
            rect[normal] -= depth
        result.append({'authority': 'agent-heuristic/1', 'kind': 'differential-terminal-space',
                       'nets': [name, mate], 'pads': [p['uuid'], n['uuid']], 'rect_nm': rect,
                       'width_nm': width, 'gap_nm': gap, 'clearance_nm': clearance})
    return result


def _rails(rails):
    """The one rails shape the engine reads: canonical sidecar rails, an older measurement form, or none."""
    if rails is None:
        return {'shared': {}, 'scoped': {}, 'bindings': None}
    return {'shared': dict(rails.get('shared', rails.get('shared_rails', {}))),
            'scoped': dict(rails.get('scoped', {})), 'bindings': rails.get('bindings')}


def _document(board, *, fixed_poses, edges, groups, rails):
    """The parts capture and evaluation share: geometry, roles, edges, groups, rails and weights."""
    owners = fixed_pose_owners(fixed_poses, board['footprints'])
    rules = edge_rules(edges, board['footprints'])
    declared_edges = {row['ref']: (order, row) for order, row in enumerate(edges)}
    pair = board['pair']
    rows, records = [], []
    for fp in board['footprints']:
        kind = role(fp, fp['uuid'] in owners)
        if kind == 'silk_only':
            occupancy = dict(SILK_ONLY)
        elif kind == 'fixed_copper_mark':
            occupancy = dict(COPPER_MARK)
        elif fp['occupancy_refusal']:
            raise JobRefusal(fp['occupancy_refusal'])
        else:
            occupancy = fp['occupancy']
        q = fp['quarter_turns']
        row = {'uuid': fp['uuid'], 'native_ref': fp['ref'], 'role': kind,
               'pose': {'x_nm': fp['position_nm'][0], 'y_nm': fp['position_nm'][1], 'quarter_turns': q},
               'occupancy': ({k: v for k, v in occupancy.items() if k in ('status', 'label', 'rung')}
                             | ({'local_rect_nm': fp['occupancy_local_nm']} if 'box' in occupancy else {})),
               'copper_local_nm': fp['copper_local_nm'], 'mask_local_nm': fp['mask_local_nm'],
               'holes_local_nm': fp['holes_local_nm'], 'pads': fp['pads']}
        if fp['ref'] in rules:
            order, declaration = declared_edges[fp['ref']]
            seat, basis = _seat(fp, declaration, occupancy)
            row['edge'] = dict(rules[fp['ref']], seat_local_nm=seat, seat_basis=basis, order=order)
            # Mating-face direction is not captured, so an edge connector keeps its native orientation.
            row['rotations'] = [q]
        else:
            row['rotations'] = [0, 1, 2, 3] if kind == 'movable' else [q]
        row['escape_local_nm'] = escape_hints(row, width=pair['width_nm'], gap=pair['gap_nm'],
                                              clearance=pair['clearance_nm'])
        rows.append(row)
        records.append({'uuid': fp['uuid'], 'native_ref': fp['ref'], 'role': kind, 'occupancy': occupancy,
                        'classified_layers': fp['classified_layers'],
                        'diagnostics': fp['occupancy_diagnostics'] if kind in ('movable', 'fixed_body') else [],
                        'fixed_pose': owners.get(fp['uuid']), 'routing_preferences': row['escape_local_nm']})
    document = {'schema': INPUT_SCHEMA,
                'outline': {'kind': 'rectangle', 'rect_nm': board['outline_rect_nm']},
                'rules': dict(board['rules']),
                'footprints': sorted(rows, key=lambda r: r['uuid']),
                'groups': [{k: g[k] for k in ('name', 'anchor', 'refs', 'near') if k in g} for g in groups],
                'rails': _rails(rails),
                'weights': dict(WEIGHTS)}
    return document, records


def _proximity(intent, rows):
    """Declared proximity resolved to captured pad UUIDs; the solver refuses what it cannot score."""
    by_ref = {r['native_ref']: r for r in rows if r['role'] == 'movable'}
    proximity = []
    for index, row in enumerate(intent.get('proximity', [])):
        pads = row['pads']
        for key in ('ref', 'near'):
            ref = row[key]
            if sum(r['native_ref'] == ref and r['role'] == 'movable' for r in rows) != 1:
                raise JobRefusal(f'intent.proximity[{index}].{key}: must identify one movable footprint')
            if ref in pads and set(pads[ref]) - {p['number'] for p in by_ref[ref]['pads']}:
                raise JobRefusal(f'intent.proximity[{index}].pads.{ref}: selected pad is missing')
        a, b = by_ref[row['ref']], by_ref[row['near']]
        # Upstream declaration modes (contract 3.1): a declared side selects exactly the named pads;
        # an undeclared side means all non-aperture pads, and an undeclared subject gives one
        # minimum leg over all pairs without net narrowing. The flags travel to the engine and the report.
        pick = lambda fp_row, numbers: [p['uuid'] for p in fp_row['pads'] if p['number'] in numbers]
        non_aperture = lambda fp_row: [p['uuid'] for p in fp_row['pads'] if p.get('half_nm') != [0, 0]]
        subject_declared, partner_declared = row['ref'] in pads, row['near'] in pads
        proximity.append({'ref': row['ref'], 'near': row['near'], 'max_nm': mm(row['max_mm']),
                          'ref_pads': pick(a, pads[row['ref']]) if subject_declared else non_aperture(a),
                          'near_pads': pick(b, pads[row['near']]) if partner_declared else non_aperture(b),
                          'subject_declared': subject_declared, 'partner_declared': partner_declared,
                          'basis': row.get('basis'), 'limit': row.get('context', {}).get('limit')})
    return proximity


def capture(board, normalized, *, dialect_checks=(), locality_footprints=None):
    """The problem the engine solves, and the per-footprint capture records.

    ``dialect_checks`` are the native checks of an original-dialect intent and
    ``locality_footprints`` the extracted footprints an inline locality document
    is graded against; both come from the native reader.
    """
    intent, groups, dialect = normalized['intent'], normalized['groups'], normalized['compatibility']
    problem, records = _document(board, fixed_poses=intent.get('fixed_poses', []),
                                 edges=intent.get('edge_connectors', []), groups=groups['groups'],
                                 rails=normalized['rails'])
    problem['proximity'] = _proximity(intent, problem['footprints'])
    if dialect:
        problem['compatibility'] = {'schema': dialect['schema'], 'dialect': dialect['dialect'],
                                    'unresolved_claims': dialect['unresolved_claims'], 'withheld': dialect['withheld'],
                                    'native_checks': list(dialect_checks),
                                    'dispositions': dialect['dispositions']}
    if 'electrical_locality' in intent:
        if locality_footprints is None:
            raise JobRefusal('intent.electrical_locality needs the extracted native footprints')
        problem['locality'] = {'document': intent['electrical_locality'], 'footprints': locality_footprints}
    problem['problem_sha256'] = identity(problem)
    return problem, records


def evaluate(board, *, fixed_poses=(), edges=(), groups=(), rails=None, proximity=None):
    """The same problem for a saved board, with one evaluation of its native poses.

    Declared ``pad_edge`` proximity rows go to the engine as declared, so the
    measurement reports an unresolved claim instead of refusing it.
    """
    document, _ = _document(board, fixed_poses=list(fixed_poses), edges=list(edges), groups=list(groups),
                            rails=rails)
    document['proximity'] = [dict(c) for c in (proximity or []) if c.get('basis') == 'pad_edge']
    document['problem_sha256'] = identity(document)
    document['evaluate'] = [{}]
    return document
