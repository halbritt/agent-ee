"""Known-good block library: catalogue, receipts, three import paths, lookup and materialization (AGENTEE-342).

Every fixture is original: the authored AGENTEE-339 sheets, parts, pads, nets and
limits, an authored board text with copper, and an authored figure record. The
native tests read the owner's fome-mc object store at a pinned commit and copy
nothing from it.
"""
import copy
import hashlib
import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_ee import block_library as bl, blocks, placer
from test_blocks import Fixture, MM, PARTS, SHEETS, board, captured_at, native_xml, policy

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'https://example.invalid/owned/ee342'
COMMIT = 'a' * 40
HARD = ('manufacturer-reference-layout', 'manufacturer-evm', 'qualified-owner-board')
POWER = ('U1', 'C1', 'L1', 'C3')
FIG_NM = 250000  # nanometres per authored figure unit
AUTO = object()


def authority(provenance_class, kind, source, references, dimensions=None, payload='0' * 64, **changes):
    """A synthetic authority record. It exercises the binding only: while hard classes are disabled
    (AGENTEE-348) no record confers a class, and every path refuses it with authority_unavailable."""
    record = {'schema': bl.AUTHORITY_RECORD, 'class': provenance_class, 'import': kind, 'source': source,
              'references': sorted(references, key=blocks._natural), 'payload_sha256': payload,
              'owner': 'Owned EE342 synthetic owner',
              'disposition': 'approved', 'reference': 'synthetic test record; not an owner approval'}
    if provenance_class.startswith('manufacturer-'):
        record['capture'] = {'origin': 'Owned EE342 synthetic capture', 'captured_by': 'coordinator test boundary'}
    if dimensions is not None:
        record['dimensions'] = dimensions
    record.update(changes)
    return json.dumps({k: v for k, v in record.items() if v is not None}, sort_keys=True).encode()


class LibraryFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.inv, self.names, self.sha = self.inventory()
        packed = blocks.pack_block(self.board, self.inv, self.derive(), 'Power', policy())['candidate']
        # Known-good authored poses: the stage-A packing of the Power block.
        self.poses = {m['reference']: m['pose'] for m in packed['members']}
        self.policy = policy()

    def cluster_raw(self, poses=None, anchor='U1'):
        poses = poses or self.poses
        return json.dumps({anchor: {r: [*p['position_nm'], p['rotation_deg']] for r, p in poses.items()}}).encode()

    def cluster(self, block_id='owned-buck', version=1, raw=None, capture=None, inventory=None):
        return bl.import_project_cluster(
            raw or self.cluster_raw(), 'U1', capture or self.board, inventory or self.inv, self.names, self.sha,
            self.policy, block_id=block_id, version=version, part='OWNED-BUCK', citation='Owned EE342 cluster',
            repository=REPOSITORY, commit=COMMIT, path='owned/cluster-poses.json')

    def library(self):
        root = self.dir / 'library'
        (root / 'blocks').mkdir(parents=True)
        (root / bl.CATALOGUE_PATH).write_text(json.dumps({'schema': bl.CATALOGUE, 'entries': []}))
        return root

    def placed_board(self, origin=(20 * MM, 18 * MM), turns=1, text=None):
        """The board with the Power members at an absolute pose of the packed frame."""
        moved = copy.deepcopy(self.board)
        for i, fp in enumerate(moved['footprints']):
            if fp['ref'] in self.poses:
                pose = self.poses[fp['ref']]
                x, y = placer.rotate(pose['position_nm'], turns)
                placed = placer.transform(fp, [origin[0] + x, origin[1] + y], (pose['rotation_deg'] + 90 * turns) % 360)
                moved['footprints'][i] = {k: v for k, v in placed.items() if not k.startswith('_')}
        raw = (text or '(kicad_pcb (version 20260206))').encode()
        moved['board_sha256'] = hashlib.sha256(raw).hexdigest()
        return moved, raw

    def pad(self, capture, ref, number):
        fp = next(f for f in capture['footprints'] if f['ref'] == ref)
        return next(p['position_nm'] for p in fp['pads'] if p['number'] == number)

    def board_text(self, capture, extra=''):
        def mm(point):
            return f'{point[0] / MM:.6f} {point[1] / MM:.6f}'
        sw_a, sw_b = self.pad(capture, 'U1', '3'), self.pad(capture, 'L1', '1')
        vout = self.pad(capture, 'C3', '1')
        return ('(kicad_pcb (version 20260206) (generator "owned")\n'
                ' (net 0 "") (net 7 "SW")\n'
                f' (segment (start {mm(sw_a)}) (end {mm(sw_b)}) (width 0.3) (layer "F.Cu") (net 7) (uuid "s1"))\n'
                f' (segment (start {mm(vout)}) (end 0.5 0.5) (width 0.3) (layer "F.Cu") (net "VOUT") (uuid "s2"))\n'
                f' (segment (start {mm(sw_a)}) (end {mm(sw_b)}) (width 0.2) (layer "B.Cu") (net "SENSE") (uuid "s3"))\n'
                f' (via (at {mm(vout)}) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "VOUT") (uuid "v1"))\n'
                f' (arc (start {mm(sw_a)}) (mid {mm(vout)}) (end {mm(sw_b)}) (width 0.25) (layer "F.Cu") '
                '(net "SW") (uuid "a1"))\n' + extra + ')\n')

    def placed_region(self):
        capture, _ = self.placed_board()
        raw = self.board_text(capture).encode()
        capture['board_sha256'] = hashlib.sha256(raw).hexdigest()
        return capture, raw

    def region_authority(self, provenance_class, raw, references=POWER, **identity):
        source = {'locator': 'Owned EVM rev A', 'sha256': hashlib.sha256(raw).hexdigest(), **identity}
        return authority(provenance_class, 'board-region/1', source, references)

    def region(self, provenance_class='search-derived', capture=None, raw=None, authority_raw=None, **identity):
        if capture is None:
            capture, raw = self.placed_region()
        return bl.import_board_region(raw, capture, self.inv, self.names, self.sha, self.policy,
                                      references=','.join(POWER), anchor='U1', block_id='owned-evm', version=1,
                                      part='OWNED-BUCK', provenance_class=provenance_class,
                                      citation='Owned EE342 evaluation board', locator='Owned EVM rev A',
                                      authority_raw=authority_raw, **identity)

    def harden(self, entry, provenance_class, record=None, **changes):
        """An agent-authored hard-class copy of an entry with a record bound to its whole payload.

        This is exactly what any caller can author; it must never be accepted as hard."""
        hard = copy.deepcopy(entry)
        source = hard['receipt']['source']
        hard['provenance'].update({'class': provenance_class, 'enforcement': 'hard',
                                   'source': {'locator': source['locator'], 'sha256': source['sha256']}})
        hard['receipt'].pop('authority_sha256', None)
        if record is None:
            scale = hard['receipt'].get('figure', {}).get('scale')
            dimensions = ([{'basis': d['basis'], 'length_nm': d['length_nm']}
                           for d in [scale['reference']] + scale['checks']] if scale else None)
            record = authority(provenance_class, hard['receipt']['import'], source,
                               [r['reference'] for r in hard['layout']['members']], dimensions,
                               payload=bl.payload_sha256(hard), **changes)
        hard['receipt']['authority_sha256'] = hashlib.sha256(record).hexdigest()
        return hard, record

    def plant(self, root, entry, record=None):
        """Write an entry (and a record under owner/) straight into a catalogue root, as any agent can."""
        relative = bl.entry_path(entry['id'], entry['version'])
        raw = bl.entry_bytes(entry)
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(raw)
        catalogue = json.loads((root / bl.CATALOGUE_PATH).read_text())
        catalogue['entries'].append({'id': entry['id'], 'version': entry['version'], 'path': relative,
                                     'sha256': hashlib.sha256(raw).hexdigest()})
        (root / bl.CATALOGUE_PATH).write_text(json.dumps(catalogue))
        if record is not None:
            path = root / 'owner/block-authority' / (hashlib.sha256(record).hexdigest() + '.json')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(record)


