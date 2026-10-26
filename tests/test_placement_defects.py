"""Carried placement evidence must gate only its own downstream candidate lineage."""
import contextlib
import copy
from pathlib import Path
import re
import shutil
import time
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import placement_defects
from agent_ee.storage import digest, read_json, write_json


def native_containment(board):
    from agent_ee.silk_artwork import containment_witness
    return containment_witness(board)


def complete_drc():
    # A native report that evaluated every severity and ignored no silk check.
    return {'violations': [], 'ignored_checks': [], 'included_severities': ['error', 'warning', 'exclusion']}


def outlined_closure_text():
    from tests.test_placement_adapter import TEXT
    # TEXT's overlapping Edge.Cuts lines cannot prove containment. Give this
    # closure fixture a rectangle enclosing both original and moved markings.
    outlined = TEXT.replace('(start 30 0) (end 0 0)', '(start 30 0) (end 30 20)')
    outline_tail = (
        '(gr_line (start 30 20) (end 0 20) (stroke (width 0.05) (type default)) '
        '(layer "Edge.Cuts") (uuid "e3"))\n'
        '(gr_line (start 0 20) (end 0 0) (stroke (width 0.05) (type default)) '
        '(layer "Edge.Cuts") (uuid "e4"))\n')
    outlined = outlined.rstrip()[:-1] + outline_tail + ')\n'
    # Native KiCad expands the abbreviated UUID in TEXT; use its canonical
    # spelling so the saved identity and the containment witness agree.
    field_uuid = '00000000-0000-0000-0000-0000000000a1'
    outlined = outlined.replace('(uuid "a1")', f'(uuid "{field_uuid}")')
    return outlined, field_uuid


class PlacementDefectContractTests(unittest.TestCase):
    def test_uncarried_candidate_has_no_defects(self):
        self.assertEqual(placement_defects.open_defects({'scope': {}, 'files': {}}, None), [])

    def test_release_clean_provenance_counts_silk_findings_at_every_severity(self):
        """AGENTEE-412: release needs a natively silk-clean board at any severity, lineaged or not."""
        for severity in ('error', 'warning', 'exclusion'):
            report = {'included_severities': ['error', 'warning', 'exclusion'], 'ignored_checks': [],
                      'violations': [{'type': 'silk_edge_clearance', 'severity': severity,
                                      'items': [{'uuid': 'g'}, {'uuid': 'e'}]},
                                     {'type': 'clearance', 'severity': 'error', 'items': [{'uuid': 'c'}]}]}
            findings = placement_defects.silk_findings(report)
            self.assertEqual(findings, [['silk_edge_clearance', ['e', 'g']]])
            for lineaged, message in ((True, 'not yet authenticated'), (False, 'no placement lineage')):
                with self.subTest(severity=severity, lineaged=lineaged), self.assertRaisesRegex(ValueError, message):
                    placement_defects.clean_provenance(findings, lineaged)
        placement_defects.clean_provenance([], True)

    def test_all_fab_family_kinds_gate_independent_of_stage(self):
        kinds = ('fab_profile', 'dfm_geometry', 'dfa_dft', 'assembly_readiness',
                 'jlc_cost_estimate', 'drc', 'fab_roundtrip', 'fab_export', 'fab_package',
                 'fab_consistency', 'cpl_rotation', 'jlc_dfm', 'part_binding',
                 'package_consistency', 'parts_subset', 'land_pattern', 'catalog', 'part_selection')
        for kind in kinds:
            for stage in (None, 'placement', 'routing', 'fab', 'verification', 'release'):
                with self.subTest(kind=kind, stage=stage):
                    self.assertTrue(placement_defects.gated({'kind': kind, 'stage': stage}))


    def test_projection_grading_composes_silk_carry_with_numeric_provenance(self):
        """Ruling 346c (1): overlap silk follows receipt-backed carry; numeric silk limits follow 331."""
        from agent_ee.checks import grade_drc
        sourced = {'rules': {}, 'settings': [{
            'setting': 'board.design_settings.rules.min_silk_clearance', 'value': 0.2, 'class': 'owner',
            'citation': {'path': 'constraints.json', 'basis': 'original test'}}]}
        edge = {'raw_locator': '/violations/2', 'type': 'silk_edge_clearance', 'items': [{'uuid': 'e'}],
                'description': 'Silkscreen clipped by board edge (board setup constraints silk '
                               'clearance 0.2000 mm; actual 0.1000 mm)'}
        unwaived = [{'raw_locator': '/violations/0', 'type': 'silk_overlap', 'items': [{'uuid': 'a'}]},
                    {'raw_locator': '/violations/1', 'type': 'silk_over_copper', 'items': [{'uuid': 'b'}]},
                    edge, {'raw_locator': '/unconnected_items/0', 'type': 'unconnected_items'}]
        outside = {'type': 'copper_outside_outline', 'items': [{'uuid': 'c'}]}

        def failing(projection, carried, structural=(), sources=None, items=unwaived):
            return [i.get('raw_locator') or i['type']
                    for i in grade_drc(items, sources, projection, carried, list(structural))[2]]
        projection = 'placement-stage/1'
        # Uncarried overlap silk blocks in the projection even under a default limit.
        self.assertEqual(failing(projection, []), ['/violations/0', '/violations/1'])
        self.assertEqual(failing(projection, ['/violations/0', '/violations/1']), [])
        # Default numeric silk-edge limit is advisory; a sourced one is hard (carry never names it).
        self.assertEqual(failing(projection, ['/violations/0', '/violations/1'], sources=sourced),
                         ['/violations/2'])
        # Structural containment blocks whatever silk is carried.
        self.assertEqual(failing(projection, ['/violations/0', '/violations/1'], [outside]),
                         ['copper_outside_outline'])
        # Unavailable containment is not a defect proved by native DRC.
        self.assertEqual(grade_drc(unwaived[2:], None, projection, [], None)[2], [])
        # The full DRC keeps its released 331 grading: unconnected items block, default silk is advisory.
        self.assertEqual(failing(None, []), ['/unconnected_items/0'])

    def test_initial_campaign_cannot_supply_coordinator_carry(self):
        placement_defects.validate_campaign({'inputs': []})
        for scope in ({'inputs': [{'at': placement_defects.DIRECTORY}]},
                      {'inputs': [], placement_defects.DECLARATION: []}):
            with self.subTest(scope=scope), self.assertRaisesRegex(ValueError, 'coordinator-owned'):
                placement_defects.validate_campaign(scope)

    def test_unanchored_initial_placer_receipt_is_not_new_carry(self):
        # The routed producer must not reinterpret historical generic placer steps.
        from agent_ee.placement_adapter import STEP
        receipt = {'spec': {'step': STEP}, 'status': 'completed'}
        with patch('agent_ee.stage_done._receipts', return_value=[(1, 'unused', receipt)]):
            self.assertIsNone(placement_defects.adopted('unused', {}, 1))

    def test_nonanchored_artwork_receipt_is_ignored(self):
        from agent_ee.routed_artwork import STEP
        # A file on disk is insufficient: only the anchored receipt index is read.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            work = root / 'implementations/1/receipts/1-agent-ee-routed-artwork'
            work.mkdir(parents=True)
            write_json(work / 'receipt.json', {'spec': {'step': STEP}, 'status': 'completed'})
            self.assertIsNone(placement_defects.adopted(root, {}, 1))

    def test_artwork_receipt_requires_the_coordinator_stage_receipt_anchor(self):
        # AGENTEE-408 G4: the coordinator's anchored index is the artwork receipt anchor.
        from agent_ee.routed_artwork import STEP
        from agent_ee.placement_adapter import STEP as PLACER
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            work = root / 'implementations/1'
            receipt = work / f'receipts/1-{STEP}/receipt.json'
            write_json(receipt, {'spec': {'step': STEP}, 'status': 'completed'})
            index = work / 'stage-receipts.json'
            write_json(index, {'receipts': [{'path': receipt.relative_to(work).as_posix(),
                                             'sha256': digest(receipt)}]})
            run = root / 'runs/1'
            write_json(run / 'candidate.json', {'candidate_id': 'c1'})
            state = {'history': [{'run': str(run)}], 'stage_receipts': {'1': digest(index)}}
            # Control: an anchored artwork receipt proceeds to authentication.
            with patch('agent_ee.stage_receipts.validate_receipt',
                       side_effect=RuntimeError('authenticating')) as validate:
                with self.assertRaisesRegex(RuntimeError, 'authenticating'):
                    placement_defects.adopted(root, state, 1)
            self.assertEqual(validate.call_args.kwargs['binding'],
                             {'implementation': 1, 'source_candidate_id': 'c1', 'receipt_index': 1})
            # A present index without its coordinator anchor, or with a different one, refuses.
            for anchors in ({}, {'2': digest(index)}, {'1': '0' * 64}):
                with self.subTest(anchors=anchors), self.assertRaisesRegex(
                        ValueError, 'stage receipt index changed or missing'):
                    placement_defects.adopted(root, {**state, 'stage_receipts': anchors}, 1)
            # A receipt substituted after the coordinator indexed it refuses.
            write_json(receipt, {'spec': {'step': STEP}, 'status': 'completed', 'forged': True})
            with self.assertRaisesRegex(ValueError, 'stage receipt changed after indexing'):
                placement_defects.adopted(root, state, 1)
            # An artwork receipt appended to an anchored index refuses.
            write_json(receipt, {'spec': {'step': STEP}, 'status': 'completed'})
            extra = work / f'receipts/2-{STEP}/receipt.json'
            write_json(extra, {'spec': {'step': STEP}, 'status': 'completed'})
            links = read_json(index)['receipts']
            write_json(index, {'receipts': [*links, {'path': extra.relative_to(work).as_posix(),
                                                     'sha256': digest(extra)}]})
            with self.assertRaisesRegex(ValueError, 'stage receipt index changed or missing'):
                placement_defects.adopted(root, state, 1)
            # An anchored placer receipt without its placement record is not carry.
            write_json(receipt, {'spec': {'step': PLACER}, 'status': 'completed'})
            write_json(index, {'receipts': [{'path': receipt.relative_to(work).as_posix(),
                                             'sha256': digest(receipt)}]})
            self.assertIsNone(placement_defects.adopted(
                root, {**state, 'stage_receipts': {'1': digest(index)}}, 1))

    def test_graphic_text_style_damage_retains_known_defect(self):
        with tempfile.TemporaryDirectory() as directory:
            before, after = (Path(directory) / name for name in ('before.kicad_pcb', 'after.kicad_pcb'))
            outlined, _ = outlined_closure_text()
            mark = '00000000-0000-0000-0000-0000000000aa'
            graphic = ('(gr_text "Owned mark" (at 4 5) (layer "F.SilkS") '
                       f'(uuid "{mark}") (effects (font (size 1 1))))')
            text = outlined.rstrip()[:-1] + graphic + ')'

            before.write_text(text)
            after.write_text(text.replace('(at 4 5)', '(at 5 6)').replace('(size 1 1)', '(size 0.8 1)'))
            defect = {'type': 'placement_silkscreen_conflict', 'status': 'open', 'verdict': 'defect',
                      'native': {'items': [{'uuid': mark}]}}
            self.assertFalse(placement_defects._closes(defect, placement_defects._identities(before),
                placement_defects._identities(after, native_containment(after)), set(), set(), True, graphic_translation=True))
            # A changed item kind is also damage, even when its UUID survives.
            shape = (outlined.rstrip()[:-1] +
                     f'(gr_line (start 4 5) (end 5 5) (layer "F.SilkS") (uuid "{mark}")))')
            before.write_text(shape)
            self.assertFalse(placement_defects._closes(defect, placement_defects._identities(before),
                placement_defects._identities(after, native_containment(after)), set(), set(), True, graphic_translation=True))
            before.write_text(text)
            # Restore the positive translation control with a qualified outline.
            after.write_text(text.replace('(at 4 5)', '(at 5 6)'))
            self.assertTrue(placement_defects._closes(defect, placement_defects._identities(before),
                placement_defects._identities(after, native_containment(after)), set(), set(), True, graphic_translation=True))


def stamped(data, *digests, block=''):
    """Saved board bytes with a title block lineage stamp, as the placement writer records it."""
    line = ' '.join([placement_defects.STAMP, *digests])
    head, rest = data.split(b'\n', 1)
    return head + f'\n (title_block {block}(comment 9 "{line}"))\n'.encode() + rest


class BoardLineageStampContractTests(unittest.TestCase):
    """AGENTEE-351: the board bytes name their carry lineage; re-scoping cannot drop it."""
    def setUp(self):
        from test_placement_adapter import TEXT
        self.board = TEXT.encode()
        self.a, self.b = 'a' * 64, 'b' * 64

    def test_stamp_reads_only_a_well_formed_title_block_comment(self):
        self.assertIsNone(placement_defects.stamp(self.board))
        self.assertEqual(placement_defects.stamp(stamped(self.board, self.a, self.b)), [self.a, self.b])
        self.assertEqual(placement_defects.stamp(stamped(self.board, self.a, block='(title "T") ')), [self.a])
        # The shared parser reads the stamp, so an escaped marker is still the stamp.
        escaped = stamped(self.board, self.a).replace(b'lineage/1', b'lineage\\/1')
        self.assertNotIn(placement_defects.STAMP.encode(), escaped)
        self.assertEqual(placement_defects.stamp(escaped), [self.a])
        # Marker text outside comment 9 is user text, not lineage; native verification covers such boards.
        for data in (self.board.replace(b'(uuid "e2")', f'(uuid "e2")) (gr_text "{placement_defects.STAMP} {self.a}"'.encode(), 1),
                     stamped(self.board, block=f'(title "{placement_defects.STAMP} {self.a}") ').replace(
                         f'(comment 9 "{placement_defects.STAMP}")'.encode(), b'')):
            with self.subTest(data=data[:200]):
                self.assertIsNone(placement_defects.stamp(data))
        # Bytes that are not one kicad_pcb expression have no stamp unless they name the marker.
        self.assertIsNone(placement_defects.stamp(b'not a board'))
        for data in (stamped(self.board), stamped(self.board, 'A' * 64), stamped(self.board, self.a, self.a),
                     stamped(self.board, 'a' * 63), stamped(stamped(self.board, self.a), self.b),
                     stamped(self.board, self.a) + b')', escaped + b'(trailing)',
                     b'(other_root ' + stamped(self.board, self.a) + b')'):
            with self.subTest(data=data[:200]), self.assertRaisesRegex(ValueError, 'lineage stamp'):
                placement_defects.stamp(data)

    def test_stamp_relocates_around_user_comments_and_refuses_only_a_full_title_block(self):
        """AGENTEE-412: user text in comment 9 keeps its slot; the stamp takes a free one."""
        from agent_ee import placement_adapter as pa

        def block(*comments):
            head, rest = self.board.split(b'\n', 1)
            text = ' '.join(f'(comment {n} "{v}")' for n, v in comments)
            return head + f'\n (title_block (title "T") {text})\n'.encode() + rest
        line = f'{placement_defects.STAMP} {self.a}'
        self.assertEqual(placement_defects.stamp_slot(self.board), '9')
        self.assertEqual(placement_defects.stamp_slot(block(('9', ''))), '9')
        self.assertEqual(placement_defects.stamp_slot(block(('9', 'Customer note'))), '8')
        self.assertEqual(placement_defects.stamp_slot(block(('9', 'n9'), ('8', 'n8'), ('1', 'n1'))), '7')
        # An existing stamp is extended in place, wherever it is.
        relocated = block(('8', line), ('9', 'Customer note'))
        self.assertEqual(placement_defects.stamp(relocated), [self.a])
        self.assertEqual(placement_defects.stamp_slot(relocated), '8')
        self.assertEqual(placement_defects.stamp_slot(stamped(self.board, self.a)), '9')
        with self.assertRaisesRegex(ValueError, 'every title block comment holds user text'):
            placement_defects.stamp_slot(block(*((str(n), f'n{n}') for n in range(1, 10))))
        for data in (block(('8', line), ('9', f'{placement_defects.STAMP} {self.b}')),
                     block(('8', line), ('8', 'Customer note')), block(('10', line)),
                     block(('8', f'{placement_defects.STAMP} not-a-digest'))):
            with self.subTest(data=data[:200]), self.assertRaisesRegex(ValueError, 'lineage stamp'):
                placement_defects.stamp(data)
        with self.assertRaisesRegex(ValueError, 'lineage stamp'):
            placement_defects.stamp_slot(block(('8', line), ('8', 'Customer note')))
        # Write scope treats a relocated stamp like comment 9 and still protects user text.
        user = block(('9', 'Customer note'))
        extended = block(('8', f'{line} {self.b}'), ('9', 'Customer note'))
        for before, after in ((user, relocated), (relocated, extended)):
            with self.subTest(before=before[:120], after=after[:120]):
                self.assertEqual(pa.write_scope(before, after)['status'], 'allowed')
        for before, after in ((user, block(('8', line), ('9', 'Edited note'))), (relocated, user),
                              (user, block(('9', line)))):
            with self.subTest(before=before[:120], after=after[:120]):
                self.assertEqual(pa.write_scope(before, after)['status'], 'refused')

    def test_full_title_block_refuses_the_writer_before_any_board_write(self):
        from agent_ee.placer_runner import _stamp_lineage
        import tempfile
        comments = ' '.join(f'(comment {n} "n{n}")' for n in range(1, 10))
        head, rest = self.board.split(b'\n', 1)
        data = head + f'\n (title_block {comments})\n'.encode() + rest
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'b.kicad_pcb'
            output.write_bytes(data)
            with self.assertRaisesRegex(ValueError, 'every title block comment holds user text'):
                _stamp_lineage(None, output, {}, {'open_defects': []}, None, None)
            self.assertEqual(output.read_bytes(), data)

    def test_marker_text_outside_a_stamp_comment_keeps_gate_verdicts(self):
        """AGENTEE-412: the literal marker in title text is user text at the gate too;
        only a comment that begins with the marker claims a stamp, and a malformed claim is unknown."""
        import tempfile
        from agent_ee.checks import CheckError, Ctx
        marker = placement_defects.STAMP
        head, rest = self.board.split(b'\n', 1)

        def titled(text):
            return head + f'\n (title_block {text})\n'.encode() + rest
        user = [titled(f'(title "{marker} {self.a}")'), titled(f'(company "{marker}")'),
                titled(f'(rev "{marker}") (date "{marker}")'),
                titled(f'(comment 9 "See {marker} {self.a}")'), titled(f'(comment 3 "notes: {marker}")')]
        claims = [titled(f'(comment 3 "{marker} documentation")'), titled(f'(comment 9 "{marker}")')]
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / 'inputs').mkdir()
            spec = {'id': 'fab', 'kind': 'fab_export'}
            manifest = {'candidate_id': 'c' * 64, 'scope': {'board': 'b.kicad_pcb', 'checks': [spec]}, 'files': {}}
            for data in [self.board, *user]:
                (run / 'inputs/b.kicad_pcb').write_bytes(data)
                manifest['files']['b.kicad_pcb'] = digest(run / 'inputs/b.kicad_pcb')
                with self.subTest(data=data[:200]):
                    self.assertIsNone(placement_defects.stamp(data))
                    self.assertTrue(placement_defects.before_check(Ctx(run, manifest, spec, time.monotonic() + 60)))
                    placement_defects.verify_gate(run, {'id': 'fab', 'execution': 'ok', 'outcome': 'pass'}, manifest)
            for data in claims:
                (run / 'inputs/b.kicad_pcb').write_bytes(data)
                manifest['files']['b.kicad_pcb'] = digest(run / 'inputs/b.kicad_pcb')
                with self.subTest(data=data[:200]):
                    with self.assertRaises(CheckError) as caught:
                        placement_defects.before_check(Ctx(run, manifest, spec, time.monotonic() + 60))
                    self.assertEqual(caught.exception.execution, 'malformed')

    def test_structurally_malformed_stamp_claims_refuse_at_admission_and_seal(self):
        """AGENTEE-412 H1: a comment that claims the stamp in a malformed shape (extra element,
        extra list, missing slot number) is unknown at admission and at seal verification."""
        import tempfile
        from agent_ee.checks import CheckError, Ctx
        marker = f'{placement_defects.STAMP} {self.a}'
        head, rest = self.board.split(b'\n', 1)
        shapes = [f'(comment 8 "{marker}" extra)', f'(comment 8 "{marker}" (extra))', f'(comment "{marker}")',
                  f'(comment 8 (extra "{marker}"))', f'(comment "{marker}" "notes")']
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / 'inputs').mkdir()
            spec = {'id': 'fab', 'kind': 'fab_export'}
            manifest = {'candidate_id': 'c' * 64, 'scope': {'board': 'b.kicad_pcb', 'checks': [spec]}, 'files': {}}
            for shape in shapes:
                data = head + f'\n (title_block {shape})\n'.encode() + rest
                (run / 'inputs/b.kicad_pcb').write_bytes(data)
                manifest['files']['b.kicad_pcb'] = digest(run / 'inputs/b.kicad_pcb')
                with self.subTest(shape=shape):
                    with self.assertRaisesRegex(ValueError, 'malformed placement lineage stamp'):
                        placement_defects.stamp(data)
                    with self.assertRaises(CheckError) as caught:
                        placement_defects.before_check(Ctx(run, manifest, spec, time.monotonic() + 60))
                    self.assertEqual(caught.exception.execution, 'malformed')
                    with self.assertRaises(ValueError):
                        placement_defects.verify_gate(run, {'id': 'fab', 'execution': 'ok', 'outcome': 'pass'}, manifest)

    def test_write_scope_admits_extension_and_refuses_drop_or_rewrite(self):
        from agent_ee import placement_adapter as pa
        one, two = stamped(self.board, self.a), stamped(self.board, self.a, self.b)
        for before, after in ((self.board, one), (one, one), (one, two)):
            with self.subTest(before=before[:120], after=after[:120]):
                self.assertEqual(pa.write_scope(before, after)['status'], 'allowed')
        for before, after in ((one, self.board), (two, one), (one, stamped(self.board, self.b)),
                              (self.board, stamped(self.board, self.a, block='(title "T") '))):
            with self.subTest(before=before[:120], after=after[:120]):
                self.assertEqual(pa.write_scope(before, after)['status'], 'refused')

    def test_stamped_board_without_lineage_refuses_release_as_unknown(self):
        import tempfile
        from agent_ee.checks import CheckError, Ctx
        from agent_ee.fab_package import artifact_record
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / 'inputs').mkdir()
            spec = {'id': 'fab', 'kind': 'fab_export'}
            manifest = {'candidate_id': 'c' * 64, 'scope': {'board': 'b.kicad_pcb', 'checks': [spec]},
                        'files': {'b.kicad_pcb': 'f' * 64}}
            (run / 'inputs/b.kicad_pcb').write_bytes(self.board)
            self.assertEqual(placement_defects.open_defects(manifest, run / 'inputs'), [])
            (run / 'inputs/b.kicad_pcb').write_bytes(stamped(self.board, self.a))
            with self.assertRaisesRegex(ValueError, 'lineage'):
                placement_defects.open_defects(manifest, run / 'inputs')
            ctx = Ctx(run, manifest, spec, time.monotonic() + 60)
            with self.assertRaisesRegex(CheckError, 'lineage'):
                placement_defects.before_check(ctx)
            write_json(run / 'candidate.json', manifest)
            with self.assertRaisesRegex(ValueError, 'lineage'):
                artifact_record(run, {'id': 'package', 'kind': 'fab_package', 'execution': 'ok', 'outcome': 'pass'})
            placement_defects.verify_gate(run, {'id': 'fab', 'execution': 'malformed', 'outcome': 'unknown'}, manifest)
            with self.assertRaisesRegex(ValueError, 'without its placement lineage'):
                placement_defects.verify_gate(run, {'id': 'fab', 'execution': 'ok', 'outcome': 'pass'}, manifest)
            # Ungated checks keep their verdicts.
            ungated = {'id': 'erc', 'kind': 'erc'}
            manifest['scope']['checks'].append(ungated)
            self.assertTrue(placement_defects.before_check(Ctx(run, manifest, ungated, time.monotonic() + 60)))
            placement_defects.verify_gate(run, {'id': 'erc', 'execution': 'ok', 'outcome': 'pass'}, manifest)


class NativePlacementCarryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        from test_placement_adapter import ReceiptIterationTest, BOARD
        from agent_ee import placement_adapter as pa, stage_receipts
        from agent_ee.placer_runner import _plan
        from agent_ee.placement_probe import extract
        import pcbnew as k
        cls.stack = contextlib.ExitStack()
        cls.helper = helper = ReceiptIterationTest()
        helper.receipts = stage_receipts
        helper.setUp()
        cls.stack.callback(helper.doCleanups)
        board = k.LoadBoard(str(helper.source / BOARD))
        for fp in board.GetFootprints():
            fp.Reference().SetVisible(False)
            fp.Value().SetVisible(False)
        if getattr(cls, 'user_comments', None):
            block = board.GetTitleBlock()
            for number, text in cls.user_comments.items():
                block.SetComment(int(number) - 1, text)
            board.SetTitleBlock(block)
        k.SaveBoard(str(helper.source / BOARD), board, True)
        helper.manifest['files'][BOARD] = digest(helper.source / BOARD)
        _, bound, policy, _, refs = pa.binding(helper.declaration, helper.manifest, helper.source, helper.state['mutable_inputs'])
        request = pa.helper_request((helper.source / BOARD).read_bytes(), policy, refs)
        plan = _plan(extract(helper.source / BOARD), request)
        if plan['status'] != 'planned':
            raise AssertionError(plan)
        for placement in plan['placements']:
            if placement['fixed']:
                continue
            x, y = placement['position_nm']
            graphic = k.PCB_SHAPE(board)
            graphic.SetShape(k.SHAPE_T_SEGMENT)
            graphic.SetLayer(k.F_SilkS)
            graphic.SetStart(k.VECTOR2I(x - 1800000, y))
            graphic.SetEnd(k.VECTOR2I(x + 1800000, y))
            graphic.SetWidth(150000)
            board.Add(graphic)
        k.SaveBoard(str(helper.source / BOARD), board, True)
        shutil.copyfile(helper.source / BOARD, helper.design / BOARD)
        helper.manifest['files'][BOARD] = digest(helper.source / BOARD)
        helper.manifest['scope'] = {'schema': 'agent-ee.scope/1', 'board': BOARD,
            'schematic': helper.manifest['scope']['schematic'],
            'inputs': [{'path': str(p), 'at': p.name} for p in helper.source.iterdir()],
            'stages': {'schema': 'agent-ee.stages/1'},
            'checks': [{'id': 'fab', 'kind': 'fab_export', 'params': {'netlist_check': 'netlist'}}]}
        helper.state['history'][0]['run'] = str(helper.root / 'runs/1')
        write_json(helper.root / 'runs/1/candidate.json', helper.manifest)
        record = helper.iterate()
        if record['status'] != 'proposed':
            raise AssertionError(record)
        helper.state['placement_records'] = {'1': digest(helper.work / 'placement-adapter.json')}
        cls.result = read_json(helper.work / record['helper']['receipt'] / pa.RESULT)
        if not cls.result['open_defects']:
            raise AssertionError('native fixture produced no carried defects')
        # Ordinary scope derivation first points at the delivered design.
        scope = copy.deepcopy(helper.manifest['scope'])
        scope['inputs'] = [{'path': str(p), 'at': p.name} for p in helper.design.iterdir()]
        scope.pop('stages', None)
        cls.scope = placement_defects.proposal_scope(scope, helper.work, helper.root, helper.state)
        from agent_ee.candidate import capture
        cls.proposal = helper.work / 'proposal.scope.json'
        write_json(cls.proposal, cls.scope)
        cls.candidate_run = helper.root / 'runs/2'
        cls.candidate_run.mkdir()
        cls.manifest = capture(cls.proposal, cls.candidate_run)

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    def test_native_receipt_is_captured_and_gates_fab_with_field_graphic_names(self):
        from agent_ee.checks import run_checks
        defects = placement_defects.open_defects(self.manifest, self.candidate_run / 'inputs')
        self.assertEqual(len(defects), len(self.result['open_defects']))
        checks = run_checks(self.candidate_run, time.monotonic() + 60)
        self.assertEqual((checks[0]['execution'], checks[0]['outcome']), ('ok', 'defect'))
        self.assertEqual(checks[0]['details']['reason'], 'open_placement_defects')
        for d in defects:
            self.assertIn(d['source_receipt']['receipt'], str(checks[0]['details']['gate_findings']))
            for item in d.get('native', {}).get('items', []):
                self.assertIn(item['uuid'], str(checks[0]['details']['gate_findings']))
        self.assertFalse((self.candidate_run / 'work/fab/fab/manifest.json').exists())

    def test_successor_binding_and_erased_or_foreign_carry(self):
        helper = self.helper
        placement_defects.verify_candidate(helper.root, helper.state, 2, self.manifest)
        for mutation in ('declaration', 'artifact', 'receipt'):
            manifest = copy.deepcopy(self.manifest)
            if mutation == 'declaration':
                manifest['scope'].pop(placement_defects.DECLARATION)
            elif mutation == 'artifact':
                manifest['files'].pop(next(p for p in manifest['files'] if p.startswith(placement_defects.DIRECTORY)))
            else:
                manifest['scope'][placement_defects.DECLARATION][0]['sha256'] = '0' * 64
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, 'lineage'):
                placement_defects.verify_candidate(helper.root, helper.state, 2, manifest)

    def test_inherited_carry_survives_nonplacement_turn(self):
        helper = self.helper
        state = {**helper.state, 'history': [*helper.state['history'], {'run': str(self.candidate_run)}]}
        manifest = copy.deepcopy(self.manifest)
        manifest['scope_path'] = 'not-an-implementation-proposal'
        placement_defects.verify_candidate(helper.root, state, 3, manifest)
        self.assertEqual(placement_defects.delivered_view(manifest['files'], helper.manifest['files']),
                         helper.manifest['files'] | {p: sha for p, sha in manifest['files'].items()
                                                   if not p.startswith(placement_defects.DIRECTORY + '/')})

    def test_missing_or_tampered_evidence_refuses_as_unknown(self):
        from agent_ee.checks import Ctx, CheckError
        for mutation in ('sha', 'missing', 'malformed', 'orphan'):
            manifest = copy.deepcopy(self.manifest)
            link = manifest['scope'][placement_defects.DECLARATION][0]
            if mutation == 'sha':
                link['sha256'] = '0' * 64
            elif mutation == 'missing':
                manifest['files'].pop(link['receipt'])
            elif mutation == 'malformed':
                manifest['scope'][placement_defects.DECLARATION] = {}
            else:
                manifest['scope'].pop(placement_defects.DECLARATION)
                manifest['files'].pop(f'{placement_defects.DIRECTORY}/index.json')
            ctx = Ctx(self.candidate_run, manifest, manifest['scope']['checks'][0], time.monotonic() + 60)
            with self.subTest(mutation=mutation), self.assertRaises(CheckError):
                placement_defects.before_check(ctx)
            self.assertNotEqual(ctx.result['outcome'], 'pass')

    def test_verification_and_release_gates_refuse_same_carry(self):
        from agent_ee.checks import Ctx
        for spec in ({'id': 'verification', 'kind': 'geometry', 'stage': 'verification'},
                     {'id': 'release', 'kind': 'fab_package'},
                     {'id': 'fabstage', 'kind': 'geometry', 'stage': 'fab'}):
            ctx = Ctx(self.candidate_run, self.manifest, spec, time.monotonic() + 60)
            with self.subTest(spec=spec):
                self.assertFalse(placement_defects.before_check(ctx))
                self.assertEqual(ctx.result['outcome'], 'defect')

    def test_board_edit_alone_does_not_close_carry(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['files'][manifest['scope']['board']] = '0' * 64
        self.assertTrue(placement_defects.open_defects(manifest, self.candidate_run / 'inputs'))

    def test_gate_witness_replay_and_release_cannot_bless_open_carry(self):
        from agent_ee.checks import run_checks
        from agent_ee.fab_package import artifact_record
        check = run_checks(self.candidate_run, time.monotonic() + 60)[0]
        placement_defects.verify_gate(self.candidate_run, check, self.manifest)
        wrong = copy.deepcopy(check)
        wrong['outcome'] = 'pass'
        with self.assertRaisesRegex(ValueError, 'did not retain'):
            placement_defects.verify_gate(self.candidate_run, wrong, self.manifest)
        with self.assertRaisesRegex(ValueError, 'carried open'):
            artifact_record(self.candidate_run, {'id': 'package', 'kind': 'fab_package',
                                              'execution': 'ok', 'outcome': 'pass'})
        witness = self.candidate_run / 'work/fab/placement-defects.json'
        original = witness.read_bytes()
        try:
            write_json(witness, {})
            with self.assertRaisesRegex(ValueError, 'witness changed'):
                placement_defects.verify_gate(self.candidate_run, check, self.manifest)
        finally:
            witness.write_bytes(original)

    def test_nested_fab_scope_inherits_index_without_scope_declaration(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['scope'].pop(placement_defects.DECLARATION)
        self.assertEqual(placement_defects.open_defects(manifest, self.candidate_run / 'inputs'),
                         placement_defects.open_defects(self.manifest, self.candidate_run / 'inputs'))


    def test_later_receipted_empty_delta_keeps_baseline_silk_defects_open(self):
        from agent_ee import placement_adapter as pa
        from agent_ee.candidate import capture
        helper = self.helper
        work = helper.root / 'implementations/2'
        design = work / 'review-work/implementation/design'
        shutil.copytree(self.candidate_run / 'inputs', design)
        state = copy.deepcopy(helper.state)
        state['history'].append({'run': str(self.candidate_run), 'earliest_failing_stage': 'placement'})
        source = copy.deepcopy(self.manifest)
        # The adapter's dispatch fixture uses a stage marker, as its native fixture does.
        source['scope']['stages'] = {'schema': 'agent-ee.stages/1'}
        record = pa.run_placement_iteration(work, design, self.candidate_run / 'inputs', source,
            state, helper.declaration, time.monotonic() + 300)
        state['placement_records']['2'] = digest(work / 'placement-adapter.json')
        result = read_json(work / record['helper']['receipt'] / pa.RESULT)
        self.assertEqual(result['open_defects'], [])
        self.assertEqual(result['native_drc_comparison']['added_by_type'], {})
        # A clean later result keeps the source board's lineage stamp (AGENTEE-351).
        self.assertEqual(placement_defects.stamp((design / self.manifest['scope']['board']).read_bytes()),
                         placement_defects.stamp((self.candidate_run / 'inputs' / self.manifest['scope']['board']).read_bytes()))
        scope = copy.deepcopy(self.scope)
        scope['inputs'] = [{'path': str(p), 'at': p.name} for p in design.iterdir()]
        scope = placement_defects.proposal_scope(scope, work, helper.root, state)
        proposal = work / 'proposal.scope.json'
        write_json(proposal, scope)
        run = helper.root / 'runs/3'
        run.mkdir()
        manifest = capture(proposal, run)
        placement_defects.verify_candidate(helper.root, state, 3, manifest)
        defects = placement_defects.open_defects(manifest, run / 'inputs')
        self.assertEqual(len(defects), len(self.result['open_defects']))
        self.assertTrue(all(d['source_receipt'] == self.scope[placement_defects.DECLARATION][0]
                            for d in defects))


    def test_carried_evidence_seals_without_changing_immutable_baseline(self):
        from agent_ee.baseline import compile_baseline, freeze_evidence, verify_evidence
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        root = self.helper.root / 'sealed-carry'
        root.mkdir()
        requirement = root / 'requirements.md'
        requirement.write_text('Fab must refuse carried open artwork defects.\n')
        scope = copy.deepcopy(self.scope)
        scope['schema'] = 'agent-ee.scope/2'
        scope['inputs'].append({'path': str(requirement), 'at': 'requirements.md'})
        scope['baseline'] = {'requirements': [{'id': 'R-fab', 'statement': requirement.read_text().strip(),
            'source': {'path': 'requirements.md', 'locator': 'R-fab'}, 'checks': ['fab']}],
            'targets': ['requirements.md'], 'reference_architectures': ['requirements.md']}
        proposal = root / 'scope.json'
        write_json(proposal, scope)
        run = root / 'run'
        run.mkdir()
        manifest = capture(proposal, run)
        sealed = copy.deepcopy(scope)
        sealed.pop(placement_defects.DECLARATION)
        original_files = {p: sha for p, sha in manifest['files'].items()
                          if not p.startswith(placement_defects.DIRECTORY + '/')}
        self.assertEqual(compile_baseline(scope, manifest['files'], run / 'inputs'),
                         compile_baseline(sealed, original_files, run / 'inputs'))
        checks = run_checks(run, time.monotonic() + 60)
        self.assertEqual(checks[0]['outcome'], 'defect')
        freeze_evidence(run, checks)
        verify_evidence(run, checks, required=True)


    def test_native_writer_stamps_its_carry_digest_on_drc_checked_bytes(self):
        from agent_ee import placement_adapter as pa
        board = self.candidate_run / 'inputs' / self.manifest['scope']['board']
        self.assertIsNone(placement_defects.stamp((self.helper.source / self.manifest['scope']['board']).read_bytes()))
        self.assertEqual(placement_defects.stamp(board.read_bytes()),
                         [placement_defects.carry_digest(self.result['open_defects'])])
        self.assertEqual(self.result['board_sha256'], digest(board))
        reversed_scope = pa.write_scope(board.read_bytes(), (self.helper.source / self.manifest['scope']['board']).read_bytes())
        self.assertIn('placement lineage stamp removed or rewritten', reversed_scope['violations'])
        self.assertEqual(self.result['write_scope']['status'], 'allowed')

    def rescope(self, name, *, lineage, edit=None):
        """Release a delivered candidate's files under a fresh, out-of-campaign scope.

        `edit(design)` changes the delivered files before the fresh capture."""
        from agent_ee.candidate import capture
        root = self.helper.root / name
        design = root / 'design'
        shutil.copytree(self.candidate_run / 'inputs', design,
                        ignore=None if lineage else shutil.ignore_patterns(placement_defects.DIRECTORY))
        if edit is not None:
            edit(design)
        scope = {'schema': 'agent-ee.scope/1', 'board': self.manifest['scope']['board'],
                 'schematic': self.manifest['scope']['schematic'],
                 'inputs': [{'path': str(p), 'at': p.name} for p in design.iterdir()],
                 'checks': copy.deepcopy(self.manifest['scope']['checks'])}
        write_json(root / 'scope.json', scope)
        run = root / 'run'
        run.mkdir()
        return run, capture(root / 'scope.json', run)

    def test_rescoped_release_without_lineage_refuses_as_unknown(self):
        from agent_ee.checks import run_checks
        from agent_ee.fab_package import artifact_record
        run, manifest = self.rescope('rescoped-bare', lineage=False)
        self.assertFalse(placement_defects.lineage(manifest))
        check = run_checks(run, time.monotonic() + 60)[0]
        self.assertEqual((check['execution'], check['outcome']), ('malformed', 'unknown'))
        self.assertIn('placement lineage stamp', check['summary'])
        self.assertFalse((run / 'work/fab/fab/manifest.json').exists())
        placement_defects.verify_gate(run, check, manifest)
        with self.assertRaisesRegex(ValueError, 'without its placement lineage'):
            placement_defects.verify_gate(run, {**check, 'execution': 'ok', 'outcome': 'pass'}, manifest)
        with self.assertRaisesRegex(ValueError, 'lineage'):
            artifact_record(run, {'id': 'package', 'kind': 'fab_package', 'execution': 'ok', 'outcome': 'pass'})

    def test_stripped_or_never_stamped_carried_board_cannot_pass_release(self):
        """H1: without lineage, only native DRC on the captured board can admit a release."""
        import pcbnew
        from agent_ee.checks import Ctx
        from agent_ee.fab_package import artifact_record
        board_rel = self.manifest['scope']['board']

        def native_strip(design):
            # An actor outside agent-ee clears the stamp with the native editor API.
            board = pcbnew.LoadBoard(str(design / board_rel))
            block = board.GetTitleBlock()
            block.SetComment(int(placement_defects.STAMP_COMMENT) - 1, '')
            board.SetTitleBlock(block)
            self.assertTrue(pcbnew.SaveBoard(str(design / board_rel), board))

        def never_stamped(design):
            # A carried board published before the stamp contract: the same bytes, no comment 9.
            data = (design / board_rel).read_bytes()
            line = re.search(rb'\n\s*\(comment 9 "agent-ee\.placement-lineage/1 [0-9a-f ]+"\)', data)
            (design / board_rel).write_bytes(data.replace(line.group(0), b''))
        for name, edit in (('stripped', native_strip), ('never-stamped', never_stamped)):
            with self.subTest(name=name):
                run, manifest = self.rescope(f'rescoped-{name}', lineage=False, edit=edit)
                self.assertIsNone(placement_defects.stamp((run / 'inputs' / board_rel).read_bytes()))
                self.assertEqual(placement_defects.open_defects(manifest, run / 'inputs'), [])
                spec = manifest['scope']['checks'][0]
                ctx = Ctx(run, manifest, spec, time.monotonic() + 300)
                self.assertTrue(placement_defects.before_check(ctx))
                # The standalone check is unchanged; the release boundary captures native evidence.
                placement_defects.prepare_release(ctx)
                witness = read_json(run / 'work' / spec['id'] / placement_defects.WITNESS)
                self.assertEqual(witness['board_sha256'], manifest['files'][board_rel])
                self.assertTrue(witness['silk_findings'])
                with self.assertRaisesRegex(ValueError, 'silkscreen conflict.*no placement lineage'):
                    artifact_record(run, {'id': spec['id'], 'kind': 'fab_package',
                                          'execution': 'ok', 'outcome': 'pass'})
                with self.assertRaisesRegex(ValueError, 'without native placement lineage verification'):
                    artifact_record(run, {'id': 'package', 'kind': 'fab_package',
                                          'execution': 'ok', 'outcome': 'pass'})

    def closure(self, name, *, honest, rules=None, residual=False):
        """Re-scope with an appended imported placement bundle that claims the carry closed.

        The honest bundle moves the footprints off the silk graphics and records native DRC
        of those bytes; the forged one keeps the board and deletes the silk findings."""
        import pcbnew
        from agent_ee.placer_runner import _native_drc
        from agent_ee.storage import identity
        board_rel = self.manifest['scope']['board']

        def append(design):
            board = design / board_rel
            index = design / placement_defects.DIRECTORY / 'index.json'
            links = read_json(index)
            original = design / Path(links[0]['receipt']).parent
            if honest:
                native = pcbnew.LoadBoard(str(board))
                # Clear the silk graphics while staying inside the 30 mm outline, so the
                # AGENTEE-350 containment witness can still contain the moved markings.
                for fp in native.GetFootprints():
                    fp.Move(pcbnew.VECTOR2I(pcbnew.FromMM(18), 0))
                if residual:
                    # Uncarried board-level silk across the outline: a warning-severity
                    # silk_edge_clearance that shares no item with the carried defects.
                    edge = native.GetBoardEdgesBoundingBox()
                    y = (edge.GetTop() + edge.GetBottom()) // 2
                    graphic = pcbnew.PCB_SHAPE(native)
                    graphic.SetShape(pcbnew.SHAPE_T_SEGMENT)
                    graphic.SetLayer(pcbnew.F_SilkS)
                    graphic.SetStart(pcbnew.VECTOR2I(edge.GetLeft() - pcbnew.FromMM(2), y))
                    graphic.SetEnd(pcbnew.VECTOR2I(edge.GetLeft() + pcbnew.FromMM(2), y))
                    graphic.SetWidth(150000)
                    native.Add(graphic)
                self.assertTrue(pcbnew.SaveBoard(str(board), native))
            bundle = design / placement_defects.DIRECTORY / f'imported-{name}'
            shutil.copytree(original, bundle)
            shutil.copyfile(board, bundle / placement_defects.BOARD)
            drc = bundle / placement_defects.DRC
            if honest:
                scratch = design.parent / 'native-drc'
                scratch.mkdir()
                _native_drc(board, scratch)
                shutil.copyfile(scratch / 'native-drc.json', drc)
                shutil.copyfile(scratch / 'native-drc-execution.json', bundle / placement_defects.EXECUTION)
            else:
                report = read_json(drc)
                report['violations'] = [v for v in report['violations'] if not v['type'].startswith('silk_')]
                write_json(drc, report)
            result = read_json(bundle / placement_defects.REPORT)
            result.update(board_sha256=digest(board), open_defects=[], outcome='pass', verdict='pass')
            if honest:
                # A real closure under AGENTEE-350 binds containment to the moved board.
                from agent_ee.silk_artwork import containment_witness
                result['containment'] = containment_witness(board)
            result['native_drc_comparison']['open_defects'] = []
            write_json(bundle / placement_defects.REPORT, result)
            if rules is not None:
                board.with_suffix('.kicad_dru').write_text(rules)
            receipt = read_json(bundle / 'receipt.json')
            receipt['outputs'][board_rel] = digest(board)
            if honest:
                # An honest receipt binds the DRC project/rule inputs it ran with (AGENTEE-344).
                for path in design.rglob('*'):
                    rel = path.relative_to(design).as_posix()
                    if (path.suffix in ('.kicad_pro', '.kicad_dru') and
                            not rel.startswith(placement_defects.DIRECTORY + '/')):
                        receipt['inputs'][rel] = digest(path)
            for artifact in (*placement_defects.FILES, placement_defects.ARTWORK):
                if artifact in receipt['artifacts']:
                    receipt['artifacts'][artifact] = digest(bundle / artifact)
            receipt['identity'] = identity({k: v for k, v in receipt.items() if k != 'identity'})
            write_json(bundle / 'receipt.json', receipt)
            links.append({'receipt': f'{placement_defects.DIRECTORY}/imported-{name}/receipt.json',
                          'sha256': digest(bundle / 'receipt.json')})
            write_json(index, links)
        return self.rescope(f'closure-{name}', lineage=True, edit=append)

    def test_imported_closure_must_match_native_drc_on_the_captured_board(self):
        """H2: self-consistent imported evidence cannot close carry; native recomputation can."""
        from agent_ee.checks import CheckError, Ctx, run_checks
        from agent_ee.fab_package import artifact_record
        run, manifest = self.closure('forged', honest=False)
        spec = manifest['scope']['checks'][0]
        with self.assertRaisesRegex(ValueError, 'unverifiable without native DRC'):
            placement_defects.open_defects(manifest, run / 'inputs')
        with self.assertRaisesRegex(CheckError, 'differs from native DRC'):
            placement_defects.before_check(Ctx(run, manifest, spec, time.monotonic() + 300))
        forged = {'id': spec['id'], 'kind': 'fab_package', 'execution': 'ok', 'outcome': 'pass'}
        with self.assertRaisesRegex(ValueError, 'differs from native DRC'):
            placement_defects.verify_gate(run, forged, manifest)
        with self.assertRaisesRegex(ValueError, 'differs from native DRC'):
            artifact_record(run, forged)
        check = run_checks(run, time.monotonic() + 300)[0]
        self.assertEqual((check['execution'], check['outcome']), ('malformed', 'unknown'))
        self.assertFalse((run / 'work/fab/fab/manifest.json').exists())
        # Honest closure, independently reproduced in the sandbox, still closes the carry.
        run, manifest = self.closure('honest', honest=True)
        self.assertTrue(placement_defects.stamp((run / 'inputs' / manifest['scope']['board']).read_bytes()))
        with self.assertRaisesRegex(ValueError, 'unverifiable without native DRC'):
            placement_defects.open_defects(manifest, run / 'inputs')
        self.assertTrue(placement_defects.before_check(Ctx(run, manifest, spec, time.monotonic() + 300)))
        placement_defects.verify_gate(run, forged, manifest)
        with self.assertRaisesRegex(ValueError, 'manifest is absent'):
            artifact_record(run, forged)
        witness = run / 'work' / spec['id'] / placement_defects.WITNESS
        changed = read_json(witness)
        changed['silk_findings'] = [['silk_overlap', ['forged']]]
        write_json(witness, changed)
        with self.assertRaisesRegex(ValueError, 'witness missing or changed'):
            placement_defects.verify_gate(run, forged, manifest)
        unverified = {**manifest, 'scope': {**manifest['scope'], 'checks': [
            *manifest['scope']['checks'], {'id': 'package', 'kind': 'fab_package'}]}}
        with self.assertRaisesRegex(ValueError, 'without native placement lineage verification'):
            placement_defects.verify_gate(run, {**forged, 'id': 'package'}, unverified)
        with self.assertRaisesRegex(ValueError, 'without native placement lineage verification'):
            artifact_record(run, {**forged, 'id': 'package'})
        # Native verification that cannot run, or cannot be trusted, leaves the closure unknown.
        with self.assertRaises(CheckError) as caught:
            placement_defects.before_check(Ctx(run, manifest, spec, time.monotonic() - 1))
        self.assertEqual(caught.exception.execution, 'interrupted')
        run, manifest = self.closure('ignored', honest=True, rules='(version 1)\n(rule quiet (severity ignore))\n')
        with self.assertRaisesRegex(CheckError, 'custom DRC rule ignores'):
            placement_defects.before_check(Ctx(run, manifest, spec, time.monotonic() + 300))

    def test_out_of_campaign_closed_carry_must_be_natively_silk_clean_at_any_severity(self):
        """AGENTEE-412: an honest imported closure clears the carry, but out of campaign the
        release still needs a natively clean board; a residual warning-severity silk finding refuses."""
        from agent_ee.checks import Ctx
        from agent_ee.fab_package import artifact_record
        run, manifest = self.closure('residual', honest=True, residual=True)
        self.assertNotIn(placement_defects.DECLARATION, manifest['scope'])
        self.assertTrue(placement_defects.lineage(manifest))
        spec = manifest['scope']['checks'][0]
        ctx = Ctx(run, manifest, spec, time.monotonic() + 300)
        # The standalone gate closes the carry on matching native DRC and keeps its verdict.
        self.assertTrue(placement_defects.before_check(ctx))
        recorded = placement_defects._recorded(run, spec['id'], manifest)
        self.assertEqual([kind for kind, _ in recorded], ['silk_edge_clearance'])
        report = read_json(run / 'work' / spec['id'] / placement_defects.VERIFICATION / 'native-drc.json')
        self.assertEqual({v['severity'] for v in report['violations'] if v['type'].startswith('silk_')}, {'warning'})
        carried = {u for d in self.result['open_defects'] for u in placement_defects._items(d)}
        self.assertFalse(carried & set(recorded[0][1]))
        self.assertEqual(placement_defects.open_defects(manifest, run / 'inputs', lambda: recorded), [])
        passed = {'id': spec['id'], 'kind': 'fab_package', 'execution': 'ok', 'outcome': 'pass'}
        package = {**manifest, 'scope': {**manifest['scope'], 'checks': [{**spec, 'kind': 'fab_package'}]}}
        with self.assertRaisesRegex(ValueError, 'silkscreen conflict.*not yet authenticated'):
            placement_defects.release_defects(run, passed, package)
        with self.assertRaisesRegex(ValueError, 'silkscreen conflict'):
            placement_defects.verify_gate(run, passed, package)
        write_json(run / 'candidate.json', package)
        with self.assertRaisesRegex(ValueError, 'silkscreen conflict'):
            artifact_record(run, passed)

    def closure_projection(self, name, *, honest):
        """A receipted closure lineage that still declares the placement projection, audited."""
        from agent_ee.candidate import capture
        run, _ = self.closure(name, honest=honest)
        root = run.parent
        requirement = root / 'requirements.md'
        requirement.write_text('A natively closed carry must keep the placement projection usable.\n')
        scope = read_json(root / 'scope.json')
        scope['schema'] = 'agent-ee.scope/2'
        scope['inputs'].append({'path': str(requirement), 'at': 'requirements.md'})
        scope['checks'] = [{'id': 'drc-placement', 'kind': 'drc', 'params': {'projection': 'placement-stage/1'}},
                           {'id': 'drc', 'kind': 'drc'}]
        scope['baseline'] = {'requirements': [{'id': 'R-native', 'statement': requirement.read_text().strip(),
            'source': {'path': 'requirements.md', 'locator': 'R-native'}, 'checks': ['drc-placement', 'drc']}],
            'targets': ['requirements.md'], 'reference_architectures': ['requirements.md']}
        write_json(root / 'audited.scope.json', scope)
        audited = root / 'audited'
        audited.mkdir()
        return audited, capture(root / 'audited.scope.json', audited)

    def test_projection_consumes_a_natively_closed_carry_and_seals(self):
        """Ruling 346d H1: the projection verifies closure natively, like the full gate."""
        from agent_ee.baseline import freeze_evidence, verify_evidence
        from agent_ee.checks import run_checks
        run, manifest = self.closure_projection('projection-closed', honest=True)
        self.assertTrue(placement_defects.lineage(manifest))
        with self.assertRaisesRegex(ValueError, 'unverifiable without native DRC'):
            placement_defects.open_defects(manifest, run / 'inputs')
        projection, full = run_checks(run, time.monotonic() + 300)
        self.assertEqual(projection['execution'], 'ok', projection['summary'])
        self.assertEqual(full['execution'], 'ok', full['summary'])
        self.assertNotIn('open_defects', projection['details'])
        self.assertNotIn('reason', full['details'])
        self.assertIn(f'work/drc-placement/{placement_defects.WITNESS}', projection['evidence'])
        self.assertEqual(placement_defects.open_defects(
            manifest, run / 'inputs', lambda: placement_defects._recorded(run, 'drc-placement', manifest)), [])
        report = read_json(run / 'work/drc-placement/drc.json')
        self.assertFalse(any(v['type'].startswith('silk_') for v in report['violations']), report)
        # With the carry closed, the projection grades its native violations and parity as usual.
        self.assertEqual(projection['outcome'],
                         'defect' if report['violations'] or report['schematic_parity'] else 'pass', report)
        freeze_evidence(run, [projection, full])
        verify_evidence(run, [projection, full], required=True)
        # Replay is tool-free and binds the retained native witness.
        witness = run / 'work/drc-placement' / placement_defects.WITNESS
        original = read_json(witness)
        changed = dict(original, silk_findings=[['silk_overlap', ['forged']]])
        write_json(witness, changed)
        with self.assertRaisesRegex(ValueError, 'witness missing or changed'):
            placement_defects.verify_gate(run, projection, manifest)
        with self.assertRaisesRegex(ValueError, 'witness missing or changed'):
            verify_evidence(run, [projection, full], required=True)
        write_json(witness, original)
        verify_evidence(run, [projection, full], required=True)

        # A self-consistent imported false closure still refuses (AGENTEE-351 unchanged).
        run, manifest = self.closure_projection('projection-forged', honest=False)
        projection, full = run_checks(run, time.monotonic() + 300)
        for check in (projection, full):
            self.assertEqual((check['execution'], check['outcome']), ('malformed', 'unknown'))
            self.assertIn('differs from native DRC', check['summary'])
        forged = dict(projection, execution='ok', outcome='pass', details={})
        with self.assertRaisesRegex(ValueError, 'differs from native DRC|witness missing or changed|'
                                                'without native placement lineage verification'):
            placement_defects.verify_gate(run, forged, manifest)

    def test_fabricated_stamp_and_imported_lineage_cannot_waive_release_witness(self):
        """H1: a self-asserted stamp and a self-hashed imported closure cannot release a
        natively silk-conflicted board; only a clean sealed native witness can."""
        from agent_ee.fab_package import artifact_record
        from agent_ee.placer_runner import _native_drc
        from agent_ee.storage import identity
        from test_fab_package import FIXTURE, NativeRun, generation_scope
        runner = NativeRun()
        runner.setUp()
        self.addCleanup(runner.doCleanups)
        source = runner.root / 'attack'
        shutil.copytree(FIXTURE, source)
        board = source / 'fab.kicad_pcb'
        board.write_text(board.read_text().replace('(at 0 -2 0)', '(at 0 0 0)', 1))
        (runner.root / 'drc-before').mkdir()
        silk = placement_defects.silk_findings(_native_drc(board, runner.root / 'drc-before'))
        self.assertTrue(silk)
        conflicted = {u for finding in silk for u in finding[1]}
        prop = next(u for u, (_, node, _) in sorted(placement_defects._identities(board).items())
                    if node[0] == 'property' and u not in conflicted)
        evidence = self.candidate_run / 'inputs' / placement_defects.DIRECTORY
        template = self.candidate_run / 'inputs' / Path(read_json(evidence / 'index.json')[0]['receipt']).parent
        fake = copy.deepcopy(read_json(template / placement_defects.REPORT)['open_defects'][0])
        self.assertIn('native', fake)
        fake['native']['items'] = [{'description': 'fabricated', 'uuid': prop}]
        # The attacker writes its own stamp text; it names only the fabricated carry.
        board.write_text(board.read_text().replace('(paper "A4")', '(paper "A4")\n\t(title_block\n\t\t(comment 9 "'
                         f'{placement_defects.STAMP} {placement_defects.carry_digest([fake])}")\n\t)', 1))
        (runner.root / 'drc-stamped').mkdir()
        self.assertEqual(placement_defects.silk_findings(_native_drc(board, runner.root / 'drc-stamped')), silk)
        scope = generation_scope('agent-ee.scope/3')
        scope['inputs'][0]['path'] = str(source)
        board_rel = scope['board']
        imported = runner.root / 'evidence'

        def bundle(name, defects):
            # Self-hashed receipts: one opens the fabricated carry, the next closes it with
            # honest native DRC of the conflicted board and a self-asserted containment.
            target = imported / name
            shutil.copytree(template, target)
            shutil.copyfile(board, target / placement_defects.BOARD)
            shutil.copyfile(runner.root / 'drc-stamped/native-drc.json', target / placement_defects.DRC)
            shutil.copyfile(runner.root / 'drc-stamped/native-drc-execution.json', target / placement_defects.EXECUTION)
            report = read_json(target / placement_defects.REPORT)
            report.update(board_sha256=digest(board), source_sha256=digest(board), open_defects=defects,
                          containment={'board_sha256': digest(board), 'markings': [prop]})
            report['native_drc_comparison']['open_defects'] = defects
            if not defects:
                report.update(outcome='pass', verdict='pass')
            write_json(target / placement_defects.REPORT, report)
            receipt = read_json(target / 'receipt.json')
            receipt['inputs'] = receipt['outputs'] = {board_rel: digest(board)}
            for artifact in (*placement_defects.FILES, placement_defects.ARTWORK):
                if artifact in receipt['artifacts']:
                    receipt['artifacts'][artifact] = digest(target / artifact)
            receipt['identity'] = identity({k: v for k, v in receipt.items() if k != 'identity'})
            write_json(target / 'receipt.json', receipt)
            return {'receipt': f'{placement_defects.DIRECTORY}/{name}/receipt.json',
                    'sha256': digest(target / 'receipt.json')}
        imported.mkdir()
        write_json(imported / 'index.json', [bundle('fabricated', [fake]), bundle('closure', [])])
        scope['inputs'].append({'path': str(imported), 'at': placement_defects.DIRECTORY})
        result = runner.run_case(scope=scope)
        self.assertEqual((result['execution'], result['outcome']), ('malformed', 'unknown'))
        self.assertIn('silkscreen conflict', result['summary'])
        self.assertNotIn('artifacts', result['details'])
        run = runner.run_dir
        manifest = read_json(run / 'candidate.json')
        # The forgery is self-consistent: lineage and stamp are present and bound, and the
        # sealed native witness agrees with the closure, so no carry remains open.
        self.assertTrue(placement_defects.lineage(manifest))
        self.assertTrue(placement_defects._board_stamp(manifest, run / 'inputs'))
        recorded = placement_defects._recorded(run, result['id'], manifest)
        self.assertEqual(recorded, silk)
        self.assertEqual(placement_defects.open_defects(manifest, run / 'inputs', lambda: recorded), [])
        passed = {**result, 'execution': 'ok', 'outcome': 'pass'}
        with self.assertRaisesRegex(ValueError, 'silkscreen conflict.*not yet authenticated'):
            placement_defects.release_defects(run, passed, manifest)
        with self.assertRaisesRegex(ValueError, 'silkscreen conflict'):
            artifact_record(run, passed)
        with self.assertRaisesRegex(ValueError, 'silkscreen conflict'):
            placement_defects.verify_gate(run, passed, manifest)

    def test_rescoped_release_with_lineage_keeps_open_carry(self):
        from agent_ee.checks import run_checks
        run, manifest = self.rescope('rescoped-lineage', lineage=True)
        self.assertNotIn(placement_defects.DECLARATION, manifest['scope'])
        defects = placement_defects.open_defects(manifest, run / 'inputs')
        self.assertEqual(defects, placement_defects.open_defects(self.manifest, self.candidate_run / 'inputs'))
        check = run_checks(run, time.monotonic() + 60)[0]
        self.assertEqual((check['execution'], check['outcome'], check['details']['reason']),
                         ('ok', 'defect', 'open_placement_defects'))
        placement_defects.verify_gate(run, check, manifest)

    def test_stamp_unbound_to_captured_lineage_refuses(self):
        board = self.candidate_run / 'inputs' / self.manifest['scope']['board']
        original = board.read_bytes()
        digest_value = placement_defects.carry_digest(self.result['open_defects'])
        try:
            for data in (original.replace(digest_value.encode(), b'0' * 64),
                         original.replace(digest_value.encode(), (digest_value + ' ' + '0' * 64).encode())):
                board.write_bytes(data)
                with self.subTest(data=data[:0]), self.assertRaisesRegex(ValueError, 'not bound'):
                    placement_defects.open_defects(self.manifest, self.candidate_run / 'inputs')
        finally:
            board.write_bytes(original)

    def test_campaign_successor_cannot_drop_board_stamp(self):
        helper = self.helper
        state = {**helper.state, 'history': [*helper.state['history'], {'run': str(self.candidate_run)}]}
        manifest = copy.deepcopy(self.manifest)
        manifest['scope_path'] = 'not-an-implementation-proposal'
        placement_defects.verify_candidate(helper.root, state, 3, manifest, self.candidate_run / 'inputs')
        successor = helper.root / 'runs/stripped/inputs'
        shutil.copytree(self.candidate_run / 'inputs', successor)
        board = successor / manifest['scope']['board']
        board.write_bytes(board.read_bytes().replace(placement_defects.STAMP.encode(), b'erased'))
        with self.assertRaisesRegex(ValueError, 'placement lineage stamp'):
            placement_defects.verify_candidate(helper.root, state, 3, manifest, successor)

    def test_forged_retained_pass_is_refused_by_freeze_evidence(self):
        from agent_ee.baseline import freeze_evidence
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        root = self.helper.root / 'seal-forgery'
        root.mkdir()
        requirement = root / 'requirements.md'
        requirement.write_text('Fab must refuse carried open artwork defects.\n')
        scope = copy.deepcopy(self.scope)
        scope['schema'] = 'agent-ee.scope/2'
        scope['inputs'].append({'path': str(requirement), 'at': 'requirements.md'})
        scope['baseline'] = {'requirements': [{'id': 'R-fab', 'statement': requirement.read_text().strip(),
            'source': {'path': 'requirements.md', 'locator': 'R-fab'}, 'checks': ['fab']}],
            'targets': ['requirements.md'], 'reference_architectures': ['requirements.md']}
        write_json(root / 'scope.json', scope)
        run = root / 'run'
        run.mkdir()
        capture(root / 'scope.json', run)
        checks = run_checks(run, time.monotonic() + 60)
        self.assertEqual(checks[0]['outcome'], 'defect')
        forged = copy.deepcopy(checks[0])
        forged.update(outcome='pass', summary='forged retained fab pass')
        for key in ('reason', 'open_defects', 'gate_findings'):
            forged['details'].pop(key, None)
        write_json(run / 'checks/fab.json', forged)
        with self.assertRaisesRegex(ValueError, 'did not retain'):
            freeze_evidence(run, [forged])

    def projection_run(self, name):
        from agent_ee.candidate import capture
        root = self.helper.root / name
        root.mkdir()
        requirement = root / 'requirements.md'
        requirement.write_text('Native DRC must grade the placed board.\n')
        scope = copy.deepcopy(self.scope)
        scope['schema'] = 'agent-ee.scope/2'
        scope['inputs'].append({'path': str(requirement), 'at': 'requirements.md'})
        scope['checks'] = [{'id': 'drc-placement', 'kind': 'drc', 'params': {'projection': 'placement-stage/1'}},
                           {'id': 'drc', 'kind': 'drc'}]
        scope['baseline'] = {'requirements': [{'id': 'R-native', 'statement': requirement.read_text().strip(),
            'source': {'path': 'requirements.md', 'locator': 'R-native'}, 'checks': ['drc-placement', 'drc']}],
            'targets': ['requirements.md'], 'reference_architectures': ['requirements.md']}
        write_json(root / 'scope.json', scope)
        run = root / 'run'
        run.mkdir()
        return run, capture(root / 'scope.json', run)

    def test_placement_projection_grades_exact_carried_silk_and_full_drc_refuses(self):
        from agent_ee.baseline import freeze_evidence, verify_evidence
        from agent_ee.checks import run_checks
        from agent_ee.silk_artwork import SILK_CONFLICTS
        run, manifest = self.projection_run('projection-carry')
        defects = placement_defects.open_defects(manifest, run / 'inputs')
        projection, full = run_checks(run, time.monotonic() + 120)
        report = read_json(run / 'work/drc-placement/drc.json')
        silk = [f'/violations/{i}' for i, v in enumerate(report['violations']) if v['type'] in SILK_CONFLICTS]
        self.assertTrue(silk, 'native fixture placed board reports no silk conflict')
        self.assertEqual(sorted(projection['details']['carried_findings']), sorted(silk))
        self.assertEqual(projection['details']['open_defects'], defects)
        # Only carried silk conflicts change grade; anything else still blocks.
        blocking = [v['type'] for v in report['violations'] + report['schematic_parity']
                    if v['type'] not in SILK_CONFLICTS]
        self.assertEqual(projection['execution'], 'ok')
        self.assertEqual(projection['outcome'], 'defect' if blocking else 'pass', report)
        self.assertNotIn('reason', projection['details'])
        self.assertIn('graded as carried open placement defects', projection['summary'])
        self.assertTrue(any('carried open placement silkscreen' in l for l in projection['limitations']))
        self.assertIn('work/drc-placement/placement-defects.json', projection['evidence'])
        # The full DRC remains a fab-family gate and refuses the same carry.
        self.assertEqual((full['execution'], full['outcome'], full['details']['reason']),
                         ('ok', 'defect', 'open_placement_defects'))
        freeze_evidence(run, [projection, full])
        verify_evidence(run, [projection, full], required=True)

        forged = copy.deepcopy(projection)
        forged['details']['carried_findings'] = ['/violations/999']
        with self.assertRaisesRegex(ValueError, 'carried grading differs'):
            placement_defects.verify_gate(run, forged, manifest)
        dropped = copy.deepcopy(projection)
        for key in ('open_defects', 'carried_findings'):
            dropped['details'].pop(key)
        with self.assertRaisesRegex(ValueError, 'carried grading differs'):
            placement_defects.verify_gate(run, dropped, manifest)

    def test_projection_seal_reconstructs_the_verdict_over_parity_and_subset_forgeries(self):
        from agent_ee.baseline import freeze_evidence, verify_evidence
        from agent_ee.checks import run_checks
        run, manifest = self.projection_run('projection-verdict')
        projection, full = run_checks(run, time.monotonic() + 120)
        report = read_json(run / 'work/drc-placement/drc.json')
        self.assertTrue(report['schematic_parity'], 'fixture lost its unwaived parity findings')
        self.assertTrue(projection['details']['carried_findings'])
        self.assertEqual((projection['execution'], projection['outcome']), ('ok', 'defect'))
        freeze_evidence(run, [projection, full])
        witness = read_json(run / 'work/drc-placement/placement-defects.json')
        forged_pass = copy.deepcopy(projection)
        forged_pass['outcome'] = 'pass'
        empty = copy.deepcopy(projection)
        empty['details']['carried_findings'] = []
        for name, forged, carried in (('pass over parity', forged_pass, witness['carried_findings']),
                                      ('empty subset', empty, []),
                                      ('empty subset plus pass', dict(empty, outcome='pass'), [])):
            with self.subTest(forgery=name):
                write_json(run / 'checks/drc-placement.json', forged)
                write_json(run / 'work/drc-placement/placement-defects.json', dict(witness, carried_findings=carried))
                with self.assertRaisesRegex(ValueError, 'differs'):
                    freeze_evidence(run, [forged, full])
                with self.assertRaisesRegex(ValueError, 'differs'):
                    verify_evidence(run, [forged, full], required=True)
        write_json(run / 'checks/drc-placement.json', projection)
        write_json(run / 'work/drc-placement/placement-defects.json', witness)
        verify_evidence(run, [projection, full], required=True)

    def uncarried_silk_design(self, name):
        """Parity-clean candidate whose added silk segment crosses C1 under a UUID no receipt carries."""
        import subprocess
        import xml.etree.ElementTree as ET
        import pcbnew as k
        source, _ = self.projection_run(name + '-source')
        root = self.helper.root / name
        design = root / 'design'
        shutil.copytree(source / 'inputs', design)
        scope = copy.deepcopy(read_json(source / 'candidate.json')['scope'])
        path = design / scope['board']
        netlist = root / 'netlist.xml'
        subprocess.run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', str(netlist),
                        str(design / scope['schematic'])], check=True, capture_output=True)
        symbols = {c.get('ref'): (c.findtext('value'), c.findtext('footprint'))
                   for c in ET.parse(netlist).getroot().findall('components/comp')}
        board = k.LoadBoard(str(path))
        for fp in board.GetFootprints():
            value, footprint = symbols[fp.GetReference()]
            fp.SetValue(value)
            fp.SetFPID(k.LIB_ID(*footprint.split(':')))
        graphic = k.PCB_SHAPE(board)
        graphic.SetShape(k.SHAPE_T_SEGMENT)
        graphic.SetLayer(k.F_SilkS)
        graphic.SetStart(k.VECTOR2I(2200000, 10600000))
        graphic.SetEnd(k.VECTOR2I(5800000, 10600000))
        graphic.SetWidth(150000)
        board.Add(graphic)
        uncarried = graphic.m_Uuid.AsString()
        k.SaveBoard(str(path), board, True)
        # Owned stand-in footprints have no library; only that comparison is ignored, never silk.
        write_json(path.with_suffix('.kicad_pro'), {'board': {'design_settings': {'rule_severities': {
            'lib_footprint_issues': 'ignore', 'lib_footprint_mismatch': 'ignore'}}}})
        scope['inputs'] = [{'path': str(design / i['at']), 'at': i['at']} for i in scope['inputs']]
        return root, scope, uncarried

    @staticmethod
    def capture_design(root, scope, name, waive=None):
        from agent_ee.candidate import capture
        scope = copy.deepcopy(scope)
        if waive is not None:
            waivers = root / (name + '-waivers.json')
            write_json(waivers, waive)
            scope['inputs'].append({'path': str(waivers), 'at': 'waivers/drc.json'})
            scope['checks'][0]['params']['waivers'] = 'waivers/drc.json'
        write_json(root / (name + '.scope.json'), scope)
        run = root / name
        run.mkdir()
        return run, capture(root / (name + '.scope.json'), run)

    def test_projection_seal_refuses_a_pass_over_native_uncarried_silk(self):
        from agent_ee.baseline import freeze_evidence, verify_evidence
        from agent_ee.checks import run_checks
        from agent_ee.storage import identity
        from agent_ee.violation_waivers import SCHEMA, violation_key
        root, scope, uncarried = self.uncarried_silk_design('projection-uncarried')
        run, manifest = self.capture_design(root, scope, 'run')
        projection, full = run_checks(run, time.monotonic() + 120)
        report = read_json(run / 'work/drc-placement/drc.json')
        self.assertEqual(report['schematic_parity'], [])
        self.assertFalse(any(c['key'].startswith('silk_') for c in report['ignored_checks']))
        extra = [(i, v) for i, v in enumerate(report['violations'])
                 if uncarried in [item['uuid'] for item in v['items']]]
        self.assertTrue(extra, 'native DRC did not report the added uncarried silk')
        carried = projection['details']['carried_findings']
        self.assertTrue(carried)
        self.assertTrue(all(f'/violations/{i}' not in carried for i, _ in extra))
        self.assertEqual((projection['execution'], projection['outcome']), ('ok', 'defect'))
        freeze_evidence(run, [projection, full])
        verify_evidence(run, [projection, full], required=True)
        forged = dict(projection, outcome='pass')
        write_json(run / 'checks/drc-placement.json', forged)
        with self.assertRaisesRegex(ValueError, 'verdict differs'):
            freeze_evidence(run, [forged, full])
        with self.assertRaisesRegex(ValueError, 'verdict differs'):
            verify_evidence(run, [forged, full], required=True)

        # An owner waiver for exactly the uncarried finding is a legitimate pass, and seals.
        entries = sorted((dict(violation_key('drc', manifest['scope']['board'], 'violations', report, v),
                               reason='owned probe segment', owner='fixture-owner') for _, v in extra),
                         key=lambda e: (e['type'], e['items']))
        record = {'schema': SCHEMA, 'waivers': entries, 'approval': {
            'owner': 'fixture-owner', 'disposition': 'approved', 'waivers_sha256': identity(entries),
            'reference': 'test fixture'}}
        run, _ = self.capture_design(root, scope, 'waived', waive=record)
        projection, full = run_checks(run, time.monotonic() + 120)
        self.assertEqual(len(projection['details']['waived']), len(extra))
        self.assertTrue(projection['details']['carried_findings'])
        self.assertEqual((projection['execution'], projection['outcome']), ('ok', 'pass'))
        self.assertEqual(full['details']['reason'], 'open_placement_defects')
        freeze_evidence(run, [projection, full])
        verify_evidence(run, [projection, full], required=True)
        # Without the captured waiver the same retained pass is a forgery.
        changed = run / 'inputs/waivers/drc.json'
        changed.chmod(0o644)
        write_json(changed, dict(record, waivers=[]))
        with self.assertRaisesRegex(ValueError, 'waiver'):
            verify_evidence(run, [projection, full], required=True)

    def test_projection_seal_replays_structural_containment_under_carried_silk(self):
        """Ruling 346c (2): a retained pass never seals over 331 structural containment."""
        import pcbnew as k
        from agent_ee.baseline import freeze_evidence, verify_evidence
        from agent_ee.checks import run_checks
        root, scope, uncarried = self.uncarried_silk_design('projection-structural')
        path = root / 'design' / scope['board']
        board = k.LoadBoard(str(path))
        board.Remove(next(d for d in board.GetDrawings() if d.m_Uuid.AsString() == uncarried))
        k.SaveBoard(str(path), board, True)
        run, _ = self.capture_design(root, scope, 'carried-only')
        projection, full = run_checks(run, time.monotonic() + 180)
        self.assertEqual(read_json(run / 'work/drc-placement/containment/copper-containment.json'), [])
        self.assertTrue(projection['details']['carried_findings'])
        self.assertEqual((projection['execution'], projection['outcome']), ('ok', 'pass'), projection['summary'])
        freeze_evidence(run, [projection, full])
        verify_evidence(run, [projection, full], required=True)

        # Owned B.Cu segment wholly outside the outline; native DRC still reports only carried silk.
        board = k.LoadBoard(str(path))
        segment = k.PCB_SHAPE(board)
        segment.SetShape(k.SHAPE_T_SEGMENT)
        segment.SetLayer(k.B_Cu)
        segment.SetStart(k.VECTOR2I(80000000, 80000000))
        segment.SetEnd(k.VECTOR2I(82000000, 80000000))
        segment.SetWidth(200000)
        board.Add(segment)
        k.SaveBoard(str(path), board, True)
        run, _ = self.capture_design(root, scope, 'structural')
        projection, full = run_checks(run, time.monotonic() + 180)
        report = read_json(run / 'work/drc-placement/drc.json')
        containment = read_json(run / 'work/drc-placement/containment/copper-containment.json')
        self.assertEqual([(f['type'], f['layers']) for f in containment], [('copper_outside_outline', ['B.Cu'])])
        self.assertEqual(sorted(projection['details']['carried_findings']),
                         sorted(f'/violations/{i}' for i in range(len(report['violations']))))
        self.assertEqual((projection['execution'], projection['outcome']), ('ok', 'defect'))
        freeze_evidence(run, [projection, full])
        verify_evidence(run, [projection, full], required=True)
        forged = dict(projection, outcome='pass')
        unavailable = copy.deepcopy(forged)
        unavailable['details']['containment'] = dict(execution='unavailable', outcome='unknown', reason='forged')
        for name, check in (('pass', forged), ('pass with containment unavailable', unavailable)):
            with self.subTest(forgery=name):
                write_json(run / 'checks/drc-placement.json', check)
                with self.assertRaisesRegex(ValueError, 'verdict differs'):
                    freeze_evidence(run, [check, full])
                with self.assertRaisesRegex(ValueError, 'verdict differs'):
                    verify_evidence(run, [check, full], required=True)
        write_json(run / 'checks/drc-placement.json', projection)
        verify_evidence(run, [projection, full], required=True)

    def test_placement_projection_does_not_carry_unmatched_or_non_silk_findings(self):
        located = [{'raw_locator': '/violations/0', 'type': 'silk_overlap',
                    'items': [{'uuid': 'a'}, {'uuid': 'b'}]},
                   {'raw_locator': '/violations/1', 'type': 'silk_overlap',
                    'items': [{'uuid': 'a'}, {'uuid': 'c'}]},
                   {'raw_locator': '/violations/2', 'type': 'courtyards_overlap',
                    'items': [{'uuid': 'a'}, {'uuid': 'b'}]},
                   {'raw_locator': '/violations/3', 'type': 'silk_edge_clearance',
                    'items': [{'uuid': 'a'}, {'uuid': 'b'}]},
                   {'raw_locator': '/violations/4', 'type': 'silk_over_copper', 'items': [{'uuid': 'a'}]},
                   {'raw_locator': '/schematic_parity/0', 'type': 'silk_overlap',
                    'items': [{'uuid': 'b'}, {'uuid': 'a'}]},
                   {'raw_locator': '/violations/5', 'type': 'silk_overlap', 'items': [{}]}]
        defects = [{'native': {'type': 'silk_overlap', 'items': [{'uuid': 'b'}, {'uuid': 'a'}]}},
                   {'native': {'type': 'silk_overlap', 'items': [{'uuid': 'a'}, {'uuid': 'b'}]}},
                   {'native': {'type': 'courtyards_overlap', 'items': [{'uuid': 'a'}, {'uuid': 'b'}]}},
                   {'native': {'type': 'silk_edge_clearance', 'items': [{'uuid': 'a'}, {'uuid': 'b'}]}},
                   {'native': {'type': 'silk_overlap', 'items': [{'uuid': 'a'}]}},
                   {'uuid': 'a', 'reason': 'footprint_side_flipped'}]
        self.assertEqual(placement_defects.carried_silk(defects, located), ['/violations/0'])
        self.assertEqual(placement_defects.carried_silk([], located), [])

    def test_placement_projection_is_the_only_drc_exempt_from_the_carry_gate(self):
        projection = {'projection': 'placement-stage/1'}
        self.assertFalse(placement_defects.gated({'kind': 'drc', 'stage': 'placement', 'params': projection}))
        self.assertFalse(placement_defects.gated({'kind': 'drc', 'params': projection}))
        for spec in ({'kind': 'drc', 'stage': 'placement'}, {'kind': 'drc', 'stage': 'placement', 'params': {}},
                     {'kind': 'drc', 'stage': 'placement', 'params': {'projection': 'other'}},
                     *({'kind': 'drc', 'stage': s, 'params': projection}
                       for s in ('schematic', 'routing', 'verification', 'fab', 'release')),
                     {'kind': 'fab_export', 'stage': 'placement', 'params': projection}):
            with self.subTest(spec=spec):
                self.assertTrue(placement_defects.gated(spec))

    def test_projection_with_missing_carry_evidence_is_unknown_not_pass(self):
        from agent_ee.checks import Ctx, CheckError
        run, manifest = self.projection_run('projection-tampered')
        manifest = copy.deepcopy(manifest)
        manifest['scope'][placement_defects.DECLARATION][0]['sha256'] = '0' * 64
        ctx = Ctx(run, manifest, manifest['scope']['checks'][0], time.monotonic() + 60)
        self.assertTrue(placement_defects.before_check(ctx))
        with self.assertRaisesRegex(CheckError, 'carried placement evidence refused'):
            placement_defects.projection_record(ctx, [])
        self.assertNotEqual(ctx.result['outcome'], 'pass')

    def test_acceptance_without_any_fab_checks_refuses_open_carry(self):
        diagnostic = {'state': 'criteria_satisfied', 'reason': 'all_criteria_satisfied'}
        refused = placement_defects.acceptance(self.candidate_run, diagnostic)
        self.assertEqual(refused, {'state': 'escalated', 'reason': 'open_placement_defects'})
        self.assertEqual(diagnostic['state'], 'criteria_satisfied')
        pending = {'state': 'repair', 'reason': 'criteria_failed'}
        self.assertEqual(placement_defects.acceptance(self.candidate_run, pending), pending)


    def test_native_masked_track_edit_preserves_the_silk_subject(self):
        from agent_ee.placer_runner import _native_drc
        import pcbnew as k
        source = self.candidate_run / 'inputs' / self.manifest['scope']['board']
        with tempfile.TemporaryDirectory(dir=self.helper.root) as temporary:
            root = Path(temporary)
            after = root / 'tracked.kicad_pcb'
            board = k.LoadBoard(str(source))
            track = k.PCB_TRACK(board)
            track.SetStart(k.VECTOR2I(20000000, 20000000))
            track.SetEnd(k.VECTOR2I(22000000, 20000000))
            track.SetWidth(250000)
            track.SetLayer(k.F_Cu)
            board.Add(track)
            k.SaveBoard(str(after), board, True)
            self.assertNotEqual(digest(source), digest(after))
            self.assertTrue(placement_defects.same_artwork(source, after))
            reports = []
            for name, path in (('before', source), ('after', after)):
                scratch = root / name
                scratch.mkdir()
                reports.append(_native_drc(path, scratch))
            def silk(report):
                return sorted((v['type'], tuple(sorted(i['uuid'] for i in v['items'])))
                              for v in report['violations'] if v['type'].startswith('silk_'))
            self.assertTrue(silk(reports[0]))
            self.assertEqual(silk(reports[0]), silk(reports[1]))

    def test_native_mid_token_quotes_cannot_hide_added_artwork(self):
        import pcbnew as k
        source = self.candidate_run / 'inputs' / self.manifest['scope']['board']
        original = source.read_text().rstrip()[:-1]
        with tempfile.TemporaryDirectory(dir=self.helper.root) as temporary:
            after = Path(temporary) / 'hidden.kicad_pcb'
            for prefix in ('h1', '\x0b', '\xa0', '\x85'):
                with self.subTest(prefix=repr(prefix)):
                    after.write_text(original + ClosureEscapeTests.quote_hidden_artwork(prefix) + ')')
                    board = k.LoadBoard(str(after))
                    self.assertIsNotNone(board)
                    self.assertIn('EVIL', [d.GetText() for d in board.GetDrawings()
                                          if isinstance(d, k.PCB_TEXT)])
                    self.assertFalse(placement_defects.same_artwork(source, after))

    def test_expired_tool_budget_does_not_erase_known_carried_defects(self):
        from agent_ee.checks import run_checks
        check = run_checks(self.candidate_run, time.monotonic() - 1)[0]
        self.assertEqual((check['execution'], check['outcome']), ('ok', 'defect'))
        placement_defects.verify_gate(self.candidate_run, check, self.manifest)


    def test_bench_epoch_projects_open_native_carry_before_publishing_terminal_state(self):
        from agent_ee import bench_transactions as tx
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, campaign, transaction = root / 'review', root / 'campaign', root / 'transaction'
            shutil.copytree(self.candidate_run, target)
            manifest = read_json(target / 'candidate.json')
            # Isolate finish_campaign's state publication: reuse the real native
            # carry, with a synthetic successful feedback result and epoch key.
            manifest['scope']['empirical'] = 'placement-evidence/index.json'
            write_json(target / 'candidate.json', manifest)
            result = {'checks': [], 'outcome': 'criteria_satisfied', 'run_id': 'feedback-projection'}
            write_json(target / 'result.json', result)
            write_json(campaign / 'config.json', {'max_iterations': 3})
            write_json(campaign / 'profile.json', {})
            state = {'baseline': {}, 'history': [], 'state': 'repair'}
            write_json(campaign / 'state.json', state)
            request = {'review_out': str(target), 'campaign': {'root': str(campaign), 'state': state,
                'config_sha256': digest(campaign / 'config.json'),
                'profile_sha256': digest(campaign / 'profile.json')}}
            terminal = {'out': str(target), 'result_sha256': digest(target / 'result.json')}
            # Campaign replay and baseline qualification have independent native
            # integration coverage; this probe observes the published projection.
            with patch('agent_ee.campaign.verify_campaign'), patch(
                    'agent_ee.rating_lineage.sealed_baseline', return_value={}):
                tx.finish_campaign(transaction, request, terminal)
            for path in (campaign / 'state.json', transaction / 'campaign-intent.json'):
                published = read_json(path)
                if path.name == 'campaign-intent.json':
                    published = published['state']
                self.assertEqual((published['state'], published['reason']),
                                 ('escalated', 'open_placement_defects'))
            self.assertEqual(read_json(campaign / 'tradeoff.json'),
                             {'state': 'escalated', 'reason': 'open_placement_defects'})
            self.assertEqual(read_json(campaign / 'history/1.json'), result)


