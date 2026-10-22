"""Placement-stage helper dispatch and placement-only write scope (AGENTEE-91).

The coordinator, not the writer, runs the pinned AGENTEE-54 placer once per
placement iteration as an AGENTEE-95 stage step, on a read-only snapshot of the
delivered design and into a fresh scratch destination. A strict comparison of the
complete saved boards admits footprint translations, quarter turns, qualified
side flips and the rigid motion of footprint-owned zone polygons.
Receipts, the helper's own judgment and its local DRC are provenance only; the
campaign's fresh captured checks grade every proposal.

A declaration may also name a captured AGENTEE-56 sub-layout request (AGENTEE-129).
Its group-local copper is then admitted, and nothing else: each added board item
must be a planned copy of a whole group that is new on the source board, with the
UUID derived from the captured sub-board bytes, the exact transformed geometry and
the voted net, inside the group's region, as a fresh sandboxed pcbnew read shows.
"""
import argparse
import copy
import hashlib
import json
import re
import subprocess
import tempfile
import uuid
from decimal import Decimal, DecimalException
from pathlib import Path, PurePosixPath

from .storage import digest, identity, read_json, write_json
from .symbol_library import LibraryError, children, parse_sexpr
from .placer import rotate

ADAPTER_SCHEMA = 'agent-ee.placement-adapter/1'
SCOPE_SCHEMA = 'agent-ee.placement-write-scope/2'
RECORD_SCHEMA = 'agent-ee.placement-adapter-record/2'
BINDING_SCHEMA = 'agent-ee.placement-binding/1'
CONTEXT_SCHEMA = 'agent-ee.placement-writer-context/1'
DRIVER_SCHEMA = 'agent-ee.placement-driver/1'
COPPER_SCHEMA = 'agent-ee.group-copper-scope/1'
SUBLAYOUT_KEYS = {'request', 'sub_boards'}
BLOCKS_KEYS = {'netlist', 'arrangement', 'candidates'}
# Board-level nodes a reused group may add, with the pcbnew class its planned copy must have.
GROUP_TAGS = {'segment': 'PCB_TRACK', 'arc': 'PCB_ARC', 'via': 'PCB_VIA', 'zone': 'ZONE', 'gr_text': 'PCB_TEXT',
              **{tag: 'PCB_SHAPE' for tag in ('gr_line', 'gr_rect', 'gr_circle', 'gr_arc', 'gr_poly', 'gr_curve')}}
# Children of a copied node that the transform or the net vote may change; the pcbnew read checks
# them. Every other token must equal the captured sub-board's source node.
COPY_GEOMETRY = ('start', 'end', 'mid', 'center', 'at', 'pts', 'polygon', 'filled_polygon', 'net', 'net_name',
                 'uuid')
RECORD_KEYS = {'schema', 'implementation', 'source_candidate_id', 'stage', 'declaration_sha256', 'binding_sha256',
               'board', 'protected', 'protected_refs', 'helper', 'writer_scope', 'proposal_scope', 'status',
               'error', 'authority'}
ATTEMPT_STATUSES = ('incomplete', 'refused', 'cancelled')
HELPER_KEYS = {'receipt', 'request_sha256', 'receipt_id', 'status', 'receipt_sha256', 'result_sha256', 'scope'}
HELPERS = ('agent-ee.placer/1',)
STEP = 'agent-ee-placer'
TOOL_AT, REQUEST_AT, ROOT = '/placement-tool', '/placement-request', '/work/placement'
OUTPUT = 'placement/placed/candidate.kicad_pcb'
RESULT = 'run/placement/placement-result.json'
# Children whose saved `at` angle KiCad 10 stores as footprint plus local angle.
ROTATED_CHILDREN = ('pad', 'property', 'fp_text')
# Angles stay exact: saved tokens are scaled to integers without any Decimal context.
# Angles needing more than this many digits before or after the point are refused, never rounded.
MAX_PLACES = 32
FULL_TURN = 360 * 10 ** MAX_PLACES
QUARTER_TURNS = tuple(turn * 10 ** MAX_PLACES for turn in (0, 90, 180, 270))
MAX_TOKEN = 128
# The exact transform family a placement iteration may apply; anything else is refused.
TRANSFORM_FAMILY = 'agent-ee.placement-transforms/3'
TRANSFORMS = ('translations, quarter-turn rotations (0, 90, 180 or 270 degrees) and front/back side '
              'flips as pcbnew saves them (native-qualified custom-pad footprints including arcs, or '
              'stock footprints without arcs; chamfered, trapezoid and offset-drill pads and '
              'vertically justified text remain unsupported); owned straight zone polygons follow '
              'the owner rigidly, with every other zone setting preserved; permitted visible silk fields may '
              'change position only, preserving all other marking properties')
HALF_TURN = 180 * 10 ** MAX_PLACES
# Side flips, qualified against KiCad 10.0.7 FOOTPRINT::Flip in both directions from every
# quarter turn: footprint-local points are saved mirrored in y, front/back layers swap, the
# footprint angle is negated plus a quarter turn, and texts gain or lose `justify mirror`.
FLIP_LAYERS = {f'F.{n}': f'B.{n}' for n in ('Cu', 'Adhes', 'Paste', 'SilkS', 'Mask', 'CrtYd', 'Fab')}
FLIP_LAYERS.update({back: front for front, back in list(FLIP_LAYERS.items())})
SIDE_NEUTRAL_LAYERS = ('*.Cu', '*.Mask')
FLIP_GRAPHICS = ('fp_line', 'fp_rect', 'fp_circle', 'fp_poly')
FLIP_TEXTS = ('property', 'fp_text')
POINT_TAGS = ('start', 'end', 'center', 'xy')
# Numeric grammar positions of footprint children. pcbnew's resave rewrites number spellings there
# (29.0 -> 29, 0.50 -> 0.5), so their numeric arguments compare as exact decimals. A position is its tag
# under a known parent path from the footprint, never a tag name alone: each rule is (slots, {child tag:
# rule}), where slots maps each supported count of plain arguments to the 1-based argument slots that
# are numbers, or is None for a container whose plain arguments are all names. A node with another
# argument count, every non-numeric slot (the `at` tail, the hatch style, stroke types, layer names),
# names, values, net names and pad numbers (the parser drops quoting, and a quoted "1.0" pad number
# differs from "1"), unknown children and their whole subtrees, and everything outside footprints
# (board setup, stackup, rules) stay exact.
_CONTAINER = None
_SCALAR, _PAIR, _TRIPLE = ({n: frozenset(range(1, n + 1))} for n in (1, 2, 3))
_POINT = (_PAIR, {})
_LAYER = (_CONTAINER, {})
_AT = ({2: frozenset((1, 2)), 3: frozenset((1, 2, 3))}, {})
_STROKE = {'stroke': (_CONTAINER, {'width': (_SCALAR, {})}), 'width': (_SCALAR, {}), 'layer': _LAYER}
# One rule per graphic kind: only that kind's own point children are positions (fp_line has no mid).
_GRAPHICS = {
    'fp_line': (_CONTAINER, {'start': _POINT, 'end': _POINT, **_STROKE}),
    'fp_rect': (_CONTAINER, {'start': _POINT, 'end': _POINT, **_STROKE}),
    'fp_circle': (_CONTAINER, {'center': _POINT, 'end': _POINT, **_STROKE}),
    'fp_arc': (_CONTAINER, {'start': _POINT, 'mid': _POINT, 'end': _POINT, **_STROKE}),
    'fp_poly': (_CONTAINER, {'pts': (_CONTAINER, {'xy': _POINT}), **_STROKE}),
}
_TEXT = (_CONTAINER, {'at': _AT, 'layer': _LAYER, 'effects': (_CONTAINER, {'font': (_CONTAINER, {
    'size': (_PAIR, {}), 'thickness': (_SCALAR, {})})})})
_MARGINS = dict.fromkeys(('solder_mask_margin', 'solder_paste_margin', 'solder_paste_margin_ratio', 'clearance',
                          'thermal_width', 'thermal_gap'), (_SCALAR, {}))
_XYZ = (_CONTAINER, {'xyz': (_TRIPLE, {})})
FOOTPRINT_NUMBERS = {
    **_MARGINS, **_GRAPHICS, **dict.fromkeys(FLIP_TEXTS, _TEXT),
    'pad': (_CONTAINER, {**_MARGINS, 'at': _AT, 'layers': _LAYER,
                         **dict.fromkeys(('size', 'rect_delta'), (_PAIR, {})),
                         **dict.fromkeys(('roundrect_rratio', 'chamfer_ratio', 'thermal_bridge_width',
                                          'thermal_bridge_angle', 'die_length'), (_SCALAR, {})),
                         # A round drill only: an oval drill's `oval` slot keeps the whole drill exact.
                         'drill': (_SCALAR, {'offset': _POINT})}),
    'model': (_CONTAINER, dict.fromkeys(('offset', 'scale', 'rotate'), _XYZ)),
    'zone': (_CONTAINER, {'hatch': ({2: frozenset((2,))}, {}), 'min_thickness': (_SCALAR, {}),
                          'connect_pads': (_CONTAINER, {'clearance': (_SCALAR, {})}),
                          'fill': (_CONTAINER, dict.fromkeys(('thermal_gap', 'thermal_bridge_width',
                                                              'island_area_min'), (_SCALAR, {})))}),
}
FOOTPRINT_RULE = (_CONTAINER, FOOTPRINT_NUMBERS)
# Tags a side flip transforms; under a flip they are interpreted only at their known positions above.
FLIP_GEOMETRY = (*POINT_TAGS, 'at', 'layer', 'layers')
FLIP_PAD_SHAPES = ('rect', 'roundrect', 'circle', 'oval')
# pcbnew re-derives arcs on a flip (endpoints swapped, midpoint recomputed), and these pad
# features are not stored as a plain mirror; a flip of a footprint using them is refused.
FLIP_UNSUPPORTED = ('fp_arc', 'offset', 'rect_delta', 'chamfer', 'chamfer_ratio', 'primitives', 'options')
JUSTIFY = ('left', 'right', 'top', 'bottom', 'mirror')
H_SWAP = {'left': 'right', 'right': 'left'}
# Only plain decimal and exponent forms, as pcbnew saves them, are read as numbers. Decimal()
# alone would also accept forms such as `9_0`, ` 90` or `nan` that KiCad's parser reads differently.
NUMBER = re.compile(r'-?[0-9]+(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?\Z')
LAUNCHER = '''"""Constant launcher: run the hashed read-only agent_ee snapshot, not /app."""
import sys
sys.path.insert(0, '/placement-tool')
from agent_ee.placement_adapter import sandbox_main
raise SystemExit(sandbox_main(sys.argv[1:]))
'''


def _relative(value, label):
    path = PurePosixPath(value) if isinstance(value, str) and value else None
    if path is None or path.is_absolute() or '..' in path.parts or str(path) != value:
        raise ValueError(f'{label} must be a normalized relative candidate path')
    return value


def validate_sublayout(value):
    """Captured sub-layout request and sub-board root whose group-local copper is admitted."""
    if not isinstance(value, dict) or set(value) != SUBLAYOUT_KEYS:
        raise ValueError(f'placement sublayout must have exactly {sorted(SUBLAYOUT_KEYS)}')
    request = _relative(value['request'], 'sub-layout request')
    if not request.endswith('.json'):
        raise ValueError('sub-layout request must be a captured .json file')
    return {'request': request, 'sub_boards': _relative(value['sub_boards'], 'sub-board root')}


def validate_blocks(value):
    """Captured block-arrangement request files (AGENTEE-439), in declared candidate order.

    The netlist is the captured native export every candidate was packed against; the
    helper uses it only while a fresh in-sandbox export has the same stamp-masked identity."""
    if (not isinstance(value, dict) or not BLOCKS_KEYS <= set(value) or set(value) - BLOCKS_KEYS - {'allocation'}):
        raise ValueError(f'placement blocks must have exactly {sorted(BLOCKS_KEYS)} and optionally allocation')
    def captured(rel, label, suffix):
        rel = _relative(rel, label)
        if not rel.endswith(suffix):
            raise ValueError(f'{label} must be a captured {suffix} file')
        return rel
    candidates = value['candidates']
    if not isinstance(candidates, list) or not candidates:
        raise ValueError('placement blocks need a nonempty list of captured candidate files')
    normalized = {'netlist': captured(value['netlist'], 'block netlist', '.xml'),
                  'arrangement': captured(value['arrangement'], 'block arrangement', '.json'),
                  'candidates': [captured(c, 'block candidate', '.json') for c in candidates]}
    if 'allocation' in value:
        normalized['allocation'] = captured(value['allocation'], 'block allocation', '.json')
    paths = [normalized['netlist'], normalized['arrangement'], *normalized['candidates']]
    paths += [normalized['allocation']] if 'allocation' in normalized else []
    if len(set(paths)) != len(paths):
        raise ValueError('placement block files must be distinct')
    return normalized


