"""Electrical land correspondence under a fixed supplier-to-library registration.

Geometry locates lands; it never makes two different terminal names equivalent.
Identity comes from equal numbers, an exact composite of the overlapping native
numbers, or an explicit map. Renumbered repeated plated lands can be located by
their drills once the electrical lands establish a unique rotation. Every merged
land must carry one connected net, and renumbering must preserve the design's net
bindings. All decisions are over captured bytes and the design's nets.
"""
from fractions import Fraction
import re

from . import land_geometry as geometry
from .land_pattern import PatternError, _transform, register

_SHIELD_OR_MECHANICAL = re.compile(
    r'^(?:(?:sh(?:ield(?:ing)?)?|shell|case|chassis|earth|eh|frame|mech(?:anical)?|mount(?:ing)?|mp)'
    r'(?:[_\s-]*(?:gnd|ground|pin|\d+))*'
    r'|(?:gnd|ground)[_\s-]+(?:sh(?:ield(?:ing)?)?|shell|case|chassis|earth|eh|frame|mech(?:anical)?|mount(?:ing)?|mp))$',
    re.IGNORECASE
)


def _is_shield_or_mechanical(pin):
    if not pin or not isinstance(pin, dict):
        return False
    name = pin.get('name', '')
    displayed = pin.get('displayed', '')
    return bool(_SHIELD_OR_MECHANICAL.match(name) or _SHIELD_OR_MECHANICAL.match(displayed))


def _normalize_nets(nets):
    if not nets:
        return {}
    return {k: (None if (v is None or v == '' or str(v).startswith('unconnected-(')) else v)
            for k, v in nets.items()}


def _refuse(message):
    raise PatternError('pad_correspondence', message, 'unsupported')


def validate_declaration(value):
    if (not isinstance(value, dict) or 'pad_map' not in value
            or set(value) - {'rotation_degrees', 'pad_map'}):
        raise ValueError('CPL registration needs pad_map and an optional derived rotation assertion')
    if 'rotation_degrees' in value and (type(value['rotation_degrees']) is not int
            or value['rotation_degrees'] not in (0, 90, 180, 270)):
        raise ValueError('rotation_degrees must assert a quarter turn (0, 90, 180, 270)')
    mapping = value['pad_map']
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError('pad_map must map every supplier number to native number lists')
    for key, targets in mapping.items():
        if (not isinstance(key, str) or not key or not isinstance(targets, list) or not targets
                or any(not isinstance(t, str) or not t for t in targets)
                or len(set(targets)) != len(targets)):
            raise ValueError('pad_map needs nonempty distinct native number strings')
    return value


def _composite(number, names, budget):
    """An exact concatenation of every overlapping native number, in any order."""
    def consume(offset, remaining):
        budget.spend('max_match_states')
        if not remaining:
            return offset == len(number)
        return any(number.startswith(name, offset) and consume(offset + len(name), remaining - {name})
                   for name in sorted(remaining))
    return consume(0, frozenset(names))


def _drill_at(pattern, pad, tolerance):
    center = [Fraction(v) for v in pad['primitive']['center']]
    return [h for h in pattern['holes'] if sum((Fraction(v)-c)**2
            for v, c in zip(h['center'], center)) <= tolerance**2]


def _box(poly):
    xs, ys = zip(*poly)
    return min(xs), min(ys), max(xs), max(ys)


def _intersection_box(a, b):
    box = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    return box if box[0] < box[2] and box[1] < box[3] else None


def _renumbered_plated(left, right, i, j, tolerance):
    a, b = left['pads'][i], right['pads'][j]
    if (set(a['layers']) != {'F.Cu', 'B.Cu'} or set(b['layers']) != {'F.Cu', 'B.Cu'}
            or sum(p['number'] == a['number'] for p in left['pads']) < 2):
        return False
    ah, bh = _drill_at(left, a, tolerance), _drill_at(right, b, tolerance)
    if len(ah) != 1 or len(bh) != 1:
        return False
    budget = geometry._Budget(None)
    circles = geometry._circle_envelopes(2, budget)
    def aperture(hole):
        return geometry._envelope(geometry._primitive(hole, budget), circles, budget)[0]
    def overlaps(a, b):
        return geometry._boolean_area(([aperture(a)], [aperture(b)]), lambda s: all(s), budget) > 0
    # Drill sizes may differ between land-pattern libraries. Unique positive
    # aperture overlap locates a land; it does not attest manufacturability.
    return (sum(overlaps(ah[0], h) for h in right['holes']) == 1
            and sum(overlaps(h, bh[0]) for h in left['holes']) == 1
            and overlaps(ah[0], bh[0]))


