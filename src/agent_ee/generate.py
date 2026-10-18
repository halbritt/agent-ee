"""Rung-1 generation driver: one-paragraph spec to a JLCPCB package in seven receipted stages.

Run ``python3 -m agent_ee.generate SPEC.md --out RUNDIR`` (see docs/generate.md).
Every stage writes ``RUNDIR/stages/N-NAME/receipt.json``; a stage never starts
after an earlier failure. ``rung1-record.json`` says ``done: true`` only when the
review verdict is ``criteria_satisfied`` and the fab exports exist and are nonempty.
"""
import argparse
import csv
import datetime
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback
import zipfile

from . import executor
from .storage import canonical, digest, write_json

# Experimental, unqualified record names: the qualified contracts are owned elsewhere
# (stage receipts AGENTEE-95, envelope AGENTEE-85) and supersede these at integration.
QUALIFICATION = 'experimental-unqualified'
RECEIPT_SCHEMA = 'agent-ee.rung1-stage-receipt/0-experimental'
RECORD_SCHEMA = 'agent-ee.rung1-record/0-experimental'
PARTS_SCHEMA = 'agent-ee.rung1-parts-manifest/0-experimental'
PARAMETERS_SCHEMA = 'agent-ee.rung1-parameters/0-experimental'
FAB_SCHEMA = 'agent-ee.rung1-fab-manifest/0-experimental'
ENVELOPE_SCHEMA = 'agent-ee.rung1-envelope/0-experimental'
STAGES = ((1, 'requirements'), (2, 'parts'), (3, 'schematic'), (4, 'placement'),
          (5, 'routing'), (6, 'review'), (7, 'fab'))
STATUSES = ('ok', 'failed', 'not_reached', 'not_reached:review_skipped',
            'not_reached:dependency_missing')
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parent
STOCK = Path('/usr/share/kicad')
SYMBOL_LIBRARIES = ('Connector_Generic', 'Device', 'power')
# The owner's fome-mc BOM is read live and copied only into the run directory; fome-mc
# design data is not republished in this repository.
CATALOG_CHECKOUT = Path.home() / 'git' / 'fome-mc'
CATALOG_PATH = 'export/BOM-fome-mc.csv'
CATALOG_COLUMNS = ['Comment', 'Designator', 'Footprint', 'LCSC Part #', 'Quantity']
SCOPE_TEMPLATE = REPO_ROOT / 'fixtures/closed-loop/control.scope.json'
FIXTURE_PROJECT = REPO_ROOT / 'fixtures/rc/control'
PROJECT_NAME = 'rung1_rc'
# Imperial chip sizes in ascending order; selection distance is measured on this list.
CHIP_SIZES = ('01005', '0201', '0402', '0603', '0805', '1206', '1210', '1812', '2010', '2512')
FAMILIES = {
    'Device:R': {'pattern': r'Resistor_SMD:R_(\d{4,5})_\d{4,5}Metric', 'default': 'Resistor_SMD:R_0805_2012Metric'},
    'Device:C': {'pattern': r'Capacitor_SMD:C_(\d{4,5})_\d{4,5}Metric', 'default': 'Capacitor_SMD:C_0805_2012Metric'},
}
FAB_LAYERS = ('F.Mask', 'B.Mask', 'F.Paste', 'B.Paste', 'F.SilkS', 'B.SilkS', 'Edge.Cuts')
NUMBER = r'(\d+(?:\.\d+)?)'
NET = r'(/?[A-Za-z][A-Za-z0-9_]*)'
# Each pattern must match exactly once in the spec; nothing is defaulted.
SPEC_PATTERNS = {
    'header': r'(\d+)-pin header (J\d+)',
    'header_pins': (r'(J\d+) carries the input ' + NET + r' on pin (\d+), the output ' + NET +
                    r' on pin (\d+) and ' + NET + r' on pin (\d+)'),
    'resistor': NUMBER + r'([kM]?) resistor (R\d+) connects ' + NET + ' to ' + NET,
    'capacitor': NUMBER + r'([pnu]) capacitor (C\d+) connects ' + NET + ' to ' + NET,
    'step': r'ideal unloaded ' + NUMBER + r' V step at ' + NET,
    'response': NET + r' at ' + NUMBER + r' ms must be between ' + NUMBER + r' V and ' + NUMBER + r' V inclusive',
    'track_width': r'at least ' + NUMBER + r' mm wide',
    'board': r'(\d+)-layer ' + NUMBER + r' mm x ' + NUMBER + r' mm rectangle with (J\d+) on the left edge',
    'hand_soldered': r'through-hole header (J\d+) is (hand-soldered|JLCPCB-assembled)',
}
SI = {'': 1, 'k': 1e3, 'M': 1e6, 'p': 1e-12, 'n': 1e-9, 'u': 1e-6}


class StageFailure(Exception):
    """A stage could not establish its output; later stages must not start."""


class DependencyMissing(Exception):
    """A required sibling module (G1/G2) is not present in this checkout."""


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def fmt(value):
    """Shortest decimal text for a parsed spec number (1.0 -> '1', 0.2 -> '0.2')."""
    text = repr(float(value))
    return text[:-2] if text.endswith('.0') else text


def copy_tree(source, destination, *, ignore=('*.kicad_prl', '*-backups', 'fp-info-cache', '.history')):
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(*ignore))
    return destination


# ------------------------------------------------------------------ tool identity

_TOOLS = {}


def tool_identity(name):
    """Path, binary sha256 and version string, recorded like `agent-ee doctor`."""
    if name in _TOOLS:
        return _TOOLS[name]
    record = {'path': None, 'sha256': None, 'version': None}
    try:
        if name == 'pcbnew':
            from .cli import pcbnew_extension
            path, diagnostic = pcbnew_extension()
            record['path'] = path
            probe = subprocess.run(['/usr/bin/python3', '-c', 'import pcbnew; print(pcbnew.GetBuildVersion())'],
                                   capture_output=True, text=True, timeout=30)
            record['version'] = probe.stdout.strip() or None
            if not path:
                record['diagnostic'] = diagnostic
        elif name == 'agent_ee':
            record['path'] = str(PACKAGE_ROOT / 'agent_ee')
            files = sorted((PACKAGE_ROOT / 'agent_ee').glob('*.py'))
            record['policy_files'] = {p.name: digest(p) for p in files}
            record['sha256'] = sha256_bytes(canonical(record['policy_files']))
            head = subprocess.run(['git', '-C', str(REPO_ROOT), 'rev-parse', 'HEAD'],
                                  capture_output=True, text=True, timeout=30)
            record['version'] = head.stdout.strip() or None
            _TOOLS[name] = record
            return record
        else:
            record['path'] = shutil.which(name)
            command = {'kicad-cli': [name, 'version'], 'bwrap': [name, '--version'],
                       '/usr/bin/python3': [name, '--version'], 'kicad-route': None}.get(name, [name, '--version'])
            if name == 'kicad-route' and record['path']:
                real = Path(record['path']).resolve()
                record['resolved'] = str(real)
                root = next((p for p in real.parents if (p / '.git').exists()), None)
                if root:
                    head = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                                          capture_output=True, text=True, timeout=30)
                    record['version'] = 'git ' + head.stdout.strip()
                    record['checkout'] = str(root)
            elif record['path'] and command:
                probe = subprocess.run(command, capture_output=True, text=True, timeout=30)
                record['version'] = (probe.stdout.strip() or probe.stderr.strip()) or None
        if record['path'] and Path(record['path']).is_file():
            record['sha256'] = digest(record['path'])
    except (OSError, subprocess.SubprocessError) as exc:
        record['error'] = str(exc)
    _TOOLS[name] = record
    return record


# ------------------------------------------------------------------ stage receipts

