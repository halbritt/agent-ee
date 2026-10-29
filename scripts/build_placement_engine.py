#!/usr/bin/env python3
"""Fetch locked inputs, build/test offline, or verify the installed Rust planner.

The build requires the same pinned Rust toolchain as Dockerfile. Compilation is
an explicit installation step; neither planning nor receipt verification builds.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUSTC = 'rustc 1.99.1 (58cf8f9c3 2026-10-13)'
CARGO = 'cargo 1.99.1 (5f94df478 2026-08-27)'
TARGET = 'x86_64-unknown-linux-gnu'
SCHEMA = 'agent-ee.placement-engine-install/1'
CRATE = Path('crates/placement-engine')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources(root):
    paths = [CRATE / 'Cargo.toml', CRATE / 'Cargo.lock',
             Path('src/agent_ee/data/jlcpcb-locality.json'),
             Path('fixtures/acquisition-qualification/geometry-model.json'),
             Path('scripts/build_placement_engine.py')]
    paths += [p.relative_to(root) for p in (root / CRATE / 'src').rglob('*') if p.is_file()]
    if not any(p.suffix == '.rs' for p in paths):
        raise ValueError('placement engine source is missing')
    return {p.as_posix(): sha(root / p) for p in sorted(paths)}


def environment(root):
    env = dict(os.environ)
    for key in list(env):
        if key == 'RUSTFLAGS' or key.startswith(('CARGO_PROFILE_', 'CARGO_BUILD_', 'CARGO_TARGET_')):
            env.pop(key)
    env.update(RUSTUP_TOOLCHAIN='1.99.1', RUSTC='rustc', RUSTDOC='rustdoc',
               RUSTC_WRAPPER='', RUSTC_WORKSPACE_WRAPPER='', CARGO_INCREMENTAL='0',
               CARGO_ENCODED_RUSTFLAGS='-Ctarget-cpu=x86-64\x1f--remap-path-prefix=' + str(root) + '=/opt/agent-ee')
    return env


def toolchain(env):
    rustc = subprocess.run(['rustc', '-vV'], env=env, check=True, capture_output=True, text=True).stdout.strip()
    cargo = subprocess.run(['cargo', '--version'], env=env, check=True, capture_output=True, text=True).stdout.strip()
    if rustc.splitlines()[:1] != [RUSTC] or cargo != CARGO or '\nhost: ' + TARGET + '\n' not in '\n' + rustc + '\n':
        raise ValueError('placement engine requires the pinned Rust 1.99.1 linux/amd64 toolchain')
    return {'rustc': rustc, 'cargo': cargo, 'target': TARGET,
            'cpu': 'x86-64', 'source_path_remap': '/opt/agent-ee'}


def cargo_command(root, action, *flags):
    return ['cargo', action, '--manifest-path', str(root / CRATE / 'Cargo.toml'),
            '--target', TARGET, *flags]


def fetch(root):
    env = environment(root)
    toolchain(env)
    sources(root)
    subprocess.run(cargo_command(root, 'fetch', '--locked'), cwd=root, env=env, check=True)


def receipt_path(binary):
    return binary.with_name(binary.name + '.json')


def atomic_write(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '.', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build(root, binary, target_dir):
    env = environment(root)
    tools = toolchain(env)
    before = sources(root)
    flags = ('--release', '--frozen', '--jobs', '1', '--target-dir', str(target_dir))
    subprocess.run(cargo_command(root, 'test', *flags, '--', '--test-threads=1'), cwd=root, env=env, check=True)
    subprocess.run(cargo_command(root, 'build', *flags), cwd=root, env=env, check=True)
    if sources(root) != before:
        raise ValueError('placement engine source changed during build')
    built = target_dir / TARGET / 'release/agent-ee-placement'
    receipt = {'schema': SCHEMA, 'toolchain': tools, 'source_sha256': before,
               'cargo_lock_sha256': before[(CRATE / 'Cargo.lock').as_posix()],
               'binary_sha256': sha(built), 'rust_unit_tests': 'passed',
               'build': {'profile': 'release', 'frozen': True}}
    atomic_write(binary, built.read_bytes(), 0o755)
    atomic_write(receipt_path(binary), (json.dumps(receipt, sort_keys=True, indent=2) + '\n').encode(), 0o644)
    verify(root, binary)


def verify(root, binary):
    receipt = json.loads(receipt_path(binary).read_bytes())
    current = sources(root)
    checks = (
        receipt.get('schema') == SCHEMA,
        receipt.get('source_sha256') == current,
        receipt.get('cargo_lock_sha256') == current[(CRATE / 'Cargo.lock').as_posix()],
        receipt.get('binary_sha256') == sha(binary),
        receipt.get('rust_unit_tests') == 'passed',
        receipt.get('build') == {'profile': 'release', 'frozen': True},
        receipt.get('toolchain', {}).get('rustc', '').splitlines()[:1] == [RUSTC],
        '\nhost: ' + TARGET + '\n' in '\n' + receipt.get('toolchain', {}).get('rustc', '') + '\n',
        receipt.get('toolchain', {}).get('cargo') == CARGO,
        receipt.get('toolchain', {}).get('target') == TARGET,
        receipt.get('toolchain', {}).get('cpu') == 'x86-64',
        receipt.get('toolchain', {}).get('source_path_remap') == '/opt/agent-ee',
        os.access(binary, os.X_OK),
    )
    if not all(checks):
        raise ValueError('installed placement engine or source differs from its build receipt')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('fetch', 'build', 'verify'))
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, help='binary path; receipt is OUTPUT.json')
    parser.add_argument('--target-dir', type=Path, help='Cargo working directory, used only by build')
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    binary = (args.output or root / 'build/placement-engine/agent-ee-placement').resolve()
    target_dir = (args.target_dir or root / 'build/placement-engine/target').resolve()
    if args.action == 'fetch':
        fetch(root)
    elif args.action == 'build':
        build(root, binary, target_dir)
    else:
        verify(root, binary)
    print(json.dumps({'action': args.action, 'status': 'ok',
                      **({'binary': str(binary), 'receipt': str(receipt_path(binary))}
                         if args.action != 'fetch' else {})}, sort_keys=True))


if __name__ == '__main__':
    main()
