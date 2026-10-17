"""One pinned kicad-route attempt inside the networkless sandbox (AGENTEE-93).

This is the in-sandbox half of the routing adapter. It imports only the
standard library, so the coordinator can run it as
``/usr/bin/python3 -I -B /opt/agent-ee-routing/kicad_route_attempt.py`` from a
read-only single-file mount whose bytes it hashes.

The adapter is a compatibility layer for kicad-route ``08179cd`` only. That
pin has no versioned result schema, no ``--version`` flag and no run locator,
so this file checks the mounted tool code byte for byte against the pin and
refuses anything else instead of guessing. The router's argv and environment
come from constants and coordinator-declared arguments matched against fixed
patterns; nothing the writer controls reaches them, and ``--backend-arg``,
``--backend-env``, ``--force``, ``--canary`` and ``--rev`` are never passed.

The report is provenance. It never makes a check pass: acceptance comes only
from agent-ee's own fresh checks on the captured proposal.
"""

import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

PIN = '08179cdc97d08864da127f14d53ac46cf5a27851'
# Tracked code at PIN (git cat-file blob). __pycache__ is never read: the
# router runs with a private pycache prefix and bytecode writes disabled.
PINNED_CODE = {
    'bin/kicad-route': '03b2a21862c568a62373951ca03d4975961b51e83d978689e3a4eccf7f2cd76c',
    'src/kicad_route/__init__.py': '23611cbf0bfd79c20f1e18fa402bbe8c1bcd63b412fbf84e7eb3b5f3c0062af4',
    'src/kicad_route/blocked.py': '0b061d6c696006ceaf7fcd7729a6ac28821fb3161df616284a6fbf6abceca810',
    'src/kicad_route/campaign.py': 'fece952726c8a18cefe209ac0ec7b38f15d945ad40d66c22c737fa27cd8c18a7',
    'src/kicad_route/canary.py': '718a56b6b73b9830007e514fc6a154ffba2d26a6684167930ef008c8124c80f0',
    'src/kicad_route/cli.py': '0409a15b837b34b84f9de844273fd728a0997340a78955a9345f5b42dff77bca',
    'src/kicad_route/common.py': '4ce854d762064049bdd80cb2d6ab335dede89f99a4d47c89bead35695947a1c8',
    'src/kicad_route/corpus.py': '194d0635cccdee9bf9f8eb6b1969956fd16a082e9ad5a57bf7974d3fd2e173e8',
    'src/kicad_route/floor.py': '13ccb02c7b90add62b828208f315a2c8ac1fce79787097d94ee56c15a97edf08',
    'src/kicad_route/kicad/facts.py': '05d3ccf6439b1bca28af9e422984b2e8199df00cab1cbaa4fe0399e6278aa911',
    'src/kicad_route/kicad/locks.py': '5b15d4ddfbec9b7e32a841a16f9c1dd383b043c240a293251737e9cba218800a',
    'src/kicad_route/kicad/pour.py': 'a49f64c51854720e8ea680d81b94969dc8242da0c3ee93b72c8948a57b908c14',
    'src/kicad_route/kicad/rename_nets.py': 'a51c1767c0ce1d0edf180700a026d695678228d56ece4bab688d9bfd3d23d2a8',
    'src/kicad_route/kicad/specctra.py': 'b68be1788cb67f1e1dd4c2afb538e7af8b2cff00642ab503d83dfc90bf3f27cb',
    'src/kicad_route/kicad/spokes.py': 'b1bff0c45cea66a4111c3159e7d76b40891a22a525218609502ce2c556eb9a91',
    'src/kicad_route/ledger.py': '0181fd38986c09125656b187373f72b6f7d6f07d4d6bc4d30d75023e20b08a65',
    'src/kicad_route/netnames.py': '17647bd81aa65090e4b903fb0918683818bbc0e3261a8d7c5e3f457a8b5facaa',
    'src/kicad_route/pour.py': '972f3baa588bf7d53d784dd5c70afa5a87c50c21e2e117e58ad948ac1134b0bd',
    'src/kicad_route/provenance.py': 'cf82fd9940086584a8056787ae0ca4fd58ced78ed5737f85b4e5713501add49f',
    'src/kicad_route/skills.py': '2768f342c5389fe7e760b743584f726aac6e723d795d019125548405fc896363',
    'src/kicad_route/snapshot.py': '1c685b8eec4f8df424138bf97c5ea6bb6249dbf33445655ee608280b05c71e23',
    'src/kicad_route/spokes.py': 'a272ef12f8b97deee8f01c11933d18456c8a737076d78f6d6c8a224ba909859a',
    'src/kicad_route/stall.py': '095445837a1621ca8eaa62fee2f87ec772d13fcb9111e75528743f24f095d188',
}
REPORT_SCHEMA = 'agent-ee.kicad-route-attempt/1'
# Every report has exactly these keys, whatever the outcome.
REPORT_KEYS = {'schema', 'adapter', 'request', 'reproducibility', 'tool_version', 'backend', 'process',
               'discovery', 'missing_artifacts', 'retained', 'omitted_design_files', 'candidate',
               'candidate_editor_state', 'candidate_files', 'rejected_candidate_sha256', 'inputs', 'tool',
               'state_write_probe', 'identity_before', 'identity_after', 'semantics', 'identity'}
