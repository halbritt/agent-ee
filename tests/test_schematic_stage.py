"""Raw writer intent through real emission, native XML and forward annotation."""
import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_ee.schematic_stage import configuration
from agent_ee.schematic_emitter import emit_schematic
from agent_ee.stage_receipts import adopt, run_step, validate_receipt, validate_step
from agent_ee.storage import digest, read_json, write_json

try:
    from toolchain import require_toolchain
except ImportError:
    from tests.toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'fixtures/generation'
INTENT = 'design/generation/connectivity-intent.json'
SCH = 'design/project/regulator.kicad_sch'
BOARD = 'design/project/regulator.kicad_pcb'


def prepare(source):
    """Use only original repository symbols, footprints and native design fixtures."""
    generation = source / 'generation'
    generation.mkdir()
    intent = read_json(FIXTURE / 'regulator.intent.json')
    write_json(generation / 'connectivity-intent.json', intent)
    shutil.copyfile(FIXTURE / 'regulator.layout.json', generation / 'schematic-layout.json')
    shutil.copyfile(FIXTURE / 'footprints.manifest.json', generation / 'footprints.json')
    write_json(generation / 'block-seed.json', {'block': 'owned regulator', 'role': 'writer context'})
    shutil.copytree(FIXTURE / 'project/lib', source / 'project/lib')
    captures = {lib['path']: (source / 'project' / lib['path']).read_bytes() for lib in intent['libraries']}
    result, files = emit_schematic(intent, captures, read_json(generation / 'schematic-layout.json'),
        symbol_table={lib['name']: '${KIPRJMOD}/' + lib['path'] for lib in intent['libraries']},
        footprint_table={'OwnedFP': '${KIPRJMOD}/lib/OwnedFP.pretty'})
    if result['execution'] != 'ok':
        raise AssertionError(result)
    project = source / 'project'
    for name, data in files.items():
        (project / name).write_bytes(data)
    shutil.copyfile(FIXTURE / 'regulator.kicad_pcb', project / 'regulator.kicad_pcb')
    return configuration(ROOT / 'src', board=BOARD, schematic=SCH, intent=INTENT,
        seeds=['design/generation/block-seed.json'], layout='design/generation/schematic-layout.json',
        captures='design/project', footprints='design/generation/footprints.json')


class SchematicStageTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'candidate/design'
        self.source.mkdir(parents=True)
        self.config = prepare(self.source)
        self.design = self.root / 'candidate'
        self.count = 0

    def step(self, index):
        self.count += 1
        path = self.root / 'receipts' / str(self.count)
        receipt = run_step(path, self.design, self.config['implementation_steps'][index],
                           {'implementation': 1, 'source_candidate_id': 'test', 'receipt_index': self.count})
        self.assertEqual(validate_receipt(path), receipt)
        return path, receipt

    def test_each_turn_consumes_new_intent_and_keeps_native_outputs_receipted(self):
        original = digest(self.design / SCH)
        first, r1 = self.step(0)
        adopt(first, r1, self.design)
        intent = read_json(self.design / INTENT)
        next(c for c in intent['components'] if c['id'] == 'c_out')['value'] = '22u'
        write_json(self.design / INTENT, intent)
        second, r2 = self.step(0)
        self.assertNotEqual(r1['receipt_id'], r2['receipt_id'])
        self.assertNotEqual(r1['inputs'][INTENT], r2['inputs'][INTENT])
        self.assertNotEqual(original, r2['outputs'][SCH])
        adopt(second, r2, self.design)
        third, board = self.step(1)
        self.assertEqual(board['status'], 'completed', (third / 'run/console.log').read_text())
        self.assertEqual(board['inputs'][INTENT], r2['inputs'][INTENT])
        self.assertEqual(board['inputs'][SCH], r2['outputs'][SCH])
        adopt(third, board, self.design)
        self.assertEqual(digest(self.design / BOARD), board['outputs'][BOARD])
        annotation = read_json(third / 'run/out/annotation.json')
        self.assertEqual(annotation['execution'], 'ok', annotation)
        self.assertIn('--unshare-net', board['execution']['sandbox_argv'])
        xml = (third / 'run/out/netlist/design.xml').read_text()
        self.assertIn('22u', xml)
        self.assertIn('22u', (self.design / BOARD).read_text())

    def test_intent_resolution_finding(self):
        # Minimal original ESP32-named pin-accounting fixture, no vendor graphics.
        library = b'''(kicad_symbol_lib (version 20241209) (generator "agent-ee-test")
          (symbol "ESP32-S3-TEST" (property "Reference" "U") (property "Value" "ESP32-S3-TEST")
           (symbol "ESP32-S3-TEST_1_1"
            (pin input line (at 0 0 0) (length 2.54) (name "GPIO0") (number "1")))))'''
        target = self.source / 'project/lib/esp32-test.kicad_sym'
        target.write_bytes(library)
        intent = {'schema': 'agent-ee.connectivity-intent/1',
                  'libraries': [{'name': 'OwnedESP32', 'path': 'lib/esp32-test.kicad_sym', 'sha256': digest(target)}],
                  'components': [{'id': 'esp32', 'reference': 'U1', 'lib_id': 'OwnedESP32:ESP32-S3-TEST',
                                  'value': 'ESP32-S3', 'footprint': 'OwnedFP:REG_4PAD', 'units': [1]}],
                  'nets': [], 'no_connects': []}
        write_json(self.design / INTENT, intent)
        before = (self.design / SCH).read_bytes()
        path, receipt = self.step(0)
        report = read_json(path / 'run/out/emission.json')
        self.assertIn('resolution_unaccounted_pin', [f['code'] for f in report['findings']])
        self.assertEqual(report['outcome'], 'unknown')
        self.assertEqual(receipt['exit_code'], 1)
        self.assertNotEqual(receipt['status'], 'completed')
        self.assertNotIn('Traceback', (path / 'run/console.log').read_text())
        with self.assertRaisesRegex(ValueError, 'no output adopted'):
            adopt(path, receipt, self.design)
        self.assertEqual((self.design / SCH).read_bytes(), before)

    def test_annotation_refuses_stale_schematic(self):
        intent = read_json(self.design / INTENT)
        next(c for c in intent['components'] if c['id'] == 'c_out')['value'] = '33u'
        write_json(self.design / INTENT, intent)
        path, receipt = self.step(1)
        self.assertNotEqual(receipt['status'], 'completed')
        self.assertIn('differs from current intent emission', (path / 'run/console.log').read_text())
        self.assertIsNone(receipt['outputs'][BOARD])

    def test_actual_esp32_unaccounted_pin_is_a_finding(self):
        from agent_ee.symbol_capture import capture_symbols
        from agent_ee.connectivity_ir import resolve_intent
        # Read the installed native reference in memory only; publish no vendor library.
        library, _ = capture_symbols(Path('/usr/share/kicad/symbols/RF_Module.kicad_sym').read_bytes(),
                                     ['ESP32-S3-WROOM-1'], library='RF_Module')
        intent = {'schema': 'agent-ee.connectivity-intent/1',
            'libraries': [{'name': 'RF_Module', 'path': 'RF_Module.kicad_sym',
                           'sha256': hashlib.sha256(library).hexdigest()}],
            'components': [{'id': 'esp32', 'reference': 'U1', 'lib_id': 'RF_Module:ESP32-S3-WROOM-1',
                            'value': 'ESP32-S3-WROOM-1', 'footprint': '', 'units': [1]}],
            'nets': [], 'no_connects': []}
        libraries = {'RF_Module.kicad_sym': library}
        resolved = resolve_intent(intent, libraries)
        self.assertEqual(resolved['execution'], 'ok', resolved)
        pins = resolved['components'][0]['pins']
        self.assertEqual(len(pins), 41)
        intent['no_connects'] = [{'component': 'esp32', 'pin': {'number': p['number']}}
                                 for p in pins if p['number'] != '1']
        emission, files = emit_schematic(intent, libraries, read_json(FIXTURE / 'regulator.layout.json'))
        self.assertIsNone(files)
        unaccounted = [f for f in emission['findings'] if f['code'] == 'resolution_unaccounted_pin']
        self.assertEqual(len(unaccounted), 1, emission)
        self.assertEqual(unaccounted[0]['location'], '/components/esp32')
        self.assertIn('1', unaccounted[0]['message'])

    def test_unknown_pin_and_missing_capture_are_named_refusals(self):
        intent = read_json(self.design / INTENT)
        intent['no_connects'][0]['pin']['number'] = '999'
        write_json(self.design / INTENT, intent)
        path, receipt = self.step(0)
        report = read_json(path / 'run/out/emission.json')
        self.assertIn('resolution_unknown_pin', [f['code'] for f in report['findings']])
        self.assertIsNone(receipt['outputs'][SCH])
        (self.source / 'project/lib/Owned_Gen.kicad_sym').unlink()
        path, receipt = self.step(0)
        report = read_json(path / 'run/out/emission.json')
        self.assertIn('resolution_library_coverage', [f['code'] for f in report['findings']])
        self.assertNotEqual(receipt['status'], 'completed')

    def test_seed_edit_is_bound_by_the_next_receipt(self):
        first, r1 = self.step(0)
        seed = 'design/generation/block-seed.json'
        write_json(self.design / seed, {'block': 'owned regulator', 'role': 'revised writer context'})
        second, r2 = self.step(0)
        self.assertEqual(r2['status'], 'completed')
        self.assertNotEqual(r1['inputs'][seed], r2['inputs'][seed])
        self.assertEqual(r2['inputs'][seed], digest(self.design / seed))

    def test_duplicate_footprint_manifest_key_is_refused(self):
        manifest = self.source / 'generation/footprints.json'
        raw = manifest.read_text()
        self.assertIn('"schema":', raw)
        manifest.write_text(raw.replace('"schema":', '"schema": "wrong", "schema":', 1))
        path, receipt = self.step(1)
        self.assertNotEqual(receipt['status'], 'completed')
        self.assertIsNone(receipt['outputs'][BOARD])
        self.assertIn('duplicate JSON key', (path / 'run/console.log').read_text())

    def test_declared_inputs_fail_closed_and_legacy_specs_stay_unchanged(self):
        spec = self.config['implementation_steps'][0]
        for inputs in ([], 'bad', ['../escape'], [INTENT, INTENT], [SCH], [42]):
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                validate_step({**spec, 'inputs': inputs})
        legacy = {k: v for k, v in spec.items() if k != 'inputs'}
        self.assertNotIn('inputs', validate_step(legacy))
        missing = 'design/missing-seed.json'
        self.config['implementation_steps'][0]['inputs'].append(missing)
        path, receipt = self.step(0)
        self.assertIn('missing_input:' + missing, receipt['violations'])
        with self.assertRaisesRegex(ValueError, 'no output adopted'):
            adopt(path, receipt, self.design)


if __name__ == '__main__':
    unittest.main()
