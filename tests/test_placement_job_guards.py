"""H1-H4 regression negatives, derived from the AGENTEE-515 Astra probes."""
import copy
from contextlib import ExitStack
import inspect
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from agent_ee import placement_job as job
from artifact_retention import RetainedTemporaryDirectory
import test_placement_job as fixtures
from test_placement_job import Fixture, intent, groups, MM


def process_finished(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().split()[2] == 'Z'
    except (FileNotFoundError, ProcessLookupError):
        return True


class ProcessObservationTests(unittest.TestCase):
    def test_process_disappearing_during_stat_read_is_already_finished(self):
        with patch.object(Path, 'read_text', side_effect=ProcessLookupError('process exited during read')):
            self.assertTrue(process_finished(123456))


def materialize(source, poses, destination):
    # Allows the identical negative to exercise the immutable pre-repair source.
    parameters = inspect.signature(job.materialize).parameters
    kwargs = ({'normalized': job.normalize_intent(intent(), groups())} if 'normalized' in parameters
              else {'intent': intent()} if 'intent' in parameters else {})
    return job.materialize(source, poses, destination, b'{}\n', **kwargs)


class ProtectedPoseTests(Fixture):
    def test_coherent_locked_and_intent_fixed_moves_refused_before_save(self):
        problem, _ = self.problem()
        poses = fixtures.NativeTransformTests._evaluate(self, problem, {})['poses']
        for ref in ('H1', 'FID'):
            for moved in (True, False):
                with self.subTest(ref=ref, moved=moved):
                    wrong = copy.deepcopy(poses)
                    row = next(p for p in wrong if p['native_ref'] == ref)
                    row['x_nm'] += MM
                    row['moved'] = moved
                    for pad in row['pads']:
                        pad['x_nm'] += MM
                    destination = self.dir / f'{ref}-{moved}'
                    with self.assertRaisesRegex(job.JobRefusal, 'protected'):
                        materialize(self.board, wrong, destination)
                    self.assertFalse((destination / 'board.kicad_pcb').exists())

    def test_protected_moved_flag_alone_is_refused(self):
        problem, _ = self.problem()
        poses = fixtures.NativeTransformTests._evaluate(self, problem, {})['poses']
        for ref in ('H1', 'FID'):
            with self.subTest(ref=ref):
                wrong = copy.deepcopy(poses)
                next(p for p in wrong if p['native_ref'] == ref)['moved'] = True
                with self.assertRaisesRegex(job.JobRefusal, 'protected'):
                    materialize(self.board, wrong, self.dir / ref)


class FinalInventoryTests(Fixture):
    def test_final_drc_describes_the_saved_board_after_stub_pruning(self):
        import pcbnew
        from agent_ee import local_decoupling as local
        problem,_=self.problem()
        poses=fixtures.NativeTransformTests._evaluate(self,problem,{})['poses']
        placed=materialize(self.board,poses,self.dir/'placed');placed['directory']=str(self.dir/'placed')
        (self.dir/'placed/native-drc.json').write_text('{"violations":[],"unconnected_items":[]}')
        native=pcbnew.LoadBoard(placed['board']);path=self.dir/'routed.kicad_pcb'
        track=pcbnew.PCB_TRACK(native);track.SetStart(pcbnew.VECTOR2I(1_000_000,1_000_000))
        track.SetEnd(pcbnew.VECTOR2I(1_100_000,1_000_000));track.SetWidth(200_000);native.Add(track)
        uid=track.m_Uuid.AsString();pcbnew.SaveBoard(str(path),native)
        def prune(board,*args,**kwargs):
            candidate=pcbnew.LoadBoard(str(board))
            candidate.Remove(next(t for t in candidate.GetTracks() if t.m_Uuid.AsString()==uid))
            pcbnew.SaveBoard(str(board),candidate)
            return dict(status='accepted',removed=[uid])
        def drc(board,directory,**kwargs):
            contains=any(t.m_Uuid.AsString()==uid for t in pcbnew.LoadBoard(str(board)).GetTracks())
            return dict(violations=[],unconnected_items=[{}] if contains else []),dict(board_sha256=job.digest(board),label=kwargs['label'])
        with patch.object(job,'_cleanup_local_router_stubs',side_effect=prune), patch.object(job,'drc',side_effect=drc), \
             patch.object(local,'verify',return_value=dict(status='pass')), \
             patch('agent_ee.decoupling_loops.measure',return_value=dict(outcome='info')), \
             patch.object(job,'_group_views',return_value=dict(status='rendered')):
            result=job.final_checks(dict(status='routed',board=str(path),local_decoupling=dict(contract={},protected=[dict(uuid=uid)])),placed,
                job._owners(native),set(),self.dir/'final-pruned',job.normalize_intent(intent(),groups()))
        self.assertEqual(result['opens'],0,result)
        self.assertEqual(result['drc']['label'],'native-drc-after-stub-prune')
        self.assertEqual(result['drc']['board_sha256'],result['board_sha256'])

    def test_saved_incomplete_route_cannot_complete_despite_router_success(self):
        import pcbnew
        problem, _ = self.problem()
        poses = fixtures.NativeTransformTests._evaluate(self, problem, {})['poses']
        placed = materialize(self.board, poses, self.dir / 'placed')
        placed['directory'] = str(self.dir / 'placed')
        native, _ = job.drc(Path(placed['board']), self.dir / 'placed', label='native-drc')
        self.assertTrue(native['unconnected_items'])
        routed = {'status': 'routed', 'board': placed['board']}
        final = job.final_checks(routed, placed, job._owners(pcbnew.LoadBoard(placed['board'])),
                                 set(), self.dir / 'final-incomplete', job.normalize_intent(intent(), groups()))
        self.assertGreater(final['opens'], 0)
        self.assertEqual(final['integrity'], 'preserved')
        self.assertEqual(job._completion_status(routed, placed, final), 'verification_failed')
        self.assertNotEqual(job._completion_status(routed, placed, {}), 'completed')

    def test_native_mutations_are_ineligible_even_with_clean_drc(self):
        import pcbnew
        problem, _ = self.problem()
        poses = fixtures.NativeTransformTests._evaluate(self, problem, {})['poses']
        placed = materialize(self.board, poses, self.dir / 'placed')
        placed['directory'] = str(self.dir / 'placed')
        (self.dir / 'placed/native-drc.json').write_text('{"violations":[],"unconnected_items":[]}')
        for mutation in ('position', 'size', 'drill', 'shape', 'layers', 'graphic', 'rule', 'outline', 'drawing', 'fpid'):
            with self.subTest(mutation=mutation):
                board = pcbnew.LoadBoard(placed['board'])
                fp = next(f for f in board.GetFootprints() if f.GetReference() == 'R1')
                pad = next(iter(fp.Pads()))
                if mutation == 'position':
                    pad.SetPosition(pcbnew.VECTOR2I(pad.GetPosition().x + 100000, pad.GetPosition().y))
                elif mutation == 'size':
                    pad.SetSize(pcbnew.PADSTACK.ALL_LAYERS, pcbnew.VECTOR2I(123456, 234567))
                elif mutation == 'drill':
                    hole = next(f for f in board.GetFootprints() if f.GetReference() == 'H1')
                    next(iter(hole.Pads())).SetDrillSize(pcbnew.VECTOR2I(500000, 600000))
                elif mutation == 'shape':
                    pad.SetShape(pcbnew.PADSTACK.ALL_LAYERS, pcbnew.PAD_SHAPE_OVAL)
                elif mutation == 'layers':
                    layers = pcbnew.LSET()
                    layers.AddLayer(pcbnew.B_Cu)
                    pad.SetLayerSet(layers)
                elif mutation == 'graphic':
                    next(iter(fp.GraphicalItems())).SetWidth(200000)
                elif mutation == 'rule':
                    board.GetDesignSettings().m_SolderMaskExpansion += 100000
                elif mutation == 'fpid':
                    fp.SetFPID(pcbnew.LIB_ID('changed', 'changed'))
                elif mutation == 'outline':
                    next(iter(board.GetDrawings())).SetWidth(200000)
                elif mutation == 'drawing':
                    from test_placement_job import _segment
                    board.Add(_segment(pcbnew, board, pcbnew.Dwgs_User, (0, 0), (MM, MM)))
                path = self.dir / f'{mutation}.kicad_pcb'
                pcbnew.SaveBoard(str(path), board)
                path.with_suffix('.kicad_pro').write_bytes(Path(placed['board']).with_suffix('.kicad_pro').read_bytes())
                # Real native DRC still runs. Clean-report injection isolates eligibility
                # from the board's unrelated physical errors, after that native check.
                actual_drc = job.drc
                def clean_drc(*args, **kwargs):
                    _, execution = actual_drc(*args, **kwargs)
                    return {'violations': [], 'unconnected_items': []}, execution
                with patch.object(job, 'drc', side_effect=clean_drc):
                    final = job.final_checks({'status': 'routed', 'board': str(path)}, placed,
                                             job._owners(board), set(), self.dir / f'final-{mutation}',
                                             job.normalize_intent(intent(), groups()))
                self.assertTrue(final['identity_problems'], mutation)
                self.assertEqual(final.get('eligibility'), 'INELIGIBLE')
                self.assertEqual(final['physical_checks'], 'FAILED')

    def test_only_tracks_vias_and_explicitly_owned_zones_may_change(self):
        import pcbnew
        problem, _ = self.problem()
        poses = fixtures.NativeTransformTests._evaluate(self, problem, {})['poses']
        placed = materialize(self.board, poses, self.dir / 'placed')
        placed['directory'] = str(self.dir / 'placed')
        (self.dir / 'placed/native-drc.json').write_text('{"violations":[],"unconnected_items":[]}')
        board = pcbnew.LoadBoard(placed['board'])
        track = pcbnew.PCB_TRACK(board)
        track.SetStart(pcbnew.VECTOR2I(MM, MM))
        track.SetEnd(pcbnew.VECTOR2I(2 * MM, MM))
        track.SetWidth(200000)
        track.SetLayer(pcbnew.F_Cu)
        board.Add(track)
        via = pcbnew.PCB_VIA(board)
        via.SetPosition(pcbnew.VECTOR2I(2 * MM, MM))
        via.SetWidth(600000)
        via.SetDrill(300000)
        via.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        board.Add(via)
        path = self.dir / 'routed.kicad_pcb'
        pcbnew.SaveBoard(str(path), board)
        path.with_suffix('.kicad_pro').write_bytes(Path(placed['board']).with_suffix('.kicad_pro').read_bytes())
        final = job.final_checks({'status': 'routed', 'board': str(path)}, placed, job._owners(board), set(),
                                 self.dir / 'final', job.normalize_intent(intent(), groups()))
        self.assertEqual(final['identity_problems'], [])
        zone = pcbnew.ZONE(board)
        zone.SetLayer(pcbnew.F_Cu)
        resistor = next(fp for fp in board.GetFootprints() if fp.GetReference() == 'R1')
        zone.SetNet(next(iter(resistor.Pads())).GetNet())
        outline = zone.Outline()
        outline.NewOutline()
        for x, y in ((1, 1), (2, 1), (2, 2), (1, 2)):
            outline.Append(pcbnew.VECTOR2I(x * MM, y * MM))
        board.Add(zone)
        pcbnew.SaveBoard(str(path), board)
        path.with_suffix('.kicad_pro').write_bytes(Path(placed['board']).with_suffix('.kicad_pro').read_bytes())
        routed = {'status': 'routed', 'board': str(path)}
        protected = job.final_checks(routed, placed, job._owners(board), set(), self.dir / 'protected-zone',
                                     job.normalize_intent(intent(), groups()))
        self.assertTrue(protected['identity_problems'], 'an unowned zone must be protected')
        owned = job.final_checks(routed, placed, job._owners(board), set(), self.dir / 'owned-zone',
                                 job.normalize_intent(intent(), groups()),
                                 owned_zone_uuids={zone.m_Uuid.AsString()})
        self.assertEqual(owned['identity_problems'], [])


class ControllerBoundaryTests(Fixture):
    def test_native_rejection_tries_next_legal_placement_and_retains_both_results(self):
        self._portfolio_failure()

    def test_full_generation_is_tried_before_deferred_fallback(self):
        self._portfolio_failure(local_failure=True)

    def test_more_generated_relations_win_between_partial_candidates(self):
        self._portfolio_failure(local_failure=True, partial=True)

    def test_all_generation_refusals_still_route_the_first_fallback(self):
        self._portfolio_failure(local_failure=True, all_fail=True)

    def test_deferred_candidate_reuses_the_verified_acquisition_seed(self):
        self._portfolio_failure(local_failure=True, all_fail=True, acquisition=True)

    def test_later_verification_failure_clears_the_previous_local_copper_reason(self):
        self._portfolio_failure(local_failure=True, final_fail=True)

    def _portfolio_failure(self, *, local_failure=False, all_fail=False, final_fail=False, partial=False, acquisition=False):
        """Controller fault injection only; normal native entrypoint runs separately."""
        problem, _ = self.problem()
        out = self.dir / 'portfolio'
        config = {'budget_s': 60, 'inputs': {k: {} for k in ('board', 'project', 'intent', 'groups')},
                  'search': {'shortlist': 3}, 'router': {'checkout': 'unused'}}
        values = [(self.board, self.board.read_bytes()), (self.project, b'{}'),
                  (self.board, json.dumps(intent()).encode()), (self.board, json.dumps(groups()).encode())]
        output = {'timing': {'prepare_s': 0}, 'status': 'completed', 'counters': {}, 'seeds': [], 'prng': 'probe',
                  'shortlist': [{'seed': n, 'cost': str(n + 1), 'poses': [n]} for n in range(3)]}
        if partial:
            from test_local_decoupling import contract
            doc = contract()
            doc['planes'] = []  # Routing is the mocked boundary in this controller test.
            doc['relations'] = [dict(doc['relations'][0], id='relation-'+str(i)) for i in range(3)]
            config['inputs']['local_decoupling'] = {}
            values.append((self.board, json.dumps(doc).encode()))
        selected = []
        produced = []
        def materialized(_board, poses, directory, *_args, **_kwargs):
            return {'board': str(self.board), 'board_sha256': f'candidate-{poses[0]}',
                    'integrity': 'preserved', 'problems': []}
        def routed(directory, *_args, **_kwargs):
            selected.append(str(directory))
            result = {'status': 'routed', 'board': str(self.board), 'generated_relation_count':1, 'preflight': {
                k: None for k in job.ROUTER_IDENTITY}}
            if partial:
                count = 1 if str(directory).endswith('seed0') else 2
                result['generated_relation_count'] = count
                if _kwargs.get('local_fallback_reason') is None:
                    result.update(status='local_copper_deferred',reason='one bank refused')
                return result
            if local_failure and (all_fail or len(selected) == 1):
                result['generated_relation_count'] = 0
                if not _kwargs.get('local_fallback_reason') and _kwargs.get('defer_local_fallback'):
                    result.update(status='local_copper_deferred', reason='local decoupling cell: no clear local path')
            if acquisition:result['copper_policy']='preserve_existing'
            return result
        failed = {'integrity': 'preserved', 'edge_intent': {'status': 'pass'},
                  'eligibility': 'ineligible', 'physical_checks': 'FAILED'}
        passed = {**failed, 'eligibility': 'eligible', 'physical_checks': 'PASS'}
        if acquisition:passed.update(board_sha256='final',local_routing=dict(board_sha256='final'))
        with ExitStack() as stack:
            capture_record = {}
            if acquisition:
                from agent_ee import local_return_seed
                normalize = job.normalize_intent
                recipe = {'controller-control': True}
                binding = dict(recipe=recipe, original_board_sha256=job.sha256_bytes(self.board.read_bytes()),
                               binding_sha256='control-binding')
                def normalized(*args, **kwargs):
                    result = normalize(*args, **kwargs); result['mechanics'] = recipe; return result
                stack.enter_context(patch.object(job,'normalize_intent',side_effect=normalized))
                capture_record['mechanics'] = binding
                stack.enter_context(patch.object(job,'_pose_solver',return_value={}))
                stack.enter_context(patch.object(job,'_repair_shortlist',side_effect=
                    lambda problem, candidates, *args, **kwargs: [] if kwargs.get('routing_exhausted') else candidates))
                stack.enter_context(patch.object(job,'local_routing_applies',return_value=True))
                def mechanics(path, *args, **kwargs):
                    index = int(Path(path).parent.name[-1])
                    return dict(status='pass',binding_sha256='control-binding',board_sha256='candidate-'+str(index))
                stack.enter_context(patch.object(job,'check_mechanics',side_effect=mechanics))
                def produce(directory, output, **kwargs):
                    produced.append(str(directory));return dict(output=str(directory),record='controlled-receipt.json')
                stack.enter_context(patch.object(local_return_seed,'produce',side_effect=produce))
                stack.enter_context(patch.object(local_return_seed,'verify',return_value=dict(status='pass')))

            stack.enter_context(patch.object(job, 'verified_input', side_effect=lambda _entry, label:
                dict(zip(('board', 'project', 'intent', 'groups', 'local_decoupling'), values))[label]))
            stack.enter_context(patch.object(job, 'preflight', return_value={'engine_sha256': 'probe', 'engine':'unused'}))
            stack.enter_context(patch.object(job, 'capture', return_value=(problem, capture_record)))
            stack.enter_context(patch.object(job, 'solve', return_value=(output, {})))
            stack.enter_context(patch.object(job, 'materialize', side_effect=materialized))
            stack.enter_context(patch.object(job, 'check_edge_intent', side_effect=[
                {'status': 'pass'}, {'status': 'failed'}, {'status': 'pass'}]))
            stack.enter_context(patch.object(job, 'drc', return_value=({'violations': [], 'unconnected_items': []}, {})))
            stack.enter_context(patch.object(job, 'route', side_effect=routed))
            stack.enter_context(patch.object(job, 'final_checks', side_effect=([failed,failed] if final_fail else [passed]) if local_failure else [failed, passed]))
            report = job.run_job(config, out)
        self.assertEqual(report['status'], 'verification_failed' if final_fail else 'completed', report)
        if partial:
            self.assertEqual([t['generated_relation_count'] for t in report['routing_trials']],[1,2,2])
            self.assertEqual([t['status'] for t in report['routing_trials']],['local_copper_deferred','local_copper_deferred','completed'])
            self.assertEqual(report['route_selection']['seed'],2)
        elif local_failure and all_fail:
            self.assertEqual([t['status'] for t in report['routing_trials']],
                             ['local_copper_deferred','local_copper_deferred','completed'])
            self.assertEqual(report['route_selection']['seed'],0)
            self.assertEqual(report['routed_candidate'],'deferred_incumbent')
            last=report['routing_trials'][-1]
            self.assertEqual(last['deferral_budget_reduced'],last['router_budget_s'] < last['original_router_budget_s'])
            self.assertIn('deferral_budget_loss_s',last)
            self.assertIn('local_fallback_reason',json.loads((out/'routed-incumbent.json').read_text())['placement'])
        elif local_failure and final_fail:
            self.assertEqual([t['status'] for t in report['routing_trials']],
                             ['local_copper_deferred','verification_failed','verification_failed'])
        else:
            self.assertEqual([t['status'] for t in report['routing_trials']],
                             ['local_copper_deferred' if local_failure else 'verification_failed',
                              'verification_failed' if final_fail else 'completed'])
            if not final_fail:
                self.assertEqual(report['route_selection']['seed'],2)
                if local_failure:self.assertEqual([t['generated_relation_count'] for t in report['routing_trials']],[0,1])
        # A completed job states no reason. A rejected one states its own, never an earlier trial's deferral.
        if final_fail:self.assertEqual(report['reason'],'final board is ineligible')
        else:self.assertNotIn('reason',report)
        if acquisition:self.assertEqual(len(produced),2,produced)
        if final_fail:self.assertFalse((out/'routed-incumbent.json').exists())
        else:self.assertTrue((out/'routed-incumbent.json').exists())
        state=json.loads((out/'checkpoint.json').read_text())
        for trial in state['routing_trials']:
            self.assertEqual(job.digest(trial['path']),trial['sha256'])

    def test_router_refusal_stops_the_trial_loop_and_keeps_the_message(self):
        """A refusal before routing does not depend on placement: one trial, no other seed (defect 31)."""
        for router, mode in (({'checkout': 'unused'}, None), ({'checkout': 'unused', 'diff_pairs': 'auto'}, 'auto')):
            with self.subTest(mode=mode):
                self._refused_trial(router, mode)

    def _refused_trial(self, router, mode):
        problem, _ = self.problem()
        out = self.dir / f'refused-{mode}'
        config = {'budget_s': 60, 'inputs': {k: {} for k in ('board', 'project', 'intent', 'groups')},
                  'search': {'shortlist': 3}, 'router': router}
        values = dict(board=(self.board, self.board.read_bytes()), project=(self.project, b'{}'),
                      intent=(self.board, json.dumps(intent()).encode()),
                      groups=(self.board, json.dumps(groups()).encode()))
        output = {'timing': {'prepare_s': 0}, 'status': 'completed', 'counters': {}, 'seeds': [], 'prng': 'probe',
                  'shortlist': [{'seed': n, 'cost': str(n + 1), 'poses': [n]} for n in range(3)]}
        message = "unpaired differential net '/LOAD_P': expected '/LOAD_N'"
        calls = []

        def routed(directory, *_args, **kwargs):
            calls.append(kwargs.get('diff_pairs'))
            return {'status': 'router_refused', 'argv': ['python3'], 'exit_code': 4,
                    'refusal': {'reason_class': 'input', 'message': message, 'record': 'out/refusals/1.json'},
                    'preflight': {k: None for k in job.ROUTER_IDENTITY}}
        with ExitStack() as stack:
            stack.enter_context(patch.object(job, 'verified_input', side_effect=lambda _entry, label: values[label]))
            stack.enter_context(patch.object(job, 'preflight', return_value={'engine_sha256': 'probe', 'engine': 'unused'}))
            stack.enter_context(patch.object(job, 'capture', return_value=(problem, {})))
            stack.enter_context(patch.object(job, 'solve', return_value=(output, {})))
            stack.enter_context(patch.object(job, 'materialize', side_effect=lambda _b, poses, *_a, **_k: {
                'board': str(self.board), 'board_sha256': f'candidate-{poses[0]}', 'integrity': 'preserved',
                'problems': []}))
            stack.enter_context(patch.object(job, 'check_edge_intent', return_value={'status': 'pass'}))
            stack.enter_context(patch.object(job, 'drc', return_value=({'violations': [], 'unconnected_items': []}, {})))
            stack.enter_context(patch.object(job, 'route', side_effect=routed))
            # Guard only: a loop that went on would reach final checks of a refused route.
            stack.enter_context(patch.object(job, 'final_checks'))
            report = job.run_job(config, out)
        self.assertEqual(calls, [mode], 'one trial, with pair handling only when declared')
        self.assertNotIn('final', report)
        self.assertEqual(report['status'], 'router_refused')
        self.assertEqual([t['status'] for t in report['routing_trials']], ['router_refused'])
        self.assertEqual(report['routing_trials'][0]['router_refusal']['message'], message)
        self.assertEqual(report['router_refusal'], {'reason_class': 'input', 'message': message,
                                                    'record': 'out/refusals/1.json'})
        self.assertEqual(report['reason'], f'kicad-route refused (input): {message}')
        self.assertEqual(json.loads((out / 'job-report.json').read_text())['router_refusal']['message'], message)
        state = json.loads((out / 'checkpoint.json').read_text())
        self.assertEqual(len(state['routing_trials']), 1)

    def test_cancel_and_deadline_between_judge_route_and_final_preserve_incumbent(self):
        problem, _ = self.problem()
        for mode in ('cancel', 'deadline', 'cancel-after-route', 'deadline-after-route', 'integrity-failure'):
            with self.subTest(mode=mode):
                out = self.dir / mode
                config = {'budget_s': 60, 'inputs': {k: {} for k in ('board', 'project', 'intent', 'groups')},
                          'search': {'shortlist': 1}, 'router': {'checkout': 'unused', 'budget_s': 1}}
                values = [(self.board, self.board.read_bytes()), (self.project, b'{}'),
                          (self.board, json.dumps(intent()).encode()), (self.board, json.dumps(groups()).encode())]
                remaining = [60.0]
                drc_calls = 0
                entry = {'index': 0, 'seed': 0, 'board_sha256': 'fixture', 'errors': {}, 'engine_violations': [],
                         'directory': str(self.dir), 'board': str(self.board)}
                def judged(*args, **kwargs):
                    (out / 'incumbent.json').write_text(json.dumps(entry))
                    return {'board': str(self.board), 'board_sha256': 'fixture', 'integrity': 'preserved', 'problems': []}
                def checked_drc(*args, **kwargs):
                    nonlocal drc_calls
                    drc_calls += 1
                    if drc_calls == 2:
                        if mode == 'cancel':
                            (out / 'CANCEL').touch()
                        elif mode == 'deadline':
                            remaining[0] = -1.0
                    return {'violations': [], 'unconnected_items': []}, {}
                output = {'timing': {'prepare_s': 0}, 'status': 'completed', 'counters': {}, 'seeds': [], 'prng': 'probe',
                          'shortlist': [{'seed': 0, 'cost': '1', 'poses': []}]}
                with ExitStack() as stack:
                    stack.enter_context(patch.object(job, 'verified_input', side_effect=lambda _entry, label:
                        dict(zip(('board', 'project', 'intent', 'groups'), values))[label]))
                    stack.enter_context(patch.object(job, 'preflight', return_value={'engine_sha256': 'probe'}))
                    stack.enter_context(patch.object(job, 'capture', return_value=(problem, {})))
                    stack.enter_context(patch.object(job, 'solve', return_value=(output, {})))
                    stack.enter_context(patch.object(job.Clock, 'remaining', side_effect=lambda: remaining[0]))
                    stack.enter_context(patch.object(job, 'materialize', side_effect=judged))
                    stack.enter_context(patch.object(job, 'promote', return_value=False))
                    stack.enter_context(patch.object(job, 'retain_diagnostic', return_value=False))
                    stack.enter_context(patch.object(job, 'drc', side_effect=checked_drc))
                    def routed(*args, **kwargs):
                        if mode.startswith('cancel'):
                            (out / 'CANCEL').touch()
                        elif mode.startswith('deadline'):
                            remaining[0] = -1.0
                        return {'status': 'routed', 'board': str(self.board), 'preflight': {
                            k: None for k in job.ROUTER_IDENTITY}}
                    route = stack.enter_context(patch.object(job, 'route', side_effect=routed))
                    final = stack.enter_context(patch.object(job, 'final_checks', return_value={
                        'integrity': 'violated', 'physical_checks': 'FAILED'}))
                    report = job.run_job(config, out)
                self.assertEqual(route.called, mode.endswith('after-route') or mode == 'integrity-failure')
                self.assertEqual(final.called, mode == 'integrity-failure')
                if route.called:
                    self.assertEqual(route.call_args.kwargs['budget_s'], 60)
                    self.assertEqual(route.call_args.kwargs['timeout_s'], 60)
                expected = 'integrity_failed' if mode == 'integrity-failure' else (
                    'cancelled' if mode.startswith('cancel') else 'deadline')
                self.assertEqual(report['status'], expected)
                self.assertEqual(json.loads((out / 'incumbent.json').read_text()), entry)
                self.assertEqual(json.loads((out / 'job-report.json').read_text())['status'], report['status'])

    def test_a_search_cut_short_by_its_time_still_yields_the_best_placement(self):
        # AGENTEE-637: the search's own deadline is not the job's. Candidates found so far are
        # checked and routed, and a job stopped later still names the placement it checked.
        problem, _ = self.problem()
        entry = {'index': 0, 'seed': 0, 'board_sha256': 'fixture', 'errors': {}, 'engine_violations': [],
                 'directory': str(self.dir), 'board': str(self.board), 'engine_cost': '1'}
        for mode in ('limited', 'limited-then-job-deadline', 'nothing-found'):
            with self.subTest(mode=mode):
                out = self.dir / mode
                config = {'budget_s': 60, 'inputs': {k: {} for k in ('board', 'project', 'intent', 'groups')},
                          'search': {'shortlist': 1}, 'router': {'checkout': 'unused'}}
                values = [(self.board, self.board.read_bytes()), (self.project, b'{}'),
                          (self.board, json.dumps(intent()).encode()), (self.board, json.dumps(groups()).encode())]
                remaining = [60.0]
                output = {'timing': {'prepare_s': None}, 'status': 'deadline', 'counters': {}, 'seeds': [], 'prng': None,
                          'shortlist': [] if mode == 'nothing-found' else [{'seed': 0, 'cost': '1', 'poses': []}]}
                def judged(*args, **kwargs):
                    (out / 'incumbent.json').write_text(json.dumps(entry))
                    return {'board': str(self.board), 'board_sha256': 'fixture', 'integrity': 'preserved', 'problems': []}
                def routed(*args, **kwargs):
                    if mode == 'limited-then-job-deadline':
                        remaining[0] = -1.0
                    return {'status': 'routed', 'board': str(self.board), 'preflight': {k: None for k in job.ROUTER_IDENTITY}}
                with ExitStack() as stack:
                    stack.enter_context(patch.object(job, 'verified_input', side_effect=lambda _entry, label:
                        dict(zip(('board', 'project', 'intent', 'groups'), values))[label]))
                    stack.enter_context(patch.object(job, 'preflight', return_value={'engine_sha256': 'probe'}))
                    stack.enter_context(patch.object(job, 'capture', return_value=(problem, {})))
                    solve = stack.enter_context(patch.object(job, 'solve', return_value=(output, {})))
                    stack.enter_context(patch.object(job.Clock, 'remaining', side_effect=lambda: remaining[0]))
                    stack.enter_context(patch.object(job, 'materialize', side_effect=judged))
                    stack.enter_context(patch.object(job, 'promote', return_value=False))
                    stack.enter_context(patch.object(job, 'retain_diagnostic', return_value=False))
                    stack.enter_context(patch.object(job, 'drc', return_value=({'violations': [], 'unconnected_items': []}, {})))
                    route = stack.enter_context(patch.object(job, 'route', side_effect=routed))
                    stack.enter_context(patch.object(job, 'final_checks', return_value={
                        'integrity': 'violated', 'physical_checks': 'FAILED'}))
                    report = job.run_job(config, out)
                # The engine is given a share of the remaining time, never all of it.
                self.assertEqual(solve.call_args.kwargs['deadline_s'], 60 * job.SEARCH_TIME_SHARE)
                self.assertEqual(report['search']['limited_by_budget'], True)
                if mode == 'nothing-found':
                    self.assertEqual((report['status'], report['placement'], route.called), ('deadline', None, False))
                    continue
                self.assertTrue(route.called, 'a search cut short still routes what it found')
                self.assertEqual(report['status'], 'deadline' if mode.endswith('job-deadline') else 'integrity_failed')
                self.assertEqual(report['placement'], {
                    'kind': 'incumbent', 'valid': True, 'board': str(self.board), 'board_sha256': 'fixture',
                    'seed': 0, 'engine_cost': '1', 'deadline_limited': True})
                self.assertEqual(json.loads((out / 'job-report.json').read_text())['placement'], report['placement'])

    def test_search_time_share_is_validated(self):
        for bad in (0, 1, 1.5, True, 'half'):
            with self.subTest(share=bad):
                problem, _ = self.problem()
                config = {'budget_s': 60, 'inputs': {k: {} for k in ('board', 'project', 'intent', 'groups')},
                          'search': {'shortlist': 1, 'time_share': bad}, 'router': {'checkout': 'unused'}}
                values = [(self.board, self.board.read_bytes()), (self.project, b'{}'),
                          (self.board, json.dumps(intent()).encode()), (self.board, json.dumps(groups()).encode())]
                with ExitStack() as stack:
                    stack.enter_context(patch.object(job, 'verified_input', side_effect=lambda _entry, label:
                        dict(zip(('board', 'project', 'intent', 'groups'), values))[label]))
                    stack.enter_context(patch.object(job, 'preflight', return_value={'engine_sha256': 'probe'}))
                    stack.enter_context(patch.object(job, 'capture', return_value=(problem, {})))
                    solve = stack.enter_context(patch.object(job, 'solve'))
                    report = job.run_job(config, self.dir / f'share-{bad}')
                self.assertEqual(report['status'], 'refused')
                self.assertIn('search.time_share', report['reason'])
                self.assertFalse(solve.called)

    def test_stalled_engine_stream_is_bounded(self):
        problem, _ = self.problem()
        child = self.dir / 'stalled'
        child.write_text('#!/usr/bin/python3\nimport time\ntime.sleep(20)\n')
        child.chmod(0o755)
        def alarm(*args):
            raise TimeoutError('stalled stdout exceeded outer test bound')
        old = signal.signal(signal.SIGALRM, alarm)
        signal.setitimer(signal.ITIMER_REAL, 2)
        started = time.monotonic()
        try:
            try:
                output, _ = job.solve(problem, self.search(1, 1), self.dir, deadline_s=0.2, executable=child)
            except TimeoutError as exc:
                self.fail(str(exc))
            self.assertEqual(output['status'], 'deadline')
            self.assertEqual(output['seeds'], [], 'no seed progress was observed')
            self.assertIsNone(output['timing']['prepare_s'])
            self.assertLess(time.monotonic() - started, 1)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old)


