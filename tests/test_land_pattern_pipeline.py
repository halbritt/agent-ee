"""Registered land-pattern check through capture, sandbox, seal and witness verification."""
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from agent_ee.footprint_library import canonical
from agent_ee.storage import read_json, write_json
from test_land_pattern import Workspace, lib
from land_pattern_fixtures import SOT_KICAD, sot_supplier


class LandPatternPipelineTests(unittest.TestCase):
    def test_pass_defect_seal_witness_and_immutable_dependency_drift(self):
        from toolchain import require_toolchain
        require_toolchain()
        from agent_ee import executor
        from agent_ee.baseline import freeze_evidence, validate_witnesses
        from agent_ee.candidate import capture as capture_candidate, verify_snapshot, verify_source
        from agent_ee.capabilities import compile_coverage
        from agent_ee.checks import run_checks
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            overlay = root / 'app'
            shutil.copytree(Path(__file__).resolve().parents[1] / 'src', overlay)
            previous = executor.PACKAGE_ROOT
            executor.PACKAGE_ROOT = overlay
            self.addCleanup(setattr, executor, 'PACKAGE_ROOT', previous)
            source = root / 'source'
            ws = Workspace(source)
            ws.library({'SOT': SOT_KICAD})
            for name in ('board', 'schematic', 'target', 'reference'):
                ws.write(name, b'Owned land-pattern fixture: ' + name.encode())
            spec = dict(id='lp', kind='land_pattern', params=dict(criteria='data/criteria.json'))
            scope = dict(schema='agent-ee.scope/3', name='owned-land-pattern', scope='Declared land patterns only',
                         board='data/board', schematic='data/schematic', inputs=[dict(path='source', at='data')],
                         checks=[spec], baseline=dict(requirements=[dict(id='R1', intent='acceptance',
                             statement='Bound supplier land patterns match the selected footprints',
                             source=dict(path='data/criteria.json', locator='requirements'), checks=['lp'])],
                             targets=['data/target'], reference_architectures=['data/reference']))
            write_json(root / 'scope.json', scope)
            for i, (numbers, expected) in enumerate(((('1', '2', '3'), 'pass'), (('2', '1', '3'), 'defect'))):
                ws.requirements = []
                binding = ws.supplier('U1', 'C900000003', sot_supplier(numbers))
                ws.require('U1', 'C900000003', dict(binding, provenance='data/' + binding['provenance']),
                           dict(lib('SOT')), rotation='90')
                criteria = ws.criteria(library_manifest='data/lib/manifest.json')
                ws.write('criteria.json', canonical(criteria))
                # Criteria paths are candidate-relative under the declared input mount.
                for path in ('supplier/U1.provenance.json',):
                    data = (source / path).read_bytes().replace(b'"supplier/U1.raweasy.json"',
                                                                 b'"data/supplier/U1.raweasy.json"')
                    ws.write(path, data)
                import hashlib
                criteria['requirements'][0]['supplier']['sha256'] = hashlib.sha256(
                    (source / 'supplier/U1.provenance.json').read_bytes()).hexdigest()
                manifest = read_json(source / 'lib/manifest.json')
                for member in manifest['libraries'][0]['members']:
                    member['path'] = 'data/' + member['path'] if not member['path'].startswith('data/') else member['path']
                ws.write('lib/manifest.json', canonical(manifest))
                ws.write('criteria.json', canonical(criteria))
                run = root / f'run{i}'
                run.mkdir()
                capture_candidate(root / 'scope.json', run)
                coverage = compile_coverage(read_json(run / 'candidate.json')['scope'],
                                            read_json(run / 'candidate.json')['files'], run / 'inputs')
                self.assertIn('data/supplier/U1.raweasy.json', coverage['checks']['lp']['immutable_inputs'])
                self.assertNotIn('data/board', coverage['checks']['lp']['immutable_inputs'])
                results = run_checks(run, time.monotonic() + 120)
                self.assertEqual((results[0]['execution'], results[0]['outcome']), ('ok', expected), results)
                freeze_evidence(run, results)
                verify_snapshot(run)
                if expected == 'defect':
                    witness = [dict(requirement_id='R1', check_id='lp')]
                    evidence = [dict(path='checks/lp.json', locator='outcome')]
                    validate_witnesses(run, witness, evidence)
                    report = read_json(run / 'work/lp/land-pattern.json')
                    report['outcome'] = 'pass'
                    write_json(run / 'work/lp/land-pattern.json', report)
                    with self.assertRaisesRegex(ValueError, 'evidence changed'):
                        validate_witnesses(run, witness, evidence)
                    (source / 'supplier/U1.raweasy.json').write_bytes(b'drifted')
                    with self.assertRaises(ValueError):
                        verify_source(run)


if __name__ == '__main__':
    unittest.main()
