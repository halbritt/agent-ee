"""PH source-model regression; pure source fixtures, no native qualification."""
from decimal import Decimal
from pathlib import Path
import json
import tempfile
import unittest

from agent_ee import acquisition_connector_geometry as connector
from agent_ee import acquisition_power_repair as power, acquisition_resistor as resistor
from agent_ee import acquisition_source_supplement as supplement
from agent_ee.spec_record import sha256
from agent_ee.storage import json_bytes

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / 'fixtures/acquisition-qualification/geometry-model.json'


def variants():
    original = ORIGINAL.read_bytes()
    murata = supplement.corrected_model(original)
    result = [original, murata]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / 'analysis').mkdir()
        for selected in ('620', '470'):
            (root / 'analysis/geometry-model.json').write_bytes(murata)
            (root / 'engineering-calculations.json').write_bytes(json_bytes({'sources': {}}))
            power.finalize_geometry(root, selected)
            model = (root / 'analysis/geometry-model.json').read_bytes()
            result.extend((model, resistor.model_variant(model)))
    return result


class ConnectorGeometryTests(unittest.TestCase):
    def test_rust_fixture_uses_the_exact_python_model_bytes_and_source_closure(self):
        from agent_ee import acquisition_mechanics as mechanics
        rows = json.loads((ORIGINAL.parent/'mechanics-derived-test-cases.json').read_bytes())['cases']
        expected = {}
        for raw in variants():
            if sha256(raw) in resistor.MODELS:
                expected[sha256(raw)] = raw
            corrected = connector.corrected_model(raw)
            expected[sha256(corrected)] = corrected
        self.assertEqual({row['sha256'] for row in rows}, set(expected))
        self.assertEqual(len(rows), len(expected))
        for row in rows:
            with self.subTest(model=row['sha256']):
                self.assertEqual(row['model_raw'].encode(), expected[row['sha256']])
                self.assertEqual(row['sources'], {
                    'spec.md': 'd'*64, 'requirement-map.json': 'e'*64,
                    'analysis/geometry-model.json': row['sha256'],
                    'analysis/geometry_model.py': mechanics.CHECKER,
                    **mechanics._model_sources(row['sha256'])})

    def test_all_existing_families_correct_only_header_offset_and_attribution(self):
        rows = variants()
        self.assertEqual({sha256(raw) for raw in rows}, set(connector.MODELS.values()))
        for raw in rows:
            with self.subTest(original=sha256(raw)):
                original = json.loads(raw)
                corrected = connector.corrected_model(raw)
                model = json.loads(corrected)
                self.assertEqual(connector.MODELS[sha256(corrected)], sha256(raw))
                for ref, right in (('J1', '3.95'), ('J2', '7.95'), ('J3', '7.95')):
                    package = model['packages'][ref]
                    x, y = map(lambda v: Decimal(str(v)), package['offset_mm'])
                    w, h = map(lambda v: Decimal(str(v)), package['size_mm'][:2])
                    self.assertEqual((x-w/2, y-h/2, x+w/2, y+h/2),
                                     tuple(map(Decimal, ('-1.95', '-1.7', right, '2.8'))))
                    self.assertIn('manufacturer-text/jst-ph.txt', package['basis'])
                    package['offset_mm'][1] = original['packages'][ref]['offset_mm'][1]
                    package['basis'] = original['packages'][ref]['basis']
                self.assertEqual(model, original)

    def test_altered_or_already_corrected_model_cannot_be_requalified(self):
        original = ORIGINAL.read_bytes()
        changed = json.loads(original)
        changed['packages']['D1']['size_mm'][0] += .1
        for raw in (original + b' ', json_bytes(changed), connector.corrected_model(original)):
            with self.subTest(pin=sha256(raw)), self.assertRaisesRegex(ValueError, 'exact admitted preceding model'):
                connector.corrected_model(raw)

    def test_missing_or_changed_source_custody_refuses_without_mutating_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'analysis').mkdir()
            model = root / 'analysis/geometry-model.json'
            original = ORIGINAL.read_bytes()
            model.write_bytes(original)
            for name in connector.SOURCES:
                for operation in ('missing', 'changed'):
                    sources = dict(connector.SOURCES)
                    if operation == 'missing':
                        del sources[name]
                    else:
                        sources[name] = '0' * 64
                    with self.subTest(source=name, operation=operation), self.assertRaisesRegex(ValueError, 'source custody'):
                        connector.apply(root, sources)
                    self.assertEqual(model.read_bytes(), original)
                    self.assertFalse((root / connector.ORIGINAL).exists())

    def test_existing_provenance_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'analysis').mkdir()
            model = root / 'analysis/geometry-model.json'
            original = ORIGINAL.read_bytes()
            model.write_bytes(original)
            retained = root / connector.ORIGINAL
            retained.parent.mkdir(parents=True)
            retained.write_bytes(b'existing history')
            with self.assertRaises(FileExistsError):
                connector.apply(root, dict(connector.SOURCES))
            self.assertEqual(model.read_bytes(), original)
            self.assertEqual(retained.read_bytes(), b'existing history')