class Stage:
    def __init__(self, rundir, number, name):
        self.rundir = Path(rundir)
        self.number, self.name = number, name
        self.dir = self.rundir / 'stages' / f'{number}-{name}'
        self.receipt = {'schema': RECEIPT_SCHEMA, 'qualification': QUALIFICATION,
                        'stage': number, 'name': name, 'status': None,
                        'inputs': [], 'outputs': [], 'tools': {}, 'commands': [], 'details': {},
                        'limitations': [], 'error': None, 'started': None, 'finished': None}

    def start(self):
        self.dir.mkdir(parents=True, exist_ok=False)
        self.receipt['started'] = now()

    def rel(self, path):
        path = Path(path).absolute()
        try:
            return str(path.relative_to(self.rundir.absolute()))
        except ValueError:
            return str(path)

    def _files(self, path, role, bucket):
        path = Path(path)
        files = sorted(p for p in path.rglob('*') if p.is_file()) if path.is_dir() else [path]
        for item in files:
            self.receipt[bucket].append({'path': self.rel(item), 'role': role, 'sha256': digest(item),
                                         'bytes': item.stat().st_size})

    def input(self, path, role):
        self._files(path, role, 'inputs')

    def output(self, path, role):
        self._files(path, role, 'outputs')

    def tool(self, name):
        self.receipt['tools'][name] = tool_identity(name)

    def run(self, argv, *, cwd=None, timeout=600, env=None, label):
        """Unsandboxed subprocess with its argv, exit code and logs retained."""
        logs = self.dir / 'logs'
        logs.mkdir(exist_ok=True)
        started, record = time.monotonic(), {'label': label, 'argv': [str(a) for a in argv],
                                             'cwd': str(cwd) if cwd else None, 'sandbox': None}
        try:
            proc = subprocess.run(record['argv'], cwd=cwd, env=env, capture_output=True, timeout=timeout)
            record.update(exit_code=proc.returncode, execution='ok' if proc.returncode == 0 else 'failed')
            out, err = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            record.update(exit_code=None, execution='timeout')
            out, err = exc.stdout or b'', exc.stderr or b''
        except OSError as exc:
            record.update(exit_code=None, execution='unavailable')
            out, err = b'', str(exc).encode()
        record['duration_s'] = round(time.monotonic() - started, 3)
        for stream, raw in (('stdout', out), ('stderr', err)):
            target = logs / f'{label}.{stream}.log'
            target.write_bytes(raw)
            record[stream] = self.rel(target)
            record[stream + '_sha256'] = sha256_bytes(raw)
        self.receipt['commands'].append(record)
        return record

    def sandboxed(self, inputs, work, argv, *, timeout=600, label):
        """Run through the Bubblewrap executor: /candidate read-only, /work writable, no network."""
        result = executor.execute(inputs, work, argv, timeout=timeout)
        self.receipt['commands'].append({
            'label': label, 'argv': result['argv'], 'cwd': '/work', 'sandbox': 'bwrap',
            'candidate': self.rel(inputs), 'work': self.rel(work), 'exit_code': result['exit_code'],
            'execution': result['execution'], 'duration_s': result['duration_s'],
            'console': self.rel(Path(work) / 'console.log'), 'console_sha256': result['console_sha256'],
            'execution_record': self.rel(Path(work) / 'execution.json')})
        return result

    def finish(self, status, error=None):
        if status not in STATUSES:
            raise ValueError('unknown stage status: ' + str(status))
        self.receipt['status'] = status
        self.receipt['error'] = error
        self.receipt['finished'] = now() if self.receipt['started'] else None
        self.dir.mkdir(parents=True, exist_ok=True)
        write_json(self.dir / 'receipt.json', self.receipt)
        return self.receipt


def validate_receipt(receipt):
    """Structural contract of agent-ee.stage-receipt/1; raises ValueError."""
    keys = {'schema', 'qualification', 'stage', 'name', 'status', 'inputs', 'outputs', 'tools', 'commands', 'details',
            'limitations', 'error', 'started', 'finished'}
    if (not isinstance(receipt, dict) or set(receipt) != keys or receipt['schema'] != RECEIPT_SCHEMA
            or receipt['qualification'] != QUALIFICATION):
        raise ValueError('not a stage receipt')
    if (receipt['stage'], receipt['name']) not in STAGES or receipt['status'] not in STATUSES:
        raise ValueError('unknown stage or status')
    for bucket in ('inputs', 'outputs'):
        for item in receipt[bucket]:
            if set(item) != {'path', 'role', 'sha256', 'bytes'} or not re.fullmatch(r'[0-9a-f]{64}', item['sha256']):
                raise ValueError('file record requires path, role, sha256 and bytes')
    for command in receipt['commands']:
        if not isinstance(command.get('argv'), list) or 'exit_code' not in command:
            raise ValueError('command record requires argv and exit_code')
    reached = receipt['status'] in ('ok', 'failed', 'not_reached:review_skipped')
    if reached and not (receipt['started'] and receipt['finished']):
        raise ValueError('a reached stage needs started and finished timestamps')
    if receipt['status'] == 'failed' and not receipt['error']:
        raise ValueError('a failed stage records its error')
    return receipt


# ------------------------------------------------------------------ stage 1: requirements

def parse_spec(text):
    """Extract rung-1 parameters with their exact source spans; refuse anything ambiguous."""
    flat = ' '.join(text.split())
    found = {}
    for key, pattern in SPEC_PATTERNS.items():
        matches = list(re.finditer(pattern, flat))
        if len(matches) != 1:
            raise StageFailure(f'spec must state {key} exactly once (pattern {pattern!r}, found {len(matches)})')
        found[key] = matches[0]
    header, pins, res, cap = found['header'], found['header_pins'], found['resistor'], found['capacitor']
    step, response, board = found['step'], found['response'], found['board']
    if len({header[2], pins[1], board[4], found['hand_soldered'][1]}) != 1:
        raise StageFailure('spec names different header references')
    count = int(header[1])
    header_nets = {pins[3]: pins[2], pins[5]: pins[4], pins[7]: pins[6]}
    if len(header_nets) != 3 or sorted(int(p) for p in header_nets) != list(range(1, count + 1)):
        raise StageFailure('header pin assignment must name every header pin once')
    net_in, net_out, ground = pins[2], pins[4], pins[6]
    if (res[4], res[5]) != (net_in, net_out) or (cap[4], cap[5]) != (net_out, ground):
        raise StageFailure('spec topology is not the rung-1 RC low-pass (R: in->out, C: out->ground)')
    if step[2] != net_in or response[1] != net_out:
        raise StageFailure('spec stimulus/measurement nets differ from the header nets')
    low, high = float(response[3]), float(response[4])
    if not low < high:
        raise StageFailure('response bounds must be increasing')
    params = {
        'header': {'ref': header[2], 'pins': count, 'nets': {p: header_nets[p] for p in sorted(header_nets)},
                   'assembly': 'not_assembled' if found['hand_soldered'][2] == 'hand-soldered' else 'assembled',
                   'assembly_reason': 'through-hole header, ' + found['hand_soldered'][2]},
        'resistor': {'ref': res[3], 'value': res[1] + res[2], 'ohms': float(res[1]) * SI[res[2]],
                     'nets': [net_in, net_out]},
        'capacitor': {'ref': cap[3], 'value': cap[1] + cap[2], 'farads': float(cap[1]) * SI[cap[2]],
                      'nets': [net_out, ground]},
        'nets': {'input': net_in, 'output': net_out, 'ground': ground},
        'step_v': float(step[1]),
        'response': {'net': net_out, 'at_ms': float(response[2]), 'min_v': low, 'max_v': high},
        'min_track_width_mm': float(found['track_width'][1]),
        'board': {'copper_layers': int(board[1]), 'width_mm': float(board[2]), 'height_mm': float(board[3]),
                  'edge_ref': board[4], 'edge': 'left'},
    }
    tau_ms = round(params['resistor']['ohms'] * params['capacitor']['farads'] * 1e3, 9)
    params['tau_ms'] = tau_ms
    params['target_v'] = params['step_v'] * (1 - math.exp(-params['response']['at_ms'] / tau_ms))
    params['sources'] = {key: {'text': match[0], 'start': match.start(), 'end': match.end()}
                         for key, match in found.items()}
    return params


def requirement_texts(p):
    width, step, r = fmt(p['min_track_width_mm']), fmt(p['step_v']), p['response']
    return {
        'R-native': ('The generated design must pass declared ERC and DRC checks, and its netlist export '
                     'must complete. Export establishes connectivity evidence, not analog behavior.'),
        'R-width': (f'Saved copper tracks must be at least {width} mm wide. This geometric criterion does '
                    'not establish ampacity or fabrication qualification.'),
        'R-response': (f"With an ideal unloaded {step} V step at {p['nets']['input']}, {r['net']} at "
                       f"{fmt(r['at_ms'])} ms must be between {fmt(r['min_v'])} and {fmt(r['max_v'])} V "
                       'inclusive. This does not establish temperature, tolerance, loaded response, or '
                       'physical prototype performance.'),
    }


def requirement_files(p, spec_sha256):
    texts = requirement_texts(p)
    system = ('# Rung-1 generated baseline\n\nCompiled by `agent_ee.generate` stage 1 from `spec.md` '
              f'(sha256 `{spec_sha256}`).\n\n' + '\n\n'.join(f'{k}: {v}' for k, v in texts.items()) + '\n')
    r = p['response']
    targets = ('# Target specification\n\n'
               f"Analytical target at {fmt(r['at_ms'])} ms: {p['target_v']:.4f} V "
               f"(tau = {fmt(round(p['tau_ms'], 9))} ms); acceptance bounds are {fmt(r['min_v'])}-{fmt(r['max_v'])} V. "
               f"A {fmt(max(5 * p['tau_ms'], r['at_ms']))} ms sample is retained as information. Native rule checks "
               f"and a minimum {fmt(p['min_track_width_mm'])} mm track width also apply.\n")
    reference = ('# Reference and model applicability\n\n'
                 f"The reference architecture is a passive first-order low-pass, nominal R={p['resistor']['value']}ohm "
                 f"and C={p['capacitor']['value']}F, driven by an ideal voltage step with no output load. The analytical "
                 'reference is Vout(t)=Vin*(1-exp(-t/(R*C))). Native ngspice verification uses the captured populated '
                 'passive components. It must not be extrapolated to loaded, nonlinear or temperature-dependent use.\n')
    return {'system.md': system, 'targets.md': targets, 'reference.md': reference}


