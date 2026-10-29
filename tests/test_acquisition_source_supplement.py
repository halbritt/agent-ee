"""Portable source correction invariants; no native or private capture dependency."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from agent_ee import acquisition_source_supplement as supplement, acquisition_mechanics as mechanics
from agent_ee.storage import json_bytes

ROOT = Path(__file__).resolve().parents[1]


class SourceCorrectionTests(unittest.TestCase):
    def test_exact_murata_attribution_changes_no_numerical_or_other_package_model(self):
        raw = (ROOT / 'fixtures/acquisition-qualification/geometry-model.json').read_bytes()
        original = json.loads(raw)
        result = json.loads(supplement.corrected_model(raw))
        for ref in ('C4', 'C5'):
            self.assertIn('GRM1885C1H103JA01D', result['packages'][ref]['basis'])
            self.assertIn('physical p2', result['packages'][ref]['basis'])
            self.assertEqual(result['packages'][ref]['size_mm'], [1.7, .9, .9])
            result['packages'][ref]['basis'] = original['packages'][ref]['basis']
        self.assertEqual(result, original)

    def test_changed_historical_geometry_refuses_instead_of_being_requalified(self):
        original = json.loads((ROOT / 'fixtures/acquisition-qualification/geometry-model.json').read_bytes())
        changed = copy.deepcopy(original)
        changed['packages']['C4']['size_mm'][2] = .8
        with self.assertRaisesRegex(ValueError, 'exact historical geometry'):
            supplement.corrected_model(json_bytes(changed))

    def test_corrected_model_without_exact_source_custody_refuses_before_native_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'placement-mechanics.json'
            for mutation in ('missing', 'wrong-hash', 'unknown-model', 'checker'):
                sources = dict.fromkeys(mechanics.SOURCES, '0' * 64)
                sources.update(supplement.geometry_sources())
                sources['analysis/geometry-model.json'] = supplement.CORRECTED_MODEL
                sources['analysis/geometry_model.py'] = mechanics.CHECKER
                if mutation == 'missing':
                    sources.pop('source-supplement/murata/reference.pdf')
                elif mutation == 'wrong-hash':
                    sources['source-supplement/murata/reference.pdf'] = '1' * 64
                elif mutation == 'unknown-model':
                    sources['analysis/geometry-model.json'] = '2' * 64
                else:
                    sources['analysis/geometry_model.py'] = '3' * 64
                path.write_bytes(json_bytes(dict(schema=mechanics.SCHEMA, sources=sources)))
                with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, 'unsupported acquisition'):
                    mechanics.load(path)

    def test_missing_or_unrelated_capture_is_not_source_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'source supplement missing'):
                supplement.read(tmp)
            (Path(tmp) / 'manufactured-approval.txt').write_text('not a captured source')
            with self.assertRaisesRegex(ValueError, 'source supplement missing'):
                supplement.read(tmp)
