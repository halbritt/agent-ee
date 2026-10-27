"""Reviewer false-positive calibration with equivalence-preserving design variants.

    python3 -m agent_ee.fp_calibration prepare PROJECT_DIR --root NAME.kicad_sch --seed N --out CAL
    python3 -m agent_ee.fp_calibration run CAL --profile PROFILE.json --repeats 3 --max-seconds S
    python3 -m agent_ee.fp_calibration report CAL [--out REPORT.json]

``prepare`` writes seeded variants of one captured flat KiCad schematic and
proves each label by sandboxed native netlist export compared with the AGENTEE-51
canonical design diff: an equivalent variant needs netlist identity after its
declared renaming, a different variant needs a non-empty diff. Anything else,
including a failed export, is excluded. ``run`` presents every included pair to
every profile reviewer in both orders, repeatedly, through the ordinary preflight
and harness seam; a failed or malformed call is retained as missing and never
replayed. ``report`` reads only the retained calls and makes no model call.
"""
import argparse
import copy
import json
import random
import re
import shutil
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from .design_diff import Malformed, compare
from . import native_run
from .schematic_decompile import _pin_position
from .schematic_emit import Quoted, _mm, format_sexpr, parse_quoted
from .storage import digest, identity, read_json, write_json
from .symbol_library import LibraryError, children, nanometres, resolve_symbol
from .run_record import pinned, policy_files, runtime_files

PLAN_SCHEMA = 'agent-ee.fp-calibration/1'
LEDGER_SCHEMA = 'agent-ee.fp-calibration-calls/1'
REPORT_SCHEMA = 'agent-ee.fp-calibration-report/1'
VERDICT_VERSION = 'agent-ee.pair-verdict/1'
EQUIVALENT_KINDS = ('reference_renumber', 'net_rename', 'sheet_reshuffle', 'symbol_relocation')
DIFFERENT_KINDS = ('value_change', 'label_reassign', 'wire_removal')
ORDERS = ('forward', 'reverse')
MAX_REPEATS = 5
EXPORT_TIMEOUT_S = 300
# Top-level schematic items whose file order carries no meaning.
MOVABLE = frozenset(('wire', 'bus', 'bus_entry', 'junction', 'no_connect', 'label', 'global_label',
                     'symbol', 'text', 'text_box', 'polyline', 'rectangle', 'circle', 'arc', 'image',
                     'netclass_flag', 'rule_area'))
# KiCad names a net without a label after one of its pins, so renumbering renames it.
AUTO_NET = re.compile(r'(?:Net|unconnected)-\(')
REFERENCE = re.compile(r'([A-Za-z_]+)(\d+)')
LEADING_NUMBER = re.compile(r'\d+')
UUID_NAMESPACE = uuid.UUID('6f1d3c52-7a0e-5b8e-9c41-1f6e2d0a7b70')

VERDICT_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["schema", "call_id", "verdict", "differences", "limitations"],
    "properties": {"schema": {"type": "string", "enum": [VERDICT_VERSION]},
        "call_id": {"type": "string"},
        "verdict": {"type": "string", "enum": ["equivalent", "different"]},
        "differences": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "required": ["title", "location", "reasoning"],
            "properties": {k: {"type": "string"} for k in ("title", "location", "reasoning")}}},
        "limitations": {"type": "array", "items": {"type": "string"}}}}

PROMPT = (
    "You are an independent electrical design reviewer. Two KiCad schematic designs are read-only at "
    "/candidate/A and /candidate/B. Decide whether A and B are electrically equivalent. They are equivalent "
    "when they contain the same components (library part, value and footprint) with the same pin-to-pin "
    "connectivity, allowing one consistent one-to-one renaming of reference designators and of net label names. "
    "Symbol placement on the sheet, wire segments that join the same pins and the order of items in the file "
    "do not change electrical function. Report verdict \"different\" for any change of a component, value, "
    "footprint, pin assignment or connection, and list each difference with its location and reasoning. "
    "Report verdict \"equivalent\" with an empty differences list otherwise. Investigate with the installed "
    "tools as needed (for example a kicad-cli netlist export into /work/experiments). Do not modify the "
    "candidate and do not read credential files. Record anything you could not establish under limitations. "
    "Return only the structured record with schema \"" + VERDICT_VERSION + "\" and call_id \"{call_id}\".")


class CalibrationError(ValueError):
    """The design, plan or retained calls cannot support a calibration; nothing is guessed."""


