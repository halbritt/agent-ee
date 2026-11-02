"""Exercise selected-source production through real parts and native consumers."""
import copy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET

from agent_ee.acquisition import power_parts as producer
from agent_ee.acquisition import reverse_blocking as variant
from agent_ee.storage import json_bytes

BASE = Path(os.environ.get('AGENT_EE_ACQUISITION_INPUTS', '/nonexistent/acquisition'))
SELECTED = Path(os.environ.get('AGENT_EE_POWER_PART_SOURCES', '/nonexistent/power-sources'))
available = (BASE / 'parts.json').is_file() and (SELECTED / 'context.json').is_file()


@unittest.skipUnless(available, 'retained exact acquisition and new power captures required')
class PowerPartsTests(unittest.TestCase):
    def produce(self, raw=None):
        return producer.inputs(BASE, SELECTED, raw or (SELECTED / 'context.json').read_bytes())

    def test_distinct_reference_preserves_selected_adc_and_locality(self):
        files, report = self.produce()
        self.assertFalse(report['accepted'])
        self.assertEqual(report['file_release'], 'not_assessed')
        self.assertEqual(report['files'], {n: sha256(b).hexdigest() for n, b in files.items()})
        parts = {p['id']: p for p in json.loads(files['parts.json'])['parts']}
        self.assertEqual(len(parts), 17)
        self.assertEqual((parts['U2']['mpn'], parts['U2']['lcsc']), ('ADS1115IDGST', 'C468683'))
        self.assertEqual({r: (parts[r]['mpn'], parts[r]['lcsc']) for r in ('U1', 'D1', 'R5', 'R6')}, {
            'U1': ('ADP7104ARDZ-3.3-R7', 'C2903961'), 'D1': ('B0520LW-7-F', 'C155367'),
            'R5': ('RC0603FR-071KL', 'C22548'), 'R6': ('RC0603FR-072K2L', 'C114662')})
        self.assertFalse(any(n.startswith(('reference-roles/', 'analysis/', 'judgments/'))
                             or Path(n).name == 'ENGINEERING-REVIEW.md' for n in files))
        original = json.loads((BASE / 'electrical-locality.json').read_bytes())
        revised = json.loads(files['electrical-locality.json'])
        self.assertEqual(len(revised['obligations']), len(original['obligations']))
        before_legs, after_legs = [], []
        for rows, legs in ((original['obligations'], before_legs), (revised['obligations'], after_legs)):
            for row in rows:
                for key, value in row.items():
                    if isinstance(value, dict) and 'from' in value and 'to' in value:
                        legs.append((row['id'], key, {k: v for k, v in value.items() if k not in ('from', 'to')}))
        self.assertEqual(len(after_legs), 18)
        self.assertEqual(after_legs, before_legs)
        for name, pin in producer.BASE.items():
            self.assertEqual(sha256((BASE / name).read_bytes()).hexdigest(), pin)

    def test_unrelated_or_substituted_named_sources_refuse(self):
        original = json.loads((SELECTED / 'context.json').read_bytes())
        for field in ('reference', 'parts'):
            doc = copy.deepcopy(original)
            if field == 'reference':
                doc[field] = 'acquisition-control'
            else:
                doc[field]['U1'] = doc[field]['D1']
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'sources require|identity differs'):
                self.produce(json_bytes(doc))
        doc = copy.deepcopy(original)
        del doc['files'][doc['supplier']['U1']['path']]
        with self.assertRaisesRegex(ValueError, 'reference differs'):
            self.produce(json_bytes(doc))

    def test_modified_captured_pdf_refuses_before_production(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'sources'
            shutil.copytree(SELECTED, root)
            doc = json.loads((root / 'context.json').read_bytes())
            record = json.loads((root / doc['parts']['U1']['path']).read_bytes())
            (root / record['artifact']['path']).write_bytes(b'not the captured PDF')
            with self.assertRaisesRegex(ValueError, 'hash mismatch|changed'):
                producer.inputs(BASE, root, json_bytes(doc))

    def test_normal_compiler_and_native_generation_bind_all_seventeen_parts(self):
        from tests.toolchain import require_toolchain
        from agent_ee import board_envelope, executor, forward_annotation, parts_stage, scaffold, schematic_emitter
        require_toolchain(native=True)
        files, preparation = self.produce()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(os.environ.get('AGENT_EE_POWER_PART_ARTIFACTS', tmp))
            if root != Path(tmp):
                root.mkdir()
            candidate = root / 'candidate'
            candidate.mkdir()
            for name, raw in files.items():
                path = candidate / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            compiled, intent = parts_stage.compile_parts(candidate, 'parts.json', 'circuit.json',
                                                        'layout.json', ratings='ratings.json')
            self.assertEqual(compiled['status'], 'produced')
            self.assertFalse(compiled['acceptance_ready'])
            self.assertEqual(len(compiled['bindings']), 17)
            self.assertEqual(compiled['binding_report']['ratings']['missing'], {})
            self.assertEqual(set(compiled['binding_report']['ratings']['derived']),
                             {'C1', 'C2', 'C3', 'C4', 'C5', 'R1', 'R2', 'R3', 'R4', 'R5', 'R6'})
            components = {c['reference']: c for c in intent['components']}
            for ref, footprint in [('U1', 'AgentEE_Power:ADP7104ARDZ_SOIC8_EP'),
                                   ('D1', 'AgentEE_Power:D_B0520LW_C155367'),
                                   ('U2', 'Package_SO:TSSOP-10_3x3mm_P0.5mm')]:
                self.assertEqual(components[ref]['footprint'], footprint)
            for ref in ('R5', 'R6'):
                self.assertIn('Power', components[ref]['part_binding'])
            captures = {lib['path']: files[lib['path']] for lib in intent['libraries']}
            cad = json.loads(files['cad.json'])
            result, emitted = schematic_emitter.emit_schematic(intent, captures, json.loads(files['layout.json']),
                symbol_table={lib['name']: '${KIPRJMOD}/' + lib['path'] for lib in intent['libraries']},
                footprint_table={lib['name']: '${KIPRJMOD}/' + lib['name'] + '.pretty' for lib in cad['libraries']})
            self.assertEqual(result['execution'], 'ok', result['findings'])
            for name, raw in emitted.items():
                (candidate / name).write_bytes(raw)
            for name, raw in [('compiled-intent.json', json_bytes(intent)), ('parts-report.json', json_bytes(compiled)),
                              ('preparation.json', json_bytes(preparation))]:
                (root / name).write_bytes(raw)
            for command, work, output in [('erc', 'erc', 'erc.json'), ('netlist', 'netlist', 'native.xml')]:
                args = (['erc', '--format', 'json'] if command == 'erc' else
                        ['export', 'netlist', '--format', 'kicadxml'])
                native = executor.execute(candidate, root / work,
                    ['kicad-cli', 'sch', *args, '--output', '/work/' + output,
                     '/candidate/acquisition.kicad_sch'], timeout=30)
                self.assertEqual(native['execution'], 'ok', native)
            self.assertEqual([v for s in json.loads((root / 'erc/erc.json').read_bytes())['sheets']
                              for v in s['violations']], [])
            xml = ET.fromstring((root / 'netlist/native.xml').read_bytes())
            native_parts = {p.attrib['ref']: p for p in xml.findall('components/comp')}
            self.assertEqual(len(native_parts), 17)
            for ref in native_parts:
                self.assertEqual(native_parts[ref].findtext('footprint'), components[ref]['footprint'])
                fields = {f.attrib['name']: f.text for f in native_parts[ref].findall('fields/field')}
                for key in ('Partnumber', 'LCSC'):
                    self.assertEqual(fields[key], components[ref]['part_binding'][key])
            self.assertEqual(native_parts['U2'].findtext('value'), 'ADS1115IDGST')
            envelope = board_envelope.validate((BASE / 'envelope.json').read_bytes(),
                profile=(BASE / 'fab-profile.json').read_bytes())
            seed = root / 'seed.kicad_pcb'
            seed.write_bytes(scaffold.board(envelope, 'power-reference-production'))
            plan = forward_annotation.plan(seed, root / 'netlist/native.xml', cad, candidate, root / 'annotation-plan')
            self.assertEqual(plan['execution'], 'ok', plan)
            annotation = forward_annotation.apply(plan, seed, root / 'netlist/native.xml', cad, candidate,
                                                  root / 'acquisition.kicad_pcb', root / 'annotation')
            self.assertEqual(annotation['execution'], 'ok', annotation)
            facts = json.loads((root / 'annotation/verify/facts/facts.json').read_bytes())
            self.assertEqual(len(facts['footprints']), 17)
            regulator = next(f for f in facts['footprints'] if f['reference'] == 'U1')
            self.assertEqual(len(regulator['pads']), 13)
            ep = [p for p in regulator['pads'] if p['number'] == '9']
            self.assertEqual(len(ep), 5)
            self.assertTrue(all(p['net'] == 'GND' for p in ep))
