"""One kicad-route attempt inside the networkless sandbox (AGENTEE-93).

This is the in-sandbox half of the routing adapter. It imports only the
standard library, so the coordinator can run it as
``/usr/bin/python3 -I -B /opt/agent-ee-routing/kicad_route_attempt.py`` from a
read-only script mount whose bytes it hashes. Exact net binding reuses the
shared net_binding and symbol_library modules, mounted read-only beside it.

kicad-route is used at whatever commit its checkout holds. Each attempt fixes
that version for the run and records it in its report (tool.commit, tool.clean,
tool.code_sha256): a checkout with uncommitted or untracked changes, or one whose
commit cannot be read, is refused before anything runs. The adapter checks
kicad-route's versioned result contract; unknown schemas and result shapes are
refused (`classify`), so a router change fails closed. The router's argv and environment come from constants and
coordinator-declared arguments matched against fixed patterns; nothing the
writer controls reaches them, and ``--backend-env``, ``--force``, ``--canary``
and ``--rev`` are never passed. ``--backend-arg`` carries only KRT's
``--layers``/``--layer-costs`` built from a declared layer policy (AGENTEE-231).
``--experiment-policy`` names only the adapter-written five-field
routability-policy/1 file (AGENTEE-442).

The report is provenance. It never makes a check pass: acceptance comes only
from agent-ee's own fresh checks on the captured proposal.
"""

import collections
import collections.abc
import contextlib
import contextvars
import errno
import hashlib
import importlib.util
import json
import math
import os
import posixpath
import pwd
import re
import secrets
import shutil
import signal
import site
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath

RESULT_SCHEMA = 'kicad-route/route/1'
NATIVE_OPERATION_SCHEMA = 'kicad-route/native-operation/1'
NATIVE_REQUEST_FILE = 'operation-request.json'
NATIVE_OPERATION_FILE = 'operation-result.json'
COMMIT = re.compile(r'[0-9a-f]{40}\Z')
# Clean-tree state comes from git with optional locks off, so a read-only checkout is
# never written; repository configuration cannot start a monitor or a hook.
GIT = '/usr/bin/git'
GIT_STATUS = (GIT, '-c', 'safe.directory=*', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
              '-c', 'core.untrackedCache=false', 'status', '--porcelain=v1', '-z', '--untracked-files=all',
              '--ignore-submodules=none')
GIT_ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C', 'GIT_OPTIONAL_LOCKS': '0',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0'}
# The checkout record every report holds in `tool` (checkout_record).
CHECKOUT_KEYS = ('commit', 'clean', 'code_sha256', 'code_files')
# A bytecode cache prefix that can hold no bytecode: nothing can be created under a device file.
NO_PYCACHE = '/dev/null'
# The interpreter flags of every command that runs Python from a kicad-route checkout or its
# environment outside the sandboxed attempt (routing_adapter.router_python, pose_milp.child_argv):
# -B stops writing bytecode, and a cache prefix that can hold none stops Python from reading bytecode
# cached beside the checkout's sources, which the recorded code digest (code_files) excludes. An
# -X option overrides any inherited PYTHONPYCACHEPREFIX, so no caller's environment can undo it.
# Python processes such a command starts inherit the environment but not -X options, so the
# command's environment carries a private prefix as well (routing_adapter.router_env).
ROUTER_PYTHON_FLAGS = ('-B', '-X', f'pycache_prefix={NO_PYCACHE}')

REPORT_SCHEMA = 'agent-ee.kicad-route-attempt/1'
# Every report has exactly these keys, whatever the outcome.
REPORT_KEYS = {'schema', 'adapter', 'request', 'reproducibility', 'tool_version', 'backend', 'process',
               'discovery', 'missing_artifacts', 'retained', 'omitted_design_files', 'candidate',
               'candidate_editor_state', 'candidate_files', 'rejected_candidate_sha256', 'inputs', 'tool',
               'state_write_probe', 'identity_before', 'identity_after', 'semantics', 'identity'}
TOOL_VERSION = {'status': 'unknown', 'value': None,
                'reason': 'kicad-route has no --version; identity is the recorded checkout commit and code digest'}
# Reports written before 2026-11-03 compared the checkout with a committed table of one
# commit's file digests and carry tool.pin; they verify against what they recorded.
LEGACY_TOOL_VERSION = {'status': 'unknown', 'value': None,
                       'reason': 'kicad-route at the pin has no --version; identity is the pinned code'}
LEGACY_TABLE_AT = '/opt/agent-ee-routing/data/kicad-route-pin.json'
NO_BACKEND = {'status': 'unknown', 'value': None, 'reason': 'no result.json'}
SANDBOX = {'work': '/work', 'candidate': '/candidate', 'checkout': '/opt/kicad-route',
           'state': '/opt/kicad-route-state',
           'engine': '/opt/kicad-route-engine/kicad-route-engine'}
# AGENTEE-442 (agent-507 origin adjudication; agent-449 seam decision): kicad-route
# experiment._origin reads a --dir whose parent is named 'candidate', or whose
# grandparent holds attempt.json or result.json, as a run's candidate with a missing
# predecessor, so the read-only /candidate/<project> mount is never an original. The
# mount stays; the router is given a real, byte-exact copy of the verified input tree,
# staged in routing/ under this neutral name (never in the router's --out), and
# report.inputs.staged records and binds it.
ORIGINAL_STAGE = 'agent-ee-original'
PYTHON = '/usr/bin/python3'
# KICADROUTE-82: 'builtin' is the canonical name of the owned backend and 'krt' its deprecated
# alias. agent-ee emits only 'builtin' (AGENTEE-450). 'krt' is read-only legacy: a request or
# retained record keeps the name it was written with (receipts that say 'krt' keep their
# bytes), and names are normalized only to compare or look up.
BACKEND_ALIASES = {'krt': 'builtin'}
BACKENDS = ('builtin', 'krt', 'freerouting')
# What cli.py is given for --backend: the canonical name, whatever the request spelled.
CLI_BACKEND_NAMES = {'builtin': 'builtin', 'krt': 'builtin', 'freerouting': 'freerouting'}


def canonical_backend(name):
    """The canonical spelling of a backend name; an unknown or non-string name is returned unchanged."""
    return BACKEND_ALIASES.get(name, name) if isinstance(name, str) else name


# Installed backend inputs cli.py reads under KICAD_ROUTE_STATE, in the
# legacy layout (no router generation selected; the image's install.sh builds this).
STATE_COMPONENTS = {'krt': ('.venv-krt',), 'builtin': ('.venv-krt',), 'freerouting': ('vendor/freerouting',)}
STATE_REQUIRED = {'krt': ('.venv-krt/bin/python',), 'builtin': ('.venv-krt/bin/python',),
                  'freerouting': ('vendor/freerouting',)}
# A router generation (`kicad-route update`) is one directory under the state
# that holds the copied engine source and its venv; the manifest's krt.generation selects
# it. These are the files kicad-route's common.router_generation requires before selecting it.
GENERATION = re.compile(r'\.router-generations/[0-9a-f]{32}\Z')
GENERATION_REQUIRED = ('installation.json', 'router/VERSION', 'router/UPSTREAM.json', 'venv/bin/python')


def state_generation(state):
    """The router generation the state manifest at `state` selects, or None (legacy layout).

    Returns (generation, problems). An absent or unreadable manifest selects nothing
    here; verify_state reports it. A selection kicad-route would not resolve, or one
    outside STATE/.router-generations, is a problem, never a fallback to .venv-krt."""
    try:
        manifest = json.loads((Path(state) / 'manifest.json').read_bytes())
    except (OSError, ValueError):
        return None, []
    krt = manifest.get('krt') if isinstance(manifest, dict) else None
    generation = krt.get('generation') if isinstance(krt, dict) else None
    if generation is None:
        return None, []
    if not isinstance(generation, str) or not GENERATION.match(generation):
        return None, [f'state manifest selects an invalid router generation {generation!r}']
    return generation, []


def state_components(backend, generation=None):
    """Installed state directories the backend reads: the selected generation, or the legacy ones."""
    if canonical_backend(backend) == 'builtin' and generation is not None:
        return (generation,)
    return STATE_COMPONENTS[backend]


def state_required(backend, generation=None):
    """Installed files the backend cannot start without, for the state's layout."""
    if canonical_backend(backend) == 'builtin' and generation is not None:
        return tuple(f'{generation}/{rel}' for rel in GENERATION_REQUIRED)
    return STATE_REQUIRED[backend]


def generation_of_components(backend, components):
    """Recover the layout a report's state identities were taken in; ValueError if none matches."""
    components = tuple(components)
    if components == STATE_COMPONENTS[backend]:
        return None
    if (canonical_backend(backend) == 'builtin' and len(components) == 1 and
            isinstance(components[0], str) and GENERATION.match(components[0])):
        return components[0]
    raise ValueError('state identities name neither the legacy nor a router-generation layout')


def runtime_paths(layout, generation):
    """backend.runtime as kicad-route reports it when it runs the engine verified in this layout."""
    checkout, state = Path(layout['checkout']), Path(layout['state'])
    if generation is None:
        return {'source': str(checkout / 'src/kicad_route/router'), 'venv': str(state / '.venv-krt'),
                'generation': None}
    return {'source': str(state / generation / 'router'), 'venv': str(state / generation / 'venv'),
            'generation': str(state / generation)}
SAFE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z')
STAMP = re.compile(r'\d{8}T\d{6}\.\d{6}Z\Z')
# Every top-level result.json/attempt.json key cli.run_attempt writes
# (protect=existing). Any other shape is an incompatible tool.
RESULT_KEYS = {'schema', 'measurement', 'evidence', 'tool_identity', 'sandbox', 'kind', 'backend',
               'input', 'settings', 'scope', 'unsupported', 'floor', 'process',
               'routing', 'validation', 'candidate', 'run_dir', 'runtime_s', 'tool_versions',
               'finished_utc', 'exit_code', 'ledger'}
# Added by kicad-route only for a builtin attempt whose process passed (KICADROUTE copper custody).
# It is provenance with replay_authority false; agent-ee binds its bytes and never grades from it.
COPPER_OWNERSHIP_RECORD = 'validate/copper-ownership.json'
COPPER_OWNERSHIP_BOUND = {'status', 'record', 'sha256', 'coverage', 'replay_authority'}
COPPER_OWNERSHIP_UNAVAILABLE = {'status', 'reasons', 'replay_authority'}
# Nested keys kicad-route adds (KICADROUTE generations and measurement qualification): every
# result.json's process carries measurement_qualification, and an owned-engine result's
# backend carries the runtime it executed, which must be the engine verified before the run.
MEASUREMENT_QUALIFICATION = {'measurement', 'build_reproducible', 'reasons'}
RUNTIME_KEYS = {'source', 'venv', 'generation'}
ATTEMPT_KEYS = {'schema', 'backend', 'project', 'board', 'schematic', 'input_fingerprint', 'source',
                'reference_dir', 'candidate_dir', 'reference_facts', 'baseline', 'scope',
                'settings', 'floor', 'rules_files'}
PROCESS = ('PASS', 'FAIL')
ROUTING = ('COMPLETE', 'INCOMPLETE', 'NOT_RUN')
VALIDATION = ('PASS', 'FAIL', 'NOT_RUN')
RETAINED_FILES = ('result.json', 'attempt.json', 'RESULT.md', 'snapshot.json')
# Reports an attempt that wrote a result is expected to have; absent ones are listed, never invented.
EXPECTED_REPORTS = ('result.json', 'attempt.json', 'RESULT.md', 'validate')
RETAINED_DIRS = ('validate', 'facts', 'work')
# Design files never enter the retained diagnostics: the only board that can
# leave the attempt is the validated candidate of an eligible exit.
DESIGN_SUFFIXES = ('.kicad_pcb', '.kicad_sch', '.kicad_pro', '.kicad_dru', '.kicad_prl',
                   '.kicad_sym', '.kicad_mod', '.kicad_wks')
JSON_LIMIT = 16 * 1024 * 1024
EXIT_REQUEST, EXIT_CONTRADICTION, EXIT_ADAPTER_ERROR, EXIT_TIMEOUT, EXIT_CANCELLED = 64, 65, 70, 75, 143
EXIT_POLICY = 5
# classification -> (proposal, routing coverage, adapter exit)
CLASSES = {
    'complete': ('eligible', 'routing_complete', 0),
    'incomplete': ('eligible_partial', 'routing_incomplete', 2),
    'backend_failure': ('none', 'missing_routing', 1),
    'validation_failed': ('none', 'missing_routing', 3),
    'refused': ('none', 'missing_routing', 4),
    'policy_failed': ('none', 'missing_routing', EXIT_POLICY),
    'contradiction': ('none', 'unknown', EXIT_CONTRADICTION),
    'timeout': ('none', 'unknown', EXIT_TIMEOUT),
    'cancelled': ('none', 'unknown', EXIT_CANCELLED),
    'not_run': ('none', 'unknown', EXIT_REQUEST),
}
NATIVE_EXIT_STATUSES = {
    0: ('complete',),
    2: ('invalid', 'unsupported', 'native_validation_failed'),
    3: ('unroutable',),
    4: ('budget_exhausted',),
    5: ('work_exhausted',),
    6: ('admission_exceeded', 'journal_exhausted', 'nesting_exhausted',
        'operation_timeout', 'cancelled', 'child_output_exceeded'),
    7: ('internal_error', 'child_process_error'),
    8: ('usage_refused', 'publication_refused', 'destination_exists', 'ambiguous_termination'),
}
REPRODUCIBILITY = {'krt': 'deterministic backend, repeatability not qualified by this attempt',
                   'builtin': 'deterministic backend, repeatability not qualified by this attempt',
                   'freerouting': 'attempt only; never a reproducible measurement'}


class RequestError(ValueError):
    pass


class RE01OwnershipError(ValueError):
    """Raised when RE-01 forbids executing legacy Python router entry points."""
    pass


def _git_main_repo_root(repo_root):
    repo_root = Path(repo_root)
    git_entry = repo_root / '.git'
    if git_entry.is_file():
        try:
            text = git_entry.read_text(encoding='utf-8', errors='replace')
        except OSError:
            return repo_root
        for line in text.splitlines():
            if line.startswith('gitdir:'):
                raw = line[len('gitdir:'):].strip()
                gitdir = Path(raw) if Path(raw).is_absolute() else (repo_root / raw).resolve()
                for candidate in (gitdir, *gitdir.parents):
                    if candidate.name == '.git':
                        return candidate.parent
    return repo_root


def _agent_ee_repo_root():
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / 'src' / 'agent_ee').is_dir() and (parent / 'pyproject.toml').is_file():
            return parent
    return here.parents[2] if len(here.parents) > 2 else here.parent


def resolve_kicad_route_checkout(explicit=None):
    """Resolve the kicad-route checkout: explicit -> env -> /opt/kicad-route -> sibling ../kicad-route."""
    if explicit is not None and str(explicit).strip():
        return Path(str(explicit).strip()).expanduser()
    env = os.environ.get('AGENT_EE_KICAD_ROUTE_CHECKOUT', '').strip()
    if env:
        return Path(env).expanduser()
    sandbox_checkout = Path(SANDBOX['checkout'])
    if sandbox_checkout.is_dir():
        return sandbox_checkout
    repo_root = _agent_ee_repo_root()
    main_root = _git_main_repo_root(repo_root)
    for base in (repo_root, main_root):
        sibling = base.parent / 'kicad-route'
        if sibling.is_dir():
            return sibling
    return sandbox_checkout


def is_rust_capable_checkout(checkout):
    """True when the checkout carries the Rust native bridge (tools/rust_engine_kicad.py)."""
    if not checkout:
        return False
    bridge = Path(checkout) / 'tools' / 'rust_engine_kicad.py'
    return bridge.is_file() and not bridge.is_symlink()


def resolve_native_bridge(checkout=None):
    """Resolve tools/rust_engine_kicad.py for the given or resolved checkout, or None."""
    co = resolve_kicad_route_checkout(checkout)
    bridge = Path(co) / 'tools' / 'rust_engine_kicad.py'
    return bridge if bridge.is_file() and not bridge.is_symlink() else None


def resolve_kicad_route_engine(checkout=None):
    """Resolve kicad-route-engine: env -> checkout target -> sandbox mount -> sibling/PATH (implicit only)."""
    for var in ('AGENT_EE_KICAD_ROUTE_ENGINE', 'KICAD_ROUTE_ENGINE_BIN'):
        val = os.environ.get(var, '').strip()
        if val:
            return Path(val).expanduser()
    env_checkout = os.environ.get('AGENT_EE_KICAD_ROUTE_CHECKOUT', '').strip()
    explicit = (checkout is not None and str(checkout).strip() != '') or bool(env_checkout)
    co = resolve_kicad_route_checkout(checkout)
    for profile in ('release', 'debug'):
        cand = Path(co) / 'rust-engine' / 'target' / profile / 'kicad-route-engine'
        if cand.is_file():
            return cand
    sandbox_engine = Path(SANDBOX['engine'])
    if sandbox_engine.is_file() and (not explicit or Path(co) == Path(SANDBOX['checkout'])):
        return sandbox_engine
    if explicit:
        return None
    repo_root = _agent_ee_repo_root()
    main_root = _git_main_repo_root(repo_root)
    seen = set()
    for base in (Path(co).parent, repo_root.parent, main_root.parent):
        sib = base / 'kicad-route'
        if sib in seen or not sib.is_dir():
            continue
        seen.add(sib)
        for profile in ('release', 'debug'):
            cand = sib / 'rust-engine' / 'target' / profile / 'kicad-route-engine'
            if cand.is_file():
                return cand
    which = shutil.which('kicad-route-engine')
    return Path(which) if which else None


_RE01_DEPTH = contextvars.ContextVar('agent_ee_re01_depth', default=0)
_RE01_HOOK_INSTALLED = False


def is_re01_enforced():
    return _RE01_DEPTH.get() > 0


_RE01_MAX_DEPTH = 8
_RE01_SHELL_NAMES = frozenset((
    'sh', 'bash', 'dash', 'zsh', 'ksh', 'ash', 'csh', 'tcsh', 'fish', 'busybox',
    'mksh', 'pdksh', 'rbash', 'rksh', 'rzsh', 'static-sh', 'sh.distrib', 'bashbug',
    'git-shell', 'byobu-shell', 'toybox', 'pwsh', 'xonsh', 'elvish', 'nu', 'yash',
    'posh', 'oksh', 'lksh',
))
_RE01_LAUNCHER_NAMES = frozenset((
    'env', 'xargs', 'find', 'nice', 'nohup', 'timeout', 'stdbuf', 'setsid',
    'strace', 'ltrace', 'taskset', 'chrt', 'ionice', 'parallel', 'chroot',
    'su', 'sudo', 'doas', 'gnuenv', 'gnuchroot', 'gnunice', 'gnunohup',
    'gnustdbuf', 'gnutimeout', 'gnuruncon', 'coreutils', 'su-rs', 'sudo-rs',
    'sudo.ws', 'script', 'scriptlive', 'flock', 'lckdo', 'time', 'uv', 'uvx',
    'pip', 'pip3', 'pipx', 'pytest', 'py.test', 'pydoc', 'pydoc3', 'pdb', 'pdb3',
    'isympy', 'isympy3', 'trial', 'trial3', 'mise', 'watch', 'nsenter', 'unshare',
    'setpriv', 'capsh', 'pkexec', 'aa-exec', 'eatmydata', 'fakeroot',
    'fakeroot-sysv', 'fakeroot-tcp', 'runcon', 'runuser', 'run0', 'systemd-run',
    'systemd-inhibit', 'run-one', 'run-one-constantly', 'run-one-until-failure',
    'run-one-until-success', 'run-this-one', 'keep-one-running', 'run-parts',
    'chronic', 'vipe', 'zrun', 'ifne', 'mispipe', 'pee', 'prlimit', 'choom',
    'coresched', 'uclampset', 'fadvise', 'xvfb-run', 'dbus-run-session', 'socat',
    'socat1', 'tmux', 'screen', 'byobu', 'byobu-tmux', 'byobu-screen',
    'byobu-launch', 'byobu-launcher', 'ssh', 'mosh', 'gdb', 'rust-gdb', 'lldb',
    'rust-lldb', 'valgrind', 'perf', 'bpftrace', 'trace-cmd', 'sotruss',
    'memusage', 'mpirun', 'mpirun.openmpi', 'mpiexec', 'mpiexec.openmpi',
    'oshrun', 'prterun', 'prun', 'cafrun', 'cafrun.openmpi', 'vcs-run',
    'setarch', 'linux32', 'linux64', 'i386', 'x86_64', 'pivot_root', 'enosys',
    'rlwrap', 'direnv', 'entr', 'at', 'batch', 'docker', 'runc', 'ctr',
    'slirp4netns', 'rootlesskit', 'antigravity', 'claude', 'codex', 'herdr',
    'hermes', 'agy',
    'ld', 'ld.so', 'ld-linux', 'ld-musl', 'ld64', 'ld-elf', 'ld-lsb', 'ld-uClibc',
    'qemu', 'proot', 'firejail', 'flatpak-spawn', 'podman', 'toolbox', 'distrobox',
    'singularity', 'apptainer', 'rr', 'gdbserver', 'box64', 'box86', 'fex',
    'tar', 'rsync', 'scp', 'sftp', 'man', 'less', 'more', 'pg',
    'git-upload-pack', 'git-receive-pack', 'git-upload-archive',
))
_RE01_ALT_RUNTIMES = frozenset((
    'perl', 'ruby', 'node', 'nodejs', 'awk', 'gawk', 'mawk', 'nawk',
    'sed', 'lua', 'php', 'tclsh', 'expect', 'make', 'gmake', 'cmake',
    'ctest', 'cpack', 'rake', 'irb', 'gem', 'bundle', 'bundler', 'rdbg',
    'erb', 'wish', 'texlua', 'texluac', 'luahbtex', 'luatex', 'js', 'npm',
    'npx', 'corepack', 'deno', 'bun', 'guile', 'julia', 'Rscript', 'm4', 'dc',
    'sqlite3', 'ed', 'ex', 'vi', 'vim', 'nvim', 'rview', 'rvim', 'prove',
    'cpan', 'java', 'jshell', 'go', 'cargo',
))
_RE01_ALWAYS_PY_HOOKS = frozenset((
    'sitecustomize', 'usercustomize', '_distutils_hack', 'apport_python_hook',
    '_sitebuiltins', 'kicad_route', 'native_delay', 'grid_router',
    'pcbnew', '_pcbnew',
))
_RE01_PY_HOOK_NAMES = frozenset(sys.stdlib_module_names) | _RE01_ALWAYS_PY_HOOKS
_RE01_ALLOWED_EXECUTABLES = frozenset((
    'kicad-route-engine', 'bwrap', 'kicad-cli', 'true', 'false', 'echo',
))
_RE01_SYSTEM_EXEC_PREFIXES = (
    '/usr/bin/', '/bin/', '/usr/local/bin/', '/usr/lib/cargo/bin/coreutils/',
)
_RE01_LD_FORBIDDEN_ENV = frozenset((
    'LD_PRELOAD', 'LD_AUDIT', 'LD_PROFILE', 'LD_DEBUG_OUTPUT', 'GCONV_PATH', 'GLIBC_TUNABLES',
))
_RE01_PYTHON_FORBIDDEN_ENV = frozenset((
    'PYTHONSTARTUP', 'PYTHONINSPECT', 'PYTHONHOME', 'PYTHONUSERBASE',
    'PYTHONEXECUTABLE', '__PYVENV_LAUNCHER__', 'PYTHONPLATLIBDIR',
    'PYTHONWARNINGS', 'PYTHON_FROZEN_MODULES', 'PYTHONBREAKPOINT',
    'PYTHONDEVMODE', 'PYTHONDEBUG', 'PYTHONCASEOK',
))
_RE01_PY_ALLOWED_SHORT_OPTS = frozenset('BEIOPqsSuv')
_RE01_BWRAP_ARITY = {
    '--unshare-all': 0, '--share-net': 0, '--unshare-user': 0, '--unshare-user-try': 0,
    '--unshare-ipc': 0, '--unshare-pid': 0, '--unshare-net': 0, '--unshare-uts': 0,
    '--unshare-cgroup': 0, '--unshare-cgroup-try': 0, '--disable-userns': 0,
    '--assert-userns-disabled': 0, '--clearenv': 0, '--new-session': 0,
    '--die-with-parent': 0, '--as-pid-1': 0, '--level-prefix': 0,
    '--argv0': 1, '--userns': 1, '--userns2': 1, '--pidns': 1, '--uid': 1, '--gid': 1,
    '--hostname': 1, '--chdir': 1, '--unsetenv': 1, '--lock-file': 1, '--sync-fd': 1,
    '--remount-ro': 1, '--proc': 1, '--dev': 1, '--tmpfs': 1, '--mqueue': 1, '--dir': 1,
    '--seccomp': 1, '--add-seccomp-fd': 1, '--block-fd': 1, '--userns-block-fd': 1,
    '--info-fd': 1, '--json-status-fd': 1, '--cap-add': 1, '--cap-drop': 1,
    '--perms': 1, '--size': 1, '--exec-label': 1, '--file-label': 1,
    '--setenv': 2, '--bind': 2, '--bind-try': 2, '--dev-bind': 2, '--dev-bind-try': 2,
    '--ro-bind': 2, '--ro-bind-try': 2, '--symlink': 2, '--chmod': 2,
}
_RE01_BWRAP_FORBIDDEN_OPTS = frozenset((
    '--args', '--bind-fd', '--ro-bind-fd', '--file', '--bind-data', '--ro-bind-data',
    '--overlay-src', '--overlay', '--tmp-overlay', '--ro-overlay',
))
_RE01_BWRAP_BIND_SRC_OPTS = frozenset((
    '--bind', '--bind-try', '--dev-bind', '--dev-bind-try', '--ro-bind', '--ro-bind-try',
))
_RE01_BWRAP_TRY_OPTS = frozenset(('--bind-try', '--dev-bind-try', '--ro-bind-try'))
_RE01_BWRAP_OPAQUE_OPTS = frozenset(('--tmpfs', '--proc', '--dev', '--mqueue'))


_RE01_NUL_OR_SURROGATE_RE = re.compile(r'[\x00\ud800-\udfff]')
_RE01_SEC_ENV_EXACT = frozenset((
    'PATH', 'HOME', 'PYTHONPATH', 'TMPDIR', 'VIRTUAL_ENV',
    'GCONV_PATH', 'GLIBC_TUNABLES', '__PYVENV_LAUNCHER__',
))
_RE01_SEC_ENV_PREFIXES = ('KICAD', 'AGENT_EE', 'PYTHON', 'LD_')


def _re01_has_nul_or_surrogate(s):
    return bool(s and _RE01_NUL_OR_SURROGATE_RE.search(s))


def _re01_is_security_env_key(key):
    return key in _RE01_SEC_ENV_EXACT or key.startswith(_RE01_SEC_ENV_PREFIXES)


def _decode_audit_arg(val):
    if isinstance(val, (bytes, bytearray, memoryview)):
        return os.fsdecode(bytes(val))
    try:
        raw = os.fspath(val)
    except TypeError:
        return str(val)
    if isinstance(raw, (bytes, bytearray, memoryview)):
        return os.fsdecode(bytes(raw))
    return str(raw)


def _re01_decode_env(env):
    raw_env = os.environ if env is None else env
    try:
        items = list(raw_env.items())
    except AttributeError:
        return None, 'RE-01 forbids invalid environment mapping under audit enforcement'
    decoded = {}
    for raw_k, raw_v in items:
        k = _decode_audit_arg(raw_k)
        v = _decode_audit_arg(raw_v)
        if _re01_has_nul_or_surrogate(k):
            return None, f'RE-01 forbids NUL or lone surrogate in environment key {k!r} under audit enforcement'
        if _re01_is_security_env_key(k) and _re01_has_nul_or_surrogate(v):
            return None, f'RE-01 forbids NUL or lone surrogate in {k} under audit enforcement'
        if k in decoded:
            return None, f'RE-01 forbids duplicate environment key {k!r} under audit enforcement'
        decoded[k] = v
    return decoded, None


def _re01_norm_posix(path_str):
    norm = posixpath.normpath(path_str)
    while norm.startswith('//'):
        norm = norm[1:]
    return norm


def _re01_safe_is_file(path_str, cwd=None):
    try:
        p = Path(path_str)
        if not p.is_absolute() and cwd is not None:
            p = Path(cwd) / p
        return p.is_file()
    except (OSError, ValueError):
        return False


def _re01_matches_cmd_name(base, names):
    if base in names:
        return True
    for n in names:
        if base.startswith(n) and len(base) > len(n) and base[len(n)] in '0123456789.-':
            return True
    return False


def _re01_is_dynamic_linker(base):
    return bool(re.fullmatch(r'ld(?:[-.].*|\.so(?:\.\d+)*)?', base))


def _re01_is_python_name(base):
    return bool(re.fullmatch(r'(?:python(?:\d+(?:\.\d+)*)?|pypy(?:\d+(?:\.\d+)*)?)', base))


def _re01_exec_category(base):
    if _re01_is_python_name(base):
        return 'python'
    if base in ('true', 'gnutrue'):
        return 'true'
    if base in ('false', 'gnufalse'):
        return 'false'
    if base in _RE01_ALLOWED_EXECUTABLES:
        return base
    return None


def _re01_is_sourceless_pyc(name):
    return bool(name) and name.lower().endswith(('.pyc', '.pyo'))


def _re01_is_python_ext_module(name):
    if not name:
        return False
    base = posixpath.basename(str(name).rstrip('/'))
    return base.lower().endswith('.so')


def _re01_is_always_py_hook_name(name):
    if not name:
        return False
    return name in _RE01_ALWAYS_PY_HOOKS or name.split('.', 1)[0] in _RE01_ALWAYS_PY_HOOKS


def _re01_is_py_hook_name(name):
    if not name:
        return False
    if name in _RE01_PY_HOOK_NAMES or name.startswith('_sysconfigdata'):
        return True
    return name.split('.', 1)[0] in _RE01_PY_HOOK_NAMES


def _re01_abs_cwd(cwd, *, mounts=None, with_chain=False):
    if cwd is None and mounts is None:
        cwd_path = os.getcwd()
        if _re01_has_nul_or_surrogate(cwd_path):
            err = f'RE-01 forbids NUL or lone surrogate in cwd under audit enforcement: {cwd_path!r}'
            return (None, [], err) if with_chain else (None, err)
        return (cwd_path, [cwd_path], None) if with_chain else (cwd_path, None)
    raw = os.getcwd() if cwd is None else cwd
    if not raw:
        err = 'RE-01 forbids empty cwd under audit enforcement'
        return (None, [], err) if with_chain else (None, err)
    if _re01_has_nul_or_surrogate(raw):
        err = f'RE-01 forbids NUL or lone surrogate in cwd under audit enforcement: {raw!r}'
        return (None, [], err) if with_chain else (None, err)
    if not posixpath.isabs(raw):
        raw = posixpath.join(os.getcwd(), raw)
    seen = []
    _, _, resolved_sb, err = _re01_follow_path(raw, mounts, seen=seen, seen_set=set())
    if err is not None:
        return (None, seen, err) if with_chain else (None, err)
    return (resolved_sb, seen, None) if with_chain else (resolved_sb, None)


def _re01_resolve_parent_leaf(raw_path, *, cwd=None, mounts=None):
    base_dir, cwd_err = _re01_abs_cwd(cwd, mounts=mounts)
    if cwd_err is not None:
        return None, cwd_err
    raw_abs = raw_path if posixpath.isabs(raw_path) else posixpath.join(base_dir, raw_path)
    raw_segs = [s for s in raw_abs.split('/') if s]
    if not raw_segs:
        return '/', None
    if raw_abs.endswith('/') or raw_segs[-1] in ('.', '..'):
        return None, None
    raw_parent = '/' + '/'.join(raw_segs[:-1])
    _, _, p_sb, p_err = _re01_follow_path(raw_parent, mounts, seen=[], seen_set=set())
    if p_err is not None:
        return None, p_err
    return posixpath.join(p_sb, raw_segs[-1]), None


def _re01_mount_lookup(norm, mounts):
    if not mounts:
        return None, None, None
    best_dest = None
    for dest in mounts:
        if norm == dest or dest == '/' or norm.startswith(dest + '/'):
            if best_dest is None or len(dest) > len(best_dest):
                best_dest = dest
    if best_dest is None:
        return None, None, None
    kind, target = mounts[best_dest]
    if kind == 'opaque':
        return 'opaque', best_dest, None
    if norm == best_dest:
        return kind, best_dest, target
    rel = norm[1:] if best_dest == '/' else norm[len(best_dest) + 1:]
    return kind, best_dest, _re01_norm_posix(posixpath.join(target, rel))


def _re01_shadow_mounts(mounts, dest_norm):
    for existing in [
        k for k in mounts
        if k == dest_norm or dest_norm == '/' or k.startswith(dest_norm + '/')
    ]:
        del mounts[existing]


def _re01_resolve_mount_symlinks(raw_path, mounts):
    pending = collections.deque(s for s in raw_path.split('/') if s and s != '.')
    parts = []
    hops = 0
    while pending:
        seg = pending.popleft()
        if seg == '..':
            if parts:
                parts.pop()
            continue
        parts.append(seg)
        sb_path = '/' + '/'.join(parts)
        entry = mounts.get(sb_path)
        if entry is not None and entry[0] == 'symlink':
            hops += 1
            if hops > 40:
                return None, f'RE-01 forbids excessive bwrap symlink nesting: {raw_path}'
            if posixpath.isabs(entry[1]):
                parts = []
            else:
                parts.pop()
            pending.extendleft(reversed([s for s in entry[1].split('/') if s and s != '.']))
            continue
        kind, best_dest, mapped = _re01_mount_lookup(sb_path, mounts)
        if kind == 'bind' and sb_path != best_dest:
            try:
                is_link = os.path.islink(mapped)
            except (OSError, ValueError):
                is_link = False
            if is_link:
                hops += 1
                if hops > 40:
                    return None, f'RE-01 forbids excessive bwrap symlink nesting: {raw_path}'
                try:
                    target = os.readlink(mapped)
                except OSError:
                    break
                if posixpath.isabs(target):
                    parts = []
                else:
                    parts.pop()
                pending.extendleft(reversed([s for s in target.split('/') if s and s != '.']))
    return '/' + '/'.join(parts), None


def _re01_follow_path(raw_path, mounts, *, seen, seen_set):
    def record(path_val):
        if path_val:
            n = _re01_norm_posix(path_val)
            if n not in seen_set:
                seen_set.add(n)
                seen.append(n)

    pending = collections.deque(s for s in raw_path.split('/') if s and s != '.')
    sandbox_parts = []
    hops = 0
    while pending:
        seg = pending.popleft()
        if seg == '..':
            if sandbox_parts:
                pre_pop = '/' + '/'.join(sandbox_parts)
                if _re01_path_reason(pre_pop, raw_path) is not None:
                    record(pre_pop)
                sandbox_parts.pop()
            continue
        sandbox_parts.append(seg)
        sb_path = '/' + '/'.join(sandbox_parts)
        if mounts:
            entry = mounts.get(sb_path)
            if entry is not None and entry[0] == 'symlink':
                hops += 1
                if hops > 40:
                    return None, None, None, f'RE-01 forbids excessive symlink nesting: {raw_path}'
                record(sb_path)
                if '..' not in entry[1].split('/'):
                    record(entry[1])
                if posixpath.isabs(entry[1]):
                    sandbox_parts = []
                else:
                    sandbox_parts.pop()
                pending.extendleft(reversed([s for s in entry[1].split('/') if s and s != '.']))
                continue
            kind, best_dest, mapped = _re01_mount_lookup(sb_path, mounts)
            if kind == 'opaque':
                continue
            if kind == 'bind':
                host_cand = mapped
                check_host_link = (sb_path != best_dest)
            else:
                if '/' in mounts or any(k.startswith(sb_path + '/') for k in mounts):
                    continue
                host_cand = sb_path
                check_host_link = True
        else:
            host_cand = sb_path
            check_host_link = True
        if check_host_link:
            try:
                is_link = os.path.islink(host_cand)
            except (OSError, ValueError):
                is_link = False
            if is_link:
                hops += 1
                if hops > 40:
                    return None, None, None, f'RE-01 forbids excessive symlink nesting: {raw_path}'
                record(sb_path)
                record(host_cand)
                try:
                    target = os.readlink(host_cand)
                except OSError:
                    break
                if posixpath.isabs(target):
                    if '..' not in target.split('/'):
                        record(target)
                    sandbox_parts = []
                else:
                    sandbox_parts.pop()
                pending.extendleft(reversed([s for s in target.split('/') if s and s != '.']))
    final_sb = '/' + '/'.join(sandbox_parts)
    record(final_sb)
    if mounts:
        kind, _, mapped = _re01_mount_lookup(final_sb, mounts)
        if kind == 'opaque' or (kind is None and ('/' in mounts or any(k.startswith(final_sb + '/') for k in mounts))):
            return None, None, final_sb, None
        host_path = mapped if kind == 'bind' else final_sb
    else:
        host_path = final_sb
    record(host_path)
    try:
        if os.path.isdir(host_path):
            return None, host_path, final_sb, None
        if os.path.isfile(host_path):
            return host_path, host_path, final_sb, None
    except (OSError, ValueError):
        pass
    return None, None, final_sb, None


