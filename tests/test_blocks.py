"""Stage-A block derivation, isolated packing, candidate schema and matching (AGENTEE-339).

Every fixture is original: authored sheets, parts, pads, nets and limits.
"""
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from agent_ee import blocks, electrical_locality, placer
from agent_ee.native_netlist import read_native_inventory, read_sheet_names
from test_placement import MM, capture, part, rectangle

ROOT = Path(__file__).resolve().parents[1]
# (reference, sheet path, libpart, fpid, value, {pad: net})
PARTS = (
    ('U1', '/s1/', 'BUCK', 'Owned:QFN4', 'owned-buck', {'1': 'VIN', '2': 'GND', '3': 'SW', '4': 'VOUT'}),
    ('C1', '/s1/', 'C', 'Owned:C0805', '4.7uF', {'1': 'VIN', '2': 'GND'}),
    ('L1', '/s1/', 'L', 'Owned:L1210', '10uH', {'1': 'SW', '2': 'VOUT'}),
    ('C3', '/s1/', 'C', 'Owned:C0805', '22uF', {'1': 'VOUT', '2': 'GND'}),
    ('U2', '/s2/', 'SENSOR', 'Owned:SOT3', 'owned-sensor', {'1': 'VOUT', '2': 'GND', '3': 'SENSE'}),
    ('C2', '/s2/', 'C', 'Owned:C0805', '100nF', {'1': 'VOUT', '2': 'GND'}),
    ('R1', '/s2/s3/', 'R', 'Owned:R0603', '10k', {'1': 'SENSE', '2': 'GND'}),
    ('J1', '/', 'CONN', 'Owned:HDR2', 'owned-conn', {'1': 'VIN', '2': 'GND'}),
)
SHEETS = {'/': '/', '/s1/': '/Power/', '/s2/': '/Sensor/', '/s2/s3/': '/Sensor/Filter/'}
PINS = {'BUCK': (('1', 'VIN', 'power_in'), ('2', 'GND', 'power_in'), ('3', 'SW', 'output'), ('4', 'FB', 'input')),
        'BUCK2': (('1', 'VIN', 'power_in'), ('2', 'GND', 'power_in'), ('3', 'PH', 'output'), ('4', 'FB', 'input')),
        'SENSOR': (('1', 'VDD', 'power_in'), ('2', 'GND', 'power_in'), ('3', 'OUT', 'output')),
        'C': (('1', '~', 'passive'), ('2', '~', 'passive')), 'L': (('1', '~', 'passive'), ('2', '~', 'passive')),
        'R': (('1', '~', 'passive'), ('2', '~', 'passive')), 'CONN': (('1', 'P1', 'passive'), ('2', 'P2', 'passive'))}


def native_xml(parts=PARTS, sheets=SHEETS, pins=PINS):
    root = ET.Element('export', version='E')
    design = ET.SubElement(root, 'design')
    for path, name in sheets.items():
        ET.SubElement(design, 'sheet', name=name, tstamps=path)
    comps = ET.SubElement(root, 'components')
    nets = {}
    for ref, sheet, lib, fpid, value, pads in parts:
        comp = ET.SubElement(comps, 'comp', ref=ref)
        ET.SubElement(comp, 'value').text = value
        ET.SubElement(comp, 'footprint').text = fpid
        ET.SubElement(comp, 'libsource', lib='Owned', part=lib)
        ET.SubElement(comp, 'sheetpath', names=sheets[sheet], tstamps=sheet)
        ET.SubElement(comp, 'tstamps').text = 'uuid-' + ref
        unit = ET.SubElement(ET.SubElement(ET.SubElement(comp, 'units'), 'unit', name='A'), 'pins')
        for pad, net in pads.items():
            ET.SubElement(unit, 'pin', num=pad)
            nets.setdefault(net, []).append((ref, pad))
    libparts = ET.SubElement(root, 'libparts')
    for lib, rows in pins.items():
        node = ET.SubElement(ET.SubElement(libparts, 'libpart', lib='Owned', part=lib), 'pins')
        for number, name, kind in rows:
            ET.SubElement(node, 'pin', num=number, name=name, type=kind)
    out = ET.SubElement(root, 'nets')
    for code, (name, terminals) in enumerate(sorted(nets.items()), 1):
        net = ET.SubElement(out, 'net', code=str(code), name=name)
        for ref, pad in terminals:
            ET.SubElement(net, 'node', ref=ref, pin=pad)
    return ET.tostring(root)