def validate_declaration(value):
    """Return the normalized campaign declaration; no placement default is implied."""
    keys = {'schema', 'helper', 'policy', 'fixed_footprints', 'timeout_s'}
    if (not isinstance(value, dict) or value.get('schema') != ADAPTER_SCHEMA or not keys <= set(value) or
            set(value) - keys - {'sublayout', 'targeted_plans', 'blocks'}):
        raise ValueError(f'expected {ADAPTER_SCHEMA} with exactly {sorted(keys)} and optionally sublayout, '
                         'targeted_plans or blocks')
    helper, policy = value['helper'], value['policy']
    if helper is not None and helper not in HELPERS:
        raise ValueError(f'placement helper must be null or one of {HELPERS}')
    if (helper is None) != (policy is None):
        raise ValueError('a placement helper requires a captured packing policy, and only then')
    if policy is not None:
        _relative(policy, 'placement policy')
    fixed = value['fixed_footprints']
    if not isinstance(fixed, list) or len(set(map(str, fixed))) != len(fixed):
        raise ValueError('fixed_footprints must be a unique list of native footprint UUIDs')
    for item in fixed:
        try:
            if not isinstance(item, str) or str(uuid.UUID(item)) != item:
                raise ValueError
        except ValueError:
            raise ValueError(f'fixed footprint is not a canonical native UUID: {item!r}') from None
    timeout = value['timeout_s']
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 86400:
        raise ValueError('placement timeout_s must be an integer from 1 to 86400')
    normalized = {'schema': ADAPTER_SCHEMA, 'helper': helper, 'policy': policy,
                  'fixed_footprints': sorted(fixed), 'timeout_s': timeout}
    if 'targeted_plans' in value:
        if helper is None:
            raise ValueError('targeted_plans requires a placement helper')
        normalized['targeted_plans'] = _relative(value['targeted_plans'], 'targeted plans')
    if 'blocks' in value:
        if helper is None:
            raise ValueError('placement blocks require a placement helper')
        if 'targeted_plans' in value:
            raise ValueError('placement blocks and targeted_plans cannot be combined')
        # Only present when declared, so the identity of every other declaration is unchanged.
        normalized['blocks'] = validate_blocks(value['blocks'])
    if 'sublayout' in value:
        if helper is not None:
            # The pinned placer refuses any board that holds tracks, vias or zones, so once a group
            # is reused every helper run would fail; refused here, before any iteration is spent.
            raise ValueError('a placement sublayout cannot be declared with a helper: '
                             f'{helper} refuses boards that hold tracks, vias or zones')
        # Only present when declared, so the identity of a copper-free declaration is unchanged.
        normalized['sublayout'] = validate_sublayout(value['sublayout'])
    return normalized


def is_placement_iteration(state, manifest):
    """Placement-only dispatch: a staged scope whose earliest failing stage is placement."""
    return (manifest['scope'].get('stages') is not None and bool(state['history']) and
            state['history'][-1].get('earliest_failing_stage') == 'placement')


def bind_declaration(declaration, manifest, mutable_inputs):
    """Resolve the declaration against one captured candidate's immutable inputs."""
    declaration = validate_declaration(declaration)
    scope, files = manifest['scope'], manifest['files']
    board, schematic = scope.get('board'), scope.get('schematic')
    if board not in files or board not in mutable_inputs:
        raise ValueError('placement scope requires the captured board to be a mutable design input')
    if declaration['helper'] is not None:
        if schematic not in files:
            raise ValueError('placement helper requires the captured schematic for native netlist export')
        if declaration['policy'] not in files or declaration['policy'] in mutable_inputs:
            raise ValueError('placement policy must be a captured immutable input')
    plans = declaration.get('targeted_plans')
    if plans is not None and (plans not in files or plans in mutable_inputs):
        raise ValueError('targeted plans must be a captured immutable input')
    sub = declaration.get('sublayout')
    if sub is not None:
        if sub['request'] not in files or sub['request'] in mutable_inputs:
            raise ValueError('sub-layout request must be a captured immutable input')
        if any(rel == sub['sub_boards'] or rel.startswith(sub['sub_boards'] + '/') for rel in mutable_inputs):
            raise ValueError('sub-boards must be captured immutable inputs')
    section = declaration.get('blocks')
    if section is not None:
        for rel in [section['netlist'], section['arrangement'], *section['candidates'],
                    *([section['allocation']] if 'allocation' in section else [])]:
            if rel not in files or rel in mutable_inputs:
                raise ValueError(f'placement block file {rel} must be a captured immutable input')
        section = {**section, 'netlist_sha256': files[section['netlist']]}
        return {**declaration, 'blocks': section, 'board': board, 'schematic': schematic}
    return {**declaration, 'board': board, 'schematic': schematic}


def envelope_references(record):
    """References an envelope fixes: its fixed footprints and its mounting holes.

    The scaffold writes each hole as a board-only footprint whose reference is the hole id
    (AGENTEE-177), so a declared hole stays fixed even if its source footprint is unlocked."""
    return sorted({f['reference'] for f in record['fixed_footprints']} |
                  {h['id'] for h in record['mounting_holes']})


def envelope_overhangs(record):
    """Fixed-courtyard overhang allowances an envelope declares (AGENTEE-212).

    Each is bound to one reference and its declared outline edges, each with a
    maximum distance, and cites the envelope bytes; the packer accepts no other
    overhang."""
    from .placement_predicates import envelope_overhangs as declared
    return declared(record)


def envelope_fixed(manifest, inputs, mutable_inputs):
    """Fixed references of every captured envelope that a placement check consumes.

    Read from the captured envelope bytes, never from an authored list, so an incomplete
    fixed_footprints declaration cannot leave an envelope-fixed connector or hole movable.
    Validated against the capture inventory (AGENTEE-271), so every owner quote is verified
    in an immutable captured source or the envelope is refused."""
    from .board_envelope import validate
    found = {}
    for spec in manifest['scope'].get('checks', []):
        params = spec.get('params') if isinstance(spec.get('params'), dict) else {}
        rel = params.get('envelope') if spec.get('kind') == 'placement' else None
        if rel is None or rel in found:
            continue
        if not isinstance(rel, str) or rel not in manifest['files'] or rel in mutable_inputs:
            raise ValueError(f'placement envelope {rel!r} must be a captured immutable input')
        path = Path(inputs) / rel
        if path.is_symlink() or not path.is_file() or digest(path) != manifest['files'][rel]:
            raise ValueError(f'captured placement envelope {rel!r} changed')
        record = validate(path.read_bytes(), captured=(manifest['files'], inputs))
        mutable = sorted(set(record['constraint_provenance']['source_paths']) & set(mutable_inputs))
        if mutable:
            raise ValueError(f'placement envelope {rel!r} cites owner quotes in mutable inputs: {mutable}')
        found[rel] = {'path': rel, 'sha256': manifest['files'][rel], 'envelope_id': record['envelope_id'],
                      'references': envelope_references(record)}
        overhangs = envelope_overhangs(record)
        if overhangs:
            found[rel]['overhangs'] = overhangs
        provenance = envelope_provenance(record)
        if provenance:
            found[rel]['provenance'] = provenance
    return [found[rel] for rel in sorted(found)]


def envelope_provenance(record):
    """Outline and fixed-reference provenance of an envelope that declares any (AGENTEE-266).

    None when every placement-bound field lacks provenance: the placer then reports
    unknown provenance and enforces as before."""
    entries = {e['locator']: {k: e[k] for k in ('class', 'enforcement', 'soft_reason') if k in e}
               for e in record['constraint_provenance']['constraints']}
    fixed = {f['reference']: entries[f'/fixed_footprints/{i}'] for i, f in enumerate(record['fixed_footprints'])}
    fixed.update({h['id']: entries[f'/mounting_holes/{i}'] for i, h in enumerate(record['mounting_holes'])})
    value = {'outline': entries['/outline'], 'fixed': fixed}
    if all(e['class'] == 'unknown' for e in [value['outline'], *fixed.values()]):
        return None
    return value


def combined_provenance(envelopes):
    """One placement provenance from every consumed envelope; a limit is soft only if all agree."""
    declared = [e['provenance'] for e in envelopes if 'provenance' in e]
    if not declared:
        return None
    if len(declared) != len(envelopes):
        return None  # Another envelope states the same board without provenance: unknown, enforced as before.
    def merge(items):
        hard = [i for i in items if i['enforcement'] == 'hard']
        if not hard:
            return items[0]
        return hard[0] if len({i['class'] for i in hard}) == 1 else {'class': 'unknown', 'enforcement': 'hard'}
    fixed = {}
    for value in declared:
        for ref, entry in value['fixed'].items():
            fixed.setdefault(ref, []).append(entry)
    return {'outline': merge([v['outline'] for v in declared]),
            'fixed': {ref: merge(items) for ref, items in sorted(fixed.items())}}


def _scope_edge_margin_mm(inputs, manifest):
    """Declared edge-clearance margin from placement check params or captured fab profiles."""
    margins = []
    files = manifest.get('files', {})
    scope = manifest.get('scope', {})
    profiles = set()
    envelope = scope.get('envelope')
    if isinstance(envelope, dict) and isinstance(envelope.get('profile'), str):
        profiles.add(envelope['profile'])
    for spec in scope.get('checks', []):
        params = spec.get('params') if isinstance(spec.get('params'), dict) else {}
        if spec.get('kind') == 'placement' and params.get('criterion') == 'edge-clearance/1':
            margin = params.get('min_courtyard_edge_mm')
            if not isinstance(margin, bool) and isinstance(margin, (int, float)):
                margins.append(margin)
        if spec.get('kind') == 'fab_profile' and isinstance(params.get('fab_profile'), str):
            profiles.add(params['fab_profile'])
    for rel in sorted(profiles):
        if rel not in files:
            continue
        path = Path(inputs) / rel
        if path.is_symlink() or not path.is_file() or digest(path) != files[rel]:
            raise ValueError(f'captured fab profile {rel!r} changed')
        profile = read_json(path)
        for limit in profile.get('limits', []) if isinstance(profile, dict) else []:
            if isinstance(limit, dict) and limit.get('key') == 'min_edge_clearance_mm':
                value = limit.get('value')
                if not isinstance(value, bool) and isinstance(value, (int, float)):
                    margins.append(value)
    return max(margins) if margins else None


def _policy(inputs, bound, manifest):
    if bound['policy'] is None:
        return {'fixed_refs': []}
    path = Path(inputs) / bound['policy']
    if path.is_symlink() or not path.is_file() or digest(path) != manifest['files'][bound['policy']]:
        raise ValueError('captured placement policy changed')
    policy = read_json(path)
    if (not isinstance(policy, dict) or not isinstance(policy.get('fixed_refs'), list) or
            any(not isinstance(r, str) for r in policy['fixed_refs'])):
        raise ValueError('placement policy must be an agent-ee.packing-policy/1 object with fixed_refs')
    if 'targeted_plans' in policy:
        raise ValueError('targeted_plans must come from the captured declaration')
    if 'blocks' in policy:
        raise ValueError('blocks must come from the captured declaration')
    scope_margin = _scope_edge_margin_mm(inputs, manifest)
    if scope_margin is not None:
        current = policy.get('min_courtyard_edge_mm')
        if current is None or (not isinstance(current, bool) and isinstance(current, (int, float))
                               and scope_margin > current):
            policy['min_courtyard_edge_mm'] = scope_margin
    if 'targeted_plans' in bound:
        plans = Path(inputs) / bound['targeted_plans']
        if plans.is_symlink() or not plans.is_file() or digest(plans) != manifest['files'][bound['targeted_plans']]:
            raise ValueError('captured targeted plans changed')
        policy['targeted_plans'] = read_json(plans)
    return policy