class OccupiedStampCommentTests(unittest.TestCase):
    """AGENTEE-412: a carried placement on a board with user text in comment 9 publishes,
    with its stamp in the highest free comment and the user text untouched."""
    user_comments = {'9': 'Customer drawing note'}

    @classmethod
    def setUpClass(cls):
        NativePlacementCarryTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    def test_carried_placement_publishes_with_a_relocated_stamp(self):
        import pcbnew
        from agent_ee.checks import run_checks
        board = self.candidate_run / 'inputs' / self.manifest['scope']['board']
        data = board.read_bytes()
        self.assertEqual(placement_defects.stamp(data), [placement_defects.carry_digest(self.result['open_defects'])])
        self.assertEqual(placement_defects.stamp_slot(data), '8')
        block = pcbnew.LoadBoard(str(board)).GetTitleBlock()
        self.assertEqual(block.GetComment(8), 'Customer drawing note')
        self.assertTrue(block.GetComment(7).startswith(placement_defects.STAMP))
        self.assertEqual(self.result['write_scope']['status'], 'allowed')
        self.assertEqual(self.result['board_sha256'], digest(board))
        placement_defects.verify_candidate(self.helper.root, self.helper.state, 2, self.manifest)
        defects = placement_defects.open_defects(self.manifest, self.candidate_run / 'inputs')
        self.assertEqual(len(defects), len(self.result['open_defects']))
        check = run_checks(self.candidate_run, time.monotonic() + 60)[0]
        self.assertEqual((check['execution'], check['outcome'], check['details']['reason']),
                         ('ok', 'defect', 'open_placement_defects'))
        placement_defects.verify_gate(self.candidate_run, check, self.manifest)


