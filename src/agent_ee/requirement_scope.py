"""Deterministic stage-1 compiler: requirement record plus verbatim spec to an agent-ee.scope/2.

Run ``python3 -m agent_ee.requirement_scope RECORD --out NEW_DIRECTORY`` (see
docs/requirement-scope.md). No inference: every requirement cites an exact spec
quote, every spec sentence is cited, and every requirement and check is mapped.
"""
import argparse
from decimal import Decimal
import hashlib
import math
from pathlib import Path
import re

from .baseline import compile_baseline
from .design_intent import JsonNumber, load_json
from .findings import safe_id
from .hardware_interface import exact_keys, strings, text
from .storage import digest, json_bytes

RECORD_SCHEMA = 'agent-ee.requirement-record/1'
RECEIPT_SCHEMA = 'agent-ee.requirement-compile-receipt/1'
COMPILER = 'agent-ee.requirement-scope/1'
STOCK = '/usr/share/kicad'
SCAFFOLD = 'scaffold'
SPEC_NAME, RECORD_NAME = 'spec.md', 'record.json'
CHECK_ID = re.compile(r'[a-z][a-z0-9_-]{0,63}')
NUMBER = re.compile(r'(?<![\w.])(\d+(?:\.\d+)?)(?!\.?\d)')
# Record fields per check kind; the compiler adds every other scope parameter.
CHECK_FIELDS = {
    'erc': 'id kind', 'drc': 'id kind', 'netlist': 'id kind',
    'geometry': 'id kind min_track_width_mm',
    'rc_transient': 'id kind input_net output_net ground_net step_v stop_ms bounds samples_ms',
    'fab_export': 'id kind',
}


class Refused(ValueError):
    """The record is well formed but cannot compile; ``findings`` names every reason."""

    def __init__(self, findings):
        super().__init__('; '.join(f['message'] for f in findings))
        self.findings = findings


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def number(value, label):
    """(float, exact Decimal) of a JSON number; strings and booleans are refused."""
    if isinstance(value, JsonNumber):
        exact = Decimal(value.token)
    elif isinstance(value, int) and not isinstance(value, bool):
        exact = Decimal(value)
    else:
        raise ValueError(f'{label} must be a JSON number')
    if not exact.is_finite() or not math.isfinite(float(exact)):
        raise ValueError(f'{label} must be finite')
    return float(exact), exact


def text_list(values, label, *, empty=False):
    if empty and values == []:
        return []
    try:
        strings(values)
    except ValueError as exc:
        raise ValueError(f'{label}: {exc}') from None
    return list(values)


def label(at_ms):
    return 'vout_' + format(at_ms.normalize(), 'f').replace('.', 'p') + 'ms'


def spec_sentences(raw):
    """Whitespace-normalized spec body (Markdown headings excluded) and its sentence spans."""
    lines = raw.decode('utf-8').splitlines()
    body = ' '.join(' '.join(line for line in lines if not line.lstrip().startswith('#')).split())
    if not body:
        raise ValueError('spec has no body text')
    spans, start = [], 0
    for match in re.finditer(r'(?<=\.) ', body):
        spans.append((start, match.start()))
        start = match.end()
    spans.append((start, len(body)))
    return body, spans