def compile_scope(template, p, name, design='../design', requirements='../requirements'):
    """scope/2 from the closed-loop control template, pointed at the generated project."""
    scope = json.loads(json.dumps(template))
    texts = requirement_texts(p)
    r = p['response']
    stop_ms = max(5 * p['tau_ms'], r['at_ms'])
    scope['name'] = 'Rung-1 generated RC low-pass'
    scope['scope'] = ('Review the RC low-pass board generated by agent_ee.generate (rung 1) against the '
                      'requirements compiled from its one-paragraph spec. This is a disposable benchmark board.')
    scope['limitations'] = [scope['limitations'][0],
                            'Footprints and LCSC numbers come from the captured fome-mc BOM, not a supplier '
                            'snapshot; stock, Basic/Extended status and part tolerances are not verified.']
    inputs = []
    for item in template['inputs']:
        if item['at'] == 'project':
            item = {'path': design, 'at': 'project'}
        elif item['at'] == 'requirements':
            item = {'path': requirements, 'at': 'requirements'}
        inputs.append(item)
    scope['inputs'] = inputs
    scope['schematic'] = f'project/{name}.kicad_sch'
    scope['board'] = f'project/{name}.kicad_pcb'
    scope['requirements'] = ['requirements/spec.md', 'requirements/system.md']
    for check in scope['checks']:
        if check['kind'] == 'geometry':
            check['params'] = {'min_track_width_mm': p['min_track_width_mm']}
        if check['kind'] == 'rc_transient':
            check['params'].update(input_net=p['nets']['input'], output_net=p['nets']['output'],
                                   ground_net=p['nets']['ground'], step_v=p['step_v'], stop_ms=stop_ms)
            check['params']['measurements'] = [
                {'name': 'vout_' + fmt(r['at_ms']).replace('.', 'p') + 'ms', 'at_ms': r['at_ms'],
                 'min': r['min_v'], 'max': r['max_v']},
                {'name': 'vout_' + fmt(stop_ms).replace('.', 'p') + 'ms', 'at_ms': stop_ms}]
    statements = {'R-native': 'Native rule checks pass and netlist exports',
                  'R-width': f"Tracks at least {fmt(p['min_track_width_mm'])} mm",
                  'R-response': (f"Unloaded {fmt(p['step_v'])} V step response at {fmt(r['at_ms'])} ms in "
                                 f"[{fmt(r['min_v'])},{fmt(r['max_v'])}] V")}
    for requirement in scope['baseline']['requirements']:
        if requirement['id'] not in texts:
            raise StageFailure('scope template requirement without a spec source: ' + requirement['id'])
        requirement['statement'] = statements[requirement['id']]
    return scope


def stage_requirements(stage, state, options):
    from .design_dialog import capture_dialog
    from .design_intent import import_record, load_json, write_artifacts
    spec_path = Path(options['spec'])
    raw = spec_path.read_bytes()
    stage.input(spec_path, 'spec')
    shutil.copyfile(spec_path, stage.dir / 'spec.md')
    stage.output(stage.dir / 'spec.md', 'spec copy')
    text = raw.decode('utf-8')
    paragraph = ' '.join(line.strip() for line in text.splitlines() if line.strip() and not line.startswith('#'))
    params = parse_spec(text)
    params_record = {'schema': PARAMETERS_SCHEMA, 'qualification': QUALIFICATION, 'spec_sha256': sha256_bytes(raw), **params}
    write_json(stage.dir / 'parameters.json', params_record)
    stage.output(stage.dir / 'parameters.json', 'parameters extracted from the spec with source spans')
    # Intent record (design_intent.import_record) authored from the spec.
    record = {'id': 'rung1-rc-lowpass', 'task': paragraph,
              'input_nodes': [params['nets']['input'], params['nets']['ground']],
              'output_nodes': [params['nets']['output'], params['nets']['ground']],
              'input_voltage': fmt(params['step_v']), 'output_voltage': f"{params['target_v']:.4f}",
              'components': {'Connector_Generic:Conn_01x%02d' % params['header']['pins']: 1,
                             'Device:C': 1, 'Device:R': 1},
              'sub_module_name': 'rc_lowpass'}
    record_raw = canonical(record) + b'\n'
    (stage.dir / 'intent-record.json').write_bytes(record_raw)
    intent = import_record(record_raw)
    write_artifacts(intent, stage.dir / 'intent')
    stage.output(stage.dir / 'intent-record.json', 'intent record')
    stage.output(stage.dir / 'intent', 'design_intent.import_record outputs')
    intent_report = load_json(intent['intent.json'])
    stage.receipt['details']['intent_outcome'] = intent_report['outcome']
    if intent_report['outcome'] != 'pass':
        raise StageFailure('intent lint did not pass: ' + json.dumps(intent_report['findings']))
    # Owner dialog: the spec decides everything, so the question batch is empty.
    originals = json.loads(intent['requirements.json'])['requirements']
    originals += [{'id': rid, 'text': text, 'source_question_ids': []}
                  for rid, text in requirement_texts(params).items()]
    requirements_raw = canonical({'schema': 'agent-ee.requirements/1', 'requirements': originals}) + b'\n'
    questions_raw = canonical({'schema': 'agent-ee.questions/1', 'questions': [],
                               'context': 'Rung-1 benchmark: the spec fixes every decision; no owner question is open.'}) + b'\n'
    answers_raw = canonical({'schema': 'agent-ee.answers/1', 'answers': {},
                             'questions_sha256': sha256_bytes(questions_raw), 'idea_sha256': sha256_bytes(raw),
                             'requirements_sha256': sha256_bytes(requirements_raw)}) + b'\n'
    dialog = capture_dialog(raw, questions_raw, answers_raw, requirements_raw)
    write_artifacts(dialog, stage.dir / 'dialog')
    stage.output(stage.dir / 'dialog', 'design_dialog.capture_dialog outputs')
    report = load_json(dialog['dialog.json'])
    stage.receipt['details']['dialog_outcome'] = report['outcome']
    if report['outcome'] != 'pass':
        raise StageFailure('dialog capture did not pass: ' + json.dumps(report['findings']))
    # Requirements and scope/2 for the generated project (relative paths survive copying RUNDIR).
    requirements = stage.rundir / 'requirements'
    requirements.mkdir()
    shutil.copyfile(spec_path, requirements / 'spec.md')
    for name, body in requirement_files(params, sha256_bytes(raw)).items():
        (requirements / name).write_text(body)
    template = json.loads(SCOPE_TEMPLATE.read_text())
    stage.input(SCOPE_TEMPLATE, 'scope/2 template')
    scope = compile_scope(template, params, PROJECT_NAME)
    scope_path = stage.rundir / 'scope' / 'rung1.scope.json'
    write_json(scope_path, scope)
    stage.output(requirements, 'scope requirements')
    stage.output(scope_path, 'compiled scope/2')
    stage.receipt['details'].update(project_name=PROJECT_NAME, requirement_ids=[r['id'] for r in originals],
                                    target_v=round(params['target_v'], 6))
    stage.receipt['limitations'].append(
        'The spec parser extracts parameters of the rung-1 RC template; it is not a general requirements compiler.')
    state.update(params=params, scope=scope_path, name=PROJECT_NAME)


# ------------------------------------------------------------------ stage 2: parts

def capture_catalog(checkout=None):
    """The fome-mc BOM as committed at the checkout's HEAD; working-tree edits are not used.

    Another session may be editing fome-mc. Reading the committed blob gives exact
    provenance (commit + sha256); a differing working copy is recorded, never read.
    """
    checkout = Path(checkout or CATALOG_CHECKOUT)
    path = checkout / CATALOG_PATH
    def git(*args):
        proc = subprocess.run(['git', '-C', str(checkout), *args], capture_output=True, timeout=60)
        if proc.returncode:
            raise StageFailure('catalog provenance unavailable: ' + proc.stderr.decode(errors='replace').strip())
        return proc.stdout
    head = git('rev-parse', 'HEAD').decode().strip()
    raw = git('show', f'{head}:{CATALOG_PATH}')
    commit = git('log', '-1', '--format=%H', head, '--', CATALOG_PATH).decode().strip()
    working = path.read_bytes() if path.is_file() else None
    source = {'path': str(path), 'repository_path': CATALOG_PATH, 'read_from': f'git blob {head}:{CATALOG_PATH}',
              'commit': commit, 'checkout_head': head, 'remote': git('remote', 'get-url', 'origin').decode().strip(),
              'sha256': sha256_bytes(raw), 'bytes': len(raw),
              'working_tree_differs': working != raw,
              'working_tree_sha256': sha256_bytes(working) if working is not None else None,
              'note': "owner's own fome-mc design BOM; copied into the run directory only"}
    return path, raw, source


