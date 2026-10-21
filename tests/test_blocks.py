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
from unittest import mock
from pathlib import Path
from xml.etree import ElementTree as ET

from agent_ee import blocks, electrical_locality, placement_groups, placer
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


FIXTURE_DECISION = 'Original fixture author: EE339 parameters define numerical locality requirements.'
FIXTURE_SOURCES = {'fixture-decisions/EE339.txt': FIXTURE_DECISION}


def obligations(limit=6 * MM):
    def pair(a, b, net, cite='Owned fixture requirement EE339: authored test limit.'):
        return {'from': dict(zip(('reference', 'pad', 'net'), a + (net,))),
                'to': dict(zip(('reference', 'pad', 'net'), b + (net,))), 'max_distance_nm': limit, 'citation': cite,
                'provenance': {'class': 'owner', 'basis': 'Explicit original fixture requirement',
                    'decision_reference': {'path': 'fixture-decisions/EE339.txt',
                        'sha256': hashlib.sha256(FIXTURE_DECISION.encode()).hexdigest(),
                        'locator': 'EE339 test parameter contract', 'quote': FIXTURE_DECISION,
                        'owner': 'Original fixture author'}}}
    return {'schema': electrical_locality.SCHEMA, 'source_records': FIXTURE_SOURCES, 'obligations': [
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

    def test_advisory_only_packs_roundtrips_and_admits(self):
        doc = obligations(limit=100000)
        doc.pop('source_records')
        del doc['obligations'][0]['supply']['citation']
        pol = policy(electrical_locality=doc)
        derived = self.derive()
        result = self.pack(electrical_locality=doc)
        self.assertEqual(result['status'], 'packed', result)
        cand = json.loads(json.dumps(result['candidate']))
        blocks.validate_candidate(cand)
        grade = cand['grade']['electrical_locality']
        self.assertEqual(grade['status'], 'info')
        self.assertEqual(len(grade['advisories']), 5)
        self.assertTrue(all(r['exceeded'] for r in grade['advisories']))
        self.assertTrue(all('below_floor' in r for r in grade['advisories']))
        self.assertEqual(blocks.admit(cand, self.board, self.inv, derived, pol),
                         {'status': 'admitted', 'problems': []})
        for change in (lambda g: g.update(status='pass'),
                       lambda g: g['advisories'].pop(),
                       lambda g: g['advisories'][0].update(exceeded=False)):
            forged = copy.deepcopy(cand)
            change(forged['grade']['electrical_locality'])
            with self.assertRaisesRegex(ValueError, 'every advisory'):
                blocks.validate_candidate(forged)

    def test_advisory_below_manufacturing_floor_is_retained(self):
        # Original, non-overlapping lands at 0.5 mm pitch establish a positive
        # necessary copper floor while the authored courtyards stay unchanged.
        for fp in self.board['footprints']:
            for i, pad in enumerate(fp['pads']):
                pad['position_nm'] = [fp['position_nm'][0] + i * 500000, fp['position_nm'][1]]
        doc = obligations(limit=1)
        doc.pop('source_records')
        pol = policy(electrical_locality=doc)
        result = self.pack(electrical_locality=doc)
        self.assertEqual(result['status'], 'packed', result)
        cand = json.loads(json.dumps(result['candidate']))
        self.assertTrue(any(r['below_floor'] for r in cand['grade']['electrical_locality']['advisories']))
        self.assertEqual(blocks.admit(cand, self.board, self.inv, self.derive(), pol),
                         {'status': 'admitted', 'problems': []})
        row = next(r for r in cand['grade']['electrical_locality']['advisories'] if r['below_floor'])
        row['below_floor'] = False
        with self.assertRaisesRegex(ValueError, 'every advisory'):
            blocks.validate_candidate(cand)

    def test_mixed_hard_advisory_roundtrip_and_admission(self):
        doc = obligations()
        soft = doc['obligations'][0]['supply']
        soft.pop('provenance')
        soft['max_distance_nm'] = 1
        pol = policy(electrical_locality=doc)
        result = self.pack(electrical_locality=doc)
        self.assertEqual(result['status'], 'packed', result)
        cand = json.loads(json.dumps(result['candidate']))
        internal = cand['block_policy']['electrical_locality']
        self.assertEqual(internal['source_records'], doc['source_records'])
        self.assertEqual(internal['obligations'], doc['obligations'][:2])
        grade = cand['grade']['electrical_locality']
        self.assertEqual(grade['status'], 'info')
        self.assertEqual(len(grade['advisories']), 1)
        self.assertTrue(grade['advisories'][0]['exceeded'])
        for row in grade['measured']:
            if row['constraint_provenance']['enforcement'] == 'hard':
                self.assertLessEqual(blocks._squared(row, 'distance'), row['max_distance_nm'] ** 2)
        self.assertEqual(blocks.admit(cand, self.board, self.inv, self.derive(), pol),
                         {'status': 'admitted', 'problems': []})
        # The same impossible bound still refuses when it has the authored source.
        doc['obligations'][0]['supply'] = obligations(limit=1)['obligations'][0]['supply']
        self.assertNotEqual(self.pack(electrical_locality=doc)['status'], 'packed')

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

    def test_stored_group_grade_must_pass_never_info(self):
        # AGENTEE-361 H1: an exceeded unsourced group grades info; stripping or falsifying its
        # advisories must not make the stored candidate usable. Legacy pass without advisories is.
        groups = [{'id': 'near', 'references': ['C1'], 'anchor': {'reference': 'U1'}, 'max_distance_nm': 4 * MM}]
        result = self.pack(groups=groups)
        self.assertEqual(result['status'], 'packed', result.get('reason'))
        legacy = copy.deepcopy(result['candidate'])
        del legacy['grade']['groups']['advisories']
        blocks.validate_candidate(legacy)
        cand = result['candidate']
        cand['block_policy']['groups'][0]['max_distance_nm'] = 1
        framed = [blocks._framed(row) for row in cand['members']]
        cand['grade']['groups'] = placement_groups.grade({'footprints': framed}, cand['block_policy']['groups'])
        self.assertEqual(cand['grade']['groups']['status'], 'info')
        self.assertEqual([a['exceeded'] for a in cand['grade']['groups']['advisories']], [True])
        for name, forge in (('intact', lambda g: None),
                            ('missing', lambda g: g.pop('advisories')),
                            ('empty', lambda g: g.update(advisories=[])),
                            ('falsified', lambda g: g['advisories'][0].update(exceeded=False))):
            with self.subTest(advisories=name):
                forged = copy.deepcopy(cand)
                forge(forged['grade']['groups'])
                with self.assertRaisesRegex(ValueError, 'block grade groups must pass'):
                    blocks.validate_candidate(forged)

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



class RecoveryTests(Fixture, unittest.TestCase):
    """Stage-A recovery of members that reach the anchor only through plane nets (AGENTEE-359)."""

    def stranded(self, nets, **changes):
        # C9's VX leaves the block (to a fixed part, say); its only in-block tie is the GND plane.
        parts = PARTS + (('C9', '/s1/', 'C', 'Owned:C0805', '1uF', nets),)
        self.board = board(parts)
        raw = native_xml(parts)
        self.inv = self.inventory(raw)[0]
        self.derived = self.derive(raw=raw)
        self.stranded_policy = policy(**changes)
        return blocks.pack_block(self.board, self.inv, self.derived, 'Power', self.stranded_policy, compact=False)

    def test_plane_only_member_is_recovered_and_graded_on_the_unchanged_policy(self):
        result = self.stranded({'1': 'VX', '2': 'GND'})
        self.assertEqual((result['status'], result['outcome']), ('packed', 'info'), result.get('reason'))
        cand = result['candidate']
        recovery = cand['search']['recovery']
        self.assertEqual((recovery['method'], recovery['status'], recovery['stranded']),
                         (blocks.RECOVERY, 'recovered', ['C9']))
        self.assertEqual(recovery['trials'][-1]['status'], 'recovered')
        self.assertLessEqual(len(recovery['trials']), blocks.RECOVERY_TRIALS)
        # The temporary group is removed before grading: the policy and grades are the original ones.
        self.assertNotIn('groups', cand['block_policy'])
        self.assertIsNone(cand['grade']['groups'])
        self.assertEqual(cand['grade']['electrical_locality']['status'], 'pass')
        self.assertEqual(cand['witness']['status'], 'pass')
        pose = next(m['pose'] for m in cand['members'] if m['reference'] == 'C9')
        self.assertLessEqual(pose['position_nm'][0] ** 2 + pose['position_nm'][1] ** 2,
                             recovery['max_distance_nm'] ** 2)
        blocks.validate_candidate(cand)
        self.assertEqual(blocks.admit(cand, self.board, self.inv, self.derived, self.stranded_policy)['status'],
                         'admitted')
        # Deterministic, and compaction keeps the recovery log on the run it selects.
        self.assertEqual(self.stranded({'1': 'VX', '2': 'GND'}), result)
        compacted = blocks.pack_block(self.board, self.inv, self.derived, 'Power', self.stranded_policy)
        self.assertEqual(compacted['status'], 'packed')
        self.assertEqual(compacted['candidate']['search']['recovery'], recovery)
        self.assertIn('compaction', compacted['candidate']['search'])

    def test_a_first_plan_inside_the_plane_runs_no_recovery(self):
        result = blocks.pack_block(self.board, self.inventory()[0], self.derive(), 'Power', policy(), compact=False)
        self.assertEqual(result['status'], 'packed')
        self.assertNotIn('recovery', result['candidate']['search'])

    def test_declared_group_member_stays_unknown(self):
        group = {'id': 'owner', 'references': ['C9'], 'anchor': {'reference': 'U1'}, 'max_distance_nm': 10 ** 9}
        result = self.stranded({'1': 'VX', '2': 'GND'}, groups=[group])
        self.assertEqual((result['status'], result['outcome']), ('synthetic_plane_reached', 'unknown'))
        self.assertNotIn('candidate', result)
        self.assertEqual(result['recovery']['status'], 'not_applicable')
        self.assertIn('C9', result['recovery']['reason'])

    def test_single_net_member_is_not_recovered(self):
        result = self.stranded({'1': 'GND', '2': 'GND'})
        self.assertEqual((result['status'], result['outcome']), ('synthetic_plane_reached', 'unknown'))
        self.assertEqual((result['recovery']['status'], result['recovery']['trials']), ('not_applicable', []))

    def test_exhaustion_is_bounded_and_unknown(self):
        real = blocks._touching
        calls = []

        def always(board, members, plan, plane):
            calls.append(plan)
            return real(board, members, plan, plane) if len(calls) == 1 else ['C9']
        with mock.patch.object(blocks, '_touching', always):
            result = self.stranded({'1': 'VX', '2': 'GND'})
        self.assertEqual((result['status'], result['outcome']), ('synthetic_plane_reached', 'unknown'))
        self.assertNotIn('candidate', result)
        trials = result['recovery']['trials']
        self.assertEqual((result['recovery']['status'], len(trials)), ('exhausted', blocks.RECOVERY_TRIALS))
        distances = [t['max_distance_nm'] for t in trials]
        self.assertEqual(distances, [distances[0] >> i for i in range(blocks.RECOVERY_TRIALS)])
        self.assertTrue(all(t['status'] == 'synthetic_plane_reached' for t in trials))
        self.assertTrue(all(t['steps_used'] <= self.stranded_policy['step_budget'] for t in trials))

    def test_recovery_record_is_validated(self):
        cand = self.stranded({'1': 'VX', '2': 'GND'})['candidate']

        def refuses(change, text):
            bad = copy.deepcopy(cand)
            change(bad)
            with self.assertRaisesRegex(ValueError, text):
                blocks.validate_candidate(bad)
        record = cand['search']['recovery']
        refuses(lambda c: c['search']['recovery'].update(status='exhausted'), 'recovered')
        refuses(lambda c: c['search']['recovery'].update(steps_total=record['steps_total'] + 1), 'sum of its trials')
        refuses(lambda c: c['search']['recovery'].update(stranded=['U1']), 'movable block members')
        refuses(lambda c: c['search']['recovery'].update(max_distance_nm=record['max_distance_nm'] + 1),
                'last trial')
        refuses(lambda c: c['search']['recovery'].update(
            trials=[dict(record['trials'][-1], status='synthetic_plane_reached')] * blocks.RECOVERY_TRIALS
            + record['trials']), 'list of 1 to')
        refuses(lambda c: c['search']['recovery']['trials'][-1].update(
            steps_used=cand['block_policy']['step_budget'] + 1), 'step budget')
        refuses(lambda c: c['search']['recovery'].pop('stranded'), 'missing stranded')

        def leaked(c):
            c['block_policy']['groups'] = [{'id': blocks.RECOVERY_GROUP, 'references': ['C9'],
                                            'anchor': {'reference': 'U1'}, 'max_distance_nm': 1}]
        refuses(leaked, 'never reaches the block policy|block grade groups')

    def refused(self, cand, mutate, text):
        # A malformed recovery history refuses through validation and so through admission.
        bad = copy.deepcopy(cand)
        mutate(bad['search']['recovery'])
        with self.assertRaisesRegex(ValueError, text):
            blocks.validate_candidate(bad)
        with self.assertRaisesRegex(ValueError, text):
            blocks.admit(bad, self.board, self.inv, self.derived, self.stranded_policy)

    def test_every_recovery_trial_is_validated(self):
        cand = self.stranded({'1': 'VX', '2': 'GND'})['candidate']
        record = cand['search']['recovery']
        self.assertEqual(len(record['trials']), 1)
        start = record['trials'][0]['max_distance_nm']

        def before(**row):
            return lambda r: r['trials'].insert(0, {'references': ['C9'], 'max_distance_nm': 2 * start,
                                                    'status': 'synthetic_plane_reached', 'steps_used': 0, **row})
        # The reviewer's adversarial set (AGENTEE-359 H1): a zero-step row ahead of the real trial.
        self.refused(cand, before(references=['OUTSIDE', 'U1', 'U1'], max_distance_nm='unbounded', status='infeasible'),
                     'trial references must be distinct movable block members')
        self.refused(cand, before(max_distance_nm=-1), 'trial max_distance_nm must be a positive integer')
        self.refused(cand, before(references=[], max_distance_nm=1), 'trial references must be distinct')
        self.refused(cand, lambda r: (r.update(max_distance_nm=True), r['trials'][-1].update(max_distance_nm=True)),
                     'max_distance_nm must be a positive integer')
        self.refused(cand, lambda r: r['trials'][-1].update(steps_used=self.stranded_policy['step_budget'] + 1),
                     'step budget')
        self.refused(cand, lambda r: r.pop('trials'), 'missing trials')
        self.refused(cand, lambda r: r.update(trials=[]), 'list of 1 to')
        self.refused(cand, lambda r: r.update(references=['C9', 'C9']), 'distinct movable block members')
        # Each field of an intermediate row on its own.
        self.refused(cand, before(status='infeasible'), 'reached the synthetic plane')
        self.refused(cand, before(status='recovered'), 'reached the synthetic plane')
        self.refused(cand, before(max_distance_nm=float(2 * start)), 'positive integer')
        self.refused(cand, before(max_distance_nm=float('inf')), 'positive integer')
        self.refused(cand, before(max_distance_nm=False), 'positive integer')
        self.refused(cand, before(references=['C9', 'C9']), 'distinct')
        self.refused(cand, before(references=['U1']), 'movable block members')
        self.refused(cand, before(references=['OUTSIDE']), 'movable block members')
        self.refused(cand, before(references=[9]), 'movable block members')
        self.refused(cand, before(steps_used=-1), 'steps_used')
        self.refused(cand, before(steps_used=self.stranded_policy['step_budget'] + 1), 'step budget')
        self.refused(cand, before(note='x'), 'recovery trial')
        # The retry ladder: halve only when the group did not grow, never shrink the group.
        self.refused(cand, before(max_distance_nm=start), 'halves its distance')
        self.refused(cand, before(max_distance_nm=4 * start), 'halves its distance')
        self.refused(cand, before(max_distance_nm=start // 2), 'halves its distance')
        self.refused(cand, before(references=['C1', 'C9']), 'first recovery trial groups exactly the stranded')
        self.refused(cand, lambda r: r['trials'][0].update(status='synthetic_plane_reached'),
                     'the last is recovered')
        self.refused(cand, lambda r: r.update(steps_total=r['steps_total'] + 1), 'sum of its trials')

    def test_multi_trial_history_follows_the_ladder(self):
        self.stranded({'1': 'VX', '2': 'GND'})
        # Forced boundary contact: C9 stays on the plane once, then C1 joins it, then all clear.
        with mock.patch.object(blocks, '_touching', side_effect=[['C9'], ['C9'], ['C1', 'C9'], []]):
            result = blocks.pack_block(self.board, self.inv, self.derived, 'Power', self.stranded_policy,
                                       compact=False)
        self.assertEqual(result['status'], 'packed', result.get('reason'))
        cand = result['candidate']
        record = cand['search']['recovery']
        start = record['trials'][0]['max_distance_nm']
        self.assertEqual([(t['references'], t['max_distance_nm'], t['status']) for t in record['trials']],
                         [(['C9'], start, 'synthetic_plane_reached'), (['C9'], start // 2, 'synthetic_plane_reached'),
                          (['C1', 'C9'], start // 2, 'recovered')])
        self.assertEqual(blocks.admit(cand, self.board, self.inv, self.derived, self.stranded_policy)['status'],
                         'admitted')
        self.refused(cand, lambda r: r['trials'][0].update(references=['C1']), 'first recovery trial')
        self.refused(cand, lambda r: r['trials'][0].update(max_distance_nm=1), 'halves its distance')
        self.refused(cand, lambda r: r['trials'][0].update(status=None), 'reached the synthetic plane')
        self.refused(cand, lambda r: r['trials'][1].update(status='recovered'), 'reached the synthetic plane')
        # A grown group keeps its distance; a later group keeps every earlier member.
        self.refused(cand, lambda r: (r['trials'][2].update(max_distance_nm=start // 4),
                                      r.update(max_distance_nm=start // 4)), 'grows the group keeps its distance')
        self.refused(cand, lambda r: r['trials'][1].update(references=['C1', 'C9']), 'grows the group keeps its distance')
        self.refused(cand, lambda r: (r['trials'][2].update(references=['C1']), r.update(references=['C1'])),
                     'keeps every member')
        self.refused(cand, lambda r: (r['trials'][2].update(references=['C9', 'C1']),
                                      r.update(references=['C9', 'C1'])), 'natural order')
        self.refused(cand, lambda r: r['trials'][1].update(steps_used=r['trials'][1]['steps_used'] + 1),
                     'sum of its trials')

    def test_admission_binds_the_recovery_ladder_to_the_board(self):
        cand = self.stranded({'1': 'VX', '2': 'GND'})['candidate']
        record = cand['search']['recovery']
        # A self-consistent history that starts from a distance the board's plane does not give.
        bad = copy.deepcopy(cand)
        bad['search']['recovery']['trials'][0]['max_distance_nm'] *= 2
        bad['search']['recovery']['max_distance_nm'] *= 2
        blocks.validate_candidate(bad)
        verdict = blocks.admit(bad, self.board, self.inv, self.derived, self.stranded_policy)
        self.assertEqual(verdict['status'], 'refused')
        self.assertEqual(verdict['problems'][0]['kind'], 'recovery_mismatch')
        self.assertEqual(verdict['problems'][0]['start_distance_nm'], record['max_distance_nm'])
        # Admission re-applies the producer's eligibility rule to every grouped member on the board.
        with mock.patch.object(blocks, '_recovery_eligible', lambda board, ref, grouped: ref != 'C9'):
            verdict = blocks.admit(cand, self.board, self.inv, self.derived, self.stranded_policy)
        self.assertEqual((verdict['status'], verdict['problems'][0]['ineligible']), ('refused', ['C9']))
        self.assertEqual(blocks.admit(cand, self.board, self.inv, self.derived, self.stranded_policy)['status'],
                         'admitted')


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


def captured_at(fp, turns, position=None):
    """fp as pcbnew captures it at a quarter-turn: each contour starts at another vertex
    and odd turns run the other way (AGENTEE-370), on top of the rigid rotation."""
    placed = placer.transform(fp, position or fp['position_nm'], (fp['rotation_deg'] + 90 * turns) % 360)
    placed = {k: v for k, v in placed.items() if not k.startswith('_')}
    def relabel(ring):
        ring = [list(p) for p in ring]
        shifted = ring[turns % len(ring):] + ring[:turns % len(ring)]
        return shifted[::-1] if turns % 2 else shifted
    for courtyard in placed['courtyards'].values():
        courtyard['polygons'] = [relabel(r) for r in courtyard['polygons']][::-1 if turns % 2 else 1]
    for pad in placed['pads']:
        custom = pad.get('custom_geometry')
        if custom:
            pad['custom_geometry'] = {**custom, 'merged_polygons_nm': [relabel(r) for r in custom['merged_polygons_nm']]}
    return placed


def asymmetric(side='F'):
    """An owned part whose courtyard is an L of two rings and whose pad 1 is custom copper."""
    fp = footprint('U1', 10, 10, 'Owned:L-part', {'1': 'A', '2': 'B'}, side=side)
    fp['courtyards'] = {side: {'status': 'proven', 'polygons': [
        [[9 * MM, 9 * MM], [13 * MM, 9 * MM], [13 * MM, 10 * MM], [10 * MM, 10 * MM], [10 * MM, 12 * MM],
         [9 * MM, 12 * MM]],
        [[14 * MM, 9 * MM], [15 * MM, 9 * MM], [15 * MM, 11 * MM], [14 * MM, 11 * MM]]]}}
    x, y = fp['pads'][0]['position_nm']
    fp['pads'][0].update(shape=4, custom_geometry={
        'status': 'proven', 'anchor': 1, 'primitives': [], 'bbox_nm': [x - 200000, y - 200000, x + 300000, y + 200000],
        'merged_polygons_nm': [[[-200000, -200000], [300000, -200000], [300000, 0], [0, 0], [0, 200000],
                                [-200000, 200000]]]})
    return fp


def captured_board(capture, ref, turns):
    turned = copy.deepcopy(capture)
    turned['footprints'] = [captured_at(fp, turns) if fp['ref'] == ref else fp for fp in turned['footprints']]
    return turned


class CanonicalIdentityTests(unittest.TestCase):
    """AGENTEE-370: land-pattern and courtyard identity do not depend on the captured pose."""

    def identity(self, fp):
        return blocks.pad_geometry_sha256(fp), blocks.courtyard_sha256(fp)

    def test_ring_relabelings_share_one_canonical_ring(self):
        ring = [[0, 0], [4, 0], [4, 1], [1, 1], [1, 3], [0, 3]]
        relabeled = [run[i:] + run[:i] for run in (ring, ring[::-1]) for i in range(len(ring))]
        self.assertEqual({json.dumps(blocks.canonical_ring(r)) for r in relabeled}, {json.dumps(ring)})
        # A zero-area ring keeps its vertex set and takes the smaller direction.
        flat = [[2, 0], [0, 0], [1, 0]]
        self.assertEqual(blocks.canonical_ring(flat), [[0, 0], [1, 0], [2, 0]])
        self.assertEqual(blocks.canonical_ring(flat[::-1]), [[0, 0], [1, 0], [2, 0]])

    def test_every_quarter_turn_and_side_share_the_identity(self):
        for side in ('F', 'B'):
            base = asymmetric(side)
            want = self.identity(base)
            for turns in range(4):
                for position in (None, [31 * MM, 7 * MM]):
                    with self.subTest(side=side, turns=turns, position=position):
                        self.assertEqual(self.identity(captured_at(base, turns, position)), want)
                        # Turned again from that pose: the identity is that of the shape, not the path.
                        self.assertEqual(self.identity(captured_at(captured_at(base, turns), 3)), want)

    def test_a_different_shape_keeps_a_different_identity(self):
        base = asymmetric()
        pads, courtyard = self.identity(base)
        moved = copy.deepcopy(base)
        moved['courtyards']['F']['polygons'][0][3] = [10 * MM + 1, 10 * MM]
        larger = copy.deepcopy(base)
        larger['courtyards']['F']['polygons'][1][1:3] = [[15 * MM + 1, 9 * MM], [15 * MM + 1, 11 * MM]]
        # The mirror image of the L is a relabeling of no ring of the original.
        mirrored = copy.deepcopy(base)
        mirrored['courtyards']['F']['polygons'][0] = [[2 * base['position_nm'][0] - x, y]
                                                      for x, y in base['courtyards']['F']['polygons'][0]]
        for name, fp in (('moved vertex', moved), ('larger', larger), ('mirrored', mirrored)):
            for turns in range(4):
                with self.subTest(name=name, turns=turns):
                    self.assertEqual(self.identity(captured_at(fp, turns))[0], pads)
                    self.assertNotEqual(self.identity(captured_at(fp, turns))[1], courtyard)
        copper = copy.deepcopy(base)
        copper['pads'][0]['custom_geometry']['merged_polygons_nm'][0][2] = [300000, 1]
        self.assertNotEqual(self.identity(copper)[0], pads)
        self.assertEqual(self.identity(copper)[1], courtyard)
        side = asymmetric('B')
        self.assertNotEqual(self.identity(side), (pads, courtyard))


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

    def test_rotated_capture_matches_at_every_quarter_turn(self):
        """AGENTEE-370: pcbnew starts and winds each contour differently per pose; identity holds."""
        target, inventory = self.second_instance()
        for turns in range(4):
            turned = copy.deepcopy(target)
            turned['footprints'] = [captured_at(fp, turns) if fp['ref'] in self.MAPPING.values() else fp
                                    for fp in turned['footprints']]
            with self.subTest(turns=turns):
                self.assertEqual(blocks.match(self.cand, turned, inventory, self.MAPPING),
                                 {'status': 'match', 'mismatches': []})
        grown = copy.deepcopy(target)
        fp = next(f for f in grown['footprints'] if f['ref'] == 'C13')
        fp['courtyards']['F']['polygons'][0][1][0] += 1
        fp['courtyards']['F']['polygons'][0][2][0] += 1
        result = blocks.match(self.cand, captured_board(grown, 'C13', 1), inventory, self.MAPPING)
        self.assertEqual(self.kinds(result), ['courtyard'])

    def test_candidate_with_a_pose_dependent_courtyard_record_refuses(self):
        """A pre-AGENTEE-370 candidate kept captured vertex order; it is refused, never re-hashed."""
        doc = copy.deepcopy(self.cand)
        row = next(m for m in doc['members'] if m['reference'] == 'C1')
        ring = row['courtyard']['F']['polygons'][0]
        row['courtyard']['F']['polygons'][0] = ring[1:] + ring[:1]
        row['courtyard_sha256'] = blocks.digest({'side': row['side'], 'courtyards': row['courtyard']})
        with self.assertRaisesRegex(ValueError, 'C1: identity_version: courtyard record is not in canonical ring'):
            blocks.validate_candidate(doc)

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
