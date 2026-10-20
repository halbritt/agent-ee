"""Coordinator via-budget pilot (AGENTEE-265; docs/routing-adapter.md#via-budget-pilot).

The AGENTEE-257 via-budget method needs the routed demand of the placed, planed,
unrouted routing input: ``K >= 3`` diagnostic routing attempts with the campaign's
signal layers and a permissive pour bound. ``run`` executes them as ordinary
coordinator stage steps (the pinned adapter in the networkless sandbox) on a copy of
the routing input. Each attempt is the campaign routing declaration with only its
label and layer policy changed; its receipt is bound as a pilot attempt and is never
adopted. The pilot is diagnostic: it has no acceptance status, delivers no board and
is kept apart from final routing.

From each attempt's retained ``validate/candidate-facts.json`` and
``facts/reference-facts.json`` the coordinator counts ``V_i`` with the AGENTEE-257
``signal_vias`` rule and writes ``via-budget-basis.json``: every attempt's receipt and
facts hashes, ``V_i``, and ``N = V_max + max(1, ceil(0.1 V_max))``. Any attempt that is
not ``complete``, unverifiable facts, a different routing input or attempts that differ
by more than the margin leave ``N`` unknown. ``verify`` re-derives the whole basis from
the retained bytes alone; campaign start refuses a basis that does not re-derive.
"""
import argparse
import collections
import json
import os
from pathlib import Path
import shutil

from . import kicad_route_attempt as attempt
from . import routing_adapter
from .layer_policy_lineage import MIN_PILOT_ATTEMPTS as MIN_ATTEMPTS, VIA_BUDGET_PILOT, pilot_budget
from .storage import digest, read_json, write_json

SCHEMA = 'agent-ee.via-budget-pilot/1'
BASIS_FILE = 'via-budget-basis.json'
PILOT = 'diagnostic_not_adoptable'
ACCEPTANCE = 'not_accepted'
MAX_ATTEMPTS = 20
# AGENTEE-257: a permissive, non-adoptable bound, so the pour budget never shapes the demand.
PILOT_LOSS_PCT = 100
FACTS = {'candidate_facts': 'attempt/validate/candidate-facts.json',
         'reference_facts': 'attempt/facts/reference-facts.json'}
# The AGENTEE-257 rule: vias on the plane net remove no plane fill.
PLANE_NET = '/GND'
KEYS = {'schema', 'pilot', 'adoptable', 'acceptance', 'location', 'declaration', 'pilot_policy', 'board',
        'board_sha256', 'attempts', 'status', 'reason', 'signal_vias', 'margin', 'via_budget'}


def signal_vias(cand, ref):
    """V_i: new through-vias of nets other than the plane net (AGENTEE-257 ``signal_vias``)."""
    def vias(facts, label):
        if not isinstance(facts, dict) or not isinstance(facts.get('vias'), list):
            raise ValueError(f'{label} has no via list')
        for via in facts['vias']:
            if (not isinstance(via, dict) or not isinstance(via.get('net'), str) or
                    not isinstance(via.get('via_type'), str) or not isinstance(via.get('position'), list) or
                    any(isinstance(v, bool) or not isinstance(v, (int, float))
                        for v in [*via['position'], via.get('diameter'), via.get('drill')])):
                raise ValueError(f'{label} has a malformed via')
        return facts['vias']

    def key(via):
        return via['net'], tuple(via['position']), via['diameter'], via['drill']

    existing = collections.Counter(key(v) for v in vias(ref, 'reference facts'))
    count = 0
    for via in vias(cand, 'candidate facts'):
        if existing[key(via)] > 0:
            existing[key(via)] -= 1
        elif via['net'] != PLANE_NET and via['via_type'] == 'through':
            count += 1
    return count


def pilot_policy(policy):
    """The pilot form of a campaign layer policy: its signal layers and costs, a permissive bound."""
    if not isinstance(policy, dict):
        raise ValueError('pilot layer policy must be an object')
    value = {**{k: policy[k] for k in ('signal_layers', 'layer_costs') if k in policy},
             'pour_area_loss_pct': PILOT_LOSS_PCT}
    try:
        return attempt.validate_layer_policy(value)
    except attempt.RequestError as exc:
        raise ValueError(f'pilot layer policy: {exc}') from None


def declaration(value):
    """The campaign routing declaration a pilot forks; a literal layer policy cannot stand in."""
    value = routing_adapter.validate_declaration(value)
    if 'layer_policy' in value:
        raise ValueError('a via-budget pilot forks a routing declaration without a literal layer_policy')
    return value