# ------------------------------------------------------------------ variants

def _property(symbol, name):
    for node in children(symbol, 'property'):
        if len(node) > 2 and node[1] == name:
            return node
    return None


def _reference(symbol):
    node = _property(symbol, 'Reference')
    return node[2] if node else None


def _placed(tree):
    return [n for n in children(tree, 'symbol') if _reference(n) is not None]


def _point(node, key='at'):
    found = children(node, key)
    if len(found) != 1 or len(found[0]) < 3:
        raise CalibrationError(f'{node[0]} without one {key}')
    return nanometres(found[0][1]), nanometres(found[0][2])


def _shift(node, delta):
    node[1], node[2] = _mm(nanometres(node[1]) + delta[0]), _mm(nanometres(node[2]) + delta[1])


def _rename_references(node, mapping):
    for child in node[1:]:
        if isinstance(child, list):
            if child and child[0] == 'reference' and len(child) == 2 and child[1] in mapping:
                child[1] = Quoted(mapping[child[1]])
            else:
                _rename_references(child, mapping)


def reference_renumber(tree, rng):
    refs = sorted({_reference(s) for s in _placed(tree)})
    taken = set(refs)
    groups = {}
    for ref in refs:
        match = REFERENCE.fullmatch(ref)
        if match and not ref.startswith('#'):
            groups.setdefault(match.group(1), []).append(ref)
    if not groups:
        return None
    mapping = {}
    for prefix, members in sorted(groups.items()):
        base = rng.choice((10, 20, 100))
        while any(prefix + str(base + i) in taken for i in range(1, len(members) + 1)):
            base += 100
        numbers = list(range(base + 1, base + len(members) + 1))
        rng.shuffle(numbers)
        mapping.update({ref: prefix + str(n) for ref, n in zip(members, numbers)})
    for symbol in _placed(tree):
        old = _reference(symbol)
        if old in mapping:
            _property(symbol, 'Reference')[2] = Quoted(mapping[old])
            _rename_references(symbol, mapping)
    return {'references': mapping}


def _labels(tree):
    return [n for n in tree[1:] if isinstance(n, list) and n and n[0] in ('label', 'global_label')]


def net_rename(tree, rng):
    labels = _labels(tree)
    names = sorted({n[1] for n in labels})
    if not names:
        return None
    suffix = rng.choice(('_A', '_N', '_NET', '_SIG')) + str(rng.randrange(10, 100))
    mapping = {name: name + suffix for name in names}
    if set(mapping.values()) & set(names):
        return None
    for node in labels:
        node[1] = Quoted(mapping[node[1]])
    return {'net_labels': mapping}


def sheet_reshuffle(tree, rng):
    slots = [i for i, n in enumerate(tree) if i and isinstance(n, list) and n and n[0] in MOVABLE]
    if len(slots) < 2:
        return None
    order = list(range(len(slots)))
    rng.shuffle(order)
    if order == sorted(order):
        order = order[1:] + order[:1]
    items = [tree[i] for i in slots]
    for slot, source in zip(slots, order):
        tree[slot] = items[source]
    return {'order': order}


def _pins(tree, symbol):
    lib_id = children(symbol, 'lib_id')
    if len(lib_id) != 1:
        return None
    library = {n[1]: n for group in children(tree, 'lib_symbols') for n in children(group, 'symbol')}
    try:
        pins, _sources, power = resolve_symbol(library, lib_id[0][1], 'lib_symbols', 'embedded')
    except LibraryError:
        return None
    if power or not pins:
        return None
    unit = int(children(symbol, 'unit')[0][1]) if children(symbol, 'unit') else 1
    (x, y), rotation = _point(symbol), int(float(children(symbol, 'at')[0][3])) % 360
    mirror = children(symbol, 'mirror')[0][1] if children(symbol, 'mirror') else None
    if rotation not in (0, 90, 180, 270):
        return None
    return [_pin_position(p, (x, y), rotation, mirror) for p in pins if p['unit'] in (0, unit)]


def _wire(start, end, name):
    return ['wire', ['pts', ['xy', _mm(start[0]), _mm(start[1])], ['xy', _mm(end[0]), _mm(end[1])]],
            ['stroke', ['width', '0'], ['type', 'default']], ['uuid', Quoted(name)]]