class ProjectClusterTests(LibraryFixture, unittest.TestCase):
    def test_cluster_imports_as_search_derived_advisory(self):
        entry = self.cluster()
        self.assertEqual(entry['provenance']['class'], 'search-derived')
        self.assertEqual(entry['provenance']['enforcement'], 'advisory')
        self.assertNotIn('source', entry['provenance'])
        self.assertEqual(entry['provenance']['label'],
                         'search-derived (advisory): project cluster U1 from owned/cluster-poses.json@aaaaaaaaaaaa')
        self.assertEqual(entry['receipt']['source']['sha256'], hashlib.sha256(self.cluster_raw()).hexdigest())
        self.assertEqual(entry['receipt']['inputs']['board_sha256'], self.board['board_sha256'])
        rows = {r['reference']: r for r in entry['layout']['members']}
        self.assertEqual({r: rows[r]['pose'] for r in rows}, self.poses)
        # Numeric records and identities only: no courtyard polygon, pin name or net name.
        text = json.dumps(entry)
        for absent in ('"courtyard"', 'SW"', 'VIN', 'power_in', 'polygons'):
            self.assertNotIn(absent, text)
        self.assertEqual(rows['U1']['pins']['3'], rows['L1']['pins']['1'])
        self.assertEqual(entry['copper'], None)
        self.assertEqual(bl.reproduce(entry, self.cluster_raw(), self.board, self.inv, self.names, self.sha,
                                      self.policy), {'status': 'reproduced', 'problems': []})
        self.assertEqual(self.cluster(), entry)

    def test_owner_ruling_cluster_can_never_be_a_reference_class(self):
        """Owner ruling 2026-10-21: a project cluster is search-derived and advisory, never a hard class."""
        signature = inspect.signature(bl.import_project_cluster).parameters
        self.assertFalse({'provenance_class', 'class', 'enforcement'} & set(signature))
        self.assertEqual(bl.IMPORTS['project-cluster/1'], frozenset(('search-derived',)))
        entry = self.cluster()
        for name in HARD:
            forged = copy.deepcopy(entry)
            forged['provenance'].update({'class': name, 'enforcement': 'hard', 'source': {
                'locator': forged['receipt']['source']['locator'], 'sha256': forged['receipt']['source']['sha256']}})
            with self.subTest(name=name), self.assertRaisesRegex(
                    ValueError, f'import project-cluster/1 cannot carry provenance class {name}'):
                bl.validate_entry(forged)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'cannot carry provenance class'):
                bl.materialize(forged, self.board, self.inv, self.names, self.sha, self.policy)
        forged = copy.deepcopy(entry)
        forged['provenance']['enforcement'] = 'hard'
        with self.assertRaisesRegex(ValueError, 'known class and its enforcement'):
            bl.validate_entry(forged)

    def test_committed_pod_entries_are_advisory(self):
        """The committed telemetry-pod clusters carry no reference-layout, EVM or owner-board class."""
        entries = bl.validate_library(ROOT)
        pods = [e for e in entries if e['receipt']['import'] == 'project-cluster/1']
        self.assertEqual(sorted(e['id'] for e in pods), ['pod-lm2903-soic8-u26', 'pod-lmr36520-dda-u5',
                                                        'pod-lmr36520-dda-u7', 'pod-tps62162-dsg-u15'])
        self.assertEqual(sorted({e['part'] for e in pods}), ['LM2903-SOIC8', 'LMR36520-DDA', 'TPS62162-DSG'])
        for entry in pods:
            self.assertEqual((entry['provenance']['class'], entry['provenance']['enforcement']),
                             ('search-derived', 'advisory'))
            self.assertNotIn('source', entry['provenance'])
            self.assertTrue(entry['provenance']['label'].startswith('search-derived (advisory): project cluster'))
            self.assertIn('owner ruling 2026-10-21', entry['provenance']['citation'])
            self.assertEqual(entry['receipt']['source']['commit'], '7a8baa137bb5072016c49b5b57c1e38bf0634cff')
        for name in HARD:
            self.assertEqual(bl.lookup(ROOT, part='LMR36520-DDA', classes={name}), [])

    def test_cluster_refusals(self):
        with self.assertRaisesRegex(ValueError, 'no cluster U9'):
            bl.import_project_cluster(self.cluster_raw(), 'U9', self.board, self.inv, self.names, self.sha,
                                      self.policy, block_id='x', version=1, part='P', citation='c',
                                      repository=REPOSITORY, commit=COMMIT, path='p')
        moved = dict(self.poses, U1={'position_nm': [1, 0], 'rotation_deg': 0})
        with self.assertRaisesRegex(ValueError, 'anchor at the origin'):
            self.cluster(raw=self.cluster_raw(moved))
        turned = dict(self.poses, C1={'position_nm': self.poses['C1']['position_nm'], 'rotation_deg': 45})
        with self.assertRaisesRegex(ValueError, 'not a quarter turn'):
            self.cluster(raw=self.cluster_raw(turned))
        # A cluster that fails its own courtyard witness is never catalogued.
        crowded = dict(self.poses, C1={'position_nm': [500000, 0], 'rotation_deg': 0})
        with self.assertRaisesRegex(ValueError, 'import_refused.*witness'):
            self.cluster(raw=self.cluster_raw(crowded))
        with self.assertRaisesRegex(ValueError, 'full lowercase Git object id'):
            bl.import_project_cluster(self.cluster_raw(), 'U1', self.board, self.inv, self.names, self.sha,
                                      self.policy, block_id='x', version=1, part='P', citation='c',
                                      repository=REPOSITORY, commit='abc', path='p')

    def test_import_ignores_fixes_on_members_but_keeps_other_fixes(self):
        fixed = policy(fixed_refs=['H1', 'U1', 'C1'])
        entry = bl.import_project_cluster(self.cluster_raw(), 'U1', self.board, self.inv, self.names, self.sha, fixed,
                                          block_id='x', version=1, part='P', citation='c', repository=REPOSITORY,
                                          commit=COMMIT, path='p')
        self.assertEqual(entry['receipt']['inputs']['policy_sha256'], blocks.digest(policy(fixed_refs=['H1'])))
        # materialize applies stage A's rule: a fixed member cannot be a rigid-block member.
        result = bl.materialize(entry, self.board, self.inv, self.names, self.sha, fixed)
        self.assertEqual(result['status'], 'refused')
        self.assertNotIn('candidate', result)
        self.assertEqual([p['kind'] for p in result['problems']], ['admission'])
        self.assertRegex(result['problems'][0]['reason'], 'fixed references cannot be rigid-block members')


class BoardRegionTests(LibraryFixture, unittest.TestCase):
    def test_region_frames_poses_and_copper(self):
        capture, _ = self.placed_board()
        raw = self.board_text(capture).encode()
        capture['board_sha256'] = hashlib.sha256(raw).hexdigest()
        entry = self.region(capture=capture, raw=raw)
        rows = {r['reference']: r for r in entry['layout']['members']}
        # The anchor's board rotation and offset drop out: the frame equals the packed frame.
        self.assertEqual({r: rows[r]['pose'] for r in rows}, self.poses)
        sha = hashlib.sha256(raw).hexdigest()
        self.assertEqual(entry['provenance'], {
            'class': 'search-derived', 'enforcement': 'advisory', 'citation': 'Owned EE342 evaluation board',
            'label': 'search-derived (advisory): board region from Owned EVM rev A'})
        self.assertEqual(entry['receipt']['source'], {'locator': 'Owned EVM rev A', 'sha256': sha})
        self.assertNotIn('authority_sha256', entry['receipt'])
        copper = entry['copper']
        framed = {r: placer.transform(next(f for f in self.board['footprints'] if f['ref'] == r),
                                      self.poses[r]['position_nm'], self.poses[r]['rotation_deg']) for r in POWER}
        sw_a, sw_b = self.pad({'footprints': framed.values()}, 'U1', '3'), self.pad(
            {'footprints': framed.values()}, 'L1', '1')
        vout = self.pad({'footprints': framed.values()}, 'C3', '1')
        sw_role, vout_role = rows['U1']['pins']['3'][0], rows['C3']['pins']['1'][0]
        # The numbered SW net and the named VOUT via are kept; the track leaving the
        # block and the SENSE net of another block are not.
        self.assertEqual(copper['tracks'], [{'start': sw_a, 'end': sw_b, 'width_nm': 300000, 'layer': 'F.Cu',
                                             'role': sw_role}])
        self.assertEqual(copper['vias'], [{'at': vout, 'size_nm': 600000, 'drill_nm': 300000,
                                           'layers': ['B.Cu', 'F.Cu'], 'role': vout_role}])
        self.assertEqual(copper['arcs'], [{'start': sw_a, 'mid': vout, 'end': sw_b, 'width_nm': 250000,
                                           'layer': 'F.Cu', 'role': sw_role}])
        self.assertNotIn('"SW"', json.dumps(entry))
        self.assertEqual(bl.reproduce(entry, raw, capture, self.inv, self.names, self.sha, self.policy),
                         {'status': 'reproduced', 'problems': []})

    def test_region_refusals(self):
        capture, raw = self.placed_board()
        with self.assertRaisesRegex(ValueError, 'differ from the placement probe'):
            self.region(capture=capture, raw=raw + b' ')
        with self.assertRaisesRegex(bl.AuthorityUnavailable, 'authority_unavailable: qualified-owner-board'):
            self.region('qualified-owner-board', capture=capture, raw=raw, repository=REPOSITORY, commit=COMMIT,
                        path='owned.kicad_pcb')
        entry = self.region(capture=capture, raw=raw, repository=REPOSITORY, commit=COMMIT, path='owned.kicad_pcb')
        self.assertEqual(entry['provenance']['label'],
                         'search-derived (advisory): board region from owned.kicad_pcb@aaaaaaaaaaaa')
        with self.assertRaisesRegex(ValueError, 'unknown provenance class'):
            self.region('trusted', capture=capture, raw=raw)
        search = self.region('search-derived', capture=capture, raw=raw)
        self.assertNotIn('source', search['provenance'])
        turned = copy.deepcopy(capture)
        next(f for f in turned['footprints'] if f['ref'] == 'C1')['rotation_deg'] = 45.0
        with self.assertRaisesRegex(ValueError, 'C1: rotation 45.0 is not a quarter turn'):
            self.region(capture=turned, raw=raw)
        with self.assertRaisesRegex(ValueError, 'not a kicad_pcb'):
            other = copy.deepcopy(capture)
            other['board_sha256'] = hashlib.sha256(b'(kicad_sch)').hexdigest()
            self.region(capture=other, raw=b'(kicad_sch)')
        with self.assertRaisesRegex(ValueError, 'include the anchor'):
            bl.import_board_region(raw, capture, self.inv, self.names, self.sha, self.policy, references='C1,L1',
                                   anchor='U1', block_id='x', version=1, part='P', provenance_class='search-derived',
                                   citation='c', locator='l')


