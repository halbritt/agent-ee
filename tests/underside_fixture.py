"""Literal AGENTEE-479 congested-underside fixture; all design geometry is authored.

Millimetres: outline 0..10.5 by 0..3, edge/gap 0.5. A occupies
0.5..2.5 on F. H1 occupies 8..10 on F and casts the same B shadow;
H2 occupies 0.5..2.5 on B. The remaining slot on either face is
3..7.5 by 0.5..2.5. R1 is exactly that size, on F. B's two 2x2
members with their 0.5 gap must therefore move from F to B to finish.
No board data is copied from the pod; native flips are done by pcbnew.

Regression contract (frozen AGENTEE-473 RESULT.md section 4):
1. Same literal geometry, seed, ordering and finite budgets for all arms.
2. Native saved hand witness, independent judge and courtyard DRC.
3. Residual exclusive slot proved by the obstacle/gap intervals; a recorded
   adjustment changes B from its first global pose before completion.
4. Regrade membership, eligibility, spread/internal wire and every pad bound
   after adjustment and native save/reload, identifying deliberate cross-face work.
5. Saved-board cohesion/pin-bound and forbidden-face mutations are refused.
6. Matched side-disabled failure is a configuration/search result, not a proof
   of physical infeasibility (the native witness remains legal).
7. A spare-front variant prefers F when its wire objective is lower, no balancing.
8. The writer rejects any side change outside original-board-bound eligibility.
"""
import hashlib
import json
from pathlib import Path
import uuid

from test_blocks import PINS, native_xml
from test_placement import MM, rectangle

NAMESPACE = uuid.UUID('a87979ad-5c32-4d51-9742-0a823863a095')
STEP_BUDGET = 1000
CHAIN_STEP_BUDGET = 20000
CITATION = 'tests/underside_fixture.py: authored access, spacing and underside shadow constraints'
SOURCE_TEXT = 'Original fixture: connector A is front-only; the U1/C1 maximum pad-pair distance is 3 mm.'
SOURCE_PATH = 'fixture-sources/underside-access-and-distance.txt'
PARTS = (
    ('J1', '/a/', 'C', 'Owned:Square', 'front access', {'1': 'VCC', '2': 'GND'}),
    ('U1', '/b/', 'C', 'Owned:Square', 'device', {'1': 'VCC', '2': 'GND'}),
    ('C1', '/b/', 'C', 'Owned:Square', 'support', {'1': 'VCC', '2': 'GND'}),
    ('R1', '/', 'C', 'Owned:Residual', 'front residual', {'1': 'VCC', '2': 'GND'}),
)
SHEETS = {'/': '/', '/a/': '/a/', '/b/': '/b/'}
WITNESS = {'J1': ([1500000, 1500000], 'F'), 'U1': ([4000000, 1500000], 'B'),
           'C1': ([6500000, 1500000], 'B'), 'R1': ([5250000, 1500000], 'F'),
           'H1': ([9000000, 1500000], 'F'), 'H2': ([1500000, 1500000], 'B')}
WITNESS_COURTYARDS = {'J1': rectangle(0.5, 0.5, 2, 2), 'U1': rectangle(3, 0.5, 2, 2),
                     'C1': rectangle(5.5, 0.5, 2, 2), 'R1': rectangle(3, 0.5, 4.5, 2),
                     'H1': rectangle(8, 0.5, 2, 2), 'H2': rectangle(0.5, 0.5, 2, 2)}


def allocation_text(*, chain=False):
    return json.dumps({'schema': 'agent-ee.block-allocation/1', 'blocks': [
        {'id': 'a', 'references': ['J1'], 'anchor': 'J1'},
        {'id': 'b', 'references': ['U1', 'C1'] + (['C2'] if chain else []), 'anchor': 'U1'}]}, sort_keys=True)