def symbol_relocation(tree, rng, seed):
    candidates = [s for s in _placed(tree) if not _reference(s).startswith('#') and _pins(tree, s)]
    if not candidates:
        return None
    symbol = rng.choice(sorted(candidates, key=_reference))
    step = rng.choice((5080000, 7620000, 10160000)) * rng.choice((1, -1))
    delta = (step, 0) if rng.random() < 0.5 else (0, step)
    points = _pins(tree, symbol)
    children(symbol, 'at')[0][1:3] = _mm(_point(symbol)[0] + delta[0]), _mm(_point(symbol)[1] + delta[1])
    for node in children(symbol, 'property'):
        for at in children(node, 'at'):
            _shift(at, delta)
    flags = {_point(n): n for n in children(tree, 'no_connect')}
    wires, moved = [], 0
    for index, point in enumerate(sorted(set(points))):
        if point in flags:
            _shift(children(flags[point], 'at')[0], delta)
            moved += 1
        else:
            name = str(uuid.uuid5(UUID_NAMESPACE, f'{seed}:symbol_relocation:{index}'))
            wires.append(_wire(point, (point[0] + delta[0], point[1] + delta[1]), name))
    heads = [n[0] if isinstance(n, list) and n else None for n in tree]
    at = (max(i for i, h in enumerate(heads) if h == 'wire') + 1 if 'wire' in heads else
          heads.index('symbol') if 'symbol' in heads else len(tree))
    tree[at:at] = wires
    return {'reference': _reference(symbol), 'offset_mm': [_mm(delta[0]), _mm(delta[1])],
            'added_wires': len(wires), 'moved_no_connects': moved}


def value_change(tree, rng):
    candidates = []
    for symbol in _placed(tree):
        value = _property(symbol, 'Value')
        if not _reference(symbol).startswith('#') and value and LEADING_NUMBER.match(value[2]):
            candidates.append(symbol)
    if not candidates:
        return None
    symbol = rng.choice(sorted(candidates, key=_reference))
    node = _property(symbol, 'Value')
    old = node[2]
    digits = LEADING_NUMBER.match(old).group(0)
    number = rng.choice([n for n in ('2', '3', '22', '47', '68', '330') if n != digits])
    node[2] = Quoted(number + old[len(digits):])
    return {'reference': _reference(symbol), 'from': old, 'to': node[2]}


def label_reassign(tree, rng):
    labels = [n for n in _labels(tree) if n[0] == 'label']
    names = sorted({n[1] for n in labels})
    if len(names) < 2:
        return None
    node = rng.choice(labels)
    old = node[1]
    node[1] = Quoted(rng.choice([n for n in names if n != old]))
    return {'at_mm': [_mm(v) for v in _point(node)], 'from': old, 'to': node[1]}


def wire_removal(tree, rng):
    wires = [i for i, n in enumerate(tree) if i and isinstance(n, list) and n and n[0] == 'wire']
    if not wires:
        return None
    index = rng.choice(wires)
    points = [[x[1], x[2]] for x in children(children(tree[index], 'pts')[0], 'xy')]
    del tree[index]
    return {'pts_mm': points}


TRANSFORMS = {'reference_renumber': reference_renumber, 'net_rename': net_rename,
              'sheet_reshuffle': sheet_reshuffle, 'symbol_relocation': symbol_relocation,
              'value_change': value_change, 'label_reassign': label_reassign, 'wire_removal': wire_removal}


# ------------------------------------------------------------------ equivalence proof

def normalized_netlist(data, references=None, nets=None):
    """Apply a declared renaming to native kicadxml bytes; pin-named nets become their node set."""
    references, nets = references or {}, nets or {}
    text = data.decode('utf-8-sig')
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise Malformed('XML declarations/entities are not supported')
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise Malformed(f'not valid XML: {exc}') from exc
    for node in [*root.iter('comp'), *root.iter('node')]:
        node.set('ref', references.get(node.get('ref'), node.get('ref')))
    for net in root.iter('net'):
        name = net.get('name', '')
        if AUTO_NET.match(name):
            name = 'pins:' + ','.join(sorted(f"{n.get('ref')}.{n.get('pin')}" for n in net.iter('node')))
        net.set('name', nets.get(name, name))
    return ET.tostring(root, encoding='utf-8')


def inverse_maps(kind, change):
    """Renaming that maps a variant's netlist back onto the original's names."""
    if kind == 'reference_renumber':
        return {new: old for old, new in change['references'].items()}, {}
    if kind == 'net_rename':
        nets = {}
        for old, new in change['net_labels'].items():
            nets[new], nets['/' + new] = old, '/' + old
        return {}, nets
    return {}, {}


