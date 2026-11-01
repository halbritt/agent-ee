"""Real source admission and retained-data consumption, not board acceptance."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from agent_ee import (acquisition_block_source, acquisition_mechanics, acquisition_power_geometry,
    acquisition_power_prepare as producer, build_workflow, cpl_rotation, land_pattern,
    requirement_map, typed_overlay)
from agent_ee.storage import identity, write_json

BASE = Path(os.environ.get('AGENT_EE_ACQUISITION_INPUTS', '/nonexistent/base'))
SELECTED = Path(os.environ.get('AGENT_EE_POWER_PART_SOURCES', '/nonexistent/selected'))
EXTRA = Path(os.environ.get('AGENT_EE_POWER_BUILD_EXTRA', '/nonexistent/extra'))
NATIVE = Path(os.environ.get('AGENT_EE_POWER_BUILD_NATIVE', '/nonexistent/native'))
AVAILABLE = all(p.is_dir() for p in (BASE, SELECTED, EXTRA, NATIVE))


@unittest.skipUnless(AVAILABLE, 'exact captured source, extra evidence and real native build required')
class PowerBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(os.environ.get('AGENT_EE_POWER_BUILD_TEST_OUTPUT', cls.temporary.name))
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.bundle = cls.root / 'bundle'
        cls.report = producer.prepare(BASE, SELECTED, EXTRA, cls.bundle)
        cls.source = cls.bundle / 'inputs'

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_complete_typed_accounting_preserves_authored_obligations_and_native_guards(self):
        admitted = build_workflow.preflight_build(self.bundle / 'build-plan.json', self.root / 'later')
        self.assertEqual(admitted[5], self.report['files'])
        self.assertEqual((self.source / 'obligations.json').read_bytes(), (BASE / 'obligations.json').read_bytes())
        self.assertEqual((self.source / 'spec.md').read_bytes(), (BASE / 'spec.md').read_bytes())
        source_map = json.loads((self.source / 'requirement-map.json').read_bytes())
        self.assertNotIn('block_instances', source_map['scope'])
        pool = typed_overlay._check_pool(source_map)
        old = typed_overlay._check_pool(json.loads((BASE / 'requirement-map.json').read_bytes()))
        retained = {k for k in old if not k.startswith(('power-', 'analog-', 'host-'))}
        self.assertTrue(retained <= pool.keys())
        self.assertFalse(any(c['kind'] == 'active_spice' for c in pool.values()))
        self.assertEqual(pool['assembly']['params']['max_age_hours'], 24)
        self.assertEqual(pool['engineering-geometry'], old['engineering-geometry'])
        removal = next(r for r in source_map['requirements'] if r['id'] == 'ACQ-ELEC-3')['spans'][1]
        self.assertEqual((removal['byte_start'], removal['byte_end']), (1822, 1929))
        self.assertEqual([c['id'] for c in removal['mapping']['checks']], ['adp-power-retained-model'])
        self.assertFalse(self.report['accepted'])
        self.assertEqual(self.report['file_release'], 'not_assessed')
        self.assertFalse((self.source / 'judgments').exists())

    def test_geometry_and_all_eighteen_relationships_bind_actual_source(self):
        plan = json.loads((self.bundle / 'build-plan.json').read_bytes())
        bound = acquisition_block_source.derive(self.source, plan, reference=producer.REFERENCE)
        self.assertEqual(bound, json.loads((self.source / acquisition_block_source.PATH).read_bytes()))
        self.assertEqual(len(bound['templates']), 18)
        self.assertEqual(sum(len(b['references']) for b in bound['allocation']['blocks']), 17)
        acquisition_mechanics.load(self.source / 'placement-mechanics.json')
        case = json.loads((producer.DATA / 'mechanics-test-case.json').read_bytes())
        self.assertEqual(case['model_raw'].encode(), (self.source / 'analysis/geometry-model.json').read_bytes())
        self.assertEqual({k: v for k, v in case['sources'].items() if k not in acquisition_mechanics.SOURCES},
                         acquisition_mechanics._model_sources(acquisition_power_geometry.MODEL))
        with self.assertRaises(ValueError):
            acquisition_power_geometry.revise((BASE / 'analysis/geometry-model.json').read_bytes() + b' ')
        with self.assertRaises(ValueError):
            acquisition_block_source.derive(self.source, plan)  # old TPS endpoint guard remains strict

    def test_actual_generated_native_cpl_frames_match_new_policy(self):
        report = json.loads((NATIVE / 'build-report.json').read_bytes())
        self.assertEqual(report['status'], 'checkpoint')
        self.assertTrue({'requirements', 'parts', 'scaffold', 'schematic'} <= {r['stage'] for r in report['completed']})
        candidate = NATIVE / report['candidate']
        board = land_pattern.board_footprints((candidate / 'project/acquisition.kicad_pcb').read_bytes())
        self.assertEqual(len(board), 17)
        policy = json.loads((self.source / 'cpl/jlc-cpl-policy.json').read_bytes())
        self.assertEqual(next(r['rotation_degrees'] for r in policy['footprint_rules']
                             if r['id'] == 'adp7104-soic-ep-270'), 270)
        for ref, lcsc, wanted in [('U1', 'C2903961', 270), ('D1', 'C155367', 0)]:
            supplier = cpl_rotation.parse_package((self.source / ('power-reference/supplier/' + lcsc + '.raweasy.json')).read_bytes(), lcsc, 200)
            native = land_pattern.board_footprint(board, ref, None)
            a = cpl_rotation.classify_pin1(cpl_rotation._top_view(supplier['pattern']))
            b = cpl_rotation.classify_pin1(cpl_rotation._top_view(native['pattern']))
            self.assertEqual(cpl_rotation.pin1_offset(a[0], b[0]), wanted)

    def test_actual_native_local_route_endpoints_follow_the_captured_power_reference(self):
        from agent_ee import acquisition_local_return as local, acquisition_local_routing as routing
        from agent_ee.placement_job import JobRefusal
        recipe = acquisition_mechanics.load(self.source / 'placement-mechanics.json')
        self.assertEqual(routing.power_reference(recipe), producer.REFERENCE)
        report = json.loads((NATIVE / 'build-report.json').read_bytes())
        board = NATIVE / report['candidate'] / 'project/acquisition.kicad_pcb'
        for name, pin, net in [('output-supply', '1', '/V3V3'), ('output-return', '3', 'GND')]:
            with self.subTest(connection=name):
                endpoints = local._endpoints(board, connection=name, reference=producer.REFERENCE)
                self.assertEqual((endpoints[1]['ref'], endpoints[1]['number'], endpoints[1]['net']), ('U1', pin, net))
                with self.assertRaisesRegex(JobRefusal, 'actual .* net'):
                    local._endpoints(board, connection=name)
        with self.assertRaisesRegex(JobRefusal, 'unsupported acquisition reference'):
            local.connections('arbitrary-board')

    def _measurement(self, name, change=None):
        from agent_ee.checks import Ctx
        from agent_ee.measurements import check_measurements
        run = self.root / name
        shutil.copytree(self.source, run / 'inputs')
        if change is not None:
            change(run / 'inputs')
        files = requirement_map.input_inventory(run / 'inputs')
        pool = typed_overlay._check_pool(json.loads((self.source / 'requirement-map.json').read_bytes()))
        check = deepcopy(pool['adp-power-retained-model'])
        # Isolate the ordinary measurement consumer. The real native build
        # separately exercises its compiled stage declaration and prerequisites.
        check.pop('stage')
        manifest = dict(candidate_id=identity(files), files=files, scope=dict(schema='agent-ee.scope/1', checks=[check]))
        write_json(run / 'candidate.json', manifest)
        ctx = Ctx(run, manifest, check, time.monotonic() + 60)
        check_measurements(ctx)
        write_json(run / 'result.json', ctx.result)
        return ctx

    def test_existing_sandbox_measurement_consumer_replays_real_native_evidence(self):
        ctx = self._measurement('model-replay')
        self.assertEqual((ctx.result['execution'], ctx.result['outcome']), ('ok', 'pass'))
        raw = json.loads((ctx.work / 'tool/model-measurements.json').read_bytes())
        self.assertIn('no fresh simulation', raw['evidence_operation'])
        self.assertFalse(raw['accepted'])
        self.assertEqual(raw['observations']['samples'], 40613)
        self.assertTrue(all(raw['model_predicates'].values()))
        self.assertTrue(all(v >= 0 for margins in ctx.result['details']['margins'].values() for v in margins.values()))

    def test_changed_current_circuit_and_retained_waveform_are_rejected(self):
        from agent_ee.checks import CheckError
        for name, diagnostic in [('circuit.json', 'operative circuit differs'),
                                 ('analysis/adp7104/high-z-removal.raw', 'retained model evidence changed')]:
            def change(root, name=name):
                p = root / name
                p.write_bytes(p.read_bytes() + b' ')
            with self.subTest(path=name), self.assertRaises(CheckError):
                self._measurement('changed-' + Path(name).name, change)
            work = self.root / ('changed-' + Path(name).name) / 'work'
            logs = '\n'.join(p.read_text(errors='replace') for p in work.rglob('*.log'))
            self.assertIn(diagnostic, logs)

    def test_external_authored_limit_can_reject_an_otherwise_valid_trace(self):
        from agent_ee.measurements import evaluate_limits
        ctx = self._measurement('tighter-limit')
        limits = deepcopy(ctx.params['limits'])
        limits['steady_vout_min']['min'] = 3.31
        margins = evaluate_limits(ctx.result['details']['metrics'], limits)
        self.assertLess(margins['steady_vout_min']['min'], 0)