class ReferenceFigureTests(LibraryFixture, unittest.TestCase):
    DOCUMENT = b'Owned EE342 example datasheet bytes; not a vendor document.\n'

    def figure(self, axes='x-right-y-down', offset=(40, 30), turns=0, checks=None, tolerance=(1, 50)):
        """Pad centres of the packed layout, as an authored figure measured at 0.25 mm per unit."""
        flip = -1 if axes == 'x-right-y-up' else 1
        members = []
        for ref, pose in sorted(self.poses.items()):
            fp = next(f for f in self.board['footprints'] if f['ref'] == ref)
            x, y = placer.rotate(pose['position_nm'], turns)
            placed = placer.transform(fp, [x, y], (pose['rotation_deg'] + 90 * turns) % 360)
            pads = {p['number']: p['position_nm'] for p in placed['pads'] if p['number'] in ('1', '2')}
            members.append({'reference': ref, 'pads': {
                n: [offset[0] + p[0] / FIG_NM, flip * (offset[1] + p[1] / FIG_NM)] for n, p in pads.items()}})
        return {'schema': bl.FIGURE, 'class': 'manufacturer-reference-layout', 'axes': axes, 'anchor': 'U1',
                'document': {'locator': 'Owned EE342', 'sha256': hashlib.sha256(self.DOCUMENT).hexdigest(),
                             'figure': 'Fig. 1', 'page': 3},
                'scale': {'reference': {'from': [0, 0], 'to': [4, 0], 'length_nm': 1_000_000,
                                        'basis': 'Owned scale bar, 1 mm'},
                          'checks': checks if checks is not None else [
                              {'from': [10, 10], 'to': [10, 30], 'length_nm': 5_000_000,
                               'basis': 'Owned package body length 5 mm'}],
                          'tolerance': list(tolerance)},
                'members': members}

    def figure_authority(self, figure=None, dimensions=None):
        figure = figure or self.figure()
        scale = figure['scale']
        if dimensions is None:
            dimensions = [{'basis': d['basis'], 'length_nm': d['length_nm']}
                          for d in [scale['reference']] + scale['checks']]
        source = {'locator': figure['document']['locator'], 'sha256': figure['document']['sha256']}
        return authority(figure['class'], 'reference-figure/1', source, [m['reference'] for m in figure['members']],
                         dimensions)

    def imported(self, figure=None, document=None, authority_raw=None):
        figure = figure or self.figure()
        return bl.import_reference_figure(figure, document or self.DOCUMENT, self.board, self.inv,
                                          self.names, self.sha, self.policy, block_id='owned-figure', version=1,
                                          part='OWNED-BUCK', citation='Owned EE342 Fig. 1 recommended layout',
                                          authority_raw=authority_raw)

    def test_figure_poses_are_scale_verified_and_frame_invariant(self):
        for axes in ('x-right-y-down', 'x-right-y-up'):
            for turns in (0, 3):
                with self.subTest(axes=axes, turns=turns):
                    self.assertEqual(bl.figure_poses(self.figure(axes, turns=turns), self.board), self.poses)
        entry = self.imported()
        self.assertEqual(entry['provenance']['label'], 'search-derived (advisory): Owned EE342 Fig. 1, scale-verified')
        # The figure claims a manufacturer reference layout; the entry stays advisory (hard classes disabled).
        self.assertEqual(entry['receipt']['figure']['class'], 'manufacturer-reference-layout')
        self.assertEqual((entry['provenance']['class'], entry['provenance']['enforcement']),
                         ('search-derived', 'advisory'))
        self.assertNotIn('source', entry['provenance'])
        self.assertEqual(entry['receipt']['scale']['status'], 'scale-verified')
        self.assertEqual(entry['receipt']['scale']['nm_per_unit_squared'], [FIG_NM ** 2, 1])
        self.assertEqual(entry['receipt']['scale']['checks'][0]['status'], 'pass')
        self.assertEqual(bl.reproduce(entry, self.DOCUMENT, self.board, self.inv, self.names, self.sha,
                                      self.policy)['status'], 'reproduced')

    def test_scale_check_boundary_and_refusals(self):
        # 20 units at 0.25 mm is 5 mm; a cited 5.1 mm is 1/51 off: inside 1/50, outside 1/60.
        check = [{'from': [10, 10], 'to': [10, 30], 'length_nm': 5_100_000, 'basis': 'Owned body'}]
        bl.verify_scale(self.figure(checks=check, tolerance=(1, 50)))
        with self.assertRaisesRegex(ValueError, 'scale_unverified: check dimension outside tolerance: Owned body'):
            bl.verify_scale(self.figure(checks=check, tolerance=(1, 60)))
        # Exactly at the tolerance passes: 5 mm against a cited 5 mm * 50/49.
        exact = [{'from': [0, 0], 'to': [0, 20], 'length_nm': 4_900_000, 'basis': 'Owned edge'}]
        bl.verify_scale(self.figure(checks=exact, tolerance=(1, 49)))
        with self.assertRaisesRegex(ValueError, 'at least one independent check'):
            bl.validate_figure(self.figure(checks=[]))
        with self.assertRaisesRegex(ValueError, 'differ from the figure document sha256'):
            self.imported(document=b'other bytes')
        figure = self.figure()
        figure['class'] = 'qualified-owner-board'
        with self.assertRaisesRegex(ValueError, 'manufacturer reference layout or EVM'):
            self.imported(figure, authority_raw=None)
        # A pad measured 1 mm off fits no quarter turn of the member's own lands.
        figure = self.figure()
        member = next(m for m in figure['members'] if m['reference'] == 'L1')
        member['pads']['2'] = [member['pads']['2'][0] + 4, member['pads']['2'][1]]
        with self.assertRaisesRegex(ValueError, 'member_orientation_unresolved: L1'):
            self.imported(figure)
        figure = self.figure()
        figure['members'][0]['pads'] = {'9': [0, 0], '1': [1, 1]}
        with self.assertRaisesRegex(ValueError, 'are not footprint pads'):
            self.imported(figure)
        with self.assertRaisesRegex(bl.AuthorityUnavailable, 'authority_unavailable'):
            self.imported(authority_raw=self.figure_authority())

    def test_entry_scale_and_figure_are_bound(self):
        entry = self.imported()
        forged = copy.deepcopy(entry)
        forged['receipt']['scale']['nm_per_unit_squared'] = [1, 1]
        with self.assertRaisesRegex(ValueError, 'stored scale verification differs'):
            bl.validate_entry(forged)
        forged = copy.deepcopy(entry)
        forged['receipt']['figure']['document']['sha256'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'figure document must equal the receipt source'):
            bl.validate_entry(forged)
        forged = copy.deepcopy(entry)
        forged['receipt']['figure']['class'] = 'qualified-owner-board'
        with self.assertRaisesRegex(ValueError, 'manufacturer reference layout or EVM'):
            bl.validate_entry(forged)
        forged = copy.deepcopy(entry)
        del forged['receipt']['figure']
        with self.assertRaisesRegex(ValueError, 'keeps its figure record'):
            bl.validate_entry(forged)
        # A pose edited away from the figure is caught when the figure is re-derived.
        forged = copy.deepcopy(entry)
        next(r for r in forged['layout']['members'] if r['reference'] == 'C1')['pose']['position_nm'][0] += 100000
        result = bl.materialize(forged, self.board, self.inv, self.names, self.sha, self.policy)
        self.assertEqual([p['kind'] for p in result['problems']], ['figure_pose_mismatch'])