def _re01_resolve_exec_chain(token, *, cwd=None, search_path=None, is_cmd=False, mounts=None):
    if not token:
        return [], None, None, None, None
    norm_tok = _re01_norm_posix(token)
    seen, seen_set = (
        ([norm_tok], {norm_tok}) if (is_cmd and '/' not in token) else ([], set())
    )
    resolved_file = resolved_any = resolved_sb = None
    base_dir, cwd_err = _re01_abs_cwd(cwd, mounts=mounts)
    if cwd_err is not None:
        return seen, None, None, None, cwd_err
    if not is_cmd or '/' in token:
        target_path = token if posixpath.isabs(token) else posixpath.join(base_dir, token)
        resolved_file, resolved_any, resolved_sb, err = _re01_follow_path(
            target_path, mounts, seen=seen, seen_set=seen_set)
        if err is not None:
            return seen, None, None, None, err
        return seen, resolved_file, resolved_any, resolved_sb, None
    eff_path = search_path if search_path is not None else os.environ.get('PATH', os.defpath)
    first_reg = first_reg_sb = None
    for p in eff_path.split(os.pathsep):
        if p == '' or p == '.':
            seg_dir = base_dir
        else:
            seg_dir = p if posixpath.isabs(p) else posixpath.join(base_dir, p)
        cand_cmd = posixpath.join(seg_dir, token)
        c_seen, c_set = [], set()
        c_file, c_any, c_sb, c_err = _re01_follow_path(cand_cmd, mounts, seen=c_seen, seen_set=c_set)
        if c_err is not None:
            return seen, None, None, None, c_err
        if c_file is not None:
            for item in c_seen:
                if item not in seen_set:
                    seen_set.add(item)
                    seen.append(item)
            if resolved_file is None and os.access(c_file, os.X_OK):
                resolved_file, resolved_any, resolved_sb = c_file, c_any, c_sb
            if first_reg is None:
                first_reg, first_reg_sb = c_file, c_sb
    if resolved_file is None and first_reg is not None:
        resolved_file, resolved_any, resolved_sb = first_reg, first_reg, first_reg_sb
    return seen, resolved_file, resolved_any, resolved_sb, None


def _re01_path_reason(norm, token, *, is_cmd_pos=False, cwd=None):
    n = _re01_norm_posix(norm)
    base = posixpath.basename(n)
    parts = n.split('/')
    if (n == 'kicad-route' or n == 'bin/kicad-route' or n.startswith('bin/kicad-route/')
            or n.endswith('/bin/kicad-route') or '/bin/kicad-route/' in n
            or (base == 'kicad-route' and not os.path.isdir(n)
                and (is_cmd_pos or _re01_safe_is_file(n, cwd=cwd)))):
        return f'RE-01 forbids executing legacy Python router entrypoint: {token}'
    if ('/src/kicad_route/' in n or n.startswith('src/kicad_route/')
            or n == 'src/kicad_route' or n.endswith('/src/kicad_route')
            or 'kicad_route' in parts or base == 'kicad_route.py'
            or 'native_delay' in parts or base == 'native_delay.py'
            or 'grid_router' in parts or base == 'grid_router.py'):
        return f'RE-01 forbids executing legacy Python router module: {token}'
    if ('/.venv-krt/' in n or n.startswith('.venv-krt/')
            or n == '.venv-krt' or n.endswith('/.venv-krt')
            or '.router-generations' in parts):
        return f'RE-01 forbids executing legacy Python router venv: {token}'
    return None


def _re01_check_zip_py_hooks(zip_path, entry, *, sub_prefix=''):
    prefix_parts = [p for p in sub_prefix.split('/') if p and p != '.']
    try:
        if not zipfile.is_zipfile(zip_path):
            return None
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                parts = [p for p in name.replace('\\', '/').split('/') if p and p != '.']
                if prefix_parts:
                    if len(parts) <= len(prefix_parts) or parts[:len(prefix_parts)] != prefix_parts:
                        continue
                    rel_parts = parts[len(prefix_parts):]
                else:
                    rel_parts = parts
                if not rel_parts:
                    continue
                if any(_re01_is_sourceless_pyc(p) for p in rel_parts):
                    return f'RE-01 forbids PYTHONPATH zipfile containing bytecode {name}: {entry}'
                if any(_re01_is_python_ext_module(p) for p in rel_parts):
                    return f'RE-01 forbids PYTHONPATH zipfile containing extension module {name}: {entry}'
                top = rel_parts[0]
                if _re01_is_py_hook_name(top):
                    return f'RE-01 forbids PYTHONPATH zipfile containing {name}: {entry}'
                if top == '__pycache__' and len(rel_parts) > 1 and _re01_is_py_hook_name(rel_parts[1]):
                    return f'RE-01 forbids PYTHONPATH zipfile containing {name}: {entry}'
                if any(_re01_is_always_py_hook_name(p) for p in rel_parts):
                    return f'RE-01 forbids PYTHONPATH zipfile containing {name}: {entry}'
    except (OSError, zipfile.BadZipFile, ValueError):
        return f'RE-01 forbids unreadable zipfile on PYTHONPATH: {entry}'
    return None


def _re01_trusted_repo_import_dirs():
    repo_root = _agent_ee_repo_root()
    main_root = _git_main_repo_root(repo_root)
    dirs = set()
    for r in (repo_root, main_root):
        for sub in ('src', 'tests'):
            p = r / sub
            try:
                dirs.add(_re01_norm_posix(os.path.realpath(p)))
            except OSError:
                pass
    try:
        co = resolve_kicad_route_checkout()
    except (OSError, ValueError):
        co = None
    co_bases = []
    if co is not None:
        co_bases.extend((Path(co), Path(co).parent / 'kicad-route'))
    co_bases.extend((repo_root.parent / 'kicad-route', main_root.parent / 'kicad-route'))
    for base in co_bases:
        p = base / 'tools'
        try:
            dirs.add(_re01_norm_posix(os.path.realpath(p)))
        except OSError:
            pass
    return dirs


def _re01_is_trusted_repo_import_dir(dir_path):
    if not dir_path:
        return False
    try:
        real_dir = _re01_norm_posix(os.path.realpath(dir_path))
    except OSError:
        return False
    for trusted in _re01_trusted_repo_import_dirs():
        if real_dir == trusted or real_dir.startswith(trusted + '/'):
            return True
    return False


def _re01_is_real_host_usersite_dir(dir_path):
    if not dir_path:
        return False
    try:
        pw_dir = pwd.getpwuid(os.getuid()).pw_dir
    except KeyError:
        return False
    if not pw_dir:
        return False
    try:
        real_pw = _re01_norm_posix(os.path.realpath(pw_dir))
        real_dir = _re01_norm_posix(os.path.realpath(dir_path))
    except OSError:
        return False
    prefix = posixpath.join(real_pw, '.local', 'lib')
    if not real_dir.startswith(prefix + '/'):
        return False
    rel = real_dir[len(prefix) + 1:].split('/')
    return (len(rel) >= 2 and rel[0].startswith('python')
            and rel[1] in ('site-packages', 'dist-packages'))


def _re01_dir_strict_pycache(dir_path, sb_targets, mounts, *, is_usersite=False):
    py_roots = _re01_system_python_roots()
    if mounts:
        for target_sb in sb_targets:
            if not target_sb:
                continue
            prefix = '/' if target_sb == '/' else target_sb + '/'
            if any(dest.startswith(prefix) for dest in mounts):
                return True
            kind, best_dest, _ = _re01_mount_lookup(target_sb, mounts)
            if kind == 'opaque':
                return True
            if best_dest is not None and best_dest != '/' and not _re01_is_trusted_repo_import_dir(dir_path):
                if _re01_sb_touches_nonroot_mount(target_sb, mounts, py_roots=py_roots):
                    return True
    if _re01_is_trusted_repo_import_dir(dir_path):
        return False
    try:
        real_dir = _re01_norm_posix(os.path.realpath(dir_path))
    except OSError:
        real_dir = None
    if (real_dir and _re01_is_protected_system_path(real_dir, py_roots)) or any(
        t and _re01_is_protected_system_path(t, py_roots) for t in sb_targets
    ):
        return False
    if is_usersite and _re01_is_real_host_usersite_dir(dir_path):
        return False
    return True


def _re01_scan_pycache_dir(cache_path, entry, label, *, strict_pycache=False, check_stdlib_hooks=True):
    hook_fn = _re01_is_py_hook_name if check_stdlib_hooks else _re01_is_always_py_hook_name
    try:
        with os.scandir(cache_path) as sub_it:
            for sub in sub_it:
                if (sub.is_symlink() or hook_fn(sub.name)
                        or (strict_pycache and sub.name.lower().endswith(('.pyc', '.pyo')))):
                    return f'RE-01 forbids {label} containing __pycache__/{sub.name}: {entry}'
    except OSError:
        pass
    return None


_RE01_TOP_HOST_ROOTS = ('/', '/tmp', '/var/tmp', '/opt', '/var', '/home', '/usr', '/etc')


def _re01_sb_touches_nonroot_mount(sb_path, mounts, *, py_roots=()):
    if not mounts or not sb_path:
        return False
    prefix = '/' if sb_path == '/' else sb_path + '/'
    for dest, (kind, host_tgt) in mounts.items():
        if dest == '/':
            continue
        if dest == sb_path or dest.startswith(prefix):
            return True
        if kind != 'opaque' and sb_path.startswith(dest + '/'):
            if dest in _RE01_TOP_HOST_ROOTS or _re01_is_protected_system_path(dest, py_roots):
                try:
                    if host_tgt and os.path.realpath(host_tgt) == os.path.realpath(dest):
                        continue
                except OSError:
                    pass
            return True
    return False


def _re01_check_rel_parts_pycache(dir_path, sb_targets, mounts, entry, label, *, cwd=None):
    for target_sb in sb_targets:
        if not target_sb:
            continue
        prefix = '/' if target_sb == '/' else target_sb + '/'
        for dest in mounts:
            if not dest.startswith(prefix):
                continue
            rel_parts = [p for p in dest[len(prefix):].split('/') if p and p != '.']
            for i in range(1, len(rel_parts)):
                pkg_dir = os.path.join(dir_path, *rel_parts[:i])
                cache_dir = os.path.join(pkg_dir, '__pycache__')
                if os.path.islink(cache_dir):
                    return f'RE-01 forbids {label} containing symlinked __pycache__: {entry}'
                if os.path.isdir(cache_dir):
                    err = _re01_scan_pycache_dir(cache_dir, entry, label, strict_pycache=True)
                    if err is not None:
                        return err
                anc_sb = posixpath.join(target_sb, *rel_parts[:i])
                _, _, anc_any, _, anc_err = _re01_resolve_exec_chain(
                    anc_sb, cwd=cwd, is_cmd=False, mounts=mounts)
                if anc_err is not None:
                    return anc_err
                if anc_any and anc_any != pkg_dir:
                    acache_dir = os.path.join(anc_any, '__pycache__')
                    if os.path.islink(acache_dir):
                        return f'RE-01 forbids {label} containing symlinked __pycache__: {entry}'
                    if os.path.isdir(acache_dir):
                        err = _re01_scan_pycache_dir(acache_dir, entry, label, strict_pycache=True)
                        if err is not None:
                            return err
    return None


def _re01_check_cur_pycache_strict(cur_dir, cur_sb, mounts, entry, label, py_roots, *, checked=None):
    ckey = (cur_dir, cur_sb)
    if checked is not None:
        if ckey in checked:
            return None
        checked.add(ckey)
    host_cache = os.path.join(cur_dir, '__pycache__')
    if os.path.islink(host_cache):
        return f'RE-01 forbids {label} containing symlinked __pycache__: {entry}'
    if os.path.isdir(host_cache):
        err = _re01_scan_pycache_dir(host_cache, entry, label, strict_pycache=True)
        if err is not None:
            return err
    if mounts and cur_sb:
        cache_sb = posixpath.join(cur_sb, '__pycache__')
        if any(dest == cache_sb or dest.startswith(cache_sb + '/') for dest in mounts):
            return f'RE-01 forbids {label} mounted/symlinked __pycache__: {entry}'
        lk, _, lm = _re01_mount_lookup(cache_sb, mounts)
        if lk == 'symlink':
            return f'RE-01 forbids {label} containing symlinked __pycache__: {entry}'
        if lk == 'bind' and lm and lm != host_cache:
            if os.path.islink(lm):
                return f'RE-01 forbids {label} containing symlinked __pycache__: {entry}'
            if os.path.isdir(lm):
                err = _re01_scan_pycache_dir(lm, entry, label, strict_pycache=True)
                if err is not None:
                    return err
    return None


def _re01_enqueue_bfs_child(de, cur_dir, cur_sb, cur_strict, depth, queue, *, mounts, py_roots,
                            entry, label, cwd, forbid_pth, seen_mounted_sb, seen_states,
                            strict_checked_caches=None, only_strict_symlinks=False,
                            walk_cur=False):
    child_sb = posixpath.join(cur_sb, de.name) if cur_sb else None
    is_mounted_sym = bool(
        mounts and child_sb and _re01_mount_lookup(child_sb, mounts)[0] == 'symlink'
    )
    if de.is_symlink() or is_mounted_sym:
        target_ref = child_sb or de.path
        t_chain, t_file, t_any, t_sb, t_err = _re01_resolve_exec_chain(
            target_ref, cwd=cwd, is_cmd=False, mounts=mounts)
        if t_err is not None:
            return t_err
        for cand in (t_sb, t_file, *t_chain):
            if cand:
                reason = _re01_path_reason(cand, entry, is_cmd_pos=False, cwd=cwd)
                if reason is not None:
                    return reason
        if any('__pycache__' in c.split('/') for c in (t_sb, *t_chain) if c):
            return f'RE-01 forbids {label} containing symlinked __pycache__: {entry}'
        next_strict = cur_strict or (
            bool(mounts) and any(
                _re01_sb_touches_nonroot_mount(c, mounts, py_roots=py_roots)
                for c in (t_sb, *t_chain) if c
            )
        )
        if (depth == 0 or cur_strict or next_strict or walk_cur) and (
            _re01_is_sourceless_pyc(de.name)
            or _re01_is_sourceless_pyc(t_sb)
            or _re01_is_sourceless_pyc(t_file)
        ):
            return f'RE-01 forbids {label} containing symlinked sourceless bytecode: {entry}'
        if _re01_is_python_ext_module(de.name) or _re01_is_python_ext_module(t_sb) or _re01_is_python_ext_module(t_file):
            return f'RE-01 forbids {label} containing symlinked extension module: {entry}'
        if only_strict_symlinks and not next_strict:
            return None
        if (not cur_strict) and (
            de.name.endswith('.py')
            or (t_sb and t_sb.endswith('.py'))
            or (t_file and t_file.endswith('.py'))
            or not (t_any and os.path.isdir(t_any))
        ):
            c_err = _re01_check_cur_pycache_strict(
                cur_dir, cur_sb, mounts, entry, label, py_roots, checked=strict_checked_caches)
            if c_err is not None:
                return c_err
        if t_any and os.path.isdir(t_any):
            queue.append((t_any, t_sb, next_strict, depth + 1))
        if mounts and next_strict and t_sb and t_sb not in seen_mounted_sb:
            c_err = _re01_check_mounted_import_children(
                (t_sb,), mounts, entry, label=label, cwd=cwd, forbid_pth=forbid_pth,
                _visited_sb=seen_mounted_sb, _visited_states=seen_states)
            if c_err is not None:
                return c_err
        return None
    if only_strict_symlinks:
        return None
    try:
        if de.is_dir(follow_symlinks=False):
            sub_sb = posixpath.join(cur_sb, de.name) if cur_sb else None
            if mounts and sub_sb:
                mkind, _, mmapped = _re01_mount_lookup(sub_sb, mounts)
                if mkind == 'opaque':
                    if (cur_strict or _re01_sb_touches_nonroot_mount(sub_sb, mounts, py_roots=py_roots)) and sub_sb not in seen_mounted_sb:
                        return _re01_check_mounted_import_children(
                            (sub_sb,), mounts, entry, label=label, cwd=cwd, forbid_pth=forbid_pth,
                            _visited_sb=seen_mounted_sb, _visited_states=seen_states)
                    return None
                sub_dir = mmapped if (mkind == 'bind' and mmapped) else de.path
                next_strict = cur_strict or _re01_sb_touches_nonroot_mount(sub_sb, mounts, py_roots=py_roots)
                if os.path.isdir(sub_dir):
                    queue.append((sub_dir, sub_sb, next_strict, depth + 1))
            else:
                queue.append((de.path, sub_sb, cur_strict, depth + 1))
        elif not mounts and de.is_dir(follow_symlinks=True):
            queue.append((de.path, posixpath.join(cur_sb, de.name) if cur_sb else None, cur_strict, depth + 1))
    except OSError:
        pass
    return None


def _re01_check_dir_py_hooks(dir_path, entry, *, label='PYTHONPATH entry', forbid_pth=False,
                             strict_pycache=False, sb_targets=(), mounts=None, cwd=None,
                             _visited_states=None, _visited_mounted_sb=None):
    py_roots = _re01_system_python_roots()
    if strict_pycache and mounts and sb_targets:
        rel_err = _re01_check_rel_parts_pycache(dir_path, sb_targets, mounts, entry, label, cwd=cwd)
        if rel_err is not None:
            return rel_err
    has_nonroot_non_sys = bool(
        mounts and any(
            dest != '/' and kind != 'opaque' and not _re01_is_protected_system_path(dest, py_roots)
            for dest, (kind, _) in mounts.items()
        )
    )
    seen_states = _visited_states if _visited_states is not None else set()
    seen_mounted_sb = _visited_mounted_sb if _visited_mounted_sb is not None else set()
    strict_checked_caches = set()
    try:
        real_root = _re01_norm_posix(os.path.realpath(dir_path))
    except OSError:
        real_root = None
    root_sbs = [t for t in sb_targets if t] if sb_targets else ([real_root] if real_root else [dir_path])
    queue = collections.deque((dir_path, r_sb, strict_pycache, 0) for r_sb in root_sbs)
    while queue:
        cur_dir, cur_sb, cur_strict, depth = queue.popleft()
        try:
            cur_real = _re01_norm_posix(os.path.realpath(cur_dir))
        except OSError:
            cur_real = None
        if depth > 0 and cur_sb and _re01_is_protected_system_path(cur_sb, py_roots):
            continue
        is_unoverlaid_repo = (
            _re01_is_trusted_repo_import_dir(cur_dir) and not cur_strict
            and not (cur_sb and _re01_dir_strict_pycache(cur_dir, (cur_sb,), mounts))
        )
        if depth > 0 and is_unoverlaid_repo:
            continue
        try:
            st = os.stat(cur_dir)
            state_key = ((st.st_dev, st.st_ino), cur_sb, cur_strict)
        except OSError:
            continue
        if state_key in seen_states:
            continue
        seen_states.add(state_key)
        is_allowed_opt_sp0 = bool(
            cur_real == '/opt'
            and label == 'script sys.path[0]'
            and mounts
            and any(dest.startswith('/opt/') for dest in mounts)
            and not (st.st_mode & stat.S_IWOTH)
        )
        if cur_real in _RE01_TOP_HOST_ROOTS and not is_allowed_opt_sp0:
            return f'RE-01 forbids {label} at unoverlaid host root {cur_real}: {entry}'
        if depth > 0 and cur_strict and mounts and cur_sb and cur_sb not in seen_mounted_sb:
            c_err = _re01_check_mounted_import_children(
                (cur_sb,), mounts, entry, label=label, cwd=cwd, forbid_pth=forbid_pth,
                _visited_sb=seen_mounted_sb, _visited_states=seen_states)
            if c_err is not None:
                return c_err
        cur_sys_protected = bool(
            (cur_sb and _re01_is_protected_system_path(cur_sb, py_roots))
            or (cur_real and _re01_is_protected_system_path(cur_real, py_roots))
        )
        is_unoverlaid_real_usersite = (
            label == 'user site-packages'
            and _re01_is_real_host_usersite_dir(cur_dir)
            and not cur_strict
            and not (cur_sb and mounts and _re01_sb_touches_nonroot_mount(cur_sb, mounts, py_roots=py_roots))
        )
        eff_strict = cur_strict or (
            not is_unoverlaid_repo
            and not cur_sys_protected
            and not is_unoverlaid_real_usersite
        )
        is_top_or_sys = (cur_real in _RE01_TOP_HOST_ROOTS) or cur_sys_protected
        walk_cur = not is_top_or_sys and not is_unoverlaid_repo
        only_strict_symlinks = (not walk_cur) and has_nonroot_non_sys and not is_unoverlaid_repo
        try:
            with os.scandir(cur_dir) as it:
                for de in it:
                    name = de.name
                    if name == '__pycache__':
                        cache_sb = posixpath.join(cur_sb, '__pycache__') if cur_sb else None
                        is_mounted_sym = bool(
                            mounts and cache_sb and _re01_mount_lookup(cache_sb, mounts)[0] == 'symlink'
                        )
                        scan_strict = eff_strict or (
                            depth > 0 and mounts and cache_sb
                            and _re01_sb_touches_nonroot_mount(cache_sb, mounts, py_roots=py_roots)
                        )
                        if de.is_symlink() or is_mounted_sym:
                            if not (cur_sys_protected and not eff_strict and not scan_strict and not is_mounted_sym):
                                return f'RE-01 forbids {label} containing symlinked __pycache__: {entry}'
                        elif (depth == 0 or scan_strict or walk_cur) and de.is_dir(follow_symlinks=False):
                            check_stdlib_hooks = (depth == 0 or bool(scan_strict))
                            err = _re01_scan_pycache_dir(
                                de.path, entry, label,
                                strict_pycache=bool(scan_strict),
                                check_stdlib_hooks=check_stdlib_hooks)
                            if err is not None:
                                return err
                        continue
                    child_sb = posixpath.join(cur_sb, name) if cur_sb else None
                    is_mounted_sym = bool(
                        mounts and child_sb and _re01_mount_lookup(child_sb, mounts)[0] == 'symlink'
                    )
                    scan_strict = eff_strict or (
                        depth > 0 and mounts and child_sb
                        and _re01_sb_touches_nonroot_mount(child_sb, mounts, py_roots=py_roots)
                    )
                    is_sym_entry = de.is_symlink() or is_mounted_sym
                    if depth == 0:
                        if forbid_pth and name.endswith('.pth'):
                            return f'RE-01 forbids {label} containing {name}: {entry}'
                        if _re01_is_py_hook_name(name):
                            return f'RE-01 forbids {label} containing {name}: {entry}'
                    else:
                        if _re01_is_always_py_hook_name(name):
                            if not (cur_sys_protected and not eff_strict and not scan_strict and not is_mounted_sym):
                                return f'RE-01 forbids {label} containing {name}: {entry}'
                        elif (eff_strict or scan_strict) and _re01_is_py_hook_name(name):
                            return f'RE-01 forbids {label} containing {name}: {entry}'
                    if _re01_is_sourceless_pyc(name):
                        skip_pyc = (is_unoverlaid_repo and not is_sym_entry) or (
                            cur_sys_protected and not eff_strict and not scan_strict and not is_mounted_sym
                        )
                        if not skip_pyc and (depth == 0 or eff_strict or scan_strict or walk_cur or is_sym_entry):
                            return f'RE-01 forbids {label} containing sourceless bytecode {name}: {entry}'
                    if _re01_is_python_ext_module(name) and (
                        is_sym_entry or not de.is_dir(follow_symlinks=False)
                    ):
                        skip_so = (
                            (is_unoverlaid_repo and not is_sym_entry)
                            or (cur_sys_protected and not eff_strict and not scan_strict and not is_mounted_sym)
                            or (is_unoverlaid_real_usersite and not is_sym_entry and not scan_strict)
                        )
                        if not skip_so:
                            return f'RE-01 forbids {label} containing extension module {name}: {entry}'
                    if name == '.git' or (not walk_cur and not only_strict_symlinks):
                        continue
                    c_err = _re01_enqueue_bfs_child(
                        de, cur_dir, cur_sb, eff_strict, depth, queue, mounts=mounts, py_roots=py_roots,
                        entry=entry, label=label, cwd=cwd, forbid_pth=forbid_pth,
                        seen_mounted_sb=seen_mounted_sb, seen_states=seen_states,
                        strict_checked_caches=strict_checked_caches,
                        only_strict_symlinks=only_strict_symlinks,
                        walk_cur=walk_cur)
                    if c_err is not None:
                        return c_err
        except OSError:
            continue
    return None


def _re01_check_pycache_prefix(raw_val, *, cwd=None, mounts=None):
    if not raw_val:
        return None
    pc_chain, _, pc_any, pc_sb, pc_err = _re01_resolve_exec_chain(
        raw_val, cwd=cwd, is_cmd=False, mounts=mounts)
    if pc_err is not None:
        return pc_err
    for cand in pc_chain:
        reason = _re01_path_reason(cand, raw_val, is_cmd_pos=False, cwd=cwd)
        if reason is not None:
            return reason
    leaf_sb, leaf_err = _re01_resolve_parent_leaf(raw_val, cwd=cwd, mounts=mounts)
    if leaf_err is not None:
        return leaf_err
    if not leaf_sb:
        leaf_sb = pc_sb
    if pc_sb == '/' or leaf_sb == '/':
        return f'RE-01 forbids root PYTHONPYCACHEPREFIX: {raw_val}'
    if pc_sb == NO_PYCACHE and leaf_sb == NO_PYCACHE:
        try:
            host_dn_ok = stat.S_ISCHR(os.lstat(NO_PYCACHE).st_mode)
        except OSError:
            host_dn_ok = False
        if host_dn_ok:
            if not mounts:
                return None
            if not any(d.startswith(NO_PYCACHE + '/') for d in mounts):
                lk, _, lm = _re01_mount_lookup(NO_PYCACHE, mounts)
                if lk == 'opaque':
                    return None
                if lk == 'bind' and lm:
                    try:
                        if stat.S_ISCHR(os.lstat(lm).st_mode) and os.path.realpath(lm) == NO_PYCACHE:
                            return None
                    except OSError:
                        pass
                if lk is None and '/' not in mounts:
                    return None
    if mounts:
        for target_sb in dict.fromkeys(t for t in (leaf_sb, pc_sb) if t):
            t_pref = '/' if target_sb == '/' else target_sb + '/'
            for dest in mounts:
                if dest == target_sb or dest.startswith(t_pref):
                    return f'RE-01 forbids mounted/symlinked PYTHONPYCACHEPREFIX {dest}: {raw_val}'
    host_cands = []
    for sb_cand in dict.fromkeys(t for t in (leaf_sb, pc_sb) if t):
        if mounts:
            lk, _, lm = _re01_mount_lookup(sb_cand, mounts)
            if lk == 'bind' and lm:
                host_cands.append(lm)
            elif lk is None and '/' not in mounts and not any(k.startswith(sb_cand + '/') for k in mounts):
                host_cands.append(sb_cand)
        else:
            host_cands.append(sb_cand)
    if pc_any is not None and pc_any not in host_cands:
        host_cands.append(pc_any)
    for host_path in host_cands:
        try:
            st = os.lstat(host_path)
        except FileNotFoundError:
            continue
        except OSError:
            return f'RE-01 forbids unreadable PYTHONPYCACHEPREFIX: {raw_val}'
        if not stat.S_ISDIR(st.st_mode):
            return f'RE-01 forbids non-directory or symlinked PYTHONPYCACHEPREFIX: {raw_val}'
        try:
            with os.scandir(host_path) as it:
                if next(it, None) is not None:
                    return f'RE-01 forbids non-empty PYTHONPYCACHEPREFIX: {raw_val}'
        except OSError:
            return f'RE-01 forbids unreadable PYTHONPYCACHEPREFIX: {raw_val}'
    return None


def _re01_check_mounted_import_children(sb_targets, mounts, entry_label, *, label, cwd=None,
                                        forbid_pth=False, check_path_reason=False,
                                        _visited_sb=None, _visited_states=None):
    seen_sb = _visited_sb if _visited_sb is not None else set()
    seen_states = _visited_states if _visited_states is not None else set()
    for target_sb in sb_targets:
        if not target_sb or target_sb in seen_sb:
            continue
        seen_sb.add(target_sb)
        prefix = '/' if target_sb == '/' else target_sb + '/'
        for dest, (kind, host_tgt) in mounts.items():
            if not dest.startswith(prefix):
                continue
            rel = dest[len(prefix):]
            rel_parts = rel.split('/')
            first_comp = rel_parts[0]
            if '__pycache__' in rel_parts:
                return f'RE-01 forbids {label} mounted/symlinked __pycache__: {entry_label}'
            if any(_re01_is_sourceless_pyc(p) for p in rel_parts) or _re01_is_sourceless_pyc(dest):
                return f'RE-01 forbids {label} mounted/symlinked sourceless bytecode: {entry_label}'
            if kind == 'bind' and _re01_is_sourceless_pyc(host_tgt):
                return f'RE-01 forbids {label} mounted sourceless bytecode: {entry_label}'
            if any(_re01_is_python_ext_module(p) for p in rel_parts) or _re01_is_python_ext_module(dest):
                return f'RE-01 forbids {label} mounted/symlinked extension module: {entry_label}'
            if kind == 'bind' and _re01_is_python_ext_module(host_tgt):
                return f'RE-01 forbids {label} mounted extension module: {entry_label}'
            if (forbid_pth and first_comp.endswith('.pth')) or _re01_is_py_hook_name(first_comp):
                return f'RE-01 forbids {label} mounted {first_comp}: {entry_label}'
            if any(_re01_is_always_py_hook_name(p) for p in rel_parts):
                return f'RE-01 forbids {label} mounted {entry_label}'
            if check_path_reason and kind == 'bind' and host_tgt:
                reason = _re01_path_reason(host_tgt, entry_label, is_cmd_pos=False, cwd=cwd)
                if reason is not None:
                    return reason
            if kind == 'bind' and host_tgt and os.path.isdir(host_tgt):
                d_err = _re01_check_dir_py_hooks(
                    host_tgt, entry_label, label=label, forbid_pth=forbid_pth,
                    strict_pycache=True, sb_targets=(dest,), mounts=mounts, cwd=cwd,
                    _visited_states=seen_states, _visited_mounted_sb=seen_sb)
                if d_err is not None:
                    return d_err
            elif kind == 'symlink':
                t_chain, t_file, t_any, t_sb, t_err = _re01_resolve_exec_chain(
                    dest, cwd=cwd, is_cmd=False, mounts=mounts)
                if t_err is not None:
                    return t_err
                if any('__pycache__' in c.split('/') for c in (dest, t_sb, *t_chain) if c):
                    return f'RE-01 forbids {label} mounted/symlinked __pycache__: {entry_label}'
                if any(_re01_is_sourceless_pyc(c) for c in (dest, host_tgt, t_sb, t_file, *t_chain) if c):
                    return f'RE-01 forbids {label} mounted/symlinked sourceless bytecode: {entry_label}'
                if any(_re01_is_python_ext_module(c) for c in (dest, host_tgt, t_sb, t_file, *t_chain) if c):
                    return f'RE-01 forbids {label} mounted/symlinked extension module: {entry_label}'
                for cand in (t_sb, t_file, *t_chain):
                    if cand:
                        reason = _re01_path_reason(cand, entry_label, is_cmd_pos=False, cwd=cwd)
                        if reason is not None:
                            return reason
                if t_any and os.path.isdir(t_any):
                    t_targets = (dest, t_sb) if t_sb and t_sb != dest else (dest,)
                    d_err = _re01_check_dir_py_hooks(
                        t_any, entry_label, label=label, forbid_pth=forbid_pth,
                        strict_pycache=True, sb_targets=t_targets, mounts=mounts, cwd=cwd,
                        _visited_states=seen_states, _visited_mounted_sb=seen_sb)
                    if d_err is not None:
                        return d_err
                if t_sb and t_sb not in seen_sb:
                    sub_err = _re01_check_mounted_import_children(
                        (t_sb,), mounts, entry_label, label=label, cwd=cwd,
                        forbid_pth=forbid_pth, check_path_reason=check_path_reason,
                        _visited_sb=seen_sb, _visited_states=seen_states)
                    if sub_err is not None:
                        return sub_err
    return None


def _re01_check_bwrap_py_mounts(mounts, *, cwd=None):
    if not mounts:
        return None
    py_roots = _re01_system_python_roots()
    checked_pkg_dirs = set()
    for dest, (kind, host_tgt) in mounts.items():
        if dest == '/' or kind == 'opaque' or _re01_is_protected_system_path(dest, py_roots):
            continue
        is_py_mount = False
        is_identity_trusted = False
        if kind == 'bind':
            if _re01_is_sourceless_pyc(dest) or _re01_is_sourceless_pyc(host_tgt):
                return f'RE-01 forbids bwrap bytecode mount onto {dest}'
            if _re01_is_python_ext_module(dest) or _re01_is_python_ext_module(host_tgt):
                return f'RE-01 forbids bwrap extension module mount onto {dest}'
            if dest.endswith('.py') or (host_tgt and host_tgt.endswith('.py') and os.path.isfile(host_tgt)):
                is_py_mount = True
                if host_tgt and _re01_is_trusted_repo_import_dir(posixpath.dirname(dest)):
                    try:
                        if os.path.realpath(host_tgt) == os.path.realpath(dest):
                            is_identity_trusted = True
                    except OSError:
                        pass
        elif kind == 'symlink':
            t_chain, t_file, _, t_sb, t_err = _re01_resolve_exec_chain(
                dest, cwd=cwd, is_cmd=False, mounts=mounts)
            if t_err is not None:
                return t_err
            if any('__pycache__' in c.split('/') for c in (dest, t_sb, *t_chain) if c):
                return f'RE-01 forbids bwrap symlink into __pycache__: {dest}'
            if any(_re01_is_sourceless_pyc(c) for c in (dest, host_tgt, t_sb, t_file, *t_chain) if c):
                return f'RE-01 forbids bwrap bytecode symlink at {dest}'
            if any(_re01_is_python_ext_module(c) for c in (dest, host_tgt, t_sb, t_file, *t_chain) if c):
                return f'RE-01 forbids bwrap extension module symlink at {dest}'
            if dest.endswith('.py') or (t_sb and t_sb.endswith('.py')) or (t_file and t_file.endswith('.py')):
                is_py_mount = True
        if not is_py_mount or is_identity_trusted:
            continue
        cur_sb = posixpath.dirname(dest)
        seen_up = set()
        while cur_sb and cur_sb != '/' and cur_sb not in seen_up:
            seen_up.add(cur_sb)
            if _re01_is_protected_system_path(cur_sb, py_roots):
                break
            _, _, cur_any, cur_res_sb, cur_err = _re01_resolve_exec_chain(
                cur_sb, cwd=cwd, is_cmd=False, mounts=mounts)
            if cur_err is not None:
                return cur_err
            if cur_any and _re01_is_trusted_repo_import_dir(cur_any):
                if not _re01_dir_strict_pycache(cur_any, [c for c in (cur_sb, cur_res_sb) if c], mounts):
                    break
            for sb_c in dict.fromkeys(c for c in (cur_sb, cur_res_sb) if c and c != '/'):
                cache_sb = posixpath.join(sb_c, '__pycache__')
                for m_dest in mounts:
                    if m_dest == cache_sb or m_dest.startswith(cache_sb + '/'):
                        return f'RE-01 forbids bwrap Python mount with overlaid __pycache__: {m_dest}'
            if cur_any and os.path.isdir(cur_any) and cur_any not in checked_pkg_dirs:
                checked_pkg_dirs.add(cur_any)
                cache_dir = os.path.join(cur_any, '__pycache__')
                if os.path.islink(cache_dir):
                    return f'RE-01 forbids bwrap Python mount with symlinked __pycache__: {dest}'
                if os.path.isdir(cache_dir):
                    err = _re01_scan_pycache_dir(
                        cache_dir, dest, 'bwrap Python mount', strict_pycache=True)
                    if err is not None:
                        return err
            init_sb = posixpath.join(cur_sb, '__init__.py')
            _, init_file, _, _, init_err = _re01_resolve_exec_chain(
                init_sb, cwd=cwd, is_cmd=False, mounts=mounts)
            if init_err is not None:
                return init_err
            if init_file is None:
                break
            cur_sb = posixpath.dirname(cur_sb)
    return None