class NoLineageProjectionSealTests(unittest.TestCase):
    """Ruling 346d H2: a declared projection reconstructs its verdict without placement lineage."""

    @classmethod
    def setUpClass(cls):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def design(self, name, *shapes, rules=None):
        """Owned RC control plus (layer, start, end) segments; library comparison ignored only."""
        import pcbnew as k
        from agent_ee.candidate import capture
        root = self.root / name
        design = root / 'design'
        shutil.copytree(Path(__file__).resolve().parents[1] / 'fixtures/rc/control', design)
        if rules is not None:
            (design / 'rc_filter.kicad_dru').write_text(rules)
        path = design / 'rc_filter.kicad_pcb'
        board = k.LoadBoard(str(path))
        for layer, start, end in shapes:
            segment = k.PCB_SHAPE(board)
            segment.SetShape(k.SHAPE_T_SEGMENT)
            segment.SetLayer(getattr(k, layer))
            segment.SetStart(k.VECTOR2I(*start))
            segment.SetEnd(k.VECTOR2I(*end))
            segment.SetWidth(200000)
            board.Add(segment)
        k.SaveBoard(str(path), board, True)
        project = read_json(design / 'rc_filter.kicad_pro')
        project['board']['design_settings']['rule_severities'] = {
            'lib_footprint_issues': 'ignore', 'lib_footprint_mismatch': 'ignore'}
        write_json(design / 'rc_filter.kicad_pro', project)
        if rules is not None:
            from owner_fixture import bind_owner_rules
            bind_owner_rules(path)  # AGENTEE-348 H1 migration: the owner rules are declared and cited
        requirement = root / 'requirements.md'
        requirement.write_text('Native structural containment must block retained projection passes.\n')
        scope = {'schema': 'agent-ee.scope/2', 'name': name, 'board': 'project/rc_filter.kicad_pcb',
                 'schematic': 'project/rc_filter.kicad_sch',
                 'inputs': [{'path': str(design), 'at': 'project'}, {'path': str(requirement), 'at': 'requirements.md'}],
                 'checks': [{'id': 'projection', 'kind': 'drc', 'params': {'projection': 'placement-stage/1'}},
                            {'id': 'full', 'kind': 'drc'}],
                 'baseline': {'requirements': [{'id': 'R-native', 'statement': requirement.read_text().strip(),
                     'source': {'path': 'requirements.md', 'locator': 'R-native'}, 'checks': ['projection', 'full']}],
                     'targets': ['requirements.md'], 'reference_architectures': ['requirements.md']}}
        write_json(root / 'scope.json', scope)
        run = root / 'run'
        run.mkdir()
        manifest = capture(root / 'scope.json', run)
        self.assertFalse(placement_defects.lineage(manifest))
        self.assertIsNone(placement_defects._board_stamp(manifest, run / 'inputs'))
        return run, manifest

    def assert_forgeries_refused(self, run, manifest, projection, full, forgeries):
        from agent_ee.baseline import freeze_evidence, verify_evidence
        for name, check in forgeries:
            with self.subTest(forgery=name):
                write_json(run / 'checks/projection.json', check)
                with self.assertRaisesRegex(ValueError, 'verdict differs|containment'):
                    placement_defects.verify_gate(run, check, manifest)
                with self.assertRaisesRegex(ValueError, 'verdict differs|containment'):
                    freeze_evidence(run, [check, full])
                with self.assertRaisesRegex(ValueError, 'verdict differs|containment'):
                    verify_evidence(run, [check, full], required=True)
        write_json(run / 'checks/projection.json', projection)
        verify_evidence(run, [projection, full], required=True)

    def test_clean_pass_seals_and_forged_pass_over_structural_containment_refuses(self):
        from agent_ee.baseline import freeze_evidence, verify_evidence
        from agent_ee.checks import run_checks
        run, manifest = self.design('clean')
        projection, full = run_checks(run, time.monotonic() + 180)
        self.assertEqual((projection['execution'], projection['outcome']), ('ok', 'pass'), projection['summary'])
        self.assertEqual(read_json(run / 'work/projection/containment/copper-containment.json'), [])
        freeze_evidence(run, [projection, full])
        verify_evidence(run, [projection, full], required=True)
        # Containment removed from a clean pass is never assumed.
        containment = run / 'work/projection/containment/copper-containment.json'
        retained = containment.read_bytes()
        containment.unlink()
        with self.assertRaises((OSError, ValueError)):
            placement_defects.verify_gate(run, projection, manifest)
        containment.write_bytes(retained)

        # Owned B.Cu segment wholly outside the ~96..116 mm outline: only containment reports it.
        run, manifest = self.design('structural', ('B_Cu', (80000000, 80000000), (82000000, 80000000)))
        projection, full = run_checks(run, time.monotonic() + 180)
        containment = read_json(run / 'work/projection/containment/copper-containment.json')
        self.assertEqual([(f['type'], f['layers']) for f in containment], [('copper_outside_outline', ['B.Cu'])])
        self.assertEqual((projection['execution'], projection['outcome']), ('ok', 'defect'), projection['summary'])
        freeze_evidence(run, [projection, full])
        verify_evidence(run, [projection, full], required=True)
        forged = dict(projection, outcome='pass')
        unavailable = copy.deepcopy(forged)
        unavailable['details']['containment'] = dict(execution='unavailable', outcome='unknown', reason='forged')
        self.assert_forgeries_refused(run, manifest, projection, full,
                                      (('pass', forged), ('pass with containment unavailable', unavailable)))
        # The native structural artifact itself was never altered.
        self.assertEqual(read_json(run / 'work/projection/containment/copper-containment.json'), containment)

    def test_forged_pass_over_uncarried_silk_refuses_without_lineage(self):
        from agent_ee.baseline import freeze_evidence, verify_evidence
        from agent_ee.checks import run_checks
        # Owned silk segment across R1's pads; without lineage no silk finding is carried.
        run, manifest = self.design('silk', ('F_SilkS', (104000000, 100000000), (106000000, 100000000)))
        projection, full = run_checks(run, time.monotonic() + 180)
        report = read_json(run / 'work/projection/drc.json')
        self.assertTrue(any(v['type'].startswith('silk_') for v in report['violations']), report)
        self.assertNotIn('carried_findings', projection['details'])
        self.assertEqual((projection['execution'], projection['outcome']), ('ok', 'defect'), projection['summary'])
        freeze_evidence(run, [projection, full])
        verify_evidence(run, [projection, full], required=True)
        self.assert_forgeries_refused(run, manifest, projection, full,
                                      (('pass', dict(projection, outcome='pass')),))


    def test_forged_ok_over_runtime_unknown_evaluation_refuses(self):
        """AGENTEE-407: the seal replays the runtime admissibility of the native report, so a
        retained ok verdict cannot seal where the runtime check could only be unknown."""
        from agent_ee.baseline import freeze_evidence, verify_evidence
        from agent_ee.checks import run_checks
        from agent_ee.outline_containment import sandboxed
        # An owner rule (captured .kicad_dru) that disables a numeric clearance evaluation.
        run, manifest = self.design('ignored', rules='(version 1)\n(rule quiet (constraint clearance '
                                    '(min 0.1mm)) (severity ignore))\n')
        projection, full = run_checks(run, time.monotonic() + 180)
        self.assertEqual((projection['execution'], projection['outcome']), ('malformed', 'unknown'),
                         projection['summary'])
        self.assertIn('explicitly ignored by sourced rule quiet', projection['summary'])
        self.assertIn('work/projection/drc.json', projection['evidence'])
        freeze_evidence(run, [projection, full])
        verify_evidence(run, [projection, full], required=True)
        # Genuine native containment of this same captured board, retained where a
        # completed check keeps it: the only missing piece is the evaluation itself.
        containment = run / 'work/projection/containment'
        self.assertEqual(sandboxed(run / 'inputs', manifest['scope']['board'], containment), [])
        evidence = projection['evidence'] + ['work/projection/containment/' + name for name in
                                             ('copper-containment.json', 'execution.json', 'console.log')]
        forged = dict(projection, execution='ok', outcome='pass', evidence=evidence)
        for name, check in (('pass', forged),):
            with self.subTest(forgery=name):
                write_json(run / 'checks/projection.json', check)
                for seal in (lambda: placement_defects.verify_gate(run, check, manifest),
                             lambda: freeze_evidence(run, [check, full]),
                             lambda: verify_evidence(run, [check, full], required=True)):
                    with self.assertRaisesRegex(ValueError, 'explicitly ignored by sourced rule quiet'):
                        seal()
        write_json(run / 'checks/projection.json', projection)
        verify_evidence(run, [projection, full], required=True)

    def test_seal_replays_the_runtime_parity_skip_refusal(self):
        """AGENTEE-407: a console stating parity was not performed is unknown at runtime and at seal."""
        from agent_ee.checks import run_checks
        run, manifest = self.design('parity-console')
        projection, full = run_checks(run, time.monotonic() + 180)
        self.assertEqual((projection['execution'], projection['outcome']), ('ok', 'pass'), projection['summary'])
        placement_defects.verify_gate(run, projection, manifest)
        console = run / 'work/projection/console.log'
        retained = console.read_bytes()
        console.write_bytes(retained + b'Failed to fetch schematic netlist for parity tests.\n')
        with self.assertRaisesRegex(ValueError, 'schematic parity was not performed'):
            placement_defects.verify_gate(run, projection, manifest)
        console.unlink()
        with self.assertRaises((OSError, ValueError)):
            placement_defects.verify_gate(run, projection, manifest)
        console.write_bytes(retained)
        placement_defects.verify_gate(run, projection, manifest)


