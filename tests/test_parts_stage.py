"""Selected parts must reach real native files, with stale/contradictory inputs rejected.

Supplier and CAD captures here are original synthetic test data, not sourced
engineering qualification. Native execution is mandatory in the strict gate.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from agent_ee import parts_stage
from agent_ee.schematic_stage import configuration as schematic_steps
from agent_ee.stage_receipts import adopt, run_step
from agent_ee.storage import digest, identity, read_json, write_json
from tests.test_parts_subset import captured_fixture
from tests.toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]


class PartsProducerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.bundle = self.root / 'candidate/parts'
        self.bundle.mkdir(parents=True)
        self.manifest = captured_fixture(self, self.bundle, trio=True)
        # Complete synthetic acquisition envelopes, distinct from real datasheets.
        for part in self.manifest['parts']:
            sid = part['id']
            url = 'https://example.org/synthetic-' + sid + '.pdf'
            filename = hashlib.sha256(url.encode()).hexdigest() + '.pdf'
            folder = self.bundle / 'datasheets' / sid
            folder.mkdir(parents=True)
            (folder / filename).write_bytes(b'%PDF-1.4\nOriginal synthetic envelope only.\n%%EOF\n')
            sha = digest(folder / filename)
            manifest_path = folder / 'manifest.json'
            write_json(manifest_path, dict(schema='agent-ee.datasheet-capture/1',
                bindings_sha256=identity([dict(id=sid, datasheet_url=url)]), outcome='captured',
                parts=[dict(id=sid, url=url, final_url=url, fetched_at='2026-10-24T00:00:00Z',
                            sha256=sha, path=filename, missing=False, reason=None)]))
            part['datasheet'] = dict(status='captured', required=True,
                artifact=dict(path=str((folder / filename).relative_to(self.bundle)), sha256=sha),
                provenance=dict(path=str(manifest_path.relative_to(self.bundle)),
                                sha256=digest(manifest_path), locator='parts[id=' + sid + ']'))
        library = read_json(self.bundle / 'assignments.json')['libraries']
        self.intent = dict(schema='agent-ee.connectivity-intent/1', libraries=library,
            components=[dict(id=ref, reference=ref, lib_id=symbol, value=value,
                             footprint='', units=[1]) for ref, symbol, value in
                        [('R1', 'Owned:R', '10k'), ('C1', 'Owned:C', '100n'),
                         ('J1', 'Owned:Conn3', 'connector')]],
            nets=[dict(id=name, name=name, terminals=[dict(component=r, pin={'number': p})
                  for r, p in pins]) for name, pins in
                  [('IN', [('J1', '1'), ('R1', '1')]),
                   ('OUT', [('R1', '2'), ('C1', '1'), ('J1', '2')]),
                   ('GND', [('J1', '3'), ('C1', '2')])]], no_connects=[])
        self.layout = dict(schema='agent-ee.schematic-layout/1',
            namespace='6f1c2d3e-4a5b-5c6d-8e7f-0a1b2c3d4e5f', project='acquisition')
        self.save()

    def save(self):
        write_json(self.bundle / 'parts.json', self.manifest)
        write_json(self.bundle / 'circuit.json', self.intent)
        write_json(self.bundle / 'layout.json', self.layout)

    def compile(self):
        self.save()
        return parts_stage.compile_parts(self.bundle, 'parts.json', 'circuit.json', 'layout.json')

    def test_selected_fields_and_copper_identity_come_from_qualified_parts(self):
        before = copy.deepcopy(self.intent)
        report, compiled = self.compile()
        self.assertEqual(self.intent, before)
        self.assertEqual(report['status'], 'produced')
        self.assertFalse(report['acceptance_ready'])
        self.assertEqual(len(report['bindings']), 3)
        resistor = next(c for c in compiled['components'] if c['reference'] == 'R1')
        self.assertEqual(resistor['footprint'], 'Owned:R2')
        self.assertEqual(resistor['part_binding']['LCSC'], 'C1')
        self.assertEqual(resistor['part_binding']['MPN'], 'R-C1')
        self.assertEqual(compiled['nets'], before['nets'])
        self.assertIn('snapshot/catalog.sqlite3', report['dependencies'])

    def test_selected_nominal_cannot_disagree_with_circuit_value(self):
        self.intent['components'][0]['value'] = '1k'
        with self.assertRaisesRegex(ValueError, 'nominal'):
            self.compile()

    def test_contradictory_supplier_and_cad_fields_are_not_overwritten(self):
        original = copy.deepcopy(self.intent)
        for key, value in [('footprint', 'Owned:Wrong'), ('lib_id', 'Owned:C'),
                           ('part_binding', {'LCSC': 'C999'})]:
            self.intent = copy.deepcopy(original)
            self.intent['components'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.compile()

    def test_required_missing_datasheet_or_wrong_sheet_prevents_production(self):
        self.manifest['parts'][0]['datasheet'] = dict(status='unavailable', required=True, reason='missing')
        with self.assertRaisesRegex(ValueError, 'provenance'):
            self.compile()
        self.manifest['parts'][0]['datasheet']['required'] = False
        self.manifest['parts'][0]['instances'][0]['sheet'] = '/wrong/'
        with self.assertRaisesRegex(ValueError, 'instance'):
            self.compile()

    def test_missing_part_or_unaccounted_pin_is_not_silently_accepted(self):
        self.intent['components'][0]['reference'] = 'R99'
        with self.assertRaisesRegex(ValueError, 'instance'):
            self.compile()
        self.intent['components'][0]['reference'] = 'R1'
        self.intent['nets'][0]['terminals'].pop()
        with self.assertRaisesRegex(ValueError, 'unaccounted_pin'):
            self.compile()

    def test_stale_captured_library_refuses_even_when_template_hash_is_updated(self):
        lib = self.intent['libraries'][0]
        path = self.bundle / lib['path']
        path.write_bytes(path.read_bytes() + b'\n')
        lib['sha256'] = digest(path)
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.compile()

    def test_mixed_named_and_generic_parts_reuse_captured_datasheet_bindings(self):
        from tests.test_approved_parts import named_fixture
        record = named_fixture(self.bundle, self.manifest, index=2, lcsc='C38',
                               mpn='SYNTHETIC-CONNECTOR', package='TEST-TH')
        report, compiled = self.compile()
        header = next(c for c in compiled['components'] if c['reference'] == 'J1')
        self.assertEqual(header['part_binding']['MPN'], 'SYNTHETIC-CONNECTOR')
        self.assertEqual(read_json(self.bundle / record['datasheets']['path'])['parts'][0]['sha256'],
                         json.loads(header['part_binding']['PARAM_Datasheet'])['sha256'])
        self.assertEqual(len(report['bindings']), 3)

    def test_explicit_deviation_source_is_bound_and_missing_source_refuses(self):
        write_json(self.bundle / 'deviations.json', dict(schema='agent-ee.part-deviations/1', deviations=[]))
        report, _ = parts_stage.compile_parts(self.bundle, 'parts.json', 'circuit.json', 'layout.json',
                                              deviations='deviations.json')
        self.assertEqual(report['deviations_source'], 'deviations.json')
        self.assertEqual(report['dependencies']['deviations.json'], digest(self.bundle / 'deviations.json'))
        with self.assertRaises(FileNotFoundError):
            parts_stage.compile_parts(self.bundle, 'parts.json', 'circuit.json', 'layout.json',
                                      ratings='missing-ratings.json')

    def hierarchy(self):
        self.intent['schema'] = 'agent-ee.connectivity-intent/2'
        self.intent['hierarchy'] = [dict(id='root', parent=None, local_id='root', file='acquisition.kicad_sch'),
            dict(id='filter', parent='root', local_id='filter', file='filter.kicad_sch')]
        self.intent['ports'] = []
        # Whole circuit on a child: exercises actual native occurrence selectors.
        for category in ('components', 'nets', 'no_connects'):
            for item in self.intent[category]:
                item['sheet'] = 'filter'
        from agent_ee.schematic_emitter import native_sheet_paths
        child = native_sheet_paths(self.intent, self.layout)['filter']
        for part in self.manifest['parts']:
            part['instances'][0]['sheet'] = child
        self.save()
        return child

    def test_hierarchy_binds_every_physical_component(self):
        child = self.hierarchy()
        report, compiled = self.compile()
        self.assertEqual(len(report['bindings']), 3)
        self.assertEqual({c['reference'] for c in compiled['components'] if 'part_binding' in c},
                         {'R1', 'C1', 'J1'})
        self.assertEqual({b['instances'][0]['sheet'] for b in report['bindings']}, {child})

    def test_public_cli_produces_bound_intent_and_retains_electrical_refusal(self):
        def run(out):
            return subprocess.run([sys.executable, '-m', 'agent_ee.cli', 'parts', 'compile',
                '--bundle', str(self.bundle), '--manifest', 'parts.json', '--circuit', 'circuit.json',
                '--layout', 'layout.json', '--out', str(out)], capture_output=True, text=True)
        out = self.root / 'cli-produced'
        result = run(out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read_json(out / 'report.json')['output_sha256'], digest(out / 'connectivity-intent.json'))
        self.intent['components'][0]['value'] = '1k'
        self.save()
        refused = self.root / 'cli-refused'
        result = run(refused)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse((refused / 'connectivity-intent.json').exists())
        self.assertIn('nominal', read_json(refused / 'report.json')['reason'])

    def test_native_hierarchy_parts_to_schematic_to_board(self):
        require_toolchain(native=True)
        child = self.hierarchy()
        candidate = self.bundle
        project = candidate / 'project'
        project.mkdir()
        for name in ('acquisition.kicad_sch', 'filter.kicad_sch'):
            shutil.copyfile(ROOT / 'fixtures/stages/empty.kicad_sch', project / name)
        shutil.copyfile(ROOT / 'fixtures/stages/empty.kicad_pcb', project / 'acquisition.kicad_pcb')
        (candidate / 'generation').mkdir()
        (candidate / 'generation/intent.json').write_text('{}\n')
        part_step = parts_stage.configuration(ROOT / 'src', bundle='.', manifest='parts.json',
            circuit='circuit.json', layout='layout.json', output='generation/intent.json')
        config = schematic_steps(ROOT / 'src', board='project/acquisition.kicad_pcb',
            schematic='project/acquisition.kicad_sch', child_schematics=['project/filter.kicad_sch'],
            intent='generation/intent.json', layout='layout.json', captures='.', footprints='cad.json')
        receipts = []
        for index, step in enumerate([part_step, *config['implementation_steps']]):
            out = self.root / 'steps' / str(index)
            receipt = run_step(out, candidate, step,
                {'implementation': 1, 'source_candidate_id': 'original-parts-stage', 'receipt_index': index})
            self.assertEqual(receipt['status'], 'completed', (out / 'run/console.log').read_text())
            adopt(out, receipt, candidate)
            receipts.append(receipt)
        self.assertEqual(receipts[1]['inputs']['generation/intent.json'],
                         receipts[0]['outputs']['generation/intent.json'])
        self.assertEqual(receipts[2]['inputs']['project/filter.kicad_sch'],
                         receipts[1]['outputs']['project/filter.kicad_sch'])
        from agent_ee.native_netlist import read_native_inventory
        xml = self.root / 'steps/2/run/out/netlist/design.xml'
        graph = read_native_inventory(xml)
        inventory = graph['components']
        self.assertEqual({c['sheet'] for c in inventory.values()}, {child})
        self.assertEqual({ref: c['properties']['LCSC'] for ref, c in inventory.items()},
                         {'R1': 'C1', 'C1': 'C2', 'J1': 'C3'})
        self.assertEqual({frozenset(n['terminals']) for n in graph['nets'].values()},
                         {frozenset([('J1', '1'), ('R1', '1')]),
                          frozenset([('R1', '2'), ('C1', '1'), ('J1', '2')]),
                          frozenset([('J1', '3'), ('C1', '2')])})
        from agent_ee.parts_subset import evaluate
        membership = evaluate(candidate, 'parts.json', xml,
                              ['project/acquisition.kicad_sch', 'project/filter.kicad_sch'])
        write_json(self.root / 'native-membership.json', membership)
        self.assertEqual(membership['outcome'], 'pass', membership)
        import pcbnew
        board = pcbnew.LoadBoard(str(project / 'acquisition.kicad_pcb'))
        self.assertEqual({f.GetReference(): {p.GetName(): p.GetText() for p in f.GetFields()}['MPN']
                          for f in board.GetFootprints()},
                         {'R1': 'R-C1', 'C1': 'C-C2', 'J1': 'J-C3'})
        evidence = os.environ.get('AGENT_EE_PARTS_EVIDENCE')
        if evidence:
            shutil.copytree(self.root, Path(evidence) / self._testMethodName)

    def test_root_capture_stage_cannot_adopt_over_an_immutable_library(self):
        require_toolchain(native=True)
        library = self.intent['libraries'][0]['path']
        before = digest(self.bundle / library)
        step = parts_stage.configuration(ROOT / 'src', bundle='.', manifest='parts.json',
            circuit='circuit.json', layout='layout.json', output=library)
        out = self.root / 'refused-step'
        receipt = run_step(out, self.bundle, step,
            {'implementation': 1, 'source_candidate_id': 'original-parts-stage', 'receipt_index': 0})
        self.assertEqual(receipt['status'], 'failed')
        self.assertIn('immutable dependency', read_json(out / 'run/out/report.json')['reason'])
        with self.assertRaisesRegex(ValueError, 'no output adopted'):
            adopt(out, receipt, self.bundle)
        self.assertEqual(digest(self.bundle / library), before)


if __name__ == '__main__':
    unittest.main()
