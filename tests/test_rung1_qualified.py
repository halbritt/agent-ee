"""Rung 1 on the owned pipeline: generation inputs, staged scope, steps and campaign config.

No test here runs the campaign, a review or any inference. The native class builds the
scaffold (reference netlist export and empty-file loads in the sandbox) and validates the
scope with the real loaders.
"""
import json
import os
import shutil
from pathlib import Path
import tempfile
import unittest
import uuid

from agent_ee import generate as g
from agent_ee import rung1_campaign as rc
from agent_ee import rung1_qualified as q
from agent_ee.storage import canonical, digest
try:
    from toolchain import require_kicad_route, require_toolchain
    from test_generate import HEADER, ROWS, git_catalog
except ImportError:  # run as tests.test_rung1_qualified from the repository root
    from tests.toolchain import require_kicad_route, require_toolchain
    from tests.test_generate import HEADER, ROWS, git_catalog

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / 'fixtures/rung1/spec.md'
PARAMS = {'board': {'width_mm': 50, 'height_mm': 30, 'copper_layers': 2},
          'header': {'ref': 'J1', 'pins': 3}, 'nets': {'ground': 'GND'}}


def require_stock():
    paths = [g.STOCK / 'symbols' / f'{name}.kicad_sym' for name in g.SYMBOL_LIBRARIES]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing or not (g.STOCK / 'footprints' / 'Resistor_SMD.pretty').is_dir():
        reason = 'toolchain prerequisite: KiCad stock libraries missing: ' + ', '.join(missing or ['footprints'])
        if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1':
            raise AssertionError('AGENT_EE_REQUIRE_TOOLCHAIN=1: ' + reason)
        raise unittest.SkipTest(reason)


class EnvelopeTest(unittest.TestCase):
    def test_owned_envelope_derives_outline_and_fixed_connector(self):
        from agent_ee.board_envelope import derive
        record = q.envelope_record(PARAMS)
        artifacts = derive(canonical(record) + b'\n', source_path='generation/envelope/envelope.json')
        validation = json.loads(artifacts['envelope-validation.json'])
        self.assertEqual(q.outline_rectangle(validation), {'x': 100.0, 'y': 100.0, 'w': 50.0, 'h': 30.0})
        fixed = validation['fixed_footprints'][0]
        self.assertEqual((fixed['reference'], fixed['edge'], fixed['anchor_mm']), ('J1', 'left', ['102.54', '112.46']))
        ids = [r['id'] for r in json.loads(artifacts['envelope-requirements.json'])['requirements']]
        self.assertIn('R-envelope-rung1-rc-fixed-J1', ids)
        self.assertIn('R-envelope-rung1-rc-outline', ids)


class IntentAndLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_stock()
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-rung1-qualified-')
        root = Path(cls.tmp.name)
        catalog = git_catalog(root / 'catalog', HEADER + '\n'.join(ROWS) + '\n')
        cls.rundir = root / 'run'
        cls.rundir.mkdir()
        cls.prep = q.prepare(SPEC, cls.rundir, catalog_checkout=catalog)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_intent_is_the_rung1_ir_with_local_labels_and_a_native_ground_symbol(self):
        from agent_ee.connectivity_ir import resolve_intent
        intent = q.qualified_intent(self.prep)
        resolved = resolve_intent(intent, q.intent_captures(intent))
        self.assertEqual((resolved['execution'], resolved['outcome'], resolved['coverage']), ('ok', 'pass', 'complete'))
        self.assertEqual(q.ground_net(self.prep['params']), 'GND')
        nets = {n['name']: sorted((t['component'], t['pin']['number']) for t in n['terminals']) for n in intent['nets']}
        self.assertEqual(nets, {'IN': [('J1', '1'), ('R1', '1')], 'OUT': [('C1', '1'), ('J1', '2'), ('R1', '2')],
                                'GND': [('C1', '2'), ('FLG1', '1'), ('J1', '3'), ('pwr_gnd', '1')]})
        components = {c['reference']: (c['lib_id'], c['footprint']) for c in intent['components']}
        self.assertEqual(components['#FLG01'], ('power:PWR_FLAG', ''))
        self.assertEqual(components['#PWR01'], ('power:GND', ''))
        # Stage-2 selections are the footprints of the physical parts.
        self.assertEqual({r: components[r][1] for r in ('R1', 'C1', 'J1')},
                         {r: f['Footprint'] for r, f in self.prep['parts']['parts'].items()})
        self.assertEqual({lib['path'] for lib in intent['libraries']},
                         {f'stock/symbols/{lib}.kicad_sym' for lib in g.SYMBOL_LIBRARIES})
        # The generate intent is not changed in place.
        self.assertIn('/IN', [n['name'] for n in self.prep['intent']['nets']])
        self.assertNotIn('FLG1', [c['id'] for c in self.prep['intent']['components']])
        self.assertNotIn('pwr_gnd', [c['id'] for c in self.prep['intent']['components']])
        # A spec ground other than GND has no stock power symbol and is refused.
        other = dict(self.prep, params=dict(self.prep['params'], nets=dict(self.prep['params']['nets'], ground='AGND')))
        with self.assertRaisesRegex(q.PrepareFailure, 'no stock power symbol'):
            q.qualified_intent(other)

    def test_emitted_power_symbols_pass_the_native_staged_presence_probe(self):
        # AGENTEE-123: the stage probe's bounded reader and the native netlist agree on the
        # physical references, so the schematic subject is accepted with power symbols present.
        from agent_ee.executor import execute
        from agent_ee.schematic_emitter import emit_schematic
        require_toolchain(native=True)
        raw = q.qualified_intent(self.prep)
        result, files = emit_schematic(canonical(raw), q.intent_captures(raw), canonical(q.layout()))
        self.assertEqual(result['execution'], 'ok', result['findings'])
        with tempfile.TemporaryDirectory(prefix='agent-ee-rung1-presence-') as tmp:
            project, work = Path(tmp) / 'project', Path(tmp) / 'work'
            project.mkdir()
            work.mkdir()
            for name, data in files.items():
                (project / name).write_bytes(data)
            record = execute(project, work, ['/usr/bin/python3', '-B', '-m', 'agent_ee.stages',
                                             f'/candidate/{q.NAME}.kicad_sch', 'symbols', '/work/references.json'],
                             timeout=120)
            self.assertEqual(record['execution'], 'ok', (work / 'console.log').read_text())
            self.assertEqual(json.loads((work / 'references.json').read_text()), ['C1', 'J1', 'R1'])
            self.assertEqual(json.loads((work / 'native-observation.json').read_text())['native_references'],
                             ['C1', 'J1', 'R1'])

    def test_scaffold_root_sheet_is_the_emitters_root(self):
        from agent_ee.schematic_emitter import emit_schematic
        raw = q.qualified_intent(self.prep)
        result, files = emit_schematic(canonical(raw), q.intent_captures(raw), canonical(q.layout()))
        self.assertEqual(result['execution'], 'ok', result['findings'])
        self.assertEqual(result['root_uuid'], q.root_sheet_uuid())
        self.assertIn(f'(uuid "{q.root_sheet_uuid()}")', files[f'{q.NAME}.kicad_sch'].decode())
        self.assertEqual(str(uuid.UUID(q.LAYOUT_NAMESPACE)), q.LAYOUT_NAMESPACE)

    def test_footprint_manifest_names_exactly_the_selected_stock_members(self):
        from agent_ee.footprint_library import index_footprints, read_captures
        manifest = q.footprint_manifest(self.prep['parts']['parts'])
        paths = [m['path'] for lib in manifest['libraries'] for m in lib['members']]
        self.assertEqual(len(paths), 3)
        index = index_footprints(manifest, read_captures(g.STOCK / 'footprints', paths))
        self.assertEqual((index['execution'], index['coverage']), ('ok', 'complete'), index['findings'])
        selected = {fields['Footprint'] for fields in self.prep['parts']['parts'].values()}
        self.assertEqual({f['lib_id'] for f in index['footprints']}, selected)

    def test_placement_policy_fixes_nothing_and_declaration_validates(self):
        from agent_ee.placement_adapter import validate_declaration
        policy = q.packing_policy(self.prep['params'], {'x': 100.0, 'y': 100.0, 'w': 50.0, 'h': 30.0})
        self.assertEqual((policy['schema'], policy['fixed_refs']), ('agent-ee.packing-policy/1', []))
        self.assertEqual(policy['ground_nets'], ['GND'])
        self.assertEqual(policy['min_courtyard_edge_mm'], q.EDGE_CLEARANCE_MM)
        self.assertEqual(policy['obstacles'], [])
        self.assertEqual(validate_declaration(q.placement_declaration())['helper'], 'agent-ee.placer/1')

    def test_prep_against_fome_mc_head_passes_stage_two_and_records_header_schema(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-rung1-fome-head-') as tmp:
            rundir = Path(tmp) / 'run'
            rundir.mkdir()
            prep = q.prepare(SPEC, rundir)
            stage2_receipt = json.loads((rundir / 'prep/stages/2-parts/receipt.json').read_text())
            self.assertEqual(stage2_receipt['status'], 'ok')
            self.assertEqual(stage2_receipt['details']['catalog']['header_schema'], 'four_column')
            self.assertEqual(stage2_receipt['details']['catalog']['columns'], g.CATALOG_COLUMNS_4)
            capture = q.catalog_attempt(prep, rundir / 'parts-catalog')
            self.assertEqual(capture['header_schema'], 'four_column')
            self.assertEqual(capture['columns'], g.CATALOG_COLUMNS_4)
            self.assertGreater(capture['catalog_rows'], 0)
            self.assertEqual(prep['parts']['parts']['R1']['Footprint'], 'Resistor_SMD:R_0402_1005Metric')
            self.assertEqual(prep['parts']['parts']['C1']['Footprint'], 'Capacitor_SMD:C_0603_1608Metric')


class StepsTest(unittest.TestCase):
    def fake_route(self):
        return {'schema': 'agent-ee.stage-step/1', 'stage': 'routing', 'step': 'kicad-route-krt',
                'argv': ['/usr/bin/true'], 'mounts': [], 'writes': {f'project/{q.NAME}.kicad_pcb': 'x'},
                'artifacts': [], 'adoptable_exits': [0, 2], 'timeout_s': 60}

    def schematic_steps(self):
        from unittest import mock
        with mock.patch('agent_ee.routing_adapter.step', return_value=self.fake_route()):
            return q.step_specs(Path('/nonexistent'))

    def test_steps_validate_and_write_only_mutable_inputs(self):
        mutable = [f'project/{q.NAME}.kicad_sch', f'project/{q.NAME}.kicad_pcb']
        steps = q.check_steps(self.schematic_steps(), mutable)
        self.assertEqual([(s['stage'], s['step']) for s in steps],
                         [('schematic', 'emit-schematic'), ('schematic', 'forward-annotate'),
                          ('routing', 'kicad-route-krt')])
        for name in ('emit.sh', 'annotate.sh'):
            self.assertTrue((q.STEPS_DIR / name).is_file())

    def test_a_declared_cpl_correction_adds_the_receipted_fab_step(self):
        from unittest import mock
        mutable = [f'project/{q.NAME}.kicad_sch', f'project/{q.NAME}.kicad_pcb']
        with mock.patch('agent_ee.routing_adapter.step', return_value=self.fake_route()):
            steps = q.check_steps(q.step_specs(Path('/nonexistent'), fab=True), mutable)
        self.assertEqual([(s['stage'], s['step']) for s in steps][-1], ('fab', 'export-fab'))
        self.assertEqual(steps[-1]['writes'], {mutable[1]: f'design/{mutable[1]}'})

    def test_placement_steps_and_unadoptable_exits_are_refused(self):
        mutable = [f'project/{q.NAME}.kicad_sch', f'project/{q.NAME}.kicad_pcb']
        steps = self.schematic_steps()
        with self.assertRaisesRegex(ValueError, 'placement adapter'):
            q.check_steps([{**steps[1], 'stage': 'placement'}], mutable)
        with self.assertRaisesRegex(ValueError, 'never adoptable'):
            q.check_steps([{**steps[2], 'adoptable_exits': [0, 3]}], mutable)
        with self.assertRaisesRegex(ValueError, 'outside mutable_inputs'):
            q.check_steps([{**steps[0], 'writes': {'generation/intent.json': 'out/x'}}], mutable)

    def test_routing_step_is_the_owned_adapter_declaration(self):
        from agent_ee.routing_adapter import ARTIFACTS
        # Gated like every other kicad-route test: AGENT_EE_KICAD_ROUTE_CHECKOUT unset is a
        # skip, or a failure under AGENT_EE_REQUIRE_TOOLCHAIN=1; a configured checkout that
        # does not qualify always fails. No per-host default path.
        checkout = require_kicad_route()
        route = q.step_specs(checkout)[2]
        self.assertEqual(route['adoptable_exits'], [0, 2])
        self.assertEqual(route['artifacts'], list(ARTIFACTS))
        self.assertEqual(route['writes'], {f'project/{q.NAME}.kicad_pcb': f'routing/candidate/project/{q.NAME}.kicad_pcb'})

    def test_campaign_config_carries_the_placement_adapter(self):
        profile = json.loads((ROOT / 'profiles' / 'claude-baseline.json').read_text())
        config = q.campaign_config(Path('scope.json'), profile, [], max_seconds=3600, max_iterations=4)
        self.assertEqual(config['placement_adapter']['policy'], 'generation/placement-policy.json')
        self.assertEqual((config['max_iterations'], config['max_seconds']), (4, 3600))


class ScopeTest(unittest.TestCase):
    """The staged scope over a real scaffold, validated with capture's loaders."""

    @classmethod
    def setUpClass(cls):
        require_stock()
        require_toolchain(native=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-rung1-qualified-scope-')
        root = Path(cls.tmp.name)
        cls.catalog = catalog = git_catalog(root / 'catalog', HEADER + '\n'.join(ROWS) + '\n')
        cls.rundir = root / 'run'
        cls.rundir.mkdir()
        from tests.test_fab_stage import rotation_inputs
        cls.prep = q.prepare(SPEC, cls.rundir, catalog_checkout=catalog,
                             cpl_inputs=rotation_inputs(root / 'cpl-inputs'))
        cls.scaffold, cls.record = q.write_scaffold(cls.rundir, cls.prep)
        cls.scope = q.compile_staged_scope(cls.prep['params'], cls.rundir, cls.scaffold)
        cls.scope_path = cls.rundir / 'scope.json'
        cls.scope_path.write_text(json.dumps(cls.scope))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_checks_cover_every_stage_in_order_and_trace_to_requirements(self):
        stages = [c['stage'] for c in self.scope['checks']]
        self.assertEqual(sorted(set(stages), key=q.STAGE_ORDER.index), q.STAGE_ORDER)
        self.assertEqual(stages, sorted(stages, key=q.STAGE_ORDER.index))
        traced = {c for r in self.scope['baseline']['requirements'] for c in r['checks']}
        self.assertEqual(traced, {c['id'] for c in self.scope['checks']})
        kinds = {c['id']: c['kind'] for c in self.scope['checks']}
        for kind in ('netlist_parity', 'placement', 'part_binding', 'fab_export', 'fab_consistency', 'cpl_rotation'):
            self.assertIn(kind, kinds.values())
        rc_step = next(c for c in self.scope['checks'] if c['id'] == 'rc-step')
        self.assertEqual((rc_step['params']['input_net'], rc_step['params']['ground_net']), ('/IN', 'GND'))
        self.assertEqual(self.scope['stages']['finding_retry_limit'], 3)

    def test_real_loaders_accept_the_scope_and_freeze_generation_inputs(self):
        result = q.validate_scope_file(self.scope_path, self.rundir / 'validation-inputs')
        # The intent-sourced parity check freezes the IR and the libraries it resolves against.
        for path in ['generation/intent.json'] + [f'stock/symbols/{lib}.kicad_sym' for lib in g.SYMBOL_LIBRARIES]:
            self.assertIn(path, result['baseline_files'])
        # The RC control is a captured reference, not a parity source.
        self.assertNotIn('generation/golden/rc_control.xml', result['baseline_files'])
        self.assertIn('generation/golden/rc_control.xml', result['immutable_generation_inputs'])
        parity = next(c for c in self.scope['checks'] if c['id'] == 'parity')
        self.assertEqual(parity['params']['source'], {'kind': 'intent', 'path': 'generation/intent.json'})
        # The declared CPL correction freezes its captured policy and supplier bytes.
        self.assertIn('generation/cpl/jlc-cpl-policy.json', result['baseline_files'])
        self.assertIn('generation/cpl/supplier/C910020.raweasy.json', result['baseline_files'])
        self.assertIn('generation/placement-policy.json', result['immutable_generation_inputs'])
        self.assertEqual(sorted(result['mutable_inputs']),
                         sorted([f'project/{q.NAME}.kicad_sch', f'project/{q.NAME}.kicad_pcb']))

    def test_scaffold_without_a_cpl_capture_declares_no_cpl_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            scaffold = Path(tmp) / 'scaffold'
            shutil.copytree(self.scaffold, scaffold)
            shutil.rmtree(scaffold / 'generation' / 'cpl')
            (scaffold / 'requirements' / 'qualified.md').write_text(q.QUALIFIED_REQUIREMENTS)
            scope = q.compile_staged_scope(self.prep['params'], tmp, scaffold)
            self.assertNotIn('cpl_rotation', [c['kind'] for c in scope['checks']])
            self.assertNotIn('R-cpl', [r['id'] for r in scope['baseline']['requirements']])
            self.assertEqual(scope['checks'][-1]['id'], 'fab-consistency')
            path = Path(tmp) / 'scope.json'
            path.write_text(json.dumps(scope))
            result = q.validate_scope_file(path, Path(tmp) / 'validation-inputs')
            self.assertFalse([f for f in result['baseline_files'] if f.startswith('generation/cpl/')])

    def test_scope_without_captured_rotation_policy_is_refused(self):
        scope = json.loads(json.dumps(self.scope))
        generation = next(i for i in scope['inputs'] if i['at'] == 'generation')
        generation['exclude'] = ['cpl/jlc-cpl-policy.json']
        path = self.rundir / 'missing-policy.scope.json'
        path.write_text(json.dumps(scope))
        with self.assertRaisesRegex(ValueError, 'jlc-cpl-policy.json must be captured'):
            q.validate_scope_file(path, self.rundir / 'missing-policy-inputs')

    def test_scaffold_board_matches_the_envelope_outline_and_lacks_the_fixed_connector(self):
        trip = {f['field']: f['status'] for f in self.record['envelope']['scaffold_round_trip']['fields']}
        self.assertEqual(trip['/outline'], 'match')
        self.assertEqual(trip['/fixed_footprints/0'], 'mismatch')
        self.assertFalse(self.record['envelope']['consumed_by_scope'])

    def test_scaffold_is_the_shared_builder_run_in_place_as_the_write_scaffold_step(self):
        from agent_ee.stage_receipts import validate_receipt
        summary = self.record['stage_receipt']
        self.assertEqual(self.record['builder'], 'agent_ee.scaffold.native_files')
        receipt = validate_receipt(self.rundir / 'scaffold-receipt', binding={'pre_receipt': 1})
        self.assertEqual((receipt['spec']['stage'], receipt['spec']['step'], receipt['status'], receipt['violations']),
                         ('scaffold', 'write-scaffold', 'completed', []))
        self.assertEqual(summary['receipt'], 'scaffold-receipt/receipt.json')
        self.assertIn('--unshare-net', receipt['execution']['sandbox_argv'])
        # In place: the envelope and layout are read at their candidate paths; no project file is an input.
        for rel in (q.ENVELOPE, 'generation/layout.json'):
            self.assertIn(rel, receipt['inputs'])
            self.assertIn('/candidate/' + rel, receipt['argv'])
        self.assertFalse(any(rel.startswith('project/') for rel in receipt['inputs']))
        envelope_raw = (self.scaffold / q.ENVELOPE).read_bytes()
        self.assertEqual(envelope_raw, canonical(q.envelope_record(self.prep['params'])) + b'\n')
        files = rc.scaffold_files(envelope_raw, q.root_sheet_uuid(), g.FIXTURE_PROJECT)
        self.assertEqual(sorted(receipt['outputs']), sorted(files))
        for rel, data in files.items():
            self.assertEqual(receipt['outputs'][rel], digest(self.scaffold / rel), rel)
            self.assertEqual(data, (self.scaffold / rel).read_bytes(), rel)
        self.assertIn(f'(uuid "{q.root_sheet_uuid()}")'.encode(), files[f'project/{q.NAME}.kicad_sch'])

    def test_scaffold_pre_receipt_binds_the_first_capture_of_the_staged_scope(self):
        from agent_ee.candidate import capture
        from agent_ee.stage_receipts import import_pre_receipts, verify_pre_receipts
        link = rc.scaffold_pre_receipt(self.record)
        self.assertEqual(link['binding'], {'pre_receipt': 1})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'campaign'
            links = import_pre_receipts(root, [link], self.rundir)
            run = root / 'runs' / '1'
            run.mkdir(parents=True)
            capture(self.scope_path, run)
            state = {'history': [{'run': str(run)}], 'pre_receipts': {links[0]['path']: links[0]['sha256']}}
            verify_pre_receipts(root, state, links)
            manifest = json.loads((run / 'candidate.json').read_text())
            original = json.loads(json.dumps(manifest))
            for planted, value in ((f'project/{q.NAME}.kicad_pcb', '0' * 64), ('generation/extra.json', '1' * 64),
                                   ('generation/symbols/Device.kicad_sym', '2' * 64)):
                manifest['files'] = dict(original['files'], **{planted: value})
                (run / 'candidate.json').write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, 'pre_receipt output differs from first capture: ' + planted):
                    verify_pre_receipts(root, state, links)
            (run / 'candidate.json').write_text(json.dumps(original))
            with self.assertRaisesRegex(ValueError, 'anchors differ'):
                verify_pre_receipts(root, {**state, 'pre_receipts': {}}, links)

    def test_build_declares_scaffold_only_as_a_pre_receipt(self):
        route = Path(require_kicad_route())
        built = self.rundir.parent / 'built'
        built.mkdir()
        base, scope_path, config_path = q.build(SPEC, built, profile_path=ROOT / 'profiles/claude-baseline.json',
                                                kicad_route=route, max_seconds=3600, max_iterations=4,
                                                catalog_checkout=self.catalog)
        config = json.loads(config_path.read_text())
        summary = base['scaffold']['stage_receipt']
        self.assertEqual(config['pre_receipts'], [{'path': 'scaffold-receipt/receipt.json',
                                                   'sha256': summary['receipt_sha256'], 'binding': {'pre_receipt': 1}}])
        self.assertEqual(digest(built / config['pre_receipts'][0]['path']), summary['receipt_sha256'])
        self.assertNotIn('scaffold', [s['stage'] for s in config['implementation_steps']])
        self.assertNotIn('scaffold', json.loads(scope_path.read_text())['stages']['order'])

    def test_golden_reference_is_a_native_export_of_the_rc_control(self):
        golden = (self.scaffold / 'generation/golden/rc_control.xml').read_text()
        self.assertIn('<export', golden)
        for ref in ('R1', 'C1', 'J1'):
            self.assertIn(f'<comp ref="{ref}"', golden)


class SchematicTurnTest(unittest.TestCase):
    """AGENTEE-102: the schematic turn on the RC scaffold, as the campaign coordinator runs it.

    The declared schematic steps run through stage_receipts.run_step on the captured empty
    design; a checks-only review of the adopted result grades it. No inference is spent."""

    @classmethod
    def setUpClass(cls):
        require_stock()
        require_toolchain(native=True)
        from agent_ee.stage_receipts import file_tree
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-rung1-schematic-turn-')
        root = Path(cls.tmp.name)
        catalog = git_catalog(root / 'catalog', HEADER + '\n'.join(ROWS) + '\n')
        cls.rundir = root / 'run'
        cls.rundir.mkdir()
        prep = q.prepare(SPEC, cls.rundir, catalog_checkout=catalog)
        cls.scaffold, _ = q.write_scaffold(cls.rundir, prep)
        cls.scope = q.compile_staged_scope(prep['params'], cls.rundir, cls.scaffold)
        cls.scope_path = cls.rundir / 'scope.json'
        cls.scope_path.write_text(json.dumps(cls.scope))
        cls.steps = q.check_steps(q.schematic_step_specs(), cls.scope['mutable_inputs'])
        cls.design = cls.rundir / 'turn' / 'design'
        q.materialize_inputs(cls.scope_path, cls.design)
        cls.before = file_tree(cls.design)
        cls.review, cls.stages = q.rehearse_schematic(cls.rundir, cls.scope_path, cls.design, cls.steps)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def checks(self):
        self.assertIsNotNone(self.review, json.dumps(self.stages, default=str)[:4000])
        return self.stages['after_schematic']['result']['checks']

    def check_result(self, cid):
        return json.loads((self.review / 'checks' / f'{cid}.json').read_text())

    def test_steps_complete_and_add_no_file(self):
        from agent_ee.stage_receipts import file_tree
        self.assertEqual([(r['step'], r['status'], r['exit_code'], r['violations']) for r in self.stages['schematic_steps']],
                         [('emit-schematic', 'completed', 0, []), ('forward-annotate', 'completed', 0, [])])
        (before, before_other), (after, other) = self.before, file_tree(self.design)
        self.assertEqual((sorted(after), other), (sorted(before), before_other))
        changed = sorted(p for p in after if after[p] != before[p])
        self.assertEqual(changed, sorted(self.scope['mutable_inputs']))

    def test_emitter_receipt_binds_ir_emitter_and_outputs(self):
        from agent_ee import schematic_emitter
        from agent_ee.storage import digest
        receipt_dir = Path(self.stages['schematic_steps'][0]['receipt']).parent
        emitted = json.loads((receipt_dir / 'run' / 'out' / 'emit-receipt.json').read_text())
        intent = (self.design / 'generation' / 'intent.json').read_bytes()
        from agent_ee.connectivity_ir import resolve_intent
        resolved = resolve_intent(json.loads(intent), q.intent_captures(json.loads(intent)))
        self.assertEqual((emitted['execution'], emitted['findings']), ('ok', []))
        self.assertEqual(emitted['intent_sha256'], resolved['intent_sha256'])
        self.assertEqual(emitted['implementation'], {'module': 'agent_ee.schematic_emitter',
                                                     'sha256': digest(schematic_emitter.__file__)})
        sch = f'{q.NAME}.kicad_sch'
        self.assertEqual({f['path']: f['sha256'] for f in emitted['files']}[sch], digest(self.design / 'project' / sch))
        stage = json.loads((receipt_dir / 'receipt.json').read_text())
        self.assertEqual(stage['outputs'][f'project/{sch}'], digest(self.design / 'project' / sch))
        self.assertIn('out/emit-receipt.json', stage['artifacts'])
        self.assertEqual(stage['tool']['source_tree']['path'], q.MOUNT_SOURCE)

    def test_forward_annotation_puts_three_footprints_on_the_scaffold_board(self):
        from agent_ee.stages import native_references
        board = self.design / 'project' / f'{q.NAME}.kicad_pcb'
        self.assertEqual(native_references(board, 'footprints'), ['C1', 'J1', 'R1'])
        self.assertEqual(native_references(self.design / 'project' / f'{q.NAME}.kicad_sch', 'symbols'),
                         ['C1', 'J1', 'R1'])

    def test_forward_annotated_rc_property_resave_qualifies_native_placement(self):
        import pcbnew as k
        import shutil
        from agent_ee import placement_adapter as pa
        from agent_ee.storage import digest

        base = Path(self.tmp.name) / 'placement-turn'
        design = base / 'design'
        shutil.copytree(self.design, design)
        board = design / 'project' / f'{q.NAME}.kicad_pcb'
        before = board.read_bytes()

        def properties(data):
            return {(pa._reference(fp), field[1]): field
                    for fp in pa.footprints(pa._board(data)).values()
                    for field in pa.children(fp, 'property')}

        def native_thicknesses():
            native = k.LoadBoard(str(board))
            return {(fp.GetReference(), field.GetName()): field.GetTextThickness()
                    for fp in native.GetFootprints() for field in fp.GetFields()}

        old = properties(before)
        native_before = native_thicknesses()
        normalized = set()
        for key, field in old.items():
            font = pa.children(pa.children(field, 'effects')[0], 'font')[0]
            if not pa.children(font, 'thickness'):
                if key[1] in ('Datasheet', 'Description'):
                    self.assertEqual(native_before[key], 0)
                else:
                    self.assertEqual(native_before[key], 150000)
                    self.assertEqual(pa.children(field, 'hide'), [['hide', 'yes']])
                    normalized.add(key)
        self.assertEqual(normalized, {('J1', 'JLCPCB_Assembly'), ('R1', 'LCSC'), ('C1', 'LCSC'),
                                      *((r, 'datasheet_obligation') for r in ('C1', 'J1', 'R1'))})

        summary, ok = q.rehearse_placement(base, design, self.review, self.scope)
        self.assertTrue(ok, summary)
        self.assertEqual((summary['status'], summary['exit_code']), ('completed', 0))
        scope = pa.write_scope(before, board.read_bytes())
        self.assertEqual(scope['status'], 'allowed', scope['violations'])
        self.assertEqual({t['reference'] for t in scope['transforms']}, {'C1', 'J1', 'R1'})
        new = properties(board.read_bytes())
        for key in normalized:
            font = pa.children(pa.children(new[key], 'effects')[0], 'font')[0]
            self.assertEqual(pa.children(font, 'thickness'), [['thickness', '0.15']])
        self.assertEqual(native_thicknesses(), native_before)
        # Adoption binds the real helper output, and never rewrites the source candidate.
        receipt = Path(summary['receipt']).parent
        self.assertEqual(digest(board), digest(receipt / 'run' / pa.OUTPUT))
        result = json.loads((receipt / pa.RESULT).read_text())
        self.assertEqual(result['min_courtyard_edge_mm'], q.EDGE_CLEARANCE_MM)
        from agent_ee.placement_predicates import edge_clearance
        from agent_ee.placement_probe import extract
        self.assertEqual(edge_clearance(extract(board), q.EDGE_CLEARANCE_NM)['status'], 'pass')
        self.assertEqual((self.design / 'project' / board.name).read_bytes(), before)

    def test_erc_passes_and_intent_parity_passes(self):
        checks = self.checks()
        self.assertEqual((checks['erc']['execution'], checks['erc']['outcome']), ('ok', 'pass'))
        self.assertEqual((checks['parity']['execution'], checks['parity']['outcome']), ('ok', 'pass'))
        parity = self.check_result('parity')['details']
        self.assertEqual(parity['source_provenance'], 'resolved_from_captured_intent')
        self.assertTrue(parity['topology_equivalent'])
        self.assertEqual([m['reference'] for m in parity['normalized_power_symbols']], ['#FLG01', '#PWR01'])
        self.assertEqual(self.check_result('erc')['details'].get('violations', 0), 0)

    def test_drc_with_schematic_parity_reports_no_parity_issues(self):
        checks = self.checks()
        self.assertEqual(checks['drc-placement']['execution'], 'ok')
        details = self.check_result('drc-placement')['details']
        self.assertEqual(details['schematic_parity'], 0)
        report = json.loads((self.review / 'work' / 'drc-placement' / 'drc.json').read_text())
        self.assertEqual(report['schematic_parity'], [])
        # Routing is the next stage's input: its full DRC is not reached yet.
        self.assertEqual(checks['drc']['reason'], 'stage_not_reached')

    def test_reference_control_shows_the_same_three_components_and_nets(self):
        reference = self.stages['reference_after_schematic']
        self.assertEqual((reference['role'], reference['execution'], reference['outcome']),
                         ('reference_not_oracle', 'ok', 'pass'))
        self.assertEqual(reference['native_inventory'],
                         {'components': ['C1', 'J1', 'R1'], 'nets': ['/IN', '/OUT', 'GND']})
        # Both grounds are the global power:GND net (AGENTEE-123), so the names match too.
        self.assertEqual(reference['reference_inventory'],
                         {'components': ['C1', 'J1', 'R1'], 'nets': ['/IN', '/OUT', 'GND']})
        self.assertEqual(len(reference['matched_nets']), 3)
        self.assertEqual((reference['missing_nets'], reference['extra_nets']), ([], []))

    def test_reference_read_failure_is_recorded_not_agreement(self):
        missing = self.rundir / 'absent.xml'
        reference = q.reference_parity(missing, self.design / q.GOLDEN)
        self.assertEqual((reference['execution'], reference['outcome']), ('failed', 'unknown'))


class UnresolvedPinTest(unittest.TestCase):
    """An IR pin the captured libraries do not define fails the schematic turn; no symbol is guessed."""

    @classmethod
    def setUpClass(cls):
        require_stock()
        require_toolchain(native=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-rung1-unresolved-')
        root = Path(cls.tmp.name)
        catalog = git_catalog(root / 'catalog', HEADER + '\n'.join(ROWS) + '\n')
        cls.rundir = root / 'run'
        cls.rundir.mkdir()
        prep = q.prepare(SPEC, cls.rundir, catalog_checkout=catalog)
        scaffold, _ = q.write_scaffold(cls.rundir, prep)
        cls.scope = q.compile_staged_scope(prep['params'], cls.rundir, scaffold)
        cls.scope_path = cls.rundir / 'scope.json'
        cls.scope_path.write_text(json.dumps(cls.scope))
        cls.steps = q.check_steps(q.schematic_step_specs(), cls.scope['mutable_inputs'])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def design(self, name):
        design = self.rundir / name / 'design'
        q.materialize_inputs(self.scope_path, design)
        path = design / 'generation' / 'intent.json'
        intent = json.loads(path.read_text())
        net = next(n for n in intent['nets'] if n['name'] == 'IN')
        # Device:R has pins 1 and 2 only.
        next(t for t in net['terminals'] if t['component'] == 'R1')['pin']['number'] = '3'
        path.write_text(json.dumps(intent))
        return design

    def test_emitter_step_fails_and_nothing_is_adopted(self):
        from agent_ee.stage_receipts import adopt, run_step
        design = self.design('direct')
        sch = design / 'project' / f'{q.NAME}.kicad_sch'
        empty = sch.read_bytes()
        receipt_dir = self.rundir / 'direct' / 'receipts' / '1-emit-schematic'
        receipt = run_step(receipt_dir, design, self.steps[0], {'implementation': 1, 'receipt_index': 1})
        self.assertEqual((receipt['status'], receipt['exit_code']), ('failed', 1))
        emitted = json.loads((receipt_dir / 'run' / 'out' / 'emit-receipt.json').read_text())
        self.assertEqual((emitted['execution'], emitted['outcome'], emitted['files']), ('failed', 'unknown', []))
        codes = [f['code'] for f in emitted['findings']]
        self.assertIn('resolution_rejected', codes)
        self.assertTrue(any(c.startswith('resolution_') and c != 'resolution_rejected' for c in codes), codes)
        self.assertFalse((receipt_dir / 'run' / 'out' / 'sch').exists())
        with self.assertRaisesRegex(ValueError, 'no output adopted or substituted'):
            adopt(receipt_dir, receipt, design)
        self.assertEqual(sch.read_bytes(), empty)

    def test_campaign_implementation_turn_stops_before_forward_annotation(self):
        import time
        from agent_ee.campaign import run_implementation_steps
        design = self.design('campaign')
        work = self.rundir / 'campaign' / 'implementations' / '1'
        work.mkdir(parents=True)
        state = {'mutable_inputs': self.scope['mutable_inputs'], 'stage_receipts': {}}
        plan = [{'kind': 'step', 'spec': spec, 'receipt_index': k} for k, spec in enumerate(self.steps, 1)]
        # continue_campaign records this ValueError as state incomplete (test_stage_receipts).
        with self.assertRaisesRegex(ValueError, 'emit-schematic failed; no output adopted or substituted'):
            run_implementation_steps(work, design, {'candidate_id': 'c' * 64}, state, time.monotonic() + 900, plan)
        self.assertEqual(sorted(p.name for p in (work / 'receipts').iterdir()), ['1-emit-schematic'])
        index = state['stage_receipts']['1']
        self.assertIsNotNone(index)


if __name__ == '__main__':
    unittest.main()