def bind_locality(policy, manifest, inputs, mutable_inputs):
    """Bind pack guidance to the one immutable locality file graded in this scope."""
    from .electrical_locality import CRITERION, load_json, validate
    paths = {check['params']['locality'] for check in manifest['scope'].get('checks', [])
             if check.get('kind') == 'placement' and check.get('params', {}).get('criterion') == CRITERION}
    if not paths:
        if 'electrical_locality' in policy or 'electrical_locality_binding' in policy:
            raise ValueError('packing electrical locality requires a captured coordinator locality check')
        return None
    if len(paths) != 1:
        raise ValueError('packing requires one shared captured locality file for all locality checks')
    rel = _relative(next(iter(paths)), 'electrical locality')
    if rel not in manifest['files'] or rel in mutable_inputs:
        raise ValueError('electrical locality must be a captured immutable input')
    path = Path(inputs) / rel
    if path.is_symlink() or not path.is_file():
        raise ValueError('captured electrical locality missing or linked')
    data = path.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    if sha256 != manifest['files'][rel]:
        raise ValueError('captured electrical locality changed')
    document = validate(load_json(data))
    if 'electrical_locality' in policy and validate(policy['electrical_locality']) != document:
        raise ValueError('packing electrical locality differs from captured coordinator obligations')
    source = {'path': rel, 'sha256': sha256}
    if 'electrical_locality_binding' in policy and policy['electrical_locality_binding'] != source:
        raise ValueError('packing electrical locality binding differs from captured coordinator input')
    policy['electrical_locality'] = document
    policy['electrical_locality_binding'] = source
    return source


def refuse_policy_sources(policy):
    """Refuse constraint sources and overhangs that a packing policy supplies itself.

    The packing policy is caller-authored, so it can only state geometry. Outline and
    fixed-part classes and overhangs come from a validated captured envelope, and an
    envelope keep-out reaches the board as a native rule area graded board/hard. A
    per-obstacle ``provenance`` or a per-group ``constraint_provenance`` would let a
    caller qualify its own obstacles and regions, so either is refused rather than
    accepted as a source; the unsourced geometry stays advisory (AGENTEE-361/365).
    """
    if 'fixed_overhangs' in policy:
        raise ValueError('fixed_overhangs come only from a captured envelope, never from the packing policy')
    if 'constraint_provenance' in policy:
        raise ValueError('constraint_provenance comes only from a captured envelope, never from the packing policy')
    for name, key, reason in (
            ('obstacles', 'provenance', 'obstacle provenance is never accepted from the packing policy; '
             'a sourced keep-out comes from a captured envelope as a native rule area'),
            ('groups', 'constraint_provenance', 'group constraint_provenance is never accepted from the packing policy; '
             'caller-declared regions and distances stay advisory')):
        items = policy.get(name)
        if isinstance(items, list) and any(isinstance(item, dict) and key in item for item in items):
            raise ValueError(reason)


def binding(declaration, manifest, inputs, mutable_inputs):
    """Coordinator binding of the declaration to one captured candidate's immutable inputs.

    Returns (binding record, bound declaration, policy, protected UUIDs, protected references).
    Every declared UUID, policy reference and envelope-fixed reference must resolve to exactly
    one footprint of the captured source board, or the binding is refused."""
    bound = bind_declaration(declaration, manifest, mutable_inputs)
    inputs = Path(inputs)
    policy = _policy(inputs, bound, manifest)
    locality = bind_locality(policy, manifest, inputs, mutable_inputs) if bound['policy'] is not None else None
    envelopes = envelope_fixed(manifest, inputs, mutable_inputs)
    board = inputs / bound['board']
    if board.is_symlink() or not board.is_file() or digest(board) != manifest['files'][bound['board']]:
        raise ValueError('captured placement board changed')
    refuse_policy_sources(policy)
    provenance = combined_provenance(envelopes)
    if provenance:
        policy['constraint_provenance'] = provenance
    overhangs = sorted((o for e in envelopes for o in e.get('overhangs', [])), key=lambda o: o['reference'])
    if len({o['reference'] for o in overhangs}) != len(overhangs):
        raise ValueError('several envelopes declare a courtyard overhang for the same reference')
    if overhangs:
        policy['fixed_overhangs'] = overhangs
    refs = sorted(set(policy['fixed_refs']) | {r for e in envelopes for r in e['references']})
    protected, protected_refs = protected_footprints(board.read_bytes(), bound['fixed_footprints'], refs)
    record = {'schema': BINDING_SCHEMA, 'declaration_sha256': identity(validate_declaration(declaration)),
              'board': bound['board'], 'schematic': bound['schematic'],
              'policy': None if bound['policy'] is None else {
                  'path': bound['policy'], 'sha256': manifest['files'][bound['policy']]},
              'envelopes': envelopes, 'policy_fixed_refs': sorted(set(policy['fixed_refs']))}
    if locality is not None:
        record['electrical_locality'] = locality
    if 'targeted_plans' in bound:
        record['targeted_plans'] = {'path': bound['targeted_plans'],
                                    'sha256': manifest['files'][bound['targeted_plans']]}
    if bound.get('sublayout') is not None:
        record['sublayout'] = sublayout_binding(bound['sublayout'], manifest, inputs, mutable_inputs)
    if 'blocks' in bound:
        record['blocks'], policy['blocks'] = blocks_binding(bound['blocks'], manifest, inputs,
                                                            request_policy(policy, protected_refs))
    return record, bound, policy, protected, protected_refs


def blocks_binding(section, manifest, inputs, policy):
    """Bind captured block files by digest and their candidates to this request (AGENTEE-439).

    Returns (binding record, placer ``blocks`` request section). ``policy`` is the exact
    request policy the helper receives; every candidate must name it, the captured netlist
    and the captured allocation, so a stale candidate refuses here, before any iteration is
    spent. The placer's own admission still binds the board and derivation."""
    from .block_arrangement import request_section_from_bytes
    from .blocks import digest as canonical_digest, validate_candidate
    from .native_netlist import STAMP_FIELDS, stamp_identity
    files = manifest['files']

    def captured(rel):
        path = Path(inputs) / rel
        data = None if path.is_symlink() or not path.is_file() else path.read_bytes()
        if data is None or hashlib.sha256(data).hexdigest() != files[rel]:
            raise ValueError(f'captured placement block file {rel} changed')
        return data
    netlist = section['netlist']
    captured(netlist)
    try:
        identity_record = stamp_identity(Path(inputs) / netlist)
    except ValueError as exc:
        raise ValueError(f'captured block netlist {netlist} has no stamp-masked identity: {exc}') from None
    if identity_record['sha256'] != files[netlist]:
        raise ValueError(f'captured placement block file {netlist} changed')
    allocation = captured(section['allocation']) if 'allocation' in section else None
    raws = [captured(rel) for rel in section['candidates']]
    request = request_section_from_bytes(allocation, captured(section['arrangement']), raws,
                                         names=section['candidates'])
    expected = {'inventory_sha256': files[netlist], 'policy_sha256': canonical_digest(policy),
                'allocation_sha256': None if allocation is None else files[section['allocation']]}
    candidates = []
    for rel, entry in zip(section['candidates'], request['candidates']):
        document = entry['candidate']
        try:
            validate_candidate(document)
        except ValueError as exc:
            raise ValueError(f'block candidate {rel} is invalid: {exc}') from None
        stale = {key: document['sources'][key] for key in expected if document['sources'][key] != expected[key]}
        if stale:
            raise ValueError(f'block candidate {rel} was not packed for this request: {stale} differ from '
                             f'{ {key: expected[key] for key in stale} }')
        candidates.append({'path': rel, 'sha256': files[rel], 'block_id': document['block_id']})
    if len({c['block_id'] for c in candidates}) != len(candidates):
        raise ValueError('placement blocks name one block in several candidates')
    record = {'netlist': {'path': netlist, 'sha256': files[netlist],
                          'semantic_sha256': identity_record['semantic_sha256'],
                          'stamp_fields': list(STAMP_FIELDS)},
              'allocation': None if allocation is None else {'path': section['allocation'],
                                                             'sha256': files[section['allocation']]},
              'arrangement': {'path': section['arrangement'], 'sha256': files[section['arrangement']]},
              'candidates': candidates, 'request_policy_sha256': expected['policy_sha256']}
    return record, request


def sublayout_binding(sub, manifest, inputs, mutable_inputs):
    """The captured request and every sub-board it names, bound by their captured digests.

    A declared sub-layout must be wholly captured: a missing, mutable, linked or changed
    request or sub-board refuses the binding, never a single group."""
    from .sublayout import read_request
    files = manifest['files']
    path = Path(inputs) / sub['request']
    if path.is_symlink() or not path.is_file() or digest(path) != files[sub['request']]:
        raise ValueError('captured sub-layout request changed')
    try:
        request = read_request(read_json(path))
    except ValueError as exc:
        raise ValueError(f'captured sub-layout request is invalid: {exc}') from None
    groups = []
    for group in request['groups']:
        rel = f"{sub['sub_boards']}/{group['sub_board']}"
        if rel not in files or rel in mutable_inputs:
            raise ValueError(f"sub-board {rel} of group {group['id']} must be a captured immutable input")
        if group.get('sub_board_sha256') not in (None, files[rel]):
            raise ValueError(f"sub-board {rel} differs from the sha256 that group {group['id']} declares")
        groups.append({'id': group['id'], 'sub_board': rel, 'sha256': files[rel]})
    return {'request': {'path': sub['request'], 'sha256': files[sub['request']]},
            'sub_boards': sub['sub_boards'], 'groups': groups}


# Strict structural comparison of complete saved boards.

def _board(data):
    try:
        root = parse_sexpr(data)
    except LibraryError as exc:
        raise ValueError(f'board unparseable: {exc}') from exc
    if root[0] != 'kicad_pcb':
        raise ValueError('board root is not kicad_pcb')
    return root


def _uuid(node):
    found = children(node, 'uuid')
    if len(found) != 1 or len(found[0]) != 2 or not isinstance(found[0][1], str):
        return None
    return found[0][1]


def footprints(root):
    """Footprints by native UUID; refuses missing or duplicate identities."""
    found = {}
    for node in children(root, 'footprint'):
        key = _uuid(node)
        if key is None or key in found:
            raise ValueError('footprint without one unique native UUID')
        found[key] = node
    return found


def _reference(node):
    props = [p for p in children(node, 'property') if len(p) >= 3 and p[1] == 'Reference']
    return props[0][2] if len(props) == 1 and isinstance(props[0][2], str) else None


def _locked(node):
    return 'locked' in node[1:] or ['locked', 'yes'] in node[1:]


def _number(token):
    """Decimal of a bounded plain numeric token, else None; construction is exact."""
    if len(token) > MAX_TOKEN or not NUMBER.match(token):
        return None
    try:
        return Decimal(token)
    except DecimalException:
        return None


def _scaled(number, token):
    """Integer n with number == n / 10**MAX_PLACES exactly; no Decimal context is used."""
    sign, digits, exponent = number.as_tuple()
    coefficient = ''.join(map(str, digits)).rstrip('0')
    if not coefficient:
        return 0
    exponent += len(digits) - len(coefficient)
    if exponent < -MAX_PLACES or len(coefficient) + exponent > MAX_PLACES:
        raise ValueError(f'angle {token} is not exactly representable with at most '
                         f'{MAX_PLACES} digits before and after the point')
    n = int(coefficient) * 10 ** (exponent + MAX_PLACES)
    return -n if sign else n


def _at(node):
    """(x, y, scaled angle, tail) of a direct `at` child; the KiCad angle defaults to zero.

    None when the position is missing or malformed. An angle that cannot be held exactly
    raises ValueError rather than being rounded."""
    found = children(node, 'at')
    if len(found) != 1 or len(found[0]) < 3 or not all(isinstance(t, str) for t in found[0][1:]):
        return None
    tokens = found[0][1:]
    values = [_number(t) for t in tokens[:2]]
    angle, tail = Decimal(0), tokens[2:]
    if tail and _number(tail[0]) is not None:
        angle, tail = _number(tail[0]), tail[1:]
    if not all(v is not None and v.is_finite() for v in (*values, angle)):
        return None
    return values[0], values[1], _scaled(angle, tokens[2] if len(tokens) > 2 else '0'), tuple(tail)


def _turn(angle):
    """Exact scaled angle normalized to [0, FULL_TURN); Python integer modulo never rounds."""
    return angle % FULL_TURN


def _degrees(angle):
    """Exact text of a scaled angle, without exponent or trailing zeros."""
    whole, fraction = divmod(abs(angle), 10 ** MAX_PLACES)
    fraction = str(fraction).rjust(MAX_PLACES, '0').rstrip('0')
    return ('-' if angle < 0 else '') + str(whole) + ('.' + fraction if fraction else '')


def _canonical(value):
    return json.dumps(value, separators=(',', ':'))