def read_catalog(raw):
    rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8'))))
    if not rows or list(rows[0]) != CATALOG_COLUMNS:
        raise StageFailure('catalog columns differ from ' + ','.join(CATALOG_COLUMNS))
    return rows


def select_part(rows, lib_id, value):
    """Deterministic choice among exact-value rows of the symbol's chip family."""
    family = FAMILIES[lib_id]
    default = re.fullmatch(family['pattern'], family['default'])[1]
    candidates, rejected = [], []
    for index, row in enumerate(rows, start=2):
        if row['Comment'] != value:
            continue
        entry = {'catalog_line': index, 'footprint': row['Footprint'], 'lcsc': row['LCSC Part #'],
                 'quantity': row['Quantity']}
        match = re.fullmatch(family['pattern'], row['Footprint'])
        if not match:
            rejected.append({**entry, 'reason': 'footprint is not a two-terminal chip of the symbol family'})
        elif match[1] not in CHIP_SIZES:
            rejected.append({**entry, 'reason': 'unknown chip size ' + match[1]})
        elif not re.fullmatch(r'C\d+', row['LCSC Part #']):
            rejected.append({**entry, 'reason': 'no LCSC part number'})
        elif not row['Quantity'].isdigit():
            rejected.append({**entry, 'reason': 'quantity is not an integer'})
        else:
            size = CHIP_SIZES.index(match[1])
            key = [abs(size - CHIP_SIZES.index(default)), -size, -int(row['Quantity']), int(row['LCSC Part #'][1:])]
            candidates.append({**entry, 'package': match[1], 'rank_key': key})
    candidates.sort(key=lambda c: c['rank_key'])
    if not candidates:
        raise StageFailure(f'no catalog row for {lib_id} value {value}; rejected: {json.dumps(rejected)}')
    chosen = candidates[0]
    for other in candidates[1:]:
        rejected.append({k: v for k, v in other.items() if k != 'rank_key'} |
                        {'reason': 'ranked below the chosen row', 'rank_key': other['rank_key']})
    return chosen, rejected, default


def footprint_file(footprint):
    library, name = footprint.split(':')
    path = STOCK / 'footprints' / f'{library}.pretty' / f'{name}.kicad_mod'
    if not path.is_file():
        raise StageFailure('footprint not in the stock library: ' + footprint)
    pads = sorted(set(re.findall(r'\(pad "([^"]*)"', path.read_text())) - {''})
    return path, pads


def stage_parts(stage, state, options):
    if options.get('parts_manifest'):
        from .rung1_parts import stage_parts as captured_parts
        return captured_parts(stage, state, options['parts_manifest'])
    params = state['params']
    if params['header']['assembly'] == 'assembled':
        raise StageFailure('assembled header requires a captured parts/1 manifest')
    path, raw, source = capture_catalog(options.get('catalog_checkout'))
    rows = read_catalog(raw)
    (stage.dir / 'catalog').mkdir()
    copy = stage.dir / 'catalog' / path.name
    copy.write_bytes(raw)
    stage.input(copy, 'fome-mc BOM catalog (committed blob, copied)')
    stage.receipt['details']['catalog'] = source
    components = [('Device:R', params['resistor']), ('Device:C', params['capacitor'])]
    parts, selections = {}, {}
    for lib_id, part in components:
        chosen, rejected, default = select_part(rows, lib_id, part['value'])
        path, pads = footprint_file(chosen['footprint'])
        if pads != ['1', '2']:
            raise StageFailure(f"{chosen['footprint']} pads {pads} do not match the two-pin symbol")
        parts[part['ref']] = {'Footprint': chosen['footprint'], 'LCSC': chosen['lcsc']}
        selections[part['ref']] = {
            'lib_id': lib_id, 'value': part['value'], 'default_package': default,
            'chosen': chosen, 'rejected': rejected,
            'footprint_file': {'path': str(path), 'sha256': digest(path), 'pads': pads},
            'package_note': ('catalog package differs from the fixture default ' + default
                             if chosen['package'] != default else 'fixture default package')}
    header = params['header']
    value = 'Conn_01x%02d' % header['pins']
    datasheet_column = next((c for c in CATALOG_COLUMNS if c.lower() == 'datasheet'), None)
    footprint = 'Connector_PinHeader_2.54mm:PinHeader_1x%02d_P2.54mm_Vertical' % header['pins']
    catalog_rows = [i for i, row in enumerate(rows, start=2) if row['Comment'] == value]
    if catalog_rows:
        raise StageFailure(f'{value} has catalog rows {catalog_rows}; the not_assembled rule would hide them')
    path, pads = footprint_file(footprint)
    if pads != [str(n) for n in range(1, header['pins'] + 1)]:
        raise StageFailure(f'{footprint} pads {pads} do not match the header pins')
    parts[header['ref']] = {'Footprint': footprint, 'JLCPCB_Assembly': 'not_assembled'}
    selections[header['ref']] = {
        'lib_id': 'Connector_Generic:' + value, 'value': value, 'chosen': None,
        'rejected': [], 'catalog_rows': [], 'reason': header['assembly_reason'],
        'footprint_file': {'path': str(path), 'sha256': digest(path), 'pads': pads}}
    for ref, selection in selections.items():
        line = selection['chosen']['catalog_line'] if selection['chosen'] else None
        # Stable identity of this selection for downstream evidence (AGENTEE-33 datasheet lane).
        selection['selection_id'] = sha256_bytes(canonical({
            'catalog_sha256': source['sha256'], 'catalog_line': line, 'lib_id': selection['lib_id'],
            'value': selection['value'], 'reference': ref}))
        # The datasheet obligation stays explicit; the emitter ignores a null Datasheet.
        if datasheet_column and line is not None:
            parts[ref]['Datasheet'] = rows[line - 2][datasheet_column]
        else:
            parts[ref]['Datasheet'] = None
            parts[ref]['datasheet_obligation'] = 'missing:catalog_has_no_datasheet_column'
    manifest = {
        'schema': PARTS_SCHEMA, 'qualification': QUALIFICATION,
        'catalog': {'path': stage.rel(copy), 'sha256': source['sha256'], 'source': source,
                    'columns': CATALOG_COLUMNS},
        'policy': {'value_match': 'catalog Comment equals the intent value exactly',
                   'families': {k: v['pattern'] for k, v in FAMILIES.items()},
                   'rank': ['distance from the fixture default chip size', 'larger chip first on equal distance',
                            'catalog quantity descending (most used in fome-mc)', 'LCSC number ascending'],
                   'not_assembled': 'parts the spec names hand-soldered; they have no catalog row and are '
                                    'excluded from the JLCPCB BOM and CPL by rule'},
        'parts': parts, 'selections': selections,
        'assembly_exclusions': [{'ref': header['ref'], 'rule': 'JLCPCB_Assembly=not_assembled',
                                 'reason': header['assembly_reason']}],
        'unknown_fields': {'MPN': 'catalog has no MPN column; omitted, never invented',
                           'Manufacturer': 'catalog has no manufacturer column; omitted, never invented',
                           'LCSC': 'omitted for not_assembled parts, which have no catalog row'}}
    path = stage.dir / 'parts-manifest.json'
    write_json(path, manifest)
    stage.output(stage.dir / 'catalog', 'catalog copy')
    stage.output(path, 'parts manifest')
    # Capture phase (AGENTEE-33): each unique Datasheet URL is fetched once here; checks never fetch.
    from .datasheet_capture import capture_datasheets
    datasheets = stage.dir / 'datasheets'
    capture = capture_datasheets([{'id': ref, 'datasheet_url': fields['Datasheet']} for ref, fields in parts.items()],
                                 datasheets)
    stage.output(datasheets, 'datasheet acquisition (captured PDFs and manifest)')
    missing = [p['id'] for p in capture['parts'] if p['missing']]
    stage.receipt['details']['datasheets'] = {
        'manifest': stage.rel(datasheets / 'manifest.json'), 'outcome': capture['outcome'],
        'captured': {p['id']: {'url': p['url'], 'sha256': p['sha256'], 'fetched_at': p['fetched_at']}
                     for p in capture['parts'] if not p['missing']},
        'missing': {p['id']: p['reason'] for p in capture['parts'] if p['missing']}}
    if missing:
        stage.receipt['limitations'].append(
            'No datasheet PDF for ' + ', '.join(missing) + '; datasheet_facts and pin_map coverage for these '
            'parts is unknown (missing), never assumed.')
    state['datasheets'] = datasheets
    stage.receipt['details']['parts'] = {ref: {k: v for k, v in fields.items() if v} for ref, fields in parts.items()}
    stage.receipt['limitations'].append('The catalog is a design BOM without stock, price or tolerance data; '
                                        'qualified selection is AGENTEE-28 on a supplier snapshot (AGENTEE-27).')
    state['parts'] = path


# ------------------------------------------------------------------ stage 3: schematic