TOOL_VERSION = {'status': 'unknown', 'value': None,
                'reason': 'kicad-route at the pin has no --version; identity is the pinned code'}
NO_BACKEND = {'status': 'unknown', 'value': None, 'reason': 'no result.json'}
SANDBOX = {'work': '/work', 'candidate': '/candidate', 'checkout': '/opt/kicad-route',
           'state': '/opt/kicad-route-state'}
PYTHON = '/usr/bin/python3'
BACKENDS = ('krt', 'freerouting')
# Installed backend inputs the pinned cli.py reads under KICAD_ROUTE_STATE.
STATE_COMPONENTS = {'krt': ('.venv-krt', 'vendor/krt'), 'freerouting': ('vendor/freerouting',)}
STATE_REQUIRED = {'krt': ('.venv-krt/bin/python', 'vendor/krt/py_router/route.py'),
                  'freerouting': ('vendor/freerouting',)}
SAFE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z')
STAMP = re.compile(r'\d{8}T\d{6}\.\d{6}Z\Z')
# Every top-level result.json/attempt.json key cli.run_attempt writes at PIN
# (protect=existing). Any other shape is an incompatible tool.
RESULT_KEYS = {'kind', 'backend', 'input', 'settings', 'scope', 'unsupported', 'floor', 'process',
               'routing', 'validation', 'candidate', 'run_dir', 'runtime_s', 'tool_versions',
               'finished_utc', 'exit_code', 'ledger'}
ATTEMPT_KEYS = {'backend', 'project', 'board', 'schematic', 'input_fingerprint', 'source',
                'reference_dir', 'candidate_dir', 'reference_facts', 'baseline', 'scope',
                'settings', 'floor'}
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
# classification -> (proposal, routing coverage, adapter exit)
CLASSES = {
    'complete': ('eligible', 'routing_complete', 0),
    'incomplete': ('eligible_partial', 'routing_incomplete', 2),
    'backend_failure': ('none', 'missing_routing', 1),
    'validation_failed': ('none', 'missing_routing', 3),
    'refused': ('none', 'missing_routing', 4),
    'contradiction': ('none', 'unknown', EXIT_CONTRADICTION),
    'timeout': ('none', 'unknown', EXIT_TIMEOUT),
    'cancelled': ('none', 'unknown', EXIT_CANCELLED),
    'not_run': ('none', 'unknown', EXIT_REQUEST),
}
REPRODUCIBILITY = {'krt': 'deterministic backend, repeatability not qualified by this attempt',
                   'freerouting': 'attempt only; never a reproducible measurement'}


class RequestError(ValueError):
    pass


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


def verify_code(checkout, pinned=None):
    """Problems with the mounted tool code relative to the pinned blob hashes."""
    checkout, pinned = Path(checkout), PINNED_CODE if pinned is None else pinned
    problems, seen = [], {}
    for top in ('bin', 'src'):
        if not (checkout / top).is_dir() or (checkout / top).is_symlink():
            problems.append(f'missing tool directory {top}/')
            continue
        for rel, entry in tree(checkout / top).items():
            parts = PurePosixPath(rel).parts
            if '__pycache__' in parts:
                if entry[0] in ('link', 'other'):
                    problems.append(f'link or special file in pycache: {top}/{rel}')
                continue
            if entry[0] == 'file':
                seen[f'{top}/{rel}'] = entry[1]
            elif entry[0] != 'dir':
                problems.append(f'link or special file in tool code: {top}/{rel}')
    for rel in sorted(set(seen) | set(pinned)):
        if rel not in seen:
            problems.append(f'pinned file missing: {rel}')
        elif rel not in pinned:
            problems.append(f'unpinned file in tool code: {rel}')
        elif seen[rel] != pinned[rel]:
            problems.append(f'tool file differs from pin: {rel}')
    return problems


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
             '--backend', '--state'}
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
        'backend': raw.get('--backend', 'krt'),
        'state': raw.get('--state', 'checkout'),
    }
    if request['backend'] not in BACKENDS:
        raise RequestError(f'backend must be one of {BACKENDS}')
    if request['state'] not in ('checkout', 'separate'):
        raise RequestError('state must be checkout or separate')
    if request['timeout_s'] <= request['budget_s']:
        raise RequestError('timeout must exceed the router budget')
    return request


