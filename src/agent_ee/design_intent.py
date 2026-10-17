"""Offline stage-one intent import. Derivatives confer no design acceptance."""
import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re

from .hardware_interface import text
from .storage import canonical


def load_json(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f'duplicate JSON key: {key}')
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda v: (_ for _ in ()).throw(ValueError(f'invalid number: {v}')))


def component_counts(value):
    """Count explicit names only; commas without following whitespace stay in names."""
    counts = Counter()
    if isinstance(value, dict):
        entries = value.items()
    elif isinstance(value, (str, list)):
        tokens = re.split(r',\s+', value) if isinstance(value, str) else value
        entries = []
        for token in tokens:
            token = text(token).strip()
            match = re.fullmatch(r'(\d+)\s*[x×]\s+(.+)', token)
            entries.append((match[2], int(match[1])) if match else (token, 1))
    else:
        raise ValueError('components must be text, a name list or a name/count object')
    for name, count in entries:
        name = text(name).strip()
        if type(count) is not int or count <= 0:
            raise ValueError('component count must be a positive integer')
        counts[name] += count
    if not counts:
        raise ValueError('components must not be empty')
    return dict(sorted(counts.items()))


def voltage_targets(value):
    """Only explicit signed numeric voltages; annotations remain unsupported."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError('voltage must be numeric text or a number')
    values = []
    for token in str(value).split(','):
        token = token.strip()
        if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\s*V?', token):
            raise ValueError('unparseable voltage')
        try:
            number = Decimal(token.removesuffix('V').strip())
        except InvalidOperation as exc:
            raise ValueError('unparseable voltage') from exc
        if not number.is_finite() or number == 0:
            raise ValueError('zero or non-finite voltage')
        normalized = str(number)
        if '.' in normalized and 'E' not in normalized:
            normalized = normalized.rstrip('0').rstrip('.')
        values.append(normalized)
    return values


def import_record(raw, *, out_justification=None):
    """Return deterministic artifact bytes, hashing the exact supplied JSON bytes.

    A pass describes only the bounded lint. It never completes an interface or
    proves all natural-language requirements consistent or executable.
    """
    record = load_json(raw)
    required = {'id', 'task', 'input_nodes', 'output_nodes', 'input_voltage',
                'output_voltage', 'components', 'sub_module_name'}
    optional = {'level', 'type', 'n_gt_components', 'gt_component_types'}
    if not isinstance(record, dict) or not required <= record.keys() or record.keys() - required - optional:
        raise ValueError('invalid intent fields')
    rid = record['id']
    if not ((type(rid) is int and rid >= 0) or (isinstance(rid, str) and rid.strip())):
        raise ValueError('invalid record id')
    task = text(record['task'])
    text(record['sub_module_name'])
    counts = component_counts(record['components'])
    findings, waivers, nodes, targets = [], [], {}, {}
    if out_justification is not None:
        text(out_justification)
    for field in ('input_nodes', 'output_nodes'):
        value = record[field]
        items = [part.strip() for part in text(value).split(',')] if isinstance(value, str) else value
        if not isinstance(items, list) or not items or any(not isinstance(n, str) or not n.strip() for n in items):
            raise ValueError('interface nodes must be nonempty names')
        items = [name.strip() for name in items]
        nodes[field] = items
        if len(set(items)) != len(items):
            findings.append({'code': 'duplicate_interface_net', 'field': field})
        for name in items:
            if name.upper() in {'OUT', 'INPUT', 'OUTPUT', 'TBD', '?'}:
                if name == 'OUT' and out_justification is not None:
                    waivers.append({'field': field, 'net': name, 'justification': out_justification})
                else:
                    findings.append({'code': 'placeholder_interface_net', 'field': field, 'net': name})
    for field in ('input_voltage', 'output_voltage'):
        try:
            targets[field] = {'volts': voltage_targets(record[field]), 'raw': record[field]}
        except ValueError as exc:
            findings.append({'code': 'unknown_voltage', 'field': field, 'reason': str(exc)})
            targets[field] = {'volts': None, 'raw': record[field]}
    # Deliberately lexical: exact declared part names after explicit counts only.
    for name, count in counts.items():
        matches = re.findall(r'(?<![\w.])(\d+)\s*(?:[x×]\s*|\s+)' + re.escape(name) + r'(?![\w/-]|\.[A-Za-z0-9])', task)
        if matches and any(int(found) != count for found in matches):
            findings.append({'code': 'component_count_mismatch', 'part': name,
                             'expected': count, 'prose_counts': [int(m) for m in matches]})
    for field, expected in [('n_gt_components', sum(counts.values())), ('gt_component_types', len(counts))]:
        if field in record and (type(record[field]) is not int or record[field] != expected):
            findings.append({'code': 'component_metadata_mismatch', 'field': field, 'expected': expected})
    source = hashlib.sha256(raw).hexdigest()
    report = {'schema': 'agent-ee.intent/1', 'id': str(rid), 'source_sha256': source,
              'sub_module_name': record['sub_module_name'], 'outcome': 'unknown' if findings else 'pass',
              'findings': findings, 'waivers': waivers, 'operating_targets': targets,
              'lint_coverage': 'lexical explicit counts, interface names, numeric voltage syntax only',
              'acceptance_ready': False}
    stub = {'schema': 'agent-ee.interface-stub/1', 'source_sha256': source, 'complete': False,
            'required_nets': sorted(set(nodes['input_nodes'] + nodes['output_nodes'])),
            'interfaces': nodes, 'unresolved': ['native components', 'terminal pin identities',
                                             'owner invariants', 'executable requirement coverage']}
    return {'source-record.json': raw, 'requirements.md': task.encode('utf-8'),
            'requirements.json': canonical({'schema': 'agent-ee.requirements/1', 'requirements': [
                {'id': 'R-intent-' + hashlib.sha256(str(rid).encode()).hexdigest(),
                 'text': task, 'source_question_ids': []}]}) + b'\n',
            'intent.json': canonical(report) + b'\n',
            'hardware-interface.stub.json': canonical(stub) + b'\n',
            'component-expectation.json': canonical({'schema': 'agent-ee.component-expectation/1',
                'source_sha256': source, 'required_components': counts}) + b'\n'}


def write_artifacts(artifacts, output):
    """Write only a new staging directory; source and existing outputs stay intact."""
    output = Path(output)
    output.mkdir(parents=False, exist_ok=False)
    for name, raw in artifacts.items():
        (output / name).write_bytes(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('record', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--out-justification', help='explicit owner justification for a real OUT net')
    args = parser.parse_args()
    try:
        artifacts = import_record(args.record.read_bytes(), out_justification=args.out_justification)
        write_artifacts(artifacts, args.out)
    except (ValueError, OSError) as exc:
        parser.exit(2, f'intent import unavailable: {exc}\n')
    return 0 if load_json(artifacts['intent.json'])['outcome'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
