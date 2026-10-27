"""Read-only native verification of declared net classes in a saved candidate."""

from .check_registry import register as _register_check
from . import check_prerequisites as _requires
import argparse
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

from .candidate import safe_relative
from .net_constraints import RECORD, RULE_PREFIX, parse_document, positive
from .netclass_completeness import load_json
from .storage import digest, write_json


def capability(params, scope, files, inputs):
    if not isinstance(params, dict) or set(params) != {'project', 'record', 'constraints'}:
        raise ValueError('net_class_assignment requires project, record and constraints')
    board = scope.get('board')
    if not isinstance(board, str) or params['project'] != str(Path(board).with_suffix('.kicad_pro')):
        raise ValueError('project must be the board-sibling .kicad_pro')
    paths = [board, *params.values()]
    for rel in paths:
        if str(safe_relative(rel)) != rel or rel not in files:
            raise ValueError(f'net-class input must be captured: {rel}')
    if len(set(paths)) != len(paths) or inputs is None:
        raise ValueError('net-class inputs must be distinct captured files')
    root = Path(inputs)
    _, declared = parse_document((root / params['constraints']).read_bytes())
    record = load_json(root / params['record'])
    if (not isinstance(record, dict) or record.get('schema') != RECORD or record.get('outcome') != 'pass' or
            record.get('source_sha256') != digest(root / params['constraints'])):
        raise ValueError('net-class record must pass and bind the captured constraints')
    recorded = record.get('classes')
    if not isinstance(recorded, list) or len(recorded) != len(declared):
        raise ValueError('net-class record classes differ from constraints')
    for spec, actual in zip(declared, recorded):
        if not isinstance(actual, dict) or any(actual.get(k) != v for k, v in spec.items() if k != 'impedance_ohm'):
            raise ValueError('net-class record declaration differs from constraints')
        if 'track_width_mm' in spec or 'current_ma' in spec:
            positive(actual.get('min_track_width_mm'), 'min_track_width_mm')
            if actual.get('rule') != RULE_PREFIX + spec['name'] or not record.get('rules_sha256'):
                raise ValueError('recorded minimum width requires emitted rules')
            if actual['min_track_width_mm'] < spec.get('track_width_mm', 0):
                raise ValueError('record width is below declared width')
        impedance = actual.get('impedance')
        if 'impedance_ohm' in spec and (not isinstance(impedance, dict) or
                                         impedance.get('status') != 'recorded_not_enforced' or
                                         impedance.get('target_ohm') != spec['impedance_ohm']):
            raise ValueError('impedance must remain recorded_not_enforced')
    rules = str(Path(board).with_suffix('.kicad_dru'))
    immutable = [*params.values()]
    if record.get('rules_sha256') is not None:
        if rules not in files:
            raise ValueError('net-class rules must be captured')
        immutable.append(rules)
    return {'immutable_inputs': sorted(immutable), 'predicates': ['netclass:effective-assignment'],
            'record': record, 'rules': rules}


def extract(board_path):
    """Load the saved board and its sibling project in a fresh pcbnew process."""
    import pcbnew
    board_path = Path(board_path)
    project = board_path.with_suffix('.kicad_pro')
    board = pcbnew.LoadBoard(str(board_path))
    if board is None:
        raise ValueError('native board load failed')
    classes = {str(name): {'track_width_nm': int(cls.GetTrackWidth()),
                           'clearance_nm': int(cls.GetClearance())}
               for name, cls in board.GetAllNetClasses().items()}
    nets = {str(net.GetNetname()): str(net.GetNetClassName())
            for net in board.GetNetInfo().NetsByNetcode().values() if net.GetNetname()}
    patterns = load_json(project).get('net_settings', {}).get('netclass_patterns', [])
    if (not isinstance(patterns, list) or any(not isinstance(p, dict) or
            not isinstance(p.get('netclass'), str) or not isinstance(p.get('pattern'), str)
            for p in patterns)):
        raise ValueError('project netclass patterns must name a class and pattern')
    matches = []
    for entry in patterns:
        # Use KiCad's regex/wildcard matcher, independently of other assignments
        # and priorities that may combine classes on the saved board.
        settings = pcbnew.NET_SETTINGS(None, '')
        name = entry['netclass']
        settings.SetNetclass(name, pcbnew.NETCLASS(name))
        settings.SetNetclassPatternAssignment(entry['pattern'], name)
        matches.append({**entry, 'nets': sorted(net for net in nets
                        if settings.GetEffectiveNetClass(net).GetName() == name)})
    return {'schema': 'agent-ee.net-class-native/1', 'pcbnew_version': pcbnew.Version(),
            'board_sha256': digest(board_path), 'project_sha256': digest(project),
            'classes': classes, 'nets': nets, 'patterns': matches}


