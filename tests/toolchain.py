"""Test-only native prerequisites; check results and assertions are never caught."""
import functools
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_ee.executor import sandbox_argv


@functools.lru_cache(maxsize=None)
def prerequisite_problem(native=False):
    if shutil.which('bwrap') is None:
        return 'bwrap not found on PATH'
    with tempfile.TemporaryDirectory(prefix='agent-ee-prerequisites-') as tmp:
        root = Path(tmp)
        (root / 'inputs').mkdir()
        (root / 'work').mkdir()
        command = sandbox_argv(root / 'inputs', root / 'work', ['/usr/bin/true'])
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        if result.returncode:
            diagnostic = result.stderr.strip()
            if result.returncode == 1 and diagnostic in (
                'bwrap: Creating new namespace failed: Operation not permitted',
                'bwrap: No permissions to create new namespace, likely because the kernel '
                'does not allow non-privileged user namespaces. See '
                '<https://deb.li/bubblewrap> or <file:///usr/share/doc/bubblewrap/README.Debian.gz>.',
            ):
                return 'Bubblewrap/user namespaces unavailable: ' + diagnostic
            raise AssertionError(f'Bubblewrap prerequisite probe failed ({result.returncode}): '
                                 + diagnostic)
        if native:
            # The sandbox has a fixed PATH, independent of the launching shell.
            for tool in ('kicad-cli', 'ngspice'):
                result = subprocess.run(sandbox_argv(root / 'inputs', root / 'work',
                    ['/bin/sh', '-c', f'command -v {tool} >/dev/null; status=$?; '
                     'if [ "$status" -eq 1 ]; then exit 42; fi; exit "$status"']),
                    capture_output=True, text=True, timeout=15)
                if result.returncode == 42 and not result.stderr:
                    return f'{tool} not found in native sandbox'
                if result.returncode:
                    raise AssertionError(f'{tool} prerequisite probe failed ({result.returncode}): '
                                         + result.stderr)
            result = subprocess.run(sandbox_argv(root / 'inputs', root / 'work',
                ['/usr/bin/python3', '-c', 'import importlib.util; '
                 'raise SystemExit(0 if importlib.util.find_spec("pcbnew") else 42)']),
                capture_output=True, text=True, timeout=15)
            if result.returncode == 42:
                return 'pcbnew not installed for sandbox /usr/bin/python3'
            if result.returncode:
                raise AssertionError('pcbnew prerequisite probe failed: ' + result.stderr)
            scope = Path(__file__).resolve().parents[1] / 'fixtures/rc/control.scope.json'
            for item in json.loads(scope.read_text())['inputs']:
                source = Path(item['path'])
                if source.is_absolute():
                    for pattern in item.get('include', []):
                        if not any(source.glob(pattern)):
                            return f'KiCad fixture library missing: {source / pattern}'
    return None


def require_toolchain(*, native=False):
    problem = prerequisite_problem(native)
    if problem:
        reason = 'toolchain prerequisite: ' + problem
        if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1':
            raise AssertionError('AGENT_EE_REQUIRE_TOOLCHAIN=1: ' + reason)
        raise unittest.SkipTest(reason)


def toolchain_test(test):
    @functools.wraps(test)
    def gated(*args, **kwargs):
        require_toolchain()
        return test(*args, **kwargs)
    return gated
