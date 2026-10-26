"""Campaign routing policy fixtures and a real pinned KRT attempt on the placed RC.

Only subscription reviewers are scripted. The writer, sandbox, receipts, router
fixture and native routing run through their ordinary coordinator paths.
"""
import contextlib
import copy
import io
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee import campaign, routing_adapter as routing, run_record, stage_receipts
from agent_ee.run_record import policy_files, runtime_files
from agent_ee.storage import digest, identity, read_json, write_json
from tests.kicad_route_fixture import make_checkout, make_layout
from tests import test_kicad_route_native as native_fixture
from tests import test_stage_receipts as campaign_fixture
from tests.toolchain import require_kicad_route, require_toolchain


class RoutingCampaignTests(unittest.TestCase):
    def setUp(self):
        require_toolchain()
        self.fixture = campaign_fixture.CampaignStageReceiptTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.host = dict(self.fixture.host)
        self.source = self.root / 'source'
        self.scope = read_json(self.root / 'scope.json')
        self.scope.update(board='design/rc.kicad_pcb', schematic='design/rc.kicad_sch',
                          mutable_inputs=['design/rc.kicad_pcb'])
        (self.source / 'rc.kicad_pcb').write_text('(kicad_pcb (version 20260206))\n')
        (self.source / 'rc.kicad_sch').write_text('(kicad_sch (version 20260306))\n')
        (self.source / 'rc.kicad_pro').write_text('{}\n')
        shutil.copyfile(native_fixture.ROOT / 'fixtures/stages/schematic.kicad_sch',
                        self.source / 'rc.kicad_sch')
        (self.source / 'solver.py').write_text(
            'from pathlib import Path\nimport json\n'
            'board = Path("/candidate/design/rc.kicad_pcb").read_text()\n'
            'Path("raw.json").write_text(json.dumps({"value": 5 if "routed by fixture" in board else 15}))\n')
        self.config = {**self.fixture.config, 'max_seconds': 300, 'max_iterations': 2}
        present = copy.deepcopy(self.scope['checks'][0])
        present.update(id='present', stage='schematic')
        present['params']['limits']['output']['max'] = 20
        self.scope['checks'][0]['stage'] = 'routing'
        self.scope['checks'].insert(0, present)
        self.scope['baseline']['requirements'][0]['checks'].append('present')
        self.scope['stages'] = {'schema': 'agent-ee.stages/1', 'order': ['schematic', 'routing'],
            'subjects': {'schematic': {'path': self.scope['schematic'], 'kind': 'symbols', 'references': ['R1']}},
            'finding_retry_limit': 3}

    def spec(self, scenario='complete', *, separate=False):
        checkout, code = make_checkout(self.root / 'checkout', scenario)
        state = None
        if separate:
            state = self.root / 'state'
            state.mkdir()
            for rel in ('.venv-krt', 'vendor'):
                shutil.move(checkout / rel, state / rel)
        # The production adapter, qualified against the fixture checkout's pin file.
        self.enterContext(routing.qualified_layout(make_layout(self.root / 'pin', code)))
        return {'schema': 'agent-ee.routing-adapter/1', 'project': 'design', 'board': 'rc.kicad_pcb',
                'schematic': 'rc.kicad_sch', 'name': 'rc', 'label': 'campaign', 'checkout': str(checkout),
                'state': str(state) if state else None, 'budget': 5, 'timeout': 30}

    def run_campaign(self, spec):
        write_json(self.root / 'scope.json', self.scope)
        write_json(self.root / 'campaign.json', {**self.config, 'routing_adapter': spec})
        with contextlib.redirect_stdout(io.StringIO()):
            status = campaign.run_campaign(self.root / 'campaign.json', self.root / 'campaign', host=self.host)
        state = campaign.verify_campaign(self.root / 'campaign', host=self.host)
        work = self.root / 'campaign/implementations/1'
        return status, state, work

    def test_exits_zero_through_four_are_recorded_in_campaign_with_correct_applicability(self):
        for scenario, code, receipt_status, applicability, classification in (
                ('complete', 0, 'completed', 'applicable', 'complete'),
                ('incomplete', 2, 'partial', 'applicable', 'incomplete'),
                ('backend_failure', 1, 'failed', 'not_applicable', 'backend_failure'),
                ('validation_failed', 3, 'failed', 'not_applicable', 'validation_failed'),
                ('refused', 4, 'failed', 'not_applicable', 'refused')):
            with self.subTest(exit=code):
                # One independent frozen campaign per exit; no reuse of OUT or state.
                child = RoutingCampaignTests()
                child.setUp()
                try:
                    spec = child.spec(scenario, separate=True)
                    status, state, work = child.run_campaign(spec)
                    index = read_json(work / 'stage-receipts.json')
                    self.assertEqual(index['receipts'][0]['applicability'], applicability)
                    receipt_dir = work / 'receipts/1-kicad-route-builtin'
                    receipt = stage_receipts.validate_receipt(receipt_dir)
                    self.assertEqual((receipt['exit_code'], receipt['status']), (code, receipt_status))
                    summary = routing.verify_campaign_receipt(receipt_dir)
                    self.assertEqual(summary['classification'], classification)
                    self.assertIsNotNone(receipt['artifacts']['routing/adapter-report.json'])
                    if code in (0, 2):
                        self.assertEqual((status, state['state']), (0, 'criteria_satisfied'))
                        self.assertEqual(len(state['history']), 2)
                        self.assertEqual(receipt['outputs']['design/rc.kicad_pcb'],
                                         index['delivered']['design/rc.kicad_pcb'])
                        self.assertEqual(receipt['tool']['backend']['value']['name'], 'builtin')
                        for artifact in ('result.json', 'attempt.json', 'RESULT.md', 'validate/preservation.json'):
                            self.assertIsNotNone(receipt['artifacts']['routing/attempt/' + artifact])
                        # AGENTEE-243: the routed candidate's review binds the verified adapter evidence.
                        rf = read_json(Path(state['history'][1]['run']) / 'work/rf-routing/evidence.json')
                        self.assertEqual(rf['status'], 'verified', rf['reason'])
                        self.assertEqual(rf['candidate_sha256'], receipt['outputs']['design/rc.kicad_pcb'])
                        self.assertEqual(rf['adapter_report']['sha256'],
                                         receipt['artifacts']['routing/adapter-report.json'])
                        self.assertIsNone(rf['layer_policy'])
                        before = read_json(Path(state['history'][0]['run']) / 'work/rf-routing/evidence.json')
                        self.assertEqual(before['status'], 'missing_evidence')
                    else:
                        self.assertEqual((status, state['state']), (1, 'incomplete'))
                        self.assertEqual(len(state['history']), 1)
                        self.assertFalse((work / 'proposal.scope.json').exists())
                        self.assertEqual(receipt['outputs'], {'design/rc.kicad_pcb': None})
                        self.assertEqual(index['delivered'], receipt['inputs'])
                        self.assertNotIn('EXIT3-SENTINEL',
                            (work / 'review-work/implementation/design/design/rc.kicad_pcb').read_text())
                finally:
                    child.doCleanups()

    def test_exact_command_mounts_timeout_and_adoption_policy_are_pinned(self):
        declaration = self.spec(separate=True)
        spec = routing.campaign_spec(declaration, {'scope': self.scope}, self.scope['mutable_inputs'])
        mutations = {
            'python': lambda s: s['argv'].__setitem__(0, '/usr/bin/false'),
            'isolation': lambda s: s['argv'].__setitem__(1, '-B'),
            'script': lambda s: s['argv'].__setitem__(3, '/opt/other.py'),
            'extra mount': lambda s: s['mounts'].append({'source': str(self.root), 'at': '/extra'}),
            'missing state': lambda s: s['mounts'].pop(),
            'timeout': lambda s: s.update(timeout_s=600),
            'exit 3': lambda s: s.update(adoptable_exits=[0, 2, 3]),
            'writer output': lambda s: s.update(writes={'design/rc.kicad_pcb': 'design/design/rc.kicad_pcb'}),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                altered = copy.deepcopy(spec)
                mutate(altered)
                with self.assertRaises(ValueError):
                    routing.validate_campaign_step(altered)
                write_json(self.root / 'scope.json', self.scope)
                write_json(self.root / 'campaign.json', {**self.config, 'routing_adapter':
                    {**declaration, 'argv': altered['argv']}})
                with self.assertRaises(ValueError):
                    campaign.run_campaign(self.root / 'campaign.json', self.root / 'campaign', host=self.host)
                self.assertFalse((self.root / 'campaign').exists())

    def test_pair_opt_in_survives_binding_receipt_and_campaign_replay(self):
        declaration = {**self.spec(), 'diff_pairs': 'auto'}
        normalized = routing.validate_declaration(declaration)
        self.assertEqual(normalized['diff_pairs'], 'auto')
        spec = routing.campaign_spec(normalized, {'scope': self.scope}, self.scope['mutable_inputs'])
        self.assertEqual(spec['argv'][-2:], ['--diff-pairs', 'auto'])
        self.assertEqual(routing.validate_campaign_step(spec), spec)
        status, state, work = self.run_campaign(declaration)
        self.assertEqual((status, state['state']), (0, 'criteria_satisfied'))
        receipts = list(work.rglob('routing/attempt/result.json'))
        self.assertEqual(len(receipts), 1)
        result = read_json(receipts[0])
        self.assertEqual(result['schema'], 'kicad-route/route/2')
        self.assertEqual(result['pairs'], [])
        binding = state['routing_binding']
        self.assertEqual(binding['spec']['argv'][-2:], ['--diff-pairs', 'auto'])
        with self.assertRaisesRegex(ValueError, 'differs from its declaration'):
            routing.validate_binding(binding, {k: v for k, v in declaration.items() if k != 'diff_pairs'},
                                     {'scope': self.scope}, self.scope['mutable_inputs'], policy_files())

    def test_routing_step_dispatch_waits_for_the_routing_stage(self):
        spec = self.spec()
        manifest = {'scope': {'stages': {'order': ['placement', 'routing']}}}
        config = {'routing_adapter': spec}
        self.assertEqual(campaign.step_plan(config, manifest, {'earliest_failing_stage': 'placement'},
                                            campaign.STEP_PLAN), [])
        plan = campaign.step_plan(config, manifest, {'earliest_failing_stage': 'routing'}, campaign.STEP_PLAN)
        self.assertEqual(plan, [{'kind': 'routing', 'receipt_index': 1}])
        self.assertEqual(campaign.step_plan(config, manifest, {'earliest_failing_stage': None},
                                            campaign.STEP_PLAN), [])
        with self.assertRaisesRegex(ValueError, 'requires a campaign step plan'):
            campaign.step_plan(config, manifest, {'earliest_failing_stage': 'routing'}, None)

    def test_scope_identity_extra_options_and_duplicate_routing_steps_are_refused(self):
        declaration = self.spec()
        for edit in ({'board': 'other.kicad_pcb'}, {'layout': {}}, {'backend_env': {'X': '1'}}):
            with self.subTest(edit), self.assertRaises(ValueError):
                routing.campaign_spec({**declaration, **edit}, {'scope': self.scope}, self.scope['mutable_inputs'])
        write_json(self.root / 'scope.json', self.scope)
        write_json(self.root / 'campaign.json', {**self.config, 'routing_adapter': declaration,
            'implementation_steps': [routing.campaign_spec(declaration, {'scope': self.scope},
                                                          self.scope['mutable_inputs'])]})
        with self.assertRaisesRegex(ValueError, 'cannot be combined'):
            campaign.run_campaign(self.root / 'campaign.json', self.root / 'campaign', host=self.host)

    def test_routing_writer_cannot_change_the_placed_source(self):
        declaration = self.spec()
        agent = self.fixture.agent

        def editing_writer(run, label, *args, **kwargs):
            response = agent(run, label, *args, **kwargs)
            if label == 'implementation':
                (Path(run) / 'review-work/implementation/design/design/rc.kicad_pcb').write_text('writer edit')
            return response

        with patch('agent_ee.reviewers.dispatch', side_effect=editing_writer):
            status, state, work = self.run_campaign(declaration)
        self.assertEqual((status, state['state']), (1, 'incomplete'))
        self.assertIn('routing writer changed', state['error'])
        self.assertFalse((work / 'receipts').exists())
        self.assertFalse((work / 'proposal.scope.json').exists())

    def test_backend_state_drift_is_refused_before_launch_and_replay_needs_no_host_checkout(self):
        declaration = self.spec(separate=True)
        agent = self.fixture.agent

        def drift(run, label, *args, **kwargs):
            response = agent(run, label, *args, **kwargs)
            if label == 'implementation':
                (Path(declaration['state']) / '.venv-krt/py_router/route.py').write_text('changed state')
            return response

        with patch('agent_ee.reviewers.dispatch', side_effect=drift):
            status, state, work = self.run_campaign(declaration)
        self.assertEqual((status, state['state']), (1, 'incomplete'))
        self.assertIn('changed since campaign binding', state['error'])
        self.assertFalse((work / 'receipts').exists())
        shutil.rmtree(declaration['checkout'])
        shutil.rmtree(declaration['state'])
        self.assertEqual(campaign.verify_campaign(self.root / 'campaign', host=self.host)['state'], 'incomplete')

    def test_rehashed_tool_identity_is_refused_against_the_frozen_binding(self):
        _, state, work = self.run_campaign(self.spec(separate=True))
        receipt_dir = work / 'receipts/1-kicad-route-builtin'
        receipt = read_json(receipt_dir / 'receipt.json')
        receipt['tool']['mounts'][-1]['sha256'] = '0' * 64
        receipt['tool_after']['mounts'][-1]['sha256'] = '0' * 64
        receipt['identity'] = identity({k: v for k, v in receipt.items() if k != 'identity'})
        write_json(receipt_dir / 'receipt.json', receipt)
        stage_receipts.validate_receipt(receipt_dir)
        with self.assertRaisesRegex(ValueError, 'frozen campaign tool binding'):
            routing.verify_campaign_receipt(receipt_dir, binding=state['routing_binding'])

    def test_rehashed_writable_state_mount_is_refused_on_replay(self):
        self.run_campaign(self.spec(separate=True))
        receipt_dir = self.root / 'campaign/implementations/1/receipts/1-kicad-route-builtin'
        receipt = read_json(receipt_dir / 'receipt.json')
        execution = receipt['execution']
        argv = execution['sandbox_argv']
        at = argv.index('/opt/kicad-route-state')
        self.assertEqual(argv[at - 2], '--ro-bind-fd')
        argv[at - 2] = '--bind'
        write_json(receipt_dir / 'run/execution.json', execution)
        receipt['sandbox_profile_sha256'] = identity(stage_receipts.sandbox_profile(argv))
        receipt['identity'] = identity({k: v for k, v in receipt.items() if k != 'identity'})
        write_json(receipt_dir / 'receipt.json', receipt)
        # The generic receipt checks are deliberately not the routing mount authority.
        stage_receipts.validate_receipt(receipt_dir)
        with self.assertRaisesRegex(ValueError, 'declared read-only mounts'):
            routing.verify_campaign_receipt(receipt_dir)

    def test_fd_mount_replay_rejects_tampering_and_preserves_legacy_receipts(self):
        _, _, work = self.run_campaign(self.spec(separate=True))
        receipt_dir = work / 'receipts/1-kicad-route-builtin'
        original = read_json(receipt_dir / 'receipt.json')
        summary = routing.verify_campaign_receipt(receipt_dir)

        def retain(value):
            write_json(receipt_dir / 'run/execution.json', value['execution'])
            value['sandbox_profile_sha256'] = identity(
                stage_receipts.sandbox_profile(value['execution']['sandbox_argv']))
            value['identity'] = identity({k: v for k, v in value.items() if k != 'identity'})
            write_json(receipt_dir / 'receipt.json', value)
            stage_receipts.validate_receipt(receipt_dir)

        for case in ('invalid-fd', 'stdio-fd', 'duplicate-fd', 'destination', 'mixed-path'):
            with self.subTest(case=case):
                value = copy.deepcopy(original)
                argv = value['execution']['sandbox_argv']
                first = argv.index('--ro-bind-fd')
                second = argv.index('--ro-bind-fd', first + 1)
                if case == 'invalid-fd':
                    argv[first + 1] = 'not-a-descriptor'
                elif case == 'stdio-fd':
                    argv[first + 1] = '2'
                elif case == 'duplicate-fd':
                    argv[second + 1] = argv[first + 1]
                elif case == 'destination':
                    argv[first + 2] = '/foreign-tool'
                else:
                    argv[first:first + 2] = ['--ro-bind', original['spec']['mounts'][0]['source']]
                retain(value)
                with self.assertRaisesRegex(ValueError, 'campaign routing sandbox'):
                    routing.verify_campaign_receipt(receipt_dir)
        # Existing retained path-bound receipts remain verifiable without opening
        # their old host sources or borrowing any of today's descriptors.
        legacy = copy.deepcopy(original)
        argv = legacy['execution']['sandbox_argv']
        for mount in legacy['spec']['mounts']:
            index = argv.index('--ro-bind-fd')
            self.assertEqual(argv[index + 2], mount['at'])
            argv[index:index + 2] = ['--ro-bind', mount['source']]
        argv[argv.index('--') + 1:] = legacy['spec']['argv']
        legacy['execution'].pop('mount_guard')
        retain(legacy)
        self.assertEqual(routing.verify_campaign_receipt(receipt_dir), summary)

    def test_routing_verifier_runs_before_adoption_and_during_campaign_replay(self):
        spec = self.spec()
        original = routing.verify_campaign_receipt
        seen = []

        def observe(receipt_dir, **kwargs):
            receipt_dir = Path(receipt_dir)
            work = receipt_dir.parents[1]
            seen.append((work / 'review-work/implementation/design/design/rc.kicad_pcb').read_text())
            return original(receipt_dir, **kwargs)

        with patch.object(routing, 'verify_campaign_receipt', side_effect=observe):
            self.run_campaign(spec)
        self.assertNotIn('routed by fixture', seen[0])
        self.assertTrue(any('routed by fixture' in board for board in seen[1:]))
        with patch.object(routing, 'verify_campaign_receipt', side_effect=ValueError('routing probe refused')):
            with self.assertRaisesRegex(ValueError, 'routing probe refused'):
                campaign.verify_campaign(self.root / 'campaign', host=self.host)

    def test_failed_routing_verification_stops_before_any_proposal(self):
        spec = self.spec()
        with patch.object(routing, 'verify_campaign_receipt', side_effect=ValueError('routing probe refused')):
            write_json(self.root / 'scope.json', self.scope)
            write_json(self.root / 'campaign.json', {**self.config, 'routing_adapter': spec})
            with contextlib.redirect_stdout(io.StringIO()):
                status = campaign.run_campaign(self.root / 'campaign.json', self.root / 'campaign', host=self.host)
        state = campaign.verify_campaign(self.root / 'campaign', host=self.host)
        self.assertEqual((status, state['state']), (1, 'incomplete'))
        work = self.root / 'campaign/implementations/1'
        self.assertFalse((work / 'proposal.scope.json').exists())
        self.assertNotIn('routed by fixture',
            (work / 'review-work/implementation/design/design/rc.kicad_pcb').read_text())

    def test_crashed_adapter_without_a_report_is_recorded_but_never_adopted(self):
        spec = self.spec()
        # A different adapter script at the production mount: it crashes before any report.
        crashing = self.root / 'crashing-attempt.py'
        crashing.write_text('raise SystemExit(70)\n')
        self.enterContext(patch.object(routing, 'ADAPTER_SOURCE', crashing))
        policies = policy_files()
        policies['kicad_route_attempt.py'] = digest(routing.ADAPTER_SOURCE)
        self.host['policy_files'] = policies
        self.enterContext(patch.object(run_record, 'LOADED_POLICY_FILES', policies))
        status, state, work = self.run_campaign(spec)
        self.assertEqual((status, state['state']), (1, 'incomplete'))
        receipt_dir = work / 'receipts/1-kicad-route-builtin'
        receipt = stage_receipts.validate_receipt(receipt_dir)
        self.assertEqual((receipt['status'], receipt['exit_code']), ('failed', 70))
        self.assertIsNone(receipt['artifacts']['routing/adapter-report.json'])
        self.assertIsNone(routing.verify_campaign_receipt(receipt_dir))
        self.assertFalse((work / 'proposal.scope.json').exists())


class RoutingPredecessorTests(unittest.TestCase):
    """Plane preparation is a real sandbox producer; router receipts remain owned and verified."""
    def setUp(self):
        self.lane = RoutingCampaignTests()
        self.lane.setUp()
        self.addCleanup(self.lane.doCleanups)
        self.root = self.lane.root
        self.host = self.lane.host
        script = self.root / 'add-planes.py'
        script.write_text('from pathlib import Path\n'
                          'p=Path("/candidate/design/rc.kicad_pcb")\n'
                          'Path("/work/board").write_bytes(p.read_bytes()+b"; plane fixture\\n")\n')
        self.step = dict(schema='agent-ee.stage-step/1', stage='routing', step='add-planes',
            argv=['/usr/bin/python3', '-B', '/tool/add-planes.py'],
            mounts=[dict(source=str(script),at='/tool/add-planes.py')],
            writes={'design/rc.kicad_pcb':'board'}, adoptable_exits=[0],timeout_s=30)
        self.lane.config['implementation_steps'] = [self.step]

    def raw_run(self, declaration):
        write_json(self.root / 'scope.json', self.lane.scope)
        write_json(self.root / 'campaign.json', {**self.lane.config,'routing_adapter':declaration})
        with contextlib.redirect_stdout(io.StringIO()):
            return campaign.run_campaign(self.root / 'campaign.json',self.root / 'campaign', host=self.host)

    def test_plane_receipt_then_owned_router_forms_exact_replayable_chain(self):
        status,state,work=self.lane.run_campaign(self.lane.spec())
        self.assertEqual((status,state['state']),(0,'criteria_satisfied'))
        first=stage_receipts.validate_receipt(work/'receipts/1-add-planes')
        second=stage_receipts.validate_receipt(work/'receipts/2-kicad-route-builtin')
        source=read_json(Path(state['history'][0]['run'])/'candidate.json')
        self.assertEqual(first['inputs'],source['files'])
        self.assertEqual(second['inputs'],stage_receipts.after_tree(first))
        self.assertEqual(second['spec']['adoptable_exits'],[0,2])
        self.assertEqual(routing.verify_campaign_receipt(work/'receipts/2-kicad-route-builtin')['classification'],'complete')
        self.assertIn('plane fixture',(work/'receipts/2-kicad-route-builtin/inputs/design/rc.kicad_pcb').read_text())

    def test_owned_routing_composes_with_empty_writer_allowlist(self):
        self.lane.config['writer_editable'] = []
        status,state,work = self.lane.run_campaign(self.lane.spec())
        self.assertEqual((status,state['state']),(0,'criteria_satisfied'))
        self.assertTrue((work/'receipts/1-add-planes/receipt.json').is_file())
        self.assertTrue((work/'receipts/2-kicad-route-builtin/receipt.json').is_file())

    def test_failed_plane_producer_never_launches_router(self):
        self.step['argv']=['/usr/bin/false']
        status,state,work=self.lane.run_campaign(self.lane.spec())
        self.assertEqual((status,state['state']),(1,'incomplete'))
        self.assertTrue((work/'receipts/1-add-planes/receipt.json').is_file())
        self.assertFalse((work/'receipts/2-kicad-route-builtin').exists())
        self.assertFalse((work/'proposal.scope.json').exists())

    def test_writer_edit_outside_plane_outputs_refused_before_first_producer(self):
        (self.lane.source/'intent.json').write_text('{}\n')
        self.lane.scope['mutable_inputs'].append('design/intent.json')
        declaration=self.lane.spec()
        agent=self.lane.fixture.agent
        def editing_writer(run,label,*args,**kwargs):
            result=agent(run,label,*args,**kwargs)
            if label=='implementation':
                (Path(run)/'review-work/implementation/design/design/intent.json').write_text('{"changed":true}')
            return result
        with patch('agent_ee.reviewers.dispatch',side_effect=editing_writer):
            status,state,work=self.lane.run_campaign(declaration)
        self.assertEqual((status,state['state']),(1,'incomplete'))
        self.assertIn('routing writer changed',state['error'])
        self.assertFalse((work/'receipts').exists())
        self.assertFalse((work/'proposal.scope.json').exists())

    def test_failed_plane_prefix_requires_final_incomplete_state_on_replay(self):
        self.step['argv']=['/usr/bin/false']
        status,state,work=self.lane.run_campaign(self.lane.spec())
        index=read_json(work/'stage-receipts.json')
        source=read_json(Path(state['history'][0]['run'])/'candidate.json')
        specs=[stage_receipts.validate_step(self.step),state['routing_binding']['spec']]
        campaign.verify_routing_chain(work,index,source['files'],specs,terminal_incomplete=True)
        with self.assertRaisesRegex(ValueError,'final incomplete'):
            campaign.verify_routing_chain(work,index,source['files'],specs,terminal_incomplete=False)
        # Even a state still called incomplete cannot retain a proposal after failed planes.
        write_json(work/'proposal.scope.json',{})
        with self.assertRaisesRegex(ValueError,'final incomplete'):
            campaign.verify_routing_chain(work,index,source['files'],specs,terminal_incomplete=True)

    def test_unreceipted_between_step_edit_stops_before_router(self):
        declaration=self.lane.spec()
        original=stage_receipts.adopt
        def altered(path,receipt,design):
            original(path,receipt,design)
            if receipt['spec']['step']=='add-planes':
                with (Path(design)/'design/rc.kicad_pcb').open('a') as f:
                    f.write('; unreceipted edit\n')
        with patch.object(stage_receipts,'adopt',side_effect=altered):
            status=self.raw_run(declaration)
        state=campaign.verify_campaign(self.root/'campaign', host=self.host)
        self.assertEqual((status,state['state']),(1,'incomplete'))
        self.assertIn('preceding adopted receipt',state['error'])
        self.assertFalse((self.root/'campaign/implementations/1/receipts/2-kicad-route-builtin').exists())

    def test_individually_valid_but_forged_receipt_chain_is_refused_on_replay(self):
        declaration=self.lane.spec()
        original=stage_receipts.run_step
        def tampered(receipt_dir,design,spec,binding,**kw):
            if spec['step']=='kicad-route-builtin':
                with (Path(design)/'design/rc.kicad_pcb').open('a') as f:
                    f.write('; forged intermediate input\n')
            return original(receipt_dir,design,spec,binding,**kw)
        with patch.object(stage_receipts,'run_step',side_effect=tampered):
            self.assertEqual(self.raw_run(declaration),1)
        work=self.root/'campaign/implementations/1'
        for name in ('1-add-planes','2-kicad-route-builtin'):
            stage_receipts.validate_receipt(work/'receipts'/name)
        with self.assertRaisesRegex(ValueError,'routing receipt chain'):
            campaign.verify_campaign(self.root/'campaign', host=self.host)
        self.assertFalse((work/'proposal.scope.json').exists())

    def test_predecessor_does_not_run_before_routing_stage(self):
        config={**self.lane.config,'routing_adapter':self.lane.spec()}
        plan=campaign.step_plan(config,{'scope':self.lane.scope},{'earliest_failing_stage':'schematic'},campaign.STEP_PLAN)
        self.assertEqual(plan,[])
        plan=campaign.step_plan(config,{'scope':self.lane.scope},{'earliest_failing_stage':'routing'},campaign.STEP_PLAN)
        self.assertEqual([s['kind'] for s in plan],['step','routing'])
        self.assertEqual([s['receipt_index'] for s in plan],[1,2])
        for change in ({'step':'other'},{'adoptable_exits':[0,2]},
                       {'writes':{'design/rc.kicad_sch':'out'}}):
            with self.subTest(change=change),self.assertRaisesRegex(ValueError,'except one add-planes'):
                campaign.validate_routing_predecessors([{**self.step,**change}],self.lane.scope)


class NativeRoutingCampaignTests(unittest.TestCase):
    def test_pinned_krt_runs_inside_campaign_and_state_is_unchanged(self):
        checkout = require_kicad_route()
        fixture = RoutingCampaignTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        # Preserve actual native admission identities; only reviewer transport is scripted.
        runtime = runtime_files()
        for name in ('codex', 'codex_tool_host'):
            runtime[name] = {'path': '/usr/bin/true', 'sha256': digest('/usr/bin/true')}
        fixture.host['runtime_files'] = runtime
        native = native_fixture.NativeKicadRouteTest()
        native.checkout = checkout
        native.setUp()
        self.addCleanup(native.doCleanups)
        placed = native.placed_rc()
        scope = read_json(native_fixture.ROOT / 'fixtures/rc/control.scope.json')
        scope.update(schema='agent-ee.scope/2', mutable_inputs=['project/rc_filter.kicad_pcb'])
        scope['inputs'][0]['path'] = str(placed / 'project')
        scope['inputs'][1]['path'] = str(placed / 'requirements')
        scope['checks'] = [c for c in scope['checks'] if c['id'] in ('drc', 'geometry')]
        scope['baseline'] = {'requirements': [
            {'id': 'CONNECTED', 'statement': 'DRC with parity and zero unconnected items',
             'source': {'path': 'requirements/fixture-requirements.md', 'locator': 'Geometry'}, 'checks': ['drc']},
            {'id': 'WIDTH', 'statement': 'Every track and arc is at least 0.2 mm wide',
             'source': {'path': 'requirements/fixture-requirements.md', 'locator': 'Geometry'}, 'checks': ['geometry']}],
            'targets': ['requirements/fixture-requirements.md'],
            'reference_architectures': ['requirements/fixture-requirements.md']}
        scope['checks'][1]['params'] = {'criterion': 'copper-width/1',
            'covered_primitives': ['PCB_TRACK', 'PCB_ARC'], 'min_track_width_mm': 0.2, 'exclude_nets': []}
        # Missing copper is a staged defect, rather than unknown width coverage on an empty subject.
        scope['checks'][0]['stage'] = 'routing'
        scope['checks'][1]['stage'] = 'routing'
        scope['stages'] = {'schema': 'agent-ee.stages/1', 'order': ['routing'],
            'subjects': {'routing': {'path': scope['board'], 'kind': 'tracks', 'minimum': 1}},
            'finding_retry_limit': 3}
        fixture.scope = scope
        fixture.config['max_seconds'] = 1200
        before = stage_receipts.tree_identity(checkout)
        status, state, work = fixture.run_campaign({'schema': 'agent-ee.routing-adapter/1',
            'project': 'project', 'board': 'rc_filter.kicad_pcb', 'schematic': 'rc_filter.kicad_sch',
            'name': 'rc_filter', 'label': 'krt-native', 'checkout': str(checkout), 'budget': 120})
        self.assertEqual(stage_receipts.tree_identity(checkout), before)
        self.assertEqual(len(state['history']), 2, state)
        receipt_dir = work / 'receipts/1-kicad-route-builtin'
        receipt = stage_receipts.validate_receipt(receipt_dir)
        self.assertEqual((receipt['exit_code'], receipt['status']), (0, 'completed'))
        summary = routing.verify_campaign_receipt(receipt_dir)
        native.assert_native_attempt(receipt_dir / 'run', summary)
        report = read_json(receipt_dir / 'run/routing/adapter-report.json')
        self.assertTrue(all(p['refused'] and p['errno'] == 'EROFS' for p in report['state_write_probe']))
        self.assertEqual(report['identity_before'], report['identity_after'])
        self.assertEqual(receipt['tool']['mounts'], receipt['tool_after']['mounts'])
        final = read_json(Path(state['history'][1]['run']) / 'result.json')
        self.assertEqual([c['execution'] for c in final['checks']], ['ok', 'ok'])
        self.assertEqual(final['checks'][0]['details']['unconnected_items'], 0)
        self.assertEqual(final['checks'][0]['details']['schematic_parity'], 0)
        self.assertEqual(final['checks'][1]['outcome'], 'pass')
        # Stock-library warnings can still prevent native DRC acceptance; the receipt cannot override it.
        if final['checks'][0]['outcome'] == 'pass':
            self.assertEqual((status, state['state']), (0, 'criteria_satisfied'))
        else:
            self.assertNotEqual(state['state'], 'criteria_satisfied')


if __name__ == '__main__':
    unittest.main()
