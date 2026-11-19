"""Measure two-terminal lengths, delays and AC chains on the saved KiCad board."""
import argparse
from collections import defaultdict
from decimal import Decimal
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys

from .storage import digest, read_json, write_json
from .symbol_library import children, parse_sexpr


def _delay_profiles(board, native, names):
    """Check authored stackup values before KiCad can supply save-time defaults."""
    import pcbnew
    ids = list(native.GetEnabledLayers().CuStack())
    layers = [native.GetLayerName(layer) for layer in ids]
    canonical = [pcbnew.LayerName(layer) for layer in ids]
    aliases = dict(zip(layers, ids))
    aliases.update(zip(canonical, ids))
    setup, = children(parse_sexpr(board.read_bytes()), 'setup')
    stack, = children(setup, 'stackup')
    copper = []
    dielectric_since_copper = False
    for layer in children(stack, 'layer'):
        types = children(layer, 'type')
        kind = types[0][1] if types else None
        if kind == 'copper':
            if copper and not dielectric_since_copper:
                raise ValueError(f'missing authored dielectric before {layer[1]}')
            copper.append(layer[1])
            dielectric_since_copper = False
        elif kind in ('core', 'prepreg'):
            if not copper:
                raise ValueError('dielectric outside copper stackup')
            dielectric_since_copper = True
        elif copper and len(copper) < len(layers):
            raise ValueError(f'unrecognized layer {layer[1]} inside copper stackup')
        else:
            continue
        sheets = [[]]
        for entry in layer[2:]:
            if entry == 'addsublayer':
                sheets.append([])
            else:
                sheets[-1].append(entry)
        for entries in sheets:
            for key in (('thickness',) if kind == 'copper' else ('thickness', 'epsilon_r')):
                values = children(['sheet', *entries], key)
                if len(values) != 1 or len(values[0]) != 2:
                    raise ValueError(f'missing authored {key} for {layer[1]}')
                value = float(values[0][1])
                if not math.isfinite(value) or value <= 0 or (key == 'epsilon_r' and value < 1):
                    raise ValueError(f'invalid authored {key} for {layer[1]}')

    if [aliases.get(name) for name in copper] != ids or dielectric_since_copper:
        raise ValueError('authored copper/dielectric stack differs from enabled copper layers')
    settings = native.GetDesignSettings()
    if not settings.m_UseHeightForLengthCalcs:
        raise ValueError('native via height calculation must be enabled')
    definitions = read_json(board.with_suffix('.kicad_pro'))['tuning_profiles']['tuning_profiles_impedance_geometric']
    results = {}
    for name in names:
        selected = settings.m_NetSettings.GetEffectiveNetClass(name).GetTuningProfile()
        matches = [p for p in definitions if p['profile_name'] == selected]
        if len(matches) != 1 or not selected:
            raise ValueError(f'{name}: expected one selected authored timing profile')
        profile, = matches
        entries = profile['layer_entries']
        widths = {aliases.get(e['signal_layer']): e['width'] for e in entries}
        values = [profile['via_prop_delay'], *(v['delay'] for v in profile['via_overrides']),
                  *(e[key] for e in entries for key in ('width', 'delay'))]
        if (profile['type'] != 0 or profile['enable_time_domain_tuning'] is not True
                or len(widths) != len(entries) or not widths or None in widths
                or any(type(v) is not int or v <= 0 for v in values)):
            raise ValueError(f'{name}: incomplete single-ended tuning profile')
        for t in native.GetTracks():
            if t.GetNetname() == name and t.GetClass() != 'PCB_VIA':
                if widths.get(t.GetLayer()) != t.GetWidth():
                    raise ValueError(f'{name}: saved track layer/width differs from its delay profile')
        results[name] = profile
    return results