def _re01_host_backing_dirs(mounts, *extras):
    return {
        _re01_norm_posix(p)
        for p in (
            *extras,
            *(m_src for m_kind, m_src in (mounts.values() if mounts else ())
              if m_kind == 'bind' and m_src),
        )
        if p
    }


def _re01_is_host_backing_path(dcand, host_backing_dirs):
    dcand_norm = _re01_norm_posix(dcand)
    return any(
        dcand_norm == b or (b != '/' and dcand_norm.startswith(b + '/'))
        for b in host_backing_dirs
    )


def _re01_check_env(env, *, cwd=None, mounts=None, check_python=True,
                    python_no_usersite=False, python_ignore_env=False, python_xy=None):
    decoded, env_err = _re01_decode_env(env)
    if env_err is not None:
        return None, env_err
    for var in _RE01_LD_FORBIDDEN_ENV:
        if decoded.get(var):
            return None, f'RE-01 forbids {var} under audit enforcement'
    ld_lib = decoded.get('LD_LIBRARY_PATH')
    if ld_lib:
        for seg in ld_lib.split(os.pathsep):
            if not seg:
                continue
            chain, _, _, _, err = _re01_resolve_exec_chain(seg, cwd=cwd, is_cmd=False, mounts=mounts)
            if err is not None:
                return None, err
            for cand in chain:
                reason = _re01_path_reason(cand, seg, is_cmd_pos=False, cwd=cwd)
                if reason is not None:
                    return None, reason
    if not check_python:
        return decoded.get('PATH', os.defpath), None
    if mounts:
        m_err = _re01_check_bwrap_py_mounts(mounts, cwd=cwd)
        if m_err is not None:
            return None, m_err
    for var in _RE01_PYTHON_FORBIDDEN_ENV:
        if decoded.get(var):
            return None, f'RE-01 forbids {var} under audit enforcement'
    pc_err = _re01_check_pycache_prefix(decoded.get('PYTHONPYCACHEPREFIX'), cwd=cwd, mounts=mounts)
    if pc_err is not None:
        return None, pc_err
    venv = decoded.get('VIRTUAL_ENV')
    if venv:
        chain, _, _, _, err = _re01_resolve_exec_chain(venv, cwd=cwd, is_cmd=False, mounts=mounts)
        if err is not None:
            return None, err
        for cand in chain:
            reason = _re01_path_reason(cand, venv, is_cmd_pos=False, cwd=cwd)
            if reason is not None:
                return None, reason
    base_dir, cwd_err = _re01_abs_cwd(cwd, mounts=mounts)
    if cwd_err is not None:
        return None, cwd_err
    pypath = decoded.get('PYTHONPATH')
    if pypath:
        for raw_entry in pypath.split(os.pathsep):
            entry = '.' if raw_entry == '' else raw_entry
            chain, cand_file, cand_any, cand_sb, err = _re01_resolve_exec_chain(
                entry, cwd=cwd, is_cmd=False, mounts=mounts)
            if err is not None:
                return None, err
            for cand in chain:
                reason = _re01_path_reason(cand, entry, is_cmd_pos=False, cwd=cwd)
                if reason is not None:
                    return None, reason
                if posixpath.basename(cand) in ('sitecustomize.py', 'usercustomize.py'):
                    return None, f'RE-01 forbids PYTHONPATH startup hook: {entry}'
            checked_zips = set()
            for zcand in (cand_file, cand_any):
                if zcand and os.path.isfile(zcand) and (zcand, '') not in checked_zips:
                    checked_zips.add((zcand, ''))
                    z_err = _re01_check_zip_py_hooks(zcand, entry)
                    if z_err is not None:
                        return None, z_err
            leaf_sb, leaf_err = _re01_resolve_parent_leaf(entry, cwd=base_dir, mounts=mounts)
            if leaf_err is not None:
                return None, leaf_err
            sb_targets = [t for t in dict.fromkeys((cand_sb, leaf_sb)) if t]
            for target_sb in sb_targets:
                segs = [s for s in target_sb.split('/') if s]
                for i in range(1, len(segs)):
                    anc_sb = '/' + '/'.join(segs[:i])
                    sub_prefix = '/'.join(segs[i:])
                    a_chain, a_file, _, _, a_err = _re01_resolve_exec_chain(
                        anc_sb, cwd=cwd, is_cmd=False, mounts=mounts)
                    if a_err is not None:
                        return None, a_err
                    for acand in a_chain:
                        reason = _re01_path_reason(acand, entry, is_cmd_pos=False, cwd=cwd)
                        if reason is not None:
                            return None, reason
                    if a_file and os.path.isfile(a_file) and (a_file, sub_prefix) not in checked_zips:
                        checked_zips.add((a_file, sub_prefix))
                        z_err = _re01_check_zip_py_hooks(a_file, entry, sub_prefix=sub_prefix)
                        if z_err is not None:
                            return None, z_err
            leaf_any = None
            if mounts and leaf_sb:
                l_kind, _, l_mapped = _re01_mount_lookup(leaf_sb, mounts)
                if l_kind == 'bind' and l_mapped:
                    leaf_any = l_mapped
            host_backing_dirs = _re01_host_backing_dirs(mounts, cand_any, leaf_any)
            checked_dirs = set()
            for dcand in (cand_any, leaf_any):
                if dcand and os.path.isdir(dcand):
                    strict = _re01_dir_strict_pycache(dcand, sb_targets, mounts)
                    if (dcand, strict) not in checked_dirs:
                        checked_dirs.add((dcand, strict))
                        d_err = _re01_check_dir_py_hooks(
                            dcand, entry, strict_pycache=strict, sb_targets=sb_targets, mounts=mounts, cwd=cwd)
                        if d_err is not None:
                            return None, d_err
            for dcand in chain:
                if dcand and os.path.isdir(dcand):
                    if (mounts and not _re01_is_host_backing_path(dcand, host_backing_dirs)
                            and _re01_mount_lookup(dcand, mounts)[0] == 'opaque'):
                        continue
                    strict = _re01_dir_strict_pycache(dcand, sb_targets, mounts)
                    if (dcand, strict) not in checked_dirs:
                        checked_dirs.add((dcand, strict))
                        d_err = _re01_check_dir_py_hooks(
                            dcand, entry, strict_pycache=strict, sb_targets=sb_targets, mounts=mounts, cwd=cwd)
                        if d_err is not None:
                            return None, d_err
            if mounts:
                c_err = _re01_check_mounted_import_children(
                    sb_targets, mounts, entry, label='PYTHONPATH', cwd=cwd, check_path_reason=True)
                if c_err is not None:
                    return None, c_err
    no_usersite_env = decoded.get('PYTHONNOUSERSITE')
    env_disables_usersite = (
        not python_ignore_env
        and bool(no_usersite_env)
        and bool(re.fullmatch(r'[1-9]\d*', no_usersite_env)))
    if not python_no_usersite and not env_disables_usersite:
        home_candidates = []
        if 'HOME' in decoded:
            home_val = decoded['HOME']
            if home_val == '':
                home_candidates.append('/')
                try:
                    pw_dir = pwd.getpwuid(os.getuid()).pw_dir
                    if pw_dir:
                        home_candidates.append(pw_dir)
                except KeyError:
                    pass
            else:
                home_candidates.append(home_val)
        else:
            try:
                pw_dir = pwd.getpwuid(os.getuid()).pw_dir
                if pw_dir:
                    home_candidates.append(pw_dir)
            except KeyError:
                pass
            exp_home = os.path.expanduser('~')
            if exp_home and exp_home != '~' and exp_home not in home_candidates:
                home_candidates.append(exp_home)
        ver = python_xy or f'{sys.version_info.major}.{sys.version_info.minor}'
        abi_thread = 't' if 't' in getattr(sys, 'abiflags', '') else ''
        for home_val in home_candidates:
            raw_home = home_val if posixpath.isabs(home_val) else posixpath.join(base_dir, home_val)
            h_chain, _, _, h_sb, h_err = _re01_resolve_exec_chain(
                raw_home, cwd=base_dir, is_cmd=False, mounts=mounts)
            if h_err is not None:
                return None, h_err
            for hcand in h_chain:
                reason = _re01_path_reason(hcand, home_val, is_cmd_pos=False, cwd=base_dir)
                if reason is not None:
                    return None, reason
            eff_home = h_sb or raw_home
            for pkg_dir_name in ('site-packages', 'dist-packages'):
                user_site = posixpath.join(eff_home, '.local', 'lib', f'python{ver}{abi_thread}', pkg_dir_name)
                u_chain, _, u_any, u_sb, u_err = _re01_resolve_exec_chain(
                    user_site, cwd=base_dir, is_cmd=False, mounts=mounts)
                if u_err is not None:
                    return None, u_err
                u_leaf, u_leaf_err = _re01_resolve_parent_leaf(user_site, cwd=base_dir, mounts=mounts)
                if u_leaf_err is not None:
                    return None, u_leaf_err
                u_targets = [t for t in dict.fromkeys((u_sb, u_leaf)) if t]
                u_leaf_any = None
                if mounts and u_leaf:
                    ul_kind, _, ul_mapped = _re01_mount_lookup(u_leaf, mounts)
                    if ul_kind == 'bind' and ul_mapped:
                        u_leaf_any = ul_mapped
                u_backing_dirs = _re01_host_backing_dirs(mounts, u_any, u_leaf_any)
                checked_u = set()
                for dcand in (u_any, u_leaf_any):
                    if dcand and os.path.isdir(dcand):
                        strict = _re01_dir_strict_pycache(dcand, u_targets, mounts, is_usersite=True)
                        if (dcand, strict) not in checked_u:
                            checked_u.add((dcand, strict))
                            d_err = _re01_check_dir_py_hooks(
                                dcand, user_site, label='user site-packages', forbid_pth=True,
                                strict_pycache=strict, sb_targets=u_targets, mounts=mounts, cwd=cwd)
                            if d_err is not None:
                                return None, d_err
                for dcand in u_chain:
                    if dcand and os.path.isdir(dcand):
                        if (mounts and not _re01_is_host_backing_path(dcand, u_backing_dirs)
                                and _re01_mount_lookup(dcand, mounts)[0] == 'opaque'):
                            continue
                        strict = _re01_dir_strict_pycache(dcand, u_targets, mounts, is_usersite=True)
                        if (dcand, strict) not in checked_u:
                            checked_u.add((dcand, strict))
                            d_err = _re01_check_dir_py_hooks(
                                dcand, user_site, label='user site-packages', forbid_pth=True,
                                strict_pycache=strict, sb_targets=u_targets, mounts=mounts, cwd=cwd)
                            if d_err is not None:
                                return None, d_err
                if mounts:
                    c_err = _re01_check_mounted_import_children(
                        u_targets, mounts, user_site, label='user site-packages',
                        cwd=cwd, forbid_pth=True)
                    if c_err is not None:
                        return None, c_err
    return decoded.get('PATH', os.defpath), None


def _re01_python_xy(exe_tok, host_exe):
    for cand in (host_exe, exe_tok):
        if not cand:
            continue
        base = posixpath.basename(cand)
        m = re.fullmatch(r'python(\d+\.\d+)', base)
        if m:
            return m.group(1)
    return f'{sys.version_info.major}.{sys.version_info.minor}'


def _re01_python_opt_flags(args):
    isolated = no_usersite = ignore_env = False
    for tok in args:
        if tok == '--':
            break
        if tok == '-' or tok.startswith('--') or not tok.startswith('-') or len(tok) == 1:
            break
        opts = tok[1:]
        if 'I' in opts:
            isolated = no_usersite = ignore_env = True
        if 'P' in opts:
            isolated = True
        if 's' in opts:
            no_usersite = True
        if 'E' in opts:
            ignore_env = True
    return isolated, no_usersite, ignore_env


def _re01_check_python_venv(exe_tok, host_exe, sb_exe, chain, *, cwd=None, mounts=None,
                            argv0_tok=None, search_path=None):
    cand_paths = []
    for raw in (exe_tok, argv0_tok):
        if not raw:
            continue
        is_cmd = ('/' not in raw)
        a_chain, a_host, _, a_sb, a_err = _re01_resolve_exec_chain(
            raw, cwd=cwd, search_path=search_path, is_cmd=is_cmd, mounts=mounts)
        if a_err is not None:
            return a_err
        for p in (a_host, a_sb, *a_chain):
            if p and '/' in p:
                cand_paths.append(_re01_norm_posix(p))
        if not is_cmd:
            leaf_p, leaf_err = _re01_resolve_parent_leaf(raw, cwd=cwd, mounts=mounts)
            if leaf_err is not None:
                return leaf_err
            if leaf_p and '/' in leaf_p:
                cand_paths.append(leaf_p)
    for p in (host_exe, sb_exe, *chain):
        if p and '/' in p:
            cand_paths.append(_re01_norm_posix(p))
    seen_dirs = set()
    own_cfg = None
    if sys.prefix != sys.base_prefix:
        own_cfg = os.path.realpath(os.path.join(sys.prefix, 'pyvenv.cfg'))
    for p in cand_paths:
        d1 = posixpath.dirname(p)
        d2 = posixpath.dirname(d1) if d1 and d1 != '/' else ''
        for cfg_dir in (d1, d2):
            if not cfg_dir or cfg_dir in seen_dirs:
                continue
            seen_dirs.add(cfg_dir)
            cfg_path = posixpath.join(cfg_dir, 'pyvenv.cfg')
            _, cfg_file, _, cfg_sb, cfg_err = _re01_resolve_exec_chain(
                cfg_path, cwd=cwd, is_cmd=False, mounts=mounts)
            if cfg_err is not None:
                return cfg_err
            has_mount = bool(mounts and cfg_sb and mounts.get(cfg_sb, (None,))[0] in ('bind', 'symlink'))
            if cfg_file is not None or has_mount:
                if own_cfg and cfg_file is not None and os.path.realpath(cfg_file) == own_cfg:
                    continue
                return f'RE-01 forbids custom Python pyvenv.cfg under audit enforcement: {cfg_path}'
    return None


def _re01_check_python_args(args, *, cwd=None, mounts=None, env=None):
    end_opts = False
    script_idx = None
    isolated = ignore_env = False
    for idx, tok in enumerate(args):
        if not end_opts:
            if tok == '--':
                end_opts = True
                continue
            if tok == '-':
                return 'RE-01 forbids stdin Python execution under audit enforcement'
            if tok.startswith('--'):
                return f'RE-01 forbids Python option {tok} under audit enforcement'
            if tok.startswith('-') and len(tok) > 1:
                if any(ch not in _RE01_PY_ALLOWED_SHORT_OPTS for ch in tok[1:]):
                    return f'RE-01 forbids Python option {tok} under audit enforcement'
                if 'I' in tok[1:]:
                    isolated = ignore_env = True
                if 'P' in tok[1:]:
                    isolated = True
                if 'E' in tok[1:]:
                    ignore_env = True
                continue
        elif tok == '-':
            return 'RE-01 forbids stdin Python execution under audit enforcement'
        script_idx = idx
        break
    if script_idx is None:
        return 'RE-01 forbids interactive or stdin Python execution under audit enforcement'
    script_tok = args[script_idx]
    norm_tok = _re01_norm_posix(script_tok)
    if (norm_tok == '-' or norm_tok == '/dev/stdin' or norm_tok.startswith('/dev/fd/')
            or re.fullmatch(r'/proc/(?:\d+|self|thread-self)/fd/.*', norm_tok)):
        return f'RE-01 forbids FD/stdin Python execution: {script_tok}'
    chain, host_file, _, sb_script, err = _re01_resolve_exec_chain(
        script_tok, cwd=cwd, is_cmd=False, mounts=mounts)
    if err is not None:
        return err
    for cand in chain:
        reason = _re01_path_reason(cand, script_tok, is_cmd_pos=False, cwd=cwd)
        if reason is not None:
            return reason
    if (sb_script and (sb_script == '/dev/stdin' or sb_script.startswith('/dev/fd/')
                      or re.fullmatch(r'/proc/(?:\d+|self|thread-self)/fd/.*', sb_script))):
        return f'RE-01 forbids FD/stdin Python execution: {script_tok}'
    if (host_file is None or not norm_tok.endswith('.py')
            or not (sb_script and sb_script.endswith('.py')) or not host_file.endswith('.py')):
        return f'RE-01 requires an existing regular .py script file: {script_tok}'
    try:
        st = os.stat(host_file)
        if not stat.S_ISREG(st.st_mode):
            return f'RE-01 requires a regular .py script file: {script_tok}'
        with open(host_file, 'rb') as fh:
            head = fh.read(512)
        if head[:4] in (b'PK\x03\x04', b'PK\x05\x06') or zipfile.is_zipfile(host_file):
            return f'RE-01 forbids zipfile Python execution: {script_tok}'
        if head[:4] == importlib.util.MAGIC_NUMBER or b'\x00' in head:
            return f'RE-01 forbids binary/bytecode Python execution: {script_tok}'
    except OSError:
        return f'RE-01 requires a readable regular .py script file: {script_tok}'
    decoded_env, env_err = _re01_decode_env(env)
    if env_err is not None:
        return env_err
    sv = decoded_env.get('PYTHONSAFEPATH', '').strip()
    safepath = bool(re.fullmatch(r'[1-9]\d*', sv) or sv.lower() == 'true')
    if not isolated and not (not ignore_env and safepath):
        checked_sp0_dirs = set()
        script_dir = os.path.dirname(host_file)
        if mounts:
            orig_sb_script, o_err = _re01_resolve_parent_leaf(script_tok, cwd=cwd, mounts=mounts)
            if o_err is not None:
                return o_err
            if not orig_sb_script:
                orig_sb_script = sb_script
            sb_dirs = []
            for s_cand in (sb_script, orig_sb_script):
                if s_cand:
                    d_cand = posixpath.dirname(s_cand)
                    if d_cand and d_cand not in sb_dirs:
                        sb_dirs.append(d_cand)
            for sb_dir in sb_dirs:
                _, _, sb_any, sb_resolved, sb_err = _re01_resolve_exec_chain(
                    sb_dir, cwd=cwd, is_cmd=False, mounts=mounts)
                if sb_err is not None:
                    return sb_err
                sb_targets = [sb_dir]
                if sb_resolved and sb_resolved not in sb_targets:
                    sb_targets.append(sb_resolved)
                if sb_any and os.path.isdir(sb_any):
                    strict = _re01_dir_strict_pycache(sb_any, sb_targets, mounts)
                    if (sb_any, strict) not in checked_sp0_dirs:
                        checked_sp0_dirs.add((sb_any, strict))
                        d_err = _re01_check_dir_py_hooks(
                            sb_any, script_tok, label='script sys.path[0]',
                            strict_pycache=strict, sb_targets=sb_targets, mounts=mounts, cwd=cwd)
                        if d_err is not None:
                            return d_err
                c_err = _re01_check_mounted_import_children(
                    sb_targets, mounts, script_tok, label='script sys.path[0]', cwd=cwd)
                if c_err is not None:
                    return c_err
            if script_dir and os.path.isdir(script_dir):
                host_sb_targets = (script_dir,)
                strict_host = _re01_dir_strict_pycache(script_dir, host_sb_targets, mounts)
                if (script_dir, strict_host) not in checked_sp0_dirs:
                    checked_sp0_dirs.add((script_dir, strict_host))
                    d_err = _re01_check_dir_py_hooks(
                        script_dir, script_tok, label='script sys.path[0]',
                        strict_pycache=strict_host, sb_targets=host_sb_targets, mounts=mounts, cwd=cwd)
                    if d_err is not None:
                        return d_err
        elif script_dir:
            host_sb_targets = (script_dir,)
            strict_host = _re01_dir_strict_pycache(script_dir, host_sb_targets, None)
            d_err = _re01_check_dir_py_hooks(
                script_dir, script_tok, label='script sys.path[0]',
                strict_pycache=strict_host, sb_targets=host_sb_targets)
            if d_err is not None:
                return d_err
    for arg_tok in args[script_idx + 1:]:
        arg_chain, _, _, _, arg_err = _re01_resolve_exec_chain(
            arg_tok, cwd=cwd, is_cmd=False, mounts=mounts)
        if arg_err is not None:
            return arg_err
        for cand in arg_chain:
            reason = _re01_path_reason(cand, arg_tok, is_cmd_pos=False, cwd=cwd)
            if reason is not None:
                return reason
    return None


def _re01_alias_reason(cands, dest_norm, src_label):
    dest_base = posixpath.basename(dest_norm)
    dest_cat = _re01_exec_category(dest_base)
    for cand in cands:
        sb = posixpath.basename(cand)
        if sb == 'sh.distrib' or _re01_matches_cmd_name(sb, _RE01_SHELL_NAMES):
            if dest_base != 'sh.distrib' and not _re01_matches_cmd_name(dest_base, _RE01_SHELL_NAMES):
                return f'RE-01 forbids aliasing shell {src_label} onto {dest_norm}'
        elif _re01_is_dynamic_linker(sb) or _re01_matches_cmd_name(sb, _RE01_LAUNCHER_NAMES):
            if not _re01_is_dynamic_linker(dest_base) and not _re01_matches_cmd_name(dest_base, _RE01_LAUNCHER_NAMES):
                return f'RE-01 forbids aliasing launcher {src_label} onto {dest_norm}'
        elif _re01_matches_cmd_name(sb, _RE01_ALT_RUNTIMES):
            if not _re01_matches_cmd_name(dest_base, _RE01_ALT_RUNTIMES):
                return f'RE-01 forbids aliasing runtime {src_label} onto {dest_norm}'
        elif _re01_is_python_name(sb):
            if not _re01_is_python_name(dest_base):
                return f'RE-01 forbids aliasing Python {src_label} onto {dest_norm}'
        else:
            sb_cat = _re01_exec_category(sb)
            if ((sb_cat is not None and dest_cat != sb_cat)
                    or (dest_cat == 'kicad-route-engine' and sb_cat != 'kicad-route-engine')):
                return f'RE-01 forbids aliasing {src_label} onto {dest_norm}'
    return None


_RE01_SYSTEM_PROTECTED_RE = re.compile(
    r'^/(?:'
    r'(?:usr/(?:local/)?)?lib(?:32|64)?/(?:python|pypy)[^/]*(?:/.*)?'
    r'|etc/python[^/]*(?:/.*)?'
    r'|etc/ld\.so(?:\.preload|\.cache|\.conf(?:\.d(?:/.*)?)?)'
    r'|etc/passwd'
    r'|etc/nsswitch\.conf'
    r'|(?:usr/)?lib(?:32|64)?/(?:x86_64-linux-gnu|aarch64-linux-gnu|ld-linux[^/]*)(?:/.*)?'
    r')\Z'
)
_RE01_SYSTEM_PROTECTED_ROOTS = (
    '/usr', '/lib', '/lib64', '/lib32', '/bin', '/sbin', '/etc',
)
_RE01_SYSTEM_ANCESTORS = frozenset((
    '/', '/usr', '/usr/local', '/usr/lib', '/usr/lib64', '/usr/lib32',
    '/usr/local/lib', '/usr/local/lib64', '/lib', '/lib64', '/lib32', '/etc',
))


def _re01_system_python_roots():
    roots = set()
    for p in (os.path.dirname(os.__file__), *site.getsitepackages([sys.base_prefix, sys.base_exec_prefix])):
        if p:
            n = _re01_norm_posix(p)
            if n != '/':
                roots.add(n)
    return tuple(sorted(roots))


def _re01_is_protected_system_path(dest_norm, py_roots):
    if _RE01_SYSTEM_PROTECTED_RE.match(dest_norm) or dest_norm in _RE01_SYSTEM_ANCESTORS:
        return True
    for r in (*_RE01_SYSTEM_PROTECTED_ROOTS, *py_roots):
        if dest_norm == r or r.startswith(dest_norm + '/') or dest_norm.startswith(r + '/'):
            return True
    return False


def _re01_bwrap_system_mount_reason(mounts, *, rw_mounts=(), dir_dests=()):
    py_roots = _re01_system_python_roots()
    for dest_norm, (kind, target) in mounts.items():
        if not _re01_is_protected_system_path(dest_norm, py_roots):
            continue
        if kind == 'opaque':
            return f'RE-01 forbids opaque bwrap mount over system path {dest_norm}'
        if kind == 'symlink':
            is_id = False
            try:
                if os.path.islink(dest_norm):
                    host_tgt = _re01_norm_posix(posixpath.join(posixpath.dirname(dest_norm), os.readlink(dest_norm)))
                    is_id = (host_tgt == target)
            except OSError:
                is_id = False
            if not is_id:
                return f'RE-01 forbids bwrap symlink over system path {dest_norm} -> {target}'
        elif kind == 'bind':
            if dest_norm in rw_mounts:
                return f'RE-01 forbids writable bwrap mount {target} onto system path {dest_norm}'
            try:
                same = bool(target) and os.path.exists(dest_norm) and (
                    os.path.realpath(target) == os.path.realpath(dest_norm))
            except OSError:
                same = False
            if not same:
                return f'RE-01 forbids non-identity bwrap mount {target} onto system path {dest_norm}'
    for dir_norm in dir_dests:
        if not _re01_is_protected_system_path(dir_norm, py_roots):
            continue
        kind, best_dest, mapped = _re01_mount_lookup(dir_norm, mounts)
        try:
            ok = (kind == 'bind' and best_dest not in rw_mounts and bool(mapped)
                  and os.path.isdir(dir_norm)
                  and os.path.realpath(mapped) == os.path.realpath(dir_norm))
        except OSError:
            ok = False
        if not ok:
            return f'RE-01 forbids bwrap --dir over system path {dir_norm}'
    return None


def _re01_check_bwrap(args, *, cwd=None, env=None, depth=0, outer_mounts=None):
    if args == ['--version']:
        return None
    outer_cwd, cwd_err = _re01_abs_cwd(cwd, mounts=outer_mounts)
    if cwd_err is not None:
        return cwd_err
    mounts = {}
    rw_mounts = set()
    dir_dests = []
    bwrap_cwd = outer_cwd
    bwrap_argv0 = None
    bwrap_env, env_err = _re01_decode_env(env)
    if env_err is not None:
        return env_err
    idx = 0
    n = len(args)
    while idx < n:
        tok = args[idx]
        if tok == '--':
            idx += 1
            break
        if tok in _RE01_BWRAP_FORBIDDEN_OPTS:
            return f'RE-01 forbids unsupported bwrap option {tok} under audit enforcement'
        arity = _RE01_BWRAP_ARITY.get(tok)
        if arity is None:
            if tok.startswith('-'):
                return f'RE-01 forbids unsupported bwrap option {tok} under audit enforcement'
            break
        if idx + arity >= n:
            return f'RE-01 forbids incomplete bwrap option {tok} under audit enforcement'
        if tok in _RE01_BWRAP_BIND_SRC_OPTS:
            src, dest = args[idx + 1], args[idx + 2]
            if not src:
                return 'RE-01 forbids empty bwrap mount source under audit enforcement'
            if not posixpath.isabs(dest):
                return f'RE-01 forbids relative bwrap mount destination: {dest}'
            dest_norm, d_err = _re01_resolve_mount_symlinks(dest, mounts)
            if d_err is not None:
                return d_err
            src_chain, src_file, src_any, src_sb, src_err = _re01_resolve_exec_chain(
                src, cwd=outer_cwd, is_cmd=False, mounts=outer_mounts)
            if src_err is not None:
                return src_err
            for cand in src_chain:
                reason = _re01_path_reason(cand, src, is_cmd_pos=False, cwd=outer_cwd)
                if reason is not None:
                    return reason
            if src_file is not None:
                alias_err = _re01_alias_reason(src_chain, dest_norm, src)
                if alias_err is not None:
                    return alias_err
            if outer_mounts is not None:
                eff_src_sb = src_sb
                okind, _, omapped = _re01_mount_lookup(eff_src_sb, outer_mounts)
                src_pref = '/' if eff_src_sb == '/' else eff_src_sb + '/'
                outer_subs = [k for k in outer_mounts if k.startswith(src_pref)]
                if okind == 'opaque' or (okind is None and '/' in outer_mounts):
                    if outer_subs:
                        if dest_norm != eff_src_sb:
                            return f'RE-01 forbids nested bwrap remapping outer mounts onto {dest_norm}'
                        for k in outer_subs:
                            mounts[k] = outer_mounts[k]
                    elif tok not in _RE01_BWRAP_TRY_OPTS:
                        _re01_shadow_mounts(mounts, dest_norm)
                        rw_mounts.difference_update(
                            {k for k in rw_mounts if k == dest_norm or dest_norm == '/' or k.startswith(dest_norm + '/')})
                        mounts[dest_norm] = ('opaque', None)
                else:
                    host_src = src_any or (omapped if okind == 'bind' else eff_src_sb)
                    if tok not in _RE01_BWRAP_TRY_OPTS or os.path.exists(host_src):
                        if outer_subs and dest_norm != eff_src_sb:
                            return f'RE-01 forbids nested bwrap remapping outer mounts onto {dest_norm}'
                        _re01_shadow_mounts(mounts, dest_norm)
                        rw_mounts.difference_update(
                            {k for k in rw_mounts if k == dest_norm or dest_norm == '/' or k.startswith(dest_norm + '/')})
                        mounts[dest_norm] = ('bind', host_src)
                        if tok not in ('--ro-bind', '--ro-bind-try'):
                            rw_mounts.add(dest_norm)
                        for k in outer_subs:
                            mounts[k] = outer_mounts[k]
            else:
                host_src = src_any or src_sb
                if tok not in _RE01_BWRAP_TRY_OPTS or os.path.exists(host_src):
                    _re01_shadow_mounts(mounts, dest_norm)
                    rw_mounts.difference_update(
                        {k for k in rw_mounts if k == dest_norm or dest_norm == '/' or k.startswith(dest_norm + '/')})
                    mounts[dest_norm] = ('bind', host_src)
                    if tok not in ('--ro-bind', '--ro-bind-try'):
                        rw_mounts.add(dest_norm)
        elif tok == '--symlink':
            src, dest = args[idx + 1], args[idx + 2]
            if not src:
                return 'RE-01 forbids empty bwrap symlink target under audit enforcement'
            if not posixpath.isabs(dest):
                return f'RE-01 forbids relative bwrap symlink destination: {dest}'
            raw_segs = [s for s in dest.split('/') if s and s != '.']
            if not raw_segs or raw_segs[-1] == '..':
                return f'RE-01 forbids invalid bwrap symlink destination: {dest}'
            dest_parent_raw = '/' + '/'.join(raw_segs[:-1])
            dest_parent, d_err = _re01_resolve_mount_symlinks(dest_parent_raw, mounts)
            if d_err is not None:
                return d_err
            dest_norm = _re01_norm_posix(posixpath.join(dest_parent, raw_segs[-1]))
            raw_target = src if posixpath.isabs(src) else posixpath.join(dest_parent, src)
            t_chain, _, t_any, t_sb, t_err = _re01_resolve_exec_chain(
                raw_target, cwd=outer_cwd, is_cmd=False, mounts=mounts)
            if t_err is not None:
                return t_err
            for cand in (t_sb, *t_chain):
                if cand:
                    reason = _re01_path_reason(cand, src, is_cmd_pos=False, cwd=outer_cwd)
                    if reason is not None:
                        return reason
            if not (t_any and os.path.isdir(t_any)):
                alias_err = _re01_alias_reason([t_sb, *t_chain], dest_norm, src)
                if alias_err is not None:
                    return alias_err
            if dest_norm not in mounts:
                mounts[dest_norm] = ('symlink', raw_target)
        elif tok in _RE01_BWRAP_OPAQUE_OPTS:
            dest = args[idx + 1]
            if not posixpath.isabs(dest):
                return f'RE-01 forbids relative bwrap mount destination: {dest}'
            dest_norm, d_err = _re01_resolve_mount_symlinks(dest, mounts)
            if d_err is not None:
                return d_err
            _re01_shadow_mounts(mounts, dest_norm)
            rw_mounts.difference_update(
                {k for k in rw_mounts if k == dest_norm or dest_norm == '/' or k.startswith(dest_norm + '/')})
            mounts[dest_norm] = ('opaque', None)
        elif tok == '--dir':
            dest = args[idx + 1]
            if not posixpath.isabs(dest):
                return f'RE-01 forbids relative bwrap {tok} destination: {dest}'
            dir_norm, d_err = _re01_resolve_mount_symlinks(dest, mounts)
            if d_err is not None:
                return d_err
            dir_dests.append(dir_norm)
        elif tok == '--remount-ro':
            dest = args[idx + 1]
            if not posixpath.isabs(dest):
                return f'RE-01 forbids relative bwrap {tok} destination: {dest}'
            _, d_err = _re01_resolve_mount_symlinks(dest, mounts)
            if d_err is not None:
                return d_err
        elif tok == '--argv0':
            bwrap_argv0 = args[idx + 1]
            argv0_name = posixpath.basename(_re01_norm_posix(bwrap_argv0))
            if (argv0_name == 'sh.distrib'
                    or _re01_is_dynamic_linker(argv0_name)
                    or _re01_matches_cmd_name(argv0_name, _RE01_SHELL_NAMES)
                    or _re01_matches_cmd_name(argv0_name, _RE01_LAUNCHER_NAMES)
                    or _re01_matches_cmd_name(argv0_name, _RE01_ALT_RUNTIMES)
                    or argv0_name == 'kicad-route'):
                return f'RE-01 forbids bwrap --argv0 masquerading as {bwrap_argv0}'
        elif tok == '--chdir':
            chdir_arg = args[idx + 1]
            if not chdir_arg:
                return 'RE-01 forbids empty bwrap --chdir under audit enforcement'
            bwrap_cwd = chdir_arg if posixpath.isabs(chdir_arg) else posixpath.join('/', chdir_arg)
        elif tok == '--clearenv':
            bwrap_env = {}
        elif tok == '--setenv':
            bwrap_env[_decode_audit_arg(args[idx + 1])] = _decode_audit_arg(args[idx + 2])
        elif tok == '--unsetenv':
            bwrap_env.pop(_decode_audit_arg(args[idx + 1]), None)
        idx += 1 + arity
    if idx >= n:
        return 'RE-01 forbids bwrap without an inner command under audit enforcement'
    sys_mount_err = _re01_bwrap_system_mount_reason(mounts, rw_mounts=rw_mounts, dir_dests=dir_dests)
    if sys_mount_err is not None:
        return sys_mount_err
    bwrap_cwd, bwrap_cwd_chain, bwrap_cwd_err = _re01_abs_cwd(bwrap_cwd, mounts=mounts, with_chain=True)
    if bwrap_cwd_err is not None:
        return bwrap_cwd_err
    for cand in bwrap_cwd_chain:
        reason = _re01_path_reason(cand, bwrap_cwd, is_cmd_pos=False, cwd=bwrap_cwd)
        if reason is not None:
            return reason
    return _re01_check_command_reason(
        args[idx:], cwd=bwrap_cwd, env=bwrap_env, _depth=depth + 1, _mounts=mounts, _argv0=bwrap_argv0)