def export_netlist(project, root, work):
    """Sandboxed native export; returns (bytes or None, execution record)."""
    work.mkdir(parents=True)
    record = dict(native_run.run(native_run.netlist('/candidate/' + root, '/work/netlist.xml', timeout=EXPORT_TIMEOUT_S),
                                 inputs=project, work=work))
    output = work / 'netlist.xml'
    ok = (record['execution'] == 'ok' and record['exit_code'] == 0 and not record.get('displaced_records')
          and output.is_file() and output.stat().st_size > 0)
    return (output.read_bytes() if ok else None), record


def prove(original, variant, kind, change):
    """Compare normalized netlists with the canonical design diff; never infer equivalence."""
    expected = 'equivalent' if kind in EQUIVALENT_KINDS else 'different'
    references, nets = inverse_maps(kind, change)
    try:
        diff = compare(normalized_netlist(original), normalized_netlist(variant, references, nets), 'netlist')
    except Malformed as exc:
        return {'established': False, 'reason': 'netlist did not parse: ' + str(exc)}
    proof = {'method': 'native kicadxml export, declared renaming, agent-ee.design-diff/1 netlist identity',
             'netlist_identity': diff['equivalent'], 'hashes': diff['hashes'],
             'entry_count': len(diff['entries']), 'entries': diff['entries'][:20]}
    if (expected == 'equivalent') != diff['equivalent']:
        proof.update(established=False, reason=('netlist identity not established after the declared renaming'
                     if expected == 'equivalent' else 'change left the netlist identical'))
    else:
        proof['established'] = True
    return proof


# ------------------------------------------------------------------ prepare

def _design_files(project, root):
    project = Path(project)
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.\-]*\.kicad_sch', root):
        raise CalibrationError('root must be a schematic file name in the project directory')
    files = {}
    for path in sorted(project.iterdir()):
        if path.suffix in ('.kicad_sch', '.kicad_pro'):
            if path.is_symlink() or not path.is_file():
                raise CalibrationError('design file is not a regular file: ' + path.name)
            files[path.name] = path.read_bytes()
    if root not in files:
        raise CalibrationError('root schematic not found: ' + root)
    if [n for n in files if n.endswith('.kicad_sch')] != [root]:
        raise CalibrationError('only a flat single-schematic design is supported')
    return files


def _write_design(directory, files):
    directory.mkdir(parents=True)
    for name, data in files.items():
        (directory / name).write_bytes(data)
    return {name: digest(directory / name) for name in files}


def prepare(project, root, seed, out):
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise CalibrationError('seed must be an integer')
    out = Path(out)
    if out.exists() or out.is_symlink():
        raise CalibrationError('output directory already exists; calibrations are never overwritten')
    files = _design_files(project, root)
    try:
        tree = parse_quoted(files[root])
    except LibraryError as exc:
        raise CalibrationError('root schematic does not parse: ' + str(exc)) from exc
    if tree[0] != 'kicad_sch':
        raise CalibrationError('root is not a kicad_sch')
    if children(tree, 'sheet'):
        raise CalibrationError('hierarchical sheets are not supported')
    out.mkdir(parents=True)
    original_hashes = _write_design(out / 'design' / 'original', files)
    original, record = export_netlist(out / 'design' / 'original', root, out / 'proof' / 'original')
    if original is None:
        raise CalibrationError('original netlist export failed: ' + record['execution'])
    version = out / 'proof' / 'kicad-cli-version'
    tool = dict(native_run.run(native_run.Intent('kicad-cli', ['kicad-cli', 'version'], timeout=60), inputs=out / 'design' / 'original', work=version))
    variants = []
    for kind in (*EQUIVALENT_KINDS, *DIFFERENT_KINDS):
        rng = random.Random(f'{seed}:{kind}')
        changed = copy.deepcopy(tree)
        transform = TRANSFORMS[kind]
        change = transform(changed, rng, seed) if kind == 'symbol_relocation' else transform(changed, rng)
        entry = {'id': kind, 'kind': kind,
                 'expected': 'equivalent' if kind in EQUIVALENT_KINDS else 'different', 'change': change}
        if change is None:
            entry.update(status='excluded', reason='transform not applicable to this design')
            variants.append(entry)
            continue
        data = ('\n'.join(format_sexpr(changed)) + '\n').encode()
        entry['files'] = _write_design(out / 'variants' / kind, {**files, root: data})
        exported, record = export_netlist(out / 'variants' / kind, root, out / 'proof' / kind)
        if exported is None:
            entry.update(status='excluded', reason='variant netlist export failed: ' + record['execution'])
        else:
            proof = prove(original, exported, kind, change)
            entry['proof'] = proof
            entry.update(status='included' if proof['established'] else 'excluded',
                         **({} if proof['established'] else {'reason': proof['reason']}))
        variants.append(entry)
    plan = {'schema': PLAN_SCHEMA, 'seed': seed, 'root': root, 'original': original_hashes,
            'tool': (version / 'stdout.log').read_text().strip() if tool['execution'] == 'ok' else None,
            'variants': variants}
    plan['identity'] = identity(plan)
    write_json(out / 'plan.json', plan)
    return plan


