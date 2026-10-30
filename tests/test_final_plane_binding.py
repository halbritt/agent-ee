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

    def test_direct_native_plan_checks_do_not_need_a_pod_composition(self):
        (self.root/'producer.json').unlink()
        (self.root/'composition.json').unlink()
        link = {'path': self.path, 'sha256': digest(self.root/self.path)}
        params = {'plane_plans': [link]}
        self.assertEqual(final_planes.binding_inputs(params), [self.path])
        self.assertEqual(final_planes.bound_plans(self.root, self.files(), params), [self.path])
        with self.assertRaisesRegex(ValueError, 'duplicate plane obligation'):
            final_planes.bound_plans(self.root, self.files(), {'plane_plans': [link, link]})
        (self.root/self.path).write_bytes((self.root/self.path).read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'stale plane binding'):
            final_planes.bound_plans(self.root, self.files(), params)

    def test_direct_plan_cannot_hide_captured_producer_obligations(self):
        params = {'plane_plans': [{'path': self.path, 'sha256': digest(self.root/self.path)}]}
        with self.assertRaisesRegex(ValueError, 'unbound captured plane obligation'):
            final_planes.bound_plans(self.root, self.files(), params)

    def test_direct_plan_rejects_ambiguous_duplicate_keys(self):
        (self.root/'producer.json').unlink()
        (self.root/'composition.json').unlink()
        path = self.root/self.path
        path.write_text(path.read_text().replace('"net": "GND"', '"net": "WRONG", "net": "GND"'))
        params = {'plane_plans': [{'path': self.path, 'sha256': digest(path)}]}
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            final_planes.bound_plans(self.root, self.files(), params)

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

    def test_late_producer_prepares_without_a_report_hash_and_freezes_only_sources(self):
        from agent_ee.baseline import load_baseline
        params = copy.deepcopy(self.params)
        params['plane_obligations'][0]['producer'] = {'path': 'producer.json', 'stage': 'add-planes'}
        (self.root/'producer.json').write_bytes(b'')
        scope = fixture(self.root, 'drc')
        scope['checks'][0]['params'] = params
        scope['mutable_inputs'] = [scope['schematic'], scope['board'], 'producer.json']
        for path in (self.path, 'producer.json', 'composition.json'):
            scope['inputs'].append({'path': str(self.root/path), 'at': path})
        run, manifest = capture(self.root, scope)
        baseline = load_baseline(run)
        self.assertNotIn('producer.json', baseline['files'])
        for path in (self.path, 'composition.json'):
            self.assertEqual(baseline['files'][path], manifest['files'][path])
        with self.assertRaisesRegex(ValueError, 'no authenticated stage receipt'):
            final_planes.bound_plans(run/'inputs', manifest['files'], params)

    def test_composition_cannot_hide_second_required_plan(self):
        path = self.root/'composition.json'
        record = read_json(path)
        record['planes'].append({'path':'missing-second-plan.json', 'sha256':'a'*64})
        write_json(path, record)
        self.params['plane_obligations'][0]['composition']['sha256'] = digest(path)
        with self.assertRaisesRegex(ValueError, 'undeclared plane obligation'):
            final_planes.bound_plans(self.root, self.files(), self.params)

    def test_whitespace_prefix_does_not_hide_unbound_plan(self):
        path = self.root/self.path
        path.write_bytes(b' ' * 256 + path.read_bytes())
        (self.root/'producer.json').unlink()
        (self.root/'composition.json').unlink()
        with self.assertRaisesRegex(ValueError, 'unbound captured plane obligation'):
            final_planes.bound_plans(self.root, self.files(), {})