_RE01_ENGINE_VALUE_FLAGS = frozenset((
    '--rules', '--project', '--limits', '--max-expansions',
    '--operation-result', '--save-request', '--native-bridge',
    '--python', '--timeout-ms', '--max-child-output-bytes',
    '--pour-area-loss', '--pour-min-spokes', '--diff-pairs',
))
_RE01_ENGINE_PATH_FLAGS = frozenset((
    '--rules', '--project', '--limits', '--operation-result', '--save-request',
))


def _re01_trusted_engine_paths():
    trusted = {
        SANDBOX['engine'],
        f"{SANDBOX['checkout']}/rust-engine/target/release/kicad-route-engine",
        f"{SANDBOX['checkout']}/rust-engine/target/debug/kicad-route-engine",
    }
    for var in ('AGENT_EE_KICAD_ROUTE_ENGINE', 'KICAD_ROUTE_ENGINE_BIN'):
        val = os.environ.get(var, '').strip()
        if val:
            p = Path(val).expanduser()
            trusted.add(_re01_norm_posix(os.path.abspath(str(p))))
            try:
                trusted.add(_re01_norm_posix(os.path.realpath(p)))
            except OSError:
                pass
    try:
        co = resolve_kicad_route_checkout()
    except (OSError, ValueError):
        co = None
    repo_root = _agent_ee_repo_root()
    main_root = _git_main_repo_root(repo_root)
    co_bases = []
    if co is not None:
        co_bases.append(Path(co))
        co_bases.append(Path(co).parent / 'kicad-route')
    co_bases.extend((repo_root.parent / 'kicad-route', main_root.parent / 'kicad-route'))
    for base in co_bases:
        for profile in ('release', 'debug'):
            cand = base / 'rust-engine' / 'target' / profile / 'kicad-route-engine'
            trusted.add(_re01_norm_posix(os.path.abspath(str(cand))))
            try:
                trusted.add(_re01_norm_posix(os.path.realpath(cand)))
            except OSError:
                pass
    return trusted


def _re01_check_engine_implicit_bridge(eff_python, *, host_exe, sb_exe, cwd=None, env=None,
                                       depth=0, mounts=None):
    cands = []
    seen = set()

    def _add_anc_bridges(ref):
        if not ref:
            return
        cur = posixpath.dirname(_re01_norm_posix(ref))
        while cur:
            c = posixpath.join(cur, 'tools', 'rust_engine_kicad.py')
            if c not in seen:
                seen.add(c)
                cands.append(c)
            if cur == '/':
                break
            parent = posixpath.dirname(cur)
            if parent == cur:
                break
            cur = parent

    abs_cwd, cwd_chain, cwd_err = _re01_abs_cwd(cwd, mounts=mounts, with_chain=True)
    if cwd_err is not None:
        return cwd_err
    for cand in cwd_chain:
        reason = _re01_path_reason(cand, str(cwd or abs_cwd), is_cmd_pos=False, cwd=abs_cwd)
        if reason is not None:
            return reason
    _add_anc_bridges(sb_exe)
    _add_anc_bridges(host_exe)
    _add_anc_bridges(abs_cwd + '/_')
    _, _, cwd_any, cwd_sb, cwd_res_err = _re01_resolve_exec_chain(
        abs_cwd, cwd=abs_cwd, is_cmd=False, mounts=mounts)
    if cwd_res_err is not None:
        return cwd_res_err
    if cwd_sb:
        _add_anc_bridges(cwd_sb + '/_')
    if cwd_any:
        _add_anc_bridges(cwd_any + '/_')
    if mounts is None:
        try:
            cwd_real = _re01_norm_posix(os.path.realpath(abs_cwd))
        except OSError:
            cwd_real = None
        if cwd_real:
            _add_anc_bridges(cwd_real + '/_')
    sb_bridge = f"{SANDBOX['checkout']}/tools/rust_engine_kicad.py"
    if sb_bridge not in seen:
        seen.add(sb_bridge)
        cands.append(sb_bridge)
    try:
        co = resolve_kicad_route_checkout()
    except (OSError, ValueError):
        co = None
    repo_root = _agent_ee_repo_root()
    main_root = _git_main_repo_root(repo_root)
    co_bases = []
    if co is not None:
        co_bases.append(Path(co))
        co_bases.append(Path(co).parent / 'kicad-route')
    co_bases.extend((repo_root.parent / 'kicad-route', main_root.parent / 'kicad-route'))
    for base in co_bases:
        cand_bridge = _re01_norm_posix(os.path.abspath(str(base / 'tools' / 'rust_engine_kicad.py')))
        if cand_bridge not in seen:
            seen.add(cand_bridge)
            cands.append(cand_bridge)
        try:
            cand_real = _re01_norm_posix(os.path.realpath(base / 'tools' / 'rust_engine_kicad.py'))
            if cand_real not in seen:
                seen.add(cand_real)
                cands.append(cand_real)
        except OSError:
            pass
    nb = resolve_native_bridge()
    if nb is not None:
        for nb_str in (str(nb), os.path.realpath(nb)):
            nb_norm = _re01_norm_posix(nb_str)
            if nb_norm not in seen:
                seen.add(nb_norm)
                cands.append(nb_norm)
    found_bridge = False
    for cand in cands:
        _, c_file, _, _, c_err = _re01_resolve_exec_chain(
            cand, cwd=cwd, is_cmd=False, mounts=mounts)
        if c_err is not None:
            return c_err
        if c_file is not None:
            found_bridge = True
            b_err = _re01_check_command_reason(
                [eff_python, '-B', cand], cwd=cwd, env=env, _depth=depth + 1, _mounts=mounts)
            if b_err is not None:
                return b_err
    if found_bridge:
        return None

    # Fallback validation when no bridge file exists in synthetic check_re01_command calls.
    sp, env_err = _re01_check_env(env, cwd=cwd, mounts=mounts, check_python=False)
    if env_err is not None:
        return env_err
    py_chain, py_host, _, py_sb, py_err = _re01_resolve_exec_chain(
        eff_python, cwd=cwd, search_path=sp, is_cmd=True, mounts=mounts)
    if py_err is not None:
        return py_err
    for p in py_chain:
        r = _re01_path_reason(p, eff_python, is_cmd_pos=True, cwd=cwd)
        if r is not None:
            return r
    py_real = os.path.realpath(sys.executable)
    if mounts is not None:
        if not py_sb or not py_sb.startswith(_RE01_SYSTEM_EXEC_PREFIXES):
            return f'RE-01 forbids non-system sandbox --python path in route-native: {py_sb or eff_python}'
    if py_host is not None:
        if _re01_exec_category(posixpath.basename(py_host)) != 'python':
            return f'RE-01 forbids non-Python --python executable in route-native: {py_host}'
        if not (py_host.startswith(_RE01_SYSTEM_EXEC_PREFIXES) or py_host == py_real):
            return f'RE-01 forbids non-system --python path in route-native: {py_host}'
    else:
        if '/' not in eff_python:
            return f'RE-01 forbids untrusted --python executable in route-native: {eff_python}'
        if not py_sb or not (py_sb.startswith(_RE01_SYSTEM_EXEC_PREFIXES) or py_sb == py_real):
            return f'RE-01 forbids non-system --python path in route-native: {eff_python}'
    py_xy = _re01_python_xy(eff_python, py_host)
    _, py_env_err = _re01_check_env(
        env, cwd=cwd, mounts=mounts, check_python=True, python_xy=py_xy)
    if py_env_err is not None:
        return py_env_err
    return _re01_check_python_venv(
        eff_python, py_host, py_sb, py_chain, cwd=cwd, mounts=mounts, search_path=sp)


def _re01_check_engine_args(tokens, *, host_exe, sb_exe, cwd=None, env=None, depth=0, mounts=None):
    if tokens[1:] in (['--help'], ['-h'], ['--version']):
        return None
    if len(tokens) < 2 or tokens[1] != 'route-native':
        return f'RE-01 forbids unsupported kicad-route-engine subcommand: {tokens[1:]}'
    seen_flags = set()
    diff_pairs_mode = None
    seen_explicit_pairs = set()
    seen_explicit_nets = set()
    py_tok = bridge_tok = None
    saw_help = after_dd = False
    positional = []
    i = 2
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if not after_dd and tok == '--':
            after_dd = True
            i += 1
            continue
        if not after_dd and tok in ('--help', '-h'):
            saw_help = True
            i += 1
            continue
        if not after_dd and tok.startswith('-'):
            if '=' in tok:
                flag, val = tok.split('=', 1)
                i += 1
            else:
                flag = tok
                if i + 1 >= n:
                    return f'RE-01 forbids incomplete route-native option {flag}'
                val = tokens[i + 1]
                i += 2
            if flag not in _RE01_ENGINE_VALUE_FLAGS:
                return f'RE-01 forbids unknown route-native option {flag}'
            if not val:
                return f'RE-01 forbids empty route-native option value for {flag}'
            if flag == '--diff-pairs':
                if val.startswith('-'):
                    return f'RE-01 forbids flag-like --diff-pairs value: {val}'
                if val == 'auto':
                    if diff_pairs_mode is not None:
                        return 'RE-01 forbids duplicate or mixed --diff-pairs option'
                    diff_pairs_mode = 'auto'
                else:
                    if diff_pairs_mode == 'auto':
                        return 'RE-01 forbids combining --diff-pairs auto with explicit pairs'
                    parts = val.split(',')
                    if (len(parts) != 2 or not parts[0] or not parts[1]
                            or parts[0] != parts[0].strip() or parts[1] != parts[1].strip()
                            or parts[0].startswith('-') or parts[1].startswith('-')
                            or parts[0] == parts[1]):
                        return f'RE-01 forbids invalid --diff-pairs pair spec: {val}'
                    if val in seen_explicit_pairs:
                        return f'RE-01 forbids duplicate --diff-pairs pair: {val}'
                    if parts[0] in seen_explicit_nets or parts[1] in seen_explicit_nets:
                        return f'RE-01 forbids reusing net across --diff-pairs pairs: {val}'
                    seen_explicit_pairs.add(val)
                    seen_explicit_nets.update(parts)
                    diff_pairs_mode = 'explicit'
                continue
            if flag in seen_flags:
                return f'RE-01 forbids duplicate route-native option {flag}'
            seen_flags.add(flag)
            if flag == '--python':
                py_tok = val
            elif flag == '--native-bridge':
                bridge_tok = val
            elif flag in _RE01_ENGINE_PATH_FLAGS:
                p_chain, _, _, _, p_err = _re01_resolve_exec_chain(
                    val, cwd=cwd, is_cmd=False, mounts=mounts)
                if p_err is not None:
                    return p_err
                for cand in p_chain:
                    reason = _re01_path_reason(cand, val, is_cmd_pos=False, cwd=cwd)
                    if reason is not None:
                        return reason
        else:
            positional.append(tok)
            i += 1
    for arg_tok in tokens[1:]:
        a_chain, _, _, _, a_err = _re01_resolve_exec_chain(
            arg_tok, cwd=cwd, is_cmd=False, mounts=mounts)
        if a_err is not None:
            return a_err
        for cand in a_chain:
            reason = _re01_path_reason(cand, arg_tok, is_cmd_pos=False, cwd=cwd)
            if reason is not None:
                return reason
    eff_python = py_tok if py_tok is not None else PYTHON
    py_base = posixpath.basename(_re01_norm_posix(eff_python))
    if _re01_exec_category(py_base) != 'python':
        return f'RE-01 forbids non-Python --python executable in route-native: {eff_python}'
    if saw_help:
        return None
    sp, env_err = _re01_check_env(env, cwd=cwd, mounts=mounts, check_python=False)
    if env_err is not None:
        return env_err
    decoded_env, env_err = _re01_decode_env(env)
    if env_err is not None:
        return env_err
    pc_raw = decoded_env.get('PYTHONPYCACHEPREFIX')
    pc_err = _re01_check_pycache_prefix(pc_raw, cwd=cwd, mounts=mounts)
    if pc_err is not None:
        return pc_err
    kc_chain, kc_host, _, kc_sb, kc_err = _re01_resolve_exec_chain(
        'kicad-cli', cwd=cwd, search_path=sp, is_cmd=True, mounts=mounts)
    if kc_err is not None:
        return kc_err
    for cand in kc_chain:
        r = _re01_path_reason(cand, 'kicad-cli', is_cmd_pos=True, cwd=cwd)
        if r is not None:
            return r
    if kc_host is not None and (
            posixpath.basename(kc_host) != 'kicad-cli' or not kc_host.startswith(_RE01_SYSTEM_EXEC_PREFIXES)):
        return f'RE-01 forbids non-system kicad-cli on PATH under route-native: {kc_host}'
    if mounts is not None and kc_sb is not None and (
            posixpath.basename(kc_sb) != 'kicad-cli' or not kc_sb.startswith(_RE01_SYSTEM_EXEC_PREFIXES)):
        return f'RE-01 forbids non-system sandbox kicad-cli on PATH under route-native: {kc_sb}'
    if len(positional) != 2:
        return f'RE-01 forbids invalid route-native positional arguments: {positional}'
    if bridge_tok is not None:
        return _re01_check_command_reason(
            [eff_python, '-B', bridge_tok], cwd=cwd, env=env, _depth=depth + 1, _mounts=mounts)
    return _re01_check_engine_implicit_bridge(
        eff_python, host_exe=host_exe, sb_exe=sb_exe, cwd=cwd, env=env, depth=depth, mounts=mounts)


def _re01_check_command_reason(argv, *, cwd=None, env=None, _depth=0, _mounts=None, _argv0=None):
    if _depth >= _RE01_MAX_DEPTH:
        return 'RE-01 forbids deeply nested command execution under audit enforcement'
    tokens = [_decode_audit_arg(a) for a in argv]
    if not tokens or not tokens[0]:
        return 'RE-01 forbids empty command execution under audit enforcement'
    for tok in tokens:
        if _re01_has_nul_or_surrogate(tok):
            return f'RE-01 forbids NUL or lone surrogate in command argument under audit enforcement: {tok!r}'
    cwd_raw = _decode_audit_arg(cwd) if cwd is not None else None
    if cwd_raw is not None and _re01_has_nul_or_surrogate(cwd_raw):
        return f'RE-01 forbids NUL or lone surrogate in cwd under audit enforcement: {cwd_raw!r}'
    cwd_str, cwd_chain, cwd_err = _re01_abs_cwd(cwd_raw, mounts=_mounts, with_chain=True)
    if cwd_err is not None:
        return cwd_err
    for cand in cwd_chain:
        reason = _re01_path_reason(cand, cwd_raw or cwd_str, is_cmd_pos=False, cwd=cwd_str)
        if reason is not None:
            return reason
    search_path, env_err = _re01_check_env(env, cwd=cwd_str, mounts=_mounts, check_python=False)
    if env_err is not None:
        return env_err
    exe_tok = tokens[0]
    chain, host_exe, _, sb_exe, exe_err = _re01_resolve_exec_chain(
        exe_tok, cwd=cwd_str, search_path=search_path, is_cmd=True, mounts=_mounts)
    if exe_err is not None:
        return exe_err
    for cand in chain:
        reason = _re01_path_reason(cand, exe_tok, is_cmd_pos=True, cwd=cwd_str)
        if reason is not None:
            return reason
        base = posixpath.basename(cand)
        if base == 'sh.distrib' or _re01_matches_cmd_name(base, _RE01_SHELL_NAMES):
            return f'RE-01 forbids shell execution under audit enforcement: {exe_tok}'
        if _re01_is_dynamic_linker(base) or _re01_matches_cmd_name(base, _RE01_LAUNCHER_NAMES):
            return f'RE-01 forbids launcher/wrapper execution under audit enforcement: {exe_tok}'
        if _re01_matches_cmd_name(base, _RE01_ALT_RUNTIMES):
            return f'RE-01 forbids scripting runtime execution under audit enforcement: {exe_tok}'
    invoked_base = posixpath.basename(_re01_norm_posix(exe_tok))
    invoked_cat = _re01_exec_category(invoked_base)
    if invoked_cat is None:
        return f'RE-01 forbids unallowlisted executable under audit enforcement: {exe_tok}'
    if sb_exe is not None:
        sb_cat = _re01_exec_category(posixpath.basename(sb_exe))
        if sb_cat != invoked_cat:
            return f'RE-01 forbids mismatched sandbox executable {sb_exe} for {exe_tok}'
    if _mounts is not None:
        if invoked_cat == 'kicad-route-engine':
            trusted_sb_engines = {
                SANDBOX['engine'],
                f"{SANDBOX['checkout']}/rust-engine/target/release/kicad-route-engine",
                f"{SANDBOX['checkout']}/rust-engine/target/debug/kicad-route-engine",
            } | _re01_trusted_engine_paths()
            if not sb_exe or not (sb_exe.startswith(_RE01_SYSTEM_EXEC_PREFIXES)
                                  or sb_exe in trusted_sb_engines):
                return f'RE-01 forbids untrusted sandbox kicad-route-engine path: {sb_exe or exe_tok}'
        else:
            if not sb_exe or not sb_exe.startswith(_RE01_SYSTEM_EXEC_PREFIXES):
                return (f'RE-01 forbids non-system sandbox executable path '
                        f'under audit enforcement: {sb_exe or exe_tok}')
    if host_exe is not None:
        resolved_cat = _re01_exec_category(posixpath.basename(host_exe))
        if resolved_cat != invoked_cat:
            return f'RE-01 forbids unallowlisted resolved executable {host_exe} for {exe_tok}'
        if invoked_cat == 'kicad-route-engine':
            trusted_engines = _re01_trusted_engine_paths()
            host_real = _re01_norm_posix(os.path.realpath(host_exe))
            if not (host_exe.startswith(_RE01_SYSTEM_EXEC_PREFIXES)
                    or host_exe in trusted_engines or host_real in trusted_engines):
                return f'RE-01 forbids untrusted kicad-route-engine path: {host_exe}'
        else:
            py_real = os.path.realpath(sys.executable) if invoked_cat == 'python' else None
            if not (host_exe.startswith(_RE01_SYSTEM_EXEC_PREFIXES)
                    or (py_real is not None and host_exe == py_real)):
                return f'RE-01 forbids non-system executable path under audit enforcement: {host_exe}'
    elif invoked_cat == 'kicad-route-engine' and _mounts is None:
        if '/' not in exe_tok:
            return f'RE-01 forbids untrusted kicad-route-engine executable: {exe_tok}'
        trusted_engines = _re01_trusted_engine_paths()
        if not sb_exe or not (sb_exe.startswith(_RE01_SYSTEM_EXEC_PREFIXES) or sb_exe in trusted_engines):
            return f'RE-01 forbids untrusted kicad-route-engine path: {exe_tok}'
    elif invoked_cat == 'python' and _mounts is None:
        if '/' in exe_tok:
            py_real = os.path.realpath(sys.executable)
            if not sb_exe or not (sb_exe.startswith(_RE01_SYSTEM_EXEC_PREFIXES) or sb_exe == py_real):
                return f'RE-01 forbids non-system Python path under audit enforcement: {exe_tok}'
    if _argv0 is not None:
        if invoked_cat == 'python':
            return f'RE-01 forbids bwrap --argv0 on Python under audit enforcement: {_argv0}'
        if '/' in _argv0:
            a0_chain, _, _, a0_sb, a0_err = _re01_resolve_exec_chain(
                _argv0, cwd=cwd_str, is_cmd=False, mounts=_mounts)
            if a0_err is not None:
                return a0_err
            for cand in (a0_sb, *a0_chain):
                if cand:
                    a0_reason = _re01_path_reason(cand, _argv0, is_cmd_pos=True, cwd=cwd_str)
                    if a0_reason is not None:
                        return a0_reason
            a0_cat = _re01_exec_category(posixpath.basename(a0_sb or _re01_norm_posix(_argv0)))
        else:
            a0_reason = _re01_path_reason(_argv0, _argv0, is_cmd_pos=True, cwd=cwd_str)
            if a0_reason is not None:
                return a0_reason
            a0_cat = _re01_exec_category(posixpath.basename(_re01_norm_posix(_argv0)))
        if a0_cat != invoked_cat:
            return f'RE-01 forbids mismatched bwrap --argv0 {_argv0} for {exe_tok}'
    if host_exe is not None:
        try:
            with open(host_exe, 'rb') as fh:
                head = fh.read(512)
        except OSError:
            head = b''
        if head.startswith(b'#!'):
            first_line = os.fsdecode(head.split(b'\n', 1)[0][2:].strip())
            if _re01_has_nul_or_surrogate(first_line):
                return f'RE-01 forbids NUL or lone surrogate in shebang under audit enforcement: {exe_tok}'
            parts = first_line.split(None, 1)
            if not parts:
                return f'RE-01 forbids empty shebang execution under audit enforcement: {exe_tok}'
            target_script = sb_exe if (_mounts is not None and sb_exe is not None) else host_exe
            shebang_argv = [
                parts[0],
                *([parts[1].strip()] if len(parts) > 1 and parts[1].strip() else []),
                target_script,
            ]
            reason = _re01_check_command_reason(
                shebang_argv, cwd=cwd_str, env=env, _depth=_depth + 1, _mounts=_mounts)
            if reason is not None:
                return reason
            if '/' not in parts[0]:
                cwd_interp = posixpath.join(cwd_str, parts[0])
                reason = _re01_check_command_reason(
                    [cwd_interp, *shebang_argv[1:]],
                    cwd=cwd_str, env=env, _depth=_depth + 1, _mounts=_mounts)
                if reason is not None:
                    return reason
    if invoked_cat == 'bwrap':
        return _re01_check_bwrap(
            tokens[1:], cwd=cwd_str, env=env, depth=_depth, outer_mounts=_mounts)
    if invoked_cat == 'python':
        _, no_usersite, ignore_env = _re01_python_opt_flags(tokens[1:])
        py_xy = _re01_python_xy(exe_tok, host_exe)
        _, py_env_err = _re01_check_env(
            env, cwd=cwd_str, mounts=_mounts, check_python=True,
            python_no_usersite=no_usersite, python_ignore_env=ignore_env, python_xy=py_xy)
        if py_env_err is not None:
            return py_env_err
        venv_err = _re01_check_python_venv(
            exe_tok, host_exe, sb_exe, chain, cwd=cwd_str, mounts=_mounts,
            argv0_tok=_argv0, search_path=search_path)
        if venv_err is not None:
            return venv_err
        return _re01_check_python_args(tokens[1:], cwd=cwd_str, mounts=_mounts, env=env)
    if invoked_cat == 'kicad-route-engine':
        return _re01_check_engine_args(
            tokens, host_exe=host_exe, sb_exe=sb_exe, cwd=cwd_str, env=env, depth=_depth, mounts=_mounts)
    for arg_tok in tokens[1:]:
        arg_chain, _, _, _, arg_err = _re01_resolve_exec_chain(
            arg_tok, cwd=cwd_str, is_cmd=False, mounts=_mounts)
        if arg_err is not None:
            return arg_err
        for cand in arg_chain:
            reason = _re01_path_reason(cand, arg_tok, is_cmd_pos=False, cwd=cwd_str)
            if reason is not None:
                return reason
    return None


def check_re01_command(argv, *, cwd=None, env=None):
    reason = _re01_check_command_reason(argv, cwd=cwd, env=env)
    if reason is not None:
        raise RE01OwnershipError(reason)


def _re01_audit_hook(event, args):
    if _RE01_DEPTH.get() <= 0:
        return
    if event == 'subprocess.Popen':
        executable, cmd_args = args[0], args[1]
        if isinstance(executable, int):
            raise RE01OwnershipError('RE-01 forbids file-descriptor subprocess.Popen execution under audit enforcement')
        cwd = _decode_audit_arg(args[2]) if len(args) > 2 and args[2] is not None else None
        env = args[3] if len(args) > 3 else None
        if isinstance(cmd_args, (str, bytes, bytearray)):
            raise RE01OwnershipError('RE-01 forbids shell string execution under audit enforcement')
        if not isinstance(cmd_args, (list, tuple)):
            raise RE01OwnershipError('RE-01 forbids non-sequence subprocess.Popen argv under audit enforcement')
        argv_tokens = [_decode_audit_arg(a) for a in cmd_args]
        if executable is not None:
            exec_tok = _decode_audit_arg(executable)
            if argv_tokens:
                check_re01_command(argv_tokens, cwd=cwd, env=env)
                check_re01_command([exec_tok, *argv_tokens[1:]], cwd=cwd, env=env)
                cat_a = _re01_exec_category(posixpath.basename(_re01_norm_posix(argv_tokens[0])))
                cat_e = _re01_exec_category(posixpath.basename(_re01_norm_posix(exec_tok)))
                if cat_a != cat_e:
                    raise RE01OwnershipError(
                        f'RE-01 forbids mismatched executable {exec_tok} and argv[0] {argv_tokens[0]}')
            else:
                check_re01_command([exec_tok], cwd=cwd, env=env)
        else:
            check_re01_command(argv_tokens, cwd=cwd, env=env)
    elif event in ('os.exec', 'os.posix_spawn'):
        if isinstance(args[0], int):
            raise RE01OwnershipError(f'RE-01 forbids file-descriptor {event} execution under audit enforcement')
        exec_tok = _decode_audit_arg(args[0])
        env = args[2] if len(args) > 2 else None
        if len(args) > 1 and isinstance(args[1], (list, tuple)) and args[1]:
            argv_tokens = [_decode_audit_arg(a) for a in args[1]]
            check_re01_command(argv_tokens, env=env)
            check_re01_command([exec_tok, *argv_tokens[1:]], env=env)
            cat_a = _re01_exec_category(posixpath.basename(_re01_norm_posix(argv_tokens[0])))
            cat_e = _re01_exec_category(posixpath.basename(_re01_norm_posix(exec_tok)))
            if cat_a != cat_e:
                raise RE01OwnershipError(
                    f'RE-01 forbids mismatched executable {exec_tok} and argv[0] {argv_tokens[0]}')
        else:
            check_re01_command([exec_tok], env=env)
    elif event in ('os.system', 'os.popen'):
        raise RE01OwnershipError(f'RE-01 forbids {event} execution under audit enforcement')


def install_re01_audit_hook():
    global _RE01_HOOK_INSTALLED
    if not _RE01_HOOK_INSTALLED:
        sys.addaudithook(_re01_audit_hook)
        _RE01_HOOK_INSTALLED = True


@contextlib.contextmanager
def re01_enforced():
    """Enforce RE-01 ownership via sys.addaudithook while active."""
    install_re01_audit_hook()
    token = _RE01_DEPTH.set(_RE01_DEPTH.get() + 1)
    try:
        yield
    finally:
        _RE01_DEPTH.reset(token)


# Declared layer policy (AGENTEE-231): allowed KRT signal layers, optional KRT
# cost multipliers in the same order, kicad-route's pour area-loss bound and an
# optional reference plane whose fill must lie under the declared RF nets, and
# optional per-net allowed signal layers (AGENTEE-258), graded on the routed board.
COPPER_LAYER = re.compile(r'(F|B|In[1-9][0-9]?)\.Cu\Z')
NET_NAME = re.compile(r'[^\x00-\x1f\x7f]{1,256}\Z')
POLICY_KEYS = {'signal_layers', 'pour_area_loss_pct'}
POLICY_OPTIONAL = {'layer_costs', 'reference', 'per_net_signal_layers'}
REFERENCE_KEYS = {'net', 'layers', 'rf_nets'}
POLICY_FILE = 'agent-ee-reference-continuity.json'
# Uncovered area below this is boolean rounding, not a gap in the plane: the
# clearance KiCad's zone filler cut for the RF net's own pads is rebuilt with the
# filler's own geometry, which leaves about 2e-8 mm2 per pad crossing.
CONTINUITY_TOLERANCE_MM2 = 0.000001
# The zone filler's extra clearance beyond the rule clearance (KiCad's
# ExtraZoneClearance default): without it a clean pad launch leaves 0.00025 mm2.
FILLER_EXTRA_CLEARANCE_MM = 0.0005
# A board rules file with these constraints can change the zone-to-pad clearance,
# which pcbnew's Python cannot evaluate: continuity is then not measured.
CLEARANCE_RULE = re.compile(r'\(\s*constraint\s+(clearance|hole_clearance|physical_clearance|'
                            r'physical_hole_clearance)\b')
# Reference continuity is a 2D check: the declared plane's fill covers each RF
# track's footprint. It does not qualify impedance or the return path.
CONTINUITY_SCOPE = ('2D fill coverage of the declared reference plane under each RF track footprint; '
                    'not an impedance or return-path qualification')
# A pre-existing track on a disallowed layer is exempt only by this geometry,
# matched one to one against the input board's own facts.
TRACK_IDENTITY = ('kind', 'layer', 'net', 'width', 'start', 'mid', 'end')

# KICADROUTE-91 M1b experiment evidence (AGENTEE-442). result.json may carry
# experiment = {path, sha256, schema}; path is relative to the router's run
# directory and is always EXPERIMENT_SIDECAR. --experiment-policy PATH gives the
# router a routability-policy/1 declaration with exactly the five fields
# kicad_route.experiment_policy.load accepts (reviewed at kicad-route a5fa636d).
# The adapter retains the sidecar with its recomputed identity or refuses it with
# a typed reason; it is provenance, never qualified. The root sidecar and the
# router's retained policy copy EXPERIMENT_POLICY_COPY are retained even when
# result.json does not reference them, and are then refused as unbound, never
# dropped. The sidecar, the policy copy and the result.json binding are read with
# strict_json: a repeated key, a non-finite or out-of-range number or excessive
# nesting is malformed evidence, never a crash or a last value. The sidecar shape
# is the one kicad-route a0ed434 writes (schemas/routability-experiment-1.json,
# replayed from its real clean native receipt in tests/fixtures/k91-real): exactly
# EXPERIMENT_SIDECAR_KEYS and EXPERIMENT_IDENTITY_KEYS, identity_sha256 the
# digest of identity, replay_authority false, and artifacts binding the policy
# copy, which kicad-route writes as json.dumps(policy, indent=2) + newline. A
# receipt the producer marks not comparison_eligible is refused as 'ineligible'
# when the coordinator declared a policy (the experiment it asked for is not a
# valid one) and otherwise retained with that marking. A receipt that claims
# comparison_eligible is requalified by _requalify from the retained bytes and
# refused when that proof is missing or unsupported; the flag is never
# authority. The whole sidecar must fit EXPERIMENT_SHAPE. Nothing is pinned or
# qualified by this: EXPERIMENT_CONTRACT names the unpinned producer.
EXPERIMENT_SCHEMA = 'routability-experiment/1'
EXPERIMENT_POLICY_SCHEMA = 'routability-policy/1'
EXPERIMENT_CLASSES = ('current-policy', 'historical-all-layer')
EXPERIMENT_REFERENCE_KEYS = {'path', 'sha256', 'schema'}
EXPERIMENT_POLICY_KEYS = {'schema', 'experiment_class', 'allowed_trace_layers', 'allowed_via_spans',
                          'rule_areas_sha256'}
EXPERIMENT_SIDECAR_POLICY_KEYS = {'declared', 'declared_sha256', 'experiment_class'}
EXPERIMENT_SIDECAR_KEYS = {'schema', 'identity', 'identity_sha256', 'comparison_eligible', 'ineligible_reasons',
                           'policy_qualification', 'native_terminals', 'candidate', 'artifacts', 'outcome',
                           'replay_authority'}
EXPERIMENT_IDENTITY_KEYS = {'input', 'origin', 'scope', 'protection', 'request', 'policy', 'budgets', 'native',
                            'build', 'effective'}
EXPERIMENT_JSON_DEPTH = 64
# The effective-configuration manifest and request an eligible sidecar must seal
# (kicad-route experiment.CONFIGURATION and its request), the one coverage and
# execution this boundary can requalify, and the manifest fields the sealed
# identity.effective restates verbatim.
EXPERIMENT_MANIFESTS = ('work/effective-configuration.json', 'work/effective-configuration-request.json')
EXPERIMENT_MANIFEST_SCHEMA = 'kicad-route/effective-configuration/1'
EXPERIMENT_REQUEST_SCHEMA = 'kicad-route/effective-configuration-request/1'
EXPERIMENT_COVERAGE = 'resolved_outer_batch_only'
EXPERIMENT_EXECUTION = {'outer_batches': 1, 'later_subruns': 0}
EXPERIMENT_RESTATED = ('schema', 'capture_status', 'coverage', 'stage', 'execution', 'not_captured',
                       'configuration', 'effective_environment', 'runtime_policy', 'scope', 'input')
# The three board seals of an eligible receipt (kicad-route a0ed434), each bound to its
# own stage record: the raw backend output (the sealed design artifact the adapter
# observes but never retains, the manifest output and the result.json binding), the
# wrapper output before native refill (the board kicad-route delivered to validation)
# and the graded, refilled board (validated, candidate and file digests).
EXPERIMENT_RAW_OUTPUT = 'work/effective-configuration-output.kicad_pcb'
EXPERIMENT_RAW_STAGE = 'backend_output_before_wrapper_lock_restore_and_native_refill'
EXPERIMENT_WRAPPER_STAGE = 'after_wrapper_postprocessing_before_native_refill'
# The reports an eligible sidecar must name by digest, and the attempt settings its
# identity.request restates (kicad-route records both from the same run options).
EXPERIMENT_BOUND = ('attempt.json', 'facts/reference-facts.json', 'validate/candidate-facts.json',
                    *EXPERIMENT_MANIFESTS, EXPERIMENT_RAW_OUTPUT)
EXPERIMENT_REQUEST_SETTINGS = ('protect', 'mode', 'max_iterations', 'passes', 'default_clearance_mm',
                               'clearance_margin_mm', 'pour_area_loss_pct', 'pour_min_spokes', 'backend_args',
                               'backend_env', 'reserve_spokes', 'barrier', 'rule_floor_sha256')
_UNSTATED = object()
EXPERIMENT_POLICY_FILE = 'agent-ee-experiment-policy.json'
EXPERIMENT_SIDECAR = 'routability-experiment.json'
EXPERIMENT_POLICY_COPY = 'experiment-policy.json'
EXPERIMENT_CONTRACT = ('agent-ee/AGENTEE-442: routability-experiment/1 as written by kicad-route '
                       'a0ed43494d44fd8312054b204083881fe76f3a81 (unpinned, not qualified); '
                       'routability-policy/1 five fields')
EXPERIMENT_REFUSALS = ('missing', 'malformed', 'unknown_schema', 'tampered', 'incompatible_schema',
                       'incompatible_policy', 'unbound', 'ineligible', 'unqualified', 'run_mismatch')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
# The path type retain() records, in place of a digest, for a prepared design-file
# name under work/input that is not a regular file (agent-510 R4).
PATH_TYPES = {'link': 'link', 'dir': 'special', 'other': 'special'}


def _shape(kind, **rules):
    """One node of EXPERIMENT_SHAPE: JSON type name(s) plus the JSON-schema rules it uses."""
    return {'type': (kind,) if isinstance(kind, str) else tuple(kind), **rules}


def _closed(required, optional=None, nullable=False):
    """An object with exactly the required keys plus any optional ones (additionalProperties false)."""
    return _shape(('object', 'null') if nullable else 'object', properties={**required, **(optional or {})},
                  required=tuple(required), closed=True)


# kicad-route a0ed434 schemas/routability-experiment-1.json, restated for the
# stdlib validator _shape_error: every required member, its JSON type, each
# const/enum, every sha256 and sha256-or-null leaf, and each object the producer
# schema closes. A sidecar outside it is malformed evidence (review H2, ee442e).
_SHA = _shape('string', pattern=SHA256)
_SHA_OR_NULL = _shape(('string', 'null'), pattern=SHA256)
_STR_OR_NULL = _shape(('string', 'null'))
_OBJ_OR_NULL = _shape(('object', 'null'))
_STRINGS = _shape('array', items=_shape('string'))
_NAMED_FILE = _closed({'name': _STR_OR_NULL, 'sha256': _SHA_OR_NULL},
                      {'not_loaded': _shape(('array', 'null'), items=_shape('string'))})
