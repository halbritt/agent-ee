"""Coordinator adapter, ready for integrator-owned kind/capability registration."""
from .placement import judge
from .storage import write_json


def check_placement(ctx):
    from .checks import CheckError
    if set(ctx.params) - {'policy'}:
        raise CheckError('unsupported', 'placement only supports the declared polygon criterion and advisory policy')
    board = ctx.candidate_path(ctx.scope['board'])
    ctx.execute(['/usr/bin/python3', '-B', '-m', 'agent_ee.placement_probe', board,
                 '--out', '/work/placement-probe.json'], timeout=600)
    ctx.evidence('placement-probe.json')
    facts = ctx.load_json('placement-probe.json', 'placement probe')
    if facts.get('board_sha256') != ctx.manifest['files'].get(ctx.scope['board']):
        raise CheckError('malformed', 'placement probe does not bind the captured board')
    try:
        report = judge(facts, ctx.params.get('policy'))
    except (ValueError, KeyError, TypeError) as exc:
        raise CheckError('malformed', f'placement input/policy unusable: {exc}') from exc
    report['candidate_id'] = ctx.manifest['candidate_id']
    write_json(ctx.work / 'placement-judgment.json', report)
    ctx.evidence('placement-judgment.json')
    ctx.result['details'].update(report)
    ctx.result['limitations'].append('placement polygons only; no copper, clearance, thermal or EMC analysis; score is advisory')
    if report['verdict'] == 'unknown':
        raise CheckError('unsupported', 'placement geometry coverage incomplete')
    ctx.result.update(execution='ok', outcome=report['outcome'],
                      summary=f"placement: {len(report['hard_failures'])} proved geometric violation(s)")