class ReferencePlaneBindingTests(unittest.TestCase):
    """Captured block inputs are source evidence, with a separate current plan."""

    def setUp(self):
        PlaneBindingTests.setUp(self)
        from tests.test_reference_blocks import block
        from agent_ee.reference_blocks import block_contract, validate_block
        self.board = 'current.kicad_pcb'
        # This suite tests binding only; native saved-zone checks have their own fixtures.
        (self.root/self.board).write_text('owned current-board identity')
        self.source = self.root/'reference/source'
        self.source.mkdir(parents=True)
        record = block(self.source)
        write_json(self.source/'captured-plan.data', self.plan)
        record['source']['paths']['captured-plan.data'] = 'captured-plan.data'
        write_json(self.source/'block-contract.json', block_contract(record))
        record['files'].update({name: digest(self.source/name)
                               for name in ('captured-plan.data', 'block-contract.json')})
        write_json(self.source/'block.json', record)
        self.assertEqual(validate_block(self.source)['status'], 'unverified')

    files = PlaneBindingTests.files

    def test_validated_reference_payload_does_not_add_current_board_obligations(self):
        self.assertEqual(final_planes.bound_plans(self.root, self.files(), self.params,
                                                  board=self.board), [self.path])

    def test_reference_payload_never_replaces_explicit_current_board_plan(self):
        for board, params in ((None, self.params), (self.board, {}),
                              ('reference/source/sheet.kicad_sch', self.params)):
            with self.subTest(board=board, params=params), self.assertRaises(ValueError):
                final_planes.bound_plans(self.root, self.files(), params, board=board)

    def test_reference_payload_requires_complete_current_captured_custody(self):
        original = (self.source/'captured-plan.data').read_bytes()
        manifest = (self.source/'block.json').read_bytes()
        for mode in ('changed-payload', 'extra-payload', 'stale-manifest', 'renamed-manifest'):
            with self.subTest(mode=mode):
                files = self.files()
                if mode == 'changed-payload':
                    (self.source/'captured-plan.data').write_bytes(original+b'\n')
                    files = self.files()
                elif mode == 'extra-payload':
                    (self.source/'another-plan.data').write_bytes(original)
                    files = self.files()
                elif mode == 'stale-manifest':
                    (self.source/'block.json').write_bytes(manifest+b'\n')
                else:
                    (self.source/'block.json').rename(self.source/'renamed-block.json')
                    files = self.files()
                with self.assertRaises(ValueError):
                    final_planes.bound_plans(self.root, files, self.params, board=self.board)
                (self.source/'captured-plan.data').write_bytes(original)
                (self.source/'block.json').write_bytes(manifest)
                (self.source/'another-plan.data').unlink(missing_ok=True)
                (self.source/'renamed-block.json').unlink(missing_ok=True)

    def test_a_reference_directory_name_does_not_hide_an_unbound_plan(self):
        (self.root/'reference/uncaptured-plan.data').write_bytes(
            (self.source/'captured-plan.data').read_bytes())
        with self.assertRaisesRegex(ValueError, 'unbound captured plane obligation'):
            final_planes.bound_plans(self.root, self.files(), self.params, board=self.board)

    def test_reference_capture_cannot_hide_other_obligation_kinds(self):
        from agent_ee.reference_blocks import block_contract
        (self.root/'producer.json').unlink()
        (self.root/'composition.json').unlink()
        params = {'plane_plans': [{'path': self.path, 'sha256': digest(self.root/self.path)}]}
        cases = [('unlisted-plan', self.plan, False),
                 ('producer', self.producer, True),
                 ('composition', {'schema': 'pod-capture.scope-composition/4',
                                  'planes': [{'path': 'absent.data', 'sha256': 'a'*64}]}, True)]
        for name, payload, source_listed in cases:
            with self.subTest(name=name):
                write_json(self.source/'extra.data', payload)
                record = read_json(self.source/'block.json')
                if source_listed:
                    record['source']['paths']['extra.data'] = 'extra.data'
                write_json(self.source/'block-contract.json', block_contract(record))
                record['files'].update({p: digest(self.source/p)
                                       for p in ('extra.data', 'block-contract.json')})
                write_json(self.source/'block.json', record)
                with self.assertRaisesRegex(ValueError, 'unbound captured plane obligation'):
                    final_planes.bound_plans(self.root, self.files(), params, board=self.board)

    def test_missing_candidate_pin_and_selected_source_plan_cannot_be_exempted(self):
        files = self.files()
        del files['reference/source/captured-plan.data']
        with self.assertRaisesRegex(ValueError, 'payload differs'):
            final_planes.bound_plans(self.root, files, self.params, board=self.board)
        params = copy.deepcopy(self.params)
        params['plane_plans'] = [{'path': 'reference/source/captured-plan.data',
                                 'sha256': digest(self.source/'captured-plan.data')}]
        write_json(self.source/'another-plan.data', self.plan)
        # Selecting a source payload as an active plan prevents treating its enclosing
        # block as historical, even if other files carry an apparently valid block seal.
        with self.assertRaisesRegex(ValueError, 'unbound captured plane obligation'):
            final_planes.bound_plans(self.root, self.files(), params, board=self.board)
