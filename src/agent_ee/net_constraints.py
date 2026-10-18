"""Emit declared per-net electrical constraints as KiCad netclasses (AGENTEE-47).

Deterministic, offline transform of captured intent. Widths for declared
currents follow a curve fit of the IPC-2152 generic chart with no modifiers.
Impedance is recorded, never computed. Any unresolved input yields outcome
unknown and no project bytes, never a silent default.
"""
import argparse
import copy
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re

from .board_envelope import validate as validate_envelope
from .netclass_completeness import DEFAULT_KEYS, evaluate as default_gaps, load_json
from .routing_rules import NET, number, rule_text
from .storage import canonical
from .symbol_library import LibraryError, parse_sexpr

SCHEMA = 'agent-ee.net-constraints/1'
RECORD = 'agent-ee.net-constraint-record/1'
CLASS = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}\Z')
RULE_PREFIX = 'agent_ee_netclass_'
CONSTRAINT_FIELDS = ('current_ma', 'track_width_mm', 'clearance_mm', 'diff_pair', 'impedance_ohm')
OZ_MM = Decimal('0.034798')  # 1 oz/ft^2 nominal copper, 1.37 mil
METHOD = {
    'width': 'ipc-2152-generic-chart-fit/1',
    'fit': 'A_mil2 = (117.555*dT^-0.913 + 1.15) * I_A^(0.84*dT^-0.108 + 1.159)',
    'chart': 'IPC-2152 generic chart (3 oz, 0.070 in polyimide, still air, no planes)',
    'modifiers': 'none; the generic chart is the conservative unmodified case',
    'fit_range': {'temperature_rise_c': [2, 100], 'current_ma': [0, 30000]},
    'copper_thickness': 'minimum over stackup copper layers of thickness_mm and copper_weight_oz * 0.034798 mm',
    'rounding': 'width rounded up to 0.001 mm'}


def ipc2152_area_mil2(current_a, rise_c):
    return (117.555 * rise_c ** -0.913 + 1.15) * current_a ** (0.84 * rise_c ** -0.108 + 1.159)


def width_um(area_mil2, thickness_mm):
    return math.ceil(area_mil2 / (thickness_mm / 0.0254) * 0.0254 * 1000)


def exact_pattern(net):
    """KiCad matches a pattern as anchored regex or wildcard; escape both to literal text."""
    return ''.join(c if c.isalnum() or c == '_' else '\\' + c for c in net)


def pair_roles(nets):
    a, b = sorted(nets)
    for pos, neg in (('+', '-'), ('P', 'N')):
        if a[:-1] == b[:-1] and a[:-1] and {a[-1], b[-1]} == {pos, neg}:
            return (a, b) if a[-1] == pos else (b, a)
    raise ValueError('diff_pair requires exactly two nets differing only by a native +/- or P/N suffix')


def positive(value, key):
    """Finite positive number; lengths beyond 1 m are not board geometry."""
    if number(value, key) == 0 or (key.endswith('_mm') and value > 1000):
        raise ValueError(f'{key} must be positive' + (' and at most 1000 mm' if key.endswith('_mm') else ''))
    return value


