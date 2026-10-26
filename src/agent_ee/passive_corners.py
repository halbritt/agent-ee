"""Bounded first-order RC endpoint qualification, never physical calibration."""

from .check_registry import register as _register_check

import copy
import itertools
import math
from pathlib import Path

from .storage import digest, identity, read_json, write_json

POLICY = 'unloaded-first-order-rc/1'
TOOL = Path('/usr/bin/ngspice')
# Native qualification target: corrected ngspice 47 in the supported image.
TOOL_SHA256 = 'a238c8f410e1f59656a62b3cb12d4955cfbaca2d70db92b661b2bf98a9fc586b'
RAMP_S = 1e-9
NUMERICAL_REL = 1e-4


def qualification():
    """Expected captured policy inputs; identities do not replace analytic checks."""
    from . import checks
    return {'schema': 'agent-ee.passive-corners/1', 'policy': POLICY,
            'tool': str(TOOL), 'tool_sha256': TOOL_SHA256, 'tool_version': 'ngspice-47',
            'parser_sha256': digest(checks.__file__), 'model_sha256': digest(__file__),
            'ramp_s': RAMP_S, 'numerical_relative_v': NUMERICAL_REL,
            'tau_s': [1e-4, 1e-2], 'at_ms': [0.1, 5.0], 'step_v': [0.1, 5.0],
            'stop_ms': 5.0, 'max_corners': 4, 'max_analyses': 5,
            'timeout_s': 30, 'max_step_s': 1e-6, 'initial_v': 0.0,
            'oracle': 'positive-step endpoint monotonicity; per-analysis ideal-step error bound'}


def validate_contract(params):
    """Pure static portion, shared by execution and the scope/3 descriptor."""
    from .checks import CheckError, validate_sim_params
    from .measurements import finite
    allowed = {'netlist_check', 'input_net', 'output_net', 'ground_net', 'step_v',
               'stop_ms', 'measurements', 'tolerances', 'max_corners', 'qualification',
               'calibration_inputs'}
    if set(params) - allowed:
        raise ValueError('unsupported passive corner parameters')
    for key in ('step_v', 'stop_ms'):
        finite(params[key], key)
    measurements = params.get('measurements')
    if not isinstance(measurements, list):
        raise ValueError('measurements must be a list')
    for measurement in measurements:
        if not isinstance(measurement, dict) or set(measurement) - {'name', 'at_ms', 'min', 'max'}:
            raise ValueError('unsupported measurement declaration')
        finite(measurement['at_ms'], 'at_ms')
        for bound in ('min', 'max'):
            if measurement.get(bound) is not None:
                finite(measurement[bound], bound)
    try:
        normalized = validate_sim_params(params)
    except CheckError as exc:
        raise ValueError(str(exc)) from exc
    if not 0.1 <= normalized['step_v'] <= 5 or normalized['stop_ms'] != 5:
        raise ValueError('outside qualified stimulus/time domain')
    nets = [params[key] for key in ('input_net', 'output_net', 'ground_net')]
    if any(not isinstance(n, str) or not n for n in nets) or len(set(nets)) != 3:
        raise ValueError('three distinct declared nets required')
    wanted = normalized['measurements']
    if not 1 <= len(wanted) <= 8 or any(not 0.1 <= m['at_ms'] <= 5 for m in wanted):
        raise ValueError('outside qualified observation domain/count')
    if not any(m['min'] is not None or m['max'] is not None for m in wanted):
        raise ValueError('corner acceptance requires bounded measurements')
    tolerances = params['tolerances']
    if not isinstance(tolerances, dict) or len(tolerances) != 2:
        raise ValueError('explicit independent tolerance intervals required for exactly R and C')
    for ref, interval in tolerances.items():
        if not isinstance(ref, str) or not isinstance(interval, list) or len(interval) != 2:
            raise ValueError('tolerance needs reference and two relative endpoints')
        lo, hi = [finite(v, 'tolerance endpoint') for v in interval]
        if not -1 < lo <= 0 <= hi or hi > 1:
            raise ValueError('tolerance endpoints must contain nominal and keep positive values')
    maximum = params['max_corners']
    count = math.prod(len(set(v)) for v in tolerances.values())
    if type(maximum) is not int or not 1 <= maximum <= 4 or count > maximum:
        raise ValueError('complete corner grid exceeds declared/qualified budget')
    return normalized


def condition(params, measurement):
    return (f"{POLICY};step={float(params['step_v']):g}V;ramp=1ns;initial=0V;"
            f"at={float(measurement['at_ms']):g}ms;independent-tolerances="
            f"{identity(params['tolerances'])};qualification={params['qualification']}")