class ChildCancellationTests(Fixture):
    def test_watchdog_bounds_child_which_never_reads_stdin(self):
        started = time.monotonic()
        with self.assertRaisesRegex(Exception, 'deadline'):
            job._run_child([sys.executable, '-c', 'import time; time.sleep(20)'],
                           input_bytes=b'x' * 1000000, clock=job.Clock(0.2))
        self.assertLess(time.monotonic() - started, 1)

    def test_cancel_during_route_and_drc_bounds_stalled_children(self):
        from agent_ee import kicad_route_attempt
        from tests.kicad_route_fixture import commit_all, make_checkout
        for phase in ('route', 'drc'):
            with self.subTest(phase=phase):
                cancel = self.dir / 'CANCEL'
                cancel.unlink(missing_ok=True)
                started_file = self.dir / f'{phase}-started'
                # The fixture checkout, a clean commit, with a stalling router committed in it.
                checkout, _ = make_checkout(self.dir / f'router-{phase}', 'complete')
                shim = checkout / 'bin/kicad-route' if phase == 'route' else self.dir / 'kicad-cli'
                shim.parent.mkdir(exist_ok=True)
                shim.write_text('#!/usr/bin/python3\nimport os,time\nfrom pathlib import Path\n'
                                f'Path({str(started_file)!r}).write_text(str(os.getpid()))\n'
                                'time.sleep(20)\n')
                shim.chmod(0o755)
                commit_all(checkout, 'stalling router')
                stopped = threading.Event()
                def cancel_child():
                    limit = time.monotonic() + 2
                    while not stopped.wait(0.01) and time.monotonic() < limit:
                        if started_file.exists():
                            cancel.touch()
                            return
                thread = threading.Thread(target=cancel_child)
                thread.start()
                old = signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(
                    TimeoutError(f'{phase} ignored cancellation')))
                signal.setitimer(signal.ITIMER_REAL, 3)
                started = time.monotonic()
                try:
                    clock = job.Clock(10)
                    with self.assertRaisesRegex(Exception, 'cancelled'):
                        if phase == 'route':
                            optional = {'clock': clock, 'cancel_dir': self.dir} if 'clock' in inspect.signature(job.route).parameters else {}
                            with patch.object(kicad_route_attempt, 'PYTHON', sys.executable):
                                job.route(self.dir, self.dir / 'route', checkout, budget_s=10, timeout_s=10,
                                          home=self.dir / 'home', **optional)
                        else:
                            optional = {'clock': clock, 'cancel_dir': self.dir} if 'clock' in inspect.signature(job.drc).parameters else {}
                            with patch.dict(os.environ, {'PATH': str(self.dir) + os.pathsep + os.environ['PATH']}):
                                job.drc(self.board, self.dir, **optional)
                    self.assertLess(time.monotonic() - started, 1.5)
                    pid = int(started_file.read_text())
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)
                finally:
                    stopped.set()
                    thread.join(timeout=1)
                    signal.setitimer(signal.ITIMER_REAL, 0)
                    signal.signal(signal.SIGALRM, old)

    def test_child_run_is_bounded_by_the_job_clock(self):
        # Review c#1 (e9370fb1): native_run gets what the job clock has left as its timeout, so it
        # never waits past the deadline, and a run its limit stopped is the job deadline, not completion.
        from agent_ee import native_run
        seen = []
        real = native_run.run
        def capture(intent, **kwargs):
            seen.append(intent.timeout)
            return real(intent, **kwargs)
        clock = job.Clock(5)
        with patch.object(job.native_run, 'run', side_effect=capture):
            job._run_child(['/usr/bin/true'], clock=clock)
        self.assertEqual(len(seen), 1)
        self.assertTrue(0 < seen[0] <= 5, seen)
        stopped = native_run.Execution({'execution': 'timeout', 'exit_code': None, 'argv': ['x']}, returncode=-9)
        with patch.object(job.native_run, 'run', return_value=stopped), \
                self.assertRaisesRegex(job.JobStopped, 'deadline'):
            job._run_child(['x'], clock=job.Clock(5))

    def test_delayed_supervision_cannot_outlast_the_job_clock(self):
        # Review c#1 r2 (a83a06e7): 80 ms clock, 60 ms late entry into native_run.run, a child that
        # closes both pipes after 30 ms. The job clock has expired when the run returns: deadline.
        from agent_ee import native_run
        real = native_run.run
        def late(intent, **kwargs):
            time.sleep(0.06)
            return real(intent, **kwargs)
        for _ in range(3):
            with patch.object(job.native_run, 'run', side_effect=late), \
                    self.assertRaisesRegex(job.JobStopped, 'deadline'):
                job._run_child([sys.executable, '-c', 'import time; time.sleep(0.03)'], clock=job.Clock(0.08))

    def test_on_line_stream_is_not_kept(self):
        # Review c#2 (e9370fb1): stdout consumed by on_line is streamed, never retained (as the old loop).
        import tracemalloc
        code = 'import sys\nfor i in range(8192): sys.stdout.write("x" * 1023 + "\\n")'
        lines = []
        tracemalloc.start()
        try:
            tracemalloc.reset_peak()
            done = job._run_child([sys.executable, '-c', code], clock=job.Clock(30),
                                  on_line=lambda line: lines.append(len(line)))
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual((len(lines), set(lines), done.stdout), (8192, {1023}, b''))
        self.assertLess(peak, 2 * 1024 * 1024, '8 MiB of on_line output must not be kept')

    def test_missing_program_is_an_environment_failure_with_its_launch_error(self):
        # Defect #30 adds recovery ownership; errno and filename still survive in the cause.
        with self.assertRaises(job.EnvironmentFailure) as caught:
            job._run_child([str(self.dir / 'no-such-program')], clock=job.Clock(5))
        self.assertEqual(caught.exception.stage, 'native-tool')
        self.assertEqual((caught.exception.__cause__.errno, caught.exception.__cause__.filename),
                         (2, str(self.dir / 'no-such-program')))

    def test_watchdog_kills_descendants_even_after_leader_closes_streams(self):
        marker = self.dir / 'descendant'
        script = ('import subprocess,sys\n'
                  'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(20)"])\n'
                  f'open({str(marker)!r},"w").write(str(p.pid))\n')
        with self.assertRaisesRegex(Exception, 'deadline'):
            job._run_child([sys.executable, '-c', script], clock=job.Clock(0.3))
        pid = int(marker.read_text())
        limit = time.monotonic() + 1
        while time.monotonic() < limit:
            if process_finished(pid):
                break
            time.sleep(0.01)
        else:
            self.fail('owned descendant remains running after deadline')