def connectivity_intent(params, parts, libraries):
    header, res, cap = params['header'], params['resistor'], params['capacitor']
    def component(ref, lib_id, value):
        return {'id': ref, 'reference': ref, 'lib_id': lib_id, 'value': value, 'units': [1],
                'footprint': parts[ref]['Footprint'],
                'part_binding': {k: v for k, v in parts[ref].items() if k != 'Footprint' and v is not None}}
    terminals = {}
    for pin, net in header['nets'].items():
        terminals.setdefault(net, []).append((header['ref'], pin))
    for part in (res, cap):
        for pin, net in enumerate(part['nets'], start=1):
            terminals.setdefault(net, []).append((part['ref'], str(pin)))
    return {
        'schema': 'agent-ee.connectivity-intent/1', 'libraries': libraries,
        'components': [component(header['ref'], 'Connector_Generic:Conn_01x%02d' % header['pins'],
                                 'Conn_01x%02d' % header['pins']),
                       component(res['ref'], 'Device:R', res['value']),
                       component(cap['ref'], 'Device:C', cap['value'])],
        'nets': [{'id': name.strip('/'), 'name': name,
                  'terminals': [{'component': ref, 'pin': {'number': pin}} for ref, pin in sorted(items)]}
                 for name, items in sorted(terminals.items())],
        'no_connects': []}


def expected_nets(intent):
    return {net['name']: sorted(f"{t['component']}.{t['pin']['number']}" for t in net['terminals'])
            for net in intent['nets']}


def native_candidate(root, project, footprint_libraries=()):
    """A read-only sandbox view: project/ plus captured stock tables and libraries."""
    copy_tree(project, root / 'project')
    for library in sorted(set(footprint_libraries)):
        shutil.copytree(STOCK / 'footprints' / f'{library}.pretty',
                        root / 'stock' / 'footprints' / f'{library}.pretty')
    (root / 'stock' / 'template').mkdir(parents=True)
    for table in ('sym-lib-table', 'fp-lib-table'):
        shutil.copyfile(STOCK / 'template' / table, root / 'stock' / 'template' / table)
    (root / 'stock' / 'symbols').mkdir()
    for library in SYMBOL_LIBRARIES:
        shutil.copyfile(STOCK / 'symbols' / f'{library}.kicad_sym', root / 'stock' / 'symbols' / f'{library}.kicad_sym')
    return root


def seed_kicad_config(work, candidate):
    config = Path(work) / 'kicad-config' / '10.0'
    config.mkdir(parents=True, exist_ok=True)
    for table in ('sym-lib-table', 'fp-lib-table'):
        shutil.copyfile(Path(candidate) / 'stock' / 'template' / table, config / table)