class Terminals:
    def __init__(self, board):
        import pcbnew
        self.by_net = defaultdict(list)
        self.by_ref = {}
        self.item_nets = {t.m_Uuid.AsString(): str(t.GetNetname()) for t in board.GetTracks()}
        copper = list(board.GetEnabledLayers().CuStack())
        self.area_nets = {str(z.GetNetname()) for z in board.Zones() if not z.GetIsRuleArea()}
        for fp in board.GetFootprints():
            ref = str(fp.GetReference())
            if ref in self.by_ref:
                raise ValueError(f'ambiguous footprint reference {ref!r}')
            pads = []
            for p in fp.Pads():
                if not any(p.IsOnLayer(layer) for layer in copper):
                    continue
                name = str(p.GetNetname())
                self.by_net[name].append((ref, str(p.GetNumber())))
                self.item_nets[p.m_Uuid.AsString()] = name
                pads.append(name)
            self.by_ref[ref] = None if fp.GetAttributes() & pcbnew.FP_DNP else pads
        for item in [*board.GetDrawings(), *(g for fp in board.GetFootprints() for g in fp.GraphicalItems())]:
            if item.GetLayer() in copper and hasattr(item, 'GetNetname'):
                self.area_nets.add(str(item.GetNetname()))

    def path(self, name):
        pins = sorted(self.by_net.get(name, ()))
        if name in self.area_nets or len(pins) != 2 or pins[0] == pins[1]:
            raise ValueError(f'{name!r}: expected a trace path between two distinct copper terminals')
        if any(not re.fullmatch(r'[A-Za-z0-9_.]+', token) for pin in pins for token in pin):
            raise ValueError(f'{name!r}: terminal cannot be expressed in a KiCad fromTo condition')
        return dict(net=name, start='-'.join(pins[0]), end='-'.join(pins[1]))

    def chains(self, requirements, pairs):
        member = {}
        for index, pair in enumerate(pairs):
            for side in ('pos_name', 'neg_name'):
                name = pair[side]
                if name in member:
                    raise ValueError(f'ambiguous pair membership for {name!r}')
                member[name] = (index, side)
        chains, used = [], set()
        for refs in requirements:
            edges = defaultdict(list)
            neighbors = defaultdict(set)
            for ref in refs:
                pads = self.by_ref.get(ref)
                if not pads or len(pads) != 2 or any(n not in member for n in pads):
                    raise ValueError(f'{ref!r}: expected a populated two-pad series part on resolved pairs')
                (a, sa), (b, sb) = (member[n] for n in pads)
                if a == b or sa != sb:
                    raise ValueError(f'{ref!r}: invalid pair polarity or series connection')
                edges[tuple(sorted((a, b)))].append(sa)
                neighbors[a].add(b)
                neighbors[b].add(a)
            if (not edges or any(sorted(sides) != ['neg_name', 'pos_name'] for sides in edges.values())
                    or sorted(len(v) for v in neighbors.values()) != [1, 1] + [2] * (len(neighbors) - 2)):
                raise ValueError('series parts must join both polarities in one unbranched chain')
            reached, pending = set(), [min(neighbors)]
            while pending:
                i = pending.pop()
                if i not in reached:
                    reached.add(i)
                    pending.extend(neighbors[i] - reached)
            if reached != set(neighbors) or used & reached:
                raise ValueError('disconnected or repeated series chain')
            used.update(reached)
            pos = [pairs[i]['pos_name'] for i in sorted(reached)]
            neg = [pairs[i]['neg_name'] for i in sorted(reached)]
            for ref, pads in self.by_ref.items():
                if ref in refs or not pads or len(pads) != 2 or not set(pads).intersection(pos + neg):
                    continue
                if any(n not in member for n in pads) or member[pads[0]][0] != member[pads[1]][0]:
                    raise ValueError(f'unlisted series continuation at {ref!r}')
            chains.append(dict(bridges=sorted(refs), p_nets=pos, n_nets=neg, paths=[self.path(n) for n in pos+neg]))
        return chains