class ContainedIdentities(dict):
    """Unit closure caller supplying an explicit positive containment witness."""
    def contained(self):
        return {'field'}


class ClosureContractTests(unittest.TestCase):
    def setUp(self):
        self.native = {'type': 'placement_silkscreen_conflict', 'status': 'open', 'verdict': 'defect',
                       'native': {'items': [{'uuid': 'field'}]}}
        self.diagnostic = {'type': 'placement_silkscreen_conflict', 'status': 'open', 'verdict': 'defect',
                           'uuid': 'field', 'reason': 'footprint_side_flipped'}
        self.original = ContainedIdentities({'field': ('footprint', ['property', 'Reference', 'U1'], [['at', '0', '0']])})

    def test_native_closes_only_on_current_clear_same_identity_evidence(self):
        self.assertTrue(placement_defects._closes(self.native, self.original, self.original, set(), set(), True))
        for current, conflicts, fresh in (({}, set(), True), (self.original, {'field'}, True),
                                          (self.original, set(), False),
                                          ({'field': ('other-footprint', ['property', 'Reference', 'U1'], [])}, set(), True)):
            self.assertFalse(placement_defects._closes(self.native, self.original, current, conflicts, set(), fresh))

    def test_plain_mapping_cannot_bypass_containment(self):
        self.assertFalse(placement_defects._closes(self.native, self.original,
            dict(self.original), set(), set(), True))
        class Uncontained(ContainedIdentities):
            def contained(self):
                return set()
        self.assertFalse(placement_defects._closes(self.native, self.original,
            Uncontained(self.original), set(), set(), True))

    def test_side_flip_empty_native_delta_does_not_close_without_artwork_change(self):
        self.assertFalse(placement_defects._closes(self.diagnostic, self.original, self.original, set(), set(), True))
        moved = copy.deepcopy(self.original)
        moved['field'] = (*moved['field'][:2], [['at', '1', '0']])
        self.assertTrue(placement_defects._closes(self.diagnostic, self.original, moved, set(), set(), True))
        self.assertFalse(placement_defects._closes(self.diagnostic, self.original, moved, {'field'}, {'field'}, True))

    def test_hiding_relabeling_or_reparenting_is_not_resolution(self):
        for content in (['property', 'Reference', ''], ['property', 'Reference', 'U1', ['hide', 'yes']]):
            self.assertFalse(placement_defects._closes(self.native, self.original,
                {'field': ('footprint', content, [['at', '1', '0']])}, set(), {'field'}, True))

    def test_unrelated_baseline_silk_on_same_pad_does_not_prevent_field_resolution(self):
        defect = copy.deepcopy(self.native)
        defect['native']['items'].append({'uuid': 'pad'})
        original = ContainedIdentities({**self.original, 'pad': ('footprint', ['pad', '1', 'smd'], [['at', '0', '0']])})
        self.assertTrue(placement_defects._closes(defect, original, original, {'pad', 'other-field'}, set(), True))
        self.assertFalse(placement_defects._closes(defect, original, original, {'field'}, set(), True))