def evaluate(record, native):
    if not isinstance(native.get('classes'), dict) or not isinstance(native.get('nets'), dict):
        raise ValueError('native class and net inventories must be objects')
    for name, cls in native['classes'].items():
        if (not isinstance(name, str) or not isinstance(cls, dict) or
                any(type(cls.get(k)) is not int or cls[k] < 0 for k in ('track_width_nm', 'clearance_nm'))):
            raise ValueError('native class dimensions must be nonnegative integer nanometres')
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in native['nets'].items()):
        raise ValueError('native net assignments must be text')
    patterns = native.get('patterns', [])
    if (not isinstance(patterns, list) or any(not isinstance(p, dict) or
            not isinstance(p.get('netclass'), str) or not isinstance(p.get('pattern'), str) or
            not isinstance(p.get('nets'), list) or
            any(not isinstance(n, str) or n not in native['nets'] for n in p['nets'])
            for p in patterns)):
        raise ValueError('native pattern matches must name classes, patterns and board nets')
    defects = []
    for spec in record['classes']:
        name = spec['name']
        cls = native['classes'].get(name)
        for entry in patterns:
            if entry['netclass'] != name:
                continue
            if r'\/' in entry['pattern']:
                defects.append({'class': name, 'pattern': entry['pattern'],
                                'reason': 'legacy_escaped_slash'})
            for net in sorted(set(entry['nets']) - set(spec['nets'])):
                defects.append({'class': name, 'pattern': entry['pattern'], 'net': net,
                                'reason': 'pattern_matches_undeclared_net'})
        if cls is None:
            defects.append({'class': name, 'reason': 'class_absent'})
        for net in spec['nets']:
            assigned = native['nets'].get(net)
            if assigned != name:
                defects.append({'class': name, 'net': net, 'effective_class': assigned,
                                'reason': 'net_absent' if assigned is None else 'net_unassigned'})
        if cls is not None:
            for field, native_key in (('min_track_width_mm', 'track_width_nm'), ('clearance_mm', 'clearance_nm')):
                if field not in spec:
                    continue
                minimum_nm = int((Decimal(str(spec[field])) * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
                if cls[native_key] < minimum_nm:
                    defects.append({'class': name, 'reason': field + '_below_minimum',
                                    'expected_mm': spec[field], 'actual_nm': cls[native_key]})
    return defects


def check_net_class_assignment(ctx):
    from .checks import CheckError
    try:
        descriptor = capability(ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise CheckError('unsupported', str(exc)) from exc
    board = ctx.candidate_path(ctx.scope['board'])
    ctx.execute(['/usr/bin/python3', '-B', '-m', 'agent_ee.net_class_assignment', board,
                 '--out', '/work/net-class-native.json'], timeout=120)
    ctx.evidence('net-class-native.json')
    native = ctx.load_json('net-class-native.json', 'native net classes')
    record = descriptor['record']
    try:
        if (native['schema'] != 'agent-ee.net-class-native/1' or
                native['board_sha256'] != ctx.manifest['files'][ctx.scope['board']] or
                native['project_sha256'] != ctx.manifest['files'][ctx.params['project']]):
            raise ValueError('native net-class report does not bind candidate')
        if 'patterns' not in native:
            raise ValueError('native net-class report lacks project pattern matches')
        defects = evaluate(record, native)
        if record.get('project_sha256') != native['project_sha256']:
            defects.append({'reason': 'project_differ_from_record'})
        if record.get('rules_sha256') is not None and digest(ctx.inputs / descriptor['rules']) != record['rules_sha256']:
            defects.append({'reason': 'rules_differ_from_record'})
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise CheckError('malformed', str(exc)) from exc
    report = dict(schema='agent-ee.net-class-result/1', candidate_id=ctx.manifest['candidate_id'],
                  native=native, record_sha256=digest(ctx.inputs / ctx.params['record']), defects=defects)
    write_json(ctx.work / 'net-class-assignment.json', report)
    ctx.evidence('net-class-assignment.json')
    ctx.result['details'].update(report)
    ctx.result['limitations'].append('Saved effective assignment and class dimensions only; impedance recorded_not_enforced. '
                                     'Copper geometry and custom rule overrides require separate DRC/geometry checks.')
    ctx.result.update(execution='ok', outcome='defect' if defects else 'pass',
                      summary=f'{len(defects)} net-class assignment or dimension defect(s)')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    write_json(args.out, extract(args.board))


if __name__ == '__main__':
    main()


def describe_net_class_assignment(ctx):
    descriptor = capability(ctx.params, ctx.scope, ctx.files, ctx.inputs)
    ctx.captured(ctx.scope.get('board'))
    ctx.apply(descriptor)


if __name__ != '__main__':
    _register_check('net_class_assignment', describe_net_class_assignment, check_net_class_assignment,
        early_prerequisites=(_requires.staged_placement_clear,), prerequisites=(_requires.stage_progress,))