def contract(board_sha256, side_eligibility, packing_policy, *, chain=False):
    return {'schema': 'agent-ee.block-contract/1', 'allocation': {
        'schema': 'agent-ee.block-allocation/2', 'board_sha256': board_sha256,
        'blocks': [{'id': 'a', 'references': ['J1'], 'anchor': {'member': 'J1'}, 'interfaces_to_fixed': None},
                   {'id': 'b', 'references': ['U1', 'C1'] + (['C2'] if chain else []), 'anchor': {'member': 'U1'}, 'interfaces_to_fixed': None}],
        'fixed': [{'reference': 'H1'}, {'reference': 'H2'}],
        'shared': [{'reference': 'R1', 'role': 'residual'}]},
        'eligibility': side_eligibility, 'relationships': packing_policy['electrical_locality'],
        'templates': [], 'thickness_nm': 1600000}


def eligibility(board_sha256):
    return {'schema': 'fome-mc.side-eligibility/1', 'default': {'faces': ['F', 'B']},
            'sources': {'board_sha256': board_sha256},
            'blocks': {'a': {'eligible_faces': ['F'], 'face_restrictions': [{
                'id': 'fixture-a-front', 'citation': CITATION,
                'source': {'path': SOURCE_PATH, 'sha256': hashlib.sha256(SOURCE_TEXT.encode()).hexdigest(),
                           'quote': SOURCE_TEXT}}]}, 'b': {'eligible_faces': ['F', 'B']}},
            'members': {'R1': {'eligible_faces': ['F']}, 'H1': {'eligible_faces': ['F']},
                        'H2': {'eligible_faces': ['B']}}}


def policy():
    source = {'path': SOURCE_PATH, 'sha256': hashlib.sha256(SOURCE_TEXT.encode()).hexdigest(),
              'locator': 'authored fixture access and distance', 'quote': SOURCE_TEXT}

    def pair(pad, net):
        return {'from': {'reference': 'U1', 'pad': pad, 'net': net},
                'to': {'reference': 'C1', 'pad': pad, 'net': net},
                'max_distance_nm': 3 * MM, 'citation': CITATION,
                'provenance': {'class': 'datasheet', 'basis': 'Authored fixture numeric relationship',
                    'derivation': {'kind': 'numeric', 'value_nm': 3 * MM, 'source': source}}}

    return {'schema': 'agent-ee.packing-policy/1', 'step_budget': STEP_BUDGET, 'gap_nm': 500000,
            'min_courtyard_edge_mm': 0.5, 'fixed_refs': ['H1', 'H2'], 'priority_refs': [],
            'ground_nets': ['GND'], 'obstacles': [], 'capacity_proof': False,
            'constraint_provenance': {
                'gap_nm': {'class': 'fixture_author', 'enforcement': 'hard', 'value_nm': 500000, 'citation': CITATION},
                'min_courtyard_edge_mm': {'class': 'fixture_author', 'enforcement': 'hard',
                                           'value_nm': 500000, 'citation': CITATION}},
            'groups': [{'id': 'connector-access', 'references': ['J1'],
                        'region': {'polygons': [rectangle(0.5, 0.5, 2, 2)]},
                        'constraint_provenance': {'region': {'class': 'fixture_author', 'enforcement': 'hard',
                                                              'citation': CITATION}}}],
            'electrical_locality': {'schema': 'agent-ee.electrical-locality/1',
                'source_records': {SOURCE_PATH: SOURCE_TEXT}, 'obligations': [
                    {'id': 'device-support', 'metric': 'pad-centre-to-centre',
                     'supply': pair('1', 'VCC'), 'return': pair('2', 'GND')}]}}