def schematic_checks(stage, project, name, intent, strict=True):
    """Native ERC (zero violations) and XML netlist equal to the intent's nets."""
    from .checks import parse_xml_netlist
    stage.tool('kicad-cli')
    candidate = native_candidate(stage.dir / 'check-candidate', project,
                                 [c['footprint'].split(':')[0] for c in intent['components']])
    sch = f'/candidate/project/{name}.kicad_sch'
    work = stage.dir / 'erc'
    seed_kicad_config(work, candidate)
    stage.sandboxed(candidate, work, ['kicad-cli', 'sch', 'erc', '--format', 'json', '--severity-all',
                                      '--units', 'mm', '-o', '/work/erc.json', sch], timeout=600, label='erc')
    erc = json.loads((work / 'erc.json').read_text())
    violations = [v for sheet in erc['sheets'] for v in sheet.get('violations', [])]
    stage.output(work / 'erc.json', 'native ERC report')
    work = stage.dir / 'netlist'
    seed_kicad_config(work, candidate)
    stage.sandboxed(candidate, work, ['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                                      '-o', '/work/design.xml', sch], timeout=600, label='netlist')
    netlist = work / 'design.xml'
    if not netlist.is_file() or not netlist.stat().st_size:
        raise StageFailure('kicad-cli wrote no XML netlist')
    stage.output(netlist, 'native XML netlist')
    parsed = parse_xml_netlist(netlist)
    expected = expected_nets(intent)
    components = {c['reference']: {'value': c['value'], 'footprint': c['footprint']} for c in intent['components']}
    actual_components = {ref: {'value': c['value'], 'footprint': c['footprint']} for ref, c in parsed['components'].items()}
    details = {'erc_violations': len(violations), 'netlist_nets': parsed['nets'], 'intent_nets': expected,
               'nets_equal': parsed['nets'] == expected, 'components_equal': actual_components == components}
    stage.receipt['details'].update(details)
    if strict:
        if violations:
            raise StageFailure(f'ERC reported {len(violations)} violations')
        if parsed['nets'] != expected:
            raise StageFailure('native netlist nets differ from the connectivity intent')
        if actual_components != components:
            raise StageFailure(f'native components {actual_components} differ from intent {components}')
    return netlist


def tool_reason(work):
    """The last informative lines a failed tool printed (KiCad debug noise removed)."""
    lines = []
    for name in ('stdout.log', 'stderr.log'):
        path = Path(work) / name
        if path.is_file():
            lines += [line for line in path.read_text(errors='replace').splitlines()
                      if line.strip() and 'Debug:' not in line and 'assert "' not in line]
    return ' | '.join(lines[-2:])[-1500:]


def stage_schematic(stage, state, options):
    from .connectivity_ir import canonical_resolved, resolve_intent, validate_resolved
    params, name = state['params'], state['name']
    manifest = json.loads(state['parts'].read_text())
    stage.input(state['parts'], 'parts manifest')
    libraries_dir = stage.dir / 'libraries'
    libraries_dir.mkdir()
    libraries, captured = [], {}
    for library in SYMBOL_LIBRARIES:
        source = STOCK / 'symbols' / f'{library}.kicad_sym'
        raw = source.read_bytes()
        (libraries_dir / source.name).write_bytes(raw)
        libraries.append({'name': library, 'path': source.name, 'sha256': sha256_bytes(raw)})
        captured[source.name] = raw
        stage.input(source, 'stock symbol library')
    intent = connectivity_intent(params, manifest['parts'], libraries)
    write_json(stage.dir / 'intent.json', intent)
    stage.output(stage.dir / 'intent.json', 'connectivity intent')
    resolved = resolve_intent(intent, captured)
    (stage.dir / 'resolved.json').write_bytes(canonical_resolved(resolved) + b'\n')
    stage.output(stage.dir / 'resolved.json', 'resolved connectivity')
    stage.receipt['details']['resolution'] = {k: resolved[k] for k in ('execution', 'outcome', 'coverage', 'findings')}
    try:
        validate_resolved(resolved)
    except ValueError as exc:
        raise StageFailure('connectivity intent did not resolve: ' + str(exc))
    if not (PACKAGE_ROOT / 'agent_ee' / 'schematic_emit.py').is_file():
        if not options.get('stand_in'):
            raise DependencyMissing('agent_ee.schematic_emit (G1) is not in this checkout')
        project = copy_tree(FIXTURE_PROJECT, stage.dir / 'stand-in' / 'project')
        state['name'] = 'rc_filter'
        stage.receipt['details']['stand_in'] = str(FIXTURE_PROJECT)
        stage.receipt['limitations'].append('G1 missing: the fixture project stands in for the emitted schematic')
        state['netlist'] = schematic_checks(stage, project, 'rc_filter', intent, strict=False)
        state['project'] = project
        state['stand_in_schematic'] = True
        raise DependencyMissing('agent_ee.schematic_emit (G1) is not in this checkout; fixture stand-in used')
    stage.tool('/usr/bin/python3')
    stage.tool('bwrap')
    inputs = stage.dir / 'emit-inputs'
    inputs.mkdir()
    shutil.copyfile(stage.dir / 'resolved.json', inputs / 'resolved.json')
    shutil.copytree(libraries_dir, inputs / 'libraries')
    shutil.copyfile(state['parts'], inputs / 'parts-manifest.json')
    shutil.copyfile(FIXTURE_PROJECT / 'rc_filter.kicad_pro', inputs / 'template.kicad_pro')
    stage.input(FIXTURE_PROJECT / 'rc_filter.kicad_pro', 'project template')
    work = stage.dir / 'emit'
    result = stage.sandboxed(inputs, work, [
        '/usr/bin/python3', '-m', 'agent_ee.schematic_emit', '/candidate/resolved.json',
        '--libraries', '/candidate/libraries', '--parts', '/candidate/parts-manifest.json',
        '--project-template', '/candidate/template.kicad_pro', '--name', name, '--out', '/work/project'],
        timeout=600, label='schematic_emit')
    if result['exit_code'] != 0:
        raise StageFailure(f"schematic_emit exited {result['exit_code']}: {tool_reason(work)}")
    project = work / 'project'
    stage.output(project, 'emitted project')
    emit_receipt = project / 'emit-receipt.json'
    if emit_receipt.is_file():
        emitted = json.loads(emit_receipt.read_text())
        stage.receipt['details']['emit_receipt'] = emitted
        stage.receipt['details']['power_flags'] = emitted.get('power_flags')
    state['netlist'] = schematic_checks(stage, project, name, intent)
    state['project'] = project


# ------------------------------------------------------------------ stage 4: placement

def envelope(params):
    board = params['board']
    x, y = 100.0, 100.0
    pitch = 2.54
    # board_build's envelope contract has exact keys, so the experimental marker is the schema name.
    return {'schema': ENVELOPE_SCHEMA,
            'outline_mm': {'x': x, 'y': y, 'w': board['width_mm'], 'h': board['height_mm']},
            'copper_layers': board['copper_layers'], 'stackup': 'template',
            # Pin 1 is the footprint origin; the column of pins is centred vertically, 2.54 mm in from the edge.
            'fixed_refs': [{'ref': params['header']['ref'], 'x_mm': x + pitch,
                            'y_mm': round(y + board['height_mm'] / 2 - pitch * (params['header']['pins'] - 1) / 2, 4),
                            'rotation_deg': 0, 'side': 'F'}]}


def packing_policy(params):
    return {'schema': 'agent-ee.packing-policy/1', 'step_budget': 200000, 'gap_nm': 1_000_000,
            'fixed_refs': [params['header']['ref']], 'priority_refs': [],
            'ground_nets': [params['nets']['ground']], 'obstacles': []}


BOARD_BUILD_EXITS = {1: 'input refused', 2: 'not placed', 3: 'placed with judge or parity defects',
                     4: 'placement writer failed', 5: 'native tool failed'}


def stage_placement(stage, state, options):
    params, name = state['params'], state['name']
    if not (PACKAGE_ROOT / 'agent_ee' / 'board_build.py').is_file() or state.get('stand_in_schematic'):
        if not options.get('stand_in'):
            raise DependencyMissing('agent_ee.board_build (G2) is not in this checkout')
        placed = copy_tree(FIXTURE_PROJECT, stage.dir / 'stand-in' / 'placed')
        state['name'] = 'rc_filter'
        state['placed'] = placed
        stage.receipt['details']['stand_in'] = str(FIXTURE_PROJECT)
        raise DependencyMissing('agent_ee.board_build (G2) or its schematic input is missing; '
                                'the routed fixture board stands in')
    inputs = stage.dir / 'build-inputs'
    inputs.mkdir()
    copy_tree(state['project'], inputs / 'project')
    shutil.copyfile(state['netlist'], inputs / 'netlist.xml')
    manifest = json.loads(state['parts'].read_text())
    # The project's fp-lib-table resolves ${KICAD10_FOOTPRINT_DIR}, which the executor
    # sets to /candidate/stock/footprints; the pinned libraries live there.
    for library in sorted({fields['Footprint'].split(':')[0] for fields in manifest['parts'].values()}):
        shutil.copytree(STOCK / 'footprints' / f'{library}.pretty',
                        inputs / 'stock' / 'footprints' / f'{library}.pretty')
    (inputs / 'stock' / 'template').mkdir()
    for table in ('sym-lib-table', 'fp-lib-table'):
        shutil.copyfile(STOCK / 'template' / table, inputs / 'stock' / 'template' / table)
    shutil.copytree(stage.rundir / 'stages' / '3-schematic' / 'libraries', inputs / 'stock' / 'symbols')
    write_json(inputs / 'envelope.json', envelope(params))
    write_json(inputs / 'placement-request.json', {
        'policy': packing_policy(params),
        # board_build binds source_sha256 and writable_root to the board it builds.
        'authorization': {'schema': 'agent-ee.placement-write/1', 'single_design_writer': True}})
    # The template board's rules come with its project file, beside it under the same stem.
    shutil.copyfile(FIXTURE_PROJECT / 'rc_filter.kicad_pcb', inputs / 'template.kicad_pcb')
    shutil.copyfile(FIXTURE_PROJECT / 'rc_filter.kicad_pro', inputs / 'template.kicad_pro')
    stage.input(inputs, 'board build inputs')
    for tool in ('/usr/bin/python3', 'pcbnew', 'kicad-cli', 'bwrap'):
        stage.tool(tool)
    work = stage.dir / 'build'
    argv = ['/usr/bin/python3', '-m', 'agent_ee.board_build', '/candidate/project',
            '--netlist', '/candidate/netlist.xml', '--footprints', '/candidate/stock/footprints',
            '--envelope', '/candidate/envelope.json', '--template-board', '/candidate/template.kicad_pcb',
            '--placement-request', '/candidate/placement-request.json', '--out', '/work/out']
    # The landed placer refuses stock pad shapes (AGENTEE-109); board_build's experimental
    # row placement is requested explicitly when it exists, and its receipt says so.
    if '--placement-fallback' in (PACKAGE_ROOT / 'agent_ee' / 'board_build.py').read_text():
        argv += ['--placement-fallback', 'row']
    result = stage.sandboxed(inputs, work, argv, timeout=1200, label='board_build')
    out = work / 'out'
    receipt = out / 'build-receipt.json'
    if receipt.is_file():
        # Carried over verbatim: placer identity, fallback reason, judgment and parity.
        build = json.loads(receipt.read_text())
        stage.receipt['details']['build_receipt'] = build
        for key in ('placer', 'placer_reason', 'judge_verdict'):
            stage.receipt['details'][key] = build.get(key)
        stage.receipt['details']['build_receipt_sha256'] = digest(receipt)
        stage.output(out, 'board build outputs')
    if result['exit_code'] != 0:
        meaning = BOARD_BUILD_EXITS.get(result['exit_code'], 'unexpected exit')
        raise StageFailure(f"board_build exited {result['exit_code']} ({meaning}): {tool_reason(work)}")
    placed = out / 'placed'
    if not (placed / f'{name}.kicad_pcb').is_file():
        raise StageFailure('board_build produced no placed board')
    state['placed'] = placed
    state['placer'] = stage.receipt['details'].get('build_receipt', {}).get('placer')


# ------------------------------------------------------------------ stage 5: routing

def stage_routing(stage, state, options):
    name = state['name']
    project = copy_tree(state['placed'], stage.dir / 'input' / name)
    stage.input(project, 'placed project')
    stage.tool('kicad-route')
    stage.tool('kicad-cli')
    out = stage.dir / 'out'
    command = stage.run(['kicad-route', '--out', str(out), 'run', '--backend', 'krt', '--budget', '300'],
                        cwd=project, timeout=1500, label='kicad-route')
    # kicad-route also links runs/<project>/<label>/latest to the stamp directory.
    results = sorted(p for p in out.glob('runs/*/*/*/result.json') if not p.parent.is_symlink())
    stage.receipt['details']['kicad_route_exit'] = command['exit_code']
    stage.receipt['limitations'].append(
        'kicad-route runs as an unsandboxed process: its state directory is not yet read-only mounted '
        '(KICADROUTE-6).')
    if len(results) != 1:
        raise StageFailure(f'expected one kicad-route result.json, found {len(results)}')
    result = json.loads(results[0].read_text())
    stage.output(results[0], 'kicad-route result.json')
    stage.receipt['details'].update(
        result_json=stage.rel(results[0]), backend=result.get('backend'),
        process=result.get('process', {}).get('status'), routing=result.get('routing', {}).get('status'),
        routing_stats=result.get('routing', {}).get('stats'), validation=result.get('validation', {}).get('status'),
        validation_reasons=result.get('validation', {}).get('reasons'), input_fingerprint=result.get('input', {}).get('fingerprint'),
        tool_versions=result.get('tool_versions'))
    if command['exit_code'] != 0:
        raise StageFailure(f"kicad-route exited {command['exit_code']} (0 means complete and valid)")
    candidate = out / result['candidate']['dir']
    design = stage.rundir / 'design'
    copy_tree(candidate, design)
    stage.output(design, 'routed candidate project')
    stage.receipt['details']['candidate_sha256'] = digest(design / f'{name}.kicad_pcb')
    state['design'] = design


# ------------------------------------------------------------------ stage 6: verification

def agent_ee_argv(*args):
    return [sys.executable, str(REPO_ROOT / 'bin' / 'agent-ee'), *map(str, args)]


def campaign_config(scope_path, profile, max_seconds=2400, max_iterations=3):
    adjudicator = profile['adjudicator']
    return {'schema': 'agent-ee.campaign/1', 'scope': str(Path(scope_path).absolute()),
            'review_profile': profile,
            'implementation': {k: adjudicator[k] for k in ('harness', 'model', 'effort', 'timeout_s')},
            'max_iterations': max_iterations, 'max_seconds': max_seconds}


def summarize_run(run):
    result = json.loads((run / 'result.json').read_text())
    checks = {c.get('id'): {'execution': c.get('execution'), 'outcome': c.get('outcome'), 'summary': c.get('summary')}
              for c in result.get('checks') or []}
    # Verbatim projection of each finding's identity and disposition; nothing is reinterpreted.
    findings = [{key: f.get(key) for key in ('id', 'role', 'source', 'severity', 'assessment', 'title',
                                             'status', 'disposition', 'disposition_reason', 'decision')}
                for f in result.get('findings') or []]
    return result, checks, findings


def stage_review(stage, state, options):
    stage.tool('agent_ee')
    for tool in ('kicad-cli', 'ngspice', 'bwrap', '/usr/bin/python3', 'pcbnew'):
        stage.tool(tool)
    scope_path = Path(state['scope'])
    scope = json.loads(scope_path.read_text())
    if scope['schematic'] != f"project/{state['name']}.kicad_sch":
        # Fixture stand-in only: the project kept its own name; the scope is re-pointed, not edited.
        scope['schematic'] = f"project/{state['name']}.kicad_sch"
        scope['board'] = f"project/{state['name']}.kicad_pcb"
        scope_path = scope_path.with_name('rung1.stand-in.scope.json')
        write_json(scope_path, scope)
        stage.receipt['limitations'].append('stand-in project: scope re-pointed at ' + state['name'])
        state['scope'] = scope_path
    stage.input(state['scope'], 'scope/2')
    if options.get('skip_review'):
        run = stage.dir / 'review'
        command = stage.run(agent_ee_argv('review', state['scope'], '--out', run, '--checks-only'),
                            cwd=REPO_ROOT, timeout=1800, label='review-checks-only')
        if not (run / 'result.json').is_file():
            raise StageFailure(f"checks-only review wrote no result (exit {command['exit_code']})")
        result, checks, findings = summarize_run(run)
        stage.output(run / 'result.json', 'checks-only result')
        stage.receipt['details'].update(mode='checks_only', outcome=result.get('outcome'), checks=checks,
                                        findings=findings, verdict=None)
        state['reviewed_inputs'] = run / 'inputs'
        failing = sorted(cid for cid, c in checks.items() if (c['execution'], c['outcome']) not in
                         (('ok', 'pass'), ('ok', 'info')))
        if not checks or failing:
            raise StageFailure('declared checks did not all pass: ' + ', '.join(failing or ['none ran'])
                               + (f" (review error: {result['error']})" if result.get('error') else ''))
        return 'not_reached:review_skipped'
    profile_path = Path(options['profile'])
    profile = json.loads(profile_path.read_text())
    stage.input(profile_path, 'review profile')
    stage.tool('claude')
    config = campaign_config(state['scope'], profile, options.get('max_seconds', 2400))
    config_path = stage.dir / 'campaign.json'
    write_json(config_path, config)
    stage.output(config_path, 'campaign configuration')
    root = stage.dir / 'campaign'
    command = stage.run(agent_ee_argv('campaign', config_path, '--out', root), cwd=REPO_ROOT,
                        timeout=config['max_seconds'] + 1800, label='campaign')
    state_path = root / 'state.json'
    if not state_path.is_file():
        raise StageFailure(f"campaign wrote no state.json (exit {command['exit_code']})")
    campaign_state = json.loads(state_path.read_text())
    verify = stage.run(agent_ee_argv('verify-campaign', root), cwd=REPO_ROOT, timeout=1800, label='verify-campaign')
    runs = sorted((p for p in (root / 'runs').iterdir() if p.name.isdigit()), key=lambda p: int(p.name)) \
        if (root / 'runs').is_dir() else []
    details = {'mode': 'campaign', 'campaign_exit': command['exit_code'], 'verdict': campaign_state.get('state'),
               'diagnostic': campaign_state.get('diagnostic'), 'verify_exit': verify['exit_code'],
               'verify_stdout': (stage.rundir / verify['stdout']).read_text()[-2000:],
               'runs': []}
    for run in runs:
        if (run / 'result.json').is_file():
            result, checks, findings = summarize_run(run)
            details['runs'].append({'run': run.name, 'outcome': result.get('outcome'), 'state': result.get('state'),
                                    'checks': checks, 'findings': findings})
    stage.receipt['details'].update(details)
    stage.output(state_path, 'campaign state')
    if runs:
        state['reviewed_inputs'] = runs[-1] / 'inputs'
    state['verdict'] = campaign_state.get('state')
    if campaign_state.get('state') != 'criteria_satisfied' or command['exit_code'] != 0:
        raise StageFailure(f"campaign verdict {campaign_state.get('state')} (exit {command['exit_code']})")
    if verify['exit_code'] != 0:
        raise StageFailure('verify-campaign did not confirm campaign integrity')


# ------------------------------------------------------------------ stage 7: fab package

def copper_layers(board_text):
    block = re.search(r'\n\t\(layers\n(.*?)\n\t\)', board_text, re.S)
    if not block:
        raise StageFailure('board has no layers block')
    return re.findall(r'\(\d+ "([^"]+\.Cu)" (?:signal|power|mixed|jumper)', block[1])


def jlcpcb_cpl(kicad_csv, excluded):
    rows = list(csv.DictReader(io.StringIO(kicad_csv)))
    if not rows or not {'Ref', 'PosX', 'PosY', 'Rot', 'Side'} <= set(rows[0]):
        raise StageFailure('kicad-cli position file lacks Ref/PosX/PosY/Rot/Side columns')
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(['Designator', 'Mid X', 'Mid Y', 'Layer', 'Rotation'])
    kept, dropped = [], []
    for row in rows:
        if row['Ref'] in excluded:
            dropped.append(row['Ref'])
            continue
        side = {'top': 'Top', 'bottom': 'Bottom'}.get(row['Side'].strip().lower())
        if side is None:
            raise StageFailure('unknown side in position file: ' + row['Side'])
        writer.writerow([row['Ref'], row['PosX'] + 'mm', row['PosY'] + 'mm', side, row['Rot']])
        kept.append(row['Ref'])
    return out.getvalue(), kept, dropped


def jlcpcb_bom(kicad_csv, excluded):
    rows = list(csv.reader(io.StringIO(kicad_csv)))
    header = rows[0] if rows else []
    if header != ['Comment', 'Designator', 'Footprint', 'LCSC Part #']:
        raise StageFailure('kicad-cli BOM header differs: ' + repr(header))
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(header)
    kept, dropped = [], []
    for row in rows[1:]:
        refs = [r.strip() for r in row[1].split(',') if r.strip()]
        dropped += [r for r in refs if r in excluded]
        refs = [r for r in refs if r not in excluded]
        if refs:
            if not re.fullmatch(r'C\d+', row[3]):
                raise StageFailure(f'assembled BOM line {refs} has no LCSC part number')
            writer.writerow([row[0], ','.join(refs), row[2], row[3]])
            kept += refs
    return out.getvalue(), kept, dropped


def deterministic_zip(target, files):
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for arcname, path in sorted(files.items()):
            info = zipfile.ZipInfo(arcname, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, Path(path).read_bytes())


def stage_fab(stage, state, options):
    name = state['name']
    source = Path(state['reviewed_inputs'])
    board, sch = source / 'project' / f'{name}.kicad_pcb', source / 'project' / f'{name}.kicad_sch'
    if not board.is_file() or not sch.is_file():
        raise StageFailure('reviewed candidate project is missing its board or schematic')
    stage.input(board, 'reviewed board')
    stage.input(sch, 'reviewed schematic')
    stage.tool('kicad-cli')
    stage.tool('bwrap')
    layers = copper_layers(board.read_text()) + list(FAB_LAYERS)
    manifest_parts = json.loads(Path(state['parts']).read_text())
    excluded = {e['ref']: e for e in manifest_parts['assembly_exclusions']}
    steps = {
        'gerbers': ['kicad-cli', 'pcb', 'export', 'gerbers', '--layers', ','.join(layers),
                    '-o', '/work/out/', f'/candidate/project/{name}.kicad_pcb'],
        'drill': ['kicad-cli', 'pcb', 'export', 'drill', '--format', 'excellon', '--excellon-units', 'mm',
                  '--excellon-separate-th', '-o', '/work/out/', f'/candidate/project/{name}.kicad_pcb'],
        'pos': ['kicad-cli', 'pcb', 'export', 'pos', '--format', 'csv', '--units', 'mm', '--side', 'both',
                '--exclude-dnp', '-o', '/work/out/positions.csv', f'/candidate/project/{name}.kicad_pcb'],
        'bom': ['kicad-cli', 'sch', 'export', 'bom', '--fields', 'Value,Reference,Footprint,LCSC',
                '--labels', 'Comment,Designator,Footprint,LCSC Part #', '--group-by', 'Value,Footprint,LCSC',
                '--ref-delimiter', ',', '--ref-range-delimiter', '', '--exclude-dnp',
                '-o', '/work/out/bom.csv', f'/candidate/project/{name}.kicad_sch'],
    }
    variant = options.get('bom_variant')
    if variant is not None:
        # kicad-cli silently exports the default for an undefined name (AGENTEE-66).
        from .kicad_quirks import VariantError, validate_bom_variants
        pro = source / 'project' / f'{name}.kicad_pro'
        try:
            stage.receipt['details']['bom_variant'] = validate_bom_variants(pro, [sch], [variant])
        except VariantError as exc:
            raise StageFailure(f'BOM variant {variant!r} refused before kicad-cli ran: {exc}') from exc
        stage.input(pro, 'reviewed project file (variant registry)')
        steps['bom'][-1:-1] = ['--variant', variant]
    package = stage.dir / 'package'
    for sub in ('gerbers', 'drill'):
        (package / sub).mkdir(parents=True)
    for label, argv in steps.items():
        work = stage.dir / 'steps' / label
        (work / 'out').mkdir(parents=True)
        seed_kicad_config(work, source)
        result = stage.sandboxed(source, work, argv, timeout=600, label=label)
        if result['exit_code'] != 0:
            raise StageFailure(f'kicad-cli {label} export failed; see ' + stage.rel(work / 'console.log'))
    gerbers = sorted((stage.dir / 'steps/gerbers/out').iterdir())
    drills = sorted((stage.dir / 'steps/drill/out').iterdir())
    for path in gerbers:
        shutil.copyfile(path, package / 'gerbers' / path.name)
    for path in drills:
        shutil.copyfile(path, package / 'drill' / path.name)
    cpl, cpl_kept, cpl_dropped = jlcpcb_cpl((stage.dir / 'steps/pos/out/positions.csv').read_text(), excluded)
    bom, bom_kept, bom_dropped = jlcpcb_bom((stage.dir / 'steps/bom/out/bom.csv').read_text(), excluded)
    (package / 'cpl.csv').write_text(cpl)
    (package / 'bom.csv').write_text(bom)
    deterministic_zip(package / f'{name}-gerbers.zip',
                      {p.name: p for p in sorted((package / 'gerbers').iterdir()) + sorted((package / 'drill').iterdir())})
    files = sorted(p for p in package.rglob('*') if p.is_file())
    empty = [p.name for p in files if p.stat().st_size == 0]
    manifest = {
        'schema': FAB_SCHEMA, 'qualification': QUALIFICATION, 'producer': 'agent_ee.generate stage 7 (driver export)',
        'qualified_check': 'AGENTEE-23 (fab_export check kind; patch attached there, not duplicated here)',
        'board': {'path': stage.rel(board), 'sha256': digest(board)},
        'schematic': {'path': stage.rel(sch), 'sha256': digest(sch)},
        'kicad_cli': tool_identity('kicad-cli'), 'gerber_layers': layers,
        'assembly_exclusions': list(excluded.values()),
        'bom_designators': bom_kept, 'cpl_designators': cpl_kept,
        'excluded_from_bom': bom_dropped, 'excluded_from_cpl': cpl_dropped,
        'files': [{'path': str(p.relative_to(package)), 'bytes': p.stat().st_size, 'sha256': digest(p)} for p in files],
        'limitations': ['Gerber and drill files embed creation timestamps; hashes bind these bytes only',
                        'CPL rotations are KiCad-native; JLCPCB rotation correction is AGENTEE-72',
                        'No DFM, layer reconciliation or Gerber re-parse (AGENTEE-73, AGENTEE-74, AGENTEE-76)']}
    write_json(stage.dir / 'fab-manifest.json', manifest)
    stage.output(package, 'fab package')
    stage.output(stage.dir / 'fab-manifest.json', 'fab manifest')
    stage.receipt['details'].update(gerbers=len(gerbers), drills=len(drills), bom=bom_kept, cpl=cpl_kept,
                                    excluded={'bom': bom_dropped, 'cpl': cpl_dropped})
    stage.receipt['limitations'].append('This is the driver\'s export; the qualified check kind is AGENTEE-23.')
    state['fab_manifest'] = stage.dir / 'fab-manifest.json'
    if empty or len(gerbers) < len(layers) or not drills or not cpl_kept or not bom_kept:
        raise StageFailure(f'fab package incomplete: empty={empty} gerbers={len(gerbers)} drills={len(drills)}')
    if sorted(cpl_kept) != sorted(bom_kept):
        raise StageFailure(f'BOM designators {bom_kept} differ from CPL designators {cpl_kept}')


STAGE_FUNCTIONS = {'requirements': stage_requirements, 'parts': stage_parts, 'schematic': stage_schematic,
                   'placement': stage_placement, 'routing': stage_routing, 'review': stage_review,
                   'fab': stage_fab}


# ------------------------------------------------------------------ driver

def fab_exists(manifest_path):
    if not manifest_path or not Path(manifest_path).is_file():
        return False
    manifest = json.loads(Path(manifest_path).read_text())
    package = Path(manifest_path).parent / 'package'
    return bool(manifest['files']) and all(
        (package / f['path']).is_file() and (package / f['path']).stat().st_size > 0
        and digest(package / f['path']) == f['sha256'] for f in manifest['files'])


def generate(spec, rundir, *, profile=None, skip_review=False, stand_in=False, max_seconds=2400, functions=None,
             catalog_checkout=None, bom_variant=None):
    """Run every stage in order; returns the rung-1 record."""
    rundir = Path(rundir)
    if rundir.exists():
        raise FileExistsError(f'{rundir} exists; choose a fresh run directory')
    if not skip_review and profile is None:
        raise ValueError('a review profile is required unless --skip-review is given')
    rundir.mkdir(parents=True)
    functions = {**STAGE_FUNCTIONS, **(functions or {})}
    options = {'spec': str(Path(spec).absolute()), 'profile': str(Path(profile).absolute()) if profile else None,
               'skip_review': skip_review, 'stand_in': stand_in, 'max_seconds': max_seconds,
               'catalog_checkout': catalog_checkout, 'bom_variant': bom_variant}
    state, stages, blocked = {}, [], None
    for number, name in STAGES:
        stage = Stage(rundir, number, name)
        if blocked:
            stage.receipt['details']['blocked_by'] = blocked
            receipt = stage.finish('not_reached')
        else:
            stage.start()
            status, error = 'ok', None
            try:
                status = functions[name](stage, state, options) or 'ok'
            except DependencyMissing as exc:
                status, error = 'not_reached:dependency_missing', str(exc)
                if not stand_in:
                    blocked = f'{number}-{name}'
            except StageFailure as exc:
                status, error = 'failed', str(exc)
            except Exception as exc:  # noqa: BLE001 - recorded as a failed stage, never a pass
                status, error = 'failed', f'{type(exc).__name__}: {exc}'
                stage.receipt['details']['traceback'] = traceback.format_exc()
            if status == 'failed':
                blocked = f'{number}-{name}'
            receipt = stage.finish(status, error)
        stages.append({'stage': number, 'name': name, 'status': receipt['status'], 'error': receipt['error'],
                       'receipt': f'stages/{number}-{name}/receipt.json',
                       'receipt_sha256': digest(rundir / 'stages' / f'{number}-{name}' / 'receipt.json')})
    verdict = state.get('verdict')
    fab_ok = fab_exists(state.get('fab_manifest'))
    # The gate is exactly the contract's: never relaxed, overridden or reinterpreted here.
    done = verdict == 'criteria_satisfied' and fab_ok
    record = {'schema': RECORD_SCHEMA, 'qualification': QUALIFICATION, 'spec': {'path': options['spec'], 'sha256': digest(spec)},
              'options': {'skip_review': skip_review, 'stand_in': stand_in, 'max_seconds': max_seconds,
                          'profile': options['profile'],
                          'profile_sha256': digest(profile) if profile else None,
                          **({'bom_variant': bom_variant} if bom_variant is not None else {})},
              'generator': tool_identity('agent_ee'), 'stages': stages, 'verdict': verdict,
              'fab_manifest': ({'path': 'stages/7-fab/fab-manifest.json', 'sha256': digest(state['fab_manifest'])}
                               if state.get('fab_manifest') else None),
              'fab_exports_exist': fab_ok, 'done': done,
              # Every stage is an experimental driver step; placement in particular may be the
              # row stand-in rather than the landed placer (AGENTEE-109).
              'stage_qualification': {name: QUALIFICATION for _, name in STAGES},
              'placement': {'qualification': QUALIFICATION, 'placer': state.get('placer')},
              'done_rule': 'campaign verdict criteria_satisfied AND fab exports exist and are nonempty'}
    write_json(rundir / 'rung1-record.json', record)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('spec', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--skip-review', action='store_true')
    parser.add_argument('--stand-in-fixture', action='store_true',
                        help='use fixtures/rc/control when G1/G2 are absent (stages marked dependency_missing)')
    parser.add_argument('--max-seconds', type=float, default=2400)
    parser.add_argument('--bom-variant', help='export the BOM for this .kicad_pro-registered design variant; '
                                              'an unregistered name fails the fab stage before kicad-cli runs')
    args = parser.parse_args(argv)
    try:
        record = generate(args.spec, args.out, profile=args.profile, skip_review=args.skip_review,
                          stand_in=args.stand_in_fixture, max_seconds=args.max_seconds,
                          bom_variant=args.bom_variant)
    except (FileExistsError, ValueError, OSError) as exc:
        parser.exit(2, f'generate refused: {exc}\n')
    for item in record['stages']:
        print(f"{item['stage']}-{item['name']}: {item['status']}" + (f" ({item['error']})" if item['error'] else ''))
    print('verdict:', record['verdict'], '| done:', record['done'])
    return 0 if record['done'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
