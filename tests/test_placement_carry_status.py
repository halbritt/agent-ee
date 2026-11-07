"""Original, test-owned receipt capsules exercise pure carry validation.

These are protocol fixtures, not native qualification or historical receipts.
The real scope parser, byte hashes, receipt identities and consumer are used;
no tool is invoked and no native execution result is substituted at runtime.
"""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from agent_ee import placement_defects as carry, routed_artwork as artwork
from agent_ee.placement_adapter import STEP as PLACER
from agent_ee.storage import digest, identity, write_json


BOARD = 'project/board.kicad_pcb'
SOURCE = b'''(kicad_pcb (version 20260206) (generator "pure-carry-fixture")
 (gr_line (start 1 1) (end 2 1) (stroke (width 0.1) (type default))
  (layer "F.SilkS") (uuid "00000000-0000-0000-0000-000000000001"))
 (gr_line (start 1 1) (end 1 2) (stroke (width 0.1) (type default))
  (layer "F.SilkS") (uuid "00000000-0000-0000-0000-000000000002")))
'''
DEFECT = {'type': 'placement_silkscreen_conflict', 'status': 'open', 'verdict': 'defect',
          'native': {'type': 'silk_overlap', 'items': [
              {'uuid': '00000000-0000-0000-0000-000000000001'},
              {'uuid': '00000000-0000-0000-0000-000000000002'}]}}


class CarryStatusTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='ee625-status-')))
        self.serial = 0

    def capsule(self, *, status='unresolved', step=artwork.STEP, defects=None,
                policy=False, change=None, report_changes=None, receipt_change=None):
        """Create and seal a fresh capsule owned only by this test case."""
        self.serial += 1
        root = self.root / str(self.serial)
        root.mkdir()
        data = {'source': SOURCE, 'output': SOURCE, 'policy': None}
        argv = ['/usr/bin/python3', '-B', '-I', '/artwork-tool/artwork.py', '/candidate/'+BOARD]
        if policy:
            data['output'] = SOURCE.replace(b'(start 1 1) (end 2 1)', b'(start 4 4) (end 5 4)')
            data['policy'] = {'schema': artwork.ARTWORK_POLICY,
                'source_sha256': hashlib.sha256(SOURCE).hexdigest(),
                'free_groups': [{'owner_uuid': None,
                    'members': ['00000000-0000-0000-0000-000000000001']}]}
            argv += ['--artwork-policy-file', 'project/artwork-policy.json']
        defects = copy.deepcopy([DEFECT] if defects is None else defects)
        report = {'schema': 'agent-ee.placed-candidate/1', 'status': status,
            'producer': step, 'open_defects': defects,
            'outcome': 'defect' if defects else 'informational',
            'verdict': 'defect' if defects else 'informational',
            'native_drc_comparison': {'open_defects': copy.deepcopy(defects)},
            'write_scope': artwork.write_scope(data['source'], data['output'], artwork_policy=data['policy'])}
        if change:
            change(report, data, argv)
        payloads = {carry.BOARD: data['output'], artwork.SOURCE: data['source']}
        for name, payload in payloads.items():
            path = root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        if data['policy'] is not None:
            (root/artwork.POLICY_COPY).write_text(artwork.canonical_artwork_policy(data['policy']))
        report.update(board_sha256=digest(root/carry.BOARD), source_sha256=digest(root/artwork.SOURCE))
        report.update(report_changes or {})
        write_json(root/carry.REPORT, report)
        write_json(root/carry.DRC, {'included_severities': ['error', 'warning', 'exclusion'],
            'ignored_checks': [], 'violations': [copy.deepcopy(d['native']) for d in defects if 'native' in d]})
        write_json(root/carry.EXECUTION, {'execution': 'ok', 'exit_code': 0})
        write_json(root/carry.ARTWORK, {'moved_fields': []})
        artifacts = {name: digest(root/name) for name in [*carry.FILES, carry.ARTWORK,
            artwork.SOURCE, *([artwork.POLICY_COPY] if policy else [])]}
        receipt = {'schema': 'agent-ee.stage-receipt/3', 'status': 'completed',
            'spec': {'step': step, 'argv': argv, 'writes': {BOARD: carry.BOARD}},
            'inputs': {BOARD: report['source_sha256']}, 'outputs': {BOARD: report['board_sha256']},
            'artifacts': artifacts}
        if policy:
            receipt['spec']['inputs'] = ['project/artwork-policy.json']
            receipt['inputs']['project/artwork-policy.json'] = artifacts[artwork.POLICY_COPY]
        if receipt_change:
            receipt_change(receipt)
        receipt['identity'] = identity(receipt)
        write_json(root/'receipt.json', receipt)
        prefix = Path(carry.DIRECTORY)/digest(root/'receipt.json')
        inputs = root/'captured'
        for name in [*artifacts, 'receipt.json']:
            destination = inputs/prefix/name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((root/name).read_bytes())
        current = inputs/BOARD
        current.parent.mkdir(parents=True)
        current.write_bytes(data['output'])
        if policy:
            (inputs/'project/artwork-policy.json').write_bytes((root/artwork.POLICY_COPY).read_bytes())
        link = {'receipt': (prefix/'receipt.json').as_posix(), 'sha256': digest(root/'receipt.json')}
        write_json(inputs/carry.DIRECTORY/'index.json', [link])
        manifest = {'scope': {'board': BOARD, carry.DECLARATION: [link]},
            'files': {p.relative_to(inputs).as_posix(): digest(p)
                      for p in inputs.rglob('*') if p.is_file()}}
        return manifest, inputs, link

    def test_unresolved_artwork_keeps_its_open_silkscreen_defect(self):
        manifest, inputs, link = self.capsule()
        self.assertEqual(carry.open_defects(manifest, inputs), [dict(DEFECT, source_receipt=link)])

    def test_writer_authored_clear_artwork_retains_policy_binding(self):
        manifest, inputs, _ = self.capsule(status='placed', defects=[], policy=True)
        self.assertEqual(carry.open_defects(manifest, inputs), [])

    def test_repaired_discovery_result_is_accepted(self):
        manifest, inputs, _ = self.capsule(status='repaired', defects=[],
            change=lambda report, data, argv: argv.append('--discover-targets'))
        self.assertEqual(carry.open_defects(manifest, inputs), [])

    def test_artwork_status_must_match_defects_and_discovery_mode(self):
        cases = [dict(status='repaired', defects=[]), dict(status='placed'),
            dict(status='repaired', change=lambda report, data, argv: argv.append('--discover-targets')),
            dict(status='placed', defects=[], change=lambda report, data, argv: argv.append('--discover-targets'))]
        for case in cases:
            with self.subTest(status=case['status'], defects=case.get('defects')):
                manifest, inputs, _ = self.capsule(**case)
                with self.assertRaises(ValueError):
                    carry.open_defects(manifest, inputs)

    def test_ordinary_placer_remains_placed_only(self):
        manifest, inputs, link = self.capsule(status='placed', step=PLACER)
        self.assertEqual(carry.open_defects(manifest, inputs), [dict(DEFECT, source_receipt=link)])
        for status in ('unresolved', 'repaired', 'no_movable_fields', 'unknown'):
            with self.subTest(status=status):
                manifest, inputs, _ = self.capsule(status=status, step=PLACER)
                with self.assertRaises(ValueError):
                    carry.open_defects(manifest, inputs)

    def test_unknown_status_and_foreign_producer_refuse(self):
        cases = [dict(status='no_movable_fields'), dict(status='unknown'),
                 dict(step='foreign-artwork'), dict(report_changes={'producer': 'foreign-artwork'})]
        for case in cases:
            with self.subTest(case=case):
                manifest, inputs, _ = self.capsule(**case)
                with self.assertRaises(ValueError):
                    carry.open_defects(manifest, inputs)

    def test_unresolved_requires_nonempty_matching_silkscreen_defects(self):
        cases = [dict(defects=[]), dict(report_changes={'native_drc_comparison': {'open_defects': []}}),
            dict(defects=[dict(DEFECT, type='other_defect')]),
            dict(defects=[dict(DEFECT, status='closed')]),
            dict(defects=[dict(DEFECT, verdict='pass')]),
            dict(report_changes={'outcome': 'informational'}),
            dict(report_changes={'verdict': 'informational'})]
        for case in cases:
            with self.subTest(case=case):
                manifest, inputs, _ = self.capsule(**case)
                with self.assertRaises(ValueError):
                    carry.open_defects(manifest, inputs)

    def test_changed_board_or_source_digest_refuses(self):
        for field in ('source_sha256', 'board_sha256'):
            with self.subTest(field=field):
                manifest, inputs, _ = self.capsule(report_changes={field: '0'*64})
                with self.assertRaises(ValueError):
                    carry.open_defects(manifest, inputs)

    def test_unclassified_graphic_movement_cannot_gain_authority(self):
        def move(report, data, argv):
            data['output'] = SOURCE.replace(b'(start 1 1) (end 2 1)', b'(start 4 4) (end 5 4)')
        manifest, inputs, _ = self.capsule(change=move)
        with self.assertRaisesRegex(ValueError, 'anchored'):
            carry.open_defects(manifest, inputs)

    def test_forged_scope_or_policy_input_remains_refused(self):
        for case in [dict(report_changes={'write_scope': {}}),
                     dict(policy=True, status='placed', defects=[],
                          receipt_change=lambda r: r['inputs'].update({'project/artwork-policy.json': '0'*64}))]:
            with self.subTest(policy=bool(case.get('policy'))):
                manifest, inputs, _ = self.capsule(**case)
                with self.assertRaises(ValueError):
                    carry.open_defects(manifest, inputs)

    def test_modified_captured_receipt_or_artifact_refuses(self):
        for receipt in (False, True):
            with self.subTest(receipt=receipt):
                manifest, inputs, link = self.capsule()
                path = inputs/link['receipt']
                if not receipt:
                    path = path.parent/carry.REPORT
                path.write_bytes(path.read_bytes()+b' ')
                with self.assertRaises(ValueError):
                    carry.open_defects(manifest, inputs)


if __name__ == '__main__':
    unittest.main()