def load_plan(cal):
    plan = read_json(Path(cal) / 'plan.json')
    if plan.get('schema') != PLAN_SCHEMA or identity({k: v for k, v in plan.items() if k != 'identity'}) != plan['identity']:
        raise CalibrationError('plan changed after preparation')
    for name, sha in plan['original'].items():
        if digest(Path(cal) / 'design' / 'original' / name) != sha:
            raise CalibrationError('original design changed after preparation')
    for entry in plan['variants']:
        for name, sha in entry.get('files', {}).items():
            if digest(Path(cal) / 'variants' / entry['id'] / name) != sha:
                raise CalibrationError('variant changed after preparation: ' + entry['id'])
    return plan


# ------------------------------------------------------------------ reviewer calls

def validate_verdict(value, call_id):
    """Return 'equivalent' or 'different'; raise for anything that is not a complete verdict."""
    if not isinstance(value, dict) or set(value) != set(VERDICT_SCHEMA['required']):
        raise ValueError('verdict fields do not match the schema')
    if value['schema'] != VERDICT_VERSION or value['call_id'] != call_id:
        raise ValueError('verdict is not bound to this call')
    if value['verdict'] not in ('equivalent', 'different'):
        raise ValueError('verdict must be equivalent or different')
    differences = value['differences']
    if (not isinstance(differences, list) or not isinstance(value['limitations'], list) or
            any(not isinstance(x, str) for x in value['limitations']) or
            any(not isinstance(d, dict) or set(d) != {'title', 'location', 'reasoning'} or
                any(not isinstance(d[k], str) or not d[k].strip() for k in d) for d in differences)):
        raise ValueError('malformed differences or limitations')
    if (value['verdict'] == 'different') != bool(differences):
        raise ValueError('a different verdict needs differences and an equivalent verdict none')
    return value['verdict']


def call_identity(plan, role, variant, order, repeat):
    return identity([plan['identity'], role, variant, order, repeat])[:24]


def _pair_sides(cal, variant, order):
    first, second = Path(cal) / 'design' / 'original', Path(cal) / 'variants' / variant
    return (first, second) if order == 'forward' else (second, first)


def _side_hashes(directory):
    return {p.name: digest(p) for p in sorted(Path(directory).iterdir()) if p.is_file()}


def _retain(path, name, value):
    write_json(path / name, value)
    return digest(path / name)