def default_layout(request):
    checkout = Path(SANDBOX['checkout'])
    state = checkout if request['state'] == 'checkout' else Path(SANDBOX['state'])
    return {'work': Path(SANDBOX['work']), 'candidate': Path(SANDBOX['candidate']), 'checkout': checkout,
            'state': state, 'pin': PIN, 'code': PINNED_CODE, 'probe': None}


def tool_argv(request, layout):
    routing = Path(layout['work']) / 'routing'
    return [PYTHON, '-B', '-X', f'pycache_prefix={routing / "pycache"}',
            str(Path(layout['checkout']) / 'bin' / 'kicad-route'), '--out', str(routing / 'out'),
            'run', '--backend', request['backend'],
            '--dir', str(Path(layout['candidate']) / request['project']),
            '--board', request['board'], '--schematic', request['schematic'],
            '--name', request['name'], '--label', request['label'],
            '--protect', 'existing', '--budget', str(request['budget_s'])]


def tool_env(layout):
    work, routing = Path(layout['work']), Path(layout['work']) / 'routing'
    home = routing / 'home'
    return {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
            'TMPDIR': str(routing / 'tmp'), 'XDG_CONFIG_HOME': str(home / '.config'),
            'XDG_CACHE_HOME': str(home / '.cache'), 'XDG_DATA_HOME': str(home / '.local/share'),
            'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPYCACHEPREFIX': str(routing / 'pycache'),
            'PYTHONNOUSERSITE': '1', 'KICAD_ROUTE_STATE': str(layout['state']),
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0',
            'GIT_CEILING_DIRECTORIES': f'{layout["candidate"]}:{work}'}


def expected_settings(request, rule_floor):
    return {'backend': request['backend'], 'budget_s': request['budget_s'], 'protect': 'existing',
            'max_iterations': None, 'passes': None, 'default_clearance_mm': None,
            'clearance_margin_mm': None, 'pour_area_loss_pct': None, 'pour_min_spokes': None,
            'backend_args': [], 'backend_env': {}, 'reserve_spokes': None, 'stall_s': None,
            'mode': 'all', 'rule_floor_sha256': rule_floor}


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
    """The pinned tool's input fingerprint over a reference snapshot's file hashes."""
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
    if not isinstance(result, dict) or set(result) != RESULT_KEYS:
        return 'contradiction', ['result.json shape differs from the pinned tool']
    if result['kind'] != 'route' or not _is_int(result['exit_code']) or result['exit_code'] != exit_code:
        return 'contradiction', ['result.json kind or exit_code contradicts the process exit']
    statuses = [(result.get(k), k, allowed) for k, allowed in
                (('process', PROCESS), ('routing', ROUTING), ('validation', VALIDATION))]
    for value, key, allowed in statuses:
        if not isinstance(value, dict) or value.get('status') not in allowed:
            return 'contradiction', [f'unknown {key} status']
    if recomputed_exit(result) != exit_code:
        return 'contradiction', ['statuses imply a different exit than the one observed']
    backend = result['backend']
    if not isinstance(backend, dict) or backend.get('name') != request['backend']:
        return 'contradiction', ['result backend differs from the requested backend']
    inp = result['input']
    if not isinstance(inp, dict) or inp.get('board') != request['board'] or inp.get('project') != request['name']:
        return 'contradiction', ['result input differs from the requested board or name']
    settings = result['settings']
    floor = settings.get('rule_floor_sha256') if isinstance(settings, dict) else None
    if settings != expected_settings(request, floor) or not (floor is None or isinstance(floor, str)):
        return 'contradiction', ['result settings differ from the fixed adapter settings']
    if not discovered:
        return 'contradiction', ['result.json without exactly one attempt directory']
    if not isinstance(attempt, dict) or set(attempt) != ATTEMPT_KEYS:
        return 'contradiction', ['attempt.json missing or shaped differently from the pinned tool']
    dirname = PurePosixPath(request['project']).name
    if (attempt['board'] != request['board'] or attempt['schematic'] != request['schematic'] or
            attempt['backend'] != request['backend'] or attempt['project'] != request['name'] or
            attempt['reference_dir'] != f'reference/{dirname}' or
            attempt['candidate_dir'] != f'candidate/{dirname}' or
            attempt['input_fingerprint'] != inp.get('fingerprint') or attempt['settings'] != settings):
        return 'contradiction', ['attempt.json contradicts result.json or the request']
    process, routing, validation = (result[k]['status'] for k in ('process', 'routing', 'validation'))
    candidate = result['candidate']
    if process == 'FAIL':
        if routing != 'NOT_RUN' or validation != 'NOT_RUN' or candidate is not None:
            return 'contradiction', ['process failure with a candidate or graded statuses']
        return 'backend_failure', list(result['process'].get('reasons') or ['process FAIL'])
    if not isinstance(candidate, dict) or set(candidate) != {'dir', 'board'}:
        return 'contradiction', ['process PASS without a candidate record']
    if validation != 'PASS':
        reasons = result['validation'].get('reasons')
        return 'validation_failed', list(reasons) if isinstance(reasons, list) else ['validation FAIL']
    if routing == 'COMPLETE':
        return 'complete', reasons
    if routing == 'INCOMPLETE':
        return 'incomplete', [f"{result['routing'].get('unconnected_in_scope')} unconnected in scope"]
    return 'contradiction', ['validation PASS with routing NOT_RUN']