def check_specs(declared, findings):
    """Scope check specifications from the record's typed declarations, in record order."""
    if not isinstance(declared, list) or not declared:
        raise ValueError('record checks must be a nonempty list')
    specs, bounds, netlists = [], {}, []
    for item in declared:
        kind = item.get('kind') if isinstance(item, dict) else None
        if kind not in CHECK_FIELDS:
            raise ValueError(f'unsupported record check kind: {kind!r}')
        exact_keys(item, CHECK_FIELDS[kind])
        cid = item['id']
        if not isinstance(cid, str) or not CHECK_ID.fullmatch(cid) or any(s['id'] == cid for s in specs):
            raise ValueError(f'invalid or duplicate check ID: {cid!r}')
        spec = {'id': cid, 'kind': kind}
        if kind in ('rc_transient', 'fab_export'):
            if len(netlists) != 1:
                findings.append({'code': 'prerequisite', 'id': cid,
                                 'message': f'check {cid} needs exactly one preceding netlist check, found {len(netlists)}'})
                prerequisite = None
            else:
                prerequisite = netlists[0]
        if kind == 'netlist':
            netlists.append(cid)
        elif kind == 'geometry':
            width, exact = number(item['min_track_width_mm'], f'{cid} min_track_width_mm')
            spec['params'] = {'criterion': 'copper-width/1', 'covered_primitives': ['PCB_TRACK', 'PCB_ARC'],
                              'min_track_width_mm': width, 'exclude_nets': []}
            bounds[cid] = {'min_track_width_mm': exact}
        elif kind == 'rc_transient':
            step, step_exact = number(item['step_v'], f'{cid} step_v')
            bounds[cid] = {'step_v': step_exact}
            measurements = []
            if not isinstance(item['bounds'], list) or not item['bounds']:
                raise ValueError(f'{cid} bounds must be a nonempty list')
            for index, bound in enumerate(item['bounds']):
                exact_keys(bound, 'at_ms min max')
                values = {k: number(bound[k], f'{cid} bounds[{index}].{k}') for k in ('at_ms', 'min', 'max')}
                bounds[cid].update({f'bounds[{index}].{k}': v[1] for k, v in values.items()})
                measurements.append({'name': label(values['at_ms'][1]), 'at_ms': values['at_ms'][0],
                                     'min': values['min'][0], 'max': values['max'][0]})
            if not isinstance(item['samples_ms'], list):
                raise ValueError(f'{cid} samples_ms must be a list')
            for index, sample in enumerate(item['samples_ms']):
                at, exact = number(sample, f'{cid} samples_ms[{index}]')
                measurements.append({'name': label(exact), 'at_ms': at})
            if len({m['name'] for m in measurements}) != len(measurements):
                raise ValueError(f'{cid} repeats a measurement time')
            spec['params'] = {'netlist_check': prerequisite, 'input_net': text(item['input_net']),
                              'output_net': text(item['output_net']), 'ground_net': text(item['ground_net']),
                              'step_v': step, 'stop_ms': number(item['stop_ms'], f'{cid} stop_ms')[0],
                              'measurements': measurements}
        elif kind == 'fab_export':
            spec['params'] = {'netlist_check': prerequisite}
        specs.append(spec)
    return specs, bounds


def cite(locator, body, spans, owner, findings):
    """Sentence index of an exact, unique, single-sentence spec quote; None with a finding otherwise."""
    if not isinstance(locator, str) or not locator.strip() or ' '.join(locator.split()) != locator:
        raise ValueError(f'{owner} locator must be whitespace-normalized nonempty text')
    start = body.find(locator)
    if start < 0 or body.find(locator, start + 1) >= 0:
        findings.append({'code': 'locator', 'id': owner, 'message':
                         f'{owner} locator {locator!r} must occur exactly once in the spec, found '
                         f'{body.count(locator)}'})
        return None
    for index, (first, last) in enumerate(spans):
        if first <= start and start + len(locator) <= last:
            return {'sentence': index, 'span': [start, start + len(locator)]}
    findings.append({'code': 'locator', 'id': owner,
                     'message': f'{owner} locator {locator!r} spans more than one spec sentence'})
    return None