class MaterializeTests(LibraryFixture, unittest.TestCase):
    MAPPING = {'U1': 'U11', 'C1': 'C11', 'L1': 'L11', 'C3': 'C13'}

    def second_instance(self, change=lambda parts: parts):
        extra = [('U11', '/s4/', 'BUCK', 'Owned:QFN4', 'owned-buck', {'1': 'VIN', '2': 'GND', '3': 'SW2', '4': 'V2'}),
                 ('C11', '/s4/', 'C', 'Owned:C0805', '4.7uF', {'1': 'VIN', '2': 'GND'}),
                 ('L11', '/s4/', 'L', 'Owned:L1210', '10uH', {'1': 'SW2', '2': 'V2'}),
                 ('C13', '/s4/', 'C', 'Owned:C0805', '22uF', {'1': 'V2', '2': 'GND'}),
                 ('R9', '/', 'R', 'Owned:R0603', '1k', {'1': 'V2', '2': 'GND'})]
        parts = PARTS + tuple(change([list(p) for p in extra]))
        raw = native_xml(parts, sheets={**SHEETS, '/s4/': '/Power2/'})
        inventory, names, sha = self.inventory(raw)
        return board(parts), inventory, names, sha

    def test_second_instance_materializes_with_provenance(self):
        entry = self.cluster()
        target, inventory, names, sha = self.second_instance()
        result = bl.materialize(entry, target, inventory, names, sha, self.policy, self.MAPPING)
        self.assertEqual(result['status'], 'materialized', result['problems'])
        self.assertEqual((result['provenance'], result['enforcement'], result['label']),
                         ('search-derived', 'advisory', entry['provenance']['label']))
        candidate = blocks.validate_candidate(result['candidate'])
        self.assertEqual(candidate['provenance'], {'class': 'search-derived', 'enforcement': 'advisory',
                                                   'citation': 'Owned EE342 cluster', 'method': 'project-cluster/1'})
        self.assertEqual(candidate['sources']['board_sha256'], target['board_sha256'])
        self.assertEqual({m['reference']: m['pose'] for m in candidate['members']},
                         {self.MAPPING[r]: p for r, p in self.poses.items()})
        self.assertEqual(blocks.match(candidate, target, inventory)['status'], 'match')
        # The second instance's internal output obligation is not in this board policy,
        # so its block policy has none; the grade is the board policy's, re-run here.
        self.assertIsNone(candidate['grade']['electrical_locality'])

    def test_identity_mismatches_refuse(self):
        entry = self.cluster()
        def variant(index, **changes):
            def change(parts):
                for key, value in changes.items():
                    parts[index][{'fpid': 3, 'value': 4, 'pads': 5}[key]] = value
                return parts
            return change
        cases = ((variant(2, fpid='Owned:L1212'), ['fpid']), (variant(3, value='10uF'), ['value']),
                 (variant(2, pads={'1': 'V2', '2': 'SW2'}), ['net_role', 'net_role_merged', 'net_role_split']),
                 (variant(1, pads={'1': 'VIN', '2': 'V2'}), ['net_role_merged', 'net_role_split']))
        for change, kinds in cases:
            target, inventory, names, sha = self.second_instance(change)
            result = bl.materialize(entry, target, inventory, names, sha, self.policy, self.MAPPING)
            with self.subTest(kinds=kinds):
                self.assertEqual(result['status'], 'refused')
                self.assertNotIn('candidate', result)
                self.assertEqual(sorted({p['kind'] for p in result['problems']}), kinds)
        target, inventory, names, sha = self.second_instance()
        lands = copy.deepcopy(target)
        pad = next(f for f in lands['footprints'] if f['ref'] == 'C13')['pads'][0]
        pad['size_nm'] = [500000, 400000]
        result = bl.materialize(entry, lands, inventory, names, sha, self.policy, self.MAPPING)
        self.assertEqual([p['kind'] for p in result['problems']], ['pad_geometry'])
        result = bl.materialize(entry, target, inventory, names, sha, self.policy, dict(self.MAPPING, C3='C99'))
        self.assertEqual([p['kind'] for p in result['problems']], ['target_missing'])
        with self.assertRaisesRegex(ValueError, 'distinct target reference'):
            bl.materialize(entry, target, inventory, names, sha, self.policy, dict(self.MAPPING, C3='C11'))

    def test_rotated_members_materialize_at_every_quarter_turn(self):
        """AGENTEE-370: source and target captured at other quarter-turns, both with pose-dependent
        vertex order, still share every member identity; a different courtyard still refuses."""
        def turned(capture, refs, turns):
            capture = copy.deepcopy(capture)
            capture['footprints'] = [captured_at(fp, turns) if fp['ref'] in refs else fp for fp in capture['footprints']]
            return capture
        target, inventory, names, sha = self.second_instance()
        for source_turns in range(4):
            entry = self.cluster(capture=turned(self.board, POWER, source_turns))
            for turns in range(4):
                with self.subTest(source=source_turns, target=turns):
                    result = bl.materialize(entry, turned(target, set(self.MAPPING.values()), turns), inventory,
                                            names, sha, self.policy, self.MAPPING)
                    self.assertEqual(result['status'], 'materialized', result['problems'])
        grown = copy.deepcopy(target)
        ring = next(f for f in grown['footprints'] if f['ref'] == 'C13')['courtyards']['F']['polygons'][0]
        ring[2][1] += 1
        ring[3][1] += 1
        result = bl.materialize(entry, turned(grown, {'C13'}, 1), inventory, names, sha, self.policy, self.MAPPING)
        self.assertEqual(result['status'], 'refused')
        self.assertEqual([(p['kind'], p['reference']) for p in result['problems']], [('courtyard', 'C3')])

    def test_retired_pose_dependent_entries_validate_but_refuse_use(self):
        """An entry/1 identity was hashed in captured vertex order (AGENTEE-370): it still validates
        in a catalogue, but materialize, reproduce and add refuse it explicitly."""
        entry = self.cluster()
        self.assertEqual(entry['schema'], 'agent-ee.block-library-entry/2')
        retired = dict(copy.deepcopy(entry), schema=bl.RETIRED_ENTRY)
        self.assertIs(bl.validate_entry(retired), retired)
        target, inventory, names, sha = self.second_instance()
        result = bl.materialize(retired, target, inventory, names, sha, self.policy, self.MAPPING)
        self.assertEqual(result['status'], 'refused')
        self.assertNotIn('candidate', result)
        self.assertEqual([(p['kind'], p['expected'], p['actual']) for p in result['problems']],
                         [('identity_version', bl.ENTRY, bl.RETIRED_ENTRY)])
        replay = bl.reproduce(retired, self.cluster_raw(), self.board, self.inv, self.names, self.sha, self.policy)
        self.assertEqual((replay['status'], [p['kind'] for p in replay['problems']]), ('refused', ['identity_version']))
        self.assertEqual(bl.reproduce(entry, self.cluster_raw(), self.board, self.inv, self.names, self.sha,
                                      self.policy)['status'], 'reproduced')
        with self.assertRaisesRegex(ValueError, 'identity_version'):
            bl.add_entry(self.library(), retired)
        committed = [e for e in bl.validate_library(ROOT) if e['id'].startswith('pod-')]
        self.assertEqual({e['schema'] for e in committed}, {bl.RETIRED_ENTRY})

    def test_failed_regrade_refuses_and_is_never_a_pass(self):
        entry = self.cluster()
        tight = policy(gap_nm=5 * MM)
        result = bl.materialize(entry, self.board, self.inv, self.names, self.sha, tight)
        self.assertEqual(result['status'], 'refused')
        self.assertIn('witness', [p['kind'] for p in result['problems']])
        self.assertNotIn('candidate', result)
        result = bl.reproduce(entry, self.cluster_raw(), self.board, self.inv, self.names, self.sha, tight)
        self.assertEqual([p['kind'] for p in result['problems']], ['source_mismatch'])