def plan(conn, params):
    from .checks import build_deck
    normalized = validate_contract(params)
    _, elements, ignored, dnp, nodes, wanted = build_deck(conn, normalized)
    if sorted(e['class'] for e in elements.values()) != ['C', 'R']:
        raise ValueError('qualified topology requires exactly one resistor and one capacitor')
    if set(elements) != set(params['tolerances']):
        raise ValueError('tolerance inventory does not match native modeled components')
    for ref, element in elements.items():
        expected = sorted([params['input_net'], params['output_net']] if element['class'] == 'R'
                          else [params['output_net'], params['ground_net']])
        if element['nets'] != expected:
            raise ValueError('unsupported loaded/ambiguous RC topology')
        pins = [entry.rpartition('.')[2] for entries in conn['nets'].values()
                for entry in entries if entry.rpartition('.')[0] == ref]
        if sorted(pins) != ['1', '2']:
            raise ValueError('ambiguous native terminal identity')
        if not math.isfinite(element['parsed']) or element['parsed'] <= 0:
            raise ValueError('native values must be finite positive')
    refs = sorted(elements)
    corners = []
    for endpoints in itertools.product(*(sorted(set(params['tolerances'][r])) for r in refs)):
        values = {r: elements[r]['parsed'] * (1 + e) for r, e in zip(refs, endpoints)}
        tau = math.prod(values.values())
        if not all(math.isfinite(v) and v > 0 for v in values.values()) or not 1e-4 <= tau <= 1e-2:
            raise ValueError('corner outside qualified positive RC time-constant domain')
        corners.append({'id': f'corner-{len(corners):02d}', 'values': values, 'tau_s': tau})
    nominal = {r: elements[r]['parsed'] for r in refs}
    return {'policy': POLICY, 'elements': elements, 'ignored_components': ignored,
            'excluded_dnp': dnp, 'nodes': nodes, 'wanted': wanted, 'corners': corners,
            'nominal': {'id': 'nominal', 'values': nominal, 'tau_s': math.prod(nominal.values())}}


def run_analysis(ctx, conn, params, analysis):
    from .checks import Ctx, CheckError, analyze_sim, build_deck
    derived = copy.deepcopy(conn)
    for ref, value in analysis['values'].items():
        derived['components'][ref]['value'] = format(value, '.17g')
    unbounded = {**params, 'measurements': [{**m, 'min': None, 'max': None}
                                           for m in params['measurements']]}
    deck, _, _, _, _, wanted = build_deck(derived, unbounded)
    # Fix integration controls for this qualification rather than accepting arbitrary solver options.
    deck = deck.replace('.end\n', '.options reltol=1e-6 abstol=1e-12 vntol=1e-9\n.end\n')
    deck = '\n'.join('.tran 0.001m 5m 0 0.001m' if line.startswith('.tran ') else line
                     for line in deck.split('\n'))
    work = ctx.work / analysis['id']
    work.mkdir()
    (work / 'deck.cir').write_text(deck)
    (work / 'control.sp').write_text(
        '* qualified RC controls\n.control\nset num_threads=1\nset noaskquit\n'
        'source /work/deck.cir\nrun\nwrite /work/result.raw\nquit\n.endc\n.end\n')
    child = Ctx(ctx.run_dir, ctx.manifest, ctx.spec, min(ctx.deadline, ctx.corner_deadline))
    child.work = work
    try:
        if digest(TOOL) != TOOL_SHA256:
            raise CheckError('unsupported', 'simulator identity requires requalification')
        child.execute([str(TOOL), '-n', '-b', '-o', '/work/ngspice.log', '/work/control.sp'], 30)
        if digest(TOOL) != TOOL_SHA256:
            raise CheckError('unsupported', 'simulator changed during corner analysis')
        measured, _, raw = analyze_sim(work, wanted)
        for name, metric in measured.items():
            t = metric['at_ms'] / 1000
            ideal = params['step_v'] * -math.expm1(-t / analysis['tau_s'])
            # Ramp response lies between instantaneous steps at 0 and 1ns.
            allowance = params['step_v'] * (NUMERICAL_REL + RAMP_S / analysis['tau_s'])
            if abs(metric['measured'] - ideal) > allowance:
                raise CheckError('unsupported', f'{analysis["id"]}/{name}: analytic qualification failed')
            metric.update(ideal_step_v=ideal, allowance_v=allowance)
        report = {**analysis, 'measurements': measured, 'raw': raw}
        write_json(work / 'metrics.json', report)
        return report
    finally:
        for path in sorted(work.iterdir()):
            if path.is_file():
                ctx.evidence(f'{analysis["id"]}/{path.name}')


