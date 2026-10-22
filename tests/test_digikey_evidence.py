"""AGENTEE-254 DigiKey seated height and operating temperature as provenance-bound check evidence.

Captures here are written offline from original synthetic v4 Product
documents (see ``test_digikey_capture.product``) and replayed through the
same validator a check uses. No test contacts DigiKey.
"""
import ast
from decimal import Decimal
import hashlib
from pathlib import Path
import tempfile
import unittest

from agent_ee import digikey_record as record
from agent_ee.digikey_record import (ambient_verdict, evidence_declaration, evidence_inputs, load_package_map,
                                     package_verdict, part_evidence, replay_evidence, seated_height_bounds)
from agent_ee.storage import digest, write_json
from tests.test_digikey_capture import details, keyword, product

FETCHED = '2026-10-19T08:00:{:02d}+00:00'


def parameters(height='0.069" (1.75mm)', temperature='-40°C ~ 85°C (TA)', package='0603 (1608 Metric)'):
    """Synthetic DigiKey parameter list; ``None`` omits a parameter."""
    rows = [('Package / Case', package), ('Height - Seated (Max)', height), ('Operating Temperature', temperature)]
    return [{'ParameterId': index, 'ParameterText': name, 'ValueText': value}
            for index, (name, value) in enumerate(rows) if value is not None]


def write_capture(directory, items, queries=None):
    """Write ``digikey-capture.json`` for ``{key: product | 'not_found' | 'failed'}``; return its path.

    ``queries`` maps a key to its query when they differ (a key cannot hold '/', an MPN can)."""
    directory = Path(directory)
    (directory / 'responses').mkdir(parents=True, exist_ok=True)
    requests, parts = [], {}
    for index, key in enumerate(sorted(items), 1):
        item = items[key]
        query = (queries or {}).get(key, key)
        entry = {'key': key, 'kind': 'product', 'query': query, 'fetched_at': FETCHED.format(index),
                 'status': 'found', 'http_status': 200, 'response': None, 'reason': None}
        if item == 'failed':
            entry.update(status='failed', http_status=None, reason='synthetic transport failure')
        else:
            raw = keyword() if item == 'not_found' else details(item)
            if item == 'not_found':
                entry.update(kind='keyword', status='not_found')
            (directory / 'responses' / f'{key}.json').write_bytes(raw)
            entry['response'] = {'path': f'responses/{key}.json', 'sha256': hashlib.sha256(raw).hexdigest(),
                                 'size_bytes': len(raw)}
            status, part = record.parse_response(raw, entry['kind'], query)
            if part is not None:
                parts[key] = {**part, 'last_seen': entry['fetched_at']}
        requests.append(entry)
    doc = {'schema': record.SCHEMA, 'as_of': FETCHED.format(0), 'completed_at': FETCHED.format(len(items) + 1),
           'source': {**record._source(1.0), 'base_url': record.DEFAULT_BASE_URL},
           'token_exchanges': [{'fetched_at': FETCHED.format(0), 'http_status': 200, 'expires_in': 600}],
           'requests': requests, 'parts': parts}
    write_json(directory / record.MANIFEST, doc)
    return directory / record.MANIFEST


CHIP = {'0402': {'family': 'chip', 'pins': 2, 'body': '0402'}, '0603': {'family': 'chip', 'pins': 2, 'body': '0603'}}


def write_package_map(root, rel, footprints, packages):
    """Write an owned ``agent-ee.package-map/1`` table at ``root/rel``; return its ``{path, sha256}`` declaration.

    ``footprints`` maps a footprint ID to ``(chip code, classes)``; ``packages`` maps DigiKey text to a chip code.
    """
    table = {'schema': 'agent-ee.package-map/1', 'version': 'owned-ee274',
             'derivation': {'source': 'owned synthetic test table'},
             'rules': {'chip-imperial': 'Imperial chip code from an explicit entry'},
             'footprints': {fp: {'rule': 'chip-imperial', 'classes': classes, 'package': CHIP[code]}
                            for fp, (code, classes) in footprints.items()},
             'packages': {name: {'rule': 'chip-imperial', 'package': CHIP[code]} for name, code in packages.items()}}
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, table)
    return {'path': rel, 'sha256': digest(path)}


class DigiKeyEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ee254-digikey-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        path = write_capture(self.root / 'dk', {
            'OWNED-CAP': product('OWNED-CAP', parameters=parameters()),
            'BARE': product('BARE', parameters=parameters(height=None, temperature=None)),
            'GONE': 'not_found', 'LOST': 'failed'})
        self.declared = {'capture': 'dk/digikey-capture.json', 'capture_sha256': digest(path),
                         'parts': {'C1': 'OWNED-CAP'}}

    def files(self):
        return {p.relative_to(self.root).as_posix(): digest(p) for p in self.root.rglob('*') if p.is_file()}

    def test_seated_height_spellings_bounds_and_refusals(self):
        self.assertEqual(seated_height_bounds('0.069" (1.75mm)'), (Decimal('1.745'), Decimal('1.755')))
        self.assertEqual(seated_height_bounds('1.00mm'), (Decimal('0.995'), Decimal('1.005')))
        # The inch figure narrows the mm rounding band.
        self.assertEqual(seated_height_bounds('0.039" (1.00mm)'), (Decimal('0.995'), Decimal('1.00330')))
        for text in ('0.039" (1.75mm)', '1.0 mm', '0.039"', '0.039" (1.00mm', '1.00', '-', '0mm', '', None,
                     '1.00mm, 1.10mm', '1,00mm'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                seated_height_bounds(text)

    def test_ambient_verdicts_are_pass_defect_or_unknown(self):
        cases = {('-55°C ~ 155°C', -20, 70): 'pass', ('-40°C ~ 85°C (TA)', -40, 85): 'pass',
                 ('-40°C ~ 85°C (TA)', -41, 70): 'defect', ('-40°C ~ 85°C (TA)', 0, 85.5): 'defect',
                 ('-40°C ~ 85°C (TA), -40°C ~ 125°C (TJ)', 0, 100): 'defect',
                 ('-40°C ~ 125°C (TJ)', 0, 200): 'unknown', ('-40°C ~ 125°C (TC)', 0, 25): 'unknown',
                 ('-40°C ~ 85°C (TX)', 0, 25): 'unknown', ('85°C', 0, 25): 'unknown',
                 ('-40°C ~ 85°C, -40°C ~ 85°C (TA)', 0, 25): 'unknown',
                 ('-40°C ~ 85°C (TA), -40°C ~ 70°C (TA)', 0, 25): 'unknown', ('85°C ~ -40°C', 0, 25): 'unknown',
                 (None, 0, 25): 'unknown'}
        for (text, low, high), verdict in cases.items():
            with self.subTest(text=text, low=low, high=high):
                self.assertEqual(ambient_verdict(text, Decimal(low), Decimal(str(high)))[0], verdict)

    def test_replay_binds_capture_hash_and_captured_responses(self):
        doc, dependencies = replay_evidence(self.root, self.declared, self.files())
        evidence = part_evidence(doc, self.declared, dependencies, 'OWNED-CAP', 'seated_height')
        self.assertEqual(evidence['value'], '0.069" (1.75mm)')
        self.assertEqual(evidence['provenance']['response'],
                         {'path': 'dk/responses/OWNED-CAP.json', 'sha256': digest(self.root / 'dk/responses/OWNED-CAP.json')})
        self.assertEqual(evidence['provenance']['capture']['sha256'], self.declared['capture_sha256'])
        # Stock and price never leave the capture as check evidence.
        self.assertFalse({'quantity_available', 'unit_price'} & set(evidence))
        self.assertEqual(part_evidence(doc, self.declared, dependencies, 'OWNED-CAP',
                                       'operating_temperature')['value'], '-40°C ~ 85°C (TA)')
        self.assertIsNone(part_evidence(doc, self.declared, dependencies, 'BARE', 'seated_height')['value'])
        for key in ('GONE', 'LOST', 'NEVER'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                part_evidence(doc, self.declared, dependencies, key, 'seated_height')
        with self.assertRaisesRegex(ValueError, 'capture_sha256'):
            replay_evidence(self.root, {**self.declared, 'capture_sha256': '0' * 64}, self.files())
        files = self.files()
        files.pop('dk/responses/OWNED-CAP.json')
        with self.assertRaisesRegex(ValueError, 'not captured inputs'):
            replay_evidence(self.root, self.declared, files)
        (self.root / 'dk/responses/OWNED-CAP.json').write_bytes(b'{}')
        with self.assertRaises(ValueError):
            replay_evidence(self.root, self.declared, self.files())

    def test_declaration_and_scope_inputs(self):
        self.assertEqual(sorted(evidence_inputs(self.declared, self.root)),
                         ['dk/digikey-capture.json', 'dk/responses/BARE.json', 'dk/responses/GONE.json',
                          'dk/responses/OWNED-CAP.json'])
        self.assertEqual(evidence_inputs(self.declared), ['dk/digikey-capture.json'])
        for bad in ({**self.declared, 'extra': 1}, {**self.declared, 'capture': '../dk/digikey-capture.json'},
                    {**self.declared, 'capture': 'dk/other.json'}, {**self.declared, 'capture_sha256': 'A' * 64},
                    {**self.declared, 'parts': {}}, {**self.declared, 'parts': {' C1': 'OWNED-CAP'}},
                    {**self.declared, 'parts': {'C1': 'bad key'}}, []):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                evidence_declaration(bad, 'digikey')

    def test_package_footprint_consistency_is_pass_defect_or_unknown(self):
        # AGENTEE-274: beyond MPN/manufacturer, the DigiKey package must equal the footprint through the pinned map.
        package_map = write_package_map(self.root, 'maps/packages.json',
                                        {'Owned:C_0603': ('0603', ['capacitor']), 'Owned:C_0402': ('0402', ['capacitor'])},
                                        {'0603 (1608 Metric)': '0603'})
        declared = {**self.declared, 'package_map': package_map}
        self.assertEqual(evidence_declaration(declared, 'digikey'), declared)
        self.assertIn('maps/packages.json', evidence_inputs(declared, self.root))
        self.assertIn('maps/packages.json', evidence_inputs(declared))
        self.assertIsNone(load_package_map(self.root, self.declared, self.files()))
        table = load_package_map(self.root, declared, self.files())
        doc, dependencies = replay_evidence(self.root, declared, self.files())
        evidence = part_evidence(doc, declared, dependencies, 'OWNED-CAP', 'operating_temperature')
        self.assertEqual((evidence['package'], evidence['package_source']['rule']), ('0603 (1608 Metric)', 'package_case'))
        self.assertEqual(package_verdict(evidence, 'Owned:C_0603', table), ('pass', None))
        verdict, reason = package_verdict(evidence, 'Owned:C_0402', table)
        self.assertEqual(verdict, 'defect')
        self.assertIn('body differ', reason)
        for footprint, record_package, text in (('Owned:R_0603', '0603 (1608 Metric)', 'outside the package map'),
                                                ('Owned:C_0603', '0805 (2012 Metric)', 'outside the package map'),
                                                ('', '0603 (1608 Metric)', 'no assigned footprint'),
                                                ('Owned:C_0603', None, 'has no package')):
            with self.subTest(footprint=footprint, package=record_package):
                verdict, reason = package_verdict({**evidence, 'package': record_package}, footprint, table)
                self.assertEqual(verdict, 'unknown')
                self.assertIn(text, reason)
        # The map is pinned and must be a captured input; malformed tables refuse.
        with self.assertRaisesRegex(ValueError, 'package_map sha256'):
            load_package_map(self.root, {**declared, 'package_map': {**package_map, 'sha256': '0' * 64}})
        files = self.files()
        files.pop('maps/packages.json')
        with self.assertRaisesRegex(ValueError, 'not a captured input'):
            load_package_map(self.root, declared, files)
        (self.root / 'maps/bad.json').write_text('{"schema": "agent-ee.package-map/1"}')
        with self.assertRaises(ValueError):
            load_package_map(self.root, {**declared, 'package_map': {'path': 'maps/bad.json',
                                                                     'sha256': digest(self.root / 'maps/bad.json')}})
        for bad in ({'path': '../packages.json', 'sha256': package_map['sha256']}, {'path': 'maps/packages.json'},
                    {**package_map, 'sha256': 'A' * 64}, {**package_map, 'extra': 1}, 'maps/packages.json'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                evidence_declaration({**self.declared, 'package_map': bad}, 'digikey')

    def test_offline_record_module_has_no_network_or_process_imports(self):
        tree = ast.parse(Path(record.__file__).read_text(encoding='utf-8'))
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imported |= {node.module or '' for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertFalse(imported & {'socket', 'subprocess', 'http.client', 'urllib.request', 'os',
                                     'datasheet_cascade', 'digikey_capture'}, imported)


if __name__ == '__main__':
    unittest.main()