def _registered(native, supplier, rotation):
    """Use identified electrical lands as the datum when they are available.

    Bounding boxes depend on pad sizes, so different annuli must not move the
    electrical rows relative to each other. Equal, unique terminal numbers give
    an independent translation datum. Coordinate medians preserve exact finite
    decimals even when the number of anchors has prime factors other than 2/5.
    """
    turned = _transform(supplier, (0, 0), rotation)
    anchors = []
    for number in sorted({p['number'] for p in native['pads']}):
        a = [p for p in native['pads'] if p['number'] == number]
        b = [p for p in turned['pads'] if p['number'] == number]
        if len(a) == len(b) == 1 and set(a[0]['layers']) == set(b[0]['layers']):
            anchors.append((number, [Fraction(x)-Fraction(y) for x, y in
                zip(a[0]['primitive']['center'], b[0]['primitive']['center'])]))
    if len(anchors) < 2:
        left, right = register(native, supplier, dict(translation='copper_bbox_center',
                                                    rotation=str(rotation), mirror=False))
        return left, right, dict(method='copper_bbox_center')
    def median(values):
        values = sorted(values)
        middle = len(values) // 2
        return values[middle] if len(values) % 2 else (values[middle-1] + values[middle]) / 2
    shift = tuple(-median([pair[k] for _, pair in anchors]) for k in (0, 1))
    return native, _transform(turned, shift, 0), dict(method='unique_numbered_electrical_centers_median',
                                                   anchors=[name for name, _ in anchors])


def _overlap_graph(left, right):
    budget = geometry._Budget(None)
    lp, rp = geometry._pattern(left, budget), geometry._pattern(right, budget)
    circles = geometry._circle_envelopes(2, budget)
    le = [geometry._envelope(p[2], circles, budget) for p in lp[0]]
    re = [geometry._envelope(p[2], circles, budget) for p in rp[0]]
    # Positive certified copper intersection, with drills removed, on identical
    # surfaces. Touching boundaries alone do not establish a correspondence.
    lh = [geometry._envelope(p, circles, budget)[1] for p in lp[1]]
    rh = [geometry._envelope(p, circles, budget)[1] for p in rp[1]]
    lhi = [geometry._envelope(p, circles, budget)[0] for p in lp[1]]
    rhi = [geometry._envelope(p, circles, budget)[0] for p in rp[1]]
    lb, rb = [_box(p[1]) for p in le], [_box(p[1]) for p in re]
    def local_holes(polys, box):
        return [poly for poly in polys if _intersection_box(_box(poly), box) is not None]
    edges, possible = [], []
    for i, a in enumerate(lp[0]):
        for j, b in enumerate(rp[0]):
            budget.spend('max_match_states')
            box = _intersection_box(lb[i], rb[j])
            if box is None or not set(a[1]) & set(b[1]):
                continue
            both = lambda s: s[0] and s[1] and not s[2] and not s[3]
            if geometry._boolean_area(([le[i][1]], [re[j][1]], local_holes(lhi, box),
                                       local_holes(rhi, box)), both, budget) > 0:
                possible.append((i, j))
            if a[1] == b[1] and geometry._boolean_area(
                    ([le[i][0]], [re[j][0]], local_holes(lh, box), local_holes(rh, box)), both, budget) > 0:
                edges.append((i, j))
    if {i for i, _ in edges} != set(range(len(lp[0]))) or {j for _, j in edges} != set(range(len(rp[0]))):
        _refuse('a native or supplier land has no certified copper correspondence')
    return lp, rp, edges, possible, budget


