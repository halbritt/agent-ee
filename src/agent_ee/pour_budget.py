"""Evidence for a declared pour_area_loss_pct (AGENTEE-257; docs/pod-routing-policy-determination.md).

`measure` refills a copy of a placed, planed, unrouted board the way kicad-route
does and records the pour areas its pour_integrity check divides by, plus the
fill one routed through-via of each net class removes from each pour. `derive`
turns that record and a declared signal-via budget into the bound: the largest
fraction of any pour that the budget's antipads remove. It is a geometric
routing guard, not an electrical or RF qualification."""
import argparse
import json
import math
from pathlib import Path

from . import stage_receipts
from . import native_run
from .storage import digest, read_json, write_json

SCHEMA = 'agent-ee.pour-budget/1'
DERIVATION = 'agent-ee.pour-budget-derivation/1'
MAX_VIA_BUDGET = 100_000
# kicad-route rounds each polygon to 0.0001 mm2; a joined via changes nothing beyond that.
OWN_NET_TOLERANCE_MM2 = 0.0001
SCOPE = ('router-induced pour area loss bounded by a declared count of signal through-via antipads; '
         'not impedance, return-path, keepout or RF qualification')


def measure(candidate, board, out):
    """Run the native probe on candidate/board; write and return out/pour-budget.json."""
    candidate, out = Path(candidate).resolve(), Path(out).absolute()
    stage_receipts._relative(board, 'pour budget board')
    if out.exists() or out.is_symlink() or candidate in out.resolve().parents:
        raise ValueError('pour budget output must be new and outside the captured inputs')
    out.mkdir(parents=True)
    before = digest(candidate/board)
    report = dict(schema=SCHEMA, execution='failed', board=board, board_sha256=before)
    record = dict(native_run.run(native_run.Intent('pcbnew_script', ['/usr/bin/python3', '-B', '-m', 'agent_ee.pour_budget_probe',
                                               '/candidate/'+board, '/work/facts.json'], timeout=3600), inputs=candidate, work=out/'native'))
    if record['execution'] != 'ok' or record['exit_code'] != 0:
        report['reason'] = (f'native pour budget child {record["execution"]} exit {record["exit_code"]}: ' +
                            (out/'native/console.log').read_text(errors='replace')[-2000:])
    elif digest(candidate/board) != before:
        report['reason'] = 'the captured board changed during measurement'
    else:
        report.update(read_json(out/'native/facts.json'), execution='ok')
    write_json(out/'pour-budget.json', report)
    return report


def derive(report, via_budget):
    """The bound for a declared signal-via budget, with its basis, or ValueError (fail closed)."""
    if isinstance(via_budget, bool) or not isinstance(via_budget, int) or not 1 <= via_budget <= MAX_VIA_BUDGET:
        raise ValueError(f'via budget must be an integer from 1 to {MAX_VIA_BUDGET}')
    if not isinstance(report, dict) or report.get('schema') != SCHEMA or report.get('execution') != 'ok':
        raise ValueError('a successful agent-ee.pour-budget/1 measurement is required')
    if report['outside_outline']:
        raise ValueError('not a placed board: footprints outside the outline: ' + ', '.join(report['outside_outline']))
    pours, probes = report['pour_area_mm2'], report['probes']
    if not pours:
        raise ValueError('the board has no filled copper pour')
    if not report['net_classes']:
        raise ValueError('no routed net class was probed')
    per_pour = {}
    for key, area in pours.items():
        mine = [p for p in probes if p['key'] == key]
        if area <= 0 or len(mine) != len(report['net_classes']):
            raise ValueError(f'{key}: pour area or probes missing')
        unknown = [p for p in mine if p['status'] != 'measured']
        if unknown:
            raise ValueError(f'{key}: per-via loss unknown: {unknown[0]["reason"]}')
        own = [p for p in report['own_net_probes'] if p['key'] == key]
        if len(own) != 1 or own[0]['status'] != 'measured':
            raise ValueError(f'{key}: the loss of a via on the pour\'s own net is unknown')
        if own[0]['loss_mm2'][key] > OWN_NET_TOLERANCE_MM2:
            raise ValueError(f'{key}: a via on the pour\'s own net removes fill; a signal-via budget does not bound it')
        losses = {p['netclass']: p['loss_mm2'][key] for p in mine}
        if min(losses.values()) <= 0:
            raise ValueError(f'{key}: a probe via removed no fill; the per-via loss is not measured')
        worst = max(losses, key=lambda n: (losses[n], n))
        per_pour[key] = dict(area_mm2=area, via_loss_mm2=losses, worst_netclass=worst,
                             own_net_via_loss_mm2=own[0]['loss_mm2'][key],
                             budget_loss_pct=100.0 * via_budget * losses[worst] / area)
    exact = max(r['budget_loss_pct'] for r in per_pour.values())
    # kicad-route rounds each delta to 0.001 % and fails when -delta > bound: round up.
    bound = math.ceil(round(exact * 1000, 6)) / 1000
    if bound > 100:
        raise ValueError(f'the via budget removes {exact:.3f} % of a pour, more than the whole pour')
    return dict(schema=DERIVATION, board_sha256=report['board_sha256'], via_budget=via_budget,
                pour_area_loss_pct=bound, governing_pour=max(per_pour, key=lambda k: (per_pour[k]['budget_loss_pct'], k)),
                pours=per_pour, scope=SCOPE)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    m = sub.add_parser('measure')
    m.add_argument('--candidate', required=True)
    m.add_argument('--board', required=True)
    m.add_argument('--out', required=True)
    d = sub.add_parser('derive')
    d.add_argument('--report', required=True)
    d.add_argument('--via-budget', type=int, required=True)
    args = parser.parse_args(argv)
    if args.command == 'measure':
        report = measure(args.candidate, args.board, args.out)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report['execution'] == 'ok' else 1
    try:
        print(json.dumps(derive(read_json(args.report), args.via_budget), indent=2, sort_keys=True))
    except ValueError as exc:
        print(f'refused: {exc}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