def run(cal, profile, repeats, max_seconds, *, profile_path=None, host=None):
    """Launch every planned call once; retain each settled record before the next admission.

    host holds caller-supplied policy and runtime fingerprints (run_record.pinned) for the launch
    expectation and every launch guard."""
    from .preflight import capture_expectation
    from .profiles import validate_profile
    from .reviewers import reviewer_call
    cal = Path(cal)
    plan = load_plan(cal)
    validate_profile(profile)
    if isinstance(repeats, bool) or not isinstance(repeats, int) or not 1 <= repeats <= MAX_REPEATS:
        raise CalibrationError(f'repeats must be an integer from 1 to {MAX_REPEATS}')
    if (cal / 'calls').exists() or (cal / 'calls.json').exists():
        raise CalibrationError('calls already exist; a calibration call is never replayed')
    included = [v for v in plan['variants'] if v['status'] == 'included']
    if not included:
        raise CalibrationError('no variant was proven; nothing to present')
    definitions = [{**d, 'tool_mode': profile.get('tool_mode', 'investigate')} for d in profile['reviewers']]
    run_id = str(uuid.uuid4())
    expected = capture_expectation({'run_id': run_id, 'policy_files': pinned(host, 'policy_files', policy_files),
                                    'runtime_files': pinned(host, 'runtime_files', runtime_files)},
                                   {'scope': {'schema': 'agent-ee.scope/1', 'checks': [], 'review_profile': profile},
                                    'candidate_id': plan['identity'], 'files': {}}, definitions)
    write_json(cal / 'launch-expectation.json', expected)
    deadline = time.monotonic() + max_seconds
    ledger = {'schema': LEDGER_SCHEMA, 'plan_identity': plan['identity'], 'run_id': run_id,
              'profile': profile, 'profile_path': str(profile_path) if profile_path else None,
              'profile_sha256': digest(profile_path) if profile_path else None, 'repeats': repeats,
              'launch_expectation_sha256': digest(cal / 'launch-expectation.json'),
              'prompt_template_sha256': identity(PROMPT), 'schema_sha256': identity(VERDICT_SCHEMA),
              'calls': []}
    schedule = [(d, r, v, o) for d in definitions for r in range(repeats) for v in included for o in ORDERS]
    stopped = {}
    for index, (definition, repeat, variant, order) in enumerate(schedule):
        role = definition['role']
        call_id = call_identity(plan, role, variant['id'], order, repeat)
        entry = {'call_id': call_id, 'role': role, 'harness': definition['harness'], 'model': definition['model'],
                 'variant': variant['id'], 'kind': variant['kind'], 'expected': variant['expected'],
                 'order': order, 'repeat': repeat, 'verdict': None}
        if definition['harness'] in stopped:
            entry.update(execution='not_run', missing='harness stopped after call ' + stopped[definition['harness']])
        elif time.monotonic() >= deadline:
            entry.update(execution='not_run', missing='calibration deadline exhausted')
        else:
            path = cal / 'calls' / role / variant['id'] / order / str(repeat)
            first, second = _pair_sides(cal, variant['id'], order)
            shutil.copytree(first, path / 'inputs' / 'A')
            shutil.copytree(second, path / 'inputs' / 'B')
            prompt = PROMPT.replace('{call_id}', call_id)
            (path / 'prompt.txt').write_text(prompt)
            entry.update(path=path.relative_to(cal).as_posix(),
                         inputs={'A': _side_hashes(path / 'inputs' / 'A'), 'B': _side_hashes(path / 'inputs' / 'B')})
            try:
                record, value = reviewer_call(path, role, definition, prompt, VERDICT_SCHEMA,
                    min(definition['timeout_s'], max(0, deadline - time.monotonic())),
                    launch_context={'expected': expected, 'boundary': 'initial', 'deadline': deadline,
                                    **({'host': host} if host is not None else {})})
            except KeyboardInterrupt:
                record, value = {'execution': 'interrupted', 'error': 'operator cancelled the calibration'}, None
                _settle(cal, path, entry, record, value, call_id)
                ledger['calls'].append(entry)
                for definition, repeat, variant, order in schedule[index + 1:]:
                    ledger['calls'].append(_unrun(plan, definition, variant, order, repeat, 'operator cancelled'))
                write_json(cal / 'calls.json', ledger)
                raise
            _settle(cal, path, entry, record, value, call_id)
            if entry['execution'] not in ('ok', 'malformed'):
                stopped[definition['harness']] = call_id
        ledger['calls'].append(entry)
        # Every settled call is published before the next one is admitted.
        write_json(cal / 'calls.json', ledger)
    write_json(cal / 'calls.json', ledger)
    return ledger


def _unrun(plan, definition, variant, order, repeat, reason):
    return {'call_id': call_identity(plan, definition['role'], variant['id'], order, repeat),
            'role': definition['role'], 'harness': definition['harness'], 'model': definition['model'],
            'variant': variant['id'], 'kind': variant['kind'], 'expected': variant['expected'],
            'order': order, 'repeat': repeat, 'verdict': None, 'execution': 'not_run', 'missing': reason}


def _settle(cal, path, entry, record, value, call_id):
    entry['retained'] = {'execution.json': _retain(path, 'execution.json', record),
                         'response.json': _retain(path, 'response.json', value),
                         'prompt.txt': digest(path / 'prompt.txt')}
    entry['execution'] = record.get('execution')
    if entry['execution'] != 'ok':
        entry['missing'] = 'call ' + str(entry['execution']) + ': ' + str(record.get('error', ''))
        return
    try:
        entry['verdict'] = validate_verdict(copy.deepcopy(value), call_id)
    except (ValueError, KeyError, TypeError) as exc:
        entry.update(execution='malformed', missing='invalid verdict: ' + str(exc))