def check_passive_corners(ctx):
    from .checks import CheckError
    from .measurements import evaluate_limits
    import time

    try:
        params = validate_contract(ctx.params)
        qpath = ctx.params['qualification']
        if qpath not in ctx.manifest['files'] or digest(ctx.inputs / qpath) != ctx.manifest['files'][qpath]:
            raise ValueError('qualification must be unchanged captured input')
        if read_json(ctx.inputs / qpath) != qualification():
            raise ValueError('tool/parser/model/policy identity requires explicit requalification')
        nid = params['netlist_check']
        specs = ctx.scope['checks']
        if not any(s['id'] == nid and s['kind'] == 'netlist' for s in specs[:specs.index(ctx.spec)]):
            raise ValueError('requires earlier native netlist check')
        prior = read_json(ctx.run_dir / 'checks' / f'{nid}.json')
        if prior['execution'] != 'ok' or prior['candidate_id'] != ctx.manifest['candidate_id']:
            raise CheckError('not_run', 'native netlist prerequisite unavailable')
        conn = read_json(ctx.run_dir / 'work' / nid / 'connectivity.json')
        ctx.result['evidence'].extend([f'work/{nid}/design.xml', f'work/{nid}/connectivity.json'])
        ensemble = plan(conn, ctx.params)
        expected = [c['id'] for c in ensemble['corners']]
        receipt = {'schema': 'agent-ee.corner-ensemble/1', 'candidate_id': ctx.manifest['candidate_id'],
                   'qualification': qualification(), 'plan': ensemble,
                   'expected': expected, 'completed': [], 'complete': False}
        write_json(ctx.work / 'ensemble.json', receipt)
        ctx.evidence('ensemble.json')
        ctx.corner_deadline = time.monotonic() + 150
        reports = []
        for corner in ensemble['corners']:
            reports.append(run_analysis(ctx, conn, params, corner))
            receipt['completed'].append(corner['id'])
            write_json(ctx.work / 'ensemble.json', receipt)
        nominal_corner = next((r for r in reports if r['values'] == ensemble['nominal']['values']), None)
        nominal = nominal_corner or run_analysis(ctx, conn, params, ensemble['nominal'])
        receipt.update(complete=True, nominal_analysis=nominal['id'])
        write_json(ctx.work / 'ensemble.json', receipt)
        metrics, limits = {}, {}
        for m in ensemble['wanted']:
            name = m['name']
            low = min(reports, key=lambda r: r['measurements'][name]['measured'])
            high = max(reports, key=lambda r: r['measurements'][name]['measured'])
            # The largest allowance bounds numerical and finite-ramp error throughout the rectangle.
            error = max(r['measurements'][name]['allowance_v'] for r in reports)
            metrics[name] = {'value': nominal['measurements'][name]['measured'],
                             'lower': low['measurements'][name]['measured'] - error,
                             'upper': high['measurements'][name]['measured'] + error,
                             'unit': 'V', 'condition': condition(params, m),
                             'envelope_policy': POLICY, 'allowance_v': error,
                             'lower_witness': low['id'], 'upper_witness': high['id']}
            limit = {k: m[k] for k in ('min', 'max') if m[k] is not None}
            if limit:
                limits[name] = {**limit, 'unit': 'V', 'condition': metrics[name]['condition']}
        ctx.result['details'].update(metrics=metrics, producer={'tool': str(TOOL),
            'sha256': TOOL_SHA256, 'domain': 'electrical'}, ensemble=receipt)
        # Even exact-design nominal residuals have no qualification over hypothetical corner designs.
        if ctx.scope.get('empirical'):
            raise CheckError('unsupported', 'nominal empirical residual transfer to corner domain is unqualified')
        margins = evaluate_limits(metrics, limits)
        ctx.result['details']['margins'] = margins
        failed = any(v < 0 for bound in margins.values() for v in bound.values())
        ctx.result.update(execution='ok', outcome='defect' if failed else 'pass',
                          summary='complete qualified RC envelope ' + ('violates limits' if failed else 'meets limits'))
        ctx.result['limitations'].append('ideal independent R/C tolerances only; no physical validation, loading, temperature or empirical transfer')
    except (ValueError, KeyError, TypeError, OSError, OverflowError) as exc:
        raise CheckError('unsupported', str(exc)) from exc


def describe_passive_corners(ctx):
    from .checks import _rc_transient
    validate_contract(ctx.params)
    ctx.captured(ctx.params['qualification'], immutable=True)
    ctx.result['predicates'] = _rc_transient(ctx.params, ctx.prerequisite)


if __name__ != '__main__':
    _register_check('passive_corners', describe_passive_corners, check_passive_corners)