def pilot_spec(value, policy, index):
    """Attempt ``index``: the declaration with only its label and layer policy changed."""
    return routing_adapter.declaration_spec(routing_adapter.validate_declaration(
        {**value, 'label': f'{value["label"]}-pilot-{index}', 'layer_policy': policy}))


def binding(index):
    return {'via_budget_pilot': index, 'adoptable': False}


def attempt_facts(out, index, value, policy, board, location):
    """One attempt's facts, re-derived from its retained receipt; a reason when V_i is unknown."""
    from .stage_receipts import validate_receipt
    rel = f'attempts/{index}'
    receipt_dir = Path(out) / rel
    entry = {'index': index, 'receipt': f'{rel}/receipt.json', 'receipt_sha256': None, 'board_sha256': None,
             'tool': None, 'classification': None, 'candidate_facts': None, 'reference_facts': None,
             'signal_vias': None, 'reason': None}
    path = receipt_dir / 'receipt.json'
    if path.is_symlink() or not path.is_file():
        entry['reason'] = 'the attempt left no receipt'
        return entry
    entry['receipt_sha256'] = digest(path)
    try:
        receipt = validate_receipt(receipt_dir, binding=binding(index))
        if receipt['spec'] != pilot_spec(value, policy, index):
            raise ValueError('the attempt did not run the pilot form of the routing declaration')
        entry['board_sha256'] = receipt['inputs'].get(board)
        entry['tool'] = {k: receipt['tool'][k] for k in ('executable', 'mounts', 'source_tree')}
        summary = routing_adapter.verify_campaign_receipt(receipt_dir, location=Path(location) / rel)
    except (ValueError, KeyError, TypeError) as exc:
        entry['reason'] = f'attempt evidence did not verify: {exc}'
        return entry
    if summary is None:
        entry['reason'] = 'the adapter left no report; the routed demand is unknown'
        return entry
    entry['classification'] = summary['classification']
    if summary['classification'] != 'complete':
        entry['reason'] = f'attempt classified {summary["classification"]}, not complete; the routed demand is unknown'
        return entry
    # verify_campaign_receipt bound these hashes to the retained files and the receipt.
    retained = read_json(receipt_dir / 'run/routing/adapter-report.json')['retained']
    facts = {}
    for key, name in FACTS.items():
        file = receipt_dir / 'run/routing' / name
        if name not in retained or file.is_symlink() or not file.is_file() or digest(file) != retained[name]:
            entry['reason'] = f'the router retained no verifiable {name}'
            return entry
        entry[key] = {'path': f'{rel}/run/routing/{name}', 'sha256': retained[name]}
        facts[key] = read_json(file)
    try:
        entry['signal_vias'] = signal_vias(facts['candidate_facts'], facts['reference_facts'])
    except ValueError as exc:
        entry['reason'] = f'signal vias not counted: {exc}'
    return entry


def assemble(location, value, policy, board, board_sha256, attempts):
    """The basis these attempt facts support; pure, so verify recomputes it."""
    counts = [a['signal_vias'] for a in attempts]
    status, reason, margin, n = 'unknown', None, None, None
    unknown = [a for a in attempts if a['signal_vias'] is None]
    if unknown:
        reason = f'attempt {unknown[0]["index"]}: {unknown[0]["reason"]}'
    elif any(a['board_sha256'] != board_sha256 for a in attempts):
        reason = 'a pilot attempt routed another input than the routing input'
    else:
        try:
            n, margin = pilot_budget(counts)
            status = 'derived'
        except ValueError as exc:
            reason = str(exc)
    return {'schema': SCHEMA, 'pilot': PILOT, 'adoptable': False, 'acceptance': ACCEPTANCE,
            'location': str(location), 'declaration': value, 'pilot_policy': policy, 'board': board,
            'board_sha256': board_sha256, 'attempts': attempts, 'status': status, 'reason': reason,
            'signal_vias': None if unknown else counts, 'margin': margin, 'via_budget': n}


def run(design, declaration_value, policy, out, *, attempts=MIN_ATTEMPTS):
    """Run the pilot on the routing input ``design`` into a new ``out``; write and return the basis."""
    from .stage_receipts import run_step
    design, out = Path(design).resolve(), Path(out).absolute()
    if isinstance(attempts, bool) or not isinstance(attempts, int) or not MIN_ATTEMPTS <= attempts <= MAX_ATTEMPTS:
        raise ValueError(f'a via-budget pilot runs {MIN_ATTEMPTS} to {MAX_ATTEMPTS} attempts')
    if os.path.lexists(out) or design == out.resolve() or design in out.resolve().parents:
        raise ValueError('pilot output must be new and outside the routing input')
    value, policy = declaration(declaration_value), pilot_policy(policy)
    board = f'{value["project"]}/{value["board"]}'
    if (design / board).is_symlink() or not (design / board).is_file():
        raise ValueError(f'routing input lacks {board}')
    board_sha256 = digest(design / board)
    specs = [pilot_spec(value, policy, i) for i in range(1, attempts + 1)]
    for spec in specs:
        routing_adapter.validate_campaign_step(spec)  # the pinned command, mounts and a qualified tool
    out.mkdir(parents=True)
    for index, spec in enumerate(specs, 1):
        # Diagnostic only: the receipt is retained and never adopted into any design.
        run_step(out / 'attempts' / str(index), design, spec, binding(index))
    found = [attempt_facts(out, i, value, policy, board, out) for i in range(1, attempts + 1)]
    basis = assemble(out, value, policy, board, board_sha256, found)
    write_json(out / BASIS_FILE, basis)
    return basis


