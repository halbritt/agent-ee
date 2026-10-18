"""Owned pod symbol captures and exact datasheet table identities (AGENTEE-168)."""
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path

from agent_ee.symbol_capture import verify_capture
from agent_ee.symbol_context import build_context, check_pin_references
from agent_ee.symbol_checks import capability
from agent_ee.storage import digest, read_json, write_json
from toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]
POD = ROOT / 'fixtures/telemetry-pod'
# Independent transcription of Nexperia Table 2 (p1), ST Table 2 (p24).
EXPECTED = {
    'PESD1CAN-UX': [('cathode 1', 'passive'), ('cathode 2', 'passive'), ('common cathode', 'passive')],
    'LSM6DSO32': [('SDO', 'bidirectional'), ('SDx', 'bidirectional'), ('SCx', 'bidirectional'),
        ('INT1', 'bidirectional'), ('VDDIO', 'power_in'), ('GND', 'power_in'), ('GND', 'power_in'),
        ('VDD', 'power_in'), ('INT2', 'bidirectional'), ('NC', 'no_connect'), ('NC', 'no_connect'),
        ('CS', 'input'), ('SCL', 'input'), ('SDA', 'bidirectional')],
}


def query_inputs():
    query = read_json(POD / 'symbols/pins.json')
    return query, {lib['path']: (POD / lib['path']).read_bytes() for lib in query['libraries']}


def pod_emission_inputs():
    """A pin exercise, not a functional telemetry pod circuit."""
    query, captured = query_inputs()
    pod_bytes = captured.pop('libs/Pod.kicad_sym')
    captured['lib/Pod.kicad_sym'] = pod_bytes
    power = (ROOT / 'fixtures/generation/project/lib/Owned_Power.kicad_sym').read_bytes()
    captured['lib/Owned_Power.kicad_sym'] = power
    libraries = [dict(query['libraries'][0], path='lib/Pod.kicad_sym'),
                 dict(name='OwnedPower', path='lib/Owned_Power.kicad_sym', sha256=hashlib.sha256(power).hexdigest())]
    assignment = read_json(POD / 'symbols/assignments.json')
    components = [dict(id=c['reference'], reference=c['reference'], lib_id=c['lib_id'],
                       value=c['lib_id'].split(':')[1], footprint=c['footprint'], units=[1])
                  for c in assignment['components']]
    components += [dict(id=ref, reference=ref, lib_id='OwnedPower:PWR_FLAG', value='PWR_FLAG',
                        footprint='', units=[1]) for ref in ('#FLG01', '#FLG02')]
    nets = [dict(id='a', name='PIN_EXERCISE_A', terminals=[dict(component=r, pin=dict(number='1')) for r in ('D1', 'U1')]),
            dict(id='b', name='PIN_EXERCISE_B', terminals=[dict(component=r, pin=dict(number='2')) for r in ('D1', 'U1')]),
            dict(id='ground', name='GROUND', terminals=[dict(component=r, pin=dict(number=n))
                for r, n in [('D1', '3'), ('U1', '6'), ('U1', '7'), ('#FLG01', '1')]]),
            dict(id='supply', name='SUPPLY', terminals=[dict(component=r, pin=dict(number=n))
                for r, n in [('U1', '5'), ('U1', '8'), ('#FLG02', '1')]])]
    intent = dict(schema='agent-ee.connectivity-intent/1', libraries=libraries, components=components,
                  nets=nets, no_connects=[dict(component='U1', pin=dict(number=str(n)))
                    for n in (3, 4, 9, 10, 11, 12, 13, 14)])
    layout = dict(schema='agent-ee.schematic-layout/1', project='pod_owned_symbols',
                  namespace='78a94e4d-2e30-45ac-a6ae-282fcb365524')
    return intent, captured, layout