def load_json(path, problems, label):
    if not path.is_file() or path.is_symlink():
        return None
    if path.stat().st_size > JSON_LIMIT:
        problems.append(f'{label} exceeds {JSON_LIMIT} bytes')
        return None
    try:
        return json.loads(path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError):
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
    for label, root in (('candidate', run_dir / 'candidate'), ('reference', run_dir / 'reference')):
        if root.is_symlink() or not root.is_dir() or [p.name for p in root.iterdir()] != [cand_dir.name]:
            problems.append(f'{label}/ must hold exactly the project directory')
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
    if set(cand_files) != set(ref_files):
        problems.append('candidate file set differs from the reference snapshot')
    for rel in sorted(set(cand_files) & set(ref_files)):
        if rel != request['board'] and cand_files[rel] != ref_files[rel]:
            problems.append(f'candidate changed a non-board project file: {rel}')
    prefix = request['project'] + '/'
    for rel, sha in ref_files.items():
        if inputs.get(prefix + rel) != sha:
            problems.append(f'reference snapshot file differs from the captured input: {rel}')
    for name in (request['board'], request['schematic']):
        if name not in ref_files:
            problems.append(f'reference snapshot lacks {name}')
    snapshot = load_json(run_dir / 'snapshot.json', problems, 'snapshot.json')
    if not isinstance(snapshot, dict) or snapshot.get('files') != ref_files:
        problems.append('snapshot.json files differ from the reference snapshot')
    if fingerprint(ref_files) != result['input'].get('fingerprint'):
        problems.append('input fingerprint differs from the reference snapshot')
    sha = cand_files.get(request['board'])
    validation = result['validation']
    if (sha is None or validation.get('delivered_board_sha256') != sha or
            validation.get('validated_board_sha256') != sha or validation.get('restored_project_files') != []):
        problems.append('candidate board is not the board the router validated')
    return problems, sha, report_state, inventory


def retain(run_dir, dest):
    """Copy the attempt's diagnostics, regular files only, no design files."""
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


def _identities(request, layout):
    def identity_of(root):
        return tree_identity(root) if root.is_dir() and not root.is_symlink() else None

    checkout, state = Path(layout['checkout']), Path(layout['state'])
    return {'code': {top: identity_of(checkout / top) for top in ('bin', 'src')},
            'state': {comp: identity_of(state / comp) for comp in STATE_COMPONENTS[request['backend']]}}


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


