"""One pinned kicad-route attempt inside the networkless sandbox (AGENTEE-93).

This is the in-sandbox half of the routing adapter. It imports only the
standard library, so the coordinator can run it as
``/usr/bin/python3 -I -B /opt/agent-ee-routing/kicad_route_attempt.py`` from a
read-only single-file mount whose bytes it hashes.

The adapter checks kicad-route's versioned result contract and the mounted
code byte for byte against one qualified pin. Unknown schemas and changed
code are refused. The router's argv and environment come from constants and
coordinator-declared arguments matched against fixed patterns; nothing the
writer controls reaches them, and ``--backend-env``, ``--force``, ``--canary``
and ``--rev`` are never passed. ``--backend-arg`` carries only KRT's
``--layers``/``--layer-costs`` built from a declared layer policy (AGENTEE-231).

The report is provenance. It never makes a check pass: acceptance comes only
from agent-ee's own fresh checks on the captured proposal.
"""

import collections
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

PIN = 'a132899a26ed9d7bf95c9978f0c1c992ee066f63'
# Tracked code at PIN (git cat-file blob). __pycache__ is never read: the
# router runs with a private pycache prefix and bytecode writes disabled.
PINNED_CODE = {
    'bin/kicad-route': '4d01520182f391ff1f92be7eb1e2415d3675a309a38ef449012d49e2d03e42e9',
    'src/kicad_route/__init__.py': '23611cbf0bfd79c20f1e18fa402bbe8c1bcd63b412fbf84e7eb3b5f3c0062af4',
    'src/kicad_route/barrier.py': 'de34c57c391248c4fdb02fdd9f71d6736edbc15621350be1ff188bc8b217df6c',
    'src/kicad_route/blocked.py': 'b2dc298ca38dd994479afdb6ced91d8196b0fd1626f2828c0996135ecb57c7f5',
    'src/kicad_route/campaign.py': '5cfad4d863c03578aa02fb2221598e8dcab73b2ac673124f8ff9d440ab4d2aa6',
    'src/kicad_route/canary.py': '38e56e7bd96f01025f9ca8a3c57d4f902f939723f55f50f214395561c3cbbf1c',
    'src/kicad_route/cli.py': '97d7c5fbe96a3656ba11719ff665db45724969ae9b62d475cd8549ec7fa7356a',
    'src/kicad_route/common.py': 'ea3aeb3411fc67a44c0f8c34fa69358c75418f63a1c329abc9a428607c88abe6',
    'src/kicad_route/contract.py': '306516b16cc6a55ce62d1915f354c298297bc944f4ae5eccc24a0d49bb83b2b6',
    'src/kicad_route/corpus.py': 'f63cedc1ec87b37b06c1a717f2eb02ec57af34a659b8f7e9e330eff5736bda14',
    'src/kicad_route/decoupling.py': '29724e237cc94c0af783619991dc5659b4b582eb6cdb33fd0d59542a108232f6',
    'src/kicad_route/determinism.py': '653d6c37b4e69d50cd701223e23c6feb2e8afd5b240b5bdd5a5f4c1146af5778',
    'src/kicad_route/diff_pairs.py': 'f1d1070ed0acc459d28cbe7397ae7c22b196f5dbb869e751af24e93a3c1b1092',
    'src/kicad_route/evidence.py': '6089c25ced84474a08b30c789f2c226455b4ed8d55d3589adbe9b65a464f4096',
    'src/kicad_route/floor.py': '2f954a2d1142c4f68d7e2904781f4470c0976af925a0a91c31c8de26d4f31ae6',
    'src/kicad_route/kicad/barrier.py': 'ffeab974f15db240d55c1d3e8a19450d885be7ab33b7f595fdf79c5f6e2409e4',
    'src/kicad_route/kicad/decoupling.py': '498624e3f38485519865bb871a01e3f62fc3a41c9bde80d2966ae3c05f7e88b5',
    'src/kicad_route/kicad/facts.py': 'cbc90a12bfbbfcb5f9ff4637396992faff128a35a9d13ff494184a5d6e57b316',
    'src/kicad_route/kicad/locks.py': '5b15d4ddfbec9b7e32a841a16f9c1dd383b043c240a293251737e9cba218800a',
    'src/kicad_route/kicad/pairs.py': '44763bfd3b25871cf9c5b5636d97f4534157920ac83ca755c3d57628638552f9',
    'src/kicad_route/kicad/pour.py': 'a49f64c51854720e8ea680d81b94969dc8242da0c3ee93b72c8948a57b908c14',
    'src/kicad_route/kicad/rename_nets.py': 'a51c1767c0ce1d0edf180700a026d695678228d56ece4bab688d9bfd3d23d2a8',
    'src/kicad_route/kicad/specctra.py': '0cd5031726ab48c7b3c2bf1dd3978837574d7452d7a79dda154123acb1aeb9d1',
    'src/kicad_route/kicad/spokes.py': 'c419ea1123a6de2588358aa58d0dfaabd4d5f55d50d559023ce48d2271f12bd9',
    'src/kicad_route/ledger.py': '37f140a1b6ccbf6c6ffa6724ea8acdf97e404eabd5ddb8c8c264a19cc1e6f752',
    'src/kicad_route/netnames.py': '731bcf61a909ef2720fd90b074f6cfb991a1c6ea6bea7fecf5ba3cb7fc47304f',
    'src/kicad_route/pour.py': '630b957fddeab33d108d634fb0f229f3c1471f80953e971effad27ca33beb2e9',
    'src/kicad_route/procgroup.py': 'd843c9b334874d0334c5ea0e9a58d83d22581648e915d40f13a6d8c8b595a3d5',
    'src/kicad_route/provenance.py': 'cf82fd9940086584a8056787ae0ca4fd58ced78ed5737f85b4e5713501add49f',
    'src/kicad_route/sandbox.py': 'dbdf52f09fa83f336d326bb0504624b94861d9195c1a7d817aa2f41fbe562b07',
    'src/kicad_route/skills.py': '2768f342c5389fe7e760b743584f726aac6e723d795d019125548405fc896363',
    'src/kicad_route/snapshot.py': '7c51203f4a439cc99a575e6bc5433f3ab78dda7735dcd03c940f6169fe7fb9a4',
    'src/kicad_route/spokes.py': 'fc5b3f90c31cbcc9a9944628bfe6b9bc93dc31de21c893f8e476160f21033506',
    'src/kicad_route/stall.py': '095445837a1621ca8eaa62fee2f87ec772d13fcb9111e75528743f24f095d188',
    'src/kicad_route/statemanifest.py': '55a0557ae0115967fbf6eb15c3bbc63c4c0d0fa9cac3de9c7d7c8725c1119a1a',
}
RESULT_SCHEMA = 'kicad-route/route/1'

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
RESULT_KEYS = {'schema', 'measurement', 'evidence', 'tool_identity', 'sandbox', 'kind', 'backend',
               'input', 'settings', 'scope', 'unsupported', 'floor', 'process',
               'routing', 'validation', 'candidate', 'run_dir', 'runtime_s', 'tool_versions',
               'finished_utc', 'exit_code', 'ledger'}
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
REPRODUCIBILITY = {'krt': 'deterministic backend, repeatability not qualified by this attempt',
                   'freerouting': 'attempt only; never a reproducible measurement'}