def _numeric_text(token):
    """One exact spelling of a plain numeric token (29.0, 29 and 2.9e1 -> 29); other tokens unchanged."""
    number = _number(token)
    if number is None:
        return token
    sign, digits, exponent = number.as_tuple()
    coefficient = ''.join(map(str, digits)).rstrip('0')
    if not coefficient:
        return '0'
    exponent += len(digits) - len(coefficient)
    return str(Decimal((sign, tuple(map(int, coefficient)), exponent)))


def _rule(node, rule):
    """The numeric grammar rule of child node under a parent's rule; None for unknown or exact positions."""
    return rule[1].get(node[0]) if rule and isinstance(node, list) and node and isinstance(node[0], str) else None


def _slots(node, rule):
    """Indexes into node of its numeric arguments under its own rule (see FOOTPRINT_NUMBERS), or None
    when the position is unknown or the argument count unsupported, so the whole subtree stays exact."""
    if rule is None or isinstance(node, str) or not node:
        return None
    if rule[0] is _CONTAINER:
        return frozenset()
    plain = [i for i, c in enumerate(node[1:], 1) if isinstance(c, str)]
    slots = rule[0].get(len(plain))
    return None if slots is None else frozenset(plain[s - 1] for s in slots)


def _numeric_form(node, rule):
    """Comparison-only form of node at grammar position rule: numeric slots take one exact spelling;
    unknown positions and unsupported argument counts keep their whole subtree exact."""
    slots = _slots(node, rule)
    if slots is None:
        return node
    return [node[0], *(_numeric_text(c) if i in slots else
                       c if isinstance(c, str) else _numeric_form(c, _rule(c, rule))
                       for i, c in enumerate(node[1:], 1))]


def _same_tokens(before, after, rule=FOOTPRINT_RULE):
    """Exact token equality, except that numeric slots of a node under the parent rule compare as
    exact decimals (by default, a footprint child)."""
    return before == after or _numeric_form(before, _rule(before, rule)) == _numeric_form(after, _rule(after, rule))


def _first_difference(before, after, path='', rule=None):
    """(path, before spelling, after spelling) of the first differing token, for refusals; rule is
    before's own grammar position, as _numeric_form compares it."""
    if isinstance(before, str) or isinstance(after, str):
        return None if before == after else (path, _canonical(before), _canonical(after))
    slots = _slots(before, rule)
    if slots != _slots(after, rule):
        slots = None
    tag = before[0] if before and isinstance(before[0], str) else ''
    for index, (b, a) in enumerate(zip(before[1:], after[1:]), 1):
        step = b[0] if isinstance(b, list) and b and isinstance(b[0], str) else str(index)
        if slots and index in slots and isinstance(b, str) and isinstance(a, str) and \
                _numeric_text(b) == _numeric_text(a):
            continue
        inner = _rule(b, rule) if slots is not None else None
        found = _first_difference(b, a, f'{path}/{step}' if path else step, inner)
        if found:
            return found
    if len(before) != len(after) or before[:1] != after[:1]:
        return path or tag, f'{len(before) - 1} items', f'{len(after) - 1} items'
    return None


def _label(node):
    key = _uuid(node) if isinstance(node, list) else None
    tag = node[0] if isinstance(node, list) and node else repr(node)
    return f'{tag} {key}' if key else str(tag)


def _sequence_violations(before, after, where, rule=None):
    """Multiset and order comparison of unkeyed items; numeric spellings compare as exact decimals only
    for items at known positions under rule (footprint children), never at board level."""
    removed, added = ([_canonical(_numeric_form(n, _rule(n, rule))) for n in nodes] for nodes in (before, after))
    if removed == added:
        return []
    for item in list(removed):
        if item in added:
            removed.remove(item)
            added.remove(item)
    if not removed and not added:
        return [f'{where}: item order changed']
    labels = {_canonical(_numeric_form(n, _rule(n, rule))): _label(n) for n in (*before, *after)}
    return ([f'{where}: removed or changed {labels[i]}' for i in removed] +
            [f'{where}: added or changed {labels[i]}' for i in added])


def _property_font_defaults(node):
    """Comparison-only form of KiCad 10's omitted property font thickness (0.15 mm).

    Strip only that explicit default, from one unambiguous effects/font path. Other
    children and their order stay exact; the saved bytes and evidence hashes never change.
    """
    # Reference/Value/Datasheet/Description load an omitted thickness as zero and a resave never
    # adds it (only custom fields get the 0.15 default); Footprint is not a saved PCB field.
    if (node[0] != 'property' or len(node) < 3 or not all(isinstance(t, str) for t in node[1:3]) or
            node[1] in ('Reference', 'Value', 'Datasheet', 'Description', 'Footprint')):
        return node
    effects = children(node, 'effects')
    fonts = children(effects[0], 'font') if len(effects) == 1 else []
    if len(fonts) != 1 or children(fonts[0], 'thickness') != [['thickness', '0.15']]:
        return node
    font = [c for c in fonts[0] if c != ['thickness', '0.15']]
    effect = [font if c is fonts[0] else c for c in effects[0]]
    return [effect if c is effects[0] else c for c in node]


def _child_follows(before, after, rotation):
    """Whether a keyed child is unchanged, or a rotated child whose `at` angle advanced exactly
    by the footprint's quarter turn, allowing only the property font thickness default and
    numeric spellings (FOOTPRINT_NUMBERS).
    A malformed or inexact child angle never follows a turn."""
    if before[0] not in ROTATED_CHILDREN or not children(before, 'at'):
        return _same_tokens(before, after)
    if rotation == 0 and _same_tokens(before, after):
        return True
    b_at, a_at = _at(before), _at(after)
    if b_at is None or a_at is None or rotation not in QUARTER_TURNS:
        return False
    before, after = _property_font_defaults(before), _property_font_defaults(after)
    if before[:1] == after[:1] == ['pad'] and len(before) > 3 and before[3] == 'custom' and before != after:
        from .footprint_library import canonical_custom_pad
        try:
            before, after = canonical_custom_pad(before), canonical_custom_pad(after)
        except LibraryError:
            pass  # Unsupported pads keep the exact pre-existing token comparison.
    rest = [[c for c in node if not (isinstance(c, list) and c and c[0] == 'at')] for node in (before, after)]
    return (_same_tokens(*rest) and b_at[:2] == a_at[:2] and b_at[3] == a_at[3] and
            _turn(a_at[2] - b_at[2] - rotation) == 0)


def _silk_field_follows(before, after, rotation, names):
    """Position-only silk relocation; retain every other saved field token."""
    if (before[0] != 'property' or len(before) < 3 or before[1] not in names or
            before[:3] != after[:3] or
            children(before, 'layer') not in ([['layer', 'F.SilkS']], [['layer', 'B.SilkS']])):
        return False
    if any(n[1:] in ([], ['yes']) for n in children(before, 'hide') +
           [n for e in children(before, 'effects') for n in children(e, 'hide')]):
        return False
    b_at, a_at = _at(before), _at(after)
    if b_at is None or a_at is None:
        return False
    # Reuse the ordinary rigid-field validator with only the position restored.
    adjusted = [(['at', *children(before, 'at')[0][1:3], *c[3:]]
                 if isinstance(c, list) and c[:1] == ['at'] else c) for c in after]
    return _child_follows(before, adjusted, rotation)


def _field_follows(before, after):
    """A reused group member's Reference or Value field may take the sub-board's position, angle,
    layer and visibility; the fresh pcbnew read then requires exactly the planned values."""
    if before[0] != 'property' or len(before) < 3 or before[1] not in ('Reference', 'Value') or \
            before[:3] != after[:3]:
        return False
    free = ('at', 'layer', 'hide')
    return ([c for c in before if not (isinstance(c, list) and c and c[0] in free)] ==
            [c for c in after if not (isinstance(c, list) and c and c[0] in free)])


def _flip_layer(name):
    return FLIP_LAYERS.get(name, name if name in SIDE_NEUTRAL_LAYERS else None)


def _same_number(a, b):
    return isinstance(a, str) and isinstance(b, str) and _number(a) is not None and _number(a) == _number(b)


def _opposite_number(a, b):
    # copy_negate is exact; unary minus would round to the current Decimal context.
    return isinstance(a, str) and isinstance(b, str) and _number(a) is not None and _number(b) is not None and \
        _number(a) == _number(b).copy_negate()


def _uses(node, tags):
    return isinstance(node, list) and bool(node) and (node[0] in tags or any(_uses(c, tags) for c in node[1:]))


def _mirrored(before, after, rule):
    """Whether after is before with every layer swapped and every point mirrored in y, exactly.
    rule is before's own grammar position (FOOTPRINT_NUMBERS). Layers and points are interpreted only
    at known positions; an unknown subtree must be identical and hold nothing a flip transforms."""
    if isinstance(before, str) or isinstance(after, str):
        return before == after
    slots = _slots(before, rule)
    if slots is None:
        return before == after and not _uses(before, FLIP_GEOMETRY)
    if len(before) != len(after) or not before or before[0] != after[0] or before[0] == 'at':
        return False
    tag = before[0]
    if tag == 'layer':
        return len(before) == 2 and _flip_layer(before[1]) == after[1]
    if tag == 'layers':
        return all(isinstance(b, str) and _flip_layer(b) == a for b, a in zip(before[1:], after[1:]))
    if tag in POINT_TAGS:
        return len(before) == 3 and _same_number(before[1], after[1]) and _opposite_number(before[2], after[2])
    return all(_numeric_text(b) == _numeric_text(a) if i in slots and isinstance(b, str) and isinstance(a, str)
               else _mirrored(b, a, _rule(b, rule)) for i, (b, a) in enumerate(zip(before[1:], after[1:]), 1))


def _mirrored_at(before, after):
    """(before angle, after angle) when the local x/y are mirrored exactly, else None."""
    b_at, a_at = _at(before), _at(after)
    if b_at is None or a_at is None or b_at[3] or a_at[3] or b_at[0] != a_at[0] or b_at[1] != a_at[1].copy_negate():
        return None
    return b_at[2], a_at[2]


def _justify(effects):
    found = children(effects, 'justify')
    if not found:
        return set()
    tokens = found[0][1:]
    if len(found) != 1 or any(t not in JUSTIFY for t in tokens) or len(set(tokens)) != len(tokens):
        return None
    return set(tokens)


def _flip_text(before, after, turn):
    before, after = _property_font_defaults(before), _property_font_defaults(after)
    angles = _mirrored_at(before, after)
    rest = [[c for c in node if not (isinstance(c, list) and c and c[0] in ('at', 'effects'))]
            for node in (before, after)]
    effects = [children(node, 'effects') for node in (before, after)]
    if angles is None or not _mirrored(*rest, _rule(before, FOOTPRINT_RULE)) or len(effects[0]) != 1 or len(effects[1]) != 1:
        return False
    plain = [[c for c in e[0][1:] if not (isinstance(c, list) and c and c[0] == 'justify')] for e in effects]
    old, new = _justify(effects[0][0]), _justify(effects[1][0])
    # Vertical justification under a flip is not qualified.
    if not _same_tokens(*(['effects', *p] for p in plain), _TEXT) or old is None or new is None or old & {'top', 'bottom'}:
        return False
    mirrored = old ^ {'mirror'}
    expected = _turn(HALF_TURN - angles[0] + turn)
    # KiCad may also keep the flipped text upright: a further half turn with left/right swapped.
    return ((_turn(angles[1] - expected) == 0 and new == mirrored) or
            (_turn(angles[1] - expected - HALF_TURN) == 0 and new == {H_SWAP.get(t, t) for t in mirrored}))


def _flip_pad(before, after, theta, theta_after):
    if len(before) < 4 or before[1:4] != after[1:4] or before[3] not in FLIP_PAD_SHAPES:
        return False
    angles = _mirrored_at(before, after)
    rest = [[c for c in node if not (isinstance(c, list) and c and c[0] == 'at')] for node in (before, after)]
    # The saved pad angle is footprint plus local angle; a flip negates the local angle.
    return (angles is not None and _mirrored(*rest, _rule(before, FOOTPRINT_RULE)) and
            _turn(angles[1] - theta_after + angles[0] - theta) == 0)


def _flip_unsupported(before, after):
    """Whether a keyed child lies outside the qualified side-flip family."""
    if before[0] not in (*FLIP_GRAPHICS, *FLIP_TEXTS, 'pad') or any(_uses(n, FLIP_UNSUPPORTED) for n in (before, after)):
        return True
    return before[0] in FLIP_TEXTS and any((_justify(e) or set()) & {'top', 'bottom'}
                                            for e in children(before, 'effects'))


