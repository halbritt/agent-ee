"""Offline source tests; mutated stock rows are CPU controls, never observations."""
from dataclasses import replace
import importlib.abc
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from agent_ee import acquisition_alternative as alternative
from agent_ee import jlc_stock_capture, parts_manifest, requirement_map, storage

CAPTURE = os.environ.get('AGENT_EE_ALTERNATIVE_DRAFT')


class NoNative(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'pcbnew', '_pcbnew', 'kicad', 'kipy', 'eeschema'}:
            raise AssertionError('native import forbidden: ' + fullname)


def deny_external(event, args):
    if event in {'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.posix_spawnp',
                 'socket.connect', 'socket.connect_ex', 'socket.getaddrinfo'}:
        raise AssertionError('external execution forbidden: ' + event)


# These guards apply to every actual producer/validator call in this module.
sys.addaudithook(deny_external)
sys.meta_path.insert(0, NoNative())


@unittest.skipUnless(CAPTURE, 'explicit retained private capture required')
class RetainedCaptureTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='adc-carrier-source-')
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name) / 'owned-revision'
        self.root.mkdir()
        draft = Path(CAPTURE)
        originals = json.loads((draft / 'original-input-hashes.json').read_bytes())
        for name, sha in originals.items():
            src = draft / 'bundle' / name
            self.assertEqual(storage.digest(src), sha)
            dst = self.root / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        shutil.copytree(draft / 'bundle/stock-derived', self.root / 'preparation/catalog')
        shutil.copytree(draft / 'bundle/alternative-sources', self.root / 'preparation/ti')
        self.original = json.loads((self.root / 'circuit.json').read_bytes())
        self.original_parts = json.loads((self.root / 'parts.json').read_bytes())
        self.context = self.context_for()

    def blob(self, path):
        return dict(path=path, sha256=storage.digest(self.root / path))

    def context_for(self):
        return alternative.CapturedContext(requirement_map.input_inventory(self.root),
            self.blob('preparation/catalog/catalog.json'),
            self.blob('preparation/ti/TI-ADS1115IDGST.html'),
            self.blob('manufacturer-text/ads1115.txt'), '2026-10-29T08:00:00+00:00')

    def synthetic_row(self, lcsc, **changes):
        """Rebuild a *synthetic* internally consistent raw capture boundary case."""
        path = self.root / self.context.catalog['path']
        catalog = json.loads(path.read_bytes())
        req = next(r for r in catalog['requests'] if r['lcsc'] == lcsc)
        rawpath = path.parent / req['response']['path']
        raw = json.loads(rawpath.read_bytes())
        row = next(r for r in raw['data']['componentPageInfo']['list'] if r['componentCode'] == lcsc)
        row.update(changes)
        rawpath.write_bytes(storage.json_bytes(raw))
        req['response']['sha256'] = storage.digest(rawpath)
        req['response']['size_bytes'] = rawpath.stat().st_size
        _, parsed = jlc_stock_capture.parse_search_response(rawpath.read_bytes(), lcsc)
        catalog['parts'][lcsc] = {**parsed, 'last_seen': req['fetched_at']}
        path.write_bytes(storage.json_bytes(catalog))
        self.context = self.context_for()

    def test_actual_capture_chooses_alternative_and_preserves_constraints(self):
        result = alternative.revise(self.root, self.context)
        self.assertEqual(result['selected'], 'C468683')
        manifest = parts_manifest.load_manifest(self.root, 'parts.json')
        adc = next(p for p in manifest['parts'] if p['lcsc'] == 'C468683')
        self.assertEqual(adc['mpn'], 'ADS1115IDGST')
        self.assertEqual(adc['stock'], 3552)  # retained observation, no production constant
        self.assertEqual(adc['package'], 'VSSOP-10-0.5mm')
        circuit = json.loads((self.root / 'circuit.json').read_bytes())
        for component in circuit['components']:
            if component['reference'] == 'U2':
                component['value'] = 'ADS1115IDGSR'
        self.assertEqual(circuit, self.original)
        for before, after in zip(self.original_parts['parts'], manifest['parts']):
            for field in ('instances', 'lib_id', 'footprint', 'datasheet', 'role', 'required'):
                self.assertEqual(before[field], after[field])
        self.assertEqual(len(json.loads((self.root / alternative.PREFIX / 'part-refs.json').read_bytes())['components']), 17)
        alternative.audit_selection(self.root, result)
        record = json.loads((self.root / adc['selection']['part_record']).read_bytes())
        self.assertEqual(record['fetched_at'], '2026-10-29T03:17:16.322804+00:00')
        self.assertEqual(result['catalog'], self.context.catalog)

    def test_synthetic_primary_retained_even_with_more_alternative_stock(self):
        self.synthetic_row('C37593', stockCount=10)
        self.assertEqual(alternative.revise(self.root, self.context)['selected'], 'C37593')

    def test_synthetic_stocked_incompatible_alternative_is_not_selected(self):
        self.synthetic_row('C468683', componentModelEn='ADS1015IDGST')
        with self.assertRaisesRegex(ValueError, 'no eligible.*incompatible_identity_package'):
            alternative.revise(self.root, self.context)

    def test_synthetic_none_available(self):
        self.synthetic_row('C468683', stockCount=0)
        with self.assertRaisesRegex(ValueError, 'no eligible.*low_stock'):
            alternative.revise(self.root, self.context)

    def test_synthetic_package_incompatible(self):
        self.synthetic_row('C468683', componentSpecificationEn='X2QFN-10')
        with self.assertRaisesRegex(ValueError, 'no eligible.*incompatible_identity_package'):
            alternative.revise(self.root, self.context)

    def test_shared_connector_quantity_is_counted_across_board(self):
        self.synthetic_row('C131334', stockCount=1)
        with self.assertRaisesRegex(ValueError, 'full-board stock exhausted: C131334'):
            alternative.revise(self.root, self.context)

    def test_source_quantity_policy_is_not_relaxed(self):
        path = self.root / 'requirement-map.json'
        mapping = json.loads(path.read_bytes())
        alternative._assembly_check(mapping)['params']['boards'] = 4000
        path.write_bytes(storage.json_bytes(mapping))
        with self.assertRaisesRegex(ValueError, 'no eligible.*low_stock'):
            alternative.revise(self.root, self.context_for())

    def test_stale_source_refuses_without_clock_reset(self):
        with self.assertRaisesRegex(ValueError, 'stale'):
            alternative.revise(self.root, replace(self.context, as_of='2026-10-30T08:00:00+00:00'))

    def test_missing_alternative_is_unknown_and_refuses(self):
        catalogpath = self.root / self.context.catalog['path']
        catalog = json.loads(catalogpath.read_bytes())
        catalog['requests'] = [r for r in catalog['requests'] if r['lcsc'] != 'C468683']
        del catalog['parts']['C468683']
        catalogpath.write_bytes(storage.json_bytes(catalog))
        with self.assertRaisesRegex(ValueError, 'lacks an observation'):
            alternative.revise(self.root, self.context_for())

    def test_source_drift_refuses_before_writes(self):
        (self.root / 'OPERATING.md').write_bytes(b'source drift')
        with self.assertRaisesRegex(ValueError, 'source closure drift'):
            alternative.revise(self.root, self.context)
        self.assertFalse((self.root / alternative.PREFIX).exists())

    def test_changed_stock_after_selection_refuses_old_decision(self):
        result = alternative.revise(self.root, self.context)
        self.synthetic_row('C468683', stockCount=0)
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            alternative.audit_selection(self.root, result)

    def test_missing_ti_correspondence_cannot_be_caller_qualified(self):
        (self.root / self.context.carrier_page['path']).write_text('qualified:true')
        with self.assertRaisesRegex(ValueError, 'TI same-device'):
            alternative.revise(self.root, self.context_for())