class CleanProvenanceTests(unittest.TestCase):
    """Clean provenance is required only at the release artifact boundary."""
    @classmethod
    def setUpClass(cls):
        from toolchain import require_toolchain
        require_toolchain(native=True)

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def capture(self, fixture, name, *, rules=None):
        from agent_ee.candidate import capture
        root = Path(__file__).resolve().parents[1] / 'fixtures' / fixture
        design = self.tmp / 'design'
        shutil.rmtree(design, ignore_errors=True)
        shutil.rmtree(self.tmp / 'run', ignore_errors=True)
        shutil.copytree(root, design)
        if rules is not None:
            (design / f'{name}.kicad_dru').write_text(rules)
        scope = {'schema': 'agent-ee.scope/1', 'board': f'project/{name}.kicad_pcb',
                 'schematic': f'project/{name}.kicad_sch', 'inputs': [{'path': str(design), 'at': 'project'}],
                 'checks': [{'id': 'release', 'kind': 'fab_package'}, {'id': 'erc', 'kind': 'erc'},
                            {'id': 'export', 'kind': 'fab_export'}]}
        write_json(self.tmp / 'scope.json', scope)
        run = self.tmp / 'run'
        run.mkdir()
        return run, capture(self.tmp / 'scope.json', run)

    def admitted(self, run, manifest, index, outcome='pass', seconds=300):
        """Run the placement gate, then judge a check that reached `outcome`."""
        from agent_ee.checks import Ctx
        ctx = Ctx(run, manifest, manifest['scope']['checks'][index], time.monotonic() + seconds)
        self.assertTrue(placement_defects.before_check(ctx))
        ctx.result.update(execution='ok', outcome=outcome)
        if index == 0:
            placement_defects.prepare_release(ctx)
            placement_defects.release_defects(run, ctx.result, manifest)
        return ctx

    def test_clean_board_without_lineage_passes_on_native_evidence(self):
        run, manifest = self.capture('rc/control', 'rc_filter')
        for index in (0, 2):
            self.admitted(run, manifest, index)
        witness = read_json(run / 'work/release' / placement_defects.WITNESS)
        self.assertEqual((witness['board_sha256'], witness['silk_findings']),
                         (manifest['files']['project/rc_filter.kicad_pcb'], []))
        passed = {'id': 'release', 'execution': 'ok', 'outcome': 'pass'}
        placement_defects.verify_gate(run, passed, manifest)
        self.assertEqual(placement_defects.release_defects(run, passed, manifest), [])
        # Ungated checks neither run nor need the verification.
        self.admitted(run, manifest, 1)
        self.assertFalse((run / 'work/erc' / placement_defects.WITNESS).exists())
        placement_defects.verify_gate(run, {'id': 'erc', 'execution': 'ok', 'outcome': 'pass'}, manifest)
        board = run / 'inputs/project/rc_filter.kicad_pcb'
        original_board = board.read_bytes()
        board.write_bytes(original_board + b'\n')
        with self.assertRaisesRegex(ValueError, 'missing or changed'):
            placement_defects.release_defects(run, passed, manifest)
        with self.assertRaisesRegex(ValueError, 'missing or changed'):
            placement_defects.verify_gate(run, passed, manifest)
        board.write_bytes(original_board)
        report = run / 'work/release' / placement_defects.VERIFICATION / 'native-drc.json'
        original = report.read_bytes()
        for mutation in ('report', 'board', 'missing'):
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, 'verification'):
                if mutation == 'report':
                    report.write_bytes(original + b' ')
                    placement_defects.verify_gate(run, passed, manifest)
                elif mutation == 'board':
                    changed = copy.deepcopy(manifest)
                    changed['files']['project/rc_filter.kicad_pcb'] = '0' * 64
                    placement_defects.verify_gate(run, passed, changed)
                else:
                    placement_defects.verify_gate(run, {**passed, 'id': 'export-missing'},
                        {**manifest, 'scope': {**manifest['scope'], 'checks': [
                            *manifest['scope']['checks'], {'id': 'export-missing', 'kind': 'fab_package'}]}})
            report.write_bytes(original)

    def test_unlineaged_board_with_silk_conflicts_cannot_pass(self):
        run, manifest = self.capture('rc/stages/seeded-placement', 'rc_stage')
        self.assertIsNone(placement_defects.stamp((run / 'inputs/project/rc_stage.kicad_pcb').read_bytes()))
        with self.assertRaisesRegex(ValueError, 'silkscreen conflict'):
            self.admitted(run, manifest, 0)
        # Ordinary fab export keeps its verdict and needs no provenance witness.
        self.admitted(run, manifest, 2)
        self.assertFalse((run / 'work/export' / placement_defects.WITNESS).exists())
        placement_defects.verify_gate(run, {'id': 'export', 'execution': 'ok', 'outcome': 'pass'}, manifest)
        with self.assertRaisesRegex(ValueError, 'silkscreen conflict'):
            placement_defects.verify_gate(run, {'id': 'release', 'execution': 'ok', 'outcome': 'pass'}, manifest)
        for kept in ({'execution': 'ok', 'outcome': 'defect'}, {'execution': 'malformed', 'outcome': 'unknown'}):
            placement_defects.verify_gate(run, {'id': 'release', **kept}, manifest)

    def test_empty_imported_index_does_not_replace_clean_native_provenance(self):
        run, manifest = self.capture('rc/stages/seeded-placement', 'rc_stage')
        index = 'placement-evidence/index.json'
        write_json(run / 'inputs' / index, [])
        manifest['files'][index] = digest(run / 'inputs' / index)
        manifest['scope'][placement_defects.DECLARATION] = []
        self.assertTrue(placement_defects.lineage(manifest))
        with self.assertRaisesRegex(ValueError, 'silkscreen conflict'):
            self.admitted(run, manifest, 0)
        with self.assertRaisesRegex(ValueError, 'silkscreen conflict'):
            placement_defects.verify_gate(run, {'id': 'release', 'execution': 'ok', 'outcome': 'pass'}, manifest)

    def test_unverifiable_native_evidence_cannot_pass(self):
        from agent_ee.checks import CheckError
        run, manifest = self.capture('rc/control', 'rc_filter', rules='(version 1)\n(rule quiet (severity ignore))\n')
        with self.assertRaisesRegex(ValueError, 'custom DRC rule ignores'):
            self.admitted(run, manifest, 0)
        run, manifest = self.capture('rc/control', 'rc_filter')
        with self.assertRaises(CheckError) as caught:
            self.admitted(run, manifest, 0, seconds=-1)
        self.assertEqual(caught.exception.execution, 'interrupted')
        board = run / 'inputs/project/rc_filter.kicad_pcb'
        board.write_bytes(board.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'board missing or changed'):
            self.admitted(run, manifest, 0)


class DurableClosureTests(unittest.TestCase):
    def test_only_masked_track_changes_preserve_artwork_and_obstructions(self):
        from tests.test_placement_adapter import TEXT
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before, after = root / 'before.kicad_pcb', root / 'after.kicad_pcb'
            before.write_text(TEXT)
            track = '(segment (start 1 2) (end 3 4) (width 0.2) (layer "F.Cu") (net 0) (uuid "t"))'
            after.write_text(TEXT.rstrip()[:-1] + track + ')')
            self.assertTrue(placement_defects.same_artwork(before, after))
            for extra in (track.replace('F.Cu', 'F.SilkS'),
                          track.replace('(net 0)', '(net 0) (solder_mask_margin 0.1)'),
                          '(via (at 1 2) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu"))',
                          '(zone (net 0) (layer "F.Cu"))'):
                after.write_text(TEXT.rstrip()[:-1] + extra + ')')
                with self.subTest(extra=extra):
                    self.assertFalse(placement_defects.same_artwork(before, after))
            for old, new in (('(at 10 10)', '(at 11 10)'), ('"R1"', '"R2"'),
                             ('(at 0 -1.5 0)', '(at 0 -2.5 0)'),
                             ('(layer "F.SilkS")', '(layer "B.SilkS")'),
                             ('(size 0.5 0.5)', '(size 0.6 0.5)'),
                             ('(pad_to_mask_clearance 0)', '(pad_to_mask_clearance 0.5)')):
                after.write_text(TEXT.replace(old, new))
                with self.subTest(old=old):
                    self.assertFalse(placement_defects.same_artwork(before, after))


    def test_receipt_closure_currency_uses_artwork_subject_after_routing(self):
        # Unit-level state-machine probe; native receipt validation is covered above.
        from tests.test_placement_adapter import TEXT
        outlined, field_uuid = outlined_closure_text()
        with tempfile.TemporaryDirectory() as temporary:
            inputs = Path(temporary)
            old, fixed, current = (inputs / n for n in ('old.kicad_pcb', 'fixed.kicad_pcb', 'board.kicad_pcb'))
            old.write_text(outlined)
            fixed.write_text(outlined.replace('(at 0 -1.5 0)', '(at 0 -2.5 0)'))
            current.write_bytes(fixed.read_bytes())
            links, bundles, files = [], {}, {}
            defect = {'type': 'placement_silkscreen_conflict', 'status': 'open', 'verdict': 'defect',
                      'native': {'items': [{'uuid': field_uuid}]}}
            for number, board, defects in ((1, old, [defect]), (2, fixed, [])):
                path = inputs / f'placement-evidence/{number}/receipt.json'
                write_json(path, {'inputs': {}})
                rel = path.relative_to(inputs).as_posix()
                files[rel] = digest(path)
                link = {'receipt': rel, 'sha256': digest(path)}
                links.append(link)
                bundles[rel] = ({'board_sha256': digest(board), 'open_defects': defects,
                                 'containment': native_containment(board)},
                                {placement_defects.BOARD: board}, complete_drc())
            index = 'placement-evidence/index.json'
            write_json(inputs / index, links)
            files[index] = digest(inputs / index)
            manifest = {'scope': {'board': current.name, placement_defects.DECLARATION: links},
                        'files': files}
            # Native DRC on the captured board is the closing bundle's captured report.
            native = lambda: placement_defects.silk_findings(bundles[links[-1]['receipt']][2])
            with patch.object(placement_defects, '_bundle', side_effect=lambda m, i, link: bundles[link['receipt']]):
                manifest['files'][current.name] = digest(current)
                self.assertEqual(placement_defects.open_defects(manifest, inputs, native), [])
                with self.assertRaisesRegex(ValueError, 'unverifiable without native DRC'):
                    placement_defects.open_defects(manifest, inputs)
                track = '(segment (start 1 2) (end 3 4) (width 0.2) (layer "F.Cu") (net 0) (uuid "t"))'
                current.write_text(fixed.read_text().rstrip()[:-1] + track + ')')
                manifest['files'][current.name] = digest(current)
                self.assertEqual(placement_defects.open_defects(manifest, inputs, native), [])
                with self.assertRaisesRegex(ValueError, 'unverifiable without native DRC'):
                    placement_defects.open_defects(manifest, inputs)
                for old_token, new_token in (('(at 0 -2.5 0)', '(at 0 -1.5 0)'),
                                             ('(at 10 10)', '(at 11 10)'),
                                             ('(size 0.5 0.5)', '(size 0.6 0.5)')):
                    current.write_text(fixed.read_text().replace(old_token, new_token))
                    manifest['files'][current.name] = digest(current)
                    with self.subTest(change=old_token):
                        self.assertEqual(len(placement_defects.open_defects(manifest, inputs)), 1)
                for malformed in ('(kicad_pcb', '(not_a_board)', '()'):
                    current.write_text(malformed)
                    manifest['files'][current.name] = digest(current)
                    with self.subTest(malformed=malformed):
                        self.assertEqual(len(placement_defects.open_defects(manifest, inputs)), 1)
                current.write_text(fixed.read_text().rstrip()[:-1] + track + ')')
                manifest['files'][current.name] = digest(current)
                def unreadable_board(path):
                    if Path(path) == current:
                        raise OSError('candidate board unreadable')
                    return digest(path)
                with patch.object(placement_defects, 'digest', side_effect=unreadable_board):
                    self.assertEqual(len(placement_defects.open_defects(manifest, inputs)), 1)
                current.write_bytes(fixed.read_bytes())
                manifest['files'][current.name] = digest(current)
                manifest['files']['board.kicad_pro'] = 'changed-rule-settings'
                self.assertEqual(len(placement_defects.open_defects(manifest, inputs)), 1)
                del manifest['files']['board.kicad_pro']
                # A clean native delta and otherwise identical receipts cannot
                # close the same defect when the board has no proven outline.
                for board in (old, fixed):
                    board.write_text(''.join(line for line in board.read_text().splitlines(keepends=True)
                                             if not line.startswith(' (gr_line') and
                                             not line.startswith('(gr_line')))
                for report, loaded, _ in bundles.values():
                    report['board_sha256'] = digest(loaded[placement_defects.BOARD])
                    report['containment'] = native_containment(loaded[placement_defects.BOARD])
                for routed in (False, True):
                    with self.subTest(outline=False, routed=routed):
                        current.write_text(fixed.read_text().rstrip()[:-1] + (track if routed else '') + ')')
                        manifest['files'][current.name] = digest(current)
                        self.assertEqual(len(placement_defects.open_defects(manifest, inputs)), 1)