_EFFECTIVE = _closed({
    'schema': _shape('string', const='kicad-route/effective-configuration/1'),
    'capture_status': _shape('string', const='complete'), 'coverage': _shape('string'),
    'stage': _shape('string'), 'execution': _shape('object'), 'not_captured': _STRINGS,
    'configuration': _shape('object'), 'effective_environment': _shape('object'),
    'runtime_policy': _shape('object'), 'rules_sha256': _SHA_OR_NULL,
    'routing_environment': _shape('object', values=_shape('string')), 'invocation': _STRINGS,
    'scope': _STRINGS, 'net_inventory_sha256': _SHA_OR_NULL,
    'input': _closed({'board_sha256': _SHA_OR_NULL,
                      'rules': _closed({'.kicad_pro': _SHA_OR_NULL, '.kicad_dru': _SHA_OR_NULL})}, nullable=True)},
    nullable=True)
EXPERIMENT_SHAPE = _closed({
    'schema': _shape('string', const=EXPERIMENT_SCHEMA),
    'identity': _closed({
        'input': _closed({
            'fingerprint': _STR_OR_NULL, 'board': _NAMED_FILE, 'project': _NAMED_FILE, 'rules': _NAMED_FILE,
            'files': _shape(('object', 'null'), values=_SHA), 'snapshot_files': _shape(('object', 'null'), values=_SHA),
            'netlist_sha256': _SHA_OR_NULL, 'nets_sha256': _SHA_OR_NULL, 'placement_sha256': _SHA_OR_NULL,
            'mechanical_sha256': _SHA_OR_NULL, 'setup_sha256': _SHA_OR_NULL,
            'native_facts_kicad_version': _STR_OR_NULL, 'source': _OBJ_OR_NULL}),
        'origin': _closed({
            'root_input_fingerprint': _STR_OR_NULL, 'depth': _shape('integer', minimum=0),
            'predecessors': _shape('array', items=_closed({
                'attempt_sha256': _SHA_OR_NULL, 'candidate_board_sha256': _SHA_OR_NULL,
                'experiment_sha256': _SHA_OR_NULL}))}),
        'scope': _OBJ_OR_NULL,
        'protection': _shape('object', required=('mode', 'only_sha256')),
        'request': _shape('object'),
        'policy': _closed({
            'declared': _OBJ_OR_NULL, 'declared_sha256': _SHA_OR_NULL,
            'experiment_class': _shape(('string', 'null'), enum=(*EXPERIMENT_CLASSES, None)),
            'physical_stack': _shape(('array', 'null'), items=_shape('string')),
            'input_rule_areas_sha256': _SHA_OR_NULL, 'input_rule_areas_coverage': _shape('object')}),
        'budgets': _closed({'backend_s': _shape(('integer', 'null')), 'stall_s': _shape(('number', 'null')),
                            'command_budget_s': _shape(('integer', 'null')), 'grace_s': _shape(('number', 'null'))}),
        'native': _closed({'kicad_cli': _OBJ_OR_NULL, 'pcbnew_build': _STR_OR_NULL,
                           'terminal_producer': _OBJ_OR_NULL, 'terminal_native_identity': _OBJ_OR_NULL}),
        'build': _closed({'wrapper': _OBJ_OR_NULL, 'backend': _shape('object'), 'execution_build_id': _STR_OR_NULL,
                          'executed_source_sha256': _SHA_OR_NULL, 'producer_sha256': _SHA_OR_NULL,
                          'state_manifest_sha256': _SHA_OR_NULL}),
        'effective': _EFFECTIVE}),
    'identity_sha256': _SHA,
    'comparison_eligible': _shape('boolean'),
    'ineligible_reasons': _STRINGS,
    'policy_qualification': _shape('object', required=(
        'status', 'reasons', 'policy_sha256', 'rule_areas_sha256', 'coverage', 'actual_trace_layers',
        'actual_via_spans', 'effective_layers', 'effective_via'), properties={
        'status': _shape('string', enum=('PASS', 'FAIL', 'NOT_RUN')), 'reasons': _STRINGS,
        'actual_trace_layers': _STRINGS, 'actual_via_spans': _shape('array', items=_STRINGS)}),
    'native_terminals': _shape('object', values=_shape('object', required=(
        'board_sha256', 'inventory_sha256', 'terminal_nets', 'incomplete_nets', 'component_deficit'))),
    'candidate': _closed({
        'raw_backend_output_board_sha256': _SHA_OR_NULL, 'backend_output_board_sha256': _SHA_OR_NULL, 'pre_refill_board_sha256': _SHA_OR_NULL,
        'candidate_board_sha256': _SHA_OR_NULL, 'candidate_copper_signature': _OBJ_OR_NULL,
        'validated_board_sha256': _SHA_OR_NULL, 'file_sha256': _SHA_OR_NULL}),
    'artifacts': _shape('object', values=_SHA_OR_NULL),
    'outcome': _closed({'process': _STR_OR_NULL, 'routing': _STR_OR_NULL, 'validation': _STR_OR_NULL,
                        'unconnected_total': _shape(('integer', 'null')),
                        'backend_runtime_s': _shape(('number', 'null')), 'wall_clock_stop': _shape('boolean')}),
    'replay_authority': _shape('boolean', const=False)})
_JSON_TYPES = {'string': (str,), 'null': (type(None),), 'object': (dict,), 'array': (list,), 'boolean': (bool,),
               'integer': (int,), 'number': (int, float)}


def _shape_error(value, shape, where='sidecar'):
    """The first place value leaves shape, as 'where: what', or None. Iterative, so nesting
    is bounded by strict_json's EXPERIMENT_JSON_DEPTH rather than by recursion."""
    stack = [(value, shape, where)]
    while stack:
        value, shape, where = stack.pop()
        # bool is a Python int, never a JSON integer or number.
        kinds = [k for k in shape['type'] if isinstance(value, _JSON_TYPES[k]) and
                 not (isinstance(value, bool) and k in ('integer', 'number'))]
        if not kinds:
            return f'{where} must be {" or ".join(shape["type"])}'
        if 'const' in shape and canonical_json(value) != canonical_json(shape['const']):
            return f'{where} must be {canonical_json(shape["const"])}'
        if 'enum' in shape and canonical_json(value) not in map(canonical_json, shape['enum']):
            return f'{where} must be one of {canonical_json(list(shape["enum"]))}'
        if isinstance(value, str) and 'pattern' in shape and not shape['pattern'].match(value):
            return f'{where} must be 64 lowercase hex digits'
        if isinstance(value, int) and 'minimum' in shape and value < shape['minimum']:
            return f'{where} must be at least {shape["minimum"]}'
        if isinstance(value, dict):
            missing = [k for k in shape.get('required', ()) if k not in value]
            if missing:
                return f'{where} lacks {", ".join(missing)}'
            extra = sorted(set(value) - set(shape['properties'])) if shape.get('closed') else []
            if extra:
                return f'{where} has unsupported fields {", ".join(extra)}'
            for key in sorted(value, reverse=True):
                inner = shape.get('properties', {}).get(key, shape.get('values'))
                if inner is not None:
                    stack.append((value[key], inner, f'{where}.{key}'))
        if isinstance(value, list) and 'items' in shape:
            stack.extend((item, shape['items'], f'{where}[{i}]') for i, item in reversed(list(enumerate(value))))
    return None


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _number(value, low, high):
    return (type(value) in (int, float) and value == value and low <= value <= high)


def _names(value, pattern, label):
    if (not isinstance(value, list) or not value or len(value) != len(set(map(str, value))) or
            any(not isinstance(v, str) or not pattern.match(v) for v in value)):
        raise RequestError(f'{label} must be a nonempty list of distinct names matching {pattern.pattern}')
    return value


def validate_layer_policy(policy):
    """The declared layer policy, unchanged, or RequestError."""
    if (not isinstance(policy, dict) or not POLICY_KEYS <= set(policy) or
            not set(policy) <= POLICY_KEYS | POLICY_OPTIONAL):
        raise RequestError('layer policy must have signal_layers and pour_area_loss_pct, '
                           'optionally layer_costs, reference and per_net_signal_layers')
    _names(policy['signal_layers'], COPPER_LAYER, 'signal_layers')
    if 'per_net_signal_layers' in policy:
        nets = policy['per_net_signal_layers']
        if not isinstance(nets, dict) or not nets:
            raise RequestError('per_net_signal_layers must map net names to allowed layers')
        for net, layers in nets.items():
            if not isinstance(net, str) or not NET_NAME.fullmatch(net):
                raise RequestError('per_net_signal_layers keys must be net names')
            _names(layers, COPPER_LAYER, 'per-net signal layers')
            if not set(layers) <= set(policy['signal_layers']):
                raise RequestError('per-net signal layers must be within global signal_layers')
    if 'layer_costs' in policy:
        costs = policy['layer_costs']
        if (not isinstance(costs, list) or len(costs) != len(policy['signal_layers']) or
                not all(_number(c, 1, 1000) for c in costs)):
            raise RequestError('layer_costs must give one multiplier from 1 to 1000 per signal layer')
    if not _number(policy['pour_area_loss_pct'], 0, 100):
        raise RequestError('pour_area_loss_pct must be a number from 0 to 100')
    if 'reference' in policy:
        ref = policy['reference']
        if not isinstance(ref, dict) or set(ref) != REFERENCE_KEYS:
            raise RequestError('reference must have exactly net, layers and rf_nets')
        if not isinstance(ref['net'], str) or not NET_NAME.match(ref['net']):
            raise RequestError('reference net must be a net name')
        _names(ref['layers'], COPPER_LAYER, 'reference layers')
        _names(ref['rf_nets'], NET_NAME, 'reference rf_nets')
        if ref['net'] in ref['rf_nets']:
            raise RequestError('the reference net cannot be one of its RF nets')
    return policy


def parse_layer_policy(text):
    try:
        policy = json.loads(text)
    except (TypeError, ValueError, RecursionError):
        raise RequestError('layer policy must be JSON') from None
    validate_layer_policy(policy)
    if canonical_json(policy) != text:
        raise RequestError('layer policy must be canonical JSON (sorted keys, no spaces)')
    return policy


def _unique_names(value):
    return (isinstance(value, list) and bool(value) and
            all(isinstance(v, str) and v for v in value) and len(value) == len(set(value)))


def strict_json(data, label):
    """JSON text or bytes with unique keys, finite numbers and bounded nesting, or RequestError.

    The same strictness as kicad_route.experiment_policy.load: a repeated key is
    ambiguous evidence, never silently resolved to its last value. NaN,
    Infinity, a float that overflows to infinity, an integer beyond the
    interpreter's digit limit and nesting beyond EXPERIMENT_JSON_DEPTH are
    malformed evidence too, never an escaping exception."""
    def unique(pairs):
        found = {}
        for key, value in pairs:
            if key in found:
                raise RequestError(f'{label} has a duplicate key {json.dumps(key)}')
            found[key] = value
        return found

    def constant(name):
        raise RequestError(f'{label} has a non-finite number {name}')

    def finite(token):
        value = float(token)
        if not math.isfinite(value):
            raise RequestError(f'{label} has a non-finite number {token[:32]}')
        return value
    try:
        value = json.loads(data, object_pairs_hook=unique, parse_constant=constant, parse_float=finite)
    except RequestError:
        raise
    except RecursionError:
        raise RequestError(f'{label} nesting exceeds {EXPERIMENT_JSON_DEPTH}') from None
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
        raise RequestError(f'{label} must be JSON') from None
    except ValueError:
        # For example an integer beyond the interpreter's digit limit.
        raise RequestError(f'{label} has an out-of-range number') from None
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, (dict, list)):
            if depth > EXPERIMENT_JSON_DEPTH:
                raise RequestError(f'{label} nesting exceeds {EXPERIMENT_JSON_DEPTH}')
            stack.extend((v, depth + 1) for v in (item.values() if isinstance(item, dict) else item))
    return value


def strict_json_file(path, label):
    """strict_json of a retained regular file within JSON_LIMIT, or RequestError."""
    if not path.is_file() or path.is_symlink():
        raise RequestError(f'{label} is not a regular file')
    if path.stat().st_size > JSON_LIMIT:
        raise RequestError(f'{label} exceeds {JSON_LIMIT} bytes')
    return strict_json(path.read_bytes(), label)


def validate_experiment_policy(policy, label='experiment policy'):
    """A routability-policy/1 document, unchanged, or RequestError.

    Exactly the five fields kicad_route.experiment_policy.load accepts, with its
    value rules; any other schema version, class, key or shape (including the
    two-field {schema, policy} draft) is refused. Whether rule_areas_sha256
    matches the board's native rule areas is the router's qualification, which
    agent-ee does not claim."""
    if not isinstance(policy, dict) or set(policy) != EXPERIMENT_POLICY_KEYS:
        raise RequestError(f'{label} must have exactly the routability-policy/1 fields '
                           + ', '.join(sorted(EXPERIMENT_POLICY_KEYS)))
    if policy['schema'] != EXPERIMENT_POLICY_SCHEMA:
        raise RequestError(f'incompatible {label} schema: expected {EXPERIMENT_POLICY_SCHEMA}')
    if policy['experiment_class'] not in EXPERIMENT_CLASSES:
        raise RequestError(f'experiment_class must be one of {EXPERIMENT_CLASSES}')
    if not _unique_names(policy['allowed_trace_layers']):
        raise RequestError('allowed_trace_layers must be unique nonempty layer names')
    spans = policy['allowed_via_spans']
    if not isinstance(spans, list) or any(not _unique_names(s) or len(s) != 2 for s in spans):
        raise RequestError('allowed_via_spans must contain distinct endpoint pairs')
    if len({tuple(s) for s in spans}) != len(spans):
        raise RequestError('duplicate allowed_via_spans')
    if not isinstance(policy['rule_areas_sha256'], str) or not SHA256.match(policy['rule_areas_sha256']):
        raise RequestError('rule_areas_sha256 must be 64 lowercase hex digits')
    return policy


def parse_experiment_policy(text):
    """A declared routability-policy/1 document, unchanged, or RequestError.

    validate_experiment_policy of strict canonical JSON; refused before the
    router runs."""
    policy = validate_experiment_policy(strict_json(text, 'experiment policy'))
    if canonical_json(policy) != text:
        raise RequestError('experiment policy must be canonical JSON (sorted keys, no spaces)')
    return policy


def experiment_policy_identity(policy):
    """The identity of the declaration the adapter writes for the router, or None.

    sha256 is of the canonical file bytes; policy_sha256 is kicad-route's
    common.sha256_of(policy), the digest experiment_policy.validate reports."""
    if policy is None:
        return None
    return {'declared': policy, 'sha256': hashlib.sha256(canonical_json(policy).encode()).hexdigest(),
            'policy_sha256': hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()}


def retain_experiment(run_dir, routing):
    """Copy the root sidecar and the router's policy copy into routing/attempt.

    Both are run-relative root files, copied whether or not result.json names
    them, so experiment_grade can grade or refuse them from the retained bytes.
    Returns the retained names and those that are links or special files."""
    kept, links = [], []
    if run_dir is None:
        return kept, links
    for name in (EXPERIMENT_SIDECAR, EXPERIMENT_POLICY_COPY):
        src = Path(run_dir) / name
        if src.is_symlink() or (os.path.lexists(src) and not src.is_file()):
            links.append(name)
        elif src.is_file():
            shutil.copyfile(src, routing / 'attempt' / name)
            kept.append(name)
    return kept, links


def _retained_file(attempt_dir, name):
    path = Path(attempt_dir) / name
    if path.is_symlink() or not path.is_file():
        return None
    return {'path': f'attempt/{name}', 'sha256': digest(path)}


def experiment_grade(request, result, attempt_dir, run=None):
    """The experiment-evidence record, re-derived from the retained bytes alone.

    None when no policy was declared, result.json references no sidecar and
    neither root file was retained. Otherwise 'retained' with the sidecar's
    recomputed identity, 'refused' with one of EXPERIMENT_REFUSALS, or 'absent'
    when the router wrote neither result.json nor a sidecar. The reference binds
    only the run-relative EXPERIMENT_SIDECAR; a retained root file that no valid
    reference binds is refused as 'unbound' with its recomputed identity. A
    sidecar that claims comparison_eligible is requalified by _requalify from
    the retained attempt record, facts and sealed manifests it names, bound to
    the execution run (experiment_run), and refused (typed) when that proof is
    missing, unsupported or another run's.
    Every record is qualified: false."""
    expected = experiment_policy_identity(request.get('experiment_policy'))
    has_ref = isinstance(result, dict) and 'experiment' in result
    orphan = _retained_file(attempt_dir, EXPERIMENT_SIDECAR)
    copy = _retained_file(attempt_dir, EXPERIMENT_POLICY_COPY)
    if expected is None and not has_ref and orphan is None and copy is None:
        return None
    base = {'qualified': False, 'contract': EXPERIMENT_CONTRACT, 'policy': expected,
            'policy_file': f'attempt/{EXPERIMENT_POLICY_FILE}' if expected is not None else None,
            'policy_copy': copy}
    ref = result.get('experiment') if isinstance(result, dict) else None
    try:
        canonical_json(ref)
        echo = ref
    except (ValueError, RecursionError):
        # A non-finite number the permissive result.json read decoded: not echoed.
        echo = None

    def refused(reason, detail, **known):
        return {'status': 'refused', **base, 'reason': reason, 'detail': detail,
                'reference': echo, 'path': None, 'sha256': None, **known}

    unbound = orphan or copy
    unbound_name = EXPERIMENT_SIDECAR if orphan is not None else EXPERIMENT_POLICY_COPY
    if not isinstance(result, dict):
        if unbound is not None:
            return refused('unbound', f'{unbound_name} exists but there is no result.json to bind it', **unbound)
        return {'status': 'absent', **base, 'reason': 'no result.json: the router reported no experiment'}
    if not has_ref:
        if unbound is not None:
            return refused('unbound', f'{unbound_name} exists but result.json has no experiment reference',
                           **unbound)
        return refused('missing', 'a policy was declared but result.json has no experiment reference')
    if echo is not ref:
        return refused('malformed', 'experiment reference has a non-finite number or excessive nesting',
                       **(orphan or {}))
    if (not isinstance(ref, dict) or set(ref) != EXPERIMENT_REFERENCE_KEYS or
            not all(isinstance(ref[k], str) for k in EXPERIMENT_REFERENCE_KEYS)):
        return refused('malformed', 'experiment reference must have exactly string path, sha256 and schema',
                       **(orphan or {}))
    ambiguous = _ambiguous_binding(Path(attempt_dir) / 'result.json')
    if ambiguous:
        return refused('malformed', ambiguous, **(orphan or {}))
    if ref['schema'] != EXPERIMENT_SCHEMA:
        return refused('unknown_schema', f'experiment reference schema is not {EXPERIMENT_SCHEMA}',
                       **(orphan or {}))
    if not SHA256.match(ref['sha256']):
        return refused('malformed', 'experiment reference sha256 is not 64 lowercase hex digits',
                       **(orphan or {}))
    if ref['path'] != EXPERIMENT_SIDECAR:
        # Only the run-relative root sidecar binds; a cwd-relative, absolute or
        # other path is unsupported and must not look like bound evidence.
        return refused('malformed', f'experiment path is not the run-relative {EXPERIMENT_SIDECAR}',
                       **(orphan or {}))
    if orphan is None:
        return refused('missing', 'no regular sidecar file at the referenced path was retained')
    if orphan['sha256'] != ref['sha256']:
        return refused('tampered', 'sidecar sha256 differs from the result.json reference', **orphan)
    try:
        body = strict_json_file(Path(attempt_dir) / EXPERIMENT_SIDECAR, 'experiment sidecar')
    except RequestError as exc:
        return refused('malformed', str(exc), **orphan)
    if not isinstance(body, dict):
        return refused('malformed', 'experiment sidecar is not a JSON object', **orphan)
    if body.get('schema') != EXPERIMENT_SCHEMA:
        return refused('incompatible_schema', f'sidecar schema is not {EXPERIMENT_SCHEMA}', **orphan)
    shape = _sidecar_shape_refusal(body)
    if shape is not None:
        return refused(*shape, **orphan)
    identity = body['identity']
    claimed = identity['policy']
    if not isinstance(claimed, dict) or not EXPERIMENT_SIDECAR_POLICY_KEYS <= set(claimed):
        return refused('malformed', 'sidecar has no identity.policy {declared, declared_sha256, experiment_class}',
                       **orphan)
    declared = None if expected is None else expected['declared']
    # Canonical bytes, not decoded equality: 1 == 1.0 == True in Python.
    if (canonical_json(claimed['declared']) != canonical_json(declared) or
            canonical_json(claimed['declared_sha256']) !=
            canonical_json(None if expected is None else expected['policy_sha256']) or
            canonical_json(claimed['experiment_class']) !=
            canonical_json(None if declared is None else declared['experiment_class'])):
        return refused('incompatible_policy', 'sidecar policy identity differs from the declared policy', **orphan)
    bound = body['artifacts'].get(EXPERIMENT_POLICY_COPY)
    if copy is not None:
        refusal = _policy_copy_refusal(Path(attempt_dir) / EXPERIMENT_POLICY_COPY, declared)
        if refusal is not None:
            return refused(*refusal, **orphan)
        if bound != copy['sha256']:
            return refused('tampered', f'{EXPERIMENT_POLICY_COPY} sha256 differs from the sidecar artifacts digest',
                           **orphan)
    elif bound is not None:
        return refused('missing', f'the sidecar binds {EXPERIMENT_POLICY_COPY} but no regular copy was retained',
                       **orphan)
    eligibility = {'identity_sha256': body['identity_sha256'], 'comparison_eligible': body['comparison_eligible'],
                   'ineligible_reasons': body['ineligible_reasons']}
    if expected is not None and body['comparison_eligible'] is not True:
        return refused('ineligible', 'the producer marked the declared experiment not comparison-eligible: '
                       + '; '.join(body['ineligible_reasons'][:8]), **orphan, **eligibility)
    if body['comparison_eligible']:
        unqualified = _requalify(body, result, attempt_dir, run)
        if unqualified is not None:
            return refused(*unqualified, **orphan, **eligibility)
    return {'status': 'retained', **base, 'schema': EXPERIMENT_SCHEMA, 'reference': ref, **orphan, **eligibility}


def _sidecar_shape_refusal(body):
    """(reason, detail) refusing a sidecar that is not kicad-route's routability-experiment/1, or None.

    The top-level and identity key sets are exact (the producer schema forbids
    additional properties); identity_sha256 must be kicad-route's
    common.sha256_of(identity), so an identity edited after it was sealed is
    tampered even when result.json re-binds the edited file."""
    if set(body) != EXPERIMENT_SIDECAR_KEYS:
        return 'malformed', 'sidecar must have exactly the fields ' + ', '.join(sorted(EXPERIMENT_SIDECAR_KEYS))
    identity = body['identity']
    if not isinstance(identity, dict) or set(identity) != EXPERIMENT_IDENTITY_KEYS:
        return 'malformed', 'sidecar identity must have exactly ' + ', '.join(sorted(EXPERIMENT_IDENTITY_KEYS))
    if body['replay_authority'] is not False:
        return 'malformed', 'sidecar replay_authority must be false'
    reasons = body['ineligible_reasons']
    if (not isinstance(body['comparison_eligible'], bool) or not isinstance(reasons, list) or
            not all(isinstance(r, str) for r in reasons) or body['comparison_eligible'] == bool(reasons)):
        return 'malformed', 'sidecar comparison_eligible must be a boolean that is true exactly without reasons'
    artifacts = body['artifacts']
    if not isinstance(artifacts, dict) or any(v is not None and (not isinstance(v, str) or not SHA256.match(v))
                                              for v in artifacts.values()):
        return 'malformed', 'sidecar artifacts must map names to sha256 digests or null'
    if not isinstance(body['identity_sha256'], str) or not SHA256.match(body['identity_sha256']):
        return 'malformed', 'sidecar identity_sha256 is not 64 lowercase hex digits'
    error = _shape_error(body, EXPERIMENT_SHAPE)
    if error is not None:
        return 'malformed', f'{error} (kicad-route routability-experiment/1)'
    if body['identity_sha256'] != _sha256_of(identity):
        return 'tampered', 'sidecar identity_sha256 differs from the digest of its identity'
    return None


def _sha256_of(value):
    """kicad-route common.sha256_of: the digest of the sorted-key json.dumps text."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _retained_json(attempt_dir, name, label=None):
    """(value, None) for a retained regular strict-JSON file, else (None, (reason, detail))."""
    path = Path(attempt_dir) / name
    if path.is_symlink() or not path.is_file():
        return None, ('missing', f'no regular {name} was retained')
    try:
        return strict_json_file(path, label or name), None
    except RequestError as exc:
        return None, ('malformed', str(exc))


def experiment_run(request, layout, attempt_dir, omitted=None):
    """The execution an eligible sidecar must describe, or None without a discovered run.

    attempt_dir is the discovered run relative to routing/ (report.discovery);
    the paths are those the sandboxed router was given (tool_argv's --out and
    --dir), so the sidecar is bound to this execution, never to its own claims.
    omitted is the adapter's own record of the design files retain() observed
    in the run and did not copy (report.omitted_design_files): the only digests
    of the raw backend output and of the prepared backend input (work/input)
    that are not the producer's claims."""
    if not isinstance(attempt_dir, str):
        return None
    return {'run_dir': str(Path(layout['work']) / 'routing' / attempt_dir),
            'source_dir': str(staged_dir(request, layout)),
            'observed': dict(omitted) if isinstance(omitted, dict) else {}}


def _bound_artifacts(sealed, attempt_dir, observed):
    """(reason, detail) unless every artifact the sidecar names is the retained file it names.

    Each non-null digest must name a run-relative report retain() copies and be
    recomputed from that retained file; the attempt record, both facts files,
    the sealed manifests and the raw backend output must be named
    (EXPERIMENT_BOUND). The raw output is a design file, never retained: its
    digest must be the one the adapter observed in this run (observed, from
    experiment_run). No other design file can be bound. The adapter's own files
    are never the identity source: an unnamed one is unbound evidence."""
    for name, want in sorted(sealed.items()):
        if name == EXPERIMENT_POLICY_COPY or want is None:
            continue
        parts = name.split('/')
        if (any(p in ('', '.', '..') for p in parts) or
                not (name in RETAINED_FILES or (len(parts) > 1 and parts[0] in RETAINED_DIRS))):
            return 'malformed', f'sidecar artifact {name!r} is not a run-relative retained report'
        if name == EXPERIMENT_RAW_OUTPUT:
            if name not in observed:
                return 'missing', f'the sidecar binds {name} but the adapter observed no such regular file'
            if observed[name] != want:
                return 'tampered', f'{name} sha256 differs from the sidecar artifacts digest'
            continue
        if name.endswith(DESIGN_SUFFIXES):
            return 'malformed', f'sidecar artifact {name!r} is a design file, which is never retained'
        kept = _retained_file(attempt_dir, name)
        if kept is None:
            return 'missing', f'the sidecar binds {name} but no regular file was retained'
        if kept['sha256'] != want:
            return 'tampered', f'{name} sha256 differs from the sidecar artifacts digest'
    for name in EXPERIMENT_BOUND:
        if sealed.get(name) is None:
            return 'unbound', f'the eligible sidecar binds no {name} digest'
    return None


def _requalify(body, result, attempt_dir, run):
    """(reason, detail) refusing an eligible-claiming sidecar this boundary cannot requalify, or None.

    The producer's comparison_eligible flag and the stored 'bound' status are
    claims, not authority (review H1, ee442e). First every artifact the sidecar
    names is bound by digest to the retained file (_bound_artifacts), so the
    checks below read only the bytes the sidecar describes. Then the obligations
    are re-derived from them: the attempt record and the native facts are JSON
    objects of the producer's shape (EI4); the effective-configuration manifest
    covers exactly one outer batch and no later subrun, its capture is complete,
    it binds the retained request by hash, and the sidecar identity and the
    result.json process binding restate it (EI2); and the origin is a single run
    whose input is not its own output (EI3). Last, the sidecar must describe this
    execution (run, an experiment_run): its input, source, request, candidate,
    outcome and output are this attempt's, else 'run_mismatch'. A predecessor
    chain is unsupported here: its receipts are not retained."""
    sealed = body['artifacts']
    refusal = _bound_artifacts(sealed, attempt_dir, (run or {}).get('observed') or {})
    if refusal is not None:
        return refusal
    record, refusal = _retained_json(attempt_dir, 'attempt.json')
    if refusal is not None:
        return refusal
    source = record.get('source') if isinstance(record, dict) else None
    if (not isinstance(record, dict) or not isinstance(record.get('schema'), str) or
            not record['schema'].startswith('kicad-route/attempt/') or
            not all(isinstance(record.get(k), str) and record[k] for k in ('board', 'candidate_dir',
                                                                           'input_fingerprint')) or
            not isinstance(source, dict) or not isinstance(source.get('source_dir'), str) or
            not isinstance(record.get('settings'), dict)):
        return 'malformed', ('attempt.json is not a kicad-route attempt record (an object with schema, board, '
                             'candidate_dir, input_fingerprint and source.source_dir strings and settings)')
    facts = {}
    for name, lists in (('facts/reference-facts.json', ('tracks', 'vias', 'copper_layers')),
                        ('validate/candidate-facts.json', ('tracks', 'vias'))):
        facts[name], refusal = _retained_json(attempt_dir, name)
        if refusal is not None:
            return refusal
        found = facts[name]
        if (not isinstance(found, dict) or
                any(k in found and (not isinstance(found[k], list) or
                                    not all(isinstance(v, dict) for v in found[k])) for k in lists) or
                ('footprints' in found and not isinstance(found['footprints'], dict))):
            return 'malformed', f'{name} is not a native facts object (lists of objects, footprints an object)'
    manifest = {}
    for name in EXPERIMENT_MANIFESTS:
        manifest[name], refusal = _retained_json(attempt_dir, name)
        if refusal is not None:
            return refusal
        if not isinstance(manifest[name], dict):
            return 'malformed', f'{name} is not a JSON object'
    config, request = (manifest[n] for n in EXPERIMENT_MANIFESTS)
    config_name, request_name = EXPERIMENT_MANIFESTS
    if request.get('schema') != EXPERIMENT_REQUEST_SCHEMA:
        return 'incompatible_schema', f'{request_name} schema is not {EXPERIMENT_REQUEST_SCHEMA}'
    if config.get('schema') != EXPERIMENT_MANIFEST_SCHEMA:
        return 'incompatible_schema', f'{config_name} schema is not {EXPERIMENT_MANIFEST_SCHEMA}'
    if config.get('capture_status') != 'complete' or config.get('issues') != []:
        return 'unqualified', f'{config_name} capture is not complete without issues'
    if config.get('coverage') != EXPERIMENT_COVERAGE:
        return 'unqualified', f'{config_name} coverage is not {EXPERIMENT_COVERAGE}'
    if canonical_json(config.get('execution')) != canonical_json(EXPERIMENT_EXECUTION):
        return 'unqualified', (f'{config_name} execution is not one outer batch without later subruns: '
                               + canonical_json(config.get('execution')))
    if config.get('request_sha256') != sealed[request_name]:
        return 'unqualified', f'{config_name} request_sha256 is not the retained {request_name}'
    for key in ('source_sha256', 'producer_sha256'):
        if not isinstance(config.get(key), str) or config[key] != request.get(key):
            return 'unqualified', f'{config_name} {key} differs from {request_name}'
    identity = body['identity']
    effective, build = identity['effective'], identity['build']
    restated = {**{k: config.get(k) for k in EXPERIMENT_RESTATED},
                'rules_sha256': _sha256_of(config['rules']) if config.get('rules') is not None else None,
                'net_inventory_sha256': _sha256_of(config['net_inventory']) if config.get('net_inventory') else None}
    if (effective is None or any(canonical_json(effective[k]) != canonical_json(v) for k, v in restated.items()) or
            build['executed_source_sha256'] != config['source_sha256'] or
            build['producer_sha256'] != config['producer_sha256']):
        return 'unqualified', f'the sidecar identity does not restate the retained {config_name}'
    process = result.get('process')
    binding = process.get('effective_configuration') if isinstance(process, dict) else None
    if (not isinstance(binding, dict) or binding.get('status') != 'bound' or
            binding.get('sha256') != sealed[config_name] or binding.get('request_sha256') != sealed[request_name] or
            binding.get('capture_status') != 'complete' or binding.get('coverage') != EXPERIMENT_COVERAGE):
        return 'unqualified', f'result.json process.effective_configuration does not bind the retained {config_name}'
    origin, claimed = identity['origin'], identity['input']['fingerprint']
    if origin['depth'] != 0 or origin['predecessors']:
        return 'unqualified', (f'the origin chain has {origin["depth"]} predecessor(s) whose receipts this '
                               'boundary does not retain')
    if origin['root_input_fingerprint'] is None or origin['root_input_fingerprint'] != claimed:
        return 'unqualified', 'origin root_input_fingerprint is not the sidecar input fingerprint'
    if run is None:
        return 'run_mismatch', 'no discovered run to bind the eligible sidecar to'
    # kicad-route cli.previous_step resolves source_dir and stops at a revisit of this run,
    # reporting depth 0 (EI3). The path is a producer-namespace string: it is normalised
    # lexically (., .., repeated and trailing separators), never followed on this host.
    # Any form inside this run is a self-origin, whatever alias reaches it; any other
    # string than the exact input directory the adapter passed is not this execution.
    src = source['source_dir']
    run_dir = PurePosixPath(run['run_dir'])
    canon = PurePosixPath(posixpath.normpath('/' + src.lstrip('/'))) if src.startswith('/') else None
    if canon is not None and (canon == run_dir or run_dir in canon.parents):
        return 'unqualified', ("origin is a self-cycle: attempt.json source_dir is this run's own candidate "
                               f'or another path inside this run ({src})')
    if src != run['source_dir']:
        return 'run_mismatch', f'attempt.json source_dir {src!r} is not this attempt\'s input {run["source_dir"]}'
    return _run_mismatch(body, result, record, facts, config, request, attempt_dir, run_dir,
                         run.get('observed') or {})


def _rules_inventory(files, board):
    """kicad-route a0ed434 cli.rules_files over the typed snapshot: (project, custom_rules,
    not_loaded) KiCad grades board with, derived from file presence, never from a claim.

    KiCad loads only the project and custom rules of the board's own name; every other
    .kicad_pro/.kicad_dru beside the board is not loaded. The snapshot records the
    reference copy rules_files reads, by run-relative name."""
    path = PurePosixPath(board)
    project, rules = path.with_suffix('.kicad_pro').name, path.with_suffix('.kicad_dru').name
    beside = [PurePosixPath(n) for n in files if PurePosixPath(n).parent == path.parent]
    return (project, rules if str(path.with_suffix('.kicad_dru')) in files else None,
            sorted(p.name for p in beside if p.suffix in ('.kicad_pro', '.kicad_dru') and
                   p.name not in (project, rules)))


def _prepared_input(record, board, observed):
    """(prepared, refusal): the prepared backend input the adapter observed (kicad-route
    a0ed434 experiment._retained_backend_input): work/input/<reference name>/<board> and
    its .kicad_pro and .kicad_dru, by the digests retain() recorded.

    Without a regular prepared board and project, prepared is None. The optional
    .kicad_dru is null only when retain() observed no entry: a link (valid or dangling)
    or special file there is neither absent nor regular, and is refused without being
    followed, as the producer's input guard rejects it (agent-510 R4)."""
    reference = record.get('reference_dir')
    name = PurePosixPath(reference).name if isinstance(reference, str) else ''
    if name in ('', '.', '..') or PurePosixPath(board).name != board:
        return None, None
    path = PurePosixPath('work/input', name, board)

    def regular(p):
        value = observed.get(str(p))
        return value if isinstance(value, str) and SHA256.match(value) else None
    prepared = {'board_sha256': regular(path),
                'rules': {s: regular(path.with_suffix(s)) for s in ('.kicad_pro', '.kicad_dru')}}
    if not (prepared['board_sha256'] and prepared['rules']['.kicad_pro']):
        return None, None
    rules = str(path.with_suffix('.kicad_dru'))
    if rules in observed and prepared['rules']['.kicad_dru'] is None:
        kind = {'link': 'link', 'special': 'directory or special file'}.get(observed[rules], 'unrecognised entry')
        return None, f'the prepared {rules} is a {kind}, neither absent nor a regular file'
    return prepared, None