def footprint(ref, x, y, fpid, pads, size=2, side='F', rotation=0):
    fp = part(ref, x, y, side=side, shape=rectangle(x, y, size, size))
    fp.update(fpid=fpid, locked=False, rotation_deg=rotation)
    fp['pads'] = [dict(uuid=f'{ref}-{number}', number=number, net=net, attribute='SMD', layers=[side + '.Cu'],
                       shape=1, rotation_deg=0, size_nm=[400000, 400000], copper_centroid_offset_nm=[[0, 1], [0, 1]],
                       position_nm=[int((x + 0.5 + (i % 2)) * MM), int((y + 0.5 + (i // 2)) * MM)])
                  for i, (number, net) in enumerate(sorted(pads.items()))]
    return fp


def board(parts=PARTS):
    fps = [footprint(ref, 3 + 5 * i, 3 + 4 * (i % 3), fpid, pads, size=3 if ref.startswith('U') else 2)
           for i, (ref, _, _, fpid, _, pads) in enumerate(parts)]
    hole = part('H1', 40, 30)
    hole.update(fpid='Owned:Hole', locked=True)
    b = capture(fps + [hole], outline=rectangle(0, 0, 50, 40))
    b['pcbnew_version'] = '10.0.7'
    return b


def obligations(limit=6 * MM):
    def pair(a, b, net, cite='Owned fixture requirement EE339: authored test limit.'):
        return {'from': dict(zip(('reference', 'pad', 'net'), a + (net,))),
                'to': dict(zip(('reference', 'pad', 'net'), b + (net,))), 'max_distance_nm': limit, 'citation': cite}
    return {'schema': electrical_locality.SCHEMA, 'obligations': [
        {'id': 'input-C1', 'metric': 'pad-centre-to-centre', 'supply': pair(('U1', '1'), ('C1', '1'), 'VIN'),
         'return': pair(('U1', '2'), ('C1', '2'), 'GND')},
        {'id': 'output-C3', 'metric': 'pad-centre-to-centre', 'kind': 'buck-output-loop', 'ic': 'U1',
         'inductor': 'L1', 'return_net': 'GND', 'supply': pair(('L1', '2'), ('C3', '1'), 'VOUT'),
         'return': pair(('U1', '2'), ('C3', '2'), 'GND'), 'switch': pair(('U1', '3'), ('L1', '1'), 'SW')},
        {'id': 'cross-C2', 'metric': 'pad-centre-to-centre', 'supply': pair(('U1', '4'), ('C2', '1'), 'VOUT'),
         'return': pair(('U1', '2'), ('C2', '2'), 'GND')}]}


def policy(**changes):
    value = {'schema': 'agent-ee.packing-policy/1', 'step_budget': 20000, 'gap_nm': 250000,
             'fixed_refs': ['H1'], 'priority_refs': [], 'ground_nets': ['GND'], 'obstacles': [],
             'electrical_locality': obligations()}
    value.update(changes)
    return {k: v for k, v in value.items() if v is not None}


class Fixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-blocks-')
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.board = board()

    def inventory(self, raw=None):
        path = self.dir / 'design.xml'
        path.write_bytes(raw or native_xml())
        return read_native_inventory(path), read_sheet_names(path), hashlib.sha256(path.read_bytes()).hexdigest()

    def derive(self, allocation=None, capture=None, raw=None):
        inventory, names, sha = self.inventory(raw)
        raw_allocation = None if allocation is None else json.dumps(allocation).encode()
        return blocks.derive(inventory, names, capture or self.board, inventory_sha256=sha,
                             allocation_raw=raw_allocation)


def allocation(*entries, residual=None):
    document = {'schema': blocks.ALLOCATION, 'blocks': list(entries)}
    if residual is not None:
        document['residual'] = residual
    return document


class DerivationTests(Fixture, unittest.TestCase):
    def test_child_sheets_are_blocks_and_root_parts_residual(self):
        derived = self.derive()
        self.assertEqual([(b['id'], b['references']) for b in derived['blocks']],
                         [('Power', ['C1', 'C3', 'L1', 'U1']), ('Sensor', ['C2', 'R1', 'U2'])])
        self.assertEqual(derived['residual'], [{'reference': 'H1', 'reason': 'board-only'},
                                               {'reference': 'J1', 'reason': 'root-sheet'}])
        self.assertIsNone(derived['allocation_sha256'])
        self.assertEqual(derived['inventory_sha256'], hashlib.sha256(native_xml()).hexdigest())

    def test_allocation_splits_merges_and_is_hashed(self):
        document = allocation({'id': 'buck', 'references': ['U1', 'L1', 'C3'], 'anchor': 'U1'},
                              {'id': 'rest', 'sheets': ['/Sensor/'], 'references': ['C1', 'J1']})
        derived = self.derive(document)
        self.assertEqual([(b['id'], b['references']) for b in derived['blocks']],
                         [('buck', ['C3', 'L1', 'U1']), ('rest', ['C1', 'C2', 'J1', 'R1', 'U2'])])
        self.assertEqual(derived['blocks'][1]['sheets'], ['/s2/'])
        self.assertEqual(derived['allocation_sha256'], hashlib.sha256(json.dumps(document).encode()).hexdigest())
        self.assertEqual(derived['residual'], [{'reference': 'H1', 'reason': 'board-only'}])
        # A nested sheet is allocated on its own; its parent keeps the rest.
        derived = self.derive(allocation({'id': 'filter', 'sheets': ['/s2/s3/']}, {'id': 'sense', 'references': ['U2', 'C2']},
                                         {'id': 'power', 'sheets': ['/Power/']}))
        self.assertEqual([b['references'] for b in derived['blocks']], [['R1'], ['C2', 'U2'], ['C1', 'C3', 'L1', 'U1']])

    def test_ambiguity_refuses(self):
        power = {'id': 'power', 'sheets': ['/Power/']}
        sensor = {'id': 'sensor', 'sheets': ['/Sensor/']}
        cases = (
            (allocation(power, {'id': 'x', 'references': ['C1']}, sensor), 'C1: allocated to both block power and block x'),
            (allocation(power, sensor, {'id': 'x', 'sheets': ['/Sensor/Filter/']}), 'R1: allocated to both'),
            (allocation(power, sensor, residual=['C2']), 'C2: allocated to both block sensor and residual'),
            (allocation(power), 'neither allocated to a block nor declared residual: C2, R1, U2'),
            (allocation(power, {'id': 'power', 'sheets': ['/Sensor/']}), 'duplicate block id power'),
            (allocation(power, sensor, {'id': 'x', 'references': ['C9']}), 'C9 is not an on-board native component'),
            (allocation(power, sensor, {'id': 'x', 'sheets': ['/Other/']}), 'unknown child sheet /Other/'),
            (allocation(power, sensor, {'id': 'x', 'sheets': ['/']}), 'unknown child sheet /'),
            (allocation({'id': 'p', 'sheets': ['/Power/'], 'references': ['U1']}, sensor), 'U1 is allocated twice'),
            (allocation(dict(power, anchor='U2'), sensor), 'anchor must be a member'),
            (allocation({'id': 'x'}, sensor), 'needs sheets or references'),
            (allocation(dict(power, extra=1)), 'allocated block needs id'),
            (allocation(power, sensor, residual=['Q1']), 'residual Q1 is not a board footprint'),
            (allocation(power, sensor, {'id': 'x', 'references': ['H1']}), 'H1 is not an on-board native component'),
            (allocation(power, {'id': 's', 'sheets': ['/Sensor/', '/Sensor/Filter/']}),
             'R1 is allocated twice by overlapping sheets'),
            ({'schema': blocks.ALLOCATION, 'blocks': []}, 'nonempty blocks'),
        )
        for document, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.derive(document)
        inventory, names, sha = self.inventory()
        with self.assertRaisesRegex(ValueError, 'duplicate JSON field'):
            blocks.derive(inventory, names, self.board, inventory_sha256=sha,
                          allocation_raw=b'{"schema": "agent-ee.block-allocation/1", "blocks": [], "blocks": []}')

    def test_board_and_netlist_must_agree(self):
        changed = copy.deepcopy(self.board)
        next(f for f in changed['footprints'] if f['ref'] == 'C1')['fpid'] = 'Owned:C1206'
        with self.assertRaisesRegex(ValueError, 'C1: board footprint differs from the native netlist'):
            self.derive(capture=changed)
        changed = copy.deepcopy(self.board)
        changed['footprints'] = [f for f in changed['footprints'] if f['ref'] != 'R1']
        with self.assertRaisesRegex(ValueError, 'R1: on-board component has no board footprint'):
            self.derive(capture=changed)
        changed = copy.deepcopy(self.board)
        extra = footprint('TP1', 45, 5, 'Owned:TP', {'1': 'VIN'})
        changed['footprints'].append(extra)
        with self.assertRaisesRegex(ValueError, 'TP1: board footprint is not an on-board native component'):
            self.derive(capture=changed)
        changed = copy.deepcopy(self.board)
        next(f for f in changed['footprints'] if f['ref'] == 'L1')['locked'] = True
        with self.assertRaisesRegex(ValueError, 'locked footprints cannot be rigid-block members.*L1'):
            self.derive(capture=changed)
        # Allocating the locked part to the residual set admits it.
        derived = self.derive(allocation({'id': 'p', 'references': ['U1', 'C1', 'C3']}, {'id': 's', 'sheets': ['/Sensor/']},
                                         residual=['L1']), capture=changed)
        self.assertIn({'reference': 'L1', 'reason': 'allocated-residual'}, derived['residual'])

    def test_sheet_names_are_unique(self):
        path = self.dir / 'design.xml'
        path.write_bytes(native_xml(sheets={**SHEETS, '/s2/': '/Power/'}))
        with self.assertRaisesRegex(ValueError, 'ambiguous native sheet names'):
            read_sheet_names(path)


class PackingTests(Fixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.inv = self.inventory()[0]

    def pack(self, derived=None, block='Power', **changes):
        return blocks.pack_block(self.board, self.inv, derived or self.derive(), block, policy(**changes))

    def test_rigid_candidate(self):
        original = copy.deepcopy(self.board)
        result = self.pack()
        self.assertEqual(self.board, original)
        self.assertEqual(result['status'], 'packed', result.get('reason'))
        cand = blocks.validate_candidate(result['candidate'])
        self.assertEqual(cand['frame'], {'convention': blocks.FRAME, 'anchor': 'U1'})
        members = {m['reference']: m for m in cand['members']}
        self.assertEqual(set(members), {'U1', 'C1', 'L1', 'C3'})
        self.assertEqual(members['U1']['pose'], {'position_nm': [0, 0], 'rotation_deg': 0})
        self.assertEqual(cand['provenance']['class'], 'search-derived')
        self.assertEqual(cand['provenance']['enforcement'], 'advisory')
        # The two wholly internal obligations constrain and grade the block; the
        # one reaching C2 in another block is deferred to arrangement.
        self.assertEqual([o['id'] for o in cand['block_policy']['electrical_locality']['obligations']],
                         ['input-C1', 'output-C3'])
        self.assertEqual(cand['deferred']['obligations'], ['cross-C2'])
        grade = cand['grade']['electrical_locality']
        self.assertEqual(grade['status'], 'pass')
        self.assertEqual(len(grade['measured']), 5)
        self.assertEqual(cand['witness']['status'], 'pass')
        for side in cand['witness']['minimum'].values():
            self.assertGreaterEqual(side['distance_squared_nm2'][0], 250000 ** 2 * side['distance_squared_nm2'][1])
        # The outline holds every framed courtyard; pins carry functions and local roles.
        box = cand['outline']['F']['box_nm']
        for poly in cand['outline']['F']['courtyards']:
            for x, y in poly:
                self.assertTrue(box[0] <= x <= box[2] and box[1] <= y <= box[3])
        pins = {p['pad']: p for p in members['U1']['pins']}
        self.assertEqual(pins['3']['function'], {'name': 'SW', 'electrical_type': 'output'})
        self.assertFalse(pins['3']['boundary'])
        self.assertTrue(pins['1']['boundary'])
        self.assertEqual(pins['3']['role'], {p['pad']: p for p in members['L1']['pins']}['1']['role'])
        self.assertEqual(cand['sources']['board_sha256'], self.board['board_sha256'])
        self.assertEqual(cand['sources']['policy_sha256'], blocks.digest(policy()))
        # Identical inputs give an identical candidate, which survives JSON.
        self.assertEqual(self.pack(), result)
        self.assertEqual(json.loads(json.dumps(cand)), cand)

    def test_frame_is_anchor_relative_and_rigid(self):
        result = self.pack()['candidate']
        plan = {m['reference']: m['pose'] for m in result['members']}
        # Re-placing every member at its frame pose on a board reproduces the grade exactly.
        framed = [placer.transform(next(f for f in self.board['footprints'] if f['ref'] == r),
                                   plan[r]['position_nm'], plan[r]['rotation_deg']) for r in plan]
        regrade = electrical_locality.grade({'footprints': framed}, result['block_policy']['electrical_locality'])
        self.assertEqual(regrade['measured'], result['grade']['electrical_locality']['measured'])
        # A captured anchor rotation does not change the anchor's frame pose.
        turned = copy.deepcopy(self.board)
        anchor = next(f for f in turned['footprints'] if f['ref'] == 'U1')
        turned['footprints'][turned['footprints'].index(anchor)] = placer.transform(anchor, anchor['position_nm'], 90)
        for fp in turned['footprints']:
            for key in [k for k in fp if k.startswith('_')]:
                del fp[key]
        rotated = blocks.pack_block(turned, self.inv, self.derive(capture=turned), 'Power', policy())
        self.assertEqual(rotated['status'], 'packed', rotated.get('reason'))
        frame = {m['reference']: m['pose'] for m in rotated['candidate']['members']}
        self.assertEqual(frame['U1'], {'position_nm': [0, 0], 'rotation_deg': 0})
        self.assertEqual(rotated['candidate']['grade']['electrical_locality']['status'], 'pass')
        # The anchor's land and courtyard hashes are rotation-normalized.
        def hashes(cand):
            row = next(m for m in cand['members'] if m['reference'] == 'U1')
            return row['pad_geometry_sha256'], row['courtyard_sha256']
        self.assertEqual(hashes(rotated['candidate']), hashes(result))

    def test_unavailable_obligations_and_fixed_members_refuse(self):
        doc = obligations()
        del doc['obligations'][0]['supply']['citation']
        result = self.pack(electrical_locality=doc)
        self.assertEqual((result['status'], result['outcome']), ('electrical_locality_unavailable', 'unknown'))
        self.assertNotIn('candidate', result)
        with self.assertRaisesRegex(ValueError, 'fixed references cannot be rigid-block members: L1'):
            self.pack(fixed_refs=['H1', 'L1'])
        with self.assertRaisesRegex(ValueError, 'unknown block Nope'):
            self.pack(block='Nope')
        other = copy.deepcopy(self.board)
        other['board_sha256'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'another board capture'):
            blocks.pack_block(other, self.inv, self.derive(), 'Power', policy())
        bare = copy.deepcopy(self.board)
        next(f for f in bare['footprints'] if f['ref'] == 'C3')['courtyards'] = {}
        result = blocks.pack_block(bare, self.inv, self.derive(capture=bare), 'Power', policy())
        self.assertEqual((result['status'], result['outcome'], result['reason']),
                         ('unsupported_geometry', 'unknown', 'mounting-side courtyard missing: C3'))
        result = self.pack(electrical_locality=obligations(limit=100000))
        self.assertEqual(result['outcome'], 'unknown')
        self.assertNotEqual(result['status'], 'packed')

    def test_groups_are_internal_or_deferred(self):
        groups = [{'id': 'near', 'references': ['C1'], 'anchor': {'reference': 'U1'}, 'max_distance_nm': 4 * MM},
                  {'id': 'board', 'references': ['C3'], 'anchor': {'point_nm': [10 * MM, 10 * MM]}, 'max_distance_nm': 5 * MM},
                  {'id': 'cross', 'references': ['C2'], 'anchor': {'reference': 'U1'}, 'max_distance_nm': 5 * MM},
                  {'id': 'elsewhere', 'references': ['R1'], 'anchor': {'reference': 'U2'}, 'max_distance_nm': 5 * MM}]
        result = self.pack(groups=groups)
        self.assertEqual(result['status'], 'packed', result.get('reason'))
        cand = result['candidate']
        self.assertEqual([g['id'] for g in cand['block_policy']['groups']], ['near'])
        self.assertEqual(cand['deferred']['groups'], ['board', 'cross'])
        self.assertEqual(cand['grade']['groups']['status'], 'pass')

    def test_members_follow_a_placed_same_net_neighbour(self):
        # C3 reaches the anchor only through L1. Queued before L1 it would see no
        # placed peer on VO and rank to the synthetic plane's corner.
        parts = tuple(p if p[0] not in ('U1', 'L1', 'C3') else p[:5] + ({
            'U1': {'1': 'VIN', '2': 'GND', '3': 'SW', '4': 'FB'}, 'L1': {'1': 'SW', '2': 'VO'},
            'C3': {'1': 'VO', '2': 'GND'}}[p[0]],) for p in PARTS)
        self.board = board(parts)
        self.inv = self.inventory(native_xml(parts))[0]
        result = blocks.pack_block(self.board, self.inv, self.derive(raw=native_xml(parts)), 'Power',
                                   policy(electrical_locality=None))
        self.assertEqual(result['status'], 'packed', result.get('reason'))
        self.assertEqual(result['candidate']['block_policy']['priority_refs'], ['C1', 'L1', 'C3'])

    def test_board_priority_on_the_anchor_is_dropped(self):
        # AGENTEE-340: the pod's GNSS anchor U3 is a board priority. The anchor is fixed in
        # the block frame, so the candidate validator's "anchor not prioritized" rule holds.
        derived = self.derive()
        result = self.pack(derived, priority_refs=['U1', 'L1'])
        self.assertEqual(result['status'], 'packed', result.get('reason'))
        self.assertEqual(result['candidate']['block_policy']['priority_refs'][0], 'L1')
        self.assertNotIn('U1', result['candidate']['block_policy']['priority_refs'])
        self.assertEqual(blocks.admit(result['candidate'], self.board, self.inv, derived,
                                      policy(priority_refs=['U1', 'L1']))['status'], 'admitted')

    def test_witness_gap_boundary(self):
        def at(x):
            return placer.transform(footprint('B', 0, 0, 'Owned:C0805', {}), [x, 0], 0)
        left = footprint('A', 0, 0, 'Owned:C0805', {})
        exact = blocks._witness([left, at(2 * MM + 250000)], 250000)
        self.assertEqual(exact['status'], 'pass')
        self.assertEqual(exact['minimum']['F']['distance_squared_nm2'], [250000 ** 2, 1])
        short = blocks._witness([left, at(2 * MM + 249999)], 250000)
        self.assertEqual(short['status'], 'defect')
        self.assertEqual(short['below_gap'], [{'refs': ['A', 'B'], 'side': 'F', 'distance_squared_nm2': [249999 ** 2, 1]}])
        overlap = blocks._witness([left, at(MM)], 250000)
        self.assertEqual(overlap['below_gap'][0]['distance_squared_nm2'], [0, 1])

    def test_unconnected_member_reaching_synthetic_plane_is_unknown(self):
        parts = PARTS + (('C9', '/s1/', 'C', 'Owned:C0805', '1uF', {'1': 'GND', '2': 'GND'}),)
        self.board = board(parts)
        self.inv = self.inventory(native_xml(parts))[0]
        result = blocks.pack_block(self.board, self.inv, self.derive(raw=native_xml(parts)), 'Power', policy())
        self.assertEqual((result['status'], result['outcome']), ('synthetic_plane_reached', 'unknown'))
        self.assertIn('C9', result['reason'])
        self.assertNotIn('candidate', result)


# A buck anchor driving a seven-resistor chain: the first run strings it out (AGENTEE-355).
CHAIN = (('U1', '/s1/', 'BUCK', 'Owned:QFN4', 'owned-buck', {'1': 'N1', '2': 'GND', '3': 'M1', '4': 'FB'}),) + tuple(
    (f'R{i}', '/s1/', 'R', 'Owned:R0603', '10k', {'1': f'N{i}', '2': f'N{i + 1}'}) for i in range(1, 8)) + (
    ('J1', '/', 'CONN', 'Owned:HDR2', 'owned-conn', {'1': 'N8', '2': 'GND'}),)


class CompactionTests(Fixture, unittest.TestCase):
    """Stage-A compactness objective (AGENTEE-355)."""

    def chain(self, compact=True, **changes):
        self.board = board(CHAIN)
        raw = native_xml(CHAIN)
        self.inv = self.inventory(raw)[0]
        self.derived = self.derive(raw=raw)
        self.chain_policy = policy(electrical_locality=None, **changes)
        return blocks.pack_block(self.board, self.inv, self.derived, 'Power', self.chain_policy, compact=compact)

    def test_compaction_shrinks_the_outline_deterministically(self):
        loose = self.chain(compact=False)['candidate']
        tight = self.chain()['candidate']
        area = blocks._box_area
        self.assertNotIn('compaction', loose['search'])
        record = blocks.validate_candidate(tight)['search']['compaction']
        self.assertEqual(record['objective'], blocks.COMPACTION)
        self.assertEqual(record['first_box_area_nm2'], area(loose['outline']))
        self.assertEqual(record['box_area_nm2'], area(tight['outline']))
        self.assertLess(area(tight['outline']), area(loose['outline']))
        self.assertEqual(area(loose['outline']), 81375 * 10 ** 9)
        self.assertEqual(area(tight['outline']), 56250 * 10 ** 9)
        # The bisection both packs and fails, and every trial stays within the step cap.
        self.assertTrue({t['status'] for t in record['trials']} >= {'packed', 'packing_search_exhausted'})
        self.assertTrue(all(t['steps_used'] <= record['step_cap'] <= 20000 for t in record['trials']))
        self.assertEqual({t['family'] for t in record['trials']}, set(blocks.COMPACT_FAMILIES))
        self.assertEqual(record['steps_total'], loose['search']['steps_used'] + sum(
            t['steps_used'] for t in record['trials']))
        # The same isolated policy, witness and admission: nothing is loosened.
        self.assertEqual(tight['block_policy'], loose['block_policy'])
        self.assertEqual(tight['witness']['status'], 'pass')
        self.assertEqual(blocks.admit(tight, self.board, self.inv, self.derived, self.chain_policy)['status'],
                         'admitted')
        self.assertEqual(self.chain(), {'status': 'packed', 'outcome': 'info', 'candidate': tight})

    def test_compaction_keeps_every_internal_obligation(self):
        derived = self.derive()
        loose = blocks.pack_block(self.board, self.inventory()[0], derived, 'Power', policy(), compact=False)
        tight = blocks.pack_block(self.board, self.inventory()[0], derived, 'Power', policy())
        self.assertEqual(tight['candidate']['block_policy'], loose['candidate']['block_policy'])
        self.assertEqual(tight['candidate']['grade']['electrical_locality']['status'], 'pass')
        self.assertLessEqual(blocks._box_area(tight['candidate']['outline']),
                             blocks._box_area(loose['candidate']['outline']))
        # A tight internal group bounds every trial too: a candidate that packs still passes it.
        group = [{'id': 'near', 'references': ['R7'], 'anchor': {'reference': 'U1'}, 'max_distance_nm': 6 * MM}]
        grouped = self.chain(groups=group)['candidate']
        self.assertEqual(grouped['grade']['groups']['status'], 'pass')
        self.assertEqual(grouped['block_policy']['groups'], group)

    def test_compaction_record_is_validated(self):
        cand = self.chain()['candidate']
        cases = (
            (lambda r: r.update(objective='other/1'), 'compaction objective'),
            (lambda r: r.update(first_box_area_nm2=r['box_area_nm2'] - 1), "at most the first run's"),
            (lambda r: r['trials'][0].update(family='spiral'), 'trial family'),
            (lambda r: r['trials'][0].update(steps_used=r['step_cap'] + 1), 'within the step cap'),
            (lambda r: r['trials'][0].pop('box_area_nm2'), 'exactly when it packed'),
            (lambda r: r['trials'].extend([r['trials'][0]] * 12), 'at most 12'),
            (lambda r: r.update(extra=1), 'unknown extra'),
            (lambda r: r.update(steps_total=-1), 'nonnegative'),
        )
        for mutate, message in cases:
            doc = copy.deepcopy(cand)
            mutate(doc['search']['compaction'])
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                blocks.validate_candidate(doc)
        # Admission binds the recorded area to the board's own outline.
        doc = copy.deepcopy(cand)
        doc['search']['compaction']['box_area_nm2'] -= 1
        blocks.validate_candidate(doc)
        self.assertEqual(blocks.admit(doc, self.board, self.inv, self.derived, self.chain_policy)['problems'],
                         [{'kind': 'stale_evidence', 'evidence': 'compaction'}])


class CandidateSchemaTests(Fixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.inv = self.inventory()[0]
        self.cand = blocks.pack_block(self.board, self.inv, self.derive(), 'Power', policy())['candidate']

    def test_provenance_classes(self):
        for name, enforcement in blocks.PROVENANCE.items():
            doc = copy.deepcopy(self.cand)
            doc['provenance'] = {'class': name, 'enforcement': enforcement, 'citation': 'Owned example layout'}
            if name != 'search-derived':
                with self.assertRaisesRegex(ValueError, 'captured source locator and sha256'):
                    blocks.validate_candidate(doc)
                doc['provenance']['source'] = {'locator': 'owned/example.kicad_pcb', 'sha256': 'c' * 64}
            blocks.validate_candidate(doc)
        doc = copy.deepcopy(self.cand)
        doc['provenance']['enforcement'] = 'hard'
        with self.assertRaisesRegex(ValueError, 'known class and its enforcement'):
            blocks.validate_candidate(doc)
        doc['provenance'] = {'class': 'owner', 'enforcement': 'hard', 'citation': 'x'}
        with self.assertRaisesRegex(ValueError, 'known class'):
            blocks.validate_candidate(doc)

    def test_structure_refusals(self):
        def broken(change):
            doc = copy.deepcopy(self.cand)
            change(doc)
            return doc
        anchor = next(i for i, m in enumerate(self.cand['members']) if m['reference'] == 'U1')
        other = next(i for i, m in enumerate(self.cand['members']) if m['reference'] != 'U1')
        cases = (
            (lambda d: d['members'][anchor]['pose'].update(position_nm=[1, 0]), 'anchor defines the block frame'),
            (lambda d: d['members'][other]['pose'].update(rotation_deg=45), 'quarter-turn rotation'),
            (lambda d: d['members'][other]['pose'].update(position_nm=[0.5, 0]), 'integer nanometres'),
            (lambda d: d['members'].append(copy.deepcopy(d['members'][0])), 'unique references'),
            (lambda d: d['members'][other].update(pad_geometry_sha256='x'), 'pad_geometry_sha256'),
            (lambda d: d['members'][other].update(value={'text': '1uF', 'rule': 'tolerance'}), 'citation'),
            (lambda d: d.update(status='refused'), 'only a packed block candidate'),
            (lambda d: d['frame'].update(convention='other'), 'block frame must be'),
            (lambda d: d.pop('outline'), 'missing outline'),
            (lambda d: d.update(schema='agent-ee.block-candidate/1'), 'expected agent-ee.block-candidate/2'),
        )
        for change, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                blocks.validate_candidate(broken(change))


    def test_missing_failed_or_stale_evidence_refuses(self):
        """Review H1: every evidence field is required, typed and must pass; poses bind the witness."""
        anchor = next(i for i, m in enumerate(self.cand['members']) if m['reference'] == 'U1')
        other = next(i for i, m in enumerate(self.cand['members']) if m['reference'] == 'C1')
        def pin(d, **changes):
            d['members'][anchor]['pins'][0].update(changes)
        cases = [(lambda d, k=key: d.pop(k), 'missing ' + key)
                 for key in ('grade', 'witness', 'deferred', 'block_policy', 'placer_judgment', 'search', 'sources')]
        cases += [
            (lambda d: d.update(sources={'board_sha256': d['sources']['board_sha256']}), 'missing allocation_sha256'),
            (lambda d: d['sources'].update(policy_sha256=None), 'policy sha256'),
            (lambda d: d.update(extra=1), 'unknown extra'),
            (lambda d: d['members'][other].pop('courtyard'), 'missing courtyard'),
            (lambda d: d['witness'].update(status='defect'), 'witness must pass'),
            (lambda d: d['witness'].update(status='unknown'), 'witness must pass'),
            (lambda d: d['witness'].update(drilled_pad_conflicts=[['U1', 'C1']]), 'witness must pass'),
            (lambda d: d['witness']['minimum']['F'].update(distance_squared_nm2=[1, 0]), 'positive denominator'),
            (lambda d: d['witness']['minimum']['F'].update(distance_squared_nm2=[1.5, 1]), 'exact'),
            (lambda d: d['witness']['minimum']['F'].update(distance_squared_nm2=[1, 1]), 'below the gap'),
            (lambda d: d['witness'].update(pairs_checked=1), 'witness differs'),
            (lambda d: d['witness'].update(gap_nm=0), 'block policy gap'),
            (lambda d: d['grade']['electrical_locality'].update(status='defect'), 'must pass'),
            (lambda d: d['grade']['electrical_locality'].update(status='unknown'), 'must pass'),
            (lambda d: d['grade']['electrical_locality']['unknown'].append({'kind': 'x'}), 'no defects or unknowns'),
            (lambda d: d['grade']['electrical_locality']['measured'].pop(), 'measure every internal pair'),
            (lambda d: d['grade']['electrical_locality']['measured'][0].update(distance_squared_nm=10 ** 15),
             'exceeds its limit'),
            (lambda d: d['grade'].update(electrical_locality=None), 'must pass'),
            (lambda d: d['grade'].update(groups={'status': 'pass'}), 'must be null'),
            (lambda d: d.update(grade=[]), 'block grade must be an object'),
            (lambda d: d['placer_judgment'].update(verdict='defect'), 'placer judgment must pass'),
            (lambda d: d['deferred'].update(obligations='cross-C2'), 'deferred obligations'),
            (lambda d: d['deferred'].update(obligations=['cross-C2', 'input-C1']), 'cannot also be deferred'),
            (lambda d: d['deferred'].pop('obligations'), 'missing obligations'),
            (lambda d: d['deferred'].update(obstacles=-1), 'nonnegative'),
            (lambda d: d['search'].update(steps_used=10 ** 6), 'within the block policy budget'),
            (lambda d: d['block_policy'].update(fixed_refs=[]), 'fixes exactly the anchor'),
            (lambda d: d['block_policy']['electrical_locality']['obligations'].append(obligations()['obligations'][2]),
             'wholly inside the block'),
            (lambda d: [m['pose'].update(position_nm=[0, 0]) for m in d['members']], 'outline differs'),
            (lambda d: d['members'][other]['pose'].update(rotation_deg=(d['members'][other]['pose']['rotation_deg']
                                                                         + 90) % 360), 'differs'),
            (lambda d: d['outline']['F'].update(box_nm=[0, 0, 0, 0]), 'outline differs'),
            (lambda d: d['outline']['F']['courtyards'].pop(), 'outline differs'),
            (lambda d: d['members'][other]['courtyard']['F']['polygons'][0].reverse(), 'courtyard_sha256'),
            (lambda d: d['members'][anchor]['pins'].append(copy.deepcopy(d['members'][anchor]['pins'][0])),
             'listed twice'),
            (lambda d: pin(d, role='n9'), 'one role'),
            (lambda d: pin(d, boundary='yes'), 'boolean'),
            (lambda d: pin(d, function='VIN'), 'function must be an object'),
            (lambda d: pin(d, extra=1), 'unknown extra'),
            (lambda d: d['members'][anchor].update(pins='none'), 'member pins'),
            (lambda d: d['provenance'].update(source={'locator': 'x', 'sha256': 'c' * 64}), 'no reference source'),
            (lambda d: d['provenance'].update(note='x'), 'unknown note'),
        ]
        for change, message in cases:
            doc = copy.deepcopy(self.cand)
            change(doc)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                blocks.validate_candidate(doc)
            with self.subTest(message=message, via='match'), self.assertRaises(ValueError):
                blocks.match(doc, self.board, self.inv)
        # A plain JSON round trip and a null allocation hash stay valid.
        blocks.validate_candidate(json.loads(json.dumps(self.cand)))
        self.assertIsNone(self.cand['sources']['allocation_sha256'])

    def test_admission_binds_sources_policy_and_board_evidence(self):
        derived, board_policy = self.derive(), policy()
        self.assertEqual(blocks.admit(self.cand, self.board, self.inv, derived, board_policy),
                         {'status': 'admitted', 'problems': []})
        def kinds(doc, capture=None, pol=None):
            result = blocks.admit(doc, capture or self.board, self.inv, derived, pol or board_policy)
            self.assertEqual(result['status'], 'refused')
            return sorted({p['kind'] for p in result['problems']})
        # The boundary obligation cannot be dropped from the record, and an internal
        # obligation cannot be removed from the policy and grade together.
        doc = copy.deepcopy(self.cand)
        doc['deferred']['obligations'] = []
        blocks.validate_candidate(doc)
        self.assertEqual(kinds(doc), ['deferred_mismatch'])
        doc = copy.deepcopy(self.cand)
        doc['block_policy']['electrical_locality']['obligations'].pop(0)
        grade = doc['grade']['electrical_locality']
        grade['declaration'] = doc['block_policy']['electrical_locality']
        grade['measured'] = [m for m in grade['measured'] if m['obligation'] != 'input-C1']
        blocks.validate_candidate(doc)
        self.assertEqual(kinds(doc), ['block_policy_mismatch'])
        # A moved member with a self-consistent outline and witness keeps stale grades.
        doc = copy.deepcopy(self.cand)
        row = next(m for m in doc['members'] if m['reference'] == 'C1')
        row['pose']['position_nm'][0] += 20 * 10 ** 6
        framed = [blocks._framed(r) for r in doc['members']]
        doc['outline'] = blocks._plain(blocks._outline(framed))
        doc['witness'] = blocks._plain(blocks._witness(framed, doc['witness']['gap_nm']))
        blocks.validate_candidate(doc)
        self.assertEqual(kinds(doc), ['evidence_failed', 'stale_evidence'])
        # Sources bind the board, its policy and derivation.
        self.assertEqual(kinds(self.cand, pol=policy(gap_nm=300000)), ['source_mismatch'])
        doc = copy.deepcopy(self.cand)
        doc['sources']['derivation_sha256'] = 'e' * 64
        self.assertEqual(kinds(doc), ['source_mismatch'])
        # Land geometry is bound through identity matching, kept separate from grading.
        changed = copy.deepcopy(self.board)
        next(f for f in changed['footprints'] if f['ref'] == 'C1')['pads'][0]['size_nm'] = [500000, 400000]
        self.assertEqual(kinds(self.cand, capture=changed), ['identity'])


class MatchingTests(Fixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.inv = self.inventory()[0]
        self.cand = blocks.pack_block(self.board, self.inv, self.derive(), 'Power', policy())['candidate']

    def kinds(self, result):
        return sorted({m['kind'] for m in result['mismatches']})

    def second_instance(self, change=lambda parts: parts):
        """A second buck on its own sheet, with its own references and net names."""
        extra = [('U11', '/s4/', 'BUCK', 'Owned:QFN4', 'owned-buck', {'1': 'VIN', '2': 'GND', '3': 'SW2', '4': 'V2'}),
                 ('C11', '/s4/', 'C', 'Owned:C0805', '4.7uF', {'1': 'VIN', '2': 'GND'}),
                 ('L11', '/s4/', 'L', 'Owned:L1210', '10uH', {'1': 'SW2', '2': 'V2'}),
                 ('C13', '/s4/', 'C', 'Owned:C0805', '22uF', {'1': 'V2', '2': 'GND'}),
                 ('R9', '/', 'R', 'Owned:R0603', '1k', {'1': 'V2', '2': 'GND'})]
        parts = PARTS + tuple(change([list(p) for p in extra]))
        raw = native_xml(parts, sheets={**SHEETS, '/s4/': '/Power2/'})
        return board(parts), self.inventory(raw)[0]

    MAPPING = {'U1': 'U11', 'C1': 'C11', 'L1': 'L11', 'C3': 'C13'}

    def test_identity_and_second_instance_match(self):
        self.assertEqual(blocks.match(self.cand, self.board, self.inv), {'status': 'match', 'mismatches': []})
        target, inventory = self.second_instance()
        self.assertEqual(blocks.match(self.cand, target, inventory, self.MAPPING)['status'], 'match')

    def test_mismatches_refuse(self):
        def variant(index, **changes):
            def change(parts):
                row = parts[index]
                for key, value in changes.items():
                    row[{'fpid': 3, 'value': 4, 'pads': 5, 'lib': 2}[key]] = value
                return parts
            return change
        cases = (
            (variant(2, fpid='Owned:L1212'), ['fpid']),
            (variant(3, value='10uF'), ['value']),
            # SW2 and V2 swapped on the inductor: same nets, wrong pins; the
            # internal switch role now reaches R9 outside the block.
            (variant(2, pads={'1': 'V2', '2': 'SW2'}), ['net_role', 'net_role_merged', 'net_role_split']),
            # The output cap's positive pin on VIN instead of the output rail.
            (variant(3, pads={'1': 'VIN', '2': 'GND'}), ['net_role_merged', 'net_role_split']),
            (variant(0, lib='BUCK2'), ['pin_function']),
        )
        for change, kinds in cases:
            with self.subTest(kinds=kinds):
                target, inventory = self.second_instance(change)
                result = blocks.match(self.cand, target, inventory, self.MAPPING)
                self.assertEqual(result['status'], 'refused')
                self.assertEqual(self.kinds(result), kinds)
        # The internal switch net leaving the block changes its boundary role.
        target, inventory = self.second_instance(lambda parts: parts[:4] + [
            ['R9', '/', 'R', 'Owned:R0603', '1k', {'1': 'SW2', '2': 'GND'}]])
        result = blocks.match(self.cand, target, inventory, self.MAPPING)
        self.assertEqual(self.kinds(result), ['net_role'])
        # Land geometry and courtyard differences are hashed facts.
        target, inventory = self.second_instance()
        fp = next(f for f in target['footprints'] if f['ref'] == 'C11')
        fp['pads'][0]['size_nm'] = [500000, 400000]
        fp['courtyards']['F']['polygons'][0][2][0] += 1
        fp['courtyards']['F']['polygons'][0][1][0] += 1
        self.assertEqual(self.kinds(blocks.match(self.cand, target, inventory, self.MAPPING)),
                         ['courtyard', 'pad_geometry'])
        flipped, _ = self.second_instance()
        fp = next(f for f in flipped['footprints'] if f['ref'] == 'C11')
        fp['side'] = 'B'
        self.assertIn('side', self.kinds(blocks.match(self.cand, flipped, inventory, self.MAPPING)))
        with self.assertRaisesRegex(ValueError, 'distinct target reference'):
            blocks.match(self.cand, target, inventory, {**self.MAPPING, 'C3': 'C11'})
        missing = blocks.match(self.cand, target, inventory, {**self.MAPPING, 'C3': 'C99'})
        self.assertEqual(self.kinds(missing), ['target_missing'])

    def test_declared_value_tolerance(self):
        doc = copy.deepcopy(self.cand)
        row = next(m for m in doc['members'] if m['reference'] == 'C3')
        row['value'] = {'text': '22uF', 'rule': 'tolerance', 'relative_tolerance': [1, 10],
                        'citation': 'Owned example: output capacitance within 10 percent.'}
        for value, status in (('22uF', 'match'), ('24.2uF', 'match'), ('24.3uF', 'refused'), ('22uH', 'refused'),
                              ('22 uF/25V', 'refused'), ('19.8uF', 'match'), ('19.7uF', 'refused')):
            with self.subTest(value=value):
                target, inventory = self.second_instance(lambda parts: [
                    p if p[0] != 'C13' else p[:4] + [value] + p[5:] for p in parts])
                self.assertEqual(blocks.match(doc, target, inventory, self.MAPPING)['status'], status)


class NativeHierarchyTests(unittest.TestCase):
    """Blocks from an actual sandboxed KiCad XML export of the AGENTEE-46 owned hierarchy."""

    def test_native_child_sheets_are_blocks(self):
        from agent_ee.executor import execute
        from block_first_fixture import fixture
        from toolchain import require_toolchain
        require_toolchain(native=True)
        with tempfile.TemporaryDirectory(prefix='agent-ee-blocks-native-') as tmp:
            root = Path(tmp)
            fixture(root)
            native = execute(root, root / 'native', ['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                             '-o', '/work/design.xml', '/candidate/project/top.kicad_sch'], timeout=120)
            self.assertEqual((native['execution'], native['exit_code']), ('ok', 0), native)
            path = root / 'native/design.xml'
            inventory, names = read_native_inventory(path), read_sheet_names(path)
        self.assertEqual(sorted(names.values()), ['/', '/block1/', '/block2/'])
        fps = []
        for i, ref in enumerate(sorted(inventory['components'])):
            fp = footprint(ref, 3 + 5 * i, 3, inventory['components'][ref]['footprint'],
                           {'1': f'/{ref[-1]}-1', '2': f'/{ref[-1]}-2'})
            fps.append(fp)
        derived = blocks.derive(inventory, names, capture(fps), inventory_sha256='d' * 64)
        self.assertEqual([(b['id'], b['references']) for b in derived['blocks']],
                         [('block1', ['R1', 'U1']), ('block2', ['R2', 'U2'])])
        self.assertEqual(derived['residual'], [])
        with self.assertRaisesRegex(ValueError, 'neither allocated to a block nor declared residual: R2, U2'):
            blocks.derive(inventory, names, capture(fps), inventory_sha256='d' * 64, allocation_raw=json.dumps(
                allocation({'id': 'one', 'sheets': ['/block1/']})).encode())


class CliTests(Fixture, unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'agent_ee.blocks', *map(str, args)], capture_output=True,
                              text=True, env={'PYTHONPATH': str(ROOT / 'src')}, timeout=120)

    def test_derive_pack_match(self):
        (self.dir / 'probe.json').write_text(json.dumps(self.board))
        (self.dir / 'design.xml').write_bytes(native_xml())
        (self.dir / 'policy.json').write_text(json.dumps(policy()))
        common = ('--probe', self.dir / 'probe.json', '--netlist', self.dir / 'design.xml')
        done = self.run_cli('derive', *common, '--out', self.dir / 'derived.json')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads((self.dir / 'derived.json').read_text())['blocks'][0]['id'], 'Power')
        done = self.run_cli('pack', *common, '--policy', self.dir / 'policy.json', '--block', 'Power',
                            '--out', self.dir / 'power.json')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout.splitlines()[-1])['status'], 'packed')
        self.assertIn('"status": "qualifying"', done.stderr)
        cand = json.loads((self.dir / 'power.json').read_text())
        blocks.validate_candidate(cand)
        self.assertIn('compaction', cand['search'])
        done = self.run_cli('pack', *common, '--policy', self.dir / 'policy.json', '--block', 'Power', '--no-compact',
                            '--out', self.dir / 'loose.json')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn('compaction', json.loads((self.dir / 'loose.json').read_text())['search'])
        done = self.run_cli('match', *common, '--candidate', self.dir / 'power.json', '--out', self.dir / 'match.json')
        self.assertEqual(done.returncode, 0, done.stderr)
        done = self.run_cli('admit', *common, '--policy', self.dir / 'policy.json', '--candidate', self.dir / 'power.json',
                            '--out', self.dir / 'admit.json')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads((self.dir / 'admit.json').read_text()), {'status': 'admitted', 'problems': []})
        (self.dir / 'other.json').write_text(json.dumps(policy(gap_nm=300000)))
        done = self.run_cli('admit', *common, '--policy', self.dir / 'other.json', '--candidate', self.dir / 'power.json',
                            '--out', self.dir / 'refused.json')
        self.assertEqual((done.returncode, json.loads(done.stdout)['status']), (1, 'refused'))
        done = self.run_cli('derive', *common, '--out', self.dir / 'derived.json')
        self.assertEqual(done.returncode, 1)
        (self.dir / 'bad.json').write_text(json.dumps(allocation({'id': 'p', 'sheets': ['/Power/']})))
        done = self.run_cli('derive', *common, '--allocation', self.dir / 'bad.json', '--out', self.dir / 'x.json')
        self.assertEqual(done.returncode, 1)
        self.assertEqual(json.loads(done.stdout)['status'], 'refused')
        self.assertFalse((self.dir / 'x.json').exists())


if __name__ == '__main__':
    unittest.main()