def _condition(paths):
    return json.dumps(' || '.join(f"A.fromTo('{p['start']}', '{p['end']}')" for p in paths))


def _read_paths(findings, label, paths, unit, inventory, tolerance):
    group = tolerance is not None
    expected = {p['net'] for p in paths}
    measured, skews, values = [], [], {}
    defects = []
    for item in findings['violations']:
        if f"rule '{label}'" not in item.get('description', ''):
            continue
        if item['type'] == 'skew_out_of_range':
            skews.append(item)
        if item['type'] != 'length_out_of_range':
            continue
        measured.append(item)
        names = {inventory.item_nets.get(v['uuid']) for v in item['items']}
        if len(names) != 1 or not names <= expected:
            defects.append(f'{label}: ambiguous native measurement')
            continue
        name, = names
        amount = re.search(r'actual (\d+(?:\.\d+)?) (mm|ps)\b', item['description'])
        if name in values or not amount or amount[2] != unit:
            defects.append(f'{label}: duplicate or unreadable native measurement')
            continue
        value = Decimal(amount[1])
        values[name] = (value, Decimal(1).scaleb(value.as_tuple().exponent) / 2)
    required = {'length_out_of_range'} if not group else {'length_out_of_range', 'skew_out_of_range'}
    if required & {i['key'] for i in findings['ignored_checks']}:
        defects.append(f'{label}: required native measurement is ignored')
    if group and values:
        # KiCad compares at native precision. Its rounded printable intervals
        # also independently identify a definite violation if one is excluded.
        spread = max(v - error for v, error in values.values()) - min(v + error for v, error in values.values())
        if spread > tolerance:
            defects.append(f'{label}: measured native skew exceeds tolerance')
    if values.keys() != expected or findings['unconnected_items'] or skews:
        defects.append(f'{label}: incomplete paths or excessive native skew')
    return measured, skews, values, defects