class AuthorityHoldTests(LibraryFixture, unittest.TestCase):
    """Regressions for the AGENTEE-342 review HOLD: admission (H1), hard-class authority (H2),
    receipt replay binding copper (H3) and independent scale checks (H4)."""
    DOCUMENT = ReferenceFigureTests.DOCUMENT
    figure = ReferenceFigureTests.figure
    figure_authority = ReferenceFigureTests.figure_authority
    imported = ReferenceFigureTests.imported

    # H1 ------------------------------------------------------------------------------
    def test_locked_target_member_refuses_without_candidate(self):
        entry = self.cluster()
        target = copy.deepcopy(self.board)
        next(f for f in target['footprints'] if f['ref'] == 'L1')['locked'] = True
        result = bl.materialize(entry, target, self.inv, self.names, self.sha, self.policy)
        self.assertEqual(result['status'], 'refused')
        self.assertNotIn('candidate', result)
        self.assertEqual([p['kind'] for p in result['problems']], ['admission'])
        self.assertRegex(result['problems'][0]['reason'], 'locked footprints cannot be rigid-block members.*L1')
        # The caller's capture is never unlocked.
        self.assertTrue(next(f for f in target['footprints'] if f['ref'] == 'L1')['locked'])

    def test_materialize_requires_explicit_admission_on_the_target(self):
        entry = self.cluster()
        target = copy.deepcopy(self.board)
        with mock.patch.object(blocks, 'admit', wraps=blocks.admit) as admit:
            result = bl.materialize(entry, target, self.inv, self.names, self.sha, self.policy)
        self.assertEqual(result['status'], 'materialized', result['problems'])
        admit.assert_called_once()
        document, capture, inventory, derivation, used = admit.call_args.args
        self.assertIs(document, result['candidate'])
        self.assertIs(capture, target)
        self.assertIs(used, self.policy)
        self.assertEqual(derivation['board_sha256'], target['board_sha256'])
        for status in ('unknown', 'refused', 'pending', None):
            evidence = [{'kind': 'probe-' + str(status)}]
            with self.subTest(status=status), mock.patch.object(
                    blocks, 'admit', return_value={'status': status, 'problems': evidence}):
                result = bl.materialize(entry, target, self.inv, self.names, self.sha, self.policy)
                self.assertEqual(result['status'], 'refused')
                self.assertNotIn('candidate', result)
                self.assertEqual(result['problems'], [{'kind': 'admission', 'status': status, 'problems': evidence}])
        with mock.patch.object(blocks, 'admit', side_effect=ValueError('derivation belongs to another board')):
            result = bl.materialize(entry, target, self.inv, self.names, self.sha, self.policy)
        self.assertEqual(result['status'], 'refused')
        self.assertNotIn('candidate', result)
        self.assertEqual(result['problems'][0]['problems'][0]['kind'], 'admission_error')

    def test_unlocking_is_confined_to_source_import(self):
        source = copy.deepcopy(self.board)
        for fp in source['footprints']:
            if fp['ref'] in POWER:
                fp['locked'] = True
        # A read-only import of a hand-anchored source reads the locked poses ...
        entry = self.cluster(capture=source)
        self.assertEqual(bl.reproduce(entry, self.cluster_raw(), source, self.inv, self.names, self.sha,
                                      self.policy)['status'], 'reproduced')
        # ... but the same locked board is not a target the block may be placed on.
        with mock.patch.object(bl, '_unlocked', wraps=bl._unlocked) as unlocked:
            result = bl.materialize(entry, source, self.inv, self.names, self.sha, self.policy)
        self.assertEqual(result['status'], 'refused')
        unlocked.assert_not_called()

    # H2 ------------------------------------------------------------------------------
    def test_hard_classes_refuse_authority_unavailable_on_import(self):
        """Coordinator ruling 2026-10-21: no hard class until AGENTEE-348; no record confers one."""
        capture, raw = self.placed_region()
        identity = {'repository': REPOSITORY, 'commit': COMMIT, 'path': 'owned.kicad_pcb'}
        for name in HARD:
            extra = identity if name == 'qualified-owner-board' else {}
            for record in (None, self.region_authority(name, raw, **extra)):
                with self.subTest(name=name, record=record is not None), self.assertRaisesRegex(
                        bl.AuthorityUnavailable, 'authority_unavailable: .*AGENTEE-348'):
                    self.region(name, capture=capture, raw=raw, authority_raw=record, **extra)
        with self.assertRaisesRegex(bl.AuthorityUnavailable, 'authority_unavailable: an authority record'):
            self.region('search-derived', capture=capture, raw=raw,
                        authority_raw=self.region_authority('manufacturer-evm', raw))
        with self.assertRaisesRegex(bl.AuthorityUnavailable, 'authority_unavailable'):
            self.imported(authority_raw=self.figure_authority())
        self.assertEqual(self.imported()['provenance']['class'], 'search-derived')

    def test_authority_record_binding_contract(self):
        """The binding kept for AGENTEE-348: each record field is checked against the entry."""
        capture, raw = self.placed_region()
        entry, record = self.harden(self.region(capture=capture, raw=raw), 'manufacturer-evm')
        source = entry['receipt']['source']
        payload = bl.payload_sha256(entry)
        cases = (
            (authority('manufacturer-reference-layout', 'board-region/1', source, POWER, payload=payload),
             'record class differs'),
            (authority('manufacturer-evm', 'board-region/1', source, ('U1', 'C1'), payload=payload),
             'record references differ'),
            (authority('manufacturer-evm', 'board-region/1', dict(source, sha256='b' * 64), POWER, payload=payload),
             'record source differs'),
            (authority('manufacturer-evm', 'board-region/1', source, POWER), 'record payload_sha256 differs'),
            (authority('manufacturer-evm', 'board-region/1', source, POWER, payload=payload, disposition='pending'),
             'disposition must be approved'),
            (authority('manufacturer-evm', 'board-region/1', source, POWER, payload=payload, capture=None),
             'independent capture of its source'),
            (authority('manufacturer-evm', 'board-region/1', source, POWER, payload=payload,
                       capture={'origin': 'o', 'captured_by': bl.IMPORTER}), 'importer cannot be the capturer'),
            (authority('manufacturer-evm', 'project-cluster/1', source, POWER, payload=payload), 'cannot carry class'),
            (authority('qualified-owner-board', 'board-region/1', source, POWER, payload=payload),
             'repository, commit and path'),
            (authority('manufacturer-evm', 'board-region/1', source, POWER, payload='x'), 'payload sha256'),
            (b'{"schema": "agent-ee.block-authority/1", "schema": "x"}', 'duplicate'),
        )
        for case, pattern in cases:
            bound = dict(entry, receipt=dict(entry['receipt'], authority_sha256=hashlib.sha256(case).hexdigest()))
            with self.subTest(pattern=pattern), self.assertRaisesRegex(ValueError, pattern):
                bl.verify_authority(case, bound)
        self.assertEqual(bl.verify_authority(record, entry)['payload_sha256'], payload)
        with self.assertRaisesRegex(ValueError, 'authority_unverified'):
            bl.verify_authority(None, entry)
        with self.assertRaisesRegex(ValueError, 'authority_mismatch: record bytes'):
            bl.verify_authority(record + b' ', entry)
        # A bound record still confers nothing.
        with self.assertRaisesRegex(bl.AuthorityUnavailable, 'manufacturer-evm cannot be conferred'):
            bl.validate_entry(entry)

    def test_relabelled_cluster_receipt_refuses(self):
        """Owner ruling 2026-10-21: pod and agent work is search-derived/advisory only."""
        entry = self.cluster()
        source = entry['receipt']['source']
        hard = {'locator': source['locator'], 'sha256': source['sha256']}
        root = self.library()
        for name in HARD:
            record = authority(name, 'board-region/1', source, POWER)
            relabelled = []
            # The class alone, as before.
            forged = copy.deepcopy(entry)
            forged['provenance'].update({'class': name, 'enforcement': 'hard', 'source': hard})
            relabelled.append((forged, 'cannot carry provenance class'))
            # The receipt relabelled as a board region, without and with an authority hash.
            forged = copy.deepcopy(forged)
            forged['receipt']['import'] = 'board-region/1'
            forged['copper'] = {'tracks': [], 'arcs': [], 'vias': []}
            relabelled.append((forged, 'authority_unavailable'))
            forged = copy.deepcopy(forged)
            forged['receipt']['authority_sha256'] = hashlib.sha256(record).hexdigest()
            relabelled.append((forged, 'authority_unavailable'))
            # ... and as a reference figure.
            forged = copy.deepcopy(relabelled[0][0])
            forged['receipt']['import'] = 'reference-figure/1'
            forged['receipt']['authority_sha256'] = hashlib.sha256(record).hexdigest()
            relabelled.append((forged, 'cannot carry provenance class|authority_unavailable'))
            for forged, pattern in relabelled:
                with self.subTest(name=name, pattern=pattern):
                    with self.assertRaisesRegex(ValueError, pattern):
                        bl.validate_entry(forged)
                    with self.assertRaisesRegex(ValueError, 'authority_unavailable'):
                        bl.materialize(forged, self.board, self.inv, self.names, self.sha, self.policy,
                                       authority_raw=record)
                    with self.assertRaisesRegex(ValueError, pattern):
                        bl.materialize(forged, self.board, self.inv, self.names, self.sha, self.policy)
                    with self.assertRaisesRegex(ValueError, pattern):
                        bl.add_entry(root, forged)
        self.assertEqual(bl.validate_library(root), [])

    # H3 ------------------------------------------------------------------------------
    def test_receipt_replay_binds_stored_copper_and_layout(self):
        capture, raw = self.placed_region()
        entry = self.region('search-derived', capture=capture, raw=raw)
        self.assertEqual(bl.reproduce(entry, raw, capture, self.inv, self.names, self.sha, self.policy),
                         {'status': 'reproduced', 'problems': []})
        edits = (lambda e: e['copper']['tracks'][0].update(width_nm=1),
                 lambda e: e['copper']['vias'].clear(),
                 lambda e: e['copper']['arcs'][0]['mid'].__setitem__(0, e['copper']['arcs'][0]['mid'][0] + 1),
                 lambda e: e['copper']['tracks'].append(dict(e['copper']['tracks'][0], layer='B.Cu')))
        for edit in edits:
            altered = copy.deepcopy(entry)
            edit(altered)
            bl.validate_entry(altered)
            result = bl.reproduce(altered, raw, capture, self.inv, self.names, self.sha, self.policy)
            with self.subTest(copper=altered['copper']):
                self.assertEqual(result, {'status': 'refused', 'problems': [{'kind': 'entry_mismatch',
                                                                              'fields': ['copper']}]})
        altered = copy.deepcopy(self.cluster())
        next(r for r in altered['layout']['members'] if r['reference'] == 'C1')['pose']['position_nm'][0] += 1
        result = bl.reproduce(altered, self.cluster_raw(), self.board, self.inv, self.names, self.sha, self.policy)
        self.assertEqual(result['problems'], [{'kind': 'entry_mismatch', 'fields': ['layout']}])
        result = bl.reproduce(entry, raw + b' ', capture, self.inv, self.names, self.sha, self.policy)
        self.assertEqual([(p['kind'], p['input']) for p in result['problems']], [('source_mismatch', 'source_sha256')])

    # H4 ------------------------------------------------------------------------------
    def test_scale_self_check_is_not_independent(self):
        reference = self.figure()['scale']['reference']
        cross = {'from': [10, 10], 'to': [10, 30], 'length_nm': 5_000_000, 'basis': 'Owned package body length 5 mm'}
        cases = (
            ([copy.deepcopy(reference)], 'repeats the reference'),
            ([dict(reference, **{'from': reference['to'], 'to': reference['from'], 'basis': 'reversed'})],
             'repeats the reference'),
            ([dict(cross, basis=reference['basis'])], 'repeats the reference'),
            ([cross, dict(cross, basis='again')], 'repeats the reference or another check'),
            ([cross, {'from': [8, 0], 'to': [12, 0], 'length_nm': 1_000_000, 'basis': 'bar extended'}],
             "lies on the reference dimension's line"),
            ([{'from': [0, 10], 'to': [8, 10], 'length_nm': 2_000_000, 'basis': 'parallel pitch'}],
             'no check dimension lies at 45 degrees'),
            ([{'from': [0, 10], 'to': [8, 15], 'length_nm': 2_358_495, 'basis': 'shallow diagonal'}],
             'no check dimension lies at 45 degrees'),
        )
        for checks, pattern in cases:
            with self.subTest(pattern=pattern), self.assertRaisesRegex(ValueError, 'scale_self_check: .*' + pattern):
                bl.verify_scale(self.figure(checks=checks))
        # Exactly 45 degrees is accepted (inclusive), as is a perpendicular check.
        diagonal = {'from': [0, 10], 'to': [4, 14], 'length_nm': 1_414_214, 'basis': 'diagonal 45'}
        self.assertEqual(bl.verify_scale(self.figure(checks=[diagonal]))['status'], 'scale-verified')
        # The binding for AGENTEE-348: cited lengths must be the authority's documented dimensions.
        entry = self.imported()
        for dimensions, pattern in (
                ([{'basis': 'Owned scale bar, 1 mm', 'length_nm': 1_000_000},
                  {'basis': 'Owned package body length 5 mm', 'length_nm': 5_100_000}],
                 'authority_mismatch: figure dimensions not documented by the authority: Owned package body length 5 mm'),
                ([], 'lists the documented scale and check dimensions')):
            hard, _ = self.harden(entry, 'manufacturer-reference-layout')
            record = authority('manufacturer-reference-layout', 'reference-figure/1', hard['receipt']['source'],
                               POWER, dimensions, payload=bl.payload_sha256(hard))
            hard['receipt']['authority_sha256'] = hashlib.sha256(record).hexdigest()
            with self.subTest(pattern=pattern), self.assertRaisesRegex(ValueError, pattern):
                bl.verify_authority(record, hard)