def parse_document(raw):
    doc = load_json_bytes(raw)
    if not isinstance(doc, dict) or doc.get('schema') != SCHEMA or set(doc) - {'schema', 'temperature_rise_c', 'classes'}:
        raise ValueError(f'expected {SCHEMA} with only schema, temperature_rise_c and classes')
    classes = doc.get('classes')
    if not isinstance(classes, list) or not classes:
        raise ValueError('classes must be a nonempty list')
    names, owners, parsed = set(), {}, []
    for spec in classes:
        if not isinstance(spec, dict) or set(spec) - {'name', 'requirement', 'nets', *CONSTRAINT_FIELDS}:
            raise ValueError('class has unknown fields')
        name = spec.get('name')
        if not isinstance(name, str) or not CLASS.fullmatch(name) or name.lower() == 'default' or name in names:
            raise ValueError(f'invalid, reserved or duplicate class name: {name!r}')
        names.add(name)
        if not isinstance(spec.get('requirement'), str) or not spec['requirement'].strip():
            raise ValueError(f'class {name} requires a mapped requirement')
        nets = spec.get('nets')
        if (not isinstance(nets, list) or not nets or len(set(nets)) != len(nets) or
                any(not isinstance(n, str) or not NET.fullmatch(n) for n in nets)):
            raise ValueError(f'class {name} requires unique exact safe net names')
        for net in nets:
            if net in owners:
                raise ValueError(f'net {net} declared in classes {owners[net]} and {name}')
            owners[net] = name
        if not any(key in spec for key in CONSTRAINT_FIELDS):
            raise ValueError(f'class {name} declares no constraint; unconstrained nets stay in Default')
        item = {'name': name, 'requirement': spec['requirement'], 'nets': sorted(nets)}
        for key in ('current_ma', 'track_width_mm', 'clearance_mm', 'impedance_ohm'):
            if key in spec:
                item[key] = positive(spec[key], key)
        if 'diff_pair' in spec:
            pair = spec['diff_pair']
            if not isinstance(pair, dict) or 'gap_mm' not in pair or set(pair) - {'gap_mm', 'via_gap_mm'}:
                raise ValueError('diff_pair requires gap_mm and optional via_gap_mm')
            for key, value in pair.items():
                positive(value, key)
            if len(nets) != 2:
                raise ValueError('diff_pair requires exactly two nets')
            plus, minus = pair_roles(nets)
            item['diff_pair'] = {**pair, 'positive': plus, 'negative': minus}
        parsed.append(item)
    rise = doc.get('temperature_rise_c')
    if any('current_ma' in c for c in parsed):
        if rise is None or number(rise, 'temperature_rise_c') == 0:
            raise ValueError('current_ma requires an explicit positive temperature_rise_c')
    elif rise is not None:
        raise ValueError('temperature_rise_c without any declared current')
    return rise, sorted(parsed, key=lambda c: c['name'])


def load_json_bytes(raw):
    from .hardware_interface import unique_object
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object,
                          parse_constant=lambda v: (_ for _ in ()).throw(ValueError(f'invalid number: {v}')))
    except UnicodeError as exc:
        raise ValueError('document is not UTF-8') from exc


def template_project(raw):
    project = load_json_bytes(raw)
    gaps = default_gaps(project)
    if gaps is None:
        raise ValueError('template net_settings schema is not the qualified version 5')
    if gaps:
        raise ValueError(f'template Default netclass is incomplete: {gaps}')
    settings = project['net_settings']
    if [c['name'] for c in settings['classes']] != ['Default']:
        raise ValueError('template must carry only the Default netclass')
    if settings.get('netclass_assignments') != {} or settings.get('netclass_patterns') != []:
        raise ValueError('template must carry empty netclass_assignments and netclass_patterns')
    return project


def rule_forms(raw):
    """A .kicad_dru holds several top-level forms; parse them as one wrapped list."""
    return parse_sexpr(b'(' + raw + b'\n)')


def template_rules(raw):
    try:
        roots = rule_forms(raw)
    except LibraryError as exc:
        raise ValueError(f'template rules unreadable: {exc}') from exc
    if not roots or roots[0] != ['version', '1'] or any(not isinstance(r, list) or not r for r in roots):
        raise ValueError('template rules must start with (version 1)')
    for node in roots[1:]:
        if node[0] == 'rule' and len(node) > 1 and str(node[1]).startswith(RULE_PREFIX):
            raise ValueError(f'template rules already contain reserved rule {node[1]}')
    text = raw.decode('utf-8')
    return text if text.endswith('\n') else text + '\n'


