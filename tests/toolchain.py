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
def prerequisite_problem(native=False, tools=()):
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
                'bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted',
                'bwrap: No permissions to create new namespace, likely because the kernel '
                'does not allow non-privileged user namespaces. See '
                '<https://deb.li/bubblewrap> or <file:///usr/share/doc/bubblewrap/README.Debian.gz>.',
            ):
                return 'Bubblewrap/user namespaces unavailable: ' + diagnostic
            raise AssertionError(f'Bubblewrap prerequisite probe failed ({result.returncode}): '
                                 + diagnostic)
        if native or tools:
            # The sandbox has a fixed PATH, independent of the launching shell.
            for tool in (('kicad-cli', 'ngspice') + tools if native else tools):
                result = subprocess.run(sandbox_argv(root / 'inputs', root / 'work',
                    ['/bin/sh', '-c', f'command -v {tool} >/dev/null; status=$?; '
                     'if [ "$status" -eq 1 ] || [ "$status" -eq 127 ]; then exit 42; fi; exit "$status"']),
                    capture_output=True, text=True, timeout=15)
                if result.returncode == 42 and not result.stderr:
                    return f'{tool} not found in native sandbox'
                if result.returncode:
                    raise AssertionError(f'{tool} prerequisite probe failed ({result.returncode}): '
                                         + result.stderr)
        if native:
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


def require_toolchain(*, native=False, tools=()):
    problem = prerequisite_problem(native, tuple(tools))
    if problem:
        reason = 'toolchain prerequisite: ' + problem
        if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1':
            raise AssertionError('AGENT_EE_REQUIRE_TOOLCHAIN=1: ' + reason)
        raise unittest.SkipTest(reason)


# Named partitions of required inputs that this repository does not carry.
# Under AGENT_EE_REQUIRE_TOOLCHAIN=1 an absent one always fails the test; the
# strict runner names those failures, and no setting turns them into a pass.
REQUIRED_INPUTS = ('fome-mc-store', 'pod-retained-inputs')
MISSING_INPUT = 'AGENT_EE_REQUIRE_TOOLCHAIN=1: required input {gap} missing: '


def unavailable(gap, reason):
    """An absent required input of the named partition: a skip, or a failure
    under AGENT_EE_REQUIRE_TOOLCHAIN=1 whose message names the partition so the
    strict runner reports it as not executed (docs/container.md). Never a pass."""
    if gap not in REQUIRED_INPUTS:
        raise ValueError(f'unknown required-input partition: {gap}')
    if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') != '1':
        raise unittest.SkipTest(reason)
    raise AssertionError(MISSING_INPUT.format(gap=gap) + reason)


def fome_mc_store(repository, *revisions):
    """The local fome-mc Git object store holding every pinned revision (HEAD if none).

    fome-mc design material is read from the owner's store, never committed or
    fetched here; an absent store or revision is the fome-mc-store gap."""
    repository = Path(repository)
    missing = [rev for rev in revisions or ('HEAD',) if not repository.is_dir() or subprocess.run(
        ['git', '-C', str(repository), 'cat-file', '-e', rev + '^{commit}'], capture_output=True).returncode]
    if missing:
        unavailable('fome-mc-store', f'fome-mc object store with {", ".join(r[:12] for r in missing)} '
                                     f'not available at {repository}')
    return repository


def toolchain_test(test):
    @functools.wraps(test)
    def gated(*args, **kwargs):
        require_toolchain()
        return test(*args, **kwargs)
    return gated


def require_kicad_route():
    """The configured kicad-route checkout, qualified against the adapter's pin.

    An unset AGENT_EE_KICAD_ROUTE_CHECKOUT is an absent optional tool: a skip,
    or a failure under AGENT_EE_REQUIRE_TOOLCHAIN=1. A configured checkout whose
    pin, code, state or KRT runtime does not qualify always fails."""
    configured = os.environ.get('AGENT_EE_KICAD_ROUTE_CHECKOUT')
    if not configured:
        reason = 'toolchain prerequisite: AGENT_EE_KICAD_ROUTE_CHECKOUT is unset (no pinned kicad-route checkout)'
        if os.environ.get('AGENT_EE_REQUIRE_TOOLCHAIN') == '1':
            raise AssertionError('AGENT_EE_REQUIRE_TOOLCHAIN=1: ' + reason)
        raise unittest.SkipTest(reason)
    from agent_ee import routing_adapter
    check = routing_adapter.preflight(configured)
    if check['problems']:
        raise AssertionError(f'AGENT_EE_KICAD_ROUTE_CHECKOUT={configured} does not qualify: '
                             + '; '.join(check['problems']))
    state = Path(check['state'])
    # KRT's runtime imports, as its startup checks spell them; -B writes no bytecode.
    result = subprocess.run([str(state / '.venv-krt/bin/python'), '-I', '-B', '-c',
                             'import sys; sys.path.insert(0, "rust_router"); import grid_router, numpy; '
                             'from scipy.optimize import linear_sum_assignment; '
                             'from shapely.geometry import Polygon'],
                            cwd=state / 'vendor/krt', capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise AssertionError(f'AGENT_EE_KICAD_ROUTE_CHECKOUT={configured}: KRT runtime import failed: '
                             + result.stderr.strip()[-2000:])
    return Path(check['checkout'])