def attempt(request, layout, cancel=None):
    """Run one attempt and write routing/adapter-report.json. Returns the report."""
    cancel = cancel if cancel is not None else {'requested': False}
    work = Path(layout['work'])
    routing = work / 'routing'
    if os.path.lexists(routing):
        raise RequestError('routing/ already exists; an attempt needs a fresh private OUT')
    for sub in ('home', 'tmp', 'attempt'):
        (routing / sub).mkdir(parents=True)
    report = {'schema': REPORT_SCHEMA, 'adapter': {'compatibility': f'kicad-route@{layout["pin"]}'},
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
    report['inputs'] = {'files': inputs, 'sha256': canonical_identity(inputs_tree)}
    if links_of(inputs_tree):
        problems.append('candidate inputs contain links or special files')
    project = request['project']
    for name in (request['board'], request['schematic']):
        if f'{project}/{name}' not in inputs:
            problems.append(f'candidate lacks {project}/{name}')
    commit = None
    try:
        commit = checkout_commit(checkout)
    except OSError as exc:
        problems.append(f'cannot read the checkout HEAD: {exc}')
    report['tool'] = {'commit': commit, 'pin': layout['pin'],
                      'kicad_route': _executable(checkout / 'bin' / 'kicad-route'),
                      'python': _executable(PYTHON), 'kicad_cli': _executable('/usr/bin/kicad-cli'),
                      'kicad_share_templates': {t: _executable(f'/usr/share/kicad/template/{t}')['sha256']
                                                for t in ('sym-lib-table', 'fp-lib-table')}}
    if commit != layout['pin']:
        problems.append(f'checkout HEAD {commit} is not the pinned {layout["pin"]}')
    problems += verify_code(checkout, layout['code'])
    for rel in STATE_REQUIRED[request['backend']]:
        if not os.path.lexists(state / rel):
            problems.append(f'missing installed backend dependency: KICAD_ROUTE_STATE/{rel}')
    targets = layout['probe'] if layout['probe'] is not None else (
        [state] + [state / c for c in STATE_COMPONENTS[request['backend']] if (state / c).is_dir()] +
        ([checkout] if checkout != state else []))
    report['state_write_probe'] = write_probe(targets)
    if not all(p['refused'] for p in report['state_write_probe']):
        problems.append('the tool state or checkout is writable from the sandbox')
    report['identity_before'] = _identities(request, layout)
    if problems:
        report['semantics'] = {'classification': 'not_run', 'reasons': problems, 'exit_code': None}
        return _finish(report, routing)

    argv, env = tool_argv(request, layout), tool_env(layout)
    report['process'] = run_tool(argv, env, routing, routing / 'kicad-route.stdout', routing / 'kicad-route.stderr',
                                 request['timeout_s'], cancel)
    report['identity_after'] = _identities(request, layout)
    after_inputs = tree(candidate_root)
    run_dir, found = discover(routing / 'out', request)
    report['discovery'] = {'attempt_dir': run_dir.relative_to(routing).as_posix() if run_dir else None,
                           'problems': found}
    retained, omitted, links = retain(run_dir, routing / 'attempt')
    report['retained'] = {f'attempt/{k}': v for k, v in retained.items()}
    report['omitted_design_files'] = omitted
    report['missing_artifacts'] = missing_reports(retained)
    load = []
    result = load_json(routing / 'attempt/result.json', load, 'result.json') if 'result.json' in retained else None
    attempt_record = load_json(routing / 'attempt/attempt.json', load, 'attempt.json') if 'attempt.json' in retained else None
    report['backend'] = backend_of(result)
    stdout = (routing / 'kicad-route.stdout').read_bytes().decode('utf-8', 'replace')
    refused_line = any(line.startswith('[route] refused: ') for line in stdout.splitlines())
    process = report['process']
    if process['cancelled']:
        classification, reasons = 'cancelled', ['attempt cancelled; the router process group was killed']
    elif process['timed_out']:
        classification, reasons = 'timeout', [f'router exceeded the {request["timeout_s"]} s adapter timeout']
    else:
        classification, reasons = semantics(process['exit_code'], result, attempt_record, refused_line,
                                            run_dir is not None, request)
    violations = found + load + [f'link or special file in attempt diagnostics: {n}' for n in links]
    if report['identity_after'] != report['identity_before']:
        violations.append('tool code or state changed during the attempt')
    if after_inputs != inputs_tree:
        violations.append('candidate inputs changed during the attempt')
    sha = None
    if classification in ('complete', 'incomplete'):
        bind, sha, report['candidate_editor_state'], report['candidate_files'] = bind_candidate(
            request, layout, run_dir, result, attempt_record, inputs)
        violations += bind + [f'eligible attempt lacks {n}' for n in report['missing_artifacts']]
    elif classification == 'validation_failed' and run_dir is not None:
        board = run_dir / attempt_record['candidate_dir'] / request['board']
        if board.is_file() and not board.is_symlink():
            report['rejected_candidate_sha256'] = digest(board)  # diagnostics only; never copied
    if violations and classification not in ('timeout', 'cancelled'):
        classification, reasons = 'contradiction', reasons + violations
    elif violations:
        reasons = reasons + violations
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
    return _finish(report, routing)


def _finish(report, routing):
    for name in ('kicad-route.stdout', 'kicad-route.stderr'):
        if (routing / name).is_file():
            report['retained'][name] = digest(routing / name)
    report['retained'] = dict(sorted(report['retained'].items()))
    proposal, coverage, code = CLASSES[report['semantics']['classification']]
    report['semantics'].update(proposal=proposal, coverage=coverage, adapter_exit=code)
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