def _run_mismatch(body, result, record, facts, config, request, attempt_dir, run_dir, observed):
    """(reason, detail) unless the sidecar describes this execution, or None.

    Every comparison is with retained bytes the adapter binds independently:
    result.json (whose input, settings, validation and run the adapter checks
    against the request and the delivered board), snapshot.json, and the attempt
    record, facts and manifests the sidecar names. Two input stages stay distinct:
    the ORIGINAL input (identity.input) is the snapshot, with the rules inventory
    derived from it (_rules_inventory); the PREPARED input (identity.effective.input,
    both manifests' input and the binding's captured_input_sha256) is the
    work/input the adapter observed (_prepared_input), which protection and
    preprocessing may change. A self-consistent sidecar of another run, even with
    digests rewritten to this run's files, differs here ('run_mismatch'); a
    snapshot.json that is not a file-digest map is 'missing' or 'malformed',
    never a crash."""
    identity, candidate, outcome, sealed = body['identity'], body['candidate'], body['outcome'], body['artifacts']
    claimed = identity['input']
    inp = result.get('input') if isinstance(result.get('input'), dict) else {}
    snapshot, refusal = _retained_json(attempt_dir, 'snapshot.json')
    if refusal is not None:
        return refusal
    files = snapshot.get('files') if isinstance(snapshot, dict) else None
    if (not isinstance(files, dict) or not files or
            not all(isinstance(v, str) and SHA256.match(v) for v in files.values())):
        return 'malformed', 'snapshot.json files is not a non-empty map of file names to sha256 digests'
    board = record['board']
    # Review H1 (ee442j): the attempt record's board is untrusted. kicad-route grades a
    # board by a plain file name in the input directory; an empty, dot or separated name
    # ('.', '..', '/', 'sub/b', 'b/') is not that input, and is refused before any name
    # is derived from it (PurePosixPath('.').with_suffix raises).
    if board in ('.', '..') or PurePosixPath(board).name != board:
        return 'run_mismatch', (f'attempt.json board {board!r} is not a plain file name: kicad-route '
                                'refuses such an input')
    # The named input files (review H1, ee442g; agent-510 R1): kicad-route names the
    # board's own project file and custom rules file, digests from the reference copy,
    # which snapshot.json records. Which rules file is loaded, and which siblings are
    # not, is derived from the snapshot; the attempt record and the sidecar must both
    # state that one inventory. A name or digest of another input is not this execution's.
    rules_files = record.get('rules_files')
    if not isinstance(rules_files, dict) or not {'custom_rules', 'not_loaded'} <= set(rules_files):
        return 'run_mismatch', 'attempt.json rules_files does not state custom_rules and not_loaded'
    project_name, custom, not_loaded = _rules_inventory(files, board)
    if project_name not in files:
        return 'run_mismatch', f'the snapshot has no {project_name}: kicad-route refuses such an input'
    # agent-510 R2: the prepared input is observed in work/input, not the snapshot.
    prepared, unresolved = _prepared_input(record, board, observed)
    if unresolved is not None:
        return 'run_mismatch', unresolved
    if prepared is None:
        return 'run_mismatch', ('the adapter observed no regular prepared input board and project in work/input '
                                'for attempt.json reference_dir and board')
    checks = [
        ('input fingerprint', claimed['fingerprint'], [record['input_fingerprint'], inp.get('fingerprint'),
                                                       fingerprint(files)]),
        ('input snapshot files', claimed['snapshot_files'], [files, claimed['files']]),
        ('input board', [claimed['board']['name'], claimed['board']['sha256']],
         [[inp.get('board'), files.get(board)], [board, facts['facts/reference-facts.json'].get('sha256')]]),
        ('input project', [claimed['project']['name'], claimed['project']['sha256']],
         [[project_name, files.get(project_name)]]),
        ('input rules file', [rules_files.get('project'), rules_files['custom_rules'],
                              rules_files['not_loaded']], [[project_name, custom, not_loaded]]),
        ('input rules', [claimed['rules']['name'], claimed['rules']['sha256'], claimed['rules'].get('not_loaded')],
         [[custom, files.get(custom) if custom is not None else None, not_loaded]]),
        ('effective input', identity['effective']['input'], [prepared, config.get('input'), request.get('input')]),
        ('input source', claimed['source'], [record['source']])]
    settings = record['settings']
    requested = identity['request']
    checks += [(f'request {k}', requested.get(k, _UNSTATED), [settings.get(k, _UNSTATED)])
               for k in EXPERIMENT_REQUEST_SETTINGS]
    checks += [('request backend', canonical_backend(requested.get('backend')),
                [canonical_backend(settings.get('backend'))]),
               ('request experiment_policy_sha256', requested.get('experiment_policy_sha256'),
                [identity['policy']['declared_sha256']]),
               ('protection mode', identity['protection']['mode'], [settings.get('protect')]),
               ('backend budget', identity['budgets']['backend_s'], [settings.get('budget_s')])]
    validation = result.get('validation') if isinstance(result.get('validation'), dict) else {}
    binding = result['process']['effective_configuration']
    output = config.get('output') if isinstance(config.get('output'), dict) else {}
    sha = validation.get('validated_board_sha256')
    # The prepared input and three distinct output seals, each against its own stage
    # record (kicad-route a0ed434).
    checks += [('captured input board', prepared['board_sha256'], [binding.get('captured_input_sha256')]),
               ('raw backend output board', candidate['raw_backend_output_board_sha256'],
                [sealed.get(EXPERIMENT_RAW_OUTPUT), output.get('board_sha256'), binding.get('backend_output_sha256')]),
               ('raw backend output stage', EXPERIMENT_RAW_STAGE, [output.get('stage'), binding.get('output_stage')]),
               ('wrapper output board', [candidate['backend_output_board_sha256'], candidate['pre_refill_board_sha256']],
                [[binding.get('wrapper_output_board_sha256')] * 2, [validation.get('delivered_board_sha256')] * 2]),
               ('wrapper output stage', EXPERIMENT_WRAPPER_STAGE, [binding.get('wrapper_output_stage')]),
               ('graded refilled board', [candidate[k] for k in ('candidate_board_sha256', 'validated_board_sha256',
                                                                 'file_sha256')],
                [[sha, validation.get('candidate_board_sha256'), facts['validate/candidate-facts.json'].get('sha256')]
                 if isinstance(sha, str) else None])]
    statuses = {k: result[k].get('status') if isinstance(result.get(k), dict) else None
                for k in ('process', 'routing', 'validation')}
    checks.append(('outcome', {k: outcome[k] for k in statuses}, [statuses]))
    requested_output = config.get('requested_output')
    checks.append(('requested output',
                   posixpath.normpath(requested_output) if isinstance(requested_output, str) else None,
                   [str(run_dir / 'work/krt-out' / board)]))
    for label, claim, observed in checks:
        # A request setting may be null but must be stated; every other claim must be non-null.
        if (claim is None and not label.startswith('request ')) or any(
                v is _UNSTATED for v in (claim, *observed)) or any(
                canonical_json(claim) != canonical_json(o) for o in observed):
            return 'run_mismatch', f'the sidecar {label} is not this execution\'s'
    return None


def _ambiguous_binding(result_path):
    """Why result.json's experiment binding is ambiguous, or None.

    The permissive result.json read keeps the last of repeated keys, so the
    binding is re-read strictly: a repeated top-level experiment key, or a
    repeated key inside the reference, refuses the evidence."""
    marked = []

    def pairs_hook(pairs):
        found = {}
        for key, value in pairs:
            if key in found:
                marked.append((found, key))
            found[key] = value
        return found
    try:
        top = json.loads(result_path.read_bytes(), object_pairs_hook=pairs_hook)
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return 'result.json is not JSON'
    ref = top.get('experiment') if isinstance(top, dict) else None
    if any(obj is top and key == 'experiment' for obj, key in marked):
        return 'result.json has a duplicate key "experiment"'
    if any(obj is ref for obj, _ in marked):
        return 'experiment reference has a duplicate key'
    return None


def _policy_copy_refusal(path, declared):
    """(reason, detail) refusing the router's retained policy copy, or None.

    The copy is read like kicad_route.experiment_policy.load (duplicate keys
    refused, exactly the five routability-policy/1 fields), must declare the
    same policy and must be the bytes kicad-route's common.write_json writes for
    it (json.dumps(policy, indent=2) + newline, in the copy's own key order)."""
    try:
        kept = validate_experiment_policy(strict_json_file(path, EXPERIMENT_POLICY_COPY), EXPERIMENT_POLICY_COPY)
    except RequestError as exc:
        return 'malformed', str(exc)
    if declared is None or canonical_json(kept) != canonical_json(declared):
        return 'incompatible_policy', f'{EXPERIMENT_POLICY_COPY} differs from the declared policy'
    if path.read_bytes() != (json.dumps(kept, indent=2) + '\n').encode():
        return 'malformed', f'{EXPERIMENT_POLICY_COPY} is not the bytes kicad-route writes for the policy'
    return None


def policy_backend_args(policy):
    """KRT's own --layers/--layer-costs, as --backend-arg values. A board copper layer
    not listed is appended by KRT at FORBIDDEN cost: an obstacle with no track copper."""
    args = ['--layers', *policy['signal_layers']]
    if 'layer_costs' in policy:
        args += ['--layer-costs', *(repr(float(c)) for c in policy['layer_costs'])]
    return args


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def canonical_identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def tree(root):
    """Every entry under root without following links: files by sha256 and mode,
    links by target, directories and special files by kind."""
    root = Path(root)
    entries = {}
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f'not a directory: {root}')
    for current, dirs, names in os.walk(root, followlinks=False):
        base = Path(current)
        for name in sorted(dirs + names):
            path = base / name
            rel = path.relative_to(root).as_posix()
            st = path.lstat()
            if path.is_symlink():
                entries[rel] = ['link', os.readlink(path)]
            elif path.is_dir():
                entries[rel] = ['dir', oct(st.st_mode & 0o7777)]
            elif path.is_file():
                entries[rel] = ['file', digest(path), oct(st.st_mode & 0o7777)]
            else:
                entries[rel] = ['other', oct(st.st_mode)]
        dirs[:] = [d for d in dirs if not (base / d).is_symlink()]
    return dict(sorted(entries.items()))


def tree_identity(root):
    entries = tree(root)
    return {'sha256': canonical_identity(entries), 'entries': len(entries)}


def files_of(entries):
    return {rel: e[1] for rel, e in entries.items() if e[0] == 'file'}


def links_of(entries):
    return sorted(rel for rel, e in entries.items() if e[0] in ('link', 'other'))


def checkout_commit(checkout):
    """HEAD commit from the git files themselves; running git could write the index."""
    git = Path(checkout) / '.git'
    if git.is_symlink():
        raise OSError(f'checkout .git must not be a symlink: {git}')
    if git.is_file():
        raise NotADirectoryError(errno.ENOTDIR, 'checkout .git is a worktree file, not a directory', str(git))
    head = (git / 'HEAD').read_text().strip()
    if not head.startswith('ref: '):
        return head
    ref = head[5:]
    loose = git / ref
    if loose.is_file():
        return loose.read_text().strip()
    packed = git / 'packed-refs'
    if packed.is_file():
        for line in packed.read_text().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref:
                return parts[0]
    return None


def code_files(checkout):
    """(file map, problems): the SHA-256 of every file under the checkout's bin/, src/,
    and (on Rust-capable checkouts) tools/, rust-engine/src/, Cargo manifests, and LICENSE.

    Python bytecode caches and Cargo target/ are not code here, but a link or special
    file anywhere in these trees is a problem."""
    checkout = Path(checkout)
    problems, files = [], {}
    tops = ['bin', 'src']
    rust_capable = is_rust_capable_checkout(checkout)
    if rust_capable:
        tops.append('tools')
        if (checkout / 'rust-engine' / 'src').is_dir() or (checkout / 'rust-engine' / 'src').is_symlink():
            tops.append('rust-engine/src')
    for top in tops:
        if not (checkout / top).is_dir() or (checkout / top).is_symlink():
            problems.append(f'missing tool directory {top}/')
            continue
        for rel, entry in tree(checkout / top).items():
            parts = PurePosixPath(rel).parts
            if '__pycache__' in parts or 'target' in parts or any(p.endswith('.egg-info') for p in parts):
                if entry[0] in ('link', 'other'):
                    problems.append(f'link or special file in pycache: {top}/{rel}')
                continue
            if entry[0] == 'file':
                files[f'{top}/{rel}'] = entry[1]
            elif entry[0] != 'dir':
                problems.append(f'link or special file in tool code: {top}/{rel}')
    if rust_capable:
        for rel in ('rust-engine/Cargo.toml', 'rust-engine/Cargo.lock', 'LICENSE'):
            path = checkout / rel
            if path.is_symlink():
                problems.append(f'link or special file in tool code: {rel}')
            elif path.is_file():
                files[rel] = digest(path)
    return files, problems