def _flip_follows(before, after, theta, theta_after):
    """Whether a keyed child is exactly the side-flipped form of before."""
    if _flip_unsupported(before, after):
        return False
    if before[0] in FLIP_GRAPHICS:
        return _mirrored(before, after, _rule(before, FOOTPRINT_RULE))
    if before[0] in FLIP_TEXTS:
        return _flip_text(before, after, _turn(theta_after + theta))
    if before[0] == 'pad':
        return _flip_pad(before, after, theta, theta_after)
    return False


def _zone_layer_flips(root):
    """Copper layer pairing from the captured board, including inner layers."""
    result = dict(FLIP_LAYERS)
    tables = children(root, 'layers')
    if len(tables) == 1:
        names = [layer[1] for layer in tables[0][1:] if isinstance(layer, list) and len(layer) >= 3
                 and isinstance(layer[1], str) and layer[1].endswith('.Cu')]
        inner = [f'In{i}.Cu' for i in range(1, len(names)-1)]
        if (2 <= len(names) <= 32 and len(names) % 2 == 0 and len(set(names)) == len(names) and
                set(names) == {'F.Cu', 'B.Cu', *inner}):
            result.update(zip(inner, reversed(inner)))
    return result


def _zone_follows(before, after, b_at, a_at, flipped, layer_flips):
    """Owned zone polygons are saved in board coordinates, unlike footprint graphics.

    Compare every vertex under the owner's rigid motion; all other zone tokens
    stay exact. Side flips of zones require quarter-turn owner orientations.
    """
    rotation = _turn(a_at[2] + b_at[2] if flipped else a_at[2] - b_at[2])
    if rotation not in QUARTER_TURNS or (flipped and b_at[2] % QUARTER_TURNS[1]):
        return False
    if (flipped or b_at[:2] != a_at[:2] or rotation) and _uses(before, ('filled_polygon', 'fill_segments')):
        return False
    def point(node):
        if len(node) != 3 or node[0] != 'xy' or any(not isinstance(t, str) for t in node[1:]):
            raise ValueError('zone polygon needs straight xy vertices')
        numbers = [_number(t) for t in node[1:]]
        if any(n is None for n in numbers):
            raise ValueError('zone polygon has a malformed coordinate')
        return [_scaled(n, t) for n, t in zip(numbers, node[1:])]
    origin = [[_scaled(n, str(n)) for n in at[:2]] for at in (b_at, a_at)]
    def follows(b, a):
        p, q = point(b), point(a)
        p = [p[i] - origin[0][i] for i in (0, 1)]
        if flipped:
            x, y = rotate(p, -b_at[2] // QUARTER_TURNS[1])
            p = rotate((x, -y), a_at[2] // QUARTER_TURNS[1])
        else:
            p = rotate(p, rotation // QUARTER_TURNS[1])
        return all(p[i] + origin[1][i] == q[i] for i in (0, 1))
    if len(before) != len(after):
        return False
    polygons = 0
    for b, a in zip(before, after):
        if isinstance(b, list) and b and b[0] == 'polygon':
            polygons += 1
            if (not isinstance(a, list) or len(b) != 2 or len(a) != 2 or a[0] != 'polygon' or
                    not isinstance(b[1], list) or not isinstance(a[1], list) or
                    b[1][:1] != ['pts'] or a[1][:1] != ['pts'] or
                    len(b[1]) < 4 or len(b[1]) != len(a[1]) or
                    not all(follows(p, q) for p, q in zip(b[1][1:], a[1][1:]))):
                return False
        elif flipped and isinstance(b, list) and b and b[0] in ('layer', 'layers'):
            def flip_layer(name):
                return layer_flips.get(name, _flip_layer(name))
            if (not isinstance(a, list) or a[:1] != b[:1] or len(b) != len(a) or
                    any(not isinstance(t, str) or flip_layer(t) is None for t in b[1:]) or
                    any(not isinstance(t, str) for t in a[1:]) or
                    sorted(flip_layer(t) for t in b[1:]) != sorted(a[1:])):
                return False
        elif not _same_tokens(b, a, FOOTPRINT_NUMBERS['zone']):
            return False
    return polygons > 0


def _footprint(key, before, after, protected, violations, fields=False, zone_layer_flips=None, native_flips=None, silk_moves=None):
    label = f'footprint {key} ({_reference(before) or "no reference"})'
    count = len(violations)
    if [t for t in before[1:] if isinstance(t, str)] != [t for t in after[1:] if isinstance(t, str)]:
        violations.append(f'{label}: library identity or header changed')
    sides = children(before, 'layer'), children(after, 'layer')
    flipped = sides[0] != sides[1]
    if flipped and sorted(map(_canonical, sides)) != [_canonical([['layer', 'B.Cu']]), _canonical([['layer', 'F.Cu']])]:
        violations.append(f'{label}: layer changed other than a front/back side flip')
    try:
        b_at, a_at = _at(before), _at(after)
    except ValueError as exc:
        violations.append(f'{label}: footprint {exc}')
        return None
    if b_at is None or a_at is None:
        violations.append(f'{label}: missing or malformed footprint position')
        return None
    # A flip negates the saved footprint angle; what remains must be a quarter turn.
    rotation = _turn(a_at[2] + b_at[2]) if flipped else _turn(a_at[2] - b_at[2])
    moved = b_at[:2] != a_at[:2] or rotation != 0 or flipped
    if b_at[3] != a_at[3]:
        violations.append(f'{label}: position tokens changed')
    if key in protected and (flipped or children(before, 'at') != children(after, 'at')):
        violations.append(f'{label}: protected footprint transform changed')
    if rotation not in QUARTER_TURNS:
        violations.append(f'{label}: rotation delta {_degrees(rotation)}{" after the side flip" if flipped else ""} '
                          'is not a quarter turn')
    skip = ('at', 'layer') if flipped else ('at',)
    unkeyed = [[c for c in node[1:] if isinstance(c, list) and c and c[0] not in skip and _uuid(c) is None]
               for node in (before, after)]
    violations += _sequence_violations(*unkeyed, label, FOOTPRINT_RULE)
    keyed = []
    for node in (before, after):
        items = {}
        for child in node[1:]:
            if isinstance(child, list) and child and _uuid(child) is not None:
                if (child[0], _uuid(child)) in items:
                    violations.append(f'{label}: duplicate child identity {_label(child)}')
                items[child[0], _uuid(child)] = child
        keyed.append(items)
    for item in sorted(keyed[0].keys() ^ keyed[1].keys()):
        violations.append(f'{label}: {"removed" if item in keyed[0] else "added"} {item[0]} {item[1]}')
    from .silk_artwork import DECLARATION, movable_names
    declarations = [p[2] for p in children(before, 'property') if len(p) >= 3 and p[1] == DECLARATION]
    try:
        silk_names = movable_names(declarations[0] if len(declarations) == 1 else None)
        if len(declarations) > 1:
            raise ValueError('duplicate movable silkscreen declaration')
    except ValueError as exc:
        violations.append(f'{label}: {exc}')
        silk_names = frozenset()
    for item in sorted(keyed[0].keys() & keyed[1].keys()):
        b_child, a_child = keyed[0][item], keyed[1][item]
        if flipped and (native_flips or {}).get(item) == a_child:
            continue
        if fields and _field_follows(b_child, a_child):
            continue
        try:
            if silk_moves is not None and not flipped and _silk_field_follows(b_child, a_child, rotation, silk_names):
                if _at(b_child)[:2] != _at(a_child)[:2] and silk_moves is not None:
                    silk_moves.append({'footprint': key, 'field': item[1], 'name': b_child[1]})
                continue
            if b_child[0] == 'zone':
                if _zone_follows(b_child, a_child, b_at, a_at, flipped, zone_layer_flips or FLIP_LAYERS):
                    continue
                violations.append(f'{label}: zone {item[1]} changed beyond the footprint transform')
                continue
            if (_flip_follows(b_child, a_child, b_at[2], a_at[2]) if flipped else
                    _child_follows(b_child, a_child, rotation)):
                continue
        except ValueError as exc:
            violations.append(f'{label}: {item[0]} {item[1]} {exc}')
            continue
        if flipped and _flip_unsupported(b_child, a_child):
            violations.append(f'{label}: {item[0]} {item[1]} is not supported under a side flip')
        else:
            # Name the first differing token, except under a turn or flip where it may be a transformed one.
            found = _first_difference(b_child, a_child, b_child[0], _rule(b_child, FOOTPRINT_RULE))
            hint = (f' (first differing token {found[0]}: {found[1]} -> {found[2]})'
                    if found and not flipped and not rotation else '')
            violations.append(f'{label}: {item[0]} {item[1]} changed beyond the footprint transform{hint}')
    if len(violations) != count or not moved:
        return None
    return {'uuid': key, 'reference': _reference(before), 'rotation_deg': rotation // 10 ** MAX_PLACES,
            'flipped': flipped, 'side': 'back' if sides[1] == [['layer', 'B.Cu']] else 'front',
            'from': [str(b_at[0]), str(b_at[1]), _degrees(b_at[2])],
            'to': [str(a_at[0]), str(a_at[1]), _degrees(a_at[2])]}


def native_custom_flip_probe():
    """Sandbox entry: derive the native reflection from captured source, never writer geometry."""
    import pcbnew as k
    from .placement_probe import extract
    from .symbol_library import nanometres
    source, target = Path('/candidate/before.kicad_pcb'), Path('/candidate/after.kicad_pcb')
    selected = read_json('/candidate/selected.json')
    before = {f['uuid']: f for f in extract(source)['footprints']}
    after = {f['uuid']: f for f in extract(target)['footprints']}
    targets = footprints(_board(target.read_bytes()))
    board = k.LoadBoard(str(source))
    for fp in board.GetFootprints():
        key = fp.m_Uuid.AsString()
        if key not in selected:
            continue
        pads = [p for p in before[key]['pads'] if p['shape'] == int(k.PAD_SHAPE_CUSTOM)]
        if not pads or any(p['custom_geometry']['status'] != 'proven' for p in pads):
            raise ValueError('source custom pads are not native qualified')
        at = children(targets[key], 'at')[0]
        theta = _at(targets[key])
        if theta is None:
            raise ValueError('malformed target footprint transform')
        fp.Flip(fp.GetPosition(), k.FLIP_DIRECTION_TOP_BOTTOM)
        fp.SetOrientationDegrees(float(_degrees(theta[2])))
        fp.SetPosition(k.VECTOR2I(nanometres(at[1]), nanometres(at[2])))
    expected = Path('/work/expected.kicad_pcb')
    k.SaveBoard(str(expected), board)
    facts = {f['uuid']: f for f in extract(expected)['footprints']}
    nodes = footprints(_board(expected.read_bytes()))
    admitted = {}
    for key in selected:
        # The complete native pad records include identity, net, layers, anchor,
        # primitives, merged copper contours and board-space copper extent.
        if facts[key]['pads'] != after[key]['pads']:
            raise ValueError(f'{key}: reflected native pad geometry differs')
        admitted[key] = [n for n in nodes[key][1:] if isinstance(n, list) and n and
                         (n[0] == 'fp_arc' or (n[0] == 'pad' and len(n) > 3 and n[3] == 'custom'))]
    write_json('/work/admitted.json', admitted)


def _native_custom_flips(before, after, selected):
    """Use the existing networkless executor; missing native evidence refuses admission."""
    from .executor import execute
    with tempfile.TemporaryDirectory(prefix='agent-ee-custom-flip-') as tmp:
        root = Path(tmp)
        inputs = root / 'inputs'
        inputs.mkdir()
        (inputs / 'before.kicad_pcb').write_bytes(before)
        (inputs / 'after.kicad_pcb').write_bytes(after)
        write_json(inputs / 'selected.json', selected)
        work = root / 'work'
        record = execute(inputs, work, ['/usr/bin/python3', '-B', '-c',
            'from agent_ee.placement_adapter import native_custom_flip_probe; native_custom_flip_probe()'],
            timeout=60)
        if record['execution'] != 'ok':
            lines = (work / 'console.log').read_text(errors='replace').splitlines()
            # SWIG shutdown warnings can bury the actual native refusal.
            errors = [line for line in lines if line.startswith('ValueError:')]
            detail = errors[-1][:2000] if errors else '\n'.join(lines)[-2000:]
            raise ValueError('reflection probe failed: ' + detail)
        try:
            admitted = read_json(work / 'admitted.json')
        except (OSError, ValueError) as exc:
            raise ValueError('reflection probe output missing or invalid') from exc
        if not isinstance(admitted, dict) or set(admitted) != set(selected):
            raise ValueError('reflection probe output does not cover selected footprints')
        return {key: {(n[0], _uuid(n)): n for n in nodes} for key, nodes in admitted.items()}


def write_scope(before, after, protected=(), groups=None):
    """Compare saved boards for rigid footprint transforms and permitted silk field positions.

    `groups` are admissible sub-layout plans (AGENTEE-129). A board-level node is then also
    allowed when it is new and carries the UUID and kind of one planned copy or group node;
    the report names every group with such a node, and the caller must verify those groups
    exactly. Their members' Reference and Value fields may follow the sub-board."""
    protected = sorted(set(protected))
    admitted, tokens = {}, {}
    for group in groups or ():
        plan = group['plan']
        admitted[plan['uuid']] = (group['id'], 'group')
        admitted.update({c['uuid']: (group['id'], c['expected']['kind']) for c in plan['copy']})
        tokens.update(group['tokens'])
    present = set()
    report = {'schema': SCOPE_SCHEMA, 'family': TRANSFORM_FAMILY, 'before_sha256': hashlib.sha256(before).hexdigest(),
              'after_sha256': hashlib.sha256(after).hexdigest(), 'protected': protected,
              'transforms': [], 'violations': []}
    violations = report['violations']
    silk_moves = []
    try:
        roots = _board(before), _board(after)
        prints = footprints(roots[0]), footprints(roots[1])
    except ValueError as exc:
        violations.append(str(exc))
    else:
        others = [[n for n in root[1:] if not (isinstance(n, list) and n and n[0] == 'footprint')]
                  for root in roots]
        from .placement_defects import stamp_violations, unstamped
        try:
            (others[0], stamped), (others[1], restamped) = unstamped(others[0]), unstamped(others[1])
        except ValueError as exc:
            violations.append(str(exc))
        else:
            # The writer records carry lineage on the board (AGENTEE-351); nothing else in the title block.
            violations += stamp_violations(stamped, restamped)
        if admitted:
            known = {_uuid(n) for n in others[0] if isinstance(n, list)}
            seen, kept = set(), []
            for node in others[1]:
                key = _uuid(node) if isinstance(node, list) and node else None
                hit = admitted.get(key) if key not in known else None
                if hit and key in seen:
                    # pcbnew keeps both nodes, and a fresh read would see only one of them.
                    violations.append(f'group sublayout:{hit[0]}: {node[0]} {key} repeats an admitted copy UUID')
                elif hit and hit[1] == ('group' if node[0] == 'group' else GROUP_TAGS.get(node[0])):
                    seen.add(key)
                    present.add(hit[0])
                    if key in tokens and _copy_tokens(node) != tokens[key]:
                        violations.append(f'group sublayout:{hit[0]}: {node[0]} {key} changed beyond the '
                                          'sub-board copy (a token other than geometry or net differs)')
                else:
                    kept.append(node)
            others[1] = kept
        members = {fp['uuid'] for group in groups or () if group['id'] in present for fp in group['plan']['footprints']}
        violations += _sequence_violations(*others, 'board')
        violations += [f'protected footprint {key} absent from source board'
                       for key in protected if key not in prints[0]]
        violations += [f'footprint {key} removed' for key in sorted(prints[0].keys() - prints[1].keys())]
        violations += [f'footprint {key} added' for key in sorted(prints[1].keys() - prints[0].keys())]
        native_flips = {}
        custom_flips = [key for key in prints[0].keys() & prints[1].keys()
                        if children(prints[0][key], 'layer') != children(prints[1][key], 'layer')
                        and any(len(p) > 3 and p[3] == 'custom' for p in children(prints[0][key], 'pad'))]
        if custom_flips:
            try:
                native_flips = _native_custom_flips(before, after, custom_flips)
            except (ValueError, OSError) as exc:
                violations.append(f'native custom-pad side flip: {exc}')
        zone_layer_flips = _zone_layer_flips(roots[0])
        # A silk relocation cannot absorb edits beside unadmitted board copper.
        admitted_silk_moves = silk_moves if not violations else None
        for key in sorted(prints[0].keys() & prints[1].keys()):
            moved = _footprint(key, prints[0][key], prints[1][key], protected, violations, key in members,
                               zone_layer_flips, native_flips.get(key), admitted_silk_moves)
            if moved:
                report['transforms'].append(moved)
    if silk_moves:
        report.update(family='agent-ee.placement-transforms/4', silkscreen_relocations=silk_moves)
    if groups is not None:
        report['reused_groups'] = sorted(present)
    report['status'] = 'refused' if violations else 'allowed'
    return report


def _copy_tokens(node):
    return [c for c in node if not (isinstance(c, list) and c and c[0] in COPY_GEOMETRY)]


def _source_tokens(sources, key):
    if key not in sources:
        raise ValueError(f'sub-board item {key} that pcbnew reports is absent from the saved sub-board')
    return _copy_tokens(sources[key])


def group_admission(source_inputs, bound):
    """Coordinator plan of the declared sub-layout over the captured source board, or None.

    Planned by the unchanged AGENTEE-56 planner from fresh sandboxed pcbnew facts of the
    source board and of each captured sub-board. A group whose members are not all new, or
    that the planner refuses for any other reason, admits no copper."""
    sub = bound.get('sublayout')
    if sub is None:
        return None
    from . import sublayout
    source_inputs = Path(source_inputs)
    request = sublayout.read_request(read_json(source_inputs / sub['request']))
    root = source_inputs / sub['sub_boards']
    groups = []
    with tempfile.TemporaryDirectory(prefix='agent-ee-group-copper-') as tmp:
        top = sublayout.board_facts(source_inputs / bound['board'], Path(tmp) / 'top')
        for index, group in enumerate(request['groups']):
            record, error = sublayout._sub_board(root, group)
            entry = {'id': group['id'], 'name': f"sublayout:{group['id']}", 'sub_board': record}
            if error is not None:
                entry['findings'] = [{'code': error.code, 'message': str(error)}]
            else:
                data = (root / group['sub_board']).read_bytes()
                if hashlib.sha256(data).hexdigest() != record['sha256']:
                    raise ValueError(f"sub-board {group['sub_board']} changed during admission")
                facts = sublayout.board_facts(root / group['sub_board'], Path(tmp) / f'sub-{index}', record['sha256'])
                plan = sublayout.plan_group(group, top, facts, record['sha256'])
                if plan['status'] == 'apply':
                    sources = {_uuid(n): n for n in _board(data)[1:] if isinstance(n, list) and n}
                    entry.update(plan=plan, region_nm=sublayout.module_region(plan, facts),
                                 tokens={c['uuid']: _source_tokens(sources, c['source_uuid']) for c in plan['copy']})
                else:
                    entry['findings'] = [{'code': f['code'], 'message': f['message']} for f in plan['findings']]
            groups.append(entry)
    return {'request_sha256': digest(source_inputs / sub['request']), 'top': top, 'groups': groups}


def _inside(box, region):
    return region[0] <= box[0] and region[1] <= box[1] and box[2] <= region[2] and box[3] <= region[3]


def _group_errors(group, old, after, fills):
    """Why the saved board's copy of an admitted group is not exactly its plan; empty when exact.

    `fills` holds the saved and coordinator-refilled fill of each copied zone. Fill geometry is
    not part of the plan, so a saved fill is admitted only when it is exactly that refill."""
    from .sublayout import footprint_error
    plan, region = group['plan'], group['region_nm']
    items = {i['uuid']: i for i in after['items']}
    prints = {fp['uuid']: fp for fp in after['footprints']}
    mapped = {e['top_net'] for e in plan['net_map'] if e['status'] == 'mapped'}
    errors = []
    for copy_ in plan['copy']:
        kind, key = copy_['expected']['kind'], copy_['uuid']
        got = items.get(key)
        if got is None:
            errors.append(f'{kind} {key} missing; a group is admitted whole or not at all')
            continue
        if {k: v for k, v in got.items() if k not in ('bbox_nm', 'filled')} != copy_['expected']:
            errors.append(f'{kind} {key} differs from the captured sub-board transform or the voted net')
        if got['net'] and got['net'] not in mapped:
            errors.append(f"{kind} {key} net {got['net']} is not a voted net of the group")
        if not _inside(got['bbox_nm'], region):
            errors.append(f'{kind} {key} lies outside the group region {region}')
        if kind == 'ZONE':
            fill = [f for f in fills if f['uuid'] == key]
            if len(fill) != 1 or fill[0]['saved'] != fill[0]['refilled']:
                errors.append(f'ZONE {key} saved fill is not exactly a coordinator refill of the delivered board '
                              '(refill the copied zones after the last change near them)')
    for want in plan['footprints']:
        fp = prints.get(want['uuid'])
        error = f"{want['top_ref']} absent" if fp is None else footprint_error(want, fp, old[want['uuid']])
        if error:
            errors.append(error)
    members = {fp['uuid'] for fp in plan['footprints']} | {c['uuid'] for c in plan['copy']}
    saved = {g['uuid']: g for g in after['groups']}.get(plan['uuid'])
    if saved is None or saved['name'] != plan['name'] or set(saved['members']) != members:
        errors.append('group membership differs from the plan')
    return errors


def copper_scope(copper, board, reused, sha256=None):
    """The group-copper part of a scope report, from fresh sandboxed pcbnew reads of `board`.

    `sha256` binds those reads to the board bytes the token comparison saw."""
    after, fills, violations = None, [], []
    if reused:
        from .sublayout import board_facts, zone_fills
        zones = sorted(c['uuid'] for g in copper['groups'] if g['id'] in reused
                       for c in g['plan']['copy'] if c['expected']['kind'] == 'ZONE')
        with tempfile.TemporaryDirectory(prefix='agent-ee-group-copper-') as tmp:
            after = board_facts(board, Path(tmp) / 'after', sha256)
            if zones:
                fills = zone_fills(board, Path(tmp) / 'fills', zones, sha256)
        # The token comparison already refuses these; a fresh read must agree with it.
        planned = {c['uuid'] for g in copper['groups'] if g['id'] in reused for c in g['plan']['copy']}
        source = {i['uuid'] for i in copper['top']['items']}
        keys = [i['uuid'] for i in after['items']]
        violations += [f'board item {key} appears {keys.count(key)} times' for key in sorted(set(keys))
                       if keys.count(key) > 1]
        violations += [f'board item {key} is new but no planned copy of a reused group'
                       for key in sorted(set(keys) - source - planned)]
    old = {fp['uuid']: fp for fp in copper['top']['footprints']}
    groups = []
    for group in copper['groups']:
        entry = {'id': group['id'], 'name': group['name'], 'sub_board': group['sub_board']}
        if 'plan' not in group:
            entry.update(status='not_admissible', findings=group['findings'])
        elif group['id'] not in reused:
            entry['status'] = 'absent'
        else:
            plan = group['plan']
            errors = _group_errors(group, old, after, fills)
            violations += [f"group {group['name']}: {e}" for e in errors]
            kinds = {}
            for copy_ in plan['copy']:
                kinds[copy_['expected']['kind']] = kinds.get(copy_['expected']['kind'], 0) + 1
            copied = {c['uuid'] for c in plan['copy']}
            entry.update(status='refused' if errors else 'reused', uuid=plan['uuid'], region_nm=group['region_nm'],
                         rotation_deg=plan['rotation_deg'], anchor_at_nm=plan['anchor_at_nm'],
                         members=sorted(fp['top_ref'] for fp in plan['footprints']),
                         nets={e['sub_net']: e['top_net'] for e in plan['net_map'] if e['status'] == 'mapped'},
                         copied=dict(sorted(kinds.items())), items=sorted(copied),
                         zone_fills={f['uuid']: identity(f['refilled']) for f in fills if f['uuid'] in copied},
                         withheld=plan['withheld'])
        groups.append(entry)
    return {'schema': COPPER_SCHEMA, 'request_sha256': copper['request_sha256'], 'groups': groups}, violations


def protected_footprints(board, fixed_footprints, fixed_refs=()):
    """Declared fixed UUIDs, policy- and envelope-fixed references and locked parts on the source board."""
    prints = footprints(_board(board))
    by_ref = {}
    for key, node in prints.items():
        by_ref.setdefault(_reference(node), []).append(key)
    missing = sorted(set(fixed_footprints) - prints.keys())
    if missing:
        raise ValueError(f'declared fixed footprints absent from source board: {missing}')
    uuids = set(fixed_footprints) | {k for k, node in prints.items() if _locked(node)}
    for ref in fixed_refs:
        if len(by_ref.get(ref, [])) != 1:
            raise ValueError(f'fixed reference {ref!r} does not name exactly one source footprint')
        uuids.update(by_ref[ref])
    refs = [_reference(prints[k]) for k in uuids]
    if None in refs or len(set(refs)) != len(refs):
        raise ValueError('protected footprints need unique native references')
    return sorted(uuids), sorted(refs)


def _files(root):
    files = {}
    for path in sorted(Path(root).rglob('*')):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError(f'design contains a link or special file: {path.relative_to(root)}')
        if path.is_file():
            files[path.relative_to(root).as_posix()] = digest(path)
    return files


def proposal_scope(source_inputs, design, board, protected, copper=None):
    """Placement-iteration write scope of a delivered design against its source candidate.

    `copper` is the group_admission of a declared sub-layout; the report then carries its
    group-copper section, and any inexact reused group is a violation."""
    source_inputs, design = Path(source_inputs), Path(design)
    before, after = _files(source_inputs), _files(design)
    violations = [f'non-board file {"removed" if rel in before else "added"}: {rel}'
                  for rel in sorted(before.keys() ^ after.keys())]
    violations += [f'non-board file changed: {rel}' for rel in sorted(before.keys() & after.keys())
                   if rel != board and before[rel] != after[rel]]
    if board not in before or board not in after:
        violations.append(f'board missing: {board}')
        report = {'schema': SCOPE_SCHEMA, 'family': TRANSFORM_FAMILY, 'status': 'refused', 'protected': sorted(protected),
                  'transforms': [], 'violations': []}
    else:
        report = write_scope((source_inputs / board).read_bytes(), (design / board).read_bytes(), protected,
                             None if copper is None else [g for g in copper['groups'] if 'plan' in g])
    if copper is not None:
        reused = report.pop('reused_groups', [])
        report['copper'], errors = copper_scope(copper, design / board, reused, report.get('after_sha256'))
        violations += errors
    report['violations'] = violations + report['violations']
    report.update(board=board, status='refused' if report['violations'] else 'allowed')
    return report


# Coordinator-run helper step (AGENTEE-95 stage receipt).

def request_policy(policy, protected_refs):
    """The exact packing policy a helper request carries; block candidates bind its digest."""
    policy = copy.deepcopy(policy)
    if not isinstance(policy, dict) or not isinstance(policy.get('fixed_refs'), list):
        raise ValueError('placement policy must be an agent-ee.packing-policy/1 object')
    policy['fixed_refs'] += [r for r in protected_refs if r not in policy['fixed_refs']]
    policy.pop('targeted_plans', None)
    policy.pop('blocks', None)
    return policy


def helper_request(board, policy, protected_refs):
    """Coordinator-derived placer request bound to the exact delivered board bytes."""
    if not isinstance(policy, dict):
        raise ValueError('placement policy must be an agent-ee.packing-policy/1 object')
    targeted = {key: copy.deepcopy(policy[key]) for key in ('targeted_plans', 'blocks') if key in policy}
    return {**targeted, 'policy': request_policy(policy, protected_refs), 'authorization': {
        'schema': 'agent-ee.placement-write/1', 'single_design_writer': True,
        'source_sha256': hashlib.sha256(board).hexdigest(), 'writable_root': ROOT}}


def tool_snapshot(destination):
    """Copy the pinned agent_ee files into a coordinator-owned tree the step mounts read-only.

    The files are exactly the campaign pin (cli.policy_files): the sources and the package
    data they read, such as the captured JLCPCB locality record (AGENTEE-439)."""
    from .cli import policy_files
    destination = Path(destination)
    package = destination / 'agent_ee'
    package.mkdir(parents=True)
    root = Path(__file__).resolve().parent
    for name in policy_files():
        target = package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / name).read_bytes())
    (destination / 'launch.py').write_text(LAUNCHER)
    return destination


def verify_snapshot(tool_dir, source_files):
    """Refuse a helper source snapshot that is not exactly the campaign's pinned agent_ee files.

    The pin covers package data too, so a snapshot missing a data file is refused."""
    expected = {f'agent_ee/{name}': sha for name, sha in source_files.items()}
    expected['launch.py'] = hashlib.sha256(LAUNCHER.encode()).hexdigest()
    if _files(tool_dir) != dict(sorted(expected.items())):
        raise ValueError('placement helper source snapshot differs from the campaign source pin')


def step_declaration(step_dir, bound):
    """The one placement stage-step declaration for a coordinator step directory."""
    step_dir = Path(step_dir)
    # A blocks request names its captured netlist and digest; other declarations keep their argv.
    netlist = (['--netlist', f"/candidate/{bound['blocks']['netlist']}",
                '--netlist-sha256', bound['blocks']['netlist_sha256']] if 'blocks' in bound else [])
    return {'schema': 'agent-ee.stage-step/1', 'stage': 'placement', 'step': STEP,
            'argv': ['/usr/bin/python3', '-B', '-I', f'{TOOL_AT}/launch.py',
                     f"/candidate/{bound['board']}", f"/candidate/{bound['schematic']}",
                     '--request', f'{REQUEST_AT}/request.json', '--root', ROOT, *netlist],
            'mounts': [{'source': str(step_dir / 'tool'), 'at': TOOL_AT},
                       {'source': str(step_dir / 'request'), 'at': REQUEST_AT}],
            'version_argv': ['/usr/bin/kicad-cli', 'version'], 'source': TOOL_AT,
            'writes': {bound['board']: OUTPUT}, 'artifacts': ['placement'],
            'adoptable_exits': [0], 'timeout_s': bound['timeout_s']}


def prepare_step(step_dir, board, policy, bound, protected_refs):
    """Write the tool snapshot and request for the delivered board bytes; return the step."""
    request = helper_request(board, policy, protected_refs)
    tool_snapshot(Path(step_dir) / 'tool')
    write_json(Path(step_dir) / 'request' / 'request.json', request)
    return step_declaration(step_dir, bound), request


def qualify_receipt(receipt_dir, receipt, board, protected):
    """Refuse a helper outcome unless its exact retained proposal is complete and in scope."""
    if receipt['status'] != 'completed':
        raise ValueError(f"placement helper {receipt['status']} (exit {receipt['exit_code']}); "
                         'iteration incomplete, no default placement substituted')
    receipt_dir = Path(receipt_dir)
    result_path = receipt_dir / RESULT
    try:
        result = read_json(result_path)
    except (OSError, ValueError) as exc:
        raise ValueError(f'placement helper result missing or malformed: {exc}') from exc
    if (not isinstance(result, dict) or result.get('status') != 'placed' or
            result.get('authority') != 'disposable-unsealed-proposal' or
            result.get('source_sha256') != receipt['inputs'].get(board) or
            result.get('board_sha256') != receipt['outputs'].get(board) or not set(receipt['changed']) <= {board}):
        raise ValueError('placement helper result does not bind the receipt input and output board')
    scope = write_scope((receipt_dir / 'inputs' / board).read_bytes(),
                        (receipt_dir / 'run' / OUTPUT).read_bytes(), protected)
    if scope['status'] != 'allowed':
        raise ValueError(f"placement helper proposal outside placement scope: {scope['violations']}")
    return {'result_sha256': digest(result_path), 'scope': scope}


def _record(work, manifest, declaration, binding_record, bound, protected, refs):
    return {'schema': RECORD_SCHEMA, 'implementation': int(Path(work).name),
            'source_candidate_id': manifest['candidate_id'], 'stage': 'placement',
            'declaration_sha256': identity(validate_declaration(declaration)),
            'binding_sha256': identity(binding_record), 'board': bound['board'], 'protected': protected,
            'protected_refs': refs, 'helper': None, 'writer_scope': None, 'proposal_scope': None,
            'status': 'incomplete', 'error': None, 'authority': 'provenance-only'}


def _receipt_name(index):
    if isinstance(index, bool) or not isinstance(index, int) or index < 1:
        raise ValueError('placement receipt index must be a positive integer')
    return f'receipts/{index}-{STEP}'


def run_placement_iteration(work, design, source_inputs, manifest, state, declaration, deadline, *,
                            receipts=None, cancellation=None, receipt_index=1):
    """Guard the writer's proposal, run the declared helper once, adopt only a qualified proposal,
    then prove the original-to-final write scope.

    Called by the campaign under its existing writer lease, after the writer session and
    the existing inventory checks. The writer's own delivered board is held to the placement
    scope before any helper runs, so a helper cannot overwrite and conceal an illegal edit.
    `receipt_index` comes from the campaign's one step-plan allocator. Any refusal, failure,
    timeout or cancellation raises and leaves the iteration incomplete; the retained record
    keeps the original exception, and a failure to write the record never replaces it.
    """
    work, design, source_inputs = Path(work), Path(design), Path(source_inputs)
    if not is_placement_iteration(state, manifest):
        raise ValueError('placement adapter runs only when the earliest failing stage is placement')
    binding_record, bound, policy, protected, refs = binding(declaration, manifest, source_inputs,
                                                             state['mutable_inputs'])
    board = bound['board']
    record = _record(work, manifest, declaration, binding_record, bound, protected, refs)
    ran = False
    failure = None
    try:
        copper = group_admission(source_inputs, bound)
        record['writer_scope'] = proposal_scope(source_inputs, design, board, protected, copper)
        if record['writer_scope']['status'] != 'allowed':
            record['status'] = 'refused'
            raise ValueError('placement writer proposal changed more than footprint transforms or admitted '
                             'group copper: '
                             f"{record['writer_scope']['violations']}")
        if bound['helper'] is not None:
            if receipts is None:
                from . import stage_receipts as receipts
            if 'stage_receipts' not in state:
                raise ValueError('campaign predates stage receipts; start a fresh campaign to run placement helpers')
            name = _receipt_name(receipt_index)
            step_dir = work / 'placement' / str(receipt_index)
            spec, request = prepare_step(step_dir, (design / board).read_bytes(), policy, bound, refs)
            if 'launch_environment' in state:
                verify_snapshot(step_dir / 'tool', state['launch_environment']['policy_files'])
            receipt_dir = work / name
            record['helper'] = {'receipt': name, 'request_sha256': identity(request), 'status': None}
            ran = True
            # Ordinary per-iteration placement never images its proposal (AGENTEE-458): the
            # cleared sandbox environment receives the typed skip policy, which the helper's
            # nested candidate imaging reads. Completed-iteration images are coordinator captures.
            receipt = receipts.run_step(receipt_dir, design, spec, {
                'implementation': record['implementation'], 'source_candidate_id': manifest['candidate_id'],
                'receipt_index': receipt_index}, deadline=deadline, cancellation=cancellation,
                skip_visuals=True)
            record['helper'].update(receipt_id=receipt['receipt_id'], status=receipt['status'],
                                    receipt_sha256=digest(receipt_dir / 'receipt.json'))
            record['helper'].update(qualify_receipt(receipt_dir, receipt, board, protected))
            receipts.adopt(receipt_dir, receipt, design)
        scope = proposal_scope(source_inputs, design, board, protected, copper)
        record['proposal_scope'] = scope
        if scope['status'] != 'allowed':
            record['status'] = 'refused'
            raise ValueError(f"placement iteration changed more than footprint transforms: {scope['violations']}")
        record['status'] = 'proposed'
    except KeyboardInterrupt as exc:
        failure = exc
        record.update(status='cancelled', error='KeyboardInterrupt')
        raise
    except (ValueError, OSError, KeyError, TypeError) as exc:
        failure = exc
        record['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        if ran and record['helper'].get('receipt_id') is None:
            # The step raised (for example on cancellation) after retaining its receipt.
            _retained_helper(record['helper'], work, receipts)
        try:
            write_json(work / 'placement-adapter.json', record)
        except OSError as exc:
            if failure is None:
                raise
            # The original failure stays the reported cause; the missing record is verified as absent.
            failure.add_note(f'placement adapter record not retained: {type(exc).__name__}: {exc}')
        if ran:
            state['stage_receipts'][work.name] = receipts.bind_implementation(
                work, design, record['implementation'], manifest['candidate_id'])
    return record


def _retained_helper(helper, work, receipts):
    receipt_dir = Path(work) / helper['receipt']
    if receipt_dir.is_symlink() or not (receipt_dir / 'receipt.json').is_file():
        return
    try:
        receipt = receipts.validate_receipt(receipt_dir)
    except (ValueError, OSError):
        return
    helper.update(receipt_id=receipt['receipt_id'], status=receipt['status'],
                  receipt_sha256=digest(receipt_dir / 'receipt.json'))


def _stored(work, kind):
    path = Path(work) / 'placement-adapter.json'
    if path.is_symlink() or not path.is_file():
        raise ValueError('placement adapter record missing')
    stored = read_json(path)
    if (not isinstance(stored, dict) or set(stored) != RECORD_KEYS or stored['schema'] != RECORD_SCHEMA or
            stored['authority'] != 'provenance-only'):
        raise ValueError(f'placement record is not {kind}')
    return stored


def _helper_receipt(work, bound, manifest, receipts, receipt_index, source_files=None):
    """Validated coordinator receipt, its step directory and the coordinator-derived step."""
    name = _receipt_name(receipt_index)
    receipt_dir = Path(work) / name
    if receipt_dir.is_symlink() or not (receipt_dir / 'receipt.json').is_file():
        raise ValueError('placement helper receipt missing')
    receipt = receipts.validate_receipt(receipt_dir, binding={
        'implementation': int(Path(work).name), 'source_candidate_id': manifest['candidate_id'],
        'receipt_index': receipt_index})
    step_dir = Path(work) / 'placement' / str(receipt_index)
    if receipt['spec'] != receipts.validate_step(step_declaration(step_dir, bound), host=False):
        raise ValueError('placement receipt step differs from the placement declaration')
    # The retained tool snapshot is the receipt's observed source tree, byte for byte.
    if receipt['tool']['source_tree'] != {'path': TOOL_AT, **receipts.tree_identity(step_dir / 'tool')}:
        raise ValueError('placement helper source snapshot differs from its receipt')
    if source_files is not None:
        verify_snapshot(step_dir / 'tool', source_files)
    return name, receipt_dir, receipt, step_dir


def verify_record(work, source_inputs, proposal_inputs, manifest, declaration, mutable_inputs, *, receipts=None,
                  receipt_index=1, source_files=None):
    """Recompute a proposed placement record from retained receipts and the captured proposal.

    Every field is re-derived; none is copied from the stored record. The captured proposal
    must be inside the placement write scope now, whatever the record claims, and so must the
    writer's own delivered design that the helper started from.
    """
    work, source_inputs = Path(work), Path(source_inputs)
    stored = _stored(work, 'a proposed placement iteration')
    if stored['status'] != 'proposed' or stored['error'] is not None:
        raise ValueError('placement record is not a proposed placement iteration')
    binding_record, bound, policy, protected, refs = binding(declaration, manifest, source_inputs, mutable_inputs)
    copper = group_admission(source_inputs, bound)
    scope = proposal_scope(source_inputs, proposal_inputs, bound['board'], protected, copper)
    if scope['status'] != 'allowed' or scope['violations']:
        raise ValueError(f"captured proposal is outside the placement write scope: {scope['violations']}")
    expected = {**_record(work, manifest, declaration, binding_record, bound, protected, refs),
                'writer_scope': scope, 'proposal_scope': scope, 'status': 'proposed'}
    if bound['helper'] is not None:
        if receipts is None:
            from . import stage_receipts as receipts
        helper = stored['helper']
        if not isinstance(helper, dict) or set(helper) != HELPER_KEYS:
            raise ValueError('placement record lacks the declared helper receipt')
        if helper['receipt'] != _receipt_name(receipt_index):
            raise ValueError('placement record names no coordinator placement receipt')
        name, receipt_dir, receipt, step_dir = _helper_receipt(work, bound, manifest, receipts, receipt_index,
                                                               source_files)
        # The writer's delivered design is the receipt's retained input copy.
        expected['writer_scope'] = proposal_scope(source_inputs, receipt_dir / 'inputs', bound['board'], protected,
                                                  copper)
        if expected['writer_scope']['status'] != 'allowed':
            raise ValueError('placement writer proposal before the helper is outside the placement write scope: '
                             f"{expected['writer_scope']['violations']}")
        # The request is re-derived from the receipt's retained input board, never trusted.
        request = helper_request((receipt_dir / 'inputs' / bound['board']).read_bytes(), policy, refs)
        if read_json(step_dir / 'request' / 'request.json') != request:
            raise ValueError('placement helper request differs from the coordinator derivation')
        # The captured proposal is exactly the adopted helper output over the delivered design.
        if {**receipt['inputs'], **receipt['outputs']} != _files(proposal_inputs):
            raise ValueError('captured proposal differs from the adopted placement helper output')
        expected['helper'] = {'receipt': name, 'request_sha256': identity(request),
                              'receipt_id': receipt['receipt_id'], 'status': receipt['status'],
                              'receipt_sha256': digest(receipt_dir / 'receipt.json'),
                              **qualify_receipt(receipt_dir, receipt, bound['board'], protected)}
    if expected != stored:
        raise ValueError('placement record differs from retained receipts or captured proposal')
    return stored


def verify_attempt(work, source_inputs, design, manifest, declaration, mutable_inputs, *, receipts=None,
                   receipt_index=1, source_files=None):
    """Verify the retained record of an incomplete placement iteration.

    Asserts nothing about adoption or completion. The bindings, protected set, any helper
    receipt and every scope proof are recomputed from retained coordinator records and the
    unchanged scratch design; a forged or relabelled outcome is refused.
    """
    work, source_inputs, design = Path(work), Path(source_inputs), Path(design)
    stored = _stored(work, 'an incomplete placement attempt')
    if (stored['status'] not in ATTEMPT_STATUSES or not isinstance(stored['error'], str) or
            not stored['error'] or (stored['status'] == 'cancelled') != (stored['error'] == 'KeyboardInterrupt')):
        raise ValueError('placement record is not an incomplete placement attempt')
    binding_record, bound, policy, protected, refs = binding(declaration, manifest, source_inputs, mutable_inputs)
    expected = {**_record(work, manifest, declaration, binding_record, bound, protected, refs),
                'status': stored['status'], 'error': stored['error']}
    admission = []

    def scope_of(design_dir):
        # The sub-layout plan is recomputed only when a scope proof has to be.
        if not admission:
            admission.append(group_admission(source_inputs, bound))
        return proposal_scope(source_inputs, design_dir, bound['board'], protected, admission[0])
    helper = stored['helper']
    receipt = None
    if helper is not None:
        if bound['helper'] is None or not isinstance(helper, dict):
            raise ValueError('placement attempt names an undeclared helper')
        if receipts is None:
            from . import stage_receipts as receipts
        name = _receipt_name(receipt_index)
        receipt_dir = work / name
        step_dir = work / 'placement' / str(receipt_index)
        if (receipt_dir / 'receipt.json').exists():
            name, receipt_dir, receipt, step_dir = _helper_receipt(work, bound, manifest, receipts, receipt_index,
                                                                   source_files)
            delivered = receipt_dir / 'inputs'
        elif receipt_dir.exists() or receipt_dir.is_symlink():
            raise ValueError('placement attempt receipt directory without a coordinator receipt')
        else:
            # The step never launched (for example an exhausted deadline); nothing was adopted.
            delivered = design
        request = helper_request((delivered / bound['board']).read_bytes(), policy, refs)
        if read_json(step_dir / 'request' / 'request.json') != request:
            raise ValueError('placement helper request differs from the coordinator derivation')
        expected['helper'] = {'receipt': name, 'request_sha256': identity(request), 'status': None}
        if receipt is not None:
            expected['helper'].update(receipt_id=receipt['receipt_id'], status=receipt['status'],
                                      receipt_sha256=digest(receipt_dir / 'receipt.json'))
            try:
                expected['helper'].update(qualify_receipt(receipt_dir, receipt, bound['board'], protected))
            except ValueError:
                pass
        expected['writer_scope'] = scope_of(delivered)
    elif stored['writer_scope'] is not None:
        expected['writer_scope'] = scope_of(design)
    if stored['proposal_scope'] is not None:
        expected['proposal_scope'] = scope_of(design)
    if stored['status'] == 'refused' and not any(
            s is not None and s['status'] == 'refused' for s in (expected['writer_scope'], expected['proposal_scope'])):
        raise ValueError('refused placement attempt has no refused scope proof')
    if expected['proposal_scope'] is not None and expected['proposal_scope']['status'] == 'allowed':
        raise ValueError('placement attempt with an allowed final scope is not incomplete')
    if expected != stored:
        raise ValueError('placement attempt record differs from retained receipts or scratch design')
    return stored


def writer_context(run, manifest):
    """Sealed placement-stage facts for the writer prompt; context, never evidence of a pass."""
    run = Path(run)
    result = read_json(run / 'result.json')
    stages = {c['id']: c.get('stage') for c in manifest['scope']['checks']}
    hashes = result.get('artifact_hashes', {})
    checks = []
    for check in result['checks']:
        if stages.get(check['id']) != 'placement':
            continue
        entry = {k: check.get(k) for k in ('id', 'kind', 'execution', 'outcome', 'summary', 'details', 'evidence')}
        probe = f"work/{check['id']}/placement-probe.json"
        # Only a retained artifact matching the sealed result hash is shown as geometry.
        if probe in hashes and (run / probe).is_file() and digest(run / probe) == hashes[probe]:
            entry['probe'] = read_json(run / probe)
        checks.append(entry)
    ids = {c['id'] for c in checks}
    # A collected check finding carries id `check/<check id>` and local_id `<check id>`.
    findings = [f for f in result.get('findings', []) if f.get('source') == 'check' and
                f.get('status') == 'accepted' and f.get('local_id') in ids and
                f.get('id') == f"check/{f['local_id']}"]
    return {'schema': CONTEXT_SCHEMA, 'stage': 'placement', 'checks': checks, 'findings': findings,
            'routing_feedback': {'status': 'unavailable',
                                 'reason': 'no captured AGENTEE-93 routed-attempt receipt is integrated'},
            'authority': 'context-only'}


# In-sandbox driver: native netlist export, then the unchanged AGENTEE-54 writer.

def netlist_identity(captured, exported):
    """Stamp-masked identity of a captured netlist against a fresh export (AGENTEE-439).

    ``semantic_match`` only when every byte outside native_netlist.STAMP_FIELDS is equal;
    both exact digests and both stamps are recorded, and any other difference refuses."""
    from .native_netlist import STAMP_FIELDS, stamp_identity
    record = {'stamp_fields': list(STAMP_FIELDS), 'captured': stamp_identity(captured),
              'exported': stamp_identity(exported)}
    same = record['captured']['semantic_sha256'] == record['exported']['semantic_sha256']
    return {**record, 'status': 'semantic_match' if same else 'mismatch'}


def sandbox_main(argv=None):
    parser = argparse.ArgumentParser(description='placement helper step (sandbox side)')
    parser.add_argument('board')
    parser.add_argument('schematic')
    parser.add_argument('--request', required=True)
    parser.add_argument('--root', required=True)
    parser.add_argument('--netlist', help='captured native netlist a blocks request was packed against')
    parser.add_argument('--netlist-sha256', help='bound sha256 of the captured netlist bytes')
    args = parser.parse_args(argv)
    if (args.netlist is None) != (args.netlist_sha256 is None):
        parser.error('--netlist and --netlist-sha256 go together')
    root = Path(args.root)
    root.mkdir()
    netlist = root / 'netlist.xml'
    command = ['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', str(netlist), args.schematic]
    record = {'schema': DRIVER_SCHEMA, 'netlist_export': {'argv': command, 'exit_code': None},
              'status': 'running', 'error': None}
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
        (root / 'netlist-export.stdout.txt').write_text(completed.stdout)
        (root / 'netlist-export.stderr.txt').write_text(completed.stderr)
        record['netlist_export']['exit_code'] = completed.returncode
        if completed.returncode != 0 or not netlist.is_file():
            record['status'] = 'netlist_export_failed'
            return 3
        placer_netlist = netlist
        if args.netlist is not None:
            # The captured bytes are used only when they are bound and the fresh export of the
            # captured schematic differs from them in the declared stamps at most.
            record['netlist_identity'] = None
            try:
                if digest(args.netlist) != args.netlist_sha256:
                    raise ValueError('captured netlist differs from its bound sha256')
                record['netlist_identity'] = netlist_identity(args.netlist, netlist)
            except (OSError, ValueError) as exc:
                record.update(status='netlist_identity_refused', error=f'{type(exc).__name__}: {exc}')
                return 4
            if record['netlist_identity']['status'] != 'semantic_match':
                record.update(status='netlist_identity_mismatch',
                              error='captured netlist differs from the fresh export beyond the declared stamps')
                return 4
            placer_netlist = Path(args.netlist)
        from .placer_runner import main
        code = main([args.board, str(placer_netlist), '--request', args.request,
                     '--destination', str(root / 'placed'), '--result', str(root / 'placement-result.json')])
        record['status'] = 'placed' if code == 0 else 'not_placed'
        return code
    except BaseException as exc:
        record.update(status='error', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        write_json(root / 'driver.json', record)