# ------------------------------------------------------------------ report

def _verified_calls(cal, plan):
    ledger = read_json(Path(cal) / 'calls.json')
    if ledger.get('schema') != LEDGER_SCHEMA or ledger.get('plan_identity') != plan['identity']:
        raise CalibrationError('calls do not belong to this plan')
    included = {v['id']: v for v in plan['variants'] if v['status'] == 'included'}
    seen = set()
    for entry in ledger['calls']:
        key = (entry['role'], entry['variant'], entry['order'], entry['repeat'])
        variant = included.get(entry['variant'])
        if (key in seen or variant is None or entry['order'] not in ORDERS or
                entry['expected'] != variant['expected'] or
                entry['call_id'] != call_identity(plan, *key)):
            raise CalibrationError('call ledger entry does not match the plan: ' + entry['call_id'])
        seen.add(key)
        if 'path' not in entry:
            if entry['execution'] != 'not_run' or entry['verdict'] is not None:
                raise CalibrationError('an unlaunched call carries a result: ' + entry['call_id'])
            continue
        path = Path(cal) / entry['path']
        for name, sha in entry['retained'].items():
            if digest(path / name) != sha:
                raise CalibrationError('retained call record changed: ' + entry['path'] + '/' + name)
        first, second = _pair_sides(cal, entry['variant'], entry['order'])
        if (entry['inputs'] != {'A': _side_hashes(path / 'inputs' / 'A'), 'B': _side_hashes(path / 'inputs' / 'B')}
                or entry['inputs'] != {'A': _side_hashes(first), 'B': _side_hashes(second)}):
            raise CalibrationError('presented pair does not match its order: ' + entry['path'])
        if (path / 'prompt.txt').read_text() != PROMPT.replace('{call_id}', entry['call_id']):
            raise CalibrationError('retained prompt is not the calibration prompt: ' + entry['path'])
        record, value = read_json(path / 'execution.json'), read_json(path / 'response.json')
        verdict = None
        if record.get('execution') == 'ok':
            try:
                verdict = validate_verdict(value, entry['call_id'])
            except (ValueError, KeyError, TypeError):
                verdict = None
        if verdict != entry['verdict']:
            raise CalibrationError('ledger verdict differs from the retained response: ' + entry['path'])
    # Unlaunched calls stay listed, so a removed entry cannot shrink a denominator.
    if seen != {(d['role'], v, o, r) for d in ledger['profile']['reviewers'] for r in range(ledger['repeats'])
                for v in included for o in ORDERS}:
        raise CalibrationError('call ledger does not cover the complete schedule')
    return ledger


def _rate(numerator, denominator):
    return None if denominator == 0 else numerator / denominator


def _majority(votes):
    if votes['different'] + votes['equivalent'] == 0:
        return 'unknown'
    if votes['different'] == votes['equivalent']:
        return 'split'
    return 'different' if votes['different'] > votes['equivalent'] else 'equivalent'


def _votes(entries):
    votes = {'different': 0, 'equivalent': 0, 'missing': 0}
    for e in entries:
        votes[e['verdict'] or 'missing'] += 1
    valid = votes['different'] + votes['equivalent']
    return {**votes, 'different_fraction': _rate(votes['different'], valid), 'majority': _majority(votes)}