class AuthorityDisabledTests(LibraryFixture, unittest.TestCase):
    """Regressions for the AGENTEE-342 Sol delta-review HOLD (reviews/ee342b-library-authority-sol.md).

    H1: every agent-authored record, string or file reproduced by the reviewer's probes.py
    must refuse with authority_unavailable; no path yields enforcement hard. H2 (payload.py):
    a record is bound to the complete payload, so an edited layout no longer matches it.
    """
    DOCUMENT = ReferenceFigureTests.DOCUMENT
    figure = ReferenceFigureTests.figure
    figure_authority = ReferenceFigureTests.figure_authority
    imported = ReferenceFigureTests.imported

    def assert_unavailable(self, call):
        with self.assertRaisesRegex(bl.AuthorityUnavailable, '^authority_unavailable: '):
            call()

    def test_probe_importer_authored_records_never_confer_hard(self):
        """probes.py 'NEW H2 importer-authored record' and 'forged owner namespace lookup'."""
        capture, raw = self.placed_region()
        root = self.library()
        for name in HARD:
            identity = {'repository': REPOSITORY, 'commit': COMMIT, 'path': 'owned.kicad_pcb'} \
                if name == 'qualified-owner-board' else {}
            source = {'locator': 'Owned EVM rev A', 'sha256': hashlib.sha256(raw).hexdigest(), **identity}
            capture_claim = ({'capture': {'origin': 'invented vendor capture', 'captured_by': 'agent-author-alias'}}
                             if name.startswith('manufacturer-') else {})
            record = authority(name, 'board-region/1', source, POWER, owner=bl.IMPORTER,
                               reference='importer self-approval', **capture_claim)
            with self.subTest(name=name, path='import'):
                self.assert_unavailable(lambda: self.region(name, capture=capture, raw=raw, authority_raw=record,
                                                            **identity))
            # The same record bound to the whole payload of an otherwise valid entry.
            entry, bound = self.harden(self.region(capture=capture, raw=raw, **identity), name, owner=bl.IMPORTER,
                                       reference='importer self-approval', **capture_claim)
            self.assertEqual(bl.verify_authority(bound, entry)['owner'], bl.IMPORTER)
            with self.subTest(name=name, path='use'):
                self.assert_unavailable(lambda: bl.validate_entry(entry))
                self.assert_unavailable(lambda: bl.materialize(entry, capture, self.inv, self.names, self.sha,
                                                               self.policy, authority_raw=bound))
                self.assert_unavailable(lambda: bl.materialize(entry, capture, self.inv, self.names, self.sha,
                                                               self.policy))
                self.assert_unavailable(lambda: bl.reproduce(entry, raw, capture, self.inv, self.names, self.sha,
                                                             self.policy, bound))
                self.assert_unavailable(lambda: bl.add_entry(root, entry))
        # A catalogue root an agent wrote directly: entry file, catalogue line and owner/ record.
        entry, record = self.harden(self.region(capture=capture, raw=raw), 'manufacturer-evm', owner=bl.IMPORTER)
        self.plant(root, entry, record)
        self.assert_unavailable(lambda: bl.validate_library(root))
        self.assert_unavailable(lambda: bl.lookup(root, part='OWNED-BUCK'))
        self.assertEqual(bl.lookup(ROOT, classes=set(HARD)), [])

    def test_probe_coherent_relabel_refuses(self):
        """probes.py 'NEW H2 coherent relabel': every self-declared binding updated together."""
        forged = self.cluster()
        forged['receipt']['import'] = 'board-region/1'
        forged['copper'] = {'tracks': [], 'arcs': [], 'vias': []}
        forged['receipt']['source'] = {'locator': 'agent relabelled as EVM',
                                       'sha256': forged['receipt']['inputs']['board_sha256']}
        forged, record = self.harden(forged, 'manufacturer-evm', owner=bl.IMPORTER)
        self.assertEqual(bl.verify_authority(record, forged)['class'], 'manufacturer-evm')
        self.assert_unavailable(lambda: bl.materialize(forged, self.board, self.inv, self.names, self.sha,
                                                       self.policy, authority_raw=record))
        self.assert_unavailable(lambda: bl.materialize(forged, self.board, self.inv, self.names, self.sha,
                                                       self.policy))

    def test_probe_copied_rotated_scale_with_fabricated_record(self):
        """probes.py 'NEW copied/rotated scale plus fabricated record': the record refuses, and the
        figure without it is advisory only."""
        figure = self.figure()
        reference = figure['scale']['reference']
        figure['scale']['checks'] = [dict(reference, **{'from': [10, 10], 'to': [10, 14],
                                                        'basis': 'invented independent dimension'})]
        self.assert_unavailable(lambda: self.imported(figure, authority_raw=self.figure_authority(figure)))
        entry = self.imported(figure)
        self.assertEqual((entry['provenance']['class'], entry['provenance']['enforcement']),
                         ('search-derived', 'advisory'))
        result = bl.materialize(entry, self.board, self.inv, self.names, self.sha, self.policy)
        self.assertEqual(result['status'], 'materialized', result['problems'])
        self.assertEqual((result['enforcement'], result['candidate']['provenance']['enforcement']),
                         ('advisory', 'advisory'))
        hard, record = self.harden(entry, 'manufacturer-reference-layout')
        self.assert_unavailable(lambda: bl.materialize(hard, self.board, self.inv, self.names, self.sha,
                                                       self.policy, authority_raw=record))

    def test_payload_bound_record_refuses_an_edited_layout(self):
        """payload.py: approved record and receipt unchanged, C1 moved -100000 nm in X."""
        capture, raw = self.placed_region()
        entry, record = self.harden(self.region(capture=capture, raw=raw), 'manufacturer-evm')
        self.assertEqual(bl.verify_authority(record, entry)['payload_sha256'], bl.payload_sha256(entry))
        edits = (
            ('layout', lambda e: e.update(self.moved(e))),
            ('copper', lambda e: e['copper']['tracks'][0].update(width_nm=e['copper']['tracks'][0]['width_nm'] + 1)),
            ('receipt', lambda e: e['receipt'].update(candidate_sha256='0' * 64)),
            ('inputs', lambda e: e['receipt']['inputs'].update(policy_sha256='0' * 64)),
            ('members', lambda e: next(r for r in e['layout']['members'] if r['reference'] == 'C1').update(
                courtyard_sha256='0' * 64)),
        )
        for label, edit in edits:
            changed = copy.deepcopy(entry)
            edit(changed)
            self.assertEqual(changed['receipt']['authority_sha256'], entry['receipt']['authority_sha256'])
            with self.subTest(edit=label):
                with self.assertRaisesRegex(ValueError, 'authority_mismatch: record payload_sha256 differs'):
                    bl.verify_authority(record, changed)
                self.assert_unavailable(lambda: bl.materialize(changed, capture, self.inv, self.names, self.sha,
                                                               self.policy, authority_raw=record))
                self.assert_unavailable(lambda: bl.add_entry(self.library_once(), changed))
        root = self.library_once()
        self.plant(root, self.moved(entry), record)
        self.assert_unavailable(lambda: bl.lookup(root))

    def library_once(self):
        root = self.dir / 'library'
        return root if root.exists() else self.library()

    @staticmethod
    def moved(entry):
        changed = copy.deepcopy(entry)
        next(r for r in changed['layout']['members'] if r['reference'] == 'C1')['pose']['position_nm'][0] -= 100000
        return changed

    def test_cli_refuses_hard_classes_and_records(self):
        """The CLI paths: --class, --authority, materialize and a planted catalogue --root."""
        capture, raw = self.placed_region()
        (self.dir / 'probe.json').write_text(json.dumps(capture))
        (self.dir / 'board.kicad_pcb').write_bytes(raw)
        (self.dir / 'design.xml').write_bytes(native_xml())
        (self.dir / 'policy.json').write_text(json.dumps(self.policy))
        record = self.region_authority('manufacturer-evm', raw)
        (self.dir / 'authority.json').write_bytes(record)
        common = ('--probe', self.dir / 'probe.json', '--netlist', self.dir / 'design.xml',
                  '--policy', self.dir / 'policy.json')
        board_args = (*common, '--board', self.dir / 'board.kicad_pcb', '--references', ','.join(POWER),
                      '--anchor', 'U1', '--locator', 'Owned EVM rev A', '--id', 'owned-evm', '--version', '1',
                      '--part', 'OWNED-BUCK', '--citation', 'c')
        run = CliTests.run_cli
        cases = (('import-board', *board_args, '--class', 'manufacturer-evm', '--authority', self.dir / 'authority.json',
                  '--out', self.dir / 'a.json'),
                 ('import-board', *board_args, '--class', 'manufacturer-reference-layout', '--out', self.dir / 'b.json'),
                 ('import-board', *board_args, '--class', 'search-derived', '--authority', self.dir / 'authority.json',
                  '--out', self.dir / 'c.json'))
        entry, bound = self.harden(self.region(capture=capture, raw=raw), 'manufacturer-evm')
        (self.dir / 'hard.json').write_text(json.dumps(entry))
        (self.dir / 'bound.json').write_bytes(bound)
        cases += (('materialize', *common, '--entry', self.dir / 'hard.json', '--authority', self.dir / 'bound.json',
                   '--out', self.dir / 'd.json'),)
        root = self.library()
        self.plant(root, entry, bound)
        cases += (('validate', '--root', root), ('lookup', '--root', root), ('add', '--root', self.dir / 'library',
                                                                              '--entry', self.dir / 'hard.json'))
        for args in cases:
            done = run(self, *args)
            with self.subTest(command=args[0], argv=[str(a) for a in args[-3:]]):
                self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                result = json.loads(done.stdout)
                self.assertEqual((result['status'], result['kind']), ('refused', 'authority_unavailable'))
        for name in 'abcd':
            self.assertFalse((self.dir / f'{name}.json').exists())
        done = run(self, 'import-board', *board_args, '--class', 'search-derived', '--out', self.dir / 'e.json')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(json.loads((self.dir / 'e.json').read_text())['provenance']['enforcement'], 'advisory')