class PlacementRoutingCarryTests(unittest.TestCase):
    def routing_fixture(self):
        from test_placement_campaign import PlacementCampaignFixture
        from test_placement_adapter import owned_design, BOARD, POLICY
        from toolchain import require_kicad_route, require_toolchain
        from agent_ee import placement_adapter as pa, routing_adapter
        from agent_ee.placement_probe import extract
        from agent_ee.placer_runner import _plan
        import pcbnew as k
        require_toolchain(native=True)
        checkout = require_kicad_route()
        self.assertEqual(routing_adapter.preflight(checkout)['problems'], [])
        class OwnedCarryCampaign(PlacementCampaignFixture):
            @classmethod
            def build_board(cls, project):
                # Use the original pad/courtyard stand-ins, without copying stock footprints.
                owned = owned_design(cls.base.parent / 'owned-base')
                for path in (owned / 'project').iterdir():
                    shutil.copyfile(path, project / path.name)

            def scope(self, checks):
                scope = super().scope(checks)
                # The original RC schematic has its native symbol cache embedded.
                scope['inputs'] = [i for i in scope['inputs'] if not i['at'].startswith('stock/')]
                return scope

        OwnedCarryCampaign.setUpClass()
        self.addCleanup(OwnedCarryCampaign.tearDownClass)
        fixture = OwnedCarryCampaign()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        from agent_ee import cli
        import _pcbnew
        runtime = cli.runtime_files()
        runtime['pcbnew'] = {'path': _pcbnew.__file__, 'sha256': digest(_pcbnew.__file__)}
        self.enterContext(patch('agent_ee.cli.runtime_files', return_value=runtime))
        owned = owned_design(fixture.root / 'owned')
        shutil.rmtree(fixture.source / 'project')
        shutil.copytree(owned / 'project', fixture.source / 'project')
        (fixture.source / BOARD).with_suffix('.kicad_pro').write_text('{}\n')
        fixture.fixed = {f.GetReference(): f.m_Uuid.AsString()
                         for f in k.LoadBoard(str(fixture.source / BOARD)).GetFootprints()}
        board = k.LoadBoard(str(fixture.source / BOARD))
        for fp in board.GetFootprints():
            fp.Reference().SetVisible(False)
            fp.Value().SetVisible(False)
        k.SaveBoard(str(fixture.source / BOARD), board, True)
        policy = read_json(fixture.source / POLICY)
        request = pa.helper_request((fixture.source / BOARD).read_bytes(), policy, ['J1'])
        plan = _plan(extract(fixture.source / BOARD), request)
        self.assertEqual(plan['status'], 'planned')
        for placement in plan['placements']:
            if placement['fixed']:
                continue
            x, y = placement['position_nm']
            graphic = k.PCB_SHAPE(board)
            graphic.SetShape(k.SHAPE_T_SEGMENT)
            graphic.SetLayer(k.F_SilkS)
            graphic.SetStart(k.VECTOR2I(x - 1800000, y))
            graphic.SetEnd(k.VECTOR2I(x + 1800000, y))
            graphic.SetWidth(150000)
            board.Add(graphic)
        k.SaveBoard(str(fixture.source / BOARD), board, True)
        # This acceptance check measures the declared C1/R1 origin separation.
        # The placement helper still performs its ordinary native collision gates.
        solver = fixture.source / 'project/separation.py'
        solver.write_text('import json, math, pcbnew as k\nfrom pathlib import Path\n'
            'b=k.LoadBoard("/candidate/project/rc_filter.kicad_pcb")\n'
            'p={f.GetReference():f.GetPosition() for f in b.GetFootprints()}\n'
            'v=math.hypot(p["C1"].x-p["R1"].x,p["C1"].y-p["R1"].y)/1e6\n'
            'Path("raw.json").write_text(json.dumps({"value":v}))\n')
        adapter = fixture.source / 'project/separation-adapter.py'
        adapter.write_text('import json,sys\nfrom pathlib import Path\n'
            'r=json.loads(Path(sys.argv[1]).read_text())\n'
            'v=json.loads(Path("/work/tool/raw.json").read_text())["value"]\n'
            'Path(sys.argv[2]).write_text(json.dumps({"schema":"agent-ee.measurements/1",'
            '"candidate_id":r["candidate_id"],"files":r["files"],"tool":"native separation",'
            '"version":"1","domain":"manufacturing","artifacts":["tool/raw.json"],'
            '"metrics":{"separation":{"value":v,"unit":"mm","condition":"saved board"}}}))\n')
        checks = [
            {'id': 'separation', 'kind': 'measurements', 'stage': 'placement', 'params': {
                'adapter': 'project/separation-adapter.py', 'tool': {
                    'argv': ['/usr/bin/python3', '/candidate/project/separation.py'],
                    'sha256': digest('/usr/bin/python3'), 'domain': 'manufacturing',
                    'inputs': ['project/separation.py']},
                'limits': {'separation': {'min': 4.5, 'unit': 'mm', 'condition': 'saved board'}}}},
            {'id': 'routed', 'kind': 'geometry', 'stage': 'routing',
             'params': {'criterion': 'copper-width/1', 'covered_primitives': ['PCB_TRACK', 'PCB_ARC'],
                        'min_track_width_mm': 0.1, 'exclude_nets': []}},
            {'id': 'verify', 'kind': 'geometry', 'stage': 'verification',
             'params': {'min_track_width_mm': 0.1}},
        ]
        route = routing_adapter.step(project='project', board='rc_filter.kicad_pcb',
            schematic='rc_filter.kicad_sch', name='rc_filter', label='carry', checkout=checkout, budget=120)
        return fixture, checks, route

    def test_real_placement_and_router_keep_open_carry_at_verification(self):
        from agent_ee import campaign, stage_receipts, placement_adapter as pa
        from test_placement_adapter import BOARD
        fixture, checks, route = self.routing_fixture()
        scope = fixture.scope(checks)
        scope['stages']['order'].append('verification')
        scope['stages']['subjects']['routing'] = {'path': BOARD, 'kind': 'tracks', 'minimum': 1}
        status, state = fixture.campaign(fixture.declaration(), iterations=3, scope=scope, steps=[route])
        self.assertEqual(len(state['history']), 3, {k: state.get(k) for k in ('state', 'reason', 'error', 'history')})
        self.assertEqual(status, 1, state)
        self.assertNotEqual(state['state'], 'criteria_satisfied')
        placement = read_json(fixture.out / 'implementations/1/placement-adapter.json')
        self.assertEqual(placement['status'], 'proposed')
        result = read_json(fixture.out / 'implementations/1' / placement['helper']['receipt'] / pa.RESULT)
        self.assertTrue(result['open_defects'])
        self.assertNotIn('2', state['placement_records'])
        receipt_dir = fixture.out / 'implementations/2/receipts/1-kicad-route-builtin'
        routed = stage_receipts.validate_receipt(receipt_dir)
        self.assertIn(routed['status'], ('completed', 'partial'))
        self.assertEqual(routed['violations'], [])
        prior = read_json(fixture.out / 'runs/2/candidate.json')
        self.assertEqual(routed['inputs'][BOARD], prior['files'][BOARD])
        final = fixture.out / 'runs/3'
        manifest = read_json(final / 'candidate.json')
        self.assertEqual(routed['outputs'][BOARD], manifest['files'][BOARD])
        self.assertEqual(manifest['scope'][placement_defects.DECLARATION],
                         prior['scope'][placement_defects.DECLARATION])
        self.assertTrue(placement_defects.open_defects(manifest, final / 'inputs'))
        check = read_json(final / 'checks/verify.json')
        self.assertEqual((check['execution'], check['outcome']), ('ok', 'defect'))
        self.assertEqual(check['details']['reason'], 'open_placement_defects')
        self.assertEqual(read_json(final / 'checks/routed.json')['outcome'], 'pass')
        self.assertEqual(campaign.verify_campaign(fixture.out)['state'], state['state'])

        # The same native publication must also fail acceptance in a campaign
        # that declares no routing, verification or fab gate at all.
        scope = fixture.scope([checks[0]])
        scope['stages']['order'] = ['placement']
        status, state = fixture.campaign(fixture.declaration(), iterations=2, scope=scope,
                                        name='no-downstream')
        self.assertEqual((status, state['state'], state['reason']),
                         (1, 'escalated', 'open_placement_defects'))
        self.assertEqual(campaign.verify_campaign(fixture.out)['state'], 'escalated')

    def test_placement_projection_carries_silk_to_routing_and_full_drc_refuses(self):
        from agent_ee import campaign
        from test_placement_adapter import BOARD
        import subprocess
        import xml.etree.ElementTree as ET
        import pcbnew as k
        fixture, checks, route = self.routing_fixture()
        # Bind the owned stand-ins' value and footprint id to the native schematic, so
        # schematic parity is clean and only carried silk can reach the projection.
        exported = fixture.root / 'parity-netlist.xml'
        subprocess.run(['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', str(exported),
                        str(fixture.source / 'project/rc_filter.kicad_sch')], check=True, capture_output=True)
        symbols = {c.get('ref'): (c.findtext('value'), c.findtext('footprint'))
                   for c in ET.parse(exported).getroot().findall('components/comp')}
        board = k.LoadBoard(str(fixture.source / BOARD))
        for fp in board.GetFootprints():
            value, footprint = symbols[fp.GetReference()]
            fp.SetValue(value)
            fp.SetFPID(k.LIB_ID(*footprint.split(':')))
        k.SaveBoard(str(fixture.source / BOARD), board, True)
        # The stock footprint libraries are deliberately not captured.
        write_json((fixture.source / BOARD).with_suffix('.kicad_pro'), {'board': {'design_settings': {
            'rule_severities': {'lib_footprint_issues': 'ignore', 'lib_footprint_mismatch': 'ignore'}}}})
        staged = [{'id': 'drc-placement', 'kind': 'drc', 'stage': 'placement',
                   'params': {'projection': 'placement-stage/1'}}, checks[0], checks[1],
                  {'id': 'drc', 'kind': 'drc', 'stage': 'routing'}, checks[2]]
        scope = fixture.scope(staged)
        scope['stages']['order'].append('verification')
        scope['stages']['subjects']['routing'] = {'path': BOARD, 'kind': 'tracks', 'minimum': 1}
        status, state = fixture.campaign(fixture.declaration(), iterations=3, scope=scope, steps=[route])
        self.assertEqual(len(state['history']), 3, {k: state.get(k) for k in ('state', 'reason', 'error', 'history')})
        self.assertNotEqual(state['state'], 'criteria_satisfied')
        placed = fixture.out / 'runs/2'
        manifest = read_json(placed / 'candidate.json')
        defects = placement_defects.open_defects(manifest, placed / 'inputs')
        self.assertTrue(defects)
        projection = read_json(placed / 'checks/drc-placement.json')
        self.assertEqual((projection['execution'], projection['outcome']), ('ok', 'pass'), projection)
        self.assertTrue(projection['details']['carried_findings'])
        self.assertEqual(projection['details']['open_defects'], defects)
        # The placement stage no longer holds the campaign: routing checks run,
        # and the full DRC refuses the open carry instead of grading the board.
        self.assertEqual(state['history'][1]['earliest_failing_stage'], 'routing', state['history'][1])
        full = read_json(placed / 'checks/drc.json')
        self.assertEqual((full['outcome'], full['details']['reason']), ('defect', 'open_placement_defects'))
        final = fixture.out / 'runs/3'
        self.assertEqual(read_json(final / 'checks/drc-placement.json')['outcome'], 'pass')
        for check_id in ('drc', 'verify'):
            check = read_json(final / f'checks/{check_id}.json')
            self.assertEqual((check['outcome'], check['details']['reason']), ('defect', 'open_placement_defects'))
        self.assertEqual(campaign.verify_campaign(fixture.out)['state'], state['state'])

    def test_unclassified_routed_artwork_stays_anchored_and_the_carry_escalates(self):
        """AGENTEE-523: a campaign artwork step without a source-bound policy cannot move graphics.

        The fixture's carry is board silk graphics. Since c6461f21 (AGENTEE-523) graphics stay
        anchored unless an explicit agent-ee.artwork-policy/1 request bound to the exact input
        board classifies them, and a campaign step's input board exists only at run time. So
        the receipted artwork step runs, moves nothing, and the open carry escalates. The
        earlier carry-then-close-then-fab-export path awaits AGENTEE-551 (owner-gated).
        """
        from agent_ee import campaign, stage_receipts
        from test_placement_adapter import BOARD
        fixture, checks, route = self.routing_fixture()
        from agent_ee import routed_artwork
        artwork_step = routed_artwork.step(fixture.root / 'artwork-tool', BOARD)
        closure_checks = [checks[0], {'id': 'netlist', 'kind': 'netlist', 'stage': 'placement'},
                          *checks[1:],
                          {'id': 'fab', 'kind': 'fab_export', 'stage': 'fab',
                           'params': {'netlist_check': 'netlist'}}]
        closure_scope = fixture.scope(closure_checks)
        # The netlist supplies native XML to fab; it is informational, not an
        # independent acceptance requirement.
        closure_scope['baseline']['requirements'] = [r for r in closure_scope['baseline']['requirements']
                                                      if r['id'] != 'R-netlist']
        next(r for r in closure_scope['baseline']['requirements'] if r['id'] == 'R-fab')['checks'] = ['netlist', 'fab']
        closure_scope['stages']['order'].extend(['verification', 'fab'])
        closure_scope['stages']['subjects']['routing'] = {'path': BOARD, 'kind': 'tracks', 'minimum': 1}
        status, state = fixture.campaign(fixture.declaration(), iterations=4,
            scope=closure_scope, steps=[route, artwork_step], name='anchored-artwork')
        self.assertEqual((status, state['state'], state['reason']), (1, 'escalated', 'no_monotonic_progress'),
                         {key: state.get(key) for key in ('state', 'reason', 'error')})
        self.assertEqual(state['failed_criteria'], ['fab', 'verify'])
        for run in ('runs/3', 'runs/4'):
            for check_id in ('verify', 'fab'):
                refused = read_json(fixture.out / run / f'checks/{check_id}.json')
                self.assertEqual((refused['outcome'], refused['details']['reason']),
                                 ('defect', 'open_placement_defects'), (run, check_id))
            self.assertEqual(read_json(fixture.out / run / 'checks/routed.json')['outcome'], 'pass')
        # The artwork step ran and was receipted, but anchored graphics did not move.
        artwork_receipt_dir = fixture.out / 'implementations/3/receipts/1-agent-ee-routed-artwork'
        artwork_receipt = stage_receipts.validate_receipt(artwork_receipt_dir)
        self.assertEqual(artwork_receipt['status'], 'completed')
        self.assertEqual(artwork_receipt['violations'], [])
        produced = read_json(artwork_receipt_dir / 'run' / placement_defects.REPORT)
        self.assertEqual(produced['write_scope']['moved_identities'], [])
        self.assertEqual(produced['verdict'], 'defect')
        self.assertTrue(produced['open_defects'])
        silk = read_json(artwork_receipt_dir / 'run' / placement_defects.ARTWORK)
        self.assertEqual(silk['moved_graphics'], [])
        self.assertTrue(silk['search_diagnostics'])
        self.assertTrue(all(d['reason'] == 'anchored_artwork' for d in silk['search_diagnostics']),
                        silk['search_diagnostics'])
        final = fixture.out / 'runs/4'
        self.assertTrue(placement_defects.open_defects(read_json(final / 'candidate.json'), final / 'inputs'))
        with patch('agent_ee.executor.execute', side_effect=AssertionError('replay launched a tool')):
            self.assertEqual(campaign.verify_campaign(fixture.out)['state'], 'escalated')

