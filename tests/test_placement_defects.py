"""Carried placement evidence must gate only its own downstream candidate lineage."""
import contextlib
import copy
from pathlib import Path
import shutil
import time
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import placement_defects
from agent_ee.storage import digest, read_json, write_json


class PlacementDefectContractTests(unittest.TestCase):
    def test_uncarried_candidate_has_no_defects(self):
        self.assertEqual(placement_defects.open_defects({'scope': {}, 'files': {}}, None), [])

    def test_all_fab_family_kinds_gate_independent_of_stage(self):
        kinds = ('fab_profile', 'dfm_geometry', 'dfa_dft', 'assembly_readiness',
                 'jlc_cost_estimate', 'drc', 'fab_roundtrip', 'fab_export', 'fab_package',
                 'fab_consistency', 'cpl_rotation', 'jlc_dfm', 'part_binding',
                 'package_consistency', 'parts_subset', 'land_pattern', 'catalog', 'part_selection')
        for kind in kinds:
            for stage in (None, 'placement', 'routing', 'fab', 'verification', 'release'):
                with self.subTest(kind=kind, stage=stage):
                    self.assertTrue(placement_defects.gated({'kind': kind, 'stage': stage}))


    def test_initial_campaign_cannot_supply_coordinator_carry(self):
        placement_defects.validate_campaign({'inputs': []})
        for scope in ({'inputs': [{'at': placement_defects.DIRECTORY}]},
                      {'inputs': [], placement_defects.DECLARATION: []}):
            with self.subTest(scope=scope), self.assertRaisesRegex(ValueError, 'coordinator-owned'):
                placement_defects.validate_campaign(scope)


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
        with tempfile.TemporaryDirectory() as temporary:
            inputs = Path(temporary)
            old, fixed, current = (inputs / n for n in ('old.kicad_pcb', 'fixed.kicad_pcb', 'board.kicad_pcb'))
            old.write_text(TEXT)
            fixed.write_text(TEXT.replace('(at 0 -1.5 0)', '(at 0 -2.5 0)'))
            current.write_bytes(fixed.read_bytes())
            links, bundles, files = [], {}, {}
            defect = {'type': 'placement_silkscreen_conflict', 'status': 'open', 'verdict': 'defect',
                      'native': {'items': [{'uuid': 'a1'}]}}
            for number, board, defects in ((1, old, [defect]), (2, fixed, [])):
                path = inputs / f'placement-evidence/{number}/receipt.json'
                write_json(path, {'inputs': {}})
                rel = path.relative_to(inputs).as_posix()
                files[rel] = digest(path)
                link = {'receipt': rel, 'sha256': digest(path)}
                links.append(link)
                bundles[rel] = ({'board_sha256': digest(board), 'open_defects': defects},
                                {placement_defects.BOARD: board}, {'violations': []})
            index = 'placement-evidence/index.json'
            write_json(inputs / index, links)
            files[index] = digest(inputs / index)
            manifest = {'scope': {'board': current.name, placement_defects.DECLARATION: links},
                        'files': files}
            with patch.object(placement_defects, '_bundle', side_effect=lambda m, i, link: bundles[link['receipt']]):
                manifest['files'][current.name] = digest(current)
                self.assertEqual(placement_defects.open_defects(manifest, inputs), [])
                track = '(segment (start 1 2) (end 3 4) (width 0.2) (layer "F.Cu") (net 0) (uuid "t"))'
                current.write_text(fixed.read_text().rstrip()[:-1] + track + ')')
                manifest['files'][current.name] = digest(current)
                self.assertEqual(placement_defects.open_defects(manifest, inputs), [])
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


class PlacementRoutingCarryTests(unittest.TestCase):
    def test_real_placement_and_router_keep_open_carry_at_verification(self):
        from test_placement_campaign import PlacementCampaignFixture
        from test_placement_adapter import owned_design, BOARD, POLICY
        from toolchain import require_kicad_route, require_toolchain
        from agent_ee import placement_adapter as pa, routing_adapter, campaign, stage_receipts
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
        scope = fixture.scope(checks)
        scope['stages']['order'].append('verification')
        scope['stages']['subjects']['routing'] = {'path': BOARD, 'kind': 'tracks', 'minimum': 1}
        route = routing_adapter.step(project='project', board='rc_filter.kicad_pcb',
            schematic='rc_filter.kicad_sch', name='rc_filter', label='carry', checkout=checkout, budget=120)
        status, state = fixture.campaign(fixture.declaration(), iterations=3, scope=scope, steps=[route])
        self.assertEqual(len(state['history']), 3, {k: state.get(k) for k in ('state', 'reason', 'error', 'history')})
        self.assertEqual(status, 1, state)
        self.assertNotEqual(state['state'], 'criteria_satisfied')
        placement = read_json(fixture.out / 'implementations/1/placement-adapter.json')
        self.assertEqual(placement['status'], 'proposed')
        result = read_json(fixture.out / 'implementations/1' / placement['helper']['receipt'] / pa.RESULT)
        self.assertTrue(result['open_defects'])
        self.assertNotIn('2', state['placement_records'])
        receipt_dir = fixture.out / 'implementations/2/receipts/1-kicad-route-krt'
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


class ClosureEscapeTests(unittest.TestCase):
    def run_closure(self, edit=lambda text: text, *, receipt_rules=None, current_rules=None):
        from tests.test_placement_adapter import TEXT
        with tempfile.TemporaryDirectory() as temporary:
            inputs = Path(temporary)
            old, fixed, current = (inputs / n for n in ('old.kicad_pcb', 'fixed.kicad_pcb', 'board.kicad_pcb'))
            old.write_text(TEXT)
            fixed.write_text(TEXT.replace('(at 0 -1.5 0)', '(at 0 -2.5 0)'))
            links, bundles, files = [], {}, {}
            defect = {'type': 'placement_silkscreen_conflict', 'status': 'open', 'verdict': 'defect',
                      'native': {'items': [{'uuid': 'a1'}]}}
            for number, board, defects in ((1, old, [defect]), (2, fixed, [])):
                path = inputs / f'placement-evidence/{number}/receipt.json'
                write_json(path, {'inputs': receipt_rules or {}})
                rel = path.relative_to(inputs).as_posix()
                files[rel] = digest(path)
                link = {'receipt': rel, 'sha256': digest(path)}
                links.append(link)
                bundles[rel] = ({'board_sha256': digest(board), 'open_defects': defects},
                                {placement_defects.BOARD: board}, {'violations': []})
            write_json(inputs / 'placement-evidence/index.json', links)
            files['placement-evidence/index.json'] = digest(inputs / 'placement-evidence/index.json')
            manifest = {'scope': {'board': current.name, placement_defects.DECLARATION: links}, 'files': files}
            manifest['files'].update(current_rules or {})
            current.write_text(edit(fixed.read_text()))
            manifest['files'][current.name] = digest(current)
            with patch.object(placement_defects, '_bundle', side_effect=lambda m, i, link: bundles[link['receipt']]):
                return placement_defects.open_defects(manifest, inputs)

    def test_control_identical_board_closes(self):
        self.assertEqual(self.run_closure(lambda t: t), [])

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
            self.assertEqual(self.run_closure(receipt_rules=sealed, current_rules=sealed), [])
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
                self.assertEqual(self.run_closure(lambda t: t.rstrip()[:-1] + valid + ')'), [])
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