class CatalogueTests(LibraryFixture, unittest.TestCase):
    def test_versions_are_immutable_and_lookup_ranks_authority(self):
        root = self.library()
        first = self.cluster()
        self.assertEqual(bl.add_entry(root, first), 'blocks/entries/owned-buck/v1.json')
        with self.assertRaisesRegex(ValueError, 'next version is 2; catalogued versions are immutable'):
            bl.add_entry(root, first)
        with self.assertRaisesRegex(ValueError, 'next version is 2'):
            bl.add_entry(root, self.cluster(version=3))
        second = self.cluster(version=2)
        bl.add_entry(root, second)
        evm, record = self.harden(self.region(), 'manufacturer-evm')
        with self.assertRaisesRegex(bl.AuthorityUnavailable, 'authority_unavailable'):
            bl.add_entry(root, evm)
        bl.add_entry(root, self.region())
        found = bl.lookup(root, part='OWNED-BUCK')
        self.assertEqual([(r['id'], r['version'], r['provenance']) for r in found],
                         [('owned-buck', 2, 'search-derived'), ('owned-evm', 1, 'search-derived')])
        self.assertEqual(found[1]['label'], 'search-derived (advisory): board region from Owned EVM rev A')
        self.assertEqual([r['version'] for r in bl.lookup(root, block_id='owned-buck', version=1)], [1])
        self.assertEqual(bl.lookup(root, part='OTHER'), [])
        self.assertEqual(len(bl.validate_library(root)), 3)

    def test_catalogue_binds_bytes_paths_and_order(self):
        root = self.library()
        bl.add_entry(root, self.cluster())
        path = root / 'blocks/entries/owned-buck/v1.json'
        original = path.read_bytes()
        path.write_bytes(original.replace(b'"version": 1', b'"version": 1 '))
        with self.assertRaisesRegex(ValueError, 'bytes differ from the catalogued sha256'):
            bl.validate_library(root)
        path.write_bytes(original)
        catalogue = json.loads((root / bl.CATALOGUE_PATH).read_text())
        catalogue['entries'][0]['version'] = 2
        (root / bl.CATALOGUE_PATH).write_text(json.dumps(catalogue))
        with self.assertRaisesRegex(ValueError, 'path must be blocks/entries/owned-buck/v2.json'):
            bl.validate_library(root)
        catalogue['entries'][0].update(version=1, extra=True)
        (root / bl.CATALOGUE_PATH).write_text(json.dumps(catalogue))
        with self.assertRaisesRegex(ValueError, 'unknown extra'):
            bl.validate_library(root)
        compact = json.dumps(json.loads(original)).encode()
        path.write_bytes(compact)
        catalogue['entries'][0] = {'id': 'owned-buck', 'version': 1, 'path': 'blocks/entries/owned-buck/v1.json',
                                   'sha256': hashlib.sha256(compact).hexdigest()}
        (root / bl.CATALOGUE_PATH).write_text(json.dumps(catalogue))
        with self.assertRaisesRegex(ValueError, 'not in canonical form'):
            bl.validate_library(root)

    def test_entry_structure_refusals(self):
        entry = self.cluster()
        def broken(change, pattern):
            doc = copy.deepcopy(entry)
            change(doc)
            with self.subTest(pattern=pattern), self.assertRaisesRegex(ValueError, pattern):
                bl.validate_entry(doc)
        broken(lambda d: d.update(schema='agent-ee.block-library-entry/0'), 'expected agent-ee.block-library-entry/1')
        broken(lambda d: d.update(extra=1), 'unknown extra')
        broken(lambda d: d.update(id='Owned Buck'), 'lowercase letters')
        broken(lambda d: d.update(version=0), 'positive integer')
        broken(lambda d: d['receipt'].update(importer='other'), 'receipt must name')
        broken(lambda d: d['receipt'].update(candidate_sha256='x'), 'candidate sha256')
        broken(lambda d: d['receipt']['source'].pop('commit'), 'repository, commit and path')
        broken(lambda d: d['layout']['members'][0]['pose'].update(rotation_deg=45), 'quarter-turn')
        broken(lambda d: d['layout'].update(anchor='Q1'), 'including the anchor')
        broken(lambda d: next(r for r in d['layout']['members'] if r['reference'] == 'U1')['pose'].update(
            position_nm=[1, 0]), 'anchor defines the layout frame')
        broken(lambda d: d['layout']['members'][0]['pins'].update({'1': ['x1', True]}), 'pin role')
        broken(lambda d: d.update(copper={'tracks': [], 'arcs': [], 'vias': []}), 'only a board region')
        broken(lambda d: d['provenance'].update(source={'locator': 'x', 'sha256': 'b' * 64}), 'no reference source')
        broken(lambda d: d['layout']['members'][0].update(value={'text': '1', 'rule': 'near'}), 'rule exact')
        broken(lambda d: d.__setitem__('layout', None), 'entry layout must be an object')