def chain_policy():
    """Authored U1-C2-C1 electrical chain, C1 searched before its C2 peer."""
    result = policy()
    # The taller bridge enlarges Stage A's synthetic plane. At 1,000 steps,
    # its first distant C1 pose exhausts C2's search before the first backjump.
    # A feasible frame still exists: U1(0,0) F/0, C2(-2.5,-.6) F/270,
    # C1(-2.5,-1) B/0 mm. The F courtyards have exactly .5 mm separation;
    # all four pad-pair squared distances (including 1.6 mm thickness) are
    # <=6.29 mm^2, below the authored 16 mm^2 bound. These coordinates are
    # arithmetic evidence only: the producer must discover its own candidate.
    result['step_budget'] = CHAIN_STEP_BUDGET
    result['priority_refs'] = ['C1', 'C2']
    text = 'Original synthetic chain: each declared pad-pair maximum distance is 4 mm.'
    path = 'fixture-sources/underside-chain-distance.txt'
    source = {'path': path, 'sha256': hashlib.sha256(text.encode()).hexdigest(),
              'locator': 'authored chain maximum distance', 'quote': text}

    def pair(a, apad, b, bpad, net):
        return {'from': {'reference': a, 'pad': apad, 'net': net},
                'to': {'reference': b, 'pad': bpad, 'net': net},
                'max_distance_nm': 4 * MM, 'citation': CITATION,
                'provenance': {'class': 'datasheet', 'basis': 'Authored chain numeric relationship',
                    'derivation': {'kind': 'numeric', 'value_nm': 4 * MM, 'source': source}}}

    cohesion_path = 'fixture-sources/chain-cohesion.txt'
    cohesion_text = 'Authored chain C1 origin must remain within 3.25 mm of the U1 origin.'
    cohesion_hash = hashlib.sha256(cohesion_text.encode()).hexdigest()
    result['groups'].append({'id': 'chain-cohesion', 'references': ['C1'],
        'anchor': {'reference': 'U1'}, 'max_distance_nm': 3250000,
        'constraint_provenance': {'max_distance_nm': {'class': 'fixture_author', 'enforcement': 'hard',
            'value_nm': 3250000, 'citation': f'{cohesion_path} sha256:{cohesion_hash}: {cohesion_text}'}}})
    result['electrical_locality'] = {'schema': 'agent-ee.electrical-locality/1',
        'source_records': {SOURCE_PATH: SOURCE_TEXT, path: text, cohesion_path: cohesion_text}, 'obligations': [
            {'id': 'device-support', 'metric': 'pad-centre-to-centre',
             'supply': pair('U1', '1', 'C2', '1', 'VCC'), 'return': pair('U1', '2', 'C2', '2', 'GND')},
            {'id': 'chain-support', 'metric': 'pad-centre-to-centre',
             'supply': pair('C2', '3', 'C1', '1', 'OUT'), 'return': pair('C2', '4', 'C1', '2', 'GND')}]}
    return result


def arrangement_policy():
    return {'schema': 'agent-ee.block-arrangement/1', 'step_budget': STEP_BUDGET,
            'priority_blocks': ['a', 'b'],
            'no_stacking': {'references': ['H1'], 'margin_nm': 0,
                            'provenance': {'enforcement': 'hard', 'citation': CITATION}}}


