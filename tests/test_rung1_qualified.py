"""Rung 1 on the owned pipeline: generation inputs, staged scope, steps and campaign config.

No test here runs the campaign, a review or any inference. The native class builds the
scaffold (reference netlist export and empty-file loads in the sandbox) and validates the
scope with the real loaders.
"""
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid

from agent_ee import generate as g
from agent_ee import rung1_campaign as rc
from agent_ee import rung1_qualified as q
from agent_ee.storage import canonical
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

    def captures(self):
        return {f'{lib}.kicad_sym': (g.STOCK / 'symbols' / f'{lib}.kicad_sym').read_bytes()
                for lib in g.SYMBOL_LIBRARIES}

    def test_intent_uses_local_label_names_and_no_power_symbols(self):
        from agent_ee.connectivity_ir import resolve_intent
        intent = q.qualified_intent(self.prep)
        resolved = resolve_intent(intent, self.captures())
        self.assertEqual((resolved['execution'], resolved['outcome'], resolved['coverage']), ('ok', 'pass', 'complete'))
        self.assertEqual(sorted(n['name'] for n in intent['nets']), ['GND', 'IN', 'OUT'])
        self.assertEqual(sorted(c['reference'] for c in intent['components']), ['C1', 'J1', 'R1'])
        self.assertEqual(q.native_net('GND'), '/GND')
        # The generate intent is not changed in place.
        self.assertIn('/IN', [n['name'] for n in self.prep['intent']['nets']])

    def test_scaffold_root_sheet_is_the_emitters_root(self):
        from agent_ee.schematic_emitter import emit_schematic
        intent = canonical(q.qualified_intent(self.prep))
        result, files = emit_schematic(intent, self.captures(), canonical(q.layout()))
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
        from agent_ee.placement import qualified_polygons
        policy = q.packing_policy(self.prep['params'], {'x': 100.0, 'y': 100.0, 'w': 50.0, 'h': 30.0})
        self.assertEqual((policy['schema'], policy['fixed_refs']), ('agent-ee.packing-policy/1', []))
        self.assertEqual(policy['ground_nets'], ['/GND'])
        self.assertEqual([o['side'] for o in policy['obstacles']], ['F'] * 4 + ['B'] * 4)
        for obstacle in policy['obstacles']:
            self.assertEqual(len(qualified_polygons({'status': 'proven', 'polygons': obstacle['polygons']})), 1)
        self.assertEqual(policy['obstacles'][0]['polygons'][0][0], [100_000_000, 100_000_000])
        self.assertEqual(validate_declaration(q.placement_declaration())['helper'], 'agent-ee.placer/1')


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
                          ('routing', 'kicad-route-krt'), ('fab', 'export-fab')])
        for name in ('emit.sh', 'annotate.sh'):
            self.assertTrue((q.STEPS_DIR / name).is_file())

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
        catalog = git_catalog(root / 'catalog', HEADER + '\n'.join(ROWS) + '\n')
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
        self.assertEqual((rc_step['params']['input_net'], rc_step['params']['ground_net']), ('/IN', '/GND'))
        self.assertEqual(self.scope['stages']['finding_retry_limit'], 3)

    def test_real_loaders_accept_the_scope_and_freeze_generation_inputs(self):
        result = q.validate_scope_file(self.scope_path, self.rundir / 'validation-inputs')
        self.assertIn('generation/golden/rc_control.xml', result['baseline_files'])
        self.assertIn('generation/cpl/jlc-cpl-policy.json', result['baseline_files'])
        self.assertIn('generation/cpl/supplier/C910020.raweasy.json', result['baseline_files'])
        self.assertIn('generation/placement-policy.json', result['immutable_generation_inputs'])
        self.assertEqual(sorted(result['mutable_inputs']),
                         sorted([f'project/{q.NAME}.kicad_sch', f'project/{q.NAME}.kicad_pcb']))

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

    def test_golden_reference_is_a_native_export_of_the_rc_control(self):
        golden = (self.scaffold / 'generation/golden/rc_control.xml').read_text()
        self.assertIn('<export', golden)
        for ref in ('R1', 'C1', 'J1'):
            self.assertIn(f'<comp ref="{ref}"', golden)


if __name__ == '__main__':
    unittest.main()