def governing_copper(envelope):
    layers = []
    for layer in envelope['stackup']['layers']:
        if layer['kind'] != 'copper':
            continue
        weight = Decimal(layer['copper_weight_oz']) * OZ_MM
        thickness = min(Decimal(layer['thickness_mm']), weight)
        layers.append({'layer': layer['name'], 'thickness_mm': layer['thickness_mm'],
                       'copper_weight_oz': layer['copper_weight_oz'], 'effective_thickness_mm': str(thickness)})
    return min(layers, key=lambda l: Decimal(l['effective_thickness_mm'])), layers


def emit(constraints_raw, project_raw, *, envelope_raw=None, rules_raw=None):
    """Return {name: bytes}; refuse malformed inputs with ValueError."""
    rise, classes = parse_document(constraints_raw)
    project = template_project(project_raw)
    base_rules = template_rules(rules_raw) if rules_raw is not None else None
    envelope = validate_envelope(envelope_raw) if envelope_raw is not None else None
    governing, copper = governing_copper(envelope) if envelope else (None, [])
    default = next(c for c in project['net_settings']['classes'] if c['name'] == 'Default')
    findings, records, emitted, rules = [], [], [], []
    for priority, spec in enumerate(classes):
        record = copy.deepcopy(spec)
        cls = {key: copy.deepcopy(default[key]) for key in sorted(DEFAULT_KEYS)}
        cls.update(name=spec['name'], priority=priority)
        minimum_um = (math.ceil(Decimal(str(spec['track_width_mm'])) * 1000)
                      if 'track_width_mm' in spec else None)
        if 'current_ma' in spec:
            current = spec['current_ma']
            if not (2 <= rise <= 100 and current <= 30000):
                findings.append({'code': 'outside_fit_range', 'class': spec['name'],
                                 'current_ma': current, 'temperature_rise_c': rise})
            elif governing is None:
                findings.append({'code': 'current_without_stackup', 'class': spec['name']})
            else:
                area = ipc2152_area_mil2(current / 1000, rise)
                ipc = width_um(area, float(governing['effective_thickness_mm']))
                record['ipc2152'] = {'area_mil2': round(area, 6), 'width_mm': ipc / 1000,
                                     'governing_layer': governing['layer'],
                                     'thickness_mm': governing['effective_thickness_mm']}
                minimum_um = max(minimum_um or 0, ipc)
        if minimum_um is not None:
            record['min_track_width_mm'] = minimum_um / 1000
            cls['track_width'] = minimum_um / 1000
            marker = RULE_PREFIX + spec['name']
            record['rule'] = marker
            rules.append(rule_text(marker, f"A.NetClass == '{spec['name']}'",
                                   f'track_width (min {minimum_um / 1000:.12g}mm)').split('\n', 1)[1])
        if 'clearance_mm' in spec:
            cls['clearance'] = spec['clearance_mm']
        if 'diff_pair' in spec:
            cls['diff_pair_width'] = cls['track_width']
            cls['diff_pair_gap'] = spec['diff_pair']['gap_mm']
            if 'via_gap_mm' in spec['diff_pair']:
                cls['diff_pair_via_gap'] = spec['diff_pair']['via_gap_mm']
        if 'impedance_ohm' in spec:
            if envelope is None:
                findings.append({'code': 'impedance_without_stackup', 'class': spec['name']})
            record['impedance'] = {'target_ohm': record.pop('impedance_ohm'), 'status': 'recorded_not_enforced',
                                   'stackup_sha256': envelope['envelope_sha256'] if envelope else None}
        records.append(record)
        emitted.append(cls)
    outcome = 'unknown' if findings else 'pass'
    report = {'schema': RECORD, 'outcome': outcome, 'acceptance_ready': False, 'findings': findings,
              'source_sha256': hashlib.sha256(constraints_raw).hexdigest(),
              'template_project_sha256': hashlib.sha256(project_raw).hexdigest(),
              'template_rules_sha256': hashlib.sha256(rules_raw).hexdigest() if rules_raw is not None else None,
              'envelope_sha256': envelope['envelope_sha256'] if envelope else None,
              'method': METHOD, 'temperature_rise_c': rise, 'stackup_copper': copper,
              'classes': records, 'project_sha256': None, 'rules_sha256': None,
              'nets': {net: c['name'] for c in records for net in c['nets']},
              'limitations': ['Impedance targets are recorded only; no geometry is computed or enforced.',
                              'Widths use the unmodified IPC-2152 generic-chart fit; no plane, board '
                              'thickness or copper-thickness modifiers.',
                              'Net names are exact native names; undeclared nets remain in Default.']}
    artifacts = {}
    if outcome == 'pass':
        settings = project['net_settings']
        settings['classes'] = [*settings['classes'], *emitted]
        settings['netclass_patterns'] = [{'netclass': c['name'], 'pattern': exact_pattern(net)}
                                         for c in records for net in c['nets']]
        if default_gaps(project) != []:
            raise ValueError('emitted Default netclass lost a required key')
        pro = (json.dumps(project, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
        artifacts['project'] = pro
        report['project_sha256'] = hashlib.sha256(pro).hexdigest()
        if rules or base_rules is not None:
            dru = ((base_rules or '(version 1)\n') + ''.join(rules)).encode('utf-8')
            rule_forms(dru)
            artifacts['rules'] = dru
            report['rules_sha256'] = hashlib.sha256(dru).hexdigest()
    artifacts['record'] = canonical(report) + b'\n'
    return artifacts


def net_minimums(record_path, project_path):
    """Per-net copper minimums from a pass record whose classes the captured project carries."""
    record = load_json(record_path)
    if not isinstance(record, dict) or record.get('schema') != RECORD or record.get('outcome') != 'pass':
        raise ValueError('net constraints require a passing agent-ee.net-constraint-record/1')
    settings = load_json(project_path).get('net_settings')
    if not isinstance(settings, dict) or not isinstance(settings.get('classes'), list):
        raise ValueError('captured project has no netclasses')
    classes = {c.get('name'): c for c in settings['classes'] if isinstance(c, dict)}
    patterns = settings.get('netclass_patterns')
    if not isinstance(patterns, list):
        raise ValueError('captured project has no netclass_patterns')
    limits = {}
    for spec in record['classes']:
        expected = sorted(exact_pattern(n) for n in spec['nets'])
        actual = sorted(str(p.get('pattern')) for p in patterns
                        if isinstance(p, dict) and p.get('netclass') == spec['name'])
        if spec['name'] not in classes or actual != expected:
            raise ValueError(f'captured project does not carry recorded netclass {spec["name"]}')
        if 'min_track_width_mm' not in spec:
            continue
        width = classes[spec['name']].get('track_width')
        if isinstance(width, bool) or not isinstance(width, (int, float)) or width < spec['min_track_width_mm']:
            raise ValueError(f'captured netclass {spec["name"]} track_width is below the recorded minimum')
        limits.update({net: spec['min_track_width_mm'] for net in spec['nets']})
    return limits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('constraints', type=Path)
    parser.add_argument('--project', required=True, type=Path, help='template .kicad_pro (Default netclass only)')
    parser.add_argument('--rules', type=Path, help='template .kicad_dru to extend')
    parser.add_argument('--envelope', type=Path, help='agent-ee.envelope/1 stackup source')
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.project.suffix != '.kicad_pro':
            raise ValueError('--project must be a .kicad_pro')
        artifacts = emit(args.constraints.read_bytes(), args.project.read_bytes(),
                         envelope_raw=args.envelope.read_bytes() if args.envelope else None,
                         rules_raw=args.rules.read_bytes() if args.rules else None)
        args.out.mkdir(parents=False, exist_ok=False)
        names = {'project': args.project.name, 'rules': args.project.with_suffix('.kicad_dru').name,
                 'record': 'net-constraints.record.json'}
        for key, raw in artifacts.items():
            (args.out / names[key]).write_bytes(raw)
    except (ValueError, OSError) as exc:
        parser.exit(2, f'net constraints unavailable: {exc}\n')
    return 0 if 'project' in artifacts else 1


if __name__ == '__main__':
    raise SystemExit(main())