def _check_alias_nets(mapping, nets, supplier_pins=()):
    """Supplier-only numbering inherits native nets; existing bindings cannot move.

    A group that retains its native number also retains that net identity for
    its supplier pins. An unconnected extra pin cannot join that connected group.
    Fully renumbered repeated lands use the native terminal's design binding for
    shield/mechanical pins or unlisted pins; other functions must be bound by the design.
    """
    symbol_pins_map = {p['number']: p for p in supplier_pins if isinstance(p, dict) and 'number' in p}
    target_counts = {}
    for targets in mapping.values():
        for target in targets:
            target_counts[target] = target_counts.get(target, 0) + 1

    for name, targets in mapping.items():
        pin = symbol_pins_map.get(name)
        can_inherit = (pin is None) or _is_shield_or_mechanical(pin)
        for target in targets:
            if name == target:
                continue
            is_lug_group = target_counts.get(target, 0) > 1
            if name not in nets:
                if len(targets) > 1:
                    continue
                if target not in mapping:
                    if not is_lug_group or can_inherit:
                        continue
            source_net, target_net = nets.get(name), nets.get(target)
            if source_net != target_net:
                _refuse(f'supplier pad {name} net {source_net or "unconnected"!r} differs from '
                        f'native pad {target} net {target_net or "unconnected"!r}')
            if is_lug_group and not can_inherit and name not in nets:
                _refuse(f'supplier pad {name} is not a shield or mechanical pin; '
                        f'design nets must bind it to native pad {target}')


def verify(native, supplier, rotation, *, pad_map=None, nets=None, tolerance='100000', supplier_pins=()):
    """Prove complete land coverage and identity at one quarter-turn registration.

    ``pad_map`` is supplier number -> native number list. ``nets`` binds native
    terminals and any separately assigned supplier numbers to design nets.
    Unknown or no-connect pins cannot authorize a merged physical short. Repeated
    plated aliases preserve supplied net bindings regardless of symbol pin count.
    This fixed-angle check does not establish independent orientation evidence.
    """
    nets = _normalize_nets(nets)
    left, right, translation = _registered(native, supplier, rotation)
    lp, rp, edges, possible, budget = _overlap_graph(left, right)
    native_names = {p[0] for p in lp[0]}
    supplier_names = {p[0] for p in rp[0]}
    if pad_map is not None and set(pad_map) != supplier_names:
        _refuse('pad_map must cover exactly the supplier pad numbers')
    mapping, proofs = {}, {}
    known = 0
    for name in sorted(supplier_names):
        js = [j for j, p in enumerate(rp[0]) if p[0] == name]
        pairs = [(i, j) for i, j in edges if j in js]
        names = {lp[0][i][0] for i, _ in pairs}
        if name in native_names:
            if names != {name}:
                _refuse(f'supplier pad {name} overlaps different electrical terminals {sorted(names)}')
            basis = 'equal_number_and_copper'
            known += 1
        elif _composite(name, names, budget):
            basis = 'composite_number_and_copper'
            known += 1
        elif pad_map is not None:
            basis = 'declared_map_and_copper'
        elif (len(names) == 1 and all(sum(k == j for _, k in edges) == 1
                and sum(k == i for k, _ in edges) == 1
                and _renumbered_plated(left, right, i, j, Fraction(tolerance)) for i, j in pairs)):
            basis = 'repeated_plated_land_and_drill_geometry'
        else:
            _refuse(f'supplier pad {name}: electrical identity of {sorted(names)} is unproven; declare pad_map')
        if pad_map is not None and set(pad_map[name]) != names:
            _refuse(f'pad_map for {name} disagrees with copper correspondence {sorted(names)}')
        if len(names) > 1:
            net_ids = {nets.get(n) for n in names}
            if None in net_ids or '' in net_ids or len(net_ids) != 1:
                _refuse(f'supplier pad {name} merges distinct or unconnected native nets: {sorted(names)}')
        mapping[name] = sorted(names)
        proofs[name] = basis
    if pad_map is None and known < 2:
        _refuse('automatic registration needs at least two identified electrical terminal groups')
    if any(lp[0][i][0] not in mapping[rp[0][j][0]] for i, j in possible):
        _refuse('copper may contact an unrelated electrical land on a shared layer')
    _check_alias_nets(mapping, nets, supplier_pins=supplier_pins)
    return dict(rotation_degrees=rotation, pad_map=mapping, identity_proof=proofs, translation=translation,
                pairs=[dict(native_index=i, supplier_index=j) for i, j in edges])


