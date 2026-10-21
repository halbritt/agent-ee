"""Carried placement evidence must gate only its own downstream candidate lineage."""
import contextlib
import copy
from pathlib import Path
import shutil
import time
import unittest

from agent_ee import placement_defects
from agent_ee.storage import digest, read_json, write_json


class PlacementDefectContractTests(unittest.TestCase):
    def test_uncarried_candidate_has_no_defects(self):
        self.assertEqual(placement_defects.open_defects({'scope': {}, 'files': {}}, None), [])

    def test_initial_campaign_cannot_supply_coordinator_carry(self):
        placement_defects.validate_campaign({'inputs': []})
        for scope in ({'inputs': [{'at': placement_defects.DIRECTORY}]},
                      {'inputs': [], placement_defects.DECLARATION: []}):
            with self.subTest(scope=scope), self.assertRaisesRegex(ValueError, 'coordinator-owned'):
                placement_defects.validate_campaign(scope)


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
        for data in (stamped(self.board), stamped(self.board, 'A' * 64), stamped(self.board, self.a, self.a),
                     stamped(self.board, 'a' * 63),
                     self.board.replace(b'(uuid "e2")', f'(uuid "e2")) (gr_text "{placement_defects.STAMP} {self.a}"'.encode(), 1),
                     stamped(stamped(self.board, self.a), self.b)):
            with self.subTest(data=data[:200]), self.assertRaisesRegex(ValueError, 'lineage stamp'):
                placement_defects.stamp(data)

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

    def rescope(self, name, *, lineage):
        """Release a delivered candidate's files under a fresh, out-of-campaign scope."""
        from agent_ee.candidate import capture
        root = self.helper.root / name
        design = root / 'design'
        shutil.copytree(self.candidate_run / 'inputs', design,
                        ignore=None if lineage else shutil.ignore_patterns(placement_defects.DIRECTORY))
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

    def test_expired_tool_budget_does_not_erase_known_carried_defects(self):
        from agent_ee.checks import run_checks
        check = run_checks(self.candidate_run, time.monotonic() - 1)[0]
        self.assertEqual((check['execution'], check['outcome']), ('ok', 'defect'))
        placement_defects.verify_gate(self.candidate_run, check, self.manifest)


class ClosureContractTests(unittest.TestCase):
    def setUp(self):
        self.native = {'type': 'placement_silkscreen_conflict', 'status': 'open', 'verdict': 'defect',
                       'native': {'items': [{'uuid': 'field'}]}}
        self.diagnostic = {'type': 'placement_silkscreen_conflict', 'status': 'open', 'verdict': 'defect',
                           'uuid': 'field', 'reason': 'footprint_side_flipped'}
        self.original = {'field': ('footprint', ['property', 'Reference', 'U1'], [['at', '0', '0']])}

    def test_native_closes_only_on_current_clear_same_identity_evidence(self):
        self.assertTrue(placement_defects._closes(self.native, self.original, self.original, set(), set(), True))
        for current, conflicts, fresh in (({}, set(), True), (self.original, {'field'}, True),
                                          (self.original, set(), False),
                                          ({'field': ('other-footprint', ['property', 'Reference', 'U1'], [])}, set(), True)):
            self.assertFalse(placement_defects._closes(self.native, self.original, current, conflicts, set(), fresh))

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
        original = {**self.original, 'pad': ('footprint', ['pad', '1', 'smd'], [['at', '0', '0']])}
        self.assertTrue(placement_defects._closes(defect, original, original, {'pad', 'other-field'}, set(), True))
        self.assertFalse(placement_defects._closes(defect, original, original, {'field'}, set(), True))
