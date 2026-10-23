#!/usr/bin/env python3
"""Historical extended fixture generator and complete-plan comparison driver.

Fixture generation and checking are separate: the Rust process receives only
captured inputs. Expected plans stay in the test driver, outside that process.
Some extended generators predate source authority enforcement and install
process-local synthetic owner pins. Generation refuses that unequal authority;
tests/test_placement_engine_parity.py is the supported current regression gate.
Existing frozen corpora remain historical evidence for their recorded sources.
"""
import argparse
import copy
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def normalized(plan):
    plan = copy.deepcopy(plan)
    if isinstance(plan, dict) and 'block_arrangement' in plan:
        plan['block_arrangement'].get('search', {}).pop('elapsed_s', None)
    return plan


def fixtures(seed_count):
    from test_placement import capture, part, rectangle
    from test_placer import request
    from test_placer_geometry_cost import seeded_board
    from test_placer_search import anchor_case, promotion_case
    from test_placement_plans import board as targeted_board, row, scoring
    for name, source, policy in [
        ('rectangles', capture([part('U1', 2, 2), part('C1', 2, 2), part('R1', 8, 8)]), request()),
        ('opposite-sides', capture([part('U1', 2, 2), part('C1', 2, 2, 'B')]), request()),
        ('fixed-conflict', capture([part('U1', 2, 2), part('C1', 3, 3)]), request(fixed_refs=['U1', 'C1'])),
        ('area-infeasible', capture([part('U1', 2, 2)], rectangle(0, 0, 1, 1)), request(fixed_refs=[])),
        ('candidate-budget', *promotion_case()),
        ('backjump-promotion', *promotion_case()),
        ('backjump-anchor', *anchor_case()),
    ]:
        if name == 'candidate-budget':
            policy = {**policy, 'step_budget': 1}
        yield name, source, {'policy': policy}, None
    for seed in range(seed_count):
        source, policy = seeded_board(seed)
        yield f'seed-{seed:03}', source, {'policy': policy}, None
    for name, spec in [
        ('row', row()), ('blocked-row', row(offset_nm=[10_000_000, 0])),
        ('cluster', {'mode': 'net_cluster_seed', 'refs': ['C1', 'C2'],
                     'origin_nm': [5_000_000, 2_000_000], 'spacing_nm': [3_000_000, 3_000_000],
                     'judge_policy': scoring()}),
    ]:
        yield f'targeted-{name}', targeted_board(), {'policy': request(), 'targeted': spec}, None
    yield 'targeted-selection', targeted_board(), {'policy': request(), 'targeted_plans': [
        row(step_nm=[7_000_000, 0]), row(offset_nm=[10_000_000, 0]), row(), row()]}, None
    from test_block_arrangement import ArrangementFixture, plan_policy
    from test_blocks import native_xml
    class BlockFixture(ArrangementFixture, unittest.TestCase):
        pass
    case = BlockFixture()
    try:
        case.setUp()
        section = {'arrangement': plan_policy(), 'candidates': case.entries}
        for name, entries in [('complete', case.entries), ('missing', case.entries[:1]),
                              ('duplicate', case.entries + case.entries[:1])]:
            yield f'blocks-{name}', case.board, {'policy': case.policy, 'blocks': {
                **section, 'candidates': entries}}, native_xml().decode('utf-8')
    finally:
        case.doCleanups()


def all_fixtures(seed_count, suites):
    for suite in suites:
        if suite == 'core':
            yield from fixtures(seed_count)
        else:
            module = importlib.import_module(f'placement_engine_{suite}_cases')
            yield from module.cases()