def _symbol_names(pins, *, supplier=False):
    found = {}
    for pin in pins:
        number, name = pin['number'], pin['name']
        if (supplier and pin.get('displayed') != number) or name in ('', '~'):
            continue
        found.setdefault(number, set()).add(name)
    return {number: next(iter(names)) for number, names in found.items() if len(names) == 1}


def derive(native, supplier, *, declaration=None, nets=None, supplier_pins=(), native_pins=()):
    """Derive orientation without an author map, then check the declared identities.

    A declaration may name lands and assert the result, but is never used to
    select a turn. Ambiguous geometry needs agreeing captured symbol names.
    """
    mapping = validate_declaration(declaration)['pad_map'] if declaration is not None else None
    candidates, geometric, reasons = [], [], []
    if supplier_pins is None:
        supplier_pins = ()
    snames, nnames = _symbol_names(supplier_pins, supplier=True), _symbol_names(native_pins)
    geometry_nets = {p['number']: 'geometry-only' for pattern in (native, supplier) for p in pattern['pads']}
    for angle in (0, 90, 180, 270):
        try:
            candidates.append(verify(native, supplier, angle, nets=geometry_nets, supplier_pins=supplier_pins))
        except PatternError as exc:
            reasons.append(f'{angle}: {exc}')
        # Geometry is evaluated without the author's pad map or circuit nets.
        # Provisional names describe overlaps only; final identity is checked below.
        try:
            left, right, _ = _registered(native, supplier, angle)
            lp, rp, edges, _, _ = _overlap_graph(left, right)
            inferred = {name: sorted({lp[0][i][0] for i, j in edges if rp[0][j][0] == name})
                        for name in {p[0] for p in rp[0]}}
            provisional = verify(native, supplier, angle, pad_map=inferred, nets=geometry_nets,
                                 supplier_pins=supplier_pins)
            named = [(nnames[lp[0][i][0]], snames[rp[0][j][0]]) for i, j in edges
                     if lp[0][i][0] in nnames and rp[0][j][0] in snames]
            geometric.append((provisional, len(named) == len(edges) and all(a == b for a, b in named)))
        except PatternError:
            continue
    if len(candidates) == 1:
        angle, basis = candidates[0]['rotation_degrees'], 'independent_pad_identities'
    elif len(geometric) == 1:
        angle, basis = geometric[0][0]['rotation_degrees'], 'unique_copper_geometry'
    else:
        named = [result for result, agrees in geometric if agrees]
        if len(named) != 1:
            gap = '[tool_gap] ' if len(geometric) >= 2 else ''
            _refuse(f'{gap}orientation requires one independently admissible quarter turn; '
                    f'found {len(geometric)} geometric and {len(named)} symbol-name registrations; '
                    'inspect captured geometry and symbol identities; author orientation selection is refused; '
                    + '; '.join(reasons))
        angle, basis = named[0]['rotation_degrees'], 'captured_symbol_pin_names'
    symbolic = next((result['pad_map'] for result, agrees in geometric
                     if agrees and result['rotation_degrees'] == angle), None)
    result = verify(native, supplier, angle, pad_map=mapping if mapping is not None else symbolic,
                    nets=nets, supplier_pins=supplier_pins)
    if mapping is None and symbolic is not None:
        result['identity_proof'] = {name: 'captured_symbol_name_and_copper'
            if proof == 'declared_map_and_copper' else proof for name, proof in result['identity_proof'].items()}
    if declaration is not None and 'rotation_degrees' in declaration and declaration['rotation_degrees'] != angle:
        _refuse('declared rotation assertion disagrees with independently derived orientation')
    return dict(result, orientation_proof=basis)