class RequestError(ValueError):
    pass


# Declared layer policy (AGENTEE-231): allowed KRT signal layers, optional KRT
# cost multipliers in the same order, kicad-route's pour area-loss bound and an
# optional reference plane whose fill must lie under the declared RF nets.
COPPER_LAYER = re.compile(r'(F|B|In[1-9][0-9]?)\.Cu\Z')
NET_NAME = re.compile(r'[^\x00-\x1f\x7f]{1,256}\Z')
POLICY_KEYS = {'signal_layers', 'pour_area_loss_pct'}
POLICY_OPTIONAL = {'layer_costs', 'reference'}
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
                           'optionally layer_costs and reference')
    _names(policy['signal_layers'], COPPER_LAYER, 'signal_layers')
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
    except (TypeError, json.JSONDecodeError):
        raise RequestError('layer policy must be JSON') from None
    validate_layer_policy(policy)
    if canonical_json(policy) != text:
        raise RequestError('layer policy must be canonical JSON (sorted keys, no spaces)')
    return policy


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
             '--backend', '--state', '--diff-pairs', '--layer-policy'}
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
    if '--diff-pairs' in raw:
        if raw['--diff-pairs'] != 'auto' or request['backend'] != 'krt':
            raise RequestError('diff-pairs must be auto and requires the krt backend')
        request['diff_pairs'] = 'auto'
    if '--layer-policy' in raw:
        # The pin refuses --backend-arg with --diff-pairs; freerouting has no KRT layer costs.
        if request['backend'] != 'krt' or '--diff-pairs' in raw:
            raise RequestError('layer-policy requires the krt backend without diff-pairs')
        request['layer_policy'] = parse_layer_policy(raw['--layer-policy'])
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
            str(Path(layout['checkout']) / 'bin' / 'kicad-route'), 'run',
            '--out', str(routing / 'out'), '--json', '--backend', request['backend'],
            '--dir', str(Path(layout['candidate']) / request['project']),
            '--board', request['board'], '--schematic', request['schematic'],
            '--name', request['name'], '--label', request['label'],
            '--protect', 'existing', '--budget', str(request['budget_s']),
            *(['--diff-pairs', 'auto'] if request.get('diff_pairs') == 'auto' else []),
            *policy_argv(request.get('layer_policy'))]


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
            'GIT_CEILING_DIRECTORIES': f'{layout["candidate"]}:{work}'}