def write_board(directory, *, witness=False, spare_front=False, chain=False):
    """Build and save the literal native source or the hand-constructed witness."""
    import pcbnew as k
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    board = k.BOARD()

    def vec(x, y):
        return k.VECTOR2I(round(x * MM), round(y * MM))

    def identity(item, name):
        item.SetUuidDirect(k.KIID(str(uuid.uuid5(NAMESPACE, name))))

    def line(parent, layer, start, end, name):
        shape = k.PCB_SHAPE(parent)
        shape.SetShape(k.SHAPE_T_SEGMENT)
        shape.SetLayer(layer)
        shape.SetStart(vec(*start))
        shape.SetEnd(vec(*end))
        shape.SetWidth(50000)
        identity(shape, name)
        parent.Add(shape)

    width, height = (18.5, 10) if chain else (15.5 if spare_front else 10.5, 3)
    outline = [(0, 0), (width, 0), (width, height), (0, height)]
    for i, start in enumerate(outline):
        line(board, k.Edge_Cuts, start, outline[(i + 1) % 4], f'edge-{i}')
    nets = {}
    for name in (('VCC', 'GND', 'OUT') if chain else ('VCC', 'GND')):
        nets[name] = k.NETINFO_ITEM(board, name)
        board.Add(nets[name])
    parts = tuple((*part[:-1], {'1': 'OUT', '2': 'GND'}) if chain and part[0] == 'C1' else part for part in PARTS)
    if chain:
        parts += (('C2', '/b/', 'CHAIN4', 'Owned:Square', 'chain support', {'1': 'VCC', '2': 'GND', '3': 'OUT', '4': 'GND'}),)
    refs = ('J1', 'U1', 'C1', 'C2', 'R1', 'H1', 'H2') if chain else ('J1', 'U1', 'C1', 'R1', 'H1', 'H2')
    for index, ref in enumerate(refs):
        fp = k.FOOTPRINT(board)
        identity(fp, ref)
        fp.SetReference(ref)
        fp.SetValue(dict((p[0], p[4]) for p in parts).get(ref, 'fixed reservation'))
        fp.SetFPID(k.LIB_ID('Owned', 'Residual' if ref == 'R1' else 'Square'))
        fp.SetAttributes(k.FP_BOARD_ONLY | k.FP_EXCLUDE_FROM_BOM | k.FP_EXCLUDE_FROM_POS_FILES
                         if ref in ('H1', 'H2') else k.FP_SMD)
        board.Add(fp)
        for field in fp.GetFields():
            identity(field, f'{ref}-field-{field.GetName()}')
            field.SetVisible(False)
        half_width = 2.25 if ref == 'R1' else 1
        # The chain's bridge has authored room beyond its far edge. This
        # supplies a real initial block window for an electrical member move;
        # the near edge and every sourced gap remain unchanged.
        far_y = 1.5 if chain and ref == 'C2' else 1
        corners = [(-half_width, -1), (half_width, -1), (half_width, far_y), (-half_width, far_y)]
        for i, start in enumerate(corners):
            line(fp, k.F_CrtYd, start, corners[(i + 1) % 4], f'{ref}-courtyard-{i}')
        if ref not in ('H1', 'H2'):
            # Equal .8-mm output/return pitch allows a pad alignment that
            # improves both declared relationships. Native pad edge gaps remain
            # at least .2 mm within the unchanged authored courtyard.
            pins = (('1', 'VCC', -0.6), ('2', 'GND', -0.2), ('3', 'OUT', 0.2), ('4', 'GND', 1.0)) if ref == 'C2' else (
                ('1', 'OUT' if chain and ref == 'C1' else 'VCC', -0.4), ('2', 'GND', 0.4))
            for number, net, y in pins:
                pad = k.PAD(fp)
                identity(pad, f'{ref}-pad-{number}')
                pad.SetNumber(number)
                pad.SetAttribute(k.PAD_ATTRIB_SMD)
                pad.SetShape(k.PAD_SHAPE_RECT)
                pad.SetSize(vec(0.2, 0.2))
                pad.SetPosition(vec(0, y))
                layers = k.LSET()
                for layer in (k.F_Cu, k.F_Mask, k.F_Paste):
                    layers.AddLayer(layer)
                pad.SetLayerSet(layers)
                pad.SetNet(nets[net])
                fp.Add(pad)
        if chain and ref == 'R1':
            zone = k.ZONE(fp)
            identity(zone, 'R1-owned-pad-area')
            zone.SetIsRuleArea(True)
            zone.SetLayer(k.F_Cu)
            zone.SetDoNotAllowPads(True)
            zone.SetDoNotAllowFootprints(False)
            zone.SetDoNotAllowTracks(False)
            zone.SetDoNotAllowVias(False)
            zone.SetDoNotAllowZoneFills(False)
            fp.Add(zone)
            zone.Outline().NewOutline()
            for x, y in ((-0.1, -0.1), (0.1, -0.1), (0.1, 0.1), (-0.1, 0.1)):
                zone.Outline().Append(round(x * MM), round(y * MM))
        position, side = WITNESS.get(ref, ([4000000, 4000000], 'F'))
        if spare_front and ref == 'H1':
            position = [17000000 if chain else 14000000, 1500000]
        if not witness and ref in ('U1', 'C1', 'R1', 'C2'):
            position, side = [20 * MM, (5 + index * 4) * MM], 'F'
        if side == 'B':
            fp.Flip(vec(0, 0), k.FLIP_DIRECTION_LEFT_RIGHT)
        fp.SetPosition(k.VECTOR2I(*position))
    path = directory / ('witness.kicad_pcb' if witness else 'source.kicad_pcb')
    if not k.SaveBoard(str(path), board):
        raise RuntimeError('native fixture board save failed')
    netlist = directory / 'native.xml'
    pins = {**PINS, 'CHAIN4': tuple((str(i), '~', 'passive') for i in range(1, 5))} if chain else PINS
    netlist.write_bytes(native_xml(parts=parts, sheets=SHEETS, pins=pins))
    return path, netlist
