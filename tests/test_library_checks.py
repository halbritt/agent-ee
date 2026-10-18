"""Captured declarations retain immutable library and assembly authority."""
import copy
import tempfile
import unittest
from pathlib import Path

from agent_ee.library_checks import capability
from agent_ee.storage import write_json
from test_footprint_library import capture, SOT3, symbol_capture, declarations, owner_declarations


class LibraryCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manifest, self.bytes = capture(SOT3)
        self.lib, data = symbol_capture()
        self.bytes[self.lib['path']] = data
        self.declaration = declarations(self.lib)
        self.params = dict(manifest='manifest.json', declaration='assignment.json',
                           assembly_intent=[dict(reference='U1', assembly='smd', connector=None)])
        for path, data in self.bytes.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        write_json(self.root / 'manifest.json', self.manifest)
        write_json(self.root / 'assignment.json', self.declaration)
        self.files = dict.fromkeys([*self.bytes, 'manifest.json', 'assignment.json'], 'hash')

    def test_dependency_closure_is_immutable_but_selection_remains_candidate(self):
        result = capability(self.params, self.files, self.root)
        self.assertEqual(result['immutable_inputs'], ['manifest.json', 'owned.kicad_sym', 'owned.pretty/SOT3.kicad_mod'])
        self.assertNotIn('assignment.json', result['immutable_inputs'])
        self.assertEqual(result['predicates'], ['footprint:assignment-parity/1'])

    def test_assembly_intent_cannot_change_with_candidate_declaration(self):
        self.declaration['components'][0]['assembly'] = 'through_hole'
        write_json(self.root / 'assignment.json', self.declaration)
        with self.assertRaisesRegex(ValueError, 'immutable intent'):
            capability(self.params, self.files, self.root)

    def test_missing_capture_or_incomplete_manifest_is_unsupported(self):
        for path in list(self.files):
            files = copy.deepcopy(self.files)
            del files[path]
            with self.subTest(path=path), self.assertRaises(ValueError):
                capability(self.params, files, self.root)
        self.manifest['complete'] = False
        write_json(self.root / 'manifest.json', self.manifest)
        with self.assertRaisesRegex(ValueError, 'complete'):
            capability(self.params, self.files, self.root)

    def test_owner_declarations_are_immutable_scope_inputs(self):
        declared = owner_declarations([('Owned:SOT3', dict(assembly='smd'))])
        write_json(self.root / 'declarations.json', declared)
        params = dict(self.params, declarations='declarations.json')
        files = dict(self.files, **{'declarations.json': 'hash'})
        result = capability(params, files, self.root)
        self.assertIn('declarations.json', result['immutable_inputs'])
        self.assertEqual(result['predicates'], ['footprint:assignment-parity/1'])
        with self.assertRaisesRegex(ValueError, 'captured'):
            capability(params, self.files, self.root)
        write_json(self.root / 'declarations.json', dict(declared, schema='other'))
        with self.assertRaisesRegex(ValueError, 'schema'):
            capability(params, files, self.root)