def compile_record(record_raw, spec_raw):
    """(scope, receipt) for exact record and spec bytes; raises Refused or ValueError."""
    record = load_json(record_raw)
    exact_keys(record, 'schema name scope spec project stock requirements checks context unchecked limitations')
    if record['schema'] != RECORD_SCHEMA:
        raise ValueError(f'record schema must be {RECORD_SCHEMA}')
    exact_keys(record['spec'], 'path sha256')
    exact_keys(record['project'], 'name')
    exact_keys(record['stock'], 'symbols footprints')
    project = text(record['project']['name'])
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', project):
        raise ValueError('project name must be a plain file stem')
    findings = []
    if record['spec']['sha256'] != sha256(spec_raw):
        findings.append({'code': 'spec_hash', 'id': 'spec',
                         'message': 'record spec.sha256 does not match the spec bytes'})
    body, spans = spec_sentences(spec_raw)
    checks, bounds = check_specs(record['checks'], findings)
    check_ids = [c['id'] for c in checks]
    cited, baseline_requirements, mapped = {}, [], set()
    if not isinstance(record['requirements'], list) or not record['requirements']:
        raise ValueError('record requires at least one requirement')
    receipt_requirements = []
    for requirement in record['requirements']:
        exact_keys(requirement, 'id statement locator checks')
        rid = safe_id(requirement['id'], 'requirement id')
        if any(r['id'] == rid for r in baseline_requirements):
            raise ValueError(f'duplicate requirement {rid}')
        bound = requirement['checks']
        if not isinstance(bound, list) or any(not isinstance(c, str) for c in bound) or len(set(bound)) != len(bound):
            raise ValueError(f'requirement {rid} checks must be a list of unique check IDs')
        if not bound:
            findings.append({'code': 'unmapped_requirement', 'id': rid,
                             'message': f'requirement {rid} maps to no check'})
        for cid in bound:
            if cid not in check_ids:
                findings.append({'code': 'unknown_check', 'id': rid,
                                 'message': f'requirement {rid} maps to undeclared check {cid}'})
        mapped.update(bound)
        where = cite(requirement['locator'], body, spans, rid, findings)
        if where is not None:
            cited.setdefault(where['sentence'], []).append(rid)
        baseline_requirements.append({'id': rid, 'statement': text(requirement['statement']),
                                      'source': {'path': f'requirements/{SPEC_NAME}',
                                                 'locator': requirement['locator']},
                                      'checks': bound})
        receipt_requirements.append({'id': rid, 'checks': bound, 'locator': requirement['locator'],
                                     'source': where})
    for cid in check_ids:
        if cid not in mapped:
            findings.append({'code': 'unmapped_check', 'id': cid,
                             'message': f'check {cid} is mapped to no requirement'})
    # A declared bound must be written in the spec text its requirements cite.
    for cid, values in bounds.items():
        quotes = [r['locator'] for r in record['requirements'] if cid in r['checks']]
        written = {Decimal(token) for quote in quotes for token in NUMBER.findall(quote)}
        for field, value in values.items():
            if value not in written:
                findings.append({'code': 'unsourced_bound', 'id': cid, 'message':
                                 f'check {cid} {field}={value} does not appear in a cited requirement quote'})
    notes = {}
    for category in ('context', 'unchecked'):
        if not isinstance(record[category], list):
            raise ValueError(f'record {category} must be a list')
        notes[category] = []
        for index, note in enumerate(record[category]):
            exact_keys(note, 'locator reason')
            where = cite(note['locator'], body, spans, f'{category}[{index}]', findings)
            if where is not None:
                cited.setdefault(where['sentence'], []).append(f'{category}[{index}]')
            notes[category].append({'locator': note['locator'], 'reason': text(note['reason']), 'source': where})
    for index, (first, last) in enumerate(spans):
        if index not in cited:
            findings.append({'code': 'uncited_sentence', 'id': f'sentence[{index}]', 'message':
                             f'spec sentence {body[first:last]!r} is cited by no requirement, context or '
                             'unchecked entry'})
    if findings:
        raise Refused(findings)
    limitations = text_list(record['limitations'], 'record limitations', empty=True) + [
        f'Not checked: "{n["locator"]}" ({n["reason"]})' for n in notes['unchecked']]
    scope = {
        'schema': 'agent-ee.scope/2',
        'name': text(record['name']),
        'scope': text(record['scope']),
        'limitations': limitations,
        'inputs': [
            {'path': f'{SCAFFOLD}/project', 'at': 'project'},
            {'path': f'{STOCK}/symbols', 'at': 'stock/symbols', 'include': text_list(record['stock']['symbols'], 'stock symbols')},
            {'path': f'{STOCK}/footprints', 'at': 'stock/footprints',
             'include': text_list(record['stock']['footprints'], 'stock footprints')},
            {'path': f'{STOCK}/template', 'at': 'stock/template', 'include': ['sym-lib-table', 'fp-lib-table']},
            {'path': f'{SCAFFOLD}/requirements', 'at': 'requirements'},
        ],
        'schematic': f'project/{project}.kicad_sch',
        'board': f'project/{project}.kicad_pcb',
        'mutable_inputs': [f'project/{project}.kicad_sch', f'project/{project}.kicad_pcb'],
        'requirements': [f'requirements/{SPEC_NAME}'],
        'checks': checks,
        'baseline': {'requirements': baseline_requirements,
                     'targets': [f'requirements/{RECORD_NAME}'],
                     'reference_architectures': [f'requirements/{SPEC_NAME}']},
    }
    from .capabilities import describe_check
    from .cli import validate_scope
    validate_scope(scope)
    # The baseline binds only requirement sources and criteria, so its identity is
    # known before the scaffold project exists and is reproduced at capture.
    files = {f'requirements/{SPEC_NAME}': sha256(spec_raw), f'requirements/{RECORD_NAME}': sha256(record_raw)}
    # The scaffold supplies the native design later; static descriptors need only its declared paths.
    declared = {**files, scope['schematic']: None, scope['board']: None}
    for spec in checks:
        descriptor = describe_check(spec, check_specs=checks, files=declared, scope=scope)
        if descriptor['status'] != 'supported':
            findings.append({'code': 'unsupported_check', 'id': spec['id'], 'message':
                             f"check {spec['id']} is not statically supported: {descriptor['unsupported_reasons']}"})
    if findings:
        raise Refused(findings)
    baseline = compile_baseline(scope, files)
    receipt = {
        'schema': RECEIPT_SCHEMA,
        'compiler': {'version': COMPILER, 'module': __name__, 'source_sha256': digest(__file__)},
        'record_sha256': sha256(record_raw),
        'spec_sha256': sha256(spec_raw),
        'scope_sha256': sha256(json_bytes(scope)),
        'baseline_id': baseline['baseline_id'],
        'requirements': receipt_requirements,
        'context': notes['context'],
        'unchecked': notes['unchecked'],
        'spec_sentences': len(spans),
    }
    return scope, receipt