class PodSymbolTests(unittest.TestCase):
    def test_capture_rederives_and_manifest_binds_library_and_facts(self):
        manifest = read_json(POD / 'symbols/capture-manifest.json')
        receipt = read_json(POD / manifest['capture']['path'])
        subset = (POD / manifest['subset']['path']).read_bytes()
        source = (POD / manifest['library']['path']).read_bytes()
        verify_capture(receipt, subset, source)
        for entry in (manifest['capture'], manifest['subset'], manifest['library']):
            self.assertEqual(digest(POD / entry['path']), entry['sha256'])
        self.assertEqual(receipt['selected'], sorted(EXPECTED))
        from agent_ee.symbol_library import children, parse_library
        for symbol in parse_library(source).values():
            self.assertFalse(children(symbol, 'extends'))
            for unit in children(symbol, 'symbol'):
                self.assertTrue(all(n[0] in ('rectangle', 'polyline', 'pin') for n in unit[2:]))
        from agent_ee.datasheet_facts import load_facts
        for sheet in manifest['datasheets']:
            self.assertEqual(digest(POD / sheet['facts']['path']), sheet['facts']['sha256'])
            facts = load_facts(POD, sheet['facts']['path'])
            names = [f for f in facts if f['kind'] == 'pin_name']
            numbers = [f for f in facts if f['kind'] == 'pin_number']
            expected = EXPECTED[sheet['part_number']]
            self.assertEqual([(f['pin'], f['value']) for f in names],
                             [(str(i), p[0]) for i, p in enumerate(expected, 1)])
            self.assertEqual([f['value'] for f in numbers], [str(i) for i in range(1, len(expected)+1)])
            self.assertTrue(all(f['source']['sha256'] == sheet['sha256'] and
                f['source']['page'] == sheet['page'] and f['source']['quote'] == f['value'] for f in facts))
            self.assertTrue(sheet['version'])
            self.assertFalse((POD / sheet['path']).exists(), 'vendor PDFs must remain untracked')

    def test_both_symbols_match_every_captured_pin_attribute(self):
        query, files = query_inputs()
        result = check_pin_references(query['libraries'], files, query['references'])
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertEqual(len(result['references']), 17)
        for name, expected in EXPECTED.items():
            declarations = sorted((r for r in query['references'] if r['lib_id'] == 'Pod:' + name),
                                  key=lambda r: int(r['pin']['number']))
            self.assertEqual([(r['pin']['number'], r['expected']) for r in declarations],
                             [(str(i), dict(name=n, electrical_type=t))
                              for i, (n, t) in enumerate(expected, 1)])
            context = build_context(query['libraries'], files, 'Pod:' + name)
            actual = sorted(context['pins'], key=lambda p: int(p['number']))
            self.assertEqual([(p['name'], p['electrical_type']) for p in actual], expected)
            self.assertEqual(context['bbox_by_unit']['1']['coverage'], 'complete')

    def test_swapped_pin_1_2_is_defect_for_each_symbol(self):
        for ref, symbol in [('D1', 'PESD1CAN-UX'), ('U1', 'LSM6DSO32')]:
            query, files = query_inputs()
            path = query['libraries'][0]['path']
            data = files[path]
            # Alter only the selected symbol's original pin-number nodes.
            start = data.index(('(symbol "' + symbol + '"').encode())
            before, tail = data[:start], data[start:]
            tail = tail.replace(b'(number "1"', b'(number "SWAP"', 1)
            tail = tail.replace(b'(number "2"', b'(number "1"', 1)
            data = before + tail.replace(b'(number "SWAP"', b'(number "2"', 1)
            files[path] = data
            query['libraries'][0]['sha256'] = hashlib.sha256(data).hexdigest()
            result = check_pin_references(query['libraries'], files, query['references'])
            self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'))
            defects = [r for r in result['references'] if r['status'] == 'defect']
            self.assertEqual([(r['reference'], r['pin']['number']) for r in defects], [(ref, '1'), (ref, '2')])

    def test_type_change_is_defect_and_missing_or_changed_library_is_unknown(self):
        query, files = query_inputs()
        path = query['libraries'][0]['path']
        files[path] = files[path].replace(b'(pin passive line', b'(pin input line', 1)
        result = check_pin_references(query['libraries'], files, query['references'])
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'))
        query['libraries'][0]['sha256'] = hashlib.sha256(files[path]).hexdigest()
        result = check_pin_references(query['libraries'], files, query['references'])
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'))
        self.assertEqual(result['references'][0]['actual'], [dict(name='cathode 1', electrical_type='input')])
        self.assertEqual(check_pin_references(query['libraries'], {}, query['references'])['outcome'], 'unknown')

    def test_attribute_query_is_immutable_but_legacy_query_stays_candidate_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            query, files = query_inputs()
            (root / 'query.json').write_text(json.dumps(query))
            paths = ['query.json', *files]
            result = capability(dict(declaration='query.json'), paths, root)
            self.assertEqual(result['immutable_inputs'], sorted(paths))
            self.assertIn('symbol:pin-attributes/1', result['predicates'])
            for ref in query['references']:
                ref.pop('expected')
            (root / 'query.json').write_text(json.dumps(query))
            result = capability(dict(declaration='query.json'), paths, root)
            self.assertEqual(result['immutable_inputs'], sorted(files))
            self.assertEqual(result['predicates'], ['symbol:pin-existence/1'])

    def test_attribute_expectations_are_in_the_scope3_baseline(self):
        from agent_ee.baseline import compile_baseline
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            query, libraries = query_inputs()
            for path, data in libraries.items():
                (root/path).parent.mkdir(parents=True, exist_ok=True)
                (root/path).write_bytes(data)
            for path in ('req', 'target', 'ref'):
                (root/path).write_text('Owned pin-attribute contract')
            scope = dict(schema='agent-ee.scope/3', checks=[dict(id='pins', kind='symbol_pins',
                params=dict(declaration='query.json'))], baseline=dict(requirements=[dict(id='R1',
                intent='acceptance', statement='Exact pin names and types',
                source=dict(path='req', locator='R1'), checks=['pins'])],
                targets=['target'], reference_architectures=['ref']))
            def baseline():
                write_json(root/'query.json', query)
                files = {str(p.relative_to(root)): digest(p) for p in root.rglob('*') if p.is_file()}
                return compile_baseline(scope, files, root)
            original = baseline()
            self.assertIn('query.json', original['files'])
            self.assertIn('symbol:pin-attributes/1', original['coverage']['checks']['pins']['predicates'])
            query['references'][0]['expected']['name'] = 'changed expectation'
            self.assertNotEqual(original['baseline_id'], baseline()['baseline_id'])

    def test_offline_coordinator_checks_pin_contracts(self):
        require_toolchain()
        from agent_ee.checks import Ctx
        from agent_ee.symbol_checks import check_symbol_pins
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = root / 'inputs'
            inputs.mkdir()
            query, files = query_inputs()
            for path, data in files.items():
                (inputs/path).parent.mkdir(parents=True, exist_ok=True)
                (inputs/path).write_bytes(data)
            write_json(inputs/'pins.json', query)
            spec = dict(id='pod-pins', kind='symbol_pins', params=dict(declaration='pins.json'))
            manifest = dict(scope=dict(checks=[spec]), candidate_id='owned-pod-symbols',
                            files={str(p.relative_to(inputs)): digest(p) for p in inputs.rglob('*') if p.is_file()})
            ctx = Ctx(root, manifest, spec, time.monotonic()+60)
            check_symbol_pins(ctx)
            self.assertEqual((ctx.result['execution'], ctx.result['outcome']), ('ok', 'pass'))
            self.assertTrue((ctx.work/'pins.json').is_file())
