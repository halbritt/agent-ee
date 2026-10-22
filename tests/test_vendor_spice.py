"""Hash-bound vendor SPICE model qualification through the real active_spice check."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent_ee.storage import digest, read_json, write_json
from scripts.qualify_vendor_spice import (ANALYTIC, EXPECTED, REGISTRY, bind_model_dir, load_manifest,
                                          qualify, validate_registry)
from toolchain import require_toolchain

# Original vendor-shaped macromodel (agent-ee authored): sign-bearing formals,
# bare .ENDS, a nested include and a single dominant pole. Not a vendor model.
SHAPED = ('* Original agent-ee vendor-shaped op-amp macromodel; harness tests only.\n'
          '.INCLUDE sub/stage.lib\n'
          '.SUBCKT AEOPA IN+ IN- V+ V- OUT\n'
          'RIN IN+ IN- 10MEG\nVOS INX IN+ DC {OFFSET}\nXG INX IN- MID AESTAGE\nCC MID 0 1.59u\n'
          'RG MID 0 1MEG\nEOUT OUTI 0 MID 0 1\nROUT OUTI OUT 10\nIQ V+ V- 1.5M\n.ENDS\n')
STAGE = '* Transconductance stage.\n.SUBCKT AESTAGE P N O\nG1 0 O P N 1\n.ENDS AESTAGE\n'


def write_models(root, model=SHAPED.replace('{OFFSET}', '0')):
    (root / 'sub').mkdir(parents=True)
    (root / 'AEOPA.LIB').write_text(model)
    (root / 'sub/stage.lib').write_text(STAGE)
    return {rel: digest(root / rel) for rel in ('AEOPA.LIB', 'sub/stage.lib')}


def manifest(files, **changes):
    return {'schema': 'agent-ee.vendor-spice-manifest/1', 'id': 'ae-shaped', 'vendor': 'agent-ee',
            'part': 'AEOPA', 'source': 'original test model', 'model_files': files, 'subcircuit': 'AEOPA',
            'pin_map': {'3': 'IN+', '2': 'IN-', '7': 'V+', '4': 'V-', '6': 'OUT'}, **changes}


class RegistryAndBindingTests(unittest.TestCase):
    def test_tracked_registry_records_hashes_only_and_opa197_is_unqualified(self):
        registry = validate_registry(read_json(REGISTRY))
        entry = {e['id']: e for e in registry['entries']}['ti-opa197']
        self.assertEqual((entry['status'], entry['model_files'], entry['receipt_sha256']),
                         ('unqualified', {}, None))
        self.assertIn('AGENTEE-118', registry['publication'])
        for mutate in (lambda e: e.update(status='qualified'),
                       lambda e: e.update(status='qualified', reason=None, model_files={'x.lib': 'a' * 64}),
                       lambda e: e.update(reason=''),
                       lambda e: e.update(status='passed'),
                       lambda e: e.update(model_files={'x.lib': 'not-a-hash'}),
                       lambda e: e.update(model_text='.subckt copied')):
            bad = copy.deepcopy(registry)
            mutate(bad['entries'][0])
            with self.subTest(entry=bad['entries'][0]), self.assertRaises(ValueError):
                validate_registry(bad)
        good = copy.deepcopy(registry)
        good['entries'][0].update(status='qualified', reason=None, model_files={'x.lib': 'a' * 64},
                                  receipt_sha256='b' * 64)
        validate_registry(good)

    def test_manifest_and_model_hash_binding_refuse_before_simulation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = write_models(root / 'models')
            for changes in ({'pin_map': {'3': 'IN+', '2': 'IN-', '7': 'V+', '4': 'V-'}},
                            {'model_files': {'AEOPA.LIB': 'A' * 64}},
                            {'model_files': {'../AEOPA.LIB': 'a' * 64}},
                            {'supplies': {'positive_v': 0.1, 'negative_v': -5}},
                            {'dialect': 'pspice'}, {'id': 'Bad Id'}, {'unexpected': 1}):
                write_json(root / 'manifest.json', manifest(files, **changes))
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    load_manifest(root / 'manifest.json')
            write_json(root / 'manifest.json', manifest(files))
            loaded = load_manifest(root / 'manifest.json')
            self.assertEqual(bind_model_dir(loaded, root / 'models'), files)
            (root / 'models/sub/stage.lib').write_text(STAGE + '* changed\n')
            with self.assertRaisesRegex(ValueError, 'hash differs'):
                qualify(root / 'manifest.json', root / 'models', root / 'out')
            self.assertFalse((root / 'out/receipt.json').exists())
            (root / 'models/sub/stage.lib').unlink()
            (root / 'elsewhere.lib').write_text(STAGE)
            (root / 'models/sub/stage.lib').symlink_to(root / 'elsewhere.lib')
            with self.assertRaisesRegex(ValueError, 'linked'):
                bind_model_dir(loaded, root / 'models')


class NativeQualificationTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-vendor-spice-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_qualify(self, model=SHAPED.replace('{OFFSET}', '0'), **changes):
        files = write_models(self.root / 'models', model)
        write_json(self.root / 'manifest.json', manifest(files, **changes))
        return qualify(self.root / 'manifest.json', self.root / 'models', self.root / 'out')

    def test_shaped_model_qualifies_with_every_expected_status_and_no_model_text(self):
        receipt = self.run_qualify()
        self.assertTrue(receipt['qualified'], receipt)
        self.assertEqual(sorted(receipt['scenarios']), sorted(EXPECTED))
        for name, record in receipt['scenarios'].items():
            execution, outcome, _ = EXPECTED[name]
            self.assertTrue(record['matched'], (name, record))
            self.assertEqual((record['observed']['execution'], record['observed']['outcome']),
                             (execution, outcome))
        control = receipt['scenarios']['control']
        # Finite open-loop gain (1e6) and the dominant pole keep the follower near the analytic RC.
        self.assertAlmostEqual(control['metrics']['out_1ms'], ANALYTIC['out_1ms'], delta=1e-4)
        self.assertAlmostEqual(control['metrics']['gain_100hz'], ANALYTIC['gain_100hz'], delta=1e-4)
        self.assertEqual(sorted(control['deck_sha256']), ['gain', 'step'])
        self.assertRegex(control['ngspice_sha256'], '^[0-9a-f]{64}$')
        self.assertLess(receipt['scenarios']['seeded']['metrics']['out_1ms'], 0.062)
        self.assertEqual(receipt['header'], {'path': 'AEOPA.LIB', 'line': 3,
                                             'formals': ['IN+', 'IN-', 'V+', 'V-', 'OUT']})
        self.assertEqual(receipt['model_files']['sub/stage.lib'], digest(self.root / 'models/sub/stage.lib'))
        text = json.dumps(read_json(self.root / 'out/receipt.json'))
        self.assertNotIn('AESTAGE P N O', text)
        self.assertNotIn('1.59u', text)

    def test_dialect_refused_closure_is_unqualified_without_simulation(self):
        model = SHAPED.replace('{OFFSET}', '0').replace('IQ V+ V- 1.5M\n', '.FUNC twice(x) {2*x}\nIQ V+ V- 1.5M\n')
        receipt = self.run_qualify(model)
        self.assertFalse(receipt['qualified'])
        self.assertIn('refused by the bounded active-spice dialect', receipt['reason'])
        self.assertEqual(receipt['scenarios'], {})
        self.assertFalse((self.root / 'out/scenarios').exists())

    def test_pin_map_formals_must_match_the_actual_header(self):
        receipt = self.run_qualify(pin_map={'3': 'IN+', '2': 'IN-', '7': 'VCC', '4': 'V-', '6': 'OUT'})
        self.assertFalse(receipt['qualified'])
        self.assertIn('header formals', receipt['reason'])

    def test_model_with_wrong_response_is_unqualified_by_control(self):
        # A 50 mV input offset moves the follower output outside the analytic step window.
        receipt = self.run_qualify(SHAPED.replace('{OFFSET}', '0.05'))
        self.assertFalse(receipt['qualified'])
        self.assertIn('control', receipt['reason'])
        control = receipt['scenarios']['control']
        self.assertEqual((control['observed']['execution'], control['observed']['outcome']), ('ok', 'defect'))
        self.assertAlmostEqual(control['metrics']['out_1ms'], ANALYTIC['out_1ms'] + 0.05, delta=1e-3)


if __name__ == '__main__':
    unittest.main()