class CliTests(LibraryFixture, unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'agent_ee.block_library', *map(str, args)], capture_output=True,
                              text=True, env={'PYTHONPATH': str(ROOT / 'src')}, timeout=120, cwd=ROOT)

    def test_validate_lookup_import_add_materialize(self):
        done = self.run_cli('validate')
        self.assertEqual((done.returncode, json.loads(done.stdout)['status']), (0, 'valid'), done.stdout)
        done = self.run_cli('lookup', '--part', 'TPS62162-DSG')
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertEqual([(r['id'], r['provenance'], r['enforcement']) for r in json.loads(done.stdout)],
                         [('pod-tps62162-dsg-u15', 'search-derived', 'advisory')])
        (self.dir / 'probe.json').write_text(json.dumps(self.board))
        (self.dir / 'design.xml').write_bytes(native_xml())
        (self.dir / 'policy.json').write_text(json.dumps(self.policy))
        (self.dir / 'cluster.json').write_bytes(self.cluster_raw())
        common = ('--probe', self.dir / 'probe.json', '--netlist', self.dir / 'design.xml',
                  '--policy', self.dir / 'policy.json')
        done = self.run_cli('import-cluster', *common, '--cluster-poses', self.dir / 'cluster.json', '--cluster', 'U1',
                            '--repository', REPOSITORY, '--commit', COMMIT, '--path', 'owned/cluster-poses.json',
                            '--id', 'owned-buck', '--version', '1', '--part', 'OWNED-BUCK',
                            '--citation', 'Owned EE342 cluster', '--out', self.dir / 'entry.json')
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertEqual(json.loads((self.dir / 'entry.json').read_text()), self.cluster())
        root = self.library()
        done = self.run_cli('add', '--root', root, '--entry', self.dir / 'entry.json')
        self.assertEqual(json.loads(done.stdout), {'status': 'added', 'path': 'blocks/entries/owned-buck/v1.json'})
        done = self.run_cli('materialize', *common, '--entry', self.dir / 'entry.json', '--out', self.dir / 'use.json')
        self.assertEqual((done.returncode, json.loads(done.stdout)['status']), (0, 'materialized'), done.stdout)
        done = self.run_cli('import-cluster', *common, '--cluster-poses', self.dir / 'cluster.json', '--cluster', 'U9',
                            '--repository', REPOSITORY, '--commit', COMMIT, '--path', 'p', '--id', 'x',
                            '--version', '1', '--part', 'P', '--citation', 'c', '--out', self.dir / 'x.json')
        self.assertEqual((done.returncode, json.loads(done.stdout)['status']), (1, 'refused'))
        self.assertFalse((self.dir / 'x.json').exists())


class NativeTests(unittest.TestCase):
    """The pod clusters from the owner's pinned fome-mc objects, and a native board region."""

    def test_committed_pod_entries_reproduce_from_pinned_objects(self):
        from toolchain import fome_mc_store, require_toolchain
        require_toolchain(native=True)
        sys.path.insert(0, str(ROOT / 'scripts'))
        import import_pod_blocks
        repository = fome_mc_store(os.environ.get('AGENT_EE_FOME_MC_CHECKOUT', '/home/halbritt/git/fome-mc'),
                                   import_pod_blocks.COMMIT)
        done = subprocess.run([sys.executable, str(ROOT / 'scripts/import_pod_blocks.py'), '--repository',
                               str(repository), '--check'], capture_output=True, text=True, timeout=600,
                              env=dict(os.environ, PYTHONPATH=str(ROOT / 'src')))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(json.loads(done.stdout.splitlines()[-1]), {'status': 'reproduced', 'differ': []})

    def test_native_board_region_with_copper(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        from agent_ee.placement_probe import extract
        with tempfile.TemporaryDirectory(prefix='agent-ee-block-library-native-') as tmp:
            path = Path(tmp) / 'owned.kicad_pcb'
            path.write_text(NATIVE_BOARD)
            capture = extract(path)
            raw = path.read_bytes()
            design = Path(tmp) / 'design.xml'
            design.write_bytes(native_xml(NATIVE_PARTS, sheets={'/': '/', '/s1/': '/Power/'},
                                          pins={'C': (('1', '~', 'passive'), ('2', '~', 'passive')),
                                                'R': (('1', '~', 'passive'), ('2', '~', 'passive'))}))
            from agent_ee.native_netlist import read_native_inventory, read_sheet_names
            inventory, names = read_native_inventory(design), read_sheet_names(design)
            sha = hashlib.sha256(design.read_bytes()).hexdigest()
            native_policy = {'schema': 'agent-ee.packing-policy/1', 'step_budget': 100, 'gap_nm': 250000,
                             'fixed_refs': [], 'priority_refs': [], 'ground_nets': ['GND'], 'obstacles': []}
            entry = bl.import_board_region(raw, capture, inventory, names, sha, native_policy, references='R1,C1',
                                           anchor='R1', block_id='owned-native', version=1, part='OWNED-RC',
                                           provenance_class='search-derived', citation='Owned native region',
                                           locator='owned.kicad_pcb')
            self.assertEqual(entry['receipt']['source']['sha256'], hashlib.sha256(raw).hexdigest())
            rows = {r['reference']: r for r in entry['layout']['members']}
            # R1 at (20, 20) rotated 90; C1 at (24, 20) rotated 90: C1 sits 4 mm along the anchor's -y axis.
            self.assertEqual(rows['C1']['pose'], {'position_nm': [0, 4 * MM], 'rotation_deg': 0})
            self.assertEqual([t['role'] for t in entry['copper']['tracks']], [rows['R1']['pins']['2'][0]])
            self.assertEqual(entry['copper']['tracks'][0]['width_nm'], 250000)
            self.assertEqual(bl.reproduce(entry, raw, capture, inventory, names, sha, native_policy)['status'],
                             'reproduced')


NATIVE_PARTS = (('R1', '/s1/', 'R', 'Owned:R2', '1k', {'1': 'IN', '2': 'MID'}),
                ('C1', '/s1/', 'C', 'Owned:C2', '1uF', {'1': 'MID', '2': 'GND'}))


def _native_footprint(ref, fpid, x, y, nets):
    pads = ''.join(
        f'  (pad "{n}" smd rect (at {dx} 0 90) (size 0.8 0.9) (layers "F.Cu" "F.Paste" "F.Mask") '
        f'(net {code} "{net}") (uuid "00000000-0000-4000-8000-{ref.encode().hex():0>10}{n:0>2}"))\n'
        for (n, net, code), dx in zip(nets, (-0.75, 0.75)))
    lines = ''.join(f'  (fp_line (start {a}) (end {b}) (stroke (width 0.05) (type solid)) (layer "F.CrtYd") '
                    f'(uuid "00000000-0000-4000-9000-{ref.encode().hex():0>10}{i:0>2}"))\n'
                    for i, (a, b) in enumerate((('-1.5 -0.8', '1.5 -0.8'), ('1.5 -0.8', '1.5 0.8'),
                                                ('1.5 0.8', '-1.5 0.8'), ('-1.5 0.8', '-1.5 -0.8'))))
    return (f' (footprint "{fpid}" (layer "F.Cu") (uuid "00000000-0000-4000-a000-{ref.encode().hex():0>12}")\n'
            f'  (at {x} {y} 90)\n'
            f'  (property "Reference" "{ref}" (at 0 -2 90) (layer "F.SilkS") '
            f'(uuid "00000000-0000-4000-b000-{ref.encode().hex():0>12}") (effects (font (size 1 1) (thickness 0.15))))\n'
            f'{pads}{lines} )\n')


NATIVE_BOARD = (
    '(kicad_pcb (version 20260206) (generator "pcbnew") (generator_version "10.0")\n'
    ' (general (thickness 1.6) (legacy_teardrops no))\n'
    ' (paper "A4")\n'
    ' (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (13 "F.Paste" user) (1 "F.Mask" user) (5 "F.SilkS" user "F.Silkscreen")\n'
    '  (25 "Edge.Cuts" user) (31 "F.CrtYd" user "F.Courtyard") (29 "B.CrtYd" user "B.Courtyard"))\n'
    ' (setup (pad_to_mask_clearance 0))\n'
    ' (net 0 "") (net 1 "IN") (net 2 "MID") (net 3 "GND")\n'
    + _native_footprint('R1', 'Owned:R2', 20, 20, (('1', 'IN', 1), ('2', 'MID', 2)))
    + _native_footprint('C1', 'Owned:C2', 24, 20, (('1', 'MID', 2), ('2', 'GND', 3)))
    + ' (gr_rect (start 0 0) (end 40 40) (stroke (width 0.1) (type default)) (fill no) (layer "Edge.Cuts") '
    '(uuid "00000000-0000-4000-c000-000000000001"))\n'
    ' (segment (start 20 19.25) (end 24 20.75) (width 0.25) (layer "F.Cu") (net 2) '
    '(uuid "00000000-0000-4000-d000-000000000001"))\n'
    ')\n')


if __name__ == '__main__':
    unittest.main()