def verify(path):
    """Re-derive a retained basis from its attempts' receipts and facts; raise on any difference."""
    path = Path(path)
    if path.name != BASIS_FILE or path.is_symlink() or not path.is_file():
        raise ValueError(f'via-budget pilot basis must be a regular {BASIS_FILE}')
    basis = read_json(path)
    if (not isinstance(basis, dict) or set(basis) != KEYS or basis['schema'] != SCHEMA or
            basis['pilot'] != PILOT or basis['adoptable'] is not False or basis['acceptance'] != ACCEPTANCE):
        raise ValueError(f'expected a diagnostic, non-adoptable {SCHEMA} basis')
    out = path.parent
    value = declaration(basis['declaration'])
    count = len(basis['attempts']) if isinstance(basis['attempts'], list) else 0
    policy = basis['pilot_policy']
    if value != basis['declaration'] or not isinstance(policy, dict) or pilot_policy(policy) != policy:
        raise ValueError('via-budget pilot declaration or layer policy is not a pilot form')
    if not MIN_ATTEMPTS <= count <= MAX_ATTEMPTS or not isinstance(basis['location'], str):
        raise ValueError(f'a via-budget pilot retains {MIN_ATTEMPTS} to {MAX_ATTEMPTS} attempts')
    attempts = out / 'attempts'
    if (attempts.is_symlink() or not attempts.is_dir() or
            {p.name for p in attempts.iterdir()} != {str(i) for i in range(1, count + 1)}):
        raise ValueError('via-budget pilot attempts differ from the basis')
    board = f'{value["project"]}/{value["board"]}'
    found = [attempt_facts(out, i, value, policy, board, basis['location']) for i in range(1, count + 1)]
    if assemble(basis['location'], value, policy, board, basis['board_sha256'], found) != basis:
        raise ValueError('via-budget pilot basis differs from the re-derivation of its retained attempt facts')
    return basis


def via_budget(basis, path):
    """The agent-ee.via-budget/2 campaign declaration bound to a derived basis by hash."""
    if basis['status'] != 'derived':
        raise ValueError(f'the pilot derived no via budget: {basis["reason"]}')
    return {'schema': VIA_BUDGET_PILOT, 'via_budget': basis['via_budget'], 'board_sha256': basis['board_sha256'],
            'basis': {'method': 'routed_demand', 'pilot': PILOT, 'signal_vias': basis['signal_vias']},
            'pilot': {'path': str(path), 'sha256': digest(path)}}


def copy(source, target):
    """Copy a retained pilot (basis and attempts only), preserving links without following them."""
    source, target = Path(source), Path(target)
    if target.resolve().is_relative_to(source.resolve()):
        raise ValueError('via-budget pilot import destination is inside its source')
    target.mkdir()
    shutil.copyfile(source / BASIS_FILE, target / BASIS_FILE)
    shutil.copytree(source / 'attempts', target / 'attempts', symlinks=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    r = sub.add_parser('run')
    r.add_argument('--design', required=True, help='the planed, unrouted routing input candidate tree')
    r.add_argument('--declaration', required=True, help='the campaign agent-ee.routing-adapter/1 declaration')
    r.add_argument('--policy', required=True, help='the layer_policy_derivation policy (JSON file)')
    r.add_argument('--out', required=True)
    r.add_argument('--attempts', type=int, default=MIN_ATTEMPTS)
    v = sub.add_parser('verify')
    v.add_argument('--basis', required=True)
    args = parser.parse_args(argv)
    if args.command == 'run':
        basis = run(args.design, read_json(args.declaration), read_json(args.policy), args.out,
                    attempts=args.attempts)
        path = Path(args.out) / BASIS_FILE
    else:
        path = Path(args.basis)
        basis = verify(path)
    if basis['status'] != 'derived':
        print(json.dumps({'status': basis['status'], 'reason': basis['reason']}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(via_budget(basis, path.absolute()), indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