def expected_settings(request, rule_floor, pairs=None):
    settings = {'backend': request['backend'], 'budget_s': request['budget_s'], 'protect': 'existing',
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
    return settings


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
    except json.JSONDecodeError:
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
    version = 2 if paired else 1
    if isinstance(result, dict) and result.get('schema') != f'kicad-route/route/{version}':
        return 'contradiction', ['unknown result schema version']
    if not isinstance(result, dict) or set(result) != RESULT_KEYS | ({'pairs'} if paired else set()):
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
    pairs = settings.get('pairs') if isinstance(settings, dict) and paired else None
    if settings != expected_settings(request, floor, pairs) or not (floor is None or isinstance(floor, str)):
        return 'contradiction', ['result settings differ from the fixed adapter settings']
    if not discovered:
        return 'contradiction', ['result.json without exactly one attempt directory']
    if not isinstance(attempt, dict) or set(attempt) != ATTEMPT_KEYS | ({'pairs', 'pair_routing'} if paired else set()):
        return 'contradiction', ['attempt.json missing or shaped differently from the pinned tool']
    if attempt['schema'] != f'kicad-route/attempt/{version}':
        return 'contradiction', ['unknown attempt schema version']
    if paired and not pair_binding(pairs, attempt, result):
        return 'contradiction', ['pair evidence differs from the declared pair plan']
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
    statuses = {i['status'] for i in items.values()}
    status = 'defect' if 'defect' in statuses else 'unknown' if 'unknown' in statuses else 'pass'
    return {'status': status, 'items': items}


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
    if sha is None or not validated_candidate(validation, sha) or validation.get('restored_project_files') != []:
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
    policy, grade = request.get('layer_policy'), None
    if policy is not None:
        if classification in ('complete', 'incomplete') and 'reference' in policy:
            board = run_dir / attempt_record['candidate_dir'] / request['board']
            # retain() copies only the router's named reports, so these names are agent-ee's.
            if digest(board) == sha:
                measure_reference(policy, board, routing, tool_env(layout))
                for name in (POLICY_FILE, Path(POLICY_FILE).with_suffix('.log').name):
                    path = routing / 'attempt' / name
                    if path.is_file() and not path.is_symlink():
                        report['retained'][f'attempt/{name}'] = digest(path)
        grade = layer_policy_grade(policy, routing / 'attempt', result, classification, request['board'])
        if classification in ('complete', 'incomplete') and grade['status'] != 'pass':
            failing = [f'{k} {v["status"]}' for k, v in grade['items'].items() if v['status'] != 'pass']
            classification, reasons = 'policy_failed', reasons + [
                f'declared layer policy {grade["status"]}: ' + ', '.join(failing)]
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