def generate(directory, seed_count, suites):
    from agent_ee.placer_runner import _plan_local
    from agent_ee import source_authority

    def require_production_authority():
        if source_authority.OWNER_DECISIONS:
            raise ValueError('historical fixture generation refuses process-local owner authority pins; '
                             'use tests/test_placement_engine_parity.py for the current parity gate')

    require_production_authority()
    directory.mkdir(parents=True, exist_ok=False)
    manifest = []
    names = set()
    for name, facts, request, netlist_xml in all_fixtures(seed_count, suites):
        # Fixture construction itself can register Python-only owner pins.
        require_production_authority()
        if name in names:
            raise ValueError(f'duplicate fixture name: {name}')
        names.add(name)
        payload = {'schema': 'agent-ee.rust-planning-input/1', 'facts': facts, 'request': request,
                   'policy': None, 'netlist_xml': netlist_xml}
        with tempfile.TemporaryDirectory(prefix='rust-parity-netlist-') as scratch:
            netlist = None
            if netlist_xml is not None:
                netlist = Path(scratch) / 'native.xml'
                netlist.write_bytes(netlist_xml.encode('utf-8'))
            try:
                expected = {'plan': normalized(_plan_local(copy.deepcopy(facts), copy.deepcopy(request),
                                                          netlist=netlist, heartbeat=lambda _: None))}
            except ValueError as error:
                expected = {'error': str(error)}
        raw = canonical(payload)
        (directory / f'{name}.input.json').write_bytes(raw)
        (directory / f'{name}.expected.json').write_bytes(canonical(expected))
        manifest.append({'name': name, 'input_sha256': hashlib.sha256(raw).hexdigest(),
                         'expected_sha256': hashlib.sha256(canonical(expected)).hexdigest()})
        print(json.dumps({'generated': name}), flush=True)
    (directory / 'manifest.json').write_bytes(canonical(manifest))


def first_difference(expected, actual, path='$'):
    if type(expected) is not type(actual):
        return {'path': path, 'expected_type': type(expected).__name__, 'actual_type': type(actual).__name__,
                'expected': expected, 'actual': actual}
    if isinstance(expected, dict) and isinstance(actual, dict):
        if set(expected) != set(actual):
            return {'path': path, 'missing': sorted(set(expected) - set(actual)),
                    'unexpected': sorted(set(actual) - set(expected))}
        for key in expected:
            difference = first_difference(expected[key], actual[key], f'{path}.{key}')
            if difference is not None:
                return difference
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return {'path': path, 'expected_length': len(expected), 'actual_length': len(actual)}
        for index, (before, after) in enumerate(zip(expected, actual)):
            difference = first_difference(before, after, f'{path}[{index}]')
            if difference is not None:
                return difference
    elif expected != actual or (isinstance(expected, float) and canonical(expected) != canonical(actual)):
        return {'path': path, 'expected': expected, 'actual': actual}
    return None


def check(directory, binary):
    manifest = json.loads((directory / 'manifest.json').read_bytes())
    results = []
    for row in manifest:
        name = row['name']
        raw = (directory / f'{name}.input.json').read_bytes()
        expected_raw = (directory / f'{name}.expected.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['input_sha256'] or hashlib.sha256(expected_raw).hexdigest() != row['expected_sha256']:
            raise ValueError(f'fixture identity changed: {name}')
        process = subprocess.run([str(binary)], input=raw, capture_output=True, timeout=120)
        (directory / f'{name}.rust.stdout.json').write_bytes(process.stdout)
        (directory / f'{name}.rust.stderr.txt').write_bytes(process.stderr)
        expected = json.loads(expected_raw)
        try:
            response = json.loads(process.stdout)
            if response.get('schema') != 'agent-ee.rust-planning-output/1':
                raise ValueError('wrong response schema')
            actual = {'error': response['error']} if 'error' in response else {'plan': normalized(response['plan'])}
            difference = first_difference(expected, actual)
            if process.returncode != (1 if 'error' in expected else 0):
                difference = {'exit_code': process.returncode, 'previous_difference': difference}
        except (ValueError, KeyError) as error:
            difference = {'invalid_response': str(error), 'exit_code': process.returncode}
        result = {'name': name, 'passed': difference is None, 'difference': difference}
        results.append(result)
        print(json.dumps(result), flush=True)
    (directory / 'parity-results.json').write_bytes(canonical(results))
    return all(result['passed'] for result in results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--generate', action='store_true')
    parser.add_argument('--seed-count', type=int, default=40)
    parser.add_argument('--suite', action='append', choices=('core', 'geometry', 'proof', 'block'))
    parser.add_argument('--binary', type=Path)
    args = parser.parse_args()
    if args.generate:
        generate(args.directory, args.seed_count, args.suite or ['core'])
    if args.binary:
        return 0 if check(args.directory, args.binary.resolve(strict=True)) else 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