def tree_status(checkout):
    """(clean, entries): whether git reports the checkout's tracked tree unchanged from HEAD
    with no untracked files that are not ignored. clean is None, with the reason as the one
    entry, when git cannot tell."""
    try:
        result = subprocess.run([*GIT_STATUS[:1], '-C', str(checkout), *GIT_STATUS[1:]], capture_output=True,
                                timeout=300, env=GIT_ENV, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, [f'{type(exc).__name__}: {exc}']
    if result.returncode:
        return None, [result.stderr.decode('utf-8', 'replace').strip()[-500:] or f'git exit {result.returncode}']
    entries = [e for e in result.stdout.decode('utf-8', 'replace').split('\0') if e]
    return not entries, entries


def checkout_record(checkout):
    """(record, problems): the router checkout as an attempt records it (CHECKOUT_KEYS).

    The commit is HEAD read from the git files; clean is git's view of the tree
    (tree_status); code_sha256 and code_files are the canonical identity and size of
    code_files(). There is no list of expected commits or digests: any commit runs and
    is recorded. A HEAD that is missing or not a commit, a dirty tree, a tree whose
    state git cannot report, and links in the code are problems that refuse the run."""
    checkout = Path(checkout)
    problems, commit = [], None
    try:
        commit = checkout_commit(checkout)
    except (OSError, UnicodeDecodeError) as exc:
        problems.append(f'cannot read the checkout HEAD: {exc}')
    else:
        if not isinstance(commit, str) or not COMMIT.match(commit):
            problems.append(f'checkout HEAD {commit!r} is not a commit')
            commit = None
    files, code_problems = code_files(checkout)
    problems += code_problems
    clean, entries = tree_status(checkout)
    if clean is None:
        problems.append('cannot tell whether the router checkout is clean: ' + '; '.join(entries))
    elif not clean:
        problems.append(f'router checkout has {len(entries)} uncommitted or untracked changes: '
                        + ', '.join(entries[:5]) + (' ...' if len(entries) > 5 else ''))
    record = {'commit': commit, 'clean': clean, 'code_sha256': canonical_identity(files) if files else None,
              'code_files': len(files)}
    return record, problems


def adapter_claim(commit):
    """report.adapter: the kicad-route commit the attempt recorded, or 'unknown' when HEAD was unreadable."""
    return {'compatibility': f'kicad-route@{commit if isinstance(commit, str) and COMMIT.match(commit) else "unknown"}'}


def preflight(checkout, state, backend, *, mounted_at):
    """The one precondition sequence of a router run, host-side and in the sandbox.

    The checkout is recorded (checkout_record) and must be a clean commit; Rust-capable
    checkouts qualify kicad-route-engine and tools/rust_engine_kicad.py without legacy
    .venv-krt / manifest.json unless an explicit separate state manifest is supplied."""
    checkout, state = Path(checkout), Path(state)
    record, problems = checkout_record(checkout)
    if is_rust_capable_checkout(checkout) and canonical_backend(backend) == 'builtin':
        bridge = checkout / 'tools' / 'rust_engine_kicad.py'
        if not bridge.is_file() or bridge.is_symlink():
            problems.append('missing tools/rust_engine_kicad.py')
        cargo_lock = checkout / 'rust-engine' / 'Cargo.lock'
        if not cargo_lock.is_file() or cargo_lock.is_symlink():
            problems.append('missing rust-engine/Cargo.lock')
        license_file = checkout / 'LICENSE'
        if not license_file.is_file() or license_file.is_symlink():
            problems.append('missing kicad-route LICENSE')
        engine_bin = resolve_kicad_route_engine(checkout)
        if engine_bin is None or not engine_bin.is_file() or not os.access(engine_bin, os.X_OK):
            problems.append('missing executable kicad-route-engine binary')
        try:
            separate_manifest = (state.resolve() != checkout.resolve() and (state / 'manifest.json').is_file())
        except OSError:
            separate_manifest = state != checkout and (state / 'manifest.json').is_file()
        if separate_manifest:
            if not problems:
                problems += verify_state(checkout, state, backend, mounted_at=mounted_at)
            generation, selection = state_generation(state)
            problems += selection
            for rel in state_required(backend, generation):
                if not os.path.lexists(state / rel):
                    problems.append(f'missing installed backend dependency: KICAD_ROUTE_STATE/{rel}')
        else:
            generation = None
        return record, generation, problems
    if not problems:
        problems += verify_state(checkout, state, backend, mounted_at=mounted_at)
    generation, selection = state_generation(state)
    problems += selection
    for rel in state_required(backend, generation):
        if not os.path.lexists(state / rel):
            problems.append(f'missing installed backend dependency: KICAD_ROUTE_STATE/{rel}')
    return record, generation, problems


def verify_state(checkout, state, backend='builtin', mounted_at=None):
    """Verify the owned engine using the checkout's own state-manifest reader.

    Called only for a clean checkout. A checkout without the owned engine source
    (src/kicad_route/engine.py: the coordinator's synthetic fixture router) has no
    installed engine to verify. No routing, installation or network is performed here.

    kicad-route binds a state manifest to the directory it describes
    (`state_dir`) and refuses to start anywhere else. mounted_at is where the
    routing sandbox presents this state (SANDBOX['checkout'] by default): a
    manifest recorded for any other directory would only fail inside the
    sandbox, so it is refused here first. The remaining checks read the files
    at `state`, wherever the caller sees them.

    Both layouts kicad-route installs qualify, and only when built from the
    checkout's own source: the legacy one (no generation; engine in the checkout,
    venv at .venv-krt, as the image's install.sh builds it) and a router generation
    (`kicad-route update`; STATE/.router-generations/<hex>/ holds a copy of the
    source and its venv). A generation's digest is compared with the checkout's
    SOURCE_ROOT, never with itself, so a stale generation is refused.
    """
    if canonical_backend(backend) != 'builtin' or not (Path(checkout) / 'src/kicad_route/engine.py').is_file():
        return []
    mounted_at = SANDBOX['checkout'] if mounted_at is None else str(mounted_at)
    script = r'''
import json, re, sys
from pathlib import Path
checkout, state, mounted_at = map(Path, sys.argv[1:4])
GENERATION = re.compile(sys.argv[4])
sys.path.insert(0, str(checkout / 'src'))
from kicad_route import common, engine, statemanifest
# statemanifest.read() also requires state_dir to equal the path this process
# sees, which differs between the host and the sandbox; mounted_at is checked instead.
m = common.load_json(statemanifest.manifest_path(state))
if not isinstance(m, dict):
    raise ValueError('missing or unreadable state manifest')
problems = statemanifest.schema_problems(m)
if not isinstance(m.get('state_dir'), str) or not m['state_dir'].strip():
    problems.append('state manifest has an invalid state_dir')
k = m.get('krt') or {}
# The installed engine must be built from the source in the verified checkout.
# Under a router generation both the manifest and kicad-route's engine.ROOT describe the
# generation's own copy, so the reference is always the checkout's SOURCE_ROOT: a
# generation built from any other commit (kicad-route doctor still passes it) differs.
checkout_source = engine.source_sha256(engine.SOURCE_ROOT)
generation = k.get('generation')
if generation is not None and not (isinstance(generation, str) and GENERATION.match(generation)):
    problems.append(f'state manifest selects an invalid router generation {generation!r}')
    generation = venv_path = None
else:
    venv_path = f'{generation}/venv' if generation else '.venv-krt'
if k.get('source_sha256') != checkout_source:
    problems.append('owned engine source digest differs' + (
        f'; router generation {generation} was built from commit {k.get("commit")}, not the checkout '
        f'source {checkout_source}: rerun `kicad-route update` at this checkout' if generation else ''))
module = (k.get('extension_module') or {}).get('path')
if venv_path is not None:
    venv = state / venv_path
    installed = statemanifest.extension_module(venv)
    if (not module or installed is None or
            not (state / module).resolve().is_relative_to(venv.resolve()) or
            (state / module).resolve() != installed.resolve()):
        problems.append(f'manifest must identify the installed {venv_path} grid_router extension')
if not (k.get('extension_module') or {}).get('sha256'):
    problems.append('missing installed engine digest')
if venv_path is not None and (k.get('venv') or {}).get('path') != venv_path:
    problems.append('unexpected routing environment path')
if generation:
    # The generation runs its own copy of the source: its recorded paths stay, so the
    # drift check below hashes that copy against the digest just bound to the checkout.
    if (k.get('source_path') != f'{generation}/router' or
            (k.get('requirements') or {}).get('path') != f'{generation}/router/requirements.txt'):
        problems.append('router generation source or requirements path is not the generation copy')
    record = common.load_json(state / generation / 'installation.json')
    if (not isinstance(record, dict) or not isinstance(record.get('source'), dict) or
            record['source'].get('source_sha256') != checkout_source or
            record.get('extension_sha256') != (k.get('extension_module') or {}).get('sha256')):
        problems.append('router generation installation record differs from the checkout source or module')
elif k:
    # The manifest was installed on the host. The immutable source is mounted at a
    # different absolute path inside the sandbox, and a separate state holds no
    # source at all; verify the recorded source hashes on the mounted checkout.
    k['requirements']['path'] = str(engine.SOURCE_ROOT / 'requirements.txt')
    k['source_path'] = str(engine.SOURCE_ROOT)
problems += [f"state manifest drift: {d['item']}" for d in statemanifest.verify(m, state)
             if not str(d.get('item', '')).startswith('freerouting.')]
print(json.dumps(problems))
'''
    try:
        # -B still reads caches. /dev/null cannot hold ignored bytecode.
        result = subprocess.run([PYTHON, '-I', *ROUTER_PYTHON_FLAGS,
                                 '-c', script, str(checkout), str(state), mounted_at, GENERATION.pattern],
                                capture_output=True, text=True, timeout=60,
                                env={'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1',
                                     'PYTHONPYCACHEPREFIX': NO_PYCACHE})
        if result.returncode:
            return ['installed engine verification failed: ' + result.stderr.strip()[-2000:]]
        problems = json.loads(result.stdout)
        if not isinstance(problems, list) or any(not isinstance(p, str) for p in problems):
            return ['invalid installed engine verification output']
        return problems
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return [f'installed engine verification unavailable: {exc}']


def _safe(value, label, suffix=None):
    if not isinstance(value, str) or not SAFE.match(value) or (suffix and not value.endswith(suffix)):
        raise RequestError(f'{label} must match {SAFE.pattern}' + (f' and end with {suffix}' if suffix else ''))
    return value


def _bounded(value, label, low, high):
    if not re.fullmatch(r'[0-9]{1,6}', value or '') or not low <= int(value) <= high:
        raise RequestError(f'{label} must be an integer from {low} to {high}')
    return int(value)


def parse_request(argv):
    """The coordinator-declared request. No argparse: its usage exit 2 would read as partial."""
    known = {'--project', '--board', '--schematic', '--name', '--label', '--budget', '--timeout',
             '--backend', '--state', '--diff-pairs', '--layer-policy', '--experiment-policy'}
    if len(argv) % 2:
        raise RequestError('arguments must be --option value pairs')
    raw = {}
    for flag, value in zip(argv[::2], argv[1::2]):
        if flag not in known or flag in raw:
            raise RequestError(f'unknown or repeated option {flag!r}')
        raw[flag] = value
    missing = sorted({'--project', '--board', '--schematic', '--name', '--label', '--budget', '--timeout'} - set(raw))
    if missing:
        raise RequestError(f'missing options: {", ".join(missing)}')
    project = raw['--project']
    parts = PurePosixPath(project).parts if isinstance(project, str) else ()
    if not parts or str(PurePosixPath(project)) != project or any(not SAFE.match(p) for p in parts):
        raise RequestError('project must be a normalized relative candidate directory')
    request = {
        'project': project,
        'board': _safe(raw['--board'], 'board', '.kicad_pcb'),
        'schematic': _safe(raw['--schematic'], 'schematic', '.kicad_sch'),
        'name': _safe(raw['--name'], 'name'),
        'label': _safe(raw['--label'], 'label'),
        'budget_s': _bounded(raw['--budget'], 'budget', 1, 86400),
        'timeout_s': _bounded(raw['--timeout'], 'timeout', 1, 86400),
        'backend': raw.get('--backend', 'builtin'),
        'state': raw.get('--state', 'checkout'),
    }
    if request['backend'] not in BACKENDS:
        raise RequestError(f'backend must be one of {BACKENDS}')
    if request['state'] not in ('checkout', 'separate'):
        raise RequestError('state must be checkout or separate')
    if '--diff-pairs' in raw:
        if raw['--diff-pairs'] != 'auto' or canonical_backend(request['backend']) != 'builtin':
            raise RequestError('diff-pairs must be auto and requires the builtin (krt) backend')
        request['diff_pairs'] = 'auto'
    if '--layer-policy' in raw:
        # kicad-route refuses --backend-arg with --diff-pairs; freerouting has no KRT layer costs.
        if canonical_backend(request['backend']) != 'builtin' or '--diff-pairs' in raw:
            raise RequestError('layer-policy requires the builtin (krt) backend without diff-pairs')
        request['layer_policy'] = parse_layer_policy(raw['--layer-policy'])
    if '--experiment-policy' in raw:
        request['experiment_policy'] = parse_experiment_policy(raw['--experiment-policy'])
    if request['timeout_s'] <= request['budget_s']:
        raise RequestError('timeout must exceed the router budget')
    return request


def staged_root(layout):
    """Where the adapter stages the verified input tree the router reads (AGENTEE-442)."""
    return Path(layout['work']) / 'routing' / ORIGINAL_STAGE


def staged_dir(request, layout):
    """The --dir the router is given: the staged copy of /candidate/<project>."""
    return staged_root(layout) / request['project']


def stage_original(candidate_root, inputs_tree, root):
    """Copy the verified input tree to root, a real copy with the same modes (never a
    link), and return the tree observed there. inputs_tree holds no links or special
    files: the adapter refuses those inputs before staging."""
    root.mkdir()
    for rel, entry in inputs_tree.items():
        if entry[0] == 'dir':
            (root / rel).mkdir()
        else:
            shutil.copyfile(Path(candidate_root) / rel, root / rel)
            os.chmod(root / rel, int(entry[2], 8))
    # Directory modes last: a read-only input directory still receives its files.
    for rel, entry in reversed(list(inputs_tree.items())):
        if entry[0] == 'dir':
            os.chmod(root / rel, int(entry[1], 8))
    return tree(root)


def staged_record(request, layout, staged):
    """report.inputs.staged: the staged path and the per-file manifest and tree identity
    of the copy, to equal report.inputs (the verified /candidate input)."""
    return {'path': str(staged_dir(request, layout)), 'root': str(staged_root(layout)),
            'files': files_of(staged), 'sha256': canonical_identity(staged)}


def staged_observation(layout):
    """report.inputs.staged_after: the staged tree observed after the router ran, sealed so
    that verify binds the retained tree to it even for a contradiction (review H3). A tree
    that is gone is 'missing', one that cannot be read 'unreadable'; neither is a pass."""
    root = staged_root(layout)
    if not os.path.lexists(root):
        return {'status': 'missing', 'files': None, 'sha256': None}
    try:
        entries = tree(root)
    except (OSError, ValueError):
        return {'status': 'unreadable', 'files': None, 'sha256': None}
    return {'status': 'observed', 'files': files_of(entries), 'sha256': canonical_identity(entries)}


def default_layout(request):
    checkout = Path(SANDBOX['checkout'])
    state = checkout if request['state'] == 'checkout' else Path(SANDBOX['state'])
    engine = Path(SANDBOX['engine'])
    if not engine.is_file():
        engine = resolve_kicad_route_engine(checkout)
    return {'work': Path(SANDBOX['work']), 'candidate': Path(SANDBOX['candidate']), 'checkout': checkout,
            'state': state, 'engine': engine, 'probe': None}


def is_native_request(request, checkout):
    return is_rust_capable_checkout(checkout) and canonical_backend(request['backend']) == 'builtin'


def native_unsupported_problems(request):
    """Fail-closed refusals for options unsupported by route-native (zero Python KRT fallback)."""
    problems = []
    if 'layer_policy' in request:
        problems.append('route-native does not support layer_policy')
    if 'experiment_policy' in request:
        problems.append('route-native does not support experiment_policy')
    return problems


def native_tool_argv(request, layout):
    routing = Path(layout['work']) / 'routing'
    staged = staged_dir(request, layout)
    source_board = staged / request['board']
    output_board = routing / 'out' / 'native' / request['board']
    op_result = routing / 'attempt' / NATIVE_OPERATION_FILE
    op_request = routing / 'attempt' / NATIVE_REQUEST_FILE
    bridge = Path(layout['checkout']) / 'tools' / 'rust_engine_kicad.py'
    engine = layout.get('engine')
    if engine is None:
        engine = resolve_kicad_route_engine(layout['checkout'])
    engine_str = str(engine) if engine is not None else 'kicad-route-engine'
    return [
        engine_str, 'route-native', str(source_board), str(output_board),
        '--operation-result', str(op_result),
        '--save-request', str(op_request),
        '--native-bridge', str(bridge),
        '--python', PYTHON,
        '--timeout-ms', str(int(request['budget_s']) * 1000),
        *(['--diff-pairs', 'auto'] if request.get('diff_pairs') == 'auto' else []),
    ]


def tool_argv(request, layout):
    if is_native_request(request, layout['checkout']):
        return native_tool_argv(request, layout)
    routing = Path(layout['work']) / 'routing'
    return [PYTHON, '-B', '-X', f'pycache_prefix={routing / "pycache"}',
            str(Path(layout['checkout']) / 'bin' / 'kicad-route'), 'run',
            '--out', str(routing / 'out'), '--json', '--backend', CLI_BACKEND_NAMES[request['backend']],
            '--dir', str(staged_dir(request, layout)),
            '--board', request['board'], '--schematic', request['schematic'],
            '--name', request['name'], '--label', request['label'],
            '--protect', 'existing', '--budget', str(request['budget_s']),
            *(['--diff-pairs', 'auto'] if request.get('diff_pairs') == 'auto' else []),
            *policy_argv(request.get('layer_policy')),
            *(['--experiment-policy', str(routing / 'attempt' / EXPERIMENT_POLICY_FILE)]
              if 'experiment_policy' in request else [])]


def policy_argv(policy):
    if policy is None:
        return []
    return (['--pour-area-loss', repr(float(policy['pour_area_loss_pct']))] +
            [f'--backend-arg={arg}' for arg in policy_backend_args(policy)])


def tool_env(layout):
    work, routing = Path(layout['work']), Path(layout['work']) / 'routing'
    home = routing / 'home'
    return {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
            'TMPDIR': str(routing / 'tmp'), 'XDG_CONFIG_HOME': str(home / '.config'),
            'XDG_CACHE_HOME': str(home / '.cache'), 'XDG_DATA_HOME': str(home / '.local/share'),
            'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPYCACHEPREFIX': str(routing / 'pycache'),
            'PYTHONNOUSERSITE': '1', 'KICAD_ROUTE_STATE': str(layout['state']),
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0',
            'GIT_CEILING_DIRECTORIES': f'{layout["candidate"]}:{staged_root(layout)}:{work}'}


def expected_settings(request, rule_floor, pairs=None, reported=None):
    """The adapter-fixed settings a run of this request must record."""
    # The tool echoes the --backend it was given; a reported alias of the requested backend is that echo.
    backend = reported if canonical_backend(reported) == canonical_backend(request['backend']) else request['backend']
    settings = {'backend': backend, 'budget_s': request['budget_s'], 'protect': 'existing',
            'max_iterations': None, 'passes': None, 'default_clearance_mm': None,
            'clearance_margin_mm': None, 'pour_area_loss_pct': None, 'pour_min_spokes': None,
            'backend_args': [], 'backend_env': {}, 'reserve_spokes': None, 'barrier': None, 'stall_s': None,
            'mode': 'all', 'rule_floor_sha256': rule_floor}
    if request.get('diff_pairs') == 'auto':
        settings.update(diff_pairs='auto', pairs=pairs)
    if request.get('layer_policy') is not None:
        policy = request['layer_policy']
        settings.update(pour_area_loss_pct=float(policy['pour_area_loss_pct']),
                        backend_args=policy_backend_args(policy))
    if request.get('experiment_policy') is not None:
        # KICADROUTE-91 (a0ed434 onward) records the declaration it loaded in the run
        # settings as common.sha256_of(policy): a different policy is a different attempt.
        settings['experiment_policy_sha256'] = experiment_policy_identity(request['experiment_policy'])['policy_sha256']
    return settings


def settings_match(request, settings, floor, pairs=None, reported=None):
    """Whether `settings` matches the adapter's fixed request flags.

    For `builtin`, the router may also record its own derived search choices
    (for example `grid_basis`, `grid_step_mm`, `recipe`, or `reserve_spokes: 0`)
    without the adapter duplicating the router's internal heuristics."""
    if not isinstance(settings, dict) or not (floor is None or isinstance(floor, str)):
        return False
    fixed = expected_settings(request, floor, pairs, reported)
    if canonical_backend(request['backend']) != 'builtin':
        return settings == fixed
    forbidden = {'net_patterns', 'netclasses'}
    if request.get('diff_pairs') != 'auto':
        forbidden |= {'diff_pairs', 'pairs'}
    if request.get('experiment_policy') is None:
        forbidden.add('experiment_policy_sha256')
    if forbidden & settings.keys():
        return False
    for key, value in fixed.items():
        if key == 'reserve_spokes':
            if key not in settings or settings[key] not in (None, 0):
                return False
        elif key not in settings or settings[key] != value:
            return False
    return True


def write_probe(targets):
    """Try to create a file in each directory. Each must be refused by the OS."""
    records = []
    for target in targets:
        path = Path(target) / f'.agent-ee-write-probe-{os.getpid()}-{secrets.token_hex(8)}'
        record = {'directory': str(target)}
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except OSError as exc:
            record.update(refused=True, errno=errno.errorcode.get(exc.errno, str(exc.errno)))
        else:
            os.close(fd)
            os.unlink(path)  # our own probe file; the state must not keep it
            record.update(refused=False, errno=None)
        records.append(record)
    return records


def missing_reports(retained):
    """EXPECTED_REPORTS absent from retained, whose keys are relative to the attempt directory."""
    return [n for n in EXPECTED_REPORTS if not any(k == n or k.startswith(n + '/') for k in retained)]


def fingerprint(files):
    """kicad-route's input fingerprint over a reference snapshot's file hashes."""
    return hashlib.sha256(''.join(f'{n}\0{d}\n' for n, d in sorted(files.items())).encode()).hexdigest()


def backend_of(result):
    """Backend identity copied verbatim from a parsed result.json; nothing is filled in."""
    if isinstance(result, dict) and isinstance(result.get('backend'), dict):
        return {'status': 'reported', 'value': result['backend'], 'reason': None}
    return dict(NO_BACKEND)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def recomputed_exit(result):
    if result['process']['status'] != 'PASS':
        return 1
    if result['validation']['status'] != 'PASS':
        return 3
    if result['routing']['status'] != 'COMPLETE':
        return 2
    return 0


def validated_candidate(validation, sha):
    """Bind the post-refill candidate to DRC while recording the pre-refill hash."""
    delivered = validation.get('delivered_board_sha256')
    return (isinstance(delivered, str) and re.fullmatch(r'[0-9a-f]{64}', delivered) is not None and
            validation.get('candidate_board_sha256') == sha and
            validation.get('validated_board_sha256') == sha and
            validation.get('refill_modified_board') is (delivered != sha))


def refused_output(stdout):
    """Only a known machine refusal can classify exit 4 as a refusal."""
    try:
        record = json.loads(stdout)
    except (ValueError, RecursionError):
        # ValueError includes an integer beyond the interpreter's digit limit.
        return False
    return (isinstance(record, dict) and record.get('schema') == 'kicad-route/refusal/1' and
            record.get('kind') == 'refusal' and _is_int(record.get('exit_code')) and
            record['exit_code'] == 4 and record.get('command') == 'run' and
            record.get('reason_class') in ('usage', 'input', 'ledger', 'canary', 'state') and
            isinstance(record.get('message'), str) and bool(record['message']))


def semantics(exit_code, result, attempt, refused_line, discovered, request):
    """Classify one finished attempt from its observed exit and reports alone.

    exit_code is the process exit; result/attempt are the parsed JSON objects
    (None when absent); discovered says whether exactly one attempt directory
    exists. The numeric exit is never trusted alone: each exit needs its exact
    status combination, and anything else is a contradiction."""
    reasons = []
    if not _is_int(exit_code):
        return 'contradiction', ['router did not exit normally']
    if result is None:
        if exit_code == 4 and not discovered and refused_line:
            return 'refused', ['refused before routing; the router removed its run directory']
        if exit_code == 1:
            return 'backend_failure', ['router failed before writing result.json']
        if exit_code == 2:
            return 'contradiction', ['exit 2 without result.json is a usage error, never a partial route']
        return 'contradiction', [f'exit {exit_code} without result.json']
    if exit_code == 4:
        return 'contradiction', ['refusal exit with a result.json']
    paired = request.get('diff_pairs') == 'auto'
    problems = result_problems(result, exit_code, backend=request['backend'], paired=paired)
    if problems:
        return 'contradiction', problems
    backend = result['backend']
    if not isinstance(backend, dict) or canonical_backend(backend.get('name')) != canonical_backend(request['backend']):
        return 'contradiction', ['result backend differs from the requested backend']
    inp = result['input']
    if not isinstance(inp, dict) or inp.get('board') != request['board'] or inp.get('project') != request['name']:
        return 'contradiction', ['result input differs from the requested board or name']
    settings = result['settings']
    floor = settings.get('rule_floor_sha256') if isinstance(settings, dict) else None
    pairs = settings.get('pairs') if isinstance(settings, dict) and paired else None
    reported = settings.get('backend') if isinstance(settings, dict) else None
    if not settings_match(request, settings, floor, pairs, reported):
        return 'contradiction', ['result settings differ from the fixed adapter settings']
    if not discovered:
        return 'contradiction', ['result.json without exactly one attempt directory']
    declared = request.get('experiment_policy')
    if not isinstance(attempt, dict) or set(attempt) != ATTEMPT_KEYS | ({'pairs', 'pair_routing'} if paired else set()) | (
            {'experiment_policy'} if declared is not None else set()):
        return 'contradiction', ['attempt.json missing or shaped differently from the result contract']
    # KICADROUTE-91 (a0ed434 onward) records the declaration it loaded in attempt.json.
    if declared is not None:
        try:
            recorded = canonical_json(attempt['experiment_policy'])
        except (TypeError, ValueError):
            recorded = None
        if recorded != canonical_json(declared):
            return 'contradiction', ['attempt.json experiment_policy differs from the declared policy']
    if attempt['schema'] != f'kicad-route/attempt/{2 if paired else 1}':
        return 'contradiction', ['unknown attempt schema version']
    if paired and not pair_binding(pairs, attempt, result):
        return 'contradiction', ['pair evidence differs from the declared pair plan']
    dirname = PurePosixPath(request['project']).name
    if (attempt['board'] != request['board'] or attempt['schematic'] != request['schematic'] or
            canonical_backend(attempt['backend']) != canonical_backend(request['backend']) or attempt['project'] != request['name'] or
            attempt['reference_dir'] != f'reference/{dirname}' or
            attempt['candidate_dir'] != f'candidate/{dirname}' or
            attempt['input_fingerprint'] != inp.get('fingerprint') or attempt['settings'] != settings):
        return 'contradiction', ['attempt.json contradicts result.json or the request']
    _, label, reasons = verdict(result)
    return label, reasons


def native_backend_of(op_result):
    if not isinstance(op_result, dict) or op_result.get('schema') != NATIVE_OPERATION_SCHEMA:
        return {'status': 'unknown', 'value': None, 'reason': f'no {NATIVE_OPERATION_FILE}'}
    binary = op_result.get('binary')
    if not isinstance(binary, dict):
        return {'status': 'unknown', 'value': None, 'reason': f'missing binary identity in {NATIVE_OPERATION_FILE}'}
    return {'status': 'reported', 'value': dict(binary), 'reason': None}


def native_semantics(exit_code, op_result, saved_request_path, source_board, output_board=None):
    """Classify a route-native invocation using kicad-route/native-operation/1 and exact exits."""
    if not _is_int(exit_code):
        return 'contradiction', ['route-native did not exit normally']
    if op_result is None:
        if exit_code == 7:
            return 'backend_failure', [f'route-native failed (exit 7) before writing {NATIVE_OPERATION_FILE}']
        return 'contradiction', [f'exit {exit_code} without {NATIVE_OPERATION_FILE}']
    if (not isinstance(op_result, dict) or op_result.get('schema') != NATIVE_OPERATION_SCHEMA
            or not _is_int(op_result.get('version')) or op_result.get('version') != 1):
        return 'contradiction', ['unknown native operation schema or version']
    if (exit_code not in NATIVE_EXIT_STATUSES or not _is_int(op_result.get('exit_code'))
            or op_result.get('exit_code') != exit_code):
        return 'contradiction', [f'native operation exit_code {op_result.get("exit_code")!r} disagrees with process exit {exit_code}']
    status = op_result.get('status')
    if status not in NATIVE_EXIT_STATUSES[exit_code]:
        return 'contradiction', [f'native operation status {status!r} invalid for exit {exit_code}']
    req_block = op_result.get('request')
    req_sha = req_block.get('request_sha256') if isinstance(req_block, dict) else None
    routing_block = op_result.get('routing')
    source_block = op_result.get('source')
    pub = op_result.get('publication')
    if not isinstance(req_block, dict) or not isinstance(source_block, dict) or not isinstance(pub, dict):
        return 'contradiction', ['native operation missing request, source or publication block']
    if saved_request_path is not None and Path(saved_request_path).is_file() and not Path(saved_request_path).is_symlink():
        if not isinstance(req_sha, str) or not SHA256.match(req_sha) or digest(saved_request_path) != req_sha:
            return 'contradiction', ['saved native request digest differs from operation-result request_sha256']
    elif exit_code == 0:
        return 'contradiction', ['missing saved native request file']
    inline_req = req_block.get('request_json')
    if inline_req is not None:
        if not isinstance(inline_req, str):
            return 'contradiction', ['inline native request_json must be a string']
        inline_sha = hashlib.sha256(inline_req.encode('utf-8')).hexdigest()
        if not isinstance(req_sha, str) or not SHA256.match(req_sha) or inline_sha != req_sha:
            return 'contradiction', ['inline native request_json digest differs from operation-result request_sha256']
    if routing_block is not None:
        if not isinstance(routing_block, dict) or routing_block.get('request_sha256') != req_sha:
            return 'contradiction', ['native routing.request_sha256 differs from request.request_sha256']
    if source_board is not None and Path(source_board).is_file() and not Path(source_board).is_symlink():
        src_sha = source_block.get('source_sha256')
        if src_sha is None:
            phases = op_result.get('phases')
            pre_admission_only = (
                isinstance(phases, list) and len(phases) == 1
                and isinstance(phases[0], dict)
                and phases[0].get('phase') == 'pre_snapshot_admission'
                and phases[0].get('status') == 'failed'
                and source_block.get('source_bytes') is None
                and source_block.get('snapshot_sha256') is None
                and req_block.get('request_sha256') is None
                and routing_block is None
                and pub.get('published') is False
            )
            if exit_code == 0 or (
                    status not in ('destination_exists', 'publication_refused', 'usage_refused', 'invalid', 'unsupported')
                    and not (status == 'admission_exceeded' and pre_admission_only)):
                return 'contradiction', ['native source_sha256 differs from staged source board']
        elif src_sha != digest(source_board):
            return 'contradiction', ['native source_sha256 differs from staged source board']
    sidecar = source_block.get('sidecar_check')
    if not isinstance(sidecar, dict) or not isinstance(sidecar.get('passed'), bool):
        return 'contradiction', ['native source.sidecar_check missing or malformed']
    reason = op_result.get('reason')
    msg = [reason if isinstance(reason, str) and reason else f'route-native {status}']
    if exit_code == 0:
        if not sidecar.get('passed'):
            return 'contradiction', ['route-native exited 0 with failed sidecar_check']
        if not isinstance(routing_block, dict):
            return 'contradiction', ['route-native exited 0 without routing summary']
        out_sha = pub.get('output_sha256')
        if (pub.get('published') is not True or pub.get('ambiguous_termination') is not False
                or not isinstance(out_sha, str) or not SHA256.match(out_sha)):
            return 'contradiction', ['route-native exited 0 without confirmed publication']
        if output_board is not None:
            out_path = Path(output_board)
            if out_path.is_symlink() or not out_path.is_file() or digest(out_path) != out_sha:
                return 'contradiction', ['route-native output board missing, linked or digest differs from publication']
        return 'complete', msg
    if exit_code == 2:
        return ('validation_failed' if status == 'native_validation_failed' else 'refused'), msg
    if exit_code in (3, 4, 5):
        return 'backend_failure', msg
    if exit_code == 6:
        if status == 'operation_timeout':
            return 'timeout', msg
        if status == 'cancelled':
            return 'cancelled', msg
        return 'backend_failure', msg
    if exit_code == 7:
        return 'backend_failure', msg
    # exit_code == 8
    return ('contradiction' if status == 'ambiguous_termination' else 'refused'), msg


def result_problems(result, exit_code, *, backend, paired):
    """What makes a parsed result.json differ from the result contract, in contract order.

    The request-independent half of every route's classification: schema version,
    key set, copper ownership and runtime shapes, and statuses that agree with the
    observed process exit. Empty when the result qualifies for grading."""
    version = 2 if paired else 1
    if isinstance(result, dict) and result.get('schema') != f'kicad-route/route/{version}':
        return ['unknown result schema version']
    custody = (canonical_backend(backend) == 'builtin' and isinstance(result, dict) and
               isinstance(result.get('process'), dict) and result['process'].get('status') == 'PASS')
    # A K91 experiment reference is optional; experiment_grade retains or refuses it.
    if (not isinstance(result, dict) or
            set(result) - {'experiment'} !=
            RESULT_KEYS | ({'pairs'} if paired else set()) | ({'copper_ownership'} if custody else set())):
        return ['result.json shape differs from the result contract']
    if custody and not copper_ownership_shape(result['copper_ownership']):
        return ['copper_ownership differs from the result contract']
    if not measurement_shape(result):
        return ['process.measurement_qualification differs from the result contract']
    if canonical_backend(backend) == 'builtin' and not runtime_shape(result.get('backend')):
        return ['backend.runtime differs from the result contract']
    if (canonical_backend(backend) != 'builtin' and isinstance(result.get('backend'), dict) and
            'runtime' in result['backend']):
        return ['backend.runtime is reported by a backend that has none']
    if result['kind'] != 'route' or not _is_int(result['exit_code']) or result['exit_code'] != exit_code:
        return ['result.json kind or exit_code contradicts the process exit']
    statuses = [(result.get(k), k, allowed) for k, allowed in
                (('process', PROCESS), ('routing', ROUTING), ('validation', VALIDATION))]
    for value, key, allowed in statuses:
        if not isinstance(value, dict) or value.get('status') not in allowed:
            return [f'unknown {key} status']
    if recomputed_exit(result) != exit_code:
        return ['statuses imply a different exit than the one observed']
    return []


OUTCOMES = ('accepted', 'policy_failed', 'refused')


def classify(data, exit_code, *, backend='builtin', paired=None):
    """The one classification of a router run's result.json bytes, for every route.

    Returns (outcome, reasons): 'refused' when the bytes do not qualify as kicad-route's
    result for this exit (unparseable, unknown schema or shape, contradictory
    statuses), 'accepted' when the process passed, routing is COMPLETE with nothing
    unconnected and validation passed, and 'policy_failed' for every other well-formed
    result. paired is whether --diff-pairs auto was requested; None takes the
    version the result declares. A request-bound route (semantics) checks more."""
    try:
        if len(data) > JSON_LIMIT:
            raise ValueError('result.json exceeds the size limit')
        result = json.loads(data)
    except (TypeError, ValueError, UnicodeDecodeError, RecursionError) as exc:
        return 'refused', [f'result.json is not readable JSON: {exc}']
    if not _is_int(exit_code):
        return 'refused', ['router did not exit normally']
    if paired is None:
        paired = isinstance(result, dict) and result.get('schema') == 'kicad-route/route/2'
    problems = result_problems(result, exit_code, backend=backend, paired=paired)
    if problems:
        return 'refused', problems
    outcome, _, reasons = verdict(result)
    return outcome, reasons


def verdict(result):
    """The one decision over a result that passed result_problems: (outcome, label, reasons).

    outcome is classify's accepted / policy_failed / refused. label is the sandbox report's
    finer class, a projection of the same decision: accepted is complete; policy_failed is
    backend_failure, validation_failed, incomplete or (COMPLETE with anything unconnected)
    policy_failed; refused is contradiction."""
    process, routing, validation = (result[k]['status'] for k in ('process', 'routing', 'validation'))
    candidate = result['candidate']
    if process == 'FAIL':
        if routing != 'NOT_RUN' or validation != 'NOT_RUN' or candidate is not None:
            return 'refused', 'contradiction', ['process failure with a candidate or graded statuses']
        reasons = result['process'].get('reasons')
        return 'policy_failed', 'backend_failure', (list(reasons) if isinstance(reasons, list) and reasons
                                                    else ['process FAIL'])
    if not isinstance(candidate, dict) or set(candidate) != {'dir', 'board'}:
        return 'refused', 'contradiction', ['process PASS without a candidate record']
    if validation != 'PASS':
        reasons = result['validation'].get('reasons')
        return 'policy_failed', 'validation_failed', list(reasons) if isinstance(reasons, list) else ['validation FAIL']
    if routing == 'NOT_RUN':
        return 'refused', 'contradiction', ['validation PASS with routing NOT_RUN']
    if routing == 'INCOMPLETE':
        return 'policy_failed', 'incomplete', [f"{result['routing'].get('unconnected_in_scope')} unconnected in scope"]
    unconnected = result['routing'].get('unconnected_total')
    if unconnected != 0:
        # The router reports COMPLETE yet leaves connections open: no proposal on any route.
        return 'policy_failed', 'policy_failed', [f'routing COMPLETE with {unconnected} unconnected']
    return 'accepted', 'complete', []


def terminal_problems(result):
    """An eligible attempt must carry kicad-route's native terminal preservation (KICADROUTE-98).

    Every originally joined pad group, including subsets of incomplete nets, must stay
    joined; kicad-route reports that as validation.net_completion from native terminal
    partitions, not from the capped DRC list. Missing or failing evidence, a split group,
    or a COMPLETE route whose native terminals are still split is never eligible."""
    validation = result.get('validation') if isinstance(result, dict) else None
    completion = validation.get('net_completion') if isinstance(validation, dict) else None
    native = validation.get('native_terminal_evidence') if isinstance(validation, dict) else None
    if (not isinstance(completion, dict) or completion.get('status') != 'PASS' or
            completion.get('split_components') != [] or completion.get('nets_lost') != []):
        return ['native terminal preservation (net_completion) is missing or did not pass']
    metrics = native.get('metrics') if isinstance(native, dict) else None
    candidate = metrics.get('candidate') if isinstance(metrics, dict) else None
    if (not isinstance(candidate, dict) or not isinstance(metrics.get('reference'), dict) or
            any(not _is_int(candidate.get(k)) or candidate[k] < 0
                for k in ('terminal_nets', 'incomplete_nets', 'component_deficit')) or
            any(not isinstance(native.get(k), str) or not native[k] for k in ('reference', 'candidate'))):
        return ['native terminal evidence is missing or malformed']
    if result['routing'].get('status') == 'COMPLETE' and (candidate['incomplete_nets'] or candidate['component_deficit']):
        return ['routing COMPLETE while native terminals remain split']
    return []


def measurement_shape(result):
    """process.measurement_qualification as kicad-route writes it, agreeing with the top-level measurement."""
    process = result.get('process')
    value = process.get('measurement_qualification') if isinstance(process, dict) else None
    return (isinstance(value, dict) and set(value) == MEASUREMENT_QUALIFICATION and
            isinstance(value['measurement'], bool) and value['measurement'] is result.get('measurement') and
            (value['build_reproducible'] is None or type(value['build_reproducible']) is bool) and isinstance(value['reasons'], list) and
            all(isinstance(r, str) and r for r in value['reasons']))


def runtime_shape(backend):
    """backend.runtime of an owned-engine result: exactly source, venv and generation paths."""
    runtime = backend.get('runtime') if isinstance(backend, dict) else None
    return (isinstance(runtime, dict) and set(runtime) == RUNTIME_KEYS and
            all(isinstance(runtime[k], str) and runtime[k].startswith('/') for k in ('source', 'venv')) and
            (runtime['generation'] is None or
             (isinstance(runtime['generation'], str) and runtime['generation'].startswith('/'))))


def runtime_problems(request, layout, generation, result):
    """The engine an owned-engine attempt reports it ran must be the one verified before it ran."""
    if canonical_backend(request['backend']) != 'builtin' or not isinstance(result, dict):
        return []
    runtime = (result.get('backend') or {}).get('runtime') if isinstance(result.get('backend'), dict) else None
    if runtime != runtime_paths(layout, generation):
        return ['the router ran an engine other than the verified '
                + (f'router generation {generation}' if generation else 'checkout engine')]
    return []


def copper_ownership_shape(value):
    """kicad-route's copper-custody pointer: bound to one retained record, or unavailable with reasons."""
    if not isinstance(value, dict) or value.get('replay_authority') is not False:
        return False
    if value.get('status') == 'bound':
        return (set(value) == COPPER_OWNERSHIP_BOUND and value['record'] == COPPER_OWNERSHIP_RECORD and
                isinstance(value['sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', value['sha256']) is not None and
                value['coverage'] == 'current_run_only_input_fixed')
    return (value.get('status') == 'unavailable' and set(value) == COPPER_OWNERSHIP_UNAVAILABLE and
            isinstance(value['reasons'], list) and bool(value['reasons']) and
            all(isinstance(r, str) and r for r in value['reasons']))


def pair_binding(plans, record, result):
    """Bind opt-in pair reports to the same plan; native geometry is not graded here."""
    keys = {'p', 'n', 'netclass', 'width_mm', 'gap_mm'}
    if not isinstance(plans, list) or not isinstance(record['pairs'], list):
        return False
    if len(plans) != len(record['pairs']) or not isinstance(record['pair_routing'], dict):
        return False
    members = set()
    for plan, pair in zip(plans, record['pairs']):
        if not isinstance(plan, dict) or set(plan) != keys or not isinstance(pair, dict):
            return False
        if set(pair) not in (keys, keys | {'initially_connected'}):
            return False
        if {k: pair[k] for k in keys} != plan:
            return False
        if 'initially_connected' in pair and not isinstance(pair['initially_connected'], bool):
            return False
        if any(not isinstance(plan[k], str) or not plan[k] for k in ('p', 'n', 'netclass')):
            return False
        if plan['p'] == plan['n'] or members & {plan['p'], plan['n']}:
            return False
        members.update((plan['p'], plan['n']))
        if any(type(plan[k]) not in (int, float) or not 0 < plan[k] < float('inf')
               for k in ('width_mm', 'gap_mm')):
            return False
    if not set(record['pair_routing']) <= {p['p'] for p in plans}:
        return False
    reports = result['pairs']
    if not isinstance(reports, list) or len(reports) != len(plans):
        return False
    for plan, report in zip(plans, reports):
        if not isinstance(report, dict) or any(report.get(k) != v for k, v in plan.items()):
            return False
    return True


def pair_coverage(request, result, record, classification):
    """Describe retained pair evidence, never replace fresh board acceptance checks.

    In particular routing completeness cannot imply coupled routing, and preserved
    input connectivity cannot claim this invocation created a differential pair.
    """
    limitations = ['Pair reports do not qualify length/skew limits or impedance; fresh board checks are required.']
    coverage = {'status': 'unknown', 'pairs': [], 'limitations': limitations, 'acceptance_ready': False}
    if request.get('diff_pairs') != 'auto':
        scope = record.get('scope', {}) if isinstance(record, dict) else {}
        nets = scope.get('nets', []) if isinstance(scope, dict) else []
        if (isinstance(nets, list) and all(isinstance(n, str) for n in nets) and
                any(n.rsplit('/', 1)[-1] == 'CAN+' and n[:-1] + '-' in nets for n in nets)):
            completed = (classification in ('complete', 'incomplete') or
                         (classification == 'validation_failed' and isinstance(result, dict) and
                          result.get('routing', {}).get('status') == 'COMPLETE' and
                          result.get('process', {}).get('status') == 'PASS'))
            limitations.append('CAN pair routed single-ended' if completed else
                                'CAN pair coupling unavailable; single-ended attempt did not complete routing')
            if classification not in ('complete', 'incomplete'):
                limitations.append('Single-ended attempt did not produce an eligible board')
        else:
            limitations.append('Differential pair routing was not requested; pair geometry is unqualified')
        return coverage
    if (classification not in ('complete', 'incomplete') or not isinstance(result, dict) or
            not isinstance(record, dict) or not pair_binding(result.get('settings', {}).get('pairs'), record, result)):
        limitations.append('Requested differential pair capability or eligible native pair evidence is unavailable')
        return coverage
    reports = result['pairs']
    if not reports:
        limitations.append('No differential pairs were detected; no pair geometry is qualified')
        return coverage
    for pair in reports:
        measured = all(type(pair.get(k)) in (int, float) and 0 <= pair[k] < float('inf')
                       for k in ('length_p', 'length_n', 'skew'))
        measured = measured and abs(abs(pair['length_p'] - pair['length_n']) - pair['skew']) <= .000002
        coupled = (pair.get('backend_status') == 'COUPLED' and pair.get('status') == 'ROUTED' and
                   pair.get('routed') is True and type(pair.get('gap_violations')) is int and
                   pair['gap_violations'] == 0 and measured)
        coverage['pairs'].append({'p': pair['p'], 'n': pair['n'],
            'status': 'reported_coupled' if coupled else 'unknown'})
        if not coupled:
            limitations.append(f"{pair['p']}/{pair['n']}: coupled geometry is unqualified "
                               f"(backend status {pair.get('backend_status')})")
    if all(p['status'] == 'reported_coupled' for p in coverage['pairs']):
        coverage['status'] = 'reported_coupled'
    return coverage


# Run with KiCad's Python on the graded board: the reference net's native fill on
# each reference layer must cover every RF track. Only the clearance the zone
# filler cut for the RF net's own pads is excused, rebuilt with the filler's
# geometry (rule clearance plus its extra clearance, at the board's arc error);
# any other uncovered area interrupts the plane. An RF via through a reference
# layer interrupts it by its antipad. Nothing is filled or saved.
MEASURE_REFERENCE = r"""
import hashlib, json, re, sys
from pathlib import Path
import pcbnew as k
board_path, spec, out_path = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3]
sha = hashlib.sha256(open(board_path, 'rb').read()).hexdigest()
b = k.LoadBoard(board_path)
ds = b.GetDesignSettings()
layers = {b.GetLayerName(l): l for l in b.GetEnabledLayers().CuStack()}
err, filler_err = k.FromMM(0.001), ds.m_MaxError
extra = k.FromMM(spec['extra_clearance_mm'])
problems, tracks, vias = [], [], []
for rules in sorted(Path(board_path).parent.glob('*.kicad_dru')):
    if re.search(spec['clearance_rule'], rules.read_text(errors='replace')):
        problems.append('custom clearance rules in ' + rules.name + ': the plane clearance is not evaluable')
names = {str(n) for n in b.GetNetsByName().keys()}
for net in [spec['net'], *spec['rf_nets']]:
    if net not in names:
        problems.append('net absent from the board: ' + net)
fills, gaps = {}, {}
for name in spec['layers']:
    if name not in layers:
        problems.append('reference layer not on the board: ' + name)
        continue
    layer, fill, gap = layers[name], k.SHAPE_POLY_SET(), None
    for z in b.Zones():
        if z.GetIsRuleArea() or z.GetNetname() != spec['net'] or not z.IsOnLayer(layer):
            continue
        if not z.IsFilled():
            problems.append('reference zone is not filled on ' + name)
        fill.BooleanAdd(z.GetFilledPolysList(layer))
        # The smallest zone clearance: a larger one would excuse more than one zone cut.
        own = z.GetOwnClearance(layer)
        gap = own if gap is None else min(gap, own)
    if gap is None:
        problems.append('no reference zone on ' + name)
    fills[name], gaps[name] = fill, gap or 0
pads = [p for fp in b.GetFootprints() for p in fp.Pads() if p.GetNetname() in spec['rf_nets']]
keeps = {}
for name, layer in ((n, layers[n]) for n in fills):
    keep = k.SHAPE_POLY_SET()
    for p in pads:
        clear = max(gaps[name], p.GetOwnClearance(layer), ds.m_MinClearance)
        if p.FlashLayer(layer):
            p.TransformShapeToPolygon(keep, layer, clear + extra, filler_err, k.ERROR_OUTSIDE)
        if p.HasHole():
            # The lesser of the two clearances a hole may carry: never more than KiCad cut.
            hole = min(clear, ds.m_HoleClearance)
            p.TransformHoleToPolygon(keep, hole + extra, filler_err, k.ERROR_OUTSIDE)
    keeps[name] = keep
for t in b.GetTracks():
    if t.GetNetname() not in spec['rf_nets']:
        continue
    if t.GetClass() == 'PCB_VIA':
        for name in spec['layers']:
            if name in layers and t.IsOnLayer(layers[name]):
                vias.append({'uuid': t.m_Uuid.AsString(), 'net': t.GetNetname(), 'kind': 'PCB_VIA',
                             'layers': [b.GetLayerName(t.TopLayer()), b.GetLayerName(t.BottomLayer())],
                             'reference_layer': name, 'status': 'interrupted',
                             'reason': 'RF via antipad through the reference plane'})
        continue
    if t.GetClass() not in ('PCB_TRACK', 'PCB_ARC'):
        continue
    for name in spec['layers']:
        rec = {'uuid': t.m_Uuid.AsString(), 'net': t.GetNetname(), 'kind': t.GetClass(),
               'layer': b.GetLayerName(t.GetLayer()), 'reference_layer': name}
        if name not in fills:
            rec.update(status='unmeasured', reason='reference layer not on the board')
        elif rec['layer'] == name:
            rec.update(status='unmeasured', reason='RF track on its own reference layer')
        else:
            shape = k.SHAPE_POLY_SET()
            t.TransformShapeToPolygon(shape, t.GetLayer(), 0, err, k.ERROR_INSIDE)
            shape.BooleanSubtract(fills[name])
            shape.BooleanSubtract(keeps[name])
            area = shape.Area() / 1e12
            rec.update(uncovered_mm2=round(area, 6),
                       status='interrupted' if area > spec['tolerance_mm2'] else 'continuous')
        tracks.append(rec)
json.dump({'schema': 'agent-ee.reference-continuity/2', 'board_sha256': sha, 'kicad': k.Version(),
           'scope': spec['scope'], 'spec': spec, 'problems': problems, 'tracks': tracks, 'vias': vias},
          open(out_path, 'w'), indent=1, sort_keys=True)
"""


def reference_spec(policy):
    return {**policy['reference'], 'extra_clearance_mm': FILLER_EXTRA_CLEARANCE_MM,
            'tolerance_mm2': CONTINUITY_TOLERANCE_MM2, 'clearance_rule': CLEARANCE_RULE.pattern,
            'scope': CONTINUITY_SCOPE}


def measure_reference(policy, board, routing, env):
    """KiCad's measurement of the declared reference plane under the RF nets, retained
    beside the router's reports. A failed measurement leaves no file: unknown."""
    out = routing / 'attempt' / POLICY_FILE
    with open(out.with_suffix('.log'), 'wb') as log:
        try:
            subprocess.run([PYTHON, '-I', '-B', '-c', MEASURE_REFERENCE, str(board),
                            canonical_json(reference_spec(policy)), str(out)],
                           cwd=routing, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                           timeout=600, check=False)
        except subprocess.TimeoutExpired:
            log.write(b'reference measurement timed out\n')
    if out.is_file() and not out.is_symlink():
        return digest(out)
    return None


def _item(status, **detail):
    return {'status': status, **detail}


def layer_policy_grade(policy, attempt_dir, result, classification, board):
    """Grade a declared layer policy from the retained reports alone.

    Each item is pass, defect or unknown; anything unmeasured is unknown and the
    whole grade is pass only when every item passes."""
    if classification not in ('complete', 'incomplete', 'validation_failed', 'policy_failed'):
        return {'status': 'unknown', 'items': {}, 'reason': f'{classification}: no routed board was graded'}
    attempt_dir = Path(attempt_dir)

    def read(rel):
        path = attempt_dir / rel
        return load_json(path, [], rel) if path.is_file() and not path.is_symlink() else None

    validation = result.get('validation') if isinstance(result, dict) else None
    sha = validation.get('validated_board_sha256') if isinstance(validation, dict) else None
    if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha):
        return {'status': 'unknown', 'items': {}, 'reason': 'no validated board hash'}
    items = {}
    allowed = set(policy['signal_layers'])
    cand, ref = read('validate/candidate-facts.json'), read('facts/reference-facts.json')
    if (not isinstance(cand, dict) or not isinstance(cand.get('tracks'), list) or
            not isinstance(ref, dict) or not isinstance(ref.get('tracks'), list)):
        items['disallowed_tracks'] = _item('unknown', reason='candidate or reference board facts unavailable')
    elif cand.get('sha256') != sha:
        items['disallowed_tracks'] = _item('unknown', reason='candidate facts are not of the validated board')
    elif not all(isinstance(t, dict) and isinstance(t.get('uuid'), str) and isinstance(t.get('layer'), str)
                 for t in cand['tracks'] + ref['tracks']):
        items['disallowed_tracks'] = _item('unknown', reason='malformed track facts')
    else:
        items['disallowed_tracks'] = disallowed_tracks(allowed, cand, ref, read('snapshot.json'), board)
    checks = read('validate/preservation.json')
    pour = [c for c in checks if isinstance(c, dict) and c.get('check') == 'pour_integrity'] \
        if isinstance(checks, list) else []
    if len(pour) != 1 or pour[0].get('status') not in ('PASS', 'FAIL'):
        items['pour_integrity'] = _item('unknown', reason='pour_integrity was not graded')
    else:
        items['pour_integrity'] = _item('pass' if pour[0]['status'] == 'PASS' else 'defect',
                                        detail=pour[0].get('detail'))
    delta, bound = read('validate/pour-integrity.json'), float(policy['pour_area_loss_pct'])
    layers = delta.get('layers') if isinstance(delta, dict) else None
    if not isinstance(layers, list) or not all(
            isinstance(r, dict) and isinstance(r.get('net_layer'), str) and
            (r.get('area_delta_pct') is None or type(r.get('area_delta_pct')) in (int, float)) for r in layers):
        items['area_loss'] = _item('unknown', reason='pour area deltas unavailable')
    elif delta.get('area_loss_limit_pct') != bound:
        items['area_loss'] = _item('unknown', reason='the router graded a different area-loss bound')
    elif not layers:
        items['area_loss'] = _item('unknown', reason='no copper pour was measured')
    else:
        items['area_loss'] = area_loss(policy, layers, bound)
    if 'reference' in policy:
        items['reference_continuity'] = reference_grade(policy, read(POLICY_FILE), sha, classification)
    if 'per_net_signal_layers' in policy:
        items['per_net_signal_layers'] = per_net_layer_grade(policy, cand, sha)
    statuses = {i['status'] for i in items.values()}
    status = 'defect' if 'defect' in statuses else 'unknown' if 'unknown' in statuses else 'pass'
    return {'status': status, 'items': items}


def per_net_layer_grade(policy, facts, sha):
    """All routed copper of declared nets, including input copper and full via spans.

    Reuse kicad-route's native board inventory; no pre-existing-copper exemption.
    An absent declaration is unknown, never implied by the global allowlist. Every
    graded item names the validated board hash it is evidence for (board_sha256)
    and the declaration it graded, so a consumer binds it to one candidate.
    """
    declared = policy.get('per_net_signal_layers')
    if declared is None:
        return _item('unknown', board_sha256=sha, reason='per-net signal layers were not declared')

    def item(status, **detail):
        return _item(status, board_sha256=sha, declared=declared, **detail)

    if not isinstance(facts, dict) or facts.get('sha256') != sha:
        return item('unknown', reason='native facts are not of the validated board')
    copper = facts.get('copper_layers')
    if (not isinstance(copper, list) or not all(isinstance(c, dict) and
            isinstance(c.get('name'), str) and COPPER_LAYER.fullmatch(c['name']) for c in copper)):
        return item('unknown', reason='native copper stack unavailable')
    stack = [c['name'] for c in copper]
    expected = ['F.Cu', *sorted((n for n in stack if n.startswith('In')),
                               key=lambda n: int(n[2:-3])), 'B.Cu']
    if len(stack) != len(set(stack)) or stack != expected:
        return item('unknown', reason='native copper stack malformed')
    tracks, vias = facts.get('tracks'), facts.get('vias')
    if not isinstance(tracks, list) or not isinstance(vias, list):
        return item('unknown', reason='native track or via inventory unavailable')
    problems, violations, measured = [], [], {net: 0 for net in declared}
    for net, allowed in declared.items():
        if not set(allowed) <= set(stack):
            problems.append(f'{net}: declared layer absent from board')

    def point(value):
        return isinstance(value, list) and len(value) == 2 and all(_is_int(n) for n in value)

    for kind, records in (('track', tracks), ('via', vias)):
        for rec in records:
            if not isinstance(rec, dict) or not isinstance(rec.get('net'), str):
                problems.append('unparseable native net identity')
                continue
            net = rec['net']
            if net not in declared:
                continue
            if not isinstance(rec.get('uuid'), str) or not rec['uuid']:
                problems.append(f'{net}: missing copper UUID')
                continue
            if kind == 'track':
                layers = [rec.get('layer')]
                valid = (rec.get('kind') in ('segment', 'arc') and layers[0] in stack and
                         _is_int(rec.get('width')) and rec['width'] > 0 and
                         point(rec.get('start')) and point(rec.get('end')) and
                         (rec['kind'] != 'arc' or point(rec.get('mid'))))
            else:
                pair = rec.get('layers')
                valid = (isinstance(pair, list) and len(pair) == 2 and all(n in stack for n in pair) and
                         pair[0] != pair[1] and point(rec.get('position')) and
                         rec.get('via_type') in ('through', 'blind', 'buried', 'micro') and
                         _is_int(rec.get('diameter')) and _is_int(rec.get('drill')) and
                         0 < rec['drill'] < rec['diameter'])
                layers = []
                if valid:
                    start, end = sorted(stack.index(n) for n in pair)
                    layers = stack[start:end + 1]
                    if rec['via_type'] == 'through' and layers != stack:
                        valid = False
            if not valid:
                problems.append(f'{net}: unparseable {kind} geometry ({rec["uuid"]})')
                continue
            measured[net] += 1
            disallowed = [n for n in layers if n not in declared[net]]
            if disallowed:
                violations.append({'uuid': rec['uuid'], 'net': net, 'kind': kind,
                                   'layers': layers, 'disallowed_layers': disallowed})
    problems += [f'{net}: no routed copper measured' for net, count in measured.items() if not count]
    return item('defect' if violations else 'unknown' if problems else 'pass',
                violations=violations, problems=problems, copper_measured=measured)


def disallowed_tracks(allowed, cand, ref, snapshot, board):
    """Tracks on layers outside the allowlist. One is pre-existing only when the input
    board's own facts (bound by hash to the snapshot) hold a track of the same geometry
    that no other candidate track already matched; anything else the router added."""
    files = snapshot.get('files') if isinstance(snapshot, dict) else None
    input_sha = files.get(board) if isinstance(files, dict) else None
    if not isinstance(input_sha, str) or not re.fullmatch(r'[0-9a-f]{64}', input_sha):
        return _item('unknown', reason='no input board hash in the snapshot')
    off = sorted((t for t in cand['tracks'] if t['layer'] not in allowed),
                 key=lambda t: (t['layer'], t['uuid'], canonical_json(t)))
    show = [{'uuid': t['uuid'], 'layer': t['layer'], 'net': t.get('net'), 'kind': t.get('kind')} for t in off]
    if ref.get('sha256') != input_sha:
        return _item('defect', tracks=show, preexisting=[],
                     reason='reference facts are not of the input board: no track is exempt')
    unmatched = collections.Counter(canonical_json([t.get(k) for k in TRACK_IDENTITY]) for t in ref['tracks'])
    new, preexisting = [], []
    for track, shown in zip(off, show):
        key = canonical_json([track.get(k) for k in TRACK_IDENTITY])
        if unmatched[key] > 0:
            unmatched[key] -= 1
            preexisting.append(shown)
        else:
            new.append(shown)
    return _item('defect' if new else 'pass', tracks=new, preexisting=preexisting)


def area_loss(policy, layers, bound):
    """Pour area deltas against the bound. A delta the router could not compute (no
    initial fill) is unknown, and a defect on a declared reference plane: the plane
    the policy protects had no copper to begin with."""
    reference = {f'{policy["reference"]["net"]}@{layer}' for layer in policy['reference']['layers']} \
        if 'reference' in policy else set()
    rows = [{'net_layer': r['net_layer'], 'area_delta_pct': r['area_delta_pct'], 'area_mm2': r.get('area_mm2')}
            for r in layers]
    over = [r for r in rows if r['area_delta_pct'] is not None and -r['area_delta_pct'] > bound]
    unmeasured = [r for r in rows if r['area_delta_pct'] is None]
    empty_reference = [r for r in unmeasured if r['net_layer'] in reference]
    absent = sorted(reference - {r['net_layer'] for r in rows})
    detail = dict(limit_pct=bound, exceeded=over, unmeasured=unmeasured, layers=rows)
    if over or empty_reference:
        return _item('defect', **detail, **({'reason': 'a declared reference plane had no initial fill'}
                                            if empty_reference else {}))
    if unmeasured or absent:
        return _item('unknown', **detail, absent=absent,
                     reason='area delta not measured: no initial fill' if unmeasured else
                     'a declared reference plane was not measured by the router')
    return _item('pass', **detail)


def reference_grade(policy, measured, sha, classification):
    if classification == 'validation_failed':
        return _item('unknown', scope=CONTINUITY_SCOPE,
                     reason='the router rejected the board; continuity was not measured')
    if (not isinstance(measured, dict) or measured.get('schema') != 'agent-ee.reference-continuity/2' or
            not isinstance(measured.get('tracks'), list) or not isinstance(measured.get('vias'), list) or
            not isinstance(measured.get('problems'), list)):
        return _item('unknown', scope=CONTINUITY_SCOPE, reason='reference continuity measurement unavailable')
    if (measured.get('board_sha256') != sha or measured.get('spec') != reference_spec(policy) or
            measured.get('scope') != CONTINUITY_SCOPE):
        return _item('unknown', scope=CONTINUITY_SCOPE,
                     reason='measurement is not of the validated board and declared reference')
    tracks, vias = measured['tracks'], measured['vias']
    if (not all(isinstance(t, dict) and t.get('status') in ('continuous', 'interrupted', 'unmeasured')
                for t in tracks) or
            not all(isinstance(v, dict) and v.get('status') == 'interrupted' for v in vias)):
        return _item('unknown', scope=CONTINUITY_SCOPE, reason='malformed continuity record')
    interrupted = [t for t in tracks if t['status'] == 'interrupted']
    unmeasured = [t for t in tracks if t['status'] == 'unmeasured']
    detail = {'scope': CONTINUITY_SCOPE, 'interrupted': interrupted, 'vias': vias, 'unmeasured': unmeasured,
              'problems': measured['problems'], 'tracks_measured': len(tracks) - len(unmeasured)}
    if interrupted or vias:
        return _item('defect', **detail)
    if unmeasured or measured['problems'] or not tracks:
        return _item('unknown', **detail, reason='no RF track measured' if not tracks else 'incomplete measurement')
    return _item('pass', **detail)


def load_json(path, problems, label):
    if not path.is_file() or path.is_symlink():
        return None
    if path.stat().st_size > JSON_LIMIT:
        problems.append(f'{label} exceeds {JSON_LIMIT} bytes')
        return None
    try:
        return json.loads(path.read_bytes())
    except (UnicodeDecodeError, ValueError, RecursionError):
        # ValueError includes an integer beyond the interpreter's digit limit.
        problems.append(f'{label} is not JSON')
        return None


def discover(out, request):
    """The single regular attempt directory in a fresh private OUT, or None.

    Never follows `latest` or any path a report names; every entry under
    runs/ must be a real directory except the router's `latest` link."""
    problems = []
    runs = out / 'runs'
    if not os.path.lexists(out):
        return None, problems
    if out.is_symlink() or not out.is_dir():
        return None, ['OUT is not a directory']
    if not os.path.lexists(runs):
        return None, problems
    if runs.is_symlink() or not runs.is_dir():
        return None, ['OUT/runs is not a directory']
    found = []
    for project in sorted(runs.iterdir()):
        if project.name != request['name'] or project.is_symlink() or not project.is_dir():
            problems.append(f'unexpected entry runs/{project.name}')
            continue
        for label in sorted(project.iterdir()):
            if label.name != request['label'] or label.is_symlink() or not label.is_dir():
                problems.append(f'unexpected entry runs/{project.name}/{label.name}')
                continue
            stamps = []
            for entry in sorted(label.iterdir()):
                if entry.name == 'latest' and entry.is_symlink():
                    continue
                if entry.is_symlink() or not entry.is_dir() or not STAMP.match(entry.name):
                    problems.append(f'unexpected entry in label directory: {entry.name}')
                else:
                    stamps.append(entry)
            latest = label / 'latest'
            if latest.is_symlink() and [os.readlink(latest)] != [s.name for s in stamps]:
                problems.append('latest does not name the single attempt directory')
            found += stamps
    if len(found) > 1:
        problems.append(f'{len(found)} attempt directories in a fresh OUT')
    return (found[0] if len(found) == 1 else None), problems


def resolve_reported(value, origin, routing):
    """A report path as an untrusted string: cwd-relative or absolute, never .."""
    if not isinstance(value, str) or not value:
        return None
    path = PurePosixPath(value)
    if '..' in path.parts or '.' in path.parts:
        return None
    resolved = Path(path) if path.is_absolute() else Path(origin) / path
    return resolved if resolved.is_relative_to(routing) else None


def _no_links_between(base, path):
    current = Path(base)
    for part in Path(path).relative_to(base).parts:
        current = current / part
        if current.is_symlink():
            return False
    return True


def _project_with_sibling_links(root, project, siblings, candidate_root):
    """The names of the links beside the real project directory in root, or None
    unless root holds that directory and only the links kicad-route's
    copy_project/make_snapshot place for ${KIPRJMOD}/../<dir> library paths: named
    for a top-level input directory, reading literally <staged input root>/<name>.
    The links are read, never followed."""
    entries = {p.name: p for p in root.iterdir()}
    found = entries.pop(project, None)
    if found is None or found.is_symlink() or not found.is_dir():
        return None
    if not all(name in siblings and path.is_symlink() and
               os.readlink(path) == str(PurePosixPath(candidate_root) / name)
               for name, path in entries.items()):
        return None
    return set(entries)


def candidate_binding_problems(request, origin, run_dir, cand_dir, result, inputs, files,
                               inventory, editor, sha, copper_sha, *, retained=False):
    """Shared candidate-to-attempt binding checks for live and retained verification."""
    problems = []
    if (resolve_reported(result['run_dir'], origin, origin) != run_dir or
            resolve_reported(result['candidate']['dir'], origin, origin) != cand_dir or
            resolve_reported(result['candidate']['board'], origin, origin) != cand_dir / request['board']):
        problems.append('result paths are not inside the discovered attempt')
    validation = result['validation']
    if sha is None or not validated_candidate(validation, sha):
        problems.append('routed candidate is not the board the router validated')
    if validation.get('restored_project_files') != []:
        problems.append('the router restored project files of an eligible attempt')
    problems += terminal_problems(result)
    custody = result.get('copper_ownership')
    if isinstance(custody, dict) and custody.get('status') == 'bound':
        records = (result.get('evidence') or {}).get('records') if isinstance(result.get('evidence'), dict) else None
        if (copper_sha != custody['sha256'] or not isinstance(records, dict) or
                records.get(COPPER_OWNERSHIP_RECORD) != custody['sha256']):
            problems.append('copper ownership record differs from the retained routing evidence'
                            if retained else 'copper ownership record differs from its result pointer')
    if (not isinstance(files, dict) or any(not isinstance(v, str) for v in files.values()) or
            request['board'] not in files or request['schematic'] not in files):
        problems.append('routing reference snapshot lacks the board or schematic')
        return problems
    prefix = request['project'] + '/'
    if any(inputs.get(prefix + rel) != sha_ for rel, sha_ in files.items()):
        problems.append('routing reference snapshot differs from the attempt inputs')
    if fingerprint(files) != result['input'].get('fingerprint'):
        problems.append('routing input fingerprint is not the retained snapshot fingerprint')
    state = PurePosixPath(request['board']).stem + '.kicad_prl'
    if not (editor == {} or (list(editor) == [state] and state not in files)):
        problems.append('candidate editor state is not the board editor-state file')
    if inventory != {**files, **editor, request['board']: sha}:
        problems.append('candidate inventory is not the snapshot with only the board routed')
    return problems


def bind_candidate(request, layout, run_dir, result, attempt, inputs):
    """Problems binding the eligible candidate to this attempt and these inputs, the
    candidate board's sha256, the undelivered editor-state files and the whole
    candidate file inventory, which verification re-checks against the snapshot."""
    routing = Path(layout['work']) / 'routing'
    problems = []
    if resolve_reported(result['run_dir'], routing, routing) != run_dir:
        problems.append('result run_dir is not the discovered attempt directory')
    cand_dir = run_dir / attempt['candidate_dir']
    ref_dir = run_dir / attempt['reference_dir']
    board = cand_dir / request['board']
    if (resolve_reported(result['candidate']['dir'], routing, routing) != cand_dir or
            resolve_reported(result['candidate']['board'], routing, routing) != board):
        problems.append('result candidate paths are not inside the discovered attempt')
        return problems, None, {}, {}
    if not _no_links_between(run_dir, board) or not board.is_file():
        problems.append('candidate board missing, or reached through a link')
        return problems, None, {}, {}
    # Only top-level input directories: a file's path has a single part, so a
    # link named for a top-level input file stays a contradiction.
    siblings = ({PurePosixPath(rel).parts[0] for rel in inputs if len(PurePosixPath(rel).parts) > 1} -
                {PurePosixPath(request['project']).parts[0], cand_dir.name})
    links = []
    for label, root in (('candidate', run_dir / 'candidate'), ('reference', run_dir / 'reference')):
        names = None if root.is_symlink() or not root.is_dir() else _project_with_sibling_links(
            root, cand_dir.name, siblings, staged_root(layout))
        if names is None:
            problems.append(f'{label}/ must hold exactly the project directory and its sibling links')
            return problems, None, {}, {}
        links.append(names)
    # copy_project links beside the candidate each sibling the snapshot linked.
    if links[0] != links[1]:
        problems.append('candidate/ and reference/ sibling links differ')
        return problems, None, {}, {}
    cand, ref = tree(cand_dir), tree(ref_dir)
    if links_of(cand) or links_of(ref):
        problems.append('candidate or reference tree contains links or special files')
    cand_files, ref_files = files_of(cand), files_of(ref)
    inventory = dict(cand_files)
    # kicad-cli pcb drc --save-board writes the board's editor-state file
    # beside it. It is hashed and reported, and never delivered.
    editor_state = PurePosixPath(request['board']).stem + '.kicad_prl'
    if editor_state in cand_files and editor_state not in ref_files:
        report_state = {editor_state: cand_files.pop(editor_state)}
    else:
        report_state = {}
    snapshot = load_json(run_dir / 'snapshot.json', problems, 'snapshot.json')
    if not isinstance(snapshot, dict) or snapshot.get('files') != ref_files:
        problems.append('snapshot.json files differ from the reference snapshot')
    sha = cand_files.get(request['board'])
    record = run_dir / COPPER_OWNERSHIP_RECORD
    copper_sha = digest(record) if not record.is_symlink() and record.is_file() else None
    problems += candidate_binding_problems(
        request, routing, run_dir, cand_dir, result, inputs, ref_files,
        inventory, report_state, sha, copper_sha)
    return problems, sha, report_state, inventory


def retain(run_dir, dest):
    """Copy the attempt's diagnostics, regular files only, no design files.

    Design files are observed by digest and not copied (omitted), including the
    prepared backend input under work/input. There a design-file name that is not a
    regular file is observed by its path type alone, never followed: 'link' for a
    symbolic link (valid, dangling or looping alike), 'special' for a directory or
    special file, so an absent optional file (no entry) stays distinct from a linked
    or unresolvable one (agent-510 R4). Other links under work/input are ignored."""
    retained, omitted, links = {}, {}, []
    if run_dir is None:
        return retained, omitted, links
    for name in RETAINED_FILES:
        src = run_dir / name
        if src.is_symlink():
            links.append(name)
        elif src.is_file():
            (dest / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest / name)
            retained[name] = digest(dest / name)
    for top in RETAINED_DIRS:
        root = run_dir / top
        if root.is_symlink():
            links.append(top)
            continue
        if not root.is_dir():
            continue
        for rel, entry in tree(root).items():
            name = f'{top}/{rel}'
            if top == 'work' and PurePosixPath(rel).parts[0] == 'input':
                # The prepared backend input (after protection and preprocessing) is
                # observed, never copied: its regular design files by digest only, so
                # experiment_grade can bind the sidecar's effective input to it.
                if name.endswith(DESIGN_SUFFIXES):
                    omitted[name] = entry[1] if entry[0] == 'file' else PATH_TYPES.get(entry[0], 'special')
                continue
            if entry[0] in ('link', 'other'):
                links.append(name)
            elif entry[0] == 'file':
                if name.endswith(DESIGN_SUFFIXES):
                    omitted[name] = entry[1]
                    continue
                (dest / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / rel, dest / name)
                retained[name] = digest(dest / name)
    return dict(sorted(retained.items())), dict(sorted(omitted.items())), sorted(links)


def _executable(path):
    path = Path(path)
    try:
        real = path.resolve(strict=True)
    except OSError:
        return {'path': str(path), 'resolved': None, 'sha256': None}
    return {'path': str(path), 'resolved': str(real), 'sha256': digest(real) if real.is_file() else None}


def _identities(request, layout, generation=None):
    def identity_of(root):
        return tree_identity(root) if root.is_dir() and not root.is_symlink() else None

    checkout, state = Path(layout['checkout']), Path(layout['state'])
    code_tops = ['bin', 'src']
    if is_rust_capable_checkout(checkout):
        code_tops += ['tools', 'rust-engine/src']
    identities = {'code': {top: identity_of(checkout / top) for top in code_tops},
                  'state': {comp: identity_of(state / comp)
                            for comp in state_components(request['backend'], generation)}}
    manifest = state / 'manifest.json'
    identities['state_manifest'] = digest(manifest) if manifest.is_file() else None
    return identities


class _Cancelled(Exception):
    pass


def run_tool(argv, env, cwd, stdout, stderr, timeout, cancel):
    """Run the router in its own process group; kill the whole group on timeout or cancel."""
    record = {'argv': argv, 'cwd': str(cwd), 'env': env, 'timeout_s': timeout,
              'exit_code': None, 'signal': None, 'timed_out': False, 'cancelled': False}
    start = time.monotonic()
    with open(stdout, 'wb') as out, open(stderr, 'wb') as err:
        try:
            proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=err, start_new_session=True)
        except OSError as exc:
            record.update(error=f'{type(exc).__name__}: {exc}', duration_s=0.0)
            return record
        try:
            while proc.poll() is None:
                if cancel['requested']:
                    raise _Cancelled
                if time.monotonic() - start > timeout:
                    raise subprocess.TimeoutExpired(argv, timeout)
                time.sleep(0.05)
        except (subprocess.TimeoutExpired, _Cancelled) as exc:
            record['timed_out' if isinstance(exc, subprocess.TimeoutExpired) else 'cancelled'] = True
            _kill_group(proc.pid)
            proc.wait()
        _kill_group(proc.pid)  # descendants still in the group must not outlive the attempt
    code = proc.returncode
    if code < 0:
        record['signal'] = -code
    else:
        record['exit_code'] = code
    record['duration_s'] = round(time.monotonic() - start, 3)
    record['pid'] = proc.pid
    return record


