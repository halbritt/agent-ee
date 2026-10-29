"""Build receipts bind the installed planner to the complete checkout inputs."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('placement_engine_build', ROOT / 'scripts/build_placement_engine.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class PlacementEngineBuildTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ('crates/placement-engine/Cargo.toml', 'crates/placement-engine/Cargo.lock',
                     'crates/placement-engine/src/main.rs', 'src/agent_ee/data/jlcpcb-locality.json',
                     'fixtures/acquisition-qualification/geometry-model.json',
                     'fixtures/acquisition-qualification/geometry-model-murata.json',
                     'fixtures/acquisition-qualification/geometry-model-r6.json',
                     'fixtures/acquisition-typed/source-supplement.json',
                     'fixtures/acquisition-typed/power-repair.json',
                     'scripts/build_placement_engine.py'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('original fixture input\n')
        self.binary = self.root / 'installed/agent-ee-placement'
        self.binary.parent.mkdir()
        self.binary.write_bytes(b'original fixture executable')
        self.binary.chmod(0o755)
        self.tools = {'rustc': build.RUSTC + '\nhost: ' + build.TARGET,
                      'cargo': build.CARGO, 'target': build.TARGET, 'cpu': 'x86-64',
                      'source_path_remap': '/opt/agent-ee'}
        identity = build.sources(self.root)
        self.receipt = {'schema': build.SCHEMA, 'toolchain': self.tools,
                        'source_sha256': identity,
                        'cargo_lock_sha256': identity['crates/placement-engine/Cargo.lock'],
                        'binary_sha256': build.sha(self.binary), 'rust_unit_tests': 'passed',
                        'build': {'profile': 'release', 'frozen': True}}
        self.write_receipt()

    def write_receipt(self):
        build.receipt_path(self.binary).write_text(json.dumps(self.receipt))

    def test_receipt_verification_is_offline_and_does_not_require_compilers(self):
        with mock.patch.object(build.subprocess, 'run', side_effect=AssertionError('unexpected child')):
            self.assertEqual(build.verify(self.root, self.binary), self.receipt)

    def test_source_resource_lock_binary_and_compiler_drift_are_refused(self):
        for name in ('crates/placement-engine/src/main.rs', 'crates/placement-engine/Cargo.lock',
                     'src/agent_ee/data/jlcpcb-locality.json', 'scripts/build_placement_engine.py',
                     'fixtures/acquisition-qualification/geometry-model.json',
                     'fixtures/acquisition-qualification/geometry-model-murata.json',
                     'fixtures/acquisition-qualification/geometry-model-r6.json',
                     'fixtures/acquisition-typed/source-supplement.json',
                     'fixtures/acquisition-typed/power-repair.json',
                     'installed/agent-ee-placement'):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b'changed')
                with self.assertRaisesRegex(ValueError, 'differs from its build receipt'):
                    build.verify(self.root, self.binary)
                path.write_bytes(original)
        for key, value in (('cargo', 'cargo unpinned'), ('cpu', 'native'), ('target', 'other-target')):
            with self.subTest(key=key):
                original = self.tools[key]
                self.tools[key] = value
                self.write_receipt()
                with self.assertRaises(ValueError):
                    build.verify(self.root, self.binary)
                self.tools[key] = original
        self.receipt['rust_unit_tests'] = 'failed'
        self.write_receipt()
        with self.assertRaises(ValueError):
            build.verify(self.root, self.binary)

    def test_added_source_is_bound_but_target_artifacts_are_excluded(self):
        artifact = self.root / 'crates/placement-engine/target/release/object'
        artifact.parent.mkdir(parents=True)
        artifact.write_text('compiler output')
        build.verify(self.root, self.binary)
        (self.root / 'crates/placement-engine/src/new.rs').write_text('new source')
        with self.assertRaises(ValueError):
            build.verify(self.root, self.binary)

    def test_failed_unit_tests_do_not_build_or_replace_installed_binary(self):
        previous = self.binary.read_bytes()
        failure = subprocess.CalledProcessError(101, ['cargo', 'test'])
        with mock.patch.object(build, 'toolchain', return_value=self.tools), \
                mock.patch.object(build.subprocess, 'run', side_effect=failure) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                build.build(self.root, self.binary, self.root / 'target')
        self.assertEqual(run.call_count, 1)
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ['cargo', 'test'])
        self.assertIn('--frozen', command)
        self.assertIn('--release', command)
        self.assertEqual(self.binary.read_bytes(), previous)

    def test_toolchain_rejects_wrong_version_and_host(self):
        for rustc, cargo in ((build.RUSTC + '\nhost: other', build.CARGO),
                             ('rustc unpinned\nhost: ' + build.TARGET, build.CARGO),
                             (build.RUSTC + '\nhost: ' + build.TARGET, 'cargo unpinned')):
            with self.subTest(rustc=rustc, cargo=cargo):
                output = [subprocess.CompletedProcess([], 0, rustc), subprocess.CompletedProcess([], 0, cargo)]
                with mock.patch.object(build.subprocess, 'run', side_effect=output):
                    with self.assertRaisesRegex(ValueError, 'pinned Rust'):
                        build.toolchain({})

    def test_build_flags_override_inherited_cpu_selection(self):
        with mock.patch.dict(os.environ, {'RUSTFLAGS': '-Ctarget-cpu=native',
                                          'CARGO_ENCODED_RUSTFLAGS': '-Ctarget-cpu=native'}):
            env = build.environment(self.root)
        self.assertNotIn('RUSTFLAGS', env)
        self.assertNotIn('native', env['CARGO_ENCODED_RUSTFLAGS'])
        self.assertIn('-Ctarget-cpu=x86-64', env['CARGO_ENCODED_RUSTFLAGS'])


class PlacementEngineImageTest(unittest.TestCase):
    def test_runtime_receives_verified_binary_without_compiler_or_scalar_library(self):
        dockerfile = (ROOT / 'Dockerfile').read_text()
        build_stage = dockerfile.split('FROM kicad-route-build AS placement-engine-build\n')[1]
        build_stage, runtime = build_stage.split('FROM toolchain\n', 1)
        self.assertIn('RUN --network=none python3 scripts/build_placement_engine.py build', build_stage)
        self.assertIn('COPY --from=placement-engine-build /usr/local/libexec/agent-ee/', runtime)
        self.assertIn('scripts/build_placement_engine.py verify', runtime)
        self.assertNotIn('COPY --from=rust-toolchain', runtime)
        self.assertNotIn('placement_score.so', dockerfile)
        self.assertIn('**/target', (ROOT / '.dockerignore').read_text().splitlines())


if __name__ == '__main__':
    unittest.main()
