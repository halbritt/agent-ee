"""Captured provenance binding and real capture -> final DRC negative control."""
import copy
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from agent_ee import checks, final_planes
from agent_ee.add_planes import zone_ids
from agent_ee.storage import digest, read_json, write_json
from tests.test_checks_erc import fixture, capture
from tests.test_final_planes import bind_fixture
from tests.toolchain import require_toolchain


class PlaneBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plan = {'schema': 'agent-ee.plane-plan/1', 'zones': [dict(net='GND', layer='In1.Cu',
            outline={'polygon_mm': [[10,10], [20,10], [20,20], [10,20]]}, clearance_mm=.2,
            min_width_mm=.2, thermal={'connection':'solid', 'gap_mm':.3, 'spoke_width_mm':.3},
            priority=0, island_removal='always')]}
        self.path = 'renamed-ground-plan.data'
        write_json(self.root/self.path, self.plan)
        self.producer = dict(schema='agent-ee.add-planes/1', execution='ok',
            plan_sha256=digest(self.root/self.path), planned_zone_ids=zone_ids(self.plan))
        self.params = bind_fixture(self.root, self.path, self.producer)

    def files(self):
        return {p.relative_to(self.root).as_posix():digest(p) for p in self.root.rglob('*') if p.is_file()}

    def test_nondefault_filename_bound_to_producer_and_composition(self):
        self.assertEqual(final_planes.bound_plans(self.root, self.files(), self.params), [self.path])

    def test_missing_stale_renamed_and_absent_despite_declaration(self):
        original = (self.root/self.path).read_bytes()
        for mode in ('missing', 'stale', 'renamed', 'absent-all'):
            with self.subTest(mode=mode):
                files = self.files()
                if mode == 'stale':
                    (self.root/self.path).write_bytes(original+b'\n')
                    files = self.files()
                else:
                    (self.root/self.path).unlink()
                    if mode == 'renamed':
                        (self.root/'another.data').write_bytes(original)
                    if mode in ('renamed', 'absent-all'):
                        files = self.files() if mode == 'renamed' else {}
                with self.assertRaisesRegex(ValueError, 'absent|stale'):
                    final_planes.bound_plans(self.root, files, self.params)
                (self.root/self.path).write_bytes(original)
                (self.root/'another.data').unlink(missing_ok=True)

    def test_unbound_plan_or_producer_cannot_silently_skip(self):
        with self.assertRaisesRegex(ValueError, 'unbound captured plane obligation'):
            final_planes.bound_plans(self.root, self.files(), {})
        (self.root/self.path).unlink()
        with self.assertRaisesRegex(ValueError, 'unbound captured plane obligation'):
            final_planes.bound_plans(self.root, self.files(), {})

    def test_stale_producer_and_composition_plan_hashes(self):
        for key in ('producer', 'composition'):
            with self.subTest(key=key):
                params = copy.deepcopy(self.params)
                path = self.root/params['plane_obligations'][0][key]['path']
                original = path.read_bytes()
                record = read_json(path)
                if key == 'producer':
                    record['plan_sha256'] = '0'*64
                else:
                    record['planes'][0]['sha256'] = '0'*64
                write_json(path, record)
                params['plane_obligations'][0][key]['sha256'] = digest(path)
                with self.assertRaisesRegex(ValueError, 'does not bind'):
                    final_planes.bound_plans(self.root, self.files(), params)
                path.write_bytes(original)

    def test_legitimate_no_plane_caller(self):
        self.assertEqual(final_planes.bound_plans(self.root, {}, {}), [])

    def test_capture_then_native_final_drc_refuses_unbound_renamed_plan(self):
        require_toolchain(native=True)
        scope = fixture(self.root, 'drc')
        scope['inputs'].append({'path':str(self.root/self.path), 'at':self.path})
        run, manifest = capture(self.root, scope)
        result = checks.run_checks(run, time.monotonic()+180)[0]
        self.assertIn(self.path, manifest['files'])
        self.assertTrue((run/'work/drc/drc.json').is_file(), result)
        self.assertEqual(result['execution'], 'malformed', result)
        self.assertIn('unbound captured plane obligation', result['summary'])
        evidence = os.environ.get('FINAL_PLAN_EVIDENCE')
        if evidence:
            shutil.copytree(run, Path(evidence)/self._testMethodName, dirs_exist_ok=True)

    def test_capture_freezes_all_three_binding_inputs_in_scope2_and_scope3(self):
        from agent_ee.baseline import load_baseline
        from agent_ee.capabilities import convert_scope
        scope = fixture(self.root, 'drc')
        scope['checks'][0]['params'] = self.params
        for path in (self.path, 'producer.json', 'composition.json'):
            scope['inputs'].append({'path':str(self.root/path), 'at':path})
        for version in (2, 3):
            with self.subTest(version=version):
                selected = scope if version == 2 else convert_scope(scope)
                run, manifest = capture(self.root, selected, name=f'bound-{version}')
                baseline = load_baseline(run)
                for path in final_planes.binding_inputs(self.params):
                    self.assertEqual(baseline['files'][path], manifest['files'][path])
                    if version == 3:
                        self.assertIn(path, baseline['coverage']['checks']['drc']['immutable_inputs'])

    def test_composition_cannot_hide_second_required_plan(self):
        path = self.root/'composition.json'
        record = read_json(path)
        record['planes'].append({'path':'missing-second-plan.json', 'sha256':'a'*64})
        write_json(path, record)
        self.params['plane_obligations'][0]['composition']['sha256'] = digest(path)
        with self.assertRaisesRegex(ValueError, 'undeclared plane obligation'):
            final_planes.bound_plans(self.root, self.files(), self.params)