def report(cal):
    """Compute rates from retained verdicts only. Missing calls are reported, never counted as agreement."""
    plan = load_plan(cal)
    ledger = _verified_calls(cal, plan)
    included = [v for v in plan['variants'] if v['status'] == 'included']
    reviewers = []
    for definition in ledger['profile']['reviewers']:
        role = definition['role']
        mine = [e for e in ledger['calls'] if e['role'] == role]
        valid = [e for e in mine if e['verdict']]
        equivalent = [e for e in valid if e['expected'] == 'equivalent']
        different = [e for e in valid if e['expected'] == 'different']
        fp = [e for e in equivalent if e['verdict'] == 'different']
        fn = [e for e in different if e['verdict'] == 'equivalent']
        pairs, flagged = [], []
        for variant in included:
            cells = {o: _votes([e for e in mine if e['variant'] == variant['id'] and e['order'] == o]) for o in ORDERS}
            evaluable = all(cells[o]['majority'] != 'unknown' for o in ORDERS)
            dependent = (cells['forward']['majority'] != cells['reverse']['majority']) if evaluable else None
            pooled = _votes([e for e in mine if e['variant'] == variant['id']])
            if dependent:
                flagged.append(variant['id'])
            pairs.append({'variant': variant['id'], 'kind': variant['kind'], 'expected': variant['expected'],
                          'orders': cells, 'pooled': pooled, 'order_dependent': dependent,
                          'order_gap': (abs(cells['forward']['different_fraction'] - cells['reverse']['different_fraction'])
                                        if evaluable else None),
                          'pooled_error': (None if pooled['majority'] in ('unknown', 'split')
                                           else pooled['majority'] != variant['expected'])})
        evaluable = [p for p in pairs if p['order_dependent'] is not None]
        cells = [c for p in pairs for c in p['orders'].values() if c['different'] + c['equivalent']]
        missing = {}
        for e in mine:
            if not e['verdict']:
                missing[e['execution']] = missing.get(e['execution'], 0) + 1
        reviewers.append({
            'role': role, 'harness': definition['harness'], 'model': definition['model'],
            'effort': definition['effort'], 'calls': len(mine), 'valid_calls': len(valid),
            'missing_calls': len(mine) - len(valid), 'missing_by_execution': missing,
            'false_positives': len(fp), 'equivalent_calls': len(equivalent),
            'false_positive_rate': _rate(len(fp), len(equivalent)),
            'false_negatives': len(fn), 'different_calls': len(different),
            'false_negative_rate': _rate(len(fn), len(different)),
            'agreement_rate': _rate(len(valid) - len(fp) - len(fn), len(valid)),
            'false_positives_by_kind': {k: sum(1 for e in fp if e['kind'] == k)
                                        for k in EQUIVALENT_KINDS if any(v['kind'] == k for v in included)},
            'order_dependent_pairs': flagged, 'order_evaluable_pairs': len(evaluable),
            'order_sensitivity': _rate(len(flagged), len(evaluable)),
            'repeat_consistency': _rate(sum(1 for c in cells if not (c['different'] and c['equivalent'])), len(cells)),
            'pairs': pairs})
    return {'schema': REPORT_SCHEMA, 'plan_identity': plan['identity'], 'run_id': ledger['run_id'],
            'calls_sha256': digest(Path(cal) / 'calls.json'), 'seed': plan['seed'], 'root': plan['root'],
            'repeats': ledger['repeats'], 'profile_name': ledger['profile']['name'],
            'included': [{'id': v['id'], 'kind': v['kind'], 'expected': v['expected']} for v in included],
            'excluded': [{'id': v['id'], 'kind': v['kind'], 'expected': v['expected'], 'reason': v['reason']}
                         for v in plan['variants'] if v['status'] == 'excluded'],
            'reviewers': reviewers,
            'limitations': ['Rates measure model judgment on one design and its seeded variants; they are not '
                            'design evidence and do not change adjudication.',
                            'A missing call is excluded from every denominator and listed; it is never agreement.']}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python3 -m agent_ee.fp_calibration')
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare')
    p.add_argument('project')
    p.add_argument('--root', required=True)
    p.add_argument('--seed', type=int, required=True)
    p.add_argument('--out', required=True)
    r = commands.add_parser('run')
    r.add_argument('calibration')
    r.add_argument('--profile', required=True)
    r.add_argument('--repeats', type=int, default=3)
    r.add_argument('--max-seconds', type=float, required=True)
    o = commands.add_parser('report')
    o.add_argument('calibration')
    o.add_argument('--out')
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            plan = prepare(args.project, args.root, args.seed, args.out)
            print(json.dumps({v['id']: v['status'] for v in plan['variants']}, indent=2))
        elif args.command == 'run':
            ledger = run(args.calibration, read_json(args.profile), args.repeats, args.max_seconds,
                         profile_path=Path(args.profile).resolve())
            print(json.dumps({'calls': len(ledger['calls']),
                              'valid': sum(1 for e in ledger['calls'] if e['verdict'])}))
        else:
            value = report(args.calibration)
            if args.out:
                write_json(args.out, value)
            else:
                print(json.dumps(value, indent=2, sort_keys=True))
    except (CalibrationError, ValueError, OSError) as exc:
        print('fp_calibration: ' + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