class LibraryPipelineTests(unittest.TestCase):
    def test_captured_assignment_control_defect_seal_and_dependency_drift(self):
        from toolchain import require_toolchain
        require_toolchain()
        import hashlib
        import shutil
        import time
        from agent_ee import executor, symbol_library
        from agent_ee.candidate import capture as capture_candidate, verify_snapshot, verify_source
        from agent_ee.checks import run_checks
        from agent_ee.baseline import freeze_evidence, validate_witnesses
        from agent_ee.storage import read_json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'
            source.mkdir()
            overlay = root / 'app'
            shutil.copytree(Path(__file__).resolve().parents[1] / 'src', overlay)
            shutil.copyfile(symbol_library.__file__, overlay / 'agent_ee/symbol_library.py')
            previous = executor.PACKAGE_ROOT
            executor.PACKAGE_ROOT = overlay
            self.addCleanup(setattr, executor, 'PACKAGE_ROOT', previous)
            manifest, bytefiles = capture(SOT3)
            four = SOT3.replace('"SOT3"', '"SOT4"', 1)[:-1] + '(pad "4" smd rect (at 2 3) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask")))'
            bytefiles['owned.pretty/SOT4.kicad_mod'] = four.encode()
            manifest['libraries'][0]['members'].append(dict(path='owned.pretty/SOT4.kicad_mod', sha256=hashlib.sha256(four.encode()).hexdigest()))
            lib, data = symbol_capture()
            bytefiles[lib['path']] = data
            for path, data in bytefiles.items():
                target = source / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            for member in manifest['libraries'][0]['members']:
                member['path'] = 'data/' + member['path']
            lib['path'] = 'data/' + lib['path']
            declaration = declarations(lib, footprint='Owned:SOT4')
            write_json(source / 'manifest.json', manifest)
            for name in ('board', 'schematic', 'req', 'target', 'reference'):
                (source / name).write_text('Owned assignment fixture: ' + name)
            spec = dict(id='fp', kind='footprint_assignment', params=dict(
                manifest='data/manifest.json', declaration='data/assignment.json',
                assembly_intent=[dict(reference='U1', assembly='smd', connector=None)]))
            scope = dict(schema='agent-ee.scope/3', name='owned-library', scope='Declared assignment only',
                board='data/board', schematic='data/schematic', inputs=[dict(path='source', at='data')],
                checks=[spec], baseline=dict(requirements=[dict(id='R1', intent='acceptance',
                    statement='Selected footprint has every symbol pin and matching assembly technology',
                    source=dict(path='data/req', locator='R1'), checks=['fp'])],
                    targets=['data/target'], reference_architectures=['data/reference']))
            write_json(root / 'scope.json', scope)
            baselines = []
            for i, (footprint, expected) in enumerate((('Owned:SOT4', 'pass'), ('Owned:SOT3', 'defect'))):
                declaration['components'][0]['footprint'] = footprint
                write_json(source / 'assignment.json', declaration)
                run = root / f'run{i}'
                run.mkdir()
                capture_candidate(root / 'scope.json', run)
                baselines.append(read_json(run / 'baseline.json')['baseline_id'])
                results = run_checks(run, time.monotonic() + 60)
                self.assertEqual((results[0]['execution'], results[0]['outcome']), ('ok', expected), results)
                self.assertEqual(results[0]['candidate_id'], read_json(run / 'candidate.json')['candidate_id'])
                freeze_evidence(run, results)
                verify_snapshot(run)
                if expected == 'defect':
                    witness = [dict(requirement_id='R1', check_id='fp')]
                    evidence = [dict(path='checks/fp.json', locator='outcome')]
                    validate_witnesses(run, witness, evidence)
                    report = read_json(run / 'work/fp/assignments.json')
                    self.assertEqual(report['assignments'][0]['findings'][0]['missing_pads'], ['4'])
                    report['assignments'][0]['findings'][0]['missing_pads'] = []
                    write_json(run / 'work/fp/assignments.json', report)
                    with self.assertRaisesRegex(ValueError, 'evidence changed'):
                        validate_witnesses(run, witness, evidence)
                    (source / 'owned.pretty/SOT3.kicad_mod').write_text('drifted')
                    with self.assertRaises(ValueError):
                        verify_source(run)
                    (run / 'inputs/data/owned.pretty/SOT3.kicad_mod').write_text('drifted')
                    with self.assertRaises(ValueError):
                        verify_snapshot(run)
            self.assertEqual(baselines[0], baselines[1])

    def test_declared_mechanical_pads_through_sandboxed_pipeline(self):
        from toolchain import require_toolchain
        require_toolchain()
        import shutil
        import time
        from agent_ee import executor, symbol_library
        from agent_ee.candidate import capture as capture_candidate, verify_source
        from agent_ee.checks import run_checks
        from agent_ee.storage import read_json
        from test_footprint_library import library
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'
            source.mkdir()
            overlay = root / 'app'
            shutil.copytree(Path(__file__).resolve().parents[1] / 'src', overlay)
            shutil.copyfile(symbol_library.__file__, overlay / 'agent_ee/symbol_library.py')
            previous = executor.PACKAGE_ROOT
            executor.PACKAGE_ROOT = overlay
            self.addCleanup(setattr, executor, 'PACKAGE_ROOT', previous)
            manifest, bytefiles = library('ConnMP')
            lib, data = symbol_capture(('1', '2'))
            bytefiles[lib['path']] = data
            for path, data in bytefiles.items():
                target = source / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            for member in manifest['libraries'][0]['members']:
                member['path'] = 'data/' + member['path']
            lib['path'] = 'data/' + lib['path']
            connector = {'rows': 1, 'columns': 2}
            write_json(source / 'manifest.json', manifest)
            write_json(source / 'assignment.json', declarations(lib, footprint='Owned:ConnMP', connector=connector))
            write_json(source / 'declarations.json', owner_declarations([('Owned:ConnMP', dict(mechanical_pads=['MP']))]))
            for name in ('board', 'schematic', 'req', 'target', 'reference'):
                (source / name).write_text('Owned assignment fixture: ' + name)
            params = dict(manifest='data/manifest.json', declaration='data/assignment.json',
                          assembly_intent=[dict(reference='U1', assembly='smd', connector=connector)])
            outcomes, baselines = [], []
            changed = owner_declarations([('Owned:ConnMP', dict(mechanical_pads=['MP', '1']))])
            for i, extra in enumerate(({}, {'declarations': 'data/declarations.json'}, changed)):
                if extra is changed:
                    write_json(source / 'declarations.json', changed)
                    extra = {'declarations': 'data/declarations.json'}
                scope = dict(schema='agent-ee.scope/3', name='owned-library', scope='Declared assignment only',
                    board='data/board', schematic='data/schematic', inputs=[dict(path='source', at='data')],
                    checks=[dict(id='fp', kind='footprint_assignment', params=dict(params, **extra))],
                    baseline=dict(requirements=[dict(id='R1', intent='acceptance',
                        statement='Selected footprint has every symbol pin', source=dict(path='data/req', locator='R1'),
                        checks=['fp'])], targets=['data/target'], reference_architectures=['data/reference']))
                write_json(root / 'scope.json', scope)
                run = root / f'run{i}'
                run.mkdir()
                capture_candidate(root / 'scope.json', run)
                results = run_checks(run, time.monotonic() + 60)
                outcomes.append((results[0]['execution'], results[0]['outcome']))
                baselines.append(read_json(run / 'baseline.json')['baseline_id'])
                if i == 1:
                    report = read_json(run / 'work/fp/assignments.json')
                    self.assertEqual(report['assignments'][0]['mechanical_pads'], ['MP'])
                    self.assertEqual(len(report['declarations_sha256']), 64)
            # Declaring a pad mechanical never removes a pin's pad requirement.
            self.assertEqual(outcomes, [('ok', 'defect'), ('ok', 'pass'), ('ok', 'defect')])
            self.assertEqual(len(set(baselines)), 3, 'declarations are hashed into the immutable baseline')
            write_json(source / 'declarations.json', dict(changed, footprints=[]))
            with self.assertRaises(ValueError):
                verify_source(run)