class RequiredFixtureTests(unittest.TestCase):
    def test_portable_capture_requires_intact_sources_in_both_modes(self):
        name = 'test_unchanged_esp_prog_capture_is_accepted_with_labelled_derived_occupancy'
        for strict in ('1', '0'):
            for fault in ('intact', 'missing', 'changed'):
                with self.subTest(strict=strict, fault=fault), \
                        RetainedTemporaryDirectory(self.id(), prefix='portable-contract-') as tmp, \
                        patch.dict(os.environ, {'AGENT_EE_REQUIRE_TOOLCHAIN': strict}):
                    os.environ.pop('AGENT_EE_ESP_PROG_PREPARED', None)
                    root = Path(tmp)
                    # Preserve committed originals and the selected bytes for each real invocation.
                    shutil.copytree(fixtures.portable.SOURCES, root / 'original')
                    selected = root / 'selected'
                    shutil.copytree(root / 'original', selected)
                    source = selected / 'intent.json'
                    if fault == 'missing':
                        source.unlink()
                    elif fault == 'changed':
                        source.write_bytes(source.read_bytes() + b'\n')
                    result = unittest.TestResult()
                    target = fixtures.EspProgCaptureTests(name)
                    with patch.object(fixtures.portable, 'SOURCES', selected):
                        target.run(result)
                    (root / 'result.json').write_text(json.dumps({
                        'strict': strict, 'fault': fault, 'target': target.id(),
                        'tests_run': result.testsRun,
                        'failures': [detail for _, detail in result.failures],
                        'errors': [detail for _, detail in result.errors],
                        'skipped': [reason for _, reason in result.skipped],
                    }, indent=2) + '\n')
                    self.assertEqual(result.testsRun, 1)
                    self.assertEqual(result.failures, [])
                    self.assertEqual(result.skipped, [])
                    if fault == 'intact':
                        self.assertEqual(result.errors, [])
                    else:
                        self.assertEqual(len(result.errors), 1)
                        diagnostic = result.errors[0][1]
                        self.assertIn('intent.json', diagnostic)
                        self.assertIn('FileNotFoundError' if fault == 'missing' else
                                      'ValueError: authored fixture source changed: intent.json', diagnostic)
