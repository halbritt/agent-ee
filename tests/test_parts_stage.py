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

    def test_named_passive_value_must_match_captured_supplier_nominal(self):
        from tests.test_approved_parts import named_fixture
        for attributes, expected in (
                ([dict(attribute_name_en='Resistance', attribute_value_name='10kΩ')], None),
                ([dict(attribute_name_en='Resistance', attribute_value_name='1kΩ')], 'nominal'),
                ([], 'nominal'),
                ([dict(attribute_name_en='Resistance', attribute_value_name='10kΩ')]*2, 'nominal'),
                ([dict(attribute_name_en='Resistance', attribute_value_name='10 bananas')], 'nominal')):
            with self.subTest(attributes=attributes):
                # Named selections are used for exact sourced passives too. They
                # must not bypass the generic selection's value check.
                named_fixture(self.bundle, self.manifest, index=0, lcsc='C38',
                              mpn='SYNTHETIC-10K', package='0402', attributes=attributes)
                if expected:
                    with self.assertRaisesRegex(ValueError, expected):
                        self.compile()
                else:
                    report, compiled = self.compile()
                    self.assertEqual(report['status'], 'produced')
                    self.assertEqual(compiled['components'][0]['value'], '10k')
                    nominal, = report['named_nominals']
                    self.assertEqual(nominal['nominal'], '10000')
                    self.assertEqual(nominal['source']['sha256'], digest(self.bundle / nominal['source']['path']))

    def test_named_capacitor_and_zero_ohm_link(self):
        from tests.test_approved_parts import named_fixture
        self.intent['components'][0]['value'] = '0'
        named_fixture(self.bundle, self.manifest, index=0, lcsc='C38', mpn='SYNTHETIC-LINK', package='0402',
                      attributes=[dict(attribute_name_en='Resistance', attribute_value_name='0Ω')])
        named_fixture(self.bundle, self.manifest, index=1, lcsc='C39', mpn='SYNTHETIC-CAP', package='0402',
                      attributes=[dict(attribute_name_en='Capacitance', attribute_value_name='100nF')])
        report, _ = self.compile()
        self.assertEqual(len(report['named_nominals']), 2)
        self.intent['components'][1]['value'] = '10n'
        with self.assertRaisesRegex(ValueError, 'C1: named passive nominal'):
            self.compile()

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
        self.intent['hierarchy'].append(dict(id='notes', parent='filter', local_id='notes', file='notes.kicad_sch'))
        from agent_ee.schematic_emitter import native_sheet_paths
        leaf = native_sheet_paths(self.intent, self.layout)['notes']
        self.save()
        candidate = self.bundle
        (candidate / 'generation').mkdir()
        (candidate / 'generation/intent.json').write_text('{}\n')
        part_step = parts_stage.configuration(ROOT / 'src', bundle='.', manifest='parts.json',
            circuit='circuit.json', layout='layout.json', output='generation/intent.json')
        out = self.root / 'steps/0'
        receipt = run_step(out, candidate, part_step,
            {'implementation': 1, 'source_candidate_id': 'original-parts-stage', 'receipt_index': 0})
        self.assertEqual(receipt['status'], 'completed', (out / 'run/console.log').read_text())
        adopt(out, receipt, candidate)
        receipts = [receipt]
        # The public scaffold consumes the actual parts output. No native files
        # are copied from a fixture or repaired between these production stages.
        self.planning_inputs()
        scaffold_out = self.root / 'scaffold'
        result = subprocess.run([sys.executable, '-m', 'agent_ee.cli', 'scaffold',
            str(candidate / 'envelope.json'), str(candidate / 'parts.json'),
            '--scope', str(self.root / 'planning.scope.json'),
            '--intent', str(candidate / 'generation/intent.json'),
            '--layout', str(candidate / 'layout.json'), '--out', str(scaffold_out)],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        scaffold_receipt = read_json(scaffold_out / 'scaffold-receipt.json')
        self.assertEqual(scaffold_receipt['inputs']['files']['generation/intent.json'],
                         receipts[0]['outputs']['generation/intent.json'])
        self.assertEqual(scaffold_receipt['sheets'],
                         {'/': 'project/acquisition.kicad_sch', child: 'project/filter.kicad_sch',
                          leaf: 'project/notes.kicad_sch'})
        candidate = scaffold_out / 'inputs'
        project = candidate / 'project'
        project_before = (project / 'acquisition.kicad_pro').read_bytes()
        from agent_ee.symbol_library import children, parse_sexpr
        before = {p.name: children(parse_sexpr(p.read_bytes()), 'uuid')[0][1]
                  for p in project.glob('*.kicad_sch')}
        self.assertTrue(all(not children(parse_sexpr(p.read_bytes()), 'symbol')
                            for p in project.glob('*.kicad_sch')))
        erc_path = self.root / 'empty-erc.json'
        erc = subprocess.run(['kicad-cli', 'sch', 'erc', '--format', 'json', '--output', str(erc_path),
                              str(project / 'acquisition.kicad_sch')], capture_output=True, text=True, timeout=120)
        self.assertEqual(erc.returncode, 0, erc.stderr)
        self.assertEqual(len(read_json(erc_path)['sheets']), 3)
        self.assertFalse(any(s['violations'] for s in read_json(erc_path)['sheets']))
        config = schematic_steps(ROOT / 'src', board='project/acquisition.kicad_pcb',
            schematic='project/acquisition.kicad_sch',
            child_schematics=['project/filter.kicad_sch', 'project/notes.kicad_sch'],
            intent='generation/intent.json', layout='layout.json', captures='.', footprints='cad.json')
        for index, step in enumerate(config['implementation_steps'], 1):
            out = self.root / 'steps' / str(index)
            receipt = run_step(out, candidate, step,
                {'implementation': 1, 'source_candidate_id': 'original-parts-stage', 'receipt_index': index})
            self.assertEqual(receipt['status'], 'completed', (out / 'run/console.log').read_text())
            adopt(out, receipt, candidate)
            receipts.append(receipt)
        self.assertEqual(before, {p.name: children(parse_sexpr(p.read_bytes()), 'uuid')[0][1]
                                  for p in project.glob('*.kicad_sch')})
        self.assertEqual((project / 'acquisition.kicad_pro').read_bytes(), project_before)
        self.assertEqual(read_json(project / 'acquisition.kicad_pro')['board']['design_settings']['rules'],
                         {'min_clearance': 0.25, 'min_track_width': 0.2})
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
                              ['project/acquisition.kicad_sch', 'project/filter.kicad_sch', 'project/notes.kicad_sch'])
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

    def test_scaffold_flat_identity_and_conflicting_sources(self):
        from agent_ee.scaffold import scaffold
        from agent_ee.schematic_emitter import emit_schematic
        from agent_ee.symbol_library import children, parse_sexpr
        _, compiled = self.compile()
        write_json(self.bundle / 'bound.json', compiled)
        self.planning_inputs()

        def build(name, **kwargs):
            return scaffold(self.bundle / 'envelope.json', self.bundle / 'parts.json', self.root / name,
                scope_path=self.root / 'planning.scope.json', intent_path=self.bundle / 'bound.json',
                layout_path=kwargs.get('layout', self.bundle / 'layout.json'))

        receipt = build('flat')
        emitted, files = emit_schematic(compiled,
            {lib['path']: (self.bundle / lib['path']).read_bytes() for lib in compiled['libraries']}, self.layout)
        self.assertEqual(receipt['root_sheet_uuid'], emitted['root_uuid'])
        self.assertEqual(children(parse_sexpr((self.root / 'flat/inputs/project/acquisition.kicad_sch').read_bytes()),
                                  'uuid'), children(parse_sexpr(files['acquisition.kicad_sch']), 'uuid'))
        original = copy.deepcopy(self.manifest)
        self.manifest['parts'][0]['instances'][0]['sheet'] = '/00000000-0000-0000-0000-000000000001/'
        write_json(self.bundle / 'parts.json', self.manifest)
        with self.assertRaisesRegex(ValueError, 'parts selector differs'):
            build('wrong-selector')
        self.assertFalse((self.root / 'wrong-selector').exists())
        write_json(self.bundle / 'parts.json', original)
        write_json(self.bundle / 'layout.json', {**self.layout, 'project': 'different'})
        with self.assertRaisesRegex(ValueError, 'root differs'):
            build('wrong-root')
        self.assertFalse((self.root / 'wrong-root').exists())
        collision = self.bundle / 'project/acquisition.kicad_sch'
        write_json(collision, self.layout)
        pin = digest(collision)
        scope = read_json(self.root / 'planning.scope.json')
        scope['inputs'].append(dict(path=str(collision), at='project/acquisition.kicad_sch'))
        write_json(self.root / 'planning.scope.json', scope)
        with self.assertRaisesRegex(ValueError, 'collides with immutable'):
            build('collision', layout=collision)
        self.assertFalse((self.root / 'collision').exists())
        self.assertEqual(digest(collision), pin)

    def planning_inputs(self):
        write_json(self.bundle / 'envelope.json', read_json(ROOT / 'fixtures/board-envelope/rc.envelope.json'))
        (self.bundle / 'project').mkdir()
        write_json(self.bundle / 'project/acquisition.kicad_pro', dict(
            meta=dict(version=1, filename='acquisition.kicad_pro'),
            board=dict(design_settings=dict(rules=dict(min_clearance=0.25, min_track_width=0.2))),
            net_settings=dict(meta=dict(version=3), classes=[dict(name='Default', clearance=0.25,
                track_width=0.2, via_diameter=0.6, via_drill=0.3)])))
        scope = dict(schema='agent-ee.scope/2', name='parts and scaffold integration',
            scope='Synthetic native generation integration.', limitations=['Not electrical qualification.'],
            inputs=[dict(path=str(p), at=p.relative_to(self.bundle).as_posix())
                    for p in sorted(self.bundle.rglob('*')) if p.is_file()],
            schematic='project/acquisition.kicad_sch', board='project/acquisition.kicad_pcb',
            mutable_inputs=['project/acquisition.kicad_sch', 'project/acquisition.kicad_pcb'],
            checks=[dict(id='erc', kind='erc'), dict(id='drc', kind='drc')], requirements=['requirements.md'],
            baseline=dict(requirements=[dict(id='native', statement='Native ERC and DRC pass.',
                source=dict(path='requirements.md', locator='line 1'), checks=['erc', 'drc'])],
                targets=['requirements.md'], reference_architectures=['requirements.md']))
        write_json(self.root / 'planning.scope.json', scope)

    def test_public_build_resumes_real_parts_and_native_generation_and_rejects_changed_input(self):
        require_toolchain(native=True)
        from agent_ee.spec_record import capture as capture_spec
        self.hierarchy()
        from tests.test_approved_parts import named_fixture
        named_fixture(self.bundle, self.manifest, index=0, lcsc='C38', mpn='SYNTHETIC-10K', package='0402',
                      attributes=[dict(attribute_name_en='Resistance', attribute_value_name='10kΩ')])
        self.save()
        self.planning_inputs()
        spec = b'# Checks\n\n- Native ERC must pass.\n- Native DRC must pass.\n'
        record = capture_spec(spec, 'QUAL', {'Checks': 'CHECKS'})
        (self.bundle / 'spec.md').write_bytes(spec)
        write_json(self.bundle / 'spec-record.json', record)
        template = read_json(self.root / 'planning.scope.json')
        del template['baseline'], template['checks']
        template['schema'] = 'agent-ee.scope/3'
        template['stages'] = dict(schema='agent-ee.stages/1', order=['verification'], finding_retry_limit=3,
            subjects=dict(verification=dict(path=template['schematic'], kind='symbols', references=['R1', 'C1', 'J1'])))
        write_json(self.bundle / 'requirement-map.json', dict(schema='agent-ee.requirement-map/1',
            spec_record_sha256=digest(self.bundle / 'spec-record.json'), scope=template,
            requirements=[dict(id=r['id'], checks=[dict(id=kind, kind=kind, params={}, stage='verification')])
                          for r, kind in zip(record['requirements'], ['erc', 'drc'])], prose=[]))
        write_json(self.bundle / 'placement.json', dict(edge_connectors=[], fixed_poses=[]))
        write_json(self.bundle / 'groups.json', dict(groups=[dict(name='filter', anchor='J1', refs=['R1', 'C1', 'J1'])]))
        plan = dict(schema='agent-ee.build-plan/1', source=str(self.bundle),
            requirements=dict(map='requirement-map.json', record='spec-record.json', spec='spec.md'),
            parts=dict(manifest='parts.json', circuit='circuit.json', layout='layout.json'),
            envelope='envelope.json', placement=dict(intent='placement.json', groups='groups.json',
                budget_s=30, search=dict(seeds=[dict(seed=0, start='clustered')], steps_per_seed=100, shortlist=1),
                router=dict(checkout='/opt/kicad-route')))
        write_json(self.root / 'build.json', plan)
        out = self.root / 'build'

        def run(*args):
            return subprocess.run([sys.executable, '-m', 'agent_ee.cli', 'build', 'run',
                str(self.root / 'build.json'), '--out', str(out), *args], capture_output=True, text=True, timeout=120)

        result = run('--through', 'parts')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        first = read_json(out / 'build-report.json')
        self.assertFalse(first['accepted'])
        self.assertEqual([s['stage'] for s in first['completed']], ['requirements', 'parts'])
        (out / 'CANCEL').touch()
        result = run('--resume', '--through', 'schematic')
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        interrupted = read_json(out / 'build-report.json')
        self.assertEqual(interrupted['status'], 'interrupted')
        self.assertEqual(interrupted['completed'], first['completed'])
        (out / 'CANCEL').unlink()
        result = run('--resume', '--through', 'schematic')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        second = read_json(out / 'build-report.json')
        self.assertEqual(second['completed'][:2], first['completed'])
        self.assertFalse(second['accepted'])
        self.assertEqual([s['stage'] for s in second['completed']], ['requirements', 'parts', 'scaffold', 'schematic'])
        candidate = out / second['candidate']
        import pcbnew
        self.assertEqual({f.GetReference() for f in pcbnew.LoadBoard(str(candidate / 'project/acquisition.kicad_pcb')).GetFootprints()},
                         {'R1', 'C1', 'J1'})
        project = read_json(candidate / 'project/acquisition.kicad_pro')
        self.assertEqual(project['board']['design_settings']['rules']['min_clearance'], 0.25)
        # A schematic checkpoint cannot be mistaken for a manufacturing pass.
        result = run('--resume')
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        incomplete = read_json(out / 'build-report.json')
        self.assertFalse(incomplete['accepted'])
        self.assertIn('manufacturing coverage missing', incomplete['reason'])
        self.assertEqual(incomplete['completed'], second['completed'])
        pin = digest(out / 'build-report.json')
        board_path = candidate / 'project/acquisition.kicad_pcb'
        native_bytes = board_path.read_bytes()
        board_path.write_bytes(native_bytes + b'\n')
        result = run('--resume', '--through', 'schematic')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('stage identity changed', result.stderr + result.stdout)
        self.assertEqual(digest(out / 'build-report.json'), pin)
        board_path.write_bytes(native_bytes)
        (self.bundle / 'spec.md').write_bytes(spec + b'\nChanged engineering input.\n')
        result = run('--resume', '--through', 'schematic')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('identity', result.stderr + result.stdout)
        self.assertEqual(digest(out / 'build-report.json'), pin)
        (self.bundle / 'spec.md').write_bytes(spec)
        self.intent['components'][0]['value'] = '1k'
        self.save()
        out = self.root / 'bad-electrical-build'
        result = run('--through', 'schematic')
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        bad = read_json(out / 'build-report.json')
        self.assertFalse(bad['accepted'])
        self.assertEqual([s['stage'] for s in bad['completed']], ['requirements'])
        self.assertIn('compile-parts: failed', bad['reason'])
        failed_stage = out / bad['attempts'][-1]['directory']
        self.assertIn('nominal', read_json(failed_stage / 'receipts/0/run/out/report.json')['reason'])
        self.assertFalse(list(failed_stage.rglob('*.kicad_pcb')))
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