def check(board, request, output, mode):
    import pcbnew
    from .native_run import drc_argv
    from .routing_adapter import validate_router_options
    key = 'ac_match' if mode == 'ac_matching' else 'length_match'
    options = {key: request['requirement']}
    if mode == 'ac_matching':
        options['diff_pairs'] = 'auto'
    spec = validate_router_options(options)[key]
    if mode == 'preflight' and 'tolerance_ps' not in spec:
        raise ValueError('timing preflight requires a delay group')
    original = {p: digest(p) for p in (board, board.with_suffix('.kicad_pro'), board.with_suffix('.kicad_dru')) if p.is_file()}
    native = pcbnew.LoadBoard(str(board))
    if not native:
        raise ValueError(f'cannot load {board.name}')
    result = dict(status='FAIL', requirement=spec, board_sha256=original[board], defects=[])
    if 'tolerance_ps' in spec:
        result['profiles'] = _delay_profiles(board, native, {n for group in spec['groups'] for n in group})
        result['project_sha256'] = original[board.with_suffix('.kicad_pro')]
    if mode == 'preflight':
        result['status'] = 'PASS'
        return result

    inventory = Terminals(native)
    work = output.parent / (output.stem + '-native')
    work.mkdir()
    numbered = {}
    rules = []
    if mode == 'ac_matching':
        result['chains'] = inventory.chains(spec['chains'], request['pairs'])
        for chain in result['chains']:
            for path in chain['paths']:
                label = f'agent-ee AC path {len(numbered)}'
                numbered[label] = [path]
                rules.append(f'(rule "{label}" (condition {_condition([path])}) (constraint length (max 0mm)))')
    else:
        result['groups'] = []
        unit = 'ps' if 'tolerance_ps' in spec else 'mm'
        for index, names in enumerate(spec['groups']):
            paths = [inventory.path(n) for n in names]
            label = f'agent-ee length group {index}'
            numbered[label] = paths
            limit = spec['tolerance_' + unit]
            rules.append(f'(rule "{label}" (condition {_condition(paths)}) (constraint length (max 0{unit}))'
                         f' (constraint skew (max {limit}{unit})))')
            result['groups'].append(dict(paths=paths, rule=label, measurements=[], skew_violations=[], status='FAIL'))

    project = work / 'project'
    if board.parent.resolve() in project.resolve().parents:
        raise ValueError('path measurement requires a separate project directory')
    shutil.copytree(board.parent, project, symlinks=True,
                    ignore=shutil.ignore_patterns('*.kicad_prl', '*.lck'))
    copied = project / board.name
    rule_file = copied.with_suffix('.kicad_dru')
    old_rules = rule_file.read_text() if rule_file.exists() else '(version 1)\n'
    if rule_file.is_symlink():
        rule_file.unlink()
    # Each group has its own KiCad rule. A member appearing in several groups
    # needs a separate evaluation for each, since KiCad applies rule precedence.
    observed = {}
    for index, (label, paths) in enumerate(numbered.items()):
        if mode == 'length_groups' or index == 0:
            batch = rules if mode == 'ac_matching' else [rules[index]]
            rule_file.write_text(old_rules + '\n' + '\n'.join(batch) + '\n')
            report_path = work / f'paths-{index}.json'
            # The placement controller owns this process group and its deadline.
            # KiCad inherits the group so cancellation also stops the measurement.
            with (work / f'paths-{index}.log').open('wb') as log:
                subprocess.run(drc_argv(copied, report_path, units='mm'), stdout=log, stderr=log,
                               timeout=300, check=True)
            findings = read_json(report_path)
        unit = 'ps' if 'tolerance_ps' in spec else 'mm'
        tolerance = Decimal(str(spec['tolerance_' + unit])) if mode == 'length_groups' else None
        measured, skews, values, defects = _read_paths(findings, label, paths, unit, inventory, tolerance)
        if mode == 'length_groups':
            result['groups'][index].update(measurements=measured, skew_violations=skews, status='FAIL' if defects else 'PASS')
        result['defects'].extend(defects)
        observed.update(values)
    if mode == 'ac_matching' and not result['defects']:
        for chain in result['chains']:
            p, n = [sum(observed[name][0] for name in chain[side]) for side in ('p_nets', 'n_nets')]
            names = chain['p_nets'] + chain['n_nets']
            bound = sum(observed[name][1] for name in names)
            passed = abs(p - n) + bound <= Decimal(str(spec['tolerance_mm']))
            chain.update(p_length_mm=float(p), n_length_mm=float(n), skew_mm=float(abs(p - n)),
                         rounding_bound_mm=float(bound), measurements_mm={name: float(observed[name][0]) for name in names},
                         status='PASS' if passed else 'FAIL')
            if not passed:
                result['defects'].append('final AC path skew exceeds the declared tolerance')
    if digest(copied) != original[board]:
        result['defects'].append('saved board changed during native measurement')
    if any(digest(path) != value for path, value in original.items()):
        result['defects'].append('source project changed during measurement')
    result['status'] = 'FAIL' if result['defects'] else 'PASS'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('preflight', 'length_groups', 'ac_matching'))
    parser.add_argument('board', type=Path)
    parser.add_argument('request', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    request = {}
    try:
        request = json.load(sys.stdin) if str(args.request) == '-' else read_json(args.request)
        result = check(args.board, request, args.output, args.mode)
    except (ValueError, KeyError, IndexError, OSError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        result = dict(status='FAIL', defects=[str(exc)])
        if isinstance(request, dict) and 'requirement' in request:
            result['requirement'] = request['requirement']
        if args.board.is_file():
            result['board_sha256'] = digest(args.board)
        print(str(exc), file=sys.stderr)
    if str(args.output) != '-':
        write_json(args.output, result)
    return 0 if result['status'] == 'PASS' else 2


if __name__ == '__main__':
    sys.exit(main())