class ClosureEscapeTests(unittest.TestCase):
    def run_closure(self, edit=lambda text: text, *, receipt_rules=None, current_rules=None,
                    witness_edit=lambda witness: witness, native=False, call=None):
        TEXT, field_uuid = outlined_closure_text()
        with tempfile.TemporaryDirectory() as temporary:
            inputs = Path(temporary) / 'inputs'
            inputs.mkdir()
            old, fixed, current = (inputs / n for n in ('old.kicad_pcb', 'fixed.kicad_pcb', 'board.kicad_pcb'))
            old.write_text(TEXT)
            fixed.write_text(TEXT.replace('(at 0 -1.5 0)', '(at 0 -2.5 0)'))
            links, bundles, files = [], {}, {}
            defect = {'type': 'placement_silkscreen_conflict', 'status': 'open', 'verdict': 'defect',
                      'native': {'items': [{'uuid': field_uuid}]}}
            for number, board, defects in ((1, old, [defect]), (2, fixed, [])):
                path = inputs / f'placement-evidence/{number}/receipt.json'
                write_json(path, {'inputs': receipt_rules or {}})
                rel = path.relative_to(inputs).as_posix()
                files[rel] = digest(path)
                link = {'receipt': rel, 'sha256': digest(path)}
                links.append(link)
                bundles[rel] = ({'board_sha256': digest(board), 'open_defects': defects,
                                 'containment': witness_edit(native_containment(board))},
                                {placement_defects.BOARD: board}, complete_drc())
            write_json(inputs / 'placement-evidence/index.json', links)
            files['placement-evidence/index.json'] = digest(inputs / 'placement-evidence/index.json')
            manifest = {'scope': {'board': current.name, placement_defects.DECLARATION: links}, 'files': files}
            manifest['files'].update(current_rules or {})
            current.write_text(edit(fixed.read_text()))
            manifest['files'][current.name] = digest(current)
            with patch.object(placement_defects, '_bundle', side_effect=lambda m, i, link: bundles[link['receipt']]):
                if call:
                    return call(manifest, inputs)
                if native:
                    # Native DRC on the captured board is the closing bundle's captured report.
                    return placement_defects.open_defects(manifest, inputs, lambda: placement_defects.silk_findings(
                        bundles[links[-1]['receipt']][2]))
                return placement_defects.open_defects(manifest, inputs)

    def assert_closure_needs_native(self, *args, **kwargs):
        with self.assertRaisesRegex(ValueError, 'unverifiable without native DRC'):
            self.run_closure(*args, **kwargs)

    def test_control_identical_board_closes(self):
        self.assertEqual(self.run_closure(lambda t: t, native=True), [])
        self.assert_closure_needs_native(lambda t: t)

    def test_acceptance_without_a_sealed_native_witness_fails_closed(self):
        # Carry that would close at acceptance needs a gated check's sealed native DRC witness.
        satisfied = {'state': 'criteria_satisfied', 'reason': 'all_criteria_satisfied'}
        for checks in ([], [{'id': 'netlist', 'kind': 'netlist', 'stage': 'placement'}],
                       [{'id': 'fab', 'kind': 'fab_export', 'stage': 'fab'}]):
            def accept(manifest, inputs, diagnostic=satisfied):
                manifest['scope']['checks'] = checks
                write_json(inputs.parent / 'candidate.json', manifest)
                return placement_defects.acceptance(inputs.parent, dict(diagnostic))
            with self.subTest(checks=[c['id'] for c in checks]):
                with self.assertRaisesRegex(ValueError, 'acceptance is unverifiable: no gated check sealed native DRC'):
                    self.run_closure(call=accept)
                pending = {'state': 'repair', 'reason': 'criteria_failed'}
                self.assertEqual(self.run_closure(call=lambda m, i: accept(m, i, pending)), pending)
        # Open carry escalates without any native evidence.
        def open_carry(manifest, inputs):
            manifest['scope']['checks'] = []
            write_json(inputs.parent / 'candidate.json', manifest)
            return placement_defects.acceptance(inputs.parent, dict(satisfied))
        self.assertEqual(self.run_closure(lambda t: t.replace('"R1"', '"R\\1"'), call=open_carry),
                         {'state': 'escalated', 'reason': 'open_placement_defects'})

    def test_missing_malformed_or_mismatched_witness_retains_known_defect(self):
        for witness in (None, {}, [], {'board_sha256': 'wrong', 'markings': ['field']},
                        {'board_sha256': 'wrong', 'markings': [None]}):
            with self.subTest(witness=witness):
                self.assertEqual(len(self.run_closure(witness_edit=lambda w: witness)), 1)
        for markings in (None, 'field', [None], [''], [['nested']], [1],
                         ['00000000-0000-0000-0000-0000000000a1'] * 2, []):
            with self.subTest(markings=markings):
                self.assertEqual(len(self.run_closure(
                    witness_edit=lambda w: {**w, 'markings': markings})), 1)


    def test_octal_escape_relabels_closed_reference(self):
        # KiCad's reader decodes "R\1" as 'R\x01'; the shared parser yields 'R1'.
        self.assertEqual(len(self.run_closure(lambda t: t.replace('"R1"', '"R\\1"'))), 1)

    def test_nul_escape_truncates_value(self):
        # KiCad reads "\1\0k" as '\x01' (truncated); the shared parser yields '10k'.
        self.assertEqual(len(self.run_closure(lambda t: t.replace('"10k"', '"\\1\\0k"'))), 1)

    def test_escape_inside_layer_name(self):
        # Unknown escape inside the marking's layer token.
        self.assertEqual(len(self.run_closure(
            lambda t: t.replace('(property "Reference" "R1" (at 0 -2.5 0) (layer "F.SilkS")',
                                '(property "Reference" "R1" (at 0 -2.5 0) (layer "F.Silk\\S")'))), 1)



    def test_comment_syntax_outside_strings_refuses_closure(self):
        self.assertEqual(len(self.run_closure(lambda text: text.rstrip()[:-1] + '; comment\n)')), 1)

    def test_escaped_track_is_retained(self):
        track = r'(segment (start 1 2) (end 3 4) (width 0.2) (layer "F.Cu") (net "\1") (uuid "t"))'
        self.assertEqual(len(self.run_closure(lambda text: text.rstrip()[:-1] + track + ')')), 1)

    def test_unicode_and_semicolon_inside_string_are_byte_sensitive(self):
        self.assertEqual(len(self.run_closure(lambda text: text.replace('"R1"', '"Rés;1"'))), 1)

    def test_nonstem_rule_addition_removal_and_change_reopen_closure(self):
        for name in ('other.kicad_pro', 'nested/settings.kicad_dru'):
            sealed = {name: 'a' * 64}
            self.assertEqual(self.run_closure(receipt_rules=sealed, current_rules=sealed, native=True), [])
            self.assert_closure_needs_native(receipt_rules=sealed, current_rules=sealed)
            for before, after in (({}, sealed), (sealed, {}), (sealed, {name: 'b' * 64})):
                with self.subTest(name=name, before=before, after=after):
                    self.assertEqual(len(self.run_closure(receipt_rules=before, current_rules=after)), 1)

    def hash_hidden_artwork(self, artwork, blank=''):
        # KiCad ignores the hash lines; the span locator used to count their parentheses.
        return ('\n(arc (start 10 10) (end 12 10) (width 0.2) (layer "F.Cu")'
                ' (net 0) (uuid "h1") (mid 11 10.5\n'
                + blank + '#((\n))\n' + artwork + '\n'
                '(arc (start 30 30) (mid 31 30.5) (end 32 30) (width 0.2)'
                ' (layer "F.Cu") (net 0) (uuid "h2"))\n' + blank + '#))\n')

    def test_hash_comment_hides_gr_text(self):
        artwork = ('(gr_text "EVIL" (at 20 20 0) (layer "F.SilkS") (uuid "g")'
                   ' (effects (font (size 1 1) (thickness 0.15))))')
        for blank in ('', ' ', '\t', '\r', '\0', ' \t\r\0'):
            with self.subTest(blank=repr(blank)):
                hidden = self.hash_hidden_artwork(artwork, blank)
                self.assertEqual(len(self.run_closure(lambda t: t.rstrip()[:-1] + hidden + ')')), 1)

    def test_hash_comment_hides_second_footprint(self):
        from tests.test_placement_adapter import TEXT
        from agent_ee.symbol_library import parse_sexpr
        footprint = TEXT[TEXT.index('(footprint '):TEXT.index('(footprint "Owned:J"')].strip()
        footprint = footprint.replace('"R1"', '"R9"').replace('"a1"', '"r9-field"')
        self.assertEqual(parse_sexpr(footprint.encode('utf-8'))[0], 'footprint')
        hidden = self.hash_hidden_artwork(footprint)
        self.assertEqual(len(self.run_closure(lambda t: t.rstrip()[:-1] + hidden + ')')), 1)

    def test_hash_inside_string_is_retained_in_raw_comparison(self):
        track = '(segment (start 1 2) (end 3 4) (width 0.2) (layer "F.Cu") (net 0) (uuid "t#x"))'
        self.assertEqual(len(self.run_closure(lambda t: t.rstrip()[:-1] + track + ')')), 1)
        # The prior attempt's multiline-string input must also reopen closure.
        multiline = track.replace('t#x', 't\n#x')
        self.assertEqual(len(self.run_closure(lambda t: t.rstrip()[:-1] + multiline + ')')), 1)

    @staticmethod
    def quote_hidden_artwork(prefix):
        evil = '(gr_text EVIL (at 20 20 0) (layer F.SilkS) (uuid g) (effects (font (size 1 1) (thickness 0.15))))'
        return ('\n(arc (start 10 10) (mid 11 10.5) (end 12 10) (width 0.2) '
                '(layer "F.Cu") (net 0) (uuid ' + prefix + '"\n))\n' + evil + '\n'
                '(arc (start 30 30) (mid 31 30.5) (end 32 30) (width 0.2) '
                '(layer F.Cu) (net 0) (uuid h2"\n))\n')

    def test_mid_token_quote_hides_gr_text(self):
        for prefix in ('h1', '\x0b', '\xa0', '\x85'):
            hidden = self.quote_hidden_artwork(prefix)
            with self.subTest(prefix=repr(prefix)):
                self.assertEqual(len(self.run_closure(lambda t: t.rstrip()[:-1] + hidden + ')')), 1)

    def test_lexer_comment_and_linebreak_variants_reopen(self):
        artwork = '(gr_text "EVIL" (at 20 20) (layer "F.SilkS"))'
        for blank in ('', '\t', '\r', '\0', '\v', '\f', '\xa0'):
            for newline in ('\n', '\r\n', '\r'):
                hidden = self.hash_hidden_artwork(artwork, blank).replace('\n', newline)
                with self.subTest(blank=repr(blank), newline=repr(newline)):
                    self.assertEqual(len(self.run_closure(lambda t: t.rstrip()[:-1] + hidden + ')')), 1)
        for edit in (lambda t: t.replace('(property ', '(property \n#\n', 1),
                     lambda t: t.rstrip()[:-1] + '#((\n)'):
            self.assertEqual(len(self.run_closure(edit)), 1)

    def test_only_writer_grammar_track_spans_are_exempt(self):
        track = '(segment (start 1 2) (end 3 4) (width 0.2) (layer "F.Cu") (net 0) (uuid "t"))'
        # Writer whitespace and bare atoms stay live, including CRLF output.
        for valid in (track, track.replace(' ', '\t'), track.replace(' ', '\r\n'),
                      track.replace('"t"', 't_1.+-')):
            with self.subTest(valid=repr(valid)):
                self.assertEqual(self.run_closure(lambda t: t.rstrip()[:-1] + valid + ')', native=True), [])
                self.assert_closure_needs_native(lambda t: t.rstrip()[:-1] + valid + ')')
        for invalid in (track.replace('"t"', 't/1'), track.replace('"t"', 't:1'),
                        track.replace('"t"', 't#1'), track.replace('"t"', 't;1'),
                        track.replace('"t"', '"t#1"'), track.replace('"t"', '"t;1"'),
                        track.replace('"t"', '"t""x"'),
                        *(track.replace('"t"', '"t' + c + 'x"')
                          for c in ('\n', '\r', '\t', '\x01', '\x7f', '\x85', '\x9f')),
                        *(track.replace(' ', c, 1)
                          for c in ('\r', '\v', '\f', '\xa0', '\x85'))):
            with self.subTest(invalid=repr(invalid)):
                self.assertEqual(len(self.run_closure(lambda t: t.rstrip()[:-1] + invalid + ')')), 1)

    def test_single_line_mid_token_quote_hides_gr_text(self):
        evil = '(gr_text EVIL (at 20 20 0) (layer F.SilkS) (uuid g) (effects (font (size 1 1) (thickness 0.15))))'
        for prefix in ('abc', 'h1', '0'):
            hidden = ('(arc (start 10 10) (mid 11 10.5) (end 12 10) (width 0.2) (layer "F.Cu") (net 0) (uuid '
                      + prefix + '"x ))' + evil + '(arc (start 30 30) (mid 31 30.5) (end 32 30) (width 0.2) '
                      '(layer F.Cu) (net 0) (uuid y"))')
            with self.subTest(prefix=prefix):
                self.assertEqual(len(self.run_closure(lambda t: t.rstrip()[:-1] + hidden + ')')), 1)

    def test_nested_list_track_child_refuses_comparison_and_keeps_carry(self):
        from tests.test_placement_adapter import TEXT
        for kind in ('segment', 'arc'):
            track = ('(' + kind + ' (layer "F.Cu") ((x)) (start 1 2) '
                     '(end 3 4) (width 0.2) (net 0) (uuid "t"))')
            edit = lambda text: text.rstrip()[:-1] + track + ')'
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                before, after = (Path(temporary) / n for n in ('before', 'after'))
                before.write_text(TEXT)
                after.write_text(edit(TEXT))
                with self.assertRaisesRegex(ValueError, 'nested-list track child'):
                    placement_defects.same_artwork(before, after)
                defects = self.run_closure(edit)
                self.assertEqual(len(defects), 1)
                self.assertEqual(defects[0]['verdict'], 'defect')

    def test_nested_list_value_of_allowed_track_child_refuses_comparison(self):
        from tests.test_placement_adapter import TEXT
        evil = ('(gr_text EVIL (at 20 20 0) (layer F.SilkS) (uuid g) '
                '(effects (font (size 1 1) (thickness 0.15))))')
        tracks = {
            'start-head': '(segment (layer "F.Cu") (start (1) 2) (end 3 4) (width 0.2) (net 0) (uuid "t"))',
            'start-text': ('(segment (start ' + evil + ' 2) (end 3 4) (width 0.2) '
                           '(layer "F.Cu") (net 0) (uuid "t"))'),
            'start-tail': ('(segment (start 1 2 ' + evil + ') (end 3 4) (width 0.2) '
                           '(layer "F.Cu") (net 0) (uuid "t"))'),
            'arc-mid': ('(arc (start 1 2) (mid 2 (3)) (end 3 4) (width 0.2) '
                        '(layer "F.Cu") (net 0) (uuid "t"))'),
            'locked': '(segment (start 1 2) (end 3 4) (width 0.2) (layer "F.Cu") (locked (yes)) (net 0) (uuid "t"))',
            'net': '(segment (start 1 2) (end 3 4) (width 0.2) (layer "F.Cu") (net (0)) (uuid "t"))',
            'uuid': '(segment (start 1 2) (end 3 4) (width 0.2) (layer "F.Cu") (net 0) (uuid ("t")))',
            'width': '(segment (start 1 2) (end 3 4) (width (0.2)) (layer "F.Cu") (net 0) (uuid "t"))',
            'layer': '(segment (start 1 2) (end 3 4) (width 0.2) (layer ("F.Cu")) (net 0) (uuid "t"))',
            'silk-layer': '(segment (start (1) 2) (end 3 4) (width 0.2) (layer "F.SilkS") (net 0) (uuid "t"))',
        }
        for name, track in tracks.items():
            edit = lambda text, track=track: text.rstrip()[:-1] + track + ')'
            with self.subTest(case=name), tempfile.TemporaryDirectory() as temporary:
                before, after = (Path(temporary) / n for n in ('before', 'after'))
                before.write_text(TEXT)
                after.write_text(edit(TEXT))
                with self.assertRaisesRegex(ValueError, 'nested-list track child value'):
                    placement_defects.same_artwork(before, after)
                with self.assertRaisesRegex(ValueError, 'nested-list track child value'):
                    placement_defects.same_artwork(after, after)
                defects = self.run_closure(edit)
                self.assertEqual(len(defects), 1)
                self.assertEqual(defects[0]['verdict'], 'defect')

    def test_nested_list_in_unknown_track_child_stays_in_exact_comparison(self):
        from tests.test_placement_adapter import TEXT
        track = ('(segment (start 1 2) (end 3 4) (width 0.2) (layer "F.Cu") (net 0) '
                 '(unknown (x 1)) (uuid "t"))')
        with tempfile.TemporaryDirectory() as temporary:
            before, after, moved = (Path(temporary) / n for n in ('before', 'after', 'moved'))
            before.write_text(TEXT.rstrip()[:-1] + track + ')')
            after.write_text(TEXT.rstrip()[:-1] + track + ')')
            moved.write_text(TEXT.rstrip()[:-1] + track.replace('3 4', '5 6') + ')')
            self.assertTrue(placement_defects.same_artwork(before, after))
            self.assertFalse(placement_defects.same_artwork(before, moved))

    def test_net_name_comment_characters_reopen_closure(self):
        for name, count in (('CS_N', 0), ('CS#', 1), ('A;B', 1)):
            track = ('(segment (start 1 2) (end 3 4) (width 0.2) '
                     '(layer "F.Cu") (net "' + name + '") (uuid "t"))')
            with self.subTest(name=name):
                self.assertEqual(len(self.run_closure(lambda t: t.rstrip()[:-1] + track + ')',
                                                      native=count == 0)), count)
                if count == 0:
                    self.assert_closure_needs_native(lambda t: t.rstrip()[:-1] + track + ')')