def _kill_group(pgid):
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def write_report(path, report):
    report = dict(report)
    report.pop('identity', None)
    report['identity'] = canonical_identity(report)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n')
    temp.replace(path)
    return report


def constraint_binding_problems(candidate_root, request):
    """Refuse unmatched exact constraint assignments before invoking the router."""
    project = candidate_root / request['project'] / Path(request['board']).with_suffix('.kicad_pro')
    if not project.is_file():
        return []  # Native router policy handles projects without a settings file.
    try:
        value = json.loads(project.read_bytes())
        if (isinstance(value, dict) and isinstance(value.get('net_settings', {}), dict) and
                value.get('net_settings', {}).get('netclass_patterns', []) == []):
            return []
        if __package__:
            from .net_binding import require_project_nets
        else:
            # These immutable sibling files are included in the step's hashed mounts.
            sys.path.insert(0, str(Path(__file__).parent))
            from net_binding import require_project_nets
        board = candidate_root / request['project'] / request['board']
        require_project_nets(value, board.read_bytes())
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return [f'net constraint binding refused: {exc}']
    return []


def _run_native_attempt(report, request, layout, routing, candidate_root, inputs_tree, inputs, generation, cancel):
    project = request['project']
    argv, env = tool_argv(request, layout), tool_env(layout)
    native_dir = routing / 'out' / 'native'
    native_dir.mkdir(parents=True, exist_ok=True)
    output_board = native_dir / request['board']
    op_result_path = routing / 'attempt' / NATIVE_OPERATION_FILE
    op_request_path = routing / 'attempt' / NATIVE_REQUEST_FILE
    source_board = staged_dir(request, layout) / request['board']
    with re01_enforced():
        report['process'] = run_tool(argv, env, routing, routing / 'kicad-route.stdout',
                                     routing / 'kicad-route.stderr', request['timeout_s'], cancel)
    report['identity_after'] = _identities(request, layout, generation)
    after_inputs = tree(candidate_root)
    report['discovery'] = {'attempt_dir': None, 'problems': []}
    retained, omitted, links = {}, {}, []
    for fname in (NATIVE_OPERATION_FILE, NATIVE_REQUEST_FILE):
        fpath = routing / 'attempt' / fname
        if fpath.is_symlink():
            links.append(fname)
        elif fpath.is_file():
            retained[fname] = digest(fpath)
    if output_board.is_symlink():
        links.append(f'native/{request["board"]}')
    elif output_board.is_file():
        omitted[f'native/{request["board"]}'] = digest(output_board)
    report['retained'] = {f'attempt/{k}': v for k, v in retained.items()}
    report['omitted_design_files'] = omitted
    load = []
    op_result = None
    if NATIVE_OPERATION_FILE in retained:
        try:
            op_result = strict_json_file(op_result_path, NATIVE_OPERATION_FILE)
        except RequestError as exc:
            load.append(str(exc))
    missing = []
    if NATIVE_OPERATION_FILE not in retained:
        missing.append(NATIVE_OPERATION_FILE)
    if ((report['process']['exit_code'] == 0 or (isinstance(op_result, dict) and op_result.get('routing') is not None))
            and NATIVE_REQUEST_FILE not in retained):
        missing.append(NATIVE_REQUEST_FILE)
    report['missing_artifacts'] = missing
    report['backend'] = native_backend_of(op_result)
    process = report['process']
    if process['cancelled']:
        classification, reasons = 'cancelled', ['attempt cancelled; the router process group was killed']
    elif process['timed_out']:
        classification, reasons = 'timeout', [f'router exceeded the {request["timeout_s"]} s adapter timeout']
    else:
        classification, reasons = native_semantics(
            process['exit_code'], op_result, op_request_path, source_board, output_board)
    violations = load + [f'link or special file in attempt diagnostics: {n}' for n in links]
    if report['identity_after'] != report['identity_before']:
        violations.append('tool code or state changed during the attempt')
    if after_inputs != inputs_tree:
        violations.append('candidate inputs changed during the attempt')
    report['inputs']['staged_after'] = staged_observation(layout)
    if report['inputs']['staged_after'] != {'status': 'observed', 'files': inputs,
                                            'sha256': canonical_identity(inputs_tree)}:
        violations.append('the staged original changed during the attempt')
    if classification == 'complete' and missing:
        violations.extend(f'eligible attempt lacks {n}' for n in missing)
    adapter_killed = process['cancelled'] or process['timed_out']
    if violations and not adapter_killed:
        classification, reasons = 'contradiction', reasons + violations
        report['rejected_candidate_sha256'] = None
    elif violations:
        reasons = reasons + violations
    if classification == 'complete':
        sha = digest(output_board)
        report['candidate_files'] = {request['board']: sha}
        dest = routing / 'candidate' / project / request['board']
        dest.parent.mkdir(parents=True)
        shutil.copyfile(output_board, dest)
        if digest(dest) != sha:
            classification, reasons = 'contradiction', reasons + ['candidate changed while being copied']
            shutil.rmtree(routing / 'candidate')
            report['candidate_files'] = {}
        else:
            report['candidate'] = {'path': f'candidate/{project}/{request["board"]}', 'sha256': sha}
    elif classification == 'validation_failed' and output_board.is_file() and not output_board.is_symlink():
        report['rejected_candidate_sha256'] = digest(output_board)
    report['semantics'] = {'classification': classification, 'reasons': reasons,
                           'exit_code': process['exit_code']}
    return _finish(report, routing)


def attempt(request, layout, cancel=None):
    """Run one attempt and write routing/adapter-report.json. Returns the report."""
    cancel = cancel if cancel is not None else {'requested': False}
    work = Path(layout['work'])
    routing = work / 'routing'
    if os.path.lexists(routing):
        raise RequestError('routing/ already exists; an attempt needs a fresh private OUT')
    for sub in ('home', 'tmp', 'attempt'):
        (routing / sub).mkdir(parents=True)
    report = {'schema': REPORT_SCHEMA, 'adapter': None,
              'request': request, 'reproducibility': REPRODUCIBILITY[request['backend']],
              'tool_version': dict(TOOL_VERSION), 'backend': dict(NO_BACKEND),
              'process': None, 'discovery': None, 'missing_artifacts': [], 'retained': {},
              'omitted_design_files': {}, 'candidate': None, 'candidate_editor_state': {},
              'candidate_files': {}, 'rejected_candidate_sha256': None, 'identity_after': None}
    candidate_root, checkout, state = Path(layout['candidate']), Path(layout['checkout']), Path(layout['state'])
    problems = []

    # Preconditions: nothing runs unless the input, code, state and mounts qualify.
    try:
        inputs_tree = tree(candidate_root)
    except (OSError, ValueError) as exc:
        inputs_tree, problems = {}, [f'candidate inputs unreadable: {exc}']
    inputs = files_of(inputs_tree)
    report['inputs'] = {'files': inputs, 'sha256': canonical_identity(inputs_tree), 'staged': None,
                        'staged_after': None}
    if links_of(inputs_tree):
        problems.append('candidate inputs contain links or special files')
    project = request['project']
    for name in (request['board'], request['schematic']):
        if f'{project}/{name}' not in inputs:
            problems.append(f'candidate lacks {project}/{name}')
    problems += constraint_binding_problems(candidate_root, request)
    rust_native = is_native_request(request, checkout)
    if rust_native:
        report['backend'] = native_backend_of(None)
        problems += native_unsupported_problems(request)
    record, generation, qualification = preflight(checkout, state, request['backend'], mounted_at=state)
    # The version this attempt runs is fixed here and recorded; nothing compares it with a list.
    report['adapter'] = adapter_claim(record['commit'])
    engine_bin = layout.get('engine') if 'engine' in layout else None
    if engine_bin is None and rust_native:
        engine_bin = resolve_kicad_route_engine(checkout)
    report['tool'] = {**record,
                      'kicad_route': _executable(checkout / 'bin' / 'kicad-route'),
                      **({'kicad_route_engine': _executable(engine_bin) if engine_bin else
                          {'path': None, 'resolved': None, 'sha256': None}} if rust_native else {}),
                      'python': _executable(PYTHON), 'kicad_cli': _executable('/usr/bin/kicad-cli'),
                      'kicad_share_templates': {t: _executable(f'/usr/share/kicad/template/{t}')['sha256']
                                                for t in ('sym-lib-table', 'fp-lib-table')}}
    problems += qualification
    targets = layout['probe'] if layout['probe'] is not None else (
        [state] + [state / c for c in state_components(request['backend'], generation) if (state / c).is_dir()] +
        ([checkout] if checkout != state else []))
    report['state_write_probe'] = write_probe(targets)
    if not all(p['refused'] for p in report['state_write_probe']):
        problems.append('the tool state or checkout is writable from the sandbox')
    report['identity_before'] = _identities(request, layout, generation)
    if not problems:
        # The router reads a staged copy of the verified input, never the mount itself.
        try:
            staged = stage_original(candidate_root, inputs_tree, staged_root(layout))
        except (OSError, ValueError) as exc:
            problems.append(f'cannot stage the original input: {exc}')
        else:
            report['inputs']['staged'] = staged_record(request, layout, staged)
            if staged != inputs_tree:
                problems.append('the staged original differs from the captured input')
    if problems:
        report['semantics'] = {'classification': 'not_run', 'reasons': problems, 'exit_code': None}
        experiment = experiment_grade(request, None, routing / 'attempt')
        if experiment is not None:
            report['semantics']['experiment'] = experiment
        return _finish(report, routing)

    if rust_native:
        return _run_native_attempt(report, request, layout, routing, candidate_root, inputs_tree,
                                   inputs, generation, cancel)

    argv, env = tool_argv(request, layout), tool_env(layout)
    if 'experiment_policy' in request:
        # The router reads its routability-policy/1 file here; verify re-checks the bytes.
        (routing / 'attempt' / EXPERIMENT_POLICY_FILE).write_text(canonical_json(request['experiment_policy']))
    report['process'] = run_tool(argv, env, routing, routing / 'kicad-route.stdout', routing / 'kicad-route.stderr',
                                 request['timeout_s'], cancel)
    report['identity_after'] = _identities(request, layout, generation)
    after_inputs = tree(candidate_root)
    run_dir, found = discover(routing / 'out', request)
    report['discovery'] = {'attempt_dir': run_dir.relative_to(routing).as_posix() if run_dir else None,
                           'problems': found}
    retained, omitted, links = retain(run_dir, routing / 'attempt')
    report['retained'] = {f'attempt/{k}': v for k, v in retained.items()}
    policy_file = routing / 'attempt' / EXPERIMENT_POLICY_FILE
    policy_kept = 'experiment_policy' in request and policy_file.is_file() and not policy_file.is_symlink()
    if policy_kept:
        report['retained'][f'attempt/{EXPERIMENT_POLICY_FILE}'] = digest(policy_file)
    report['omitted_design_files'] = omitted
    report['missing_artifacts'] = missing_reports(retained)
    load = []
    result = load_json(routing / 'attempt/result.json', load, 'result.json') if 'result.json' in retained else None
    attempt_record = load_json(routing / 'attempt/attempt.json', load, 'attempt.json') if 'attempt.json' in retained else None
    report['backend'] = backend_of(result)
    sidecars, sidecar_links = retain_experiment(run_dir, routing)
    for sidecar in sidecars:
        report['retained'][f'attempt/{sidecar}'] = digest(routing / 'attempt' / sidecar)
    links += sidecar_links
    experiment = experiment_grade(request, result, routing / 'attempt',
                                  experiment_run(request, layout, report['discovery']['attempt_dir'],
                                                 report['omitted_design_files']))
    stdout = (routing / 'kicad-route.stdout').read_bytes().decode('utf-8', 'replace')
    refused_line = refused_output(stdout)
    process = report['process']
    if process['cancelled']:
        classification, reasons = 'cancelled', ['attempt cancelled; the router process group was killed']
    elif process['timed_out']:
        classification, reasons = 'timeout', [f'router exceeded the {request["timeout_s"]} s adapter timeout']
    else:
        classification, reasons = semantics(process['exit_code'], result, attempt_record, refused_line,
                                            run_dir is not None, request)
    violations = found + load + [f'link or special file in attempt diagnostics: {n}' for n in links]
    violations += runtime_problems(request, layout, generation, result)
    if report['identity_after'] != report['identity_before']:
        violations.append('tool code or state changed during the attempt')
    if after_inputs != inputs_tree:
        violations.append('candidate inputs changed during the attempt')
    report['inputs']['staged_after'] = staged_observation(layout)
    if report['inputs']['staged_after'] != {'status': 'observed', 'files': inputs,
                                            'sha256': canonical_identity(inputs_tree)}:
        violations.append('the staged original changed during the attempt')
    if 'experiment_policy' in request and (
            not policy_kept or policy_file.read_bytes() != canonical_json(request['experiment_policy']).encode()):
        violations.append('experiment policy file changed during the attempt')
    if experiment is not None and experiment['status'] == 'refused':
        violations.append(f'routability experiment evidence refused ({experiment["reason"]}): '
                          f'{experiment["detail"]}')
    # verdict's policy_failed: the router said COMPLETE yet left connections open. Its board is bound
    # and graded like a complete one, then rejected exactly as a failed layer policy is (below).
    unconnected = classification == 'policy_failed'
    sha = None
    if classification in ('complete', 'incomplete') or unconnected:
        bind, sha, report['candidate_editor_state'], report['candidate_files'] = bind_candidate(
            request, layout, run_dir, result, attempt_record, inputs)
        violations += bind + [f'eligible attempt lacks {n}' for n in report['missing_artifacts']]
    elif classification == 'validation_failed' and run_dir is not None:
        board = run_dir / attempt_record['candidate_dir'] / request['board']
        if board.is_file() and not board.is_symlink():
            report['rejected_candidate_sha256'] = digest(board)  # diagnostics only; never copied
    if violations and classification not in ('timeout', 'cancelled'):
        classification, reasons = 'contradiction', reasons + violations
        # A contradiction rejects no candidate: the validation_failed diagnostic hash goes.
        report['rejected_candidate_sha256'] = None
    elif violations:
        reasons = reasons + violations
    policy, grade = request.get('layer_policy'), None
    graded = 'complete' if unconnected and classification == 'policy_failed' else classification
    if policy is not None:
        if graded in ('complete', 'incomplete') and 'reference' in policy:
            board = run_dir / attempt_record['candidate_dir'] / request['board']
            # retain() copies only the router's named reports, so these names are agent-ee's.
            if digest(board) == sha:
                measure_reference(policy, board, routing, tool_env(layout))
                for name in (POLICY_FILE, Path(POLICY_FILE).with_suffix('.log').name):
                    path = routing / 'attempt' / name
                    if path.is_file() and not path.is_symlink():
                        report['retained'][f'attempt/{name}'] = digest(path)
        grade = layer_policy_grade(policy, routing / 'attempt', result, graded, request['board'])
        if classification in ('complete', 'incomplete') and grade['status'] != 'pass':
            failing = [f'{k} {v["status"]}' for k, v in grade['items'].items() if v['status'] != 'pass']
            classification, reasons = 'policy_failed', reasons + [
                f'declared layer policy {grade["status"]}: ' + ', '.join(failing)]
            report['rejected_candidate_sha256'] = sha  # diagnostics only; never copied
            report['candidate_files'], report['candidate_editor_state'] = {}, {}
    if unconnected and classification == 'policy_failed':
        report['rejected_candidate_sha256'] = sha  # diagnostics only; never copied
        report['candidate_files'], report['candidate_editor_state'] = {}, {}
    if classification in ('complete', 'incomplete'):
        dest = routing / 'candidate' / project / request['board']
        dest.parent.mkdir(parents=True)
        shutil.copyfile(run_dir / attempt_record['candidate_dir'] / request['board'], dest)
        if digest(dest) != sha:
            classification, reasons = 'contradiction', reasons + ['candidate changed while being copied']
            shutil.rmtree(routing / 'candidate')
        else:
            report['candidate'] = {'path': f'candidate/{project}/{request["board"]}', 'sha256': sha}
    report['semantics'] = {'classification': classification, 'reasons': reasons,
                           'exit_code': process['exit_code']}
    if grade is not None:
        report['semantics']['layer_policy'] = grade
    if experiment is not None:
        report['semantics']['experiment'] = experiment
    return _finish(report, routing)


def _finish(report, routing):
    for name in ('kicad-route.stdout', 'kicad-route.stderr'):
        if (routing / name).is_file():
            report['retained'][name] = digest(routing / name)
    report['retained'] = dict(sorted(report['retained'].items()))
    proposal, coverage, code = CLASSES[report['semantics']['classification']]
    report['semantics'].update(proposal=proposal, coverage=coverage, adapter_exit=code)
    result = load_json(routing / 'attempt/result.json', [], 'result')
    record = load_json(routing / 'attempt/attempt.json', [], 'attempt')
    report['semantics']['pair_coverage'] = pair_coverage(
        report['request'], result, record, report['semantics']['classification'])
    policy = report['request'].get('layer_policy')
    if policy is not None and 'layer_policy' not in report['semantics']:
        report['semantics']['layer_policy'] = layer_policy_grade(
            policy, routing / 'attempt', result, report['semantics']['classification'], report['request']['board'])
    return write_report(routing / 'adapter-report.json', report)


def main(argv=None, layout=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        request = parse_request(argv)
    except RequestError as exc:
        print(f'kicad-route adapter: {exc}', file=sys.stderr)
        return EXIT_REQUEST
    layout = layout or default_layout(request)
    cancel = {'requested': False}
    previous = signal.signal(signal.SIGTERM, lambda *_: cancel.__setitem__('requested', True))
    try:
        report = attempt(request, layout, cancel)
    except RequestError as exc:
        print(f'kicad-route adapter: {exc}', file=sys.stderr)
        return EXIT_REQUEST
    except Exception:
        # An adapter fault must not read as the router's own exit 1.
        import traceback
        traceback.print_exc()
        return EXIT_ADAPTER_ERROR
    finally:
        signal.signal(signal.SIGTERM, previous)
    sem = report['semantics']
    print(json.dumps({'classification': sem['classification'], 'proposal': sem['proposal'],
                      'router_exit': sem['exit_code'], 'adapter_exit': sem['adapter_exit']}))
    return sem['adapter_exit']


if __name__ == '__main__':
    sys.exit(main())
