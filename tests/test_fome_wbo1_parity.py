"""Rev C identity guards and exhaustive native difference-report reproduction."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

from agent_ee.storage import read_json
from tests.toolchain import require_toolchain

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/fome-mc/wbo1'
spec = importlib.util.spec_from_file_location('wideband_parity', FIXTURE / 'parity.py')
parity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parity)


class UpstreamIdentityTests(unittest.TestCase):
    def test_attestation_uses_offline_objects_and_separates_membership_from_blob_identity(self):
        report = read_json(FIXTURE / 'upstream-parity.json')
        self.assertEqual(report['upstream']['git_tree'], parity.TREE)
        self.assertEqual(report['upstream']['identity_source'], dict(
            method='offline-git-objects', retained_blob='verified',
            commit_tree_membership='requires-local-upstream-objects'))
        self.assertNotIn('api.github.com', (FIXTURE / 'UPSTREAM-PARITY.md').read_text())
        self.assertIn('No upstream bytes were', (FIXTURE / 'UPSTREAM-PARITY.md').read_text())

    def test_wrong_upstream_bytes_are_refused(self):
        parity.verify_upstream(parity.CACHED)
        with tempfile.TemporaryDirectory() as tmp:
            changed = Path(tmp) / 'changed.kicad_sch'
            changed.write_bytes(parity.CACHED.read_bytes() + b'\n')
            with self.assertRaisesRegex(ValueError, 'pinned Rev C blob'):
                parity.verify_upstream(changed)

    def test_changed_pin_and_component_are_not_hidden(self):
        source = read_json(FIXTURE / 'blocks/wbo1/connectivity.json')
        renamed = copy.deepcopy(source)
        renamed['components'] = {ref[0] + str(int(ref[1:]) - 300): comp
                                 for ref, comp in renamed['components'].items()}
        # The public comparator, reused by the report, must retain these defects.
        source['components']['U302']['pins']['15'] = '+3V3_WBO1'
        source['components']['R310']['value'] = '619'
        diff = parity.differences(renamed, source)
        self.assertTrue(any(d['reference'] == 'U302' and d.get('pin') == '15' for d in diff))
        self.assertTrue(any(d['reference'] == 'R310' and d['field'] == 'value' for d in diff))
        self.assertTrue(all(d['disposition'] == 'unexplained' and d['reason'] is None for d in diff))


class NativeParityTests(unittest.TestCase):
    def test_native_report_reproduces_all_committed_differences(self):
        require_toolchain(native=True)
        with tempfile.TemporaryDirectory(prefix='agent-ee-revc-') as tmp:
            out = Path(tmp) / 'report'
            parity.generate(out)
            report = read_json(out / 'parity.json')
            expected = read_json(FIXTURE / 'upstream-parity.json')
            self.assertEqual(report['upstream'], expected['upstream'])
            self.assertEqual(report['fome'], expected['fome'])
            self.assertEqual(report['differences'], expected['differences'])
            self.assertEqual((report['source_components'], report['fome_components']), (60, 58))
            self.assertEqual(report['unexplained'], 78)
            changed_parts = {d['reference'] for d in report['differences'] if d['field'] == 'component'}
            self.assertEqual(changed_parts, {'J301', 'J302', 'J303', 'U307'})
            self.assertFalse(any(d['field'] in ('symbol', 'value', 'footprint', 'populated')
                                 for d in report['differences']))
            self.assertEqual({v['execution'] for v in report['exports'].values()}, {'ok'})


if __name__ == '__main__':
    unittest.main()
