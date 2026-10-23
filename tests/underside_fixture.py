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

from test_blocks import native_xml
from test_placement import MM, rectangle

NAMESPACE = uuid.UUID('a87979ad-5c32-4d51-9742-0a823863a095')
STEP_BUDGET = 1000
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


def allocation_text():
    return json.dumps({'schema': 'agent-ee.block-allocation/1', 'blocks': [
        {'id': 'a', 'references': ['J1'], 'anchor': 'J1'},
        {'id': 'b', 'references': ['U1', 'C1'], 'anchor': 'U1'}]}, sort_keys=True)


def contract(board_sha256, side_eligibility, packing_policy):
    return {'schema': 'agent-ee.block-contract/1', 'allocation': {
        'schema': 'agent-ee.block-allocation/2', 'board_sha256': board_sha256,
        'blocks': [{'id': 'a', 'references': ['J1'], 'anchor': {'member': 'J1'}, 'interfaces_to_fixed': None},
                   {'id': 'b', 'references': ['U1', 'C1'], 'anchor': {'member': 'U1'}, 'interfaces_to_fixed': None}],
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


def arrangement_policy():
    return {'schema': 'agent-ee.block-arrangement/1', 'step_budget': STEP_BUDGET,
            'priority_blocks': ['a', 'b'],
            'no_stacking': {'references': ['H1'], 'margin_nm': 0,
                            'provenance': {'enforcement': 'hard', 'citation': CITATION}}}


def write_board(directory, *, witness=False, spare_front=False):
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

    width = 15.5 if spare_front else 10.5
    outline = [(0, 0), (width, 0), (width, 3), (0, 3)]
    for i, start in enumerate(outline):
        line(board, k.Edge_Cuts, start, outline[(i + 1) % 4], f'edge-{i}')
    nets = {}
    for name in ('VCC', 'GND'):
        nets[name] = k.NETINFO_ITEM(board, name)
        board.Add(nets[name])
    for index, ref in enumerate(('J1', 'U1', 'C1', 'R1', 'H1', 'H2')):
        fp = k.FOOTPRINT(board)
        identity(fp, ref)
        fp.SetReference(ref)
        fp.SetValue(dict((p[0], p[4]) for p in PARTS).get(ref, 'fixed reservation'))
        fp.SetFPID(k.LIB_ID('Owned', 'Residual' if ref == 'R1' else 'Square'))
        fp.SetAttributes(k.FP_BOARD_ONLY | k.FP_EXCLUDE_FROM_BOM | k.FP_EXCLUDE_FROM_POS_FILES
                         if ref in ('H1', 'H2') else k.FP_SMD)
        board.Add(fp)
        for field in fp.GetFields():
            identity(field, f'{ref}-field-{field.GetName()}')
            field.SetVisible(False)
        half_width = 2.25 if ref == 'R1' else 1
        corners = [(-half_width, -1), (half_width, -1), (half_width, 1), (-half_width, 1)]
        for i, start in enumerate(corners):
            line(fp, k.F_CrtYd, start, corners[(i + 1) % 4], f'{ref}-courtyard-{i}')
        if ref not in ('H1', 'H2'):
            for number, net, y in (('1', 'VCC', -0.4), ('2', 'GND', 0.4)):
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
        position, side = WITNESS[ref]
        if spare_front and ref == 'H1':
            position = [14000000, 1500000]
        if not witness and ref in ('U1', 'C1', 'R1'):
            position, side = [20 * MM, (5 + index * 4) * MM], 'F'
        if side == 'B':
            fp.Flip(vec(0, 0), k.FLIP_DIRECTION_LEFT_RIGHT)
        fp.SetPosition(k.VECTOR2I(*position))
    path = directory / ('witness.kicad_pcb' if witness else 'source.kicad_pcb')
    if not k.SaveBoard(str(path), board):
        raise RuntimeError('native fixture board save failed')
    netlist = directory / 'native.xml'
    netlist.write_bytes(native_xml(parts=PARTS, sheets=SHEETS))
    return path, netlist