def compile_file(record_path):
    """Compile a record file whose spec path is relative to the record."""
    record_path = Path(record_path)
    record_raw = record_path.read_bytes()
    spec = load_json(record_raw).get('spec')
    if not isinstance(spec, dict) or not isinstance(spec.get('path'), str) or Path(spec['path']).is_absolute():
        raise ValueError('record spec.path must be a relative path')
    spec_raw = (record_path.parent / spec['path']).read_bytes()
    return record_raw, spec_raw, *compile_record(record_raw, spec_raw)


def write_outputs(output, record_raw, spec_raw, scope, receipt):
    """NEW_DIRECTORY/{scope.json, receipt.json, scaffold/requirements/}; the scaffold project is AGENTEE-87's."""
    output = Path(output)
    output.mkdir(parents=False, exist_ok=False)
    requirements = output / SCAFFOLD / 'requirements'
    requirements.mkdir(parents=True)
    (requirements / SPEC_NAME).write_bytes(spec_raw)
    (requirements / RECORD_NAME).write_bytes(record_raw)
    (output / 'scope.json').write_bytes(json_bytes(scope))
    (output / 'receipt.json').write_bytes(json_bytes(receipt))


def main(argv=None):
    import json
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('record', type=Path)
    parser.add_argument('--out', required=True, type=Path, help='new directory for scope, receipt and requirements')
    args = parser.parse_args(argv)
    try:
        record_raw, spec_raw, scope, receipt = compile_file(args.record)
    except Refused as exc:
        json.dump({'outcome': 'refused', 'findings': exc.findings}, sys.stdout, indent=2, sort_keys=True)
        print()
        return 1
    except (ValueError, OSError) as exc:
        parser.exit(2, f'requirement record not compiled: {exc}\n')
    try:
        write_outputs(args.out, record_raw, spec_raw, scope, receipt)
    except OSError as exc:
        parser.exit(2, f'outputs not written: {exc}\n')
    print(json.dumps({'outcome': 'compiled', 'baseline_id': receipt['baseline_id'],
                      'scope_sha256': receipt['scope_sha256']}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
