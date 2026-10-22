"""Hash-bound vendor SPICE model qualification through the real active_spice check."""
import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_ee.storage import digest, read_json, write_json
import scripts.qualify_vendor_spice as qvs
from scripts.qualify_vendor_spice import (ANALYTIC, EXPECTED, REGISTRY, UNSAFE_SUFFIX, bind_model_dir,
                                          load_manifest, qualify, validate_registry)
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

    def test_snapshot_refuses_changed_missing_or_linked_source_and_keeps_no_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = write_models(root / 'models')
            write_json(root / 'manifest.json', manifest(files))
            stage_lib = root / 'models/sub/stage.lib'
            cases = (('hash differs', lambda: stage_lib.write_text(STAGE + '* changed\n')),
                     ('missing', stage_lib.unlink),
                     ('linked', lambda: ((root / 'elsewhere.lib').write_text(STAGE), stage_lib.unlink(),
                                         stage_lib.symlink_to(root / 'elsewhere.lib'))),
                     ('linked', lambda: ((root / 'models/sub').rename(root / 'realsub'),
                                         (root / 'models/sub').symlink_to(root / 'realsub'))))
            for index, (message, breakage) in enumerate(cases):
                for path in (root / 'models', root / 'realsub', root / 'elsewhere.lib'):
                    if path.is_symlink() or path.is_file():
                        path.unlink()
                    elif path.is_dir():
                        shutil.rmtree(path)
                write_models(root / 'models')
                breakage()
                out = root / f'out{index}'
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    qualify(root / 'manifest.json', root / 'models', out)
                self.assertFalse((out / 'receipt.json').exists())
                self.assertFalse((out / 'snapshot').exists())

    def test_ancestry_swapped_during_snapshot_refuses_and_keeps_no_copy(self):
        # Review H1 (ee441b): after the pathname checks, an unprivileged writer renames a
        # directory on the model path and puts a link (or a lookalike directory) in its place
        # immediately before a component is opened. The traversal is pinned, so a link is never
        # followed and a swapped ancestor refuses even though the bytes are identical.
        def relink(path):
            path.rename(path.with_name('real-' + path.name))
            path.symlink_to(path.with_name('real-' + path.name))

        def lookalike(path):
            path.rename(path.with_name('real-' + path.name))
            shutil.copytree(path.with_name('real-' + path.name), path)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = (('sub', 'stage.lib', relink, 'replaced or linked'),
                     ('sub', 'sub', relink, 'linked'),
                     ('sub', 'stage.lib', lookalike, 'replaced or linked'),
                     ('models', 'AEOPA.LIB', relink, 'replaced or linked'),
                     ('models', 'stage.lib', lookalike, 'replaced or linked'))
            for index, (target, trigger, swap, message) in enumerate(cases):
                case = root / f'case{index}'
                files = write_models(case / 'models')
                write_json(case / 'manifest.json', manifest(files))
                victim = case / 'models' / ('sub' if target == 'sub' else '')
                real_open, swapped = qvs.os.open, []

                def racing_open(path, flags, *args, dir_fd=None, **kwargs):
                    if Path(path).name == trigger and not swapped:
                        swap(victim)
                        swapped.append(trigger)
                    return real_open(path, flags, *args, dir_fd=dir_fd, **kwargs)
                out = case / 'out'
                with self.subTest(target=target, trigger=trigger, swap=swap.__name__), \
                        mock.patch.object(qvs.os, 'open', racing_open), \
                        self.assertRaisesRegex(ValueError, message):
                    qualify(case / 'manifest.json', case / 'models', out)
                self.assertEqual(swapped, [trigger])
                self.assertFalse((out / 'receipt.json').exists())
                self.assertFalse((out / 'snapshot').exists())

    def test_root_replaced_between_snapshot_copies_refuses_and_keeps_no_copy(self):
        # Review H1 (ee441c): the model root is renamed and replaced by a link or by a
        # lookalike directory with identical bytes right after a file's copy has been
        # verified. The root is pinned once for the whole closure, so the replacement is
        # never read and refuses, whether it lands between copies or after the last one.
        def relink(path):
            path.rename(path.with_name('real-' + path.name))
            path.symlink_to(path.with_name('real-' + path.name))

        def lookalike(path):
            path.rename(path.with_name('real-' + path.name))
            shutil.copytree(path.with_name('real-' + path.name), path)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, (swap, after) in enumerate(((relink, 'AEOPA.LIB'), (lookalike, 'AEOPA.LIB'),
                                                   (relink, 'sub/stage.lib'), (lookalike, 'sub/stage.lib'))):
                case = root / f'case{index}'
                files = write_models(case / 'models')
                write_json(case / 'manifest.json', manifest(files))
                out = case / 'out'
                real_digest, swapped = qvs.digest, []

                def copied(path, *args, **kwargs):
                    result = real_digest(path, *args, **kwargs)
                    if Path(path) == out / 'snapshot' / after and not swapped:
                        swap(case / 'models')
                        swapped.append(after)
                    return result
                with self.subTest(swap=swap.__name__, after=after), \
                        mock.patch.object(qvs, 'digest', copied), \
                        self.assertRaisesRegex(ValueError, "component 'models' was replaced or linked"):
                    qualify(case / 'manifest.json', case / 'models', out)
                self.assertEqual(swapped, [after])
                self.assertFalse((out / 'receipt.json').exists())
                self.assertFalse((out / 'snapshot').exists())
                self.assertFalse((out / 'scenarios').exists())

    def test_bind_model_dir_refuses_root_replaced_between_files(self):
        def relink(path):
            path.rename(path.with_name('real-' + path.name))
            path.symlink_to(path.with_name('real-' + path.name))

        def lookalike(path):
            path.rename(path.with_name('real-' + path.name))
            shutil.copytree(path.with_name('real-' + path.name), path)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, swap in enumerate((relink, lookalike)):
                case = root / f'case{index}'
                files = write_models(case / 'models')
                loaded = manifest(files)
                self.assertEqual(bind_model_dir(loaded, case / 'models'), files)
                real_read, reads = qvs.PinnedRoot.read, []

                def read_then_swap(pinned, rel):
                    data = real_read(pinned, rel)
                    reads.append(rel)
                    if len(reads) == 1:
                        swap(case / 'models')
                    return data
                with self.subTest(swap=swap.__name__), \
                        mock.patch.object(qvs.PinnedRoot, 'read', read_then_swap), \
                        self.assertRaisesRegex(ValueError, "component 'models' was replaced or linked"):
                    bind_model_dir(loaded, case / 'models')
                self.assertEqual(reads, ['AEOPA.LIB'])

    def test_snapshot_changed_after_verification_refuses_before_capture(self):
        # The parse and every scenario read only the verified private snapshot; a later
        # change to the snapshot itself refuses rather than simulating unverified bytes.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = write_models(root / 'models')
            write_json(root / 'manifest.json', manifest(files))
            original = qvs.capture_models

            def tamper(inputs, observed, declared):
                models = original(inputs, observed, declared)
                target = Path(inputs) / 'AEOPA.LIB'
                self.assertEqual(Path(inputs), root / 'out/snapshot')
                target.chmod(0o600)
                target.write_text(SHAPED.replace('{OFFSET}', '0.05'))
                return models
            with mock.patch.object(qvs, 'capture_models', tamper), \
                    self.assertRaisesRegex(ValueError, 'private snapshot changed after verification'):
                qualify(root / 'manifest.json', root / 'models', root / 'out')
            self.assertFalse((root / 'out/scenarios/control/run').exists())


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
        for name, record in receipt['scenarios'].items():
            binding = record['model_binding']
            self.assertTrue(binding['bound'], (name, binding))
            if name == 'unsafe_directive':
                root = (self.root / 'models/AEOPA.LIB').read_bytes() + UNSAFE_SUFFIX
                self.assertEqual(binding['captured_sha256'],
                                 {'AEOPA.LIB': hashlib.sha256(root).hexdigest(),
                                  'sub/stage.lib': receipt['model_files']['sub/stage.lib']})
                self.assertEqual(binding['mutation']['roots'], ['AEOPA.LIB'])
            else:
                self.assertEqual(binding['captured_sha256'], receipt['model_files'])
        self.assertEqual({rel: digest(self.root / 'out/snapshot' / rel) for rel in receipt['model_files']},
                         receipt['model_files'])
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

    def test_source_swapped_during_run_cannot_qualify_the_manifest_bytes(self):
        # Reviewer H1: the manifest binds the defective 50 mV offset model; an external writer
        # replaces the live source with the passing zero-offset model at the first scenario
        # boundary. Every scenario must simulate the verified snapshot, so control stays a defect.
        defective, passing = SHAPED.replace('{OFFSET}', '0.05'), SHAPED.replace('{OFFSET}', '0')
        original, calls = qvs.stage, []

        def swap(observed, snapshot, directory):
            if not calls:
                (self.root / 'models/AEOPA.LIB').write_text(passing)
            calls.append(directory.name)
            return original(observed, snapshot, directory)
        with mock.patch.object(qvs, 'stage', swap):
            receipt = self.run_qualify(defective)
        self.assertEqual(calls, list(EXPECTED))
        self.assertEqual((self.root / 'models/AEOPA.LIB').read_text(), passing)
        bound = hashlib.sha256(defective.encode()).hexdigest()
        self.assertEqual(receipt['model_files']['AEOPA.LIB'], bound)
        self.assertFalse(receipt['qualified'])
        self.assertIn('control', receipt['reason'])
        control = receipt['scenarios']['control']
        self.assertEqual((control['observed']['execution'], control['observed']['outcome']), ('ok', 'defect'))
        self.assertAlmostEqual(control['metrics']['out_1ms'], ANALYTIC['out_1ms'] + 0.05, delta=1e-3)
        for name, record in receipt['scenarios'].items():
            self.assertTrue(record['model_binding']['bound'], name)
        captured = self.root / 'out/scenarios/control/run/inputs/design/vendor/AEOPA.LIB'
        self.assertEqual(digest(captured), bound)

    def test_scenario_capture_of_other_bytes_is_unbound_and_unqualified(self):
        # Bytes that differ from the verified snapshot inside a scenario capture are never
        # credited, even when they produce every expected status.
        defective, passing = SHAPED.replace('{OFFSET}', '0.05'), SHAPED.replace('{OFFSET}', '0')
        original = qvs.stage

        def substitute(observed, snapshot, directory):
            source = original(observed, snapshot, directory)
            (source / 'vendor/AEOPA.LIB').write_text(passing)
            return source
        with mock.patch.object(qvs, 'stage', substitute):
            receipt = self.run_qualify(defective)
        self.assertFalse(receipt['qualified'])
        self.assertIn('do not bind the verified model bytes', receipt['reason'])
        control = receipt['scenarios']['control']
        self.assertEqual((control['observed']['execution'], control['observed']['outcome']), ('ok', 'pass'))
        self.assertFalse(control['matched'])
        self.assertEqual(control['model_binding']['captured_sha256']['AEOPA.LIB'],
                         hashlib.sha256(passing.encode()).hexdigest())
        self.assertTrue(all(not r['model_binding']['bound'] for r in receipt['scenarios'].values()))


if __name__ == '__main__':
    unittest.main()
