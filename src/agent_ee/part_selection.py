"""Deterministic generic-part selection from a pinned offline catalog."""
from collections import Counter
from decimal import Decimal, localcontext
from pathlib import Path
import sqlite3

from .catalog import (CATEGORY_ATTRIBUTES, UNITS, attribute_interval, decimal_number,
                      decimal_text, exact_fields, load_catalog, nonempty, quantity, strict_json)
from .storage import identity

RANK_ORDER = ['library_tier:Basic,Preferred,Extended', 'known_price_before_unknown',
              'price1:ascending', 'available_stock:descending', 'lcsc_number:ascending']
VALUE_ATTRIBUTES = {'resistor': 'resistance', 'capacitor': 'capacitance', 'inductor': 'inductance'}


def positive_integer(number, name, minimum=1):
    if type(number) is not int or number < minimum or number > 2**63 - 1:
        raise ValueError(f'{name} must be an integer >= {minimum} within SQLite stock range')
    return number


def constraint_interval(attribute, declaration):
    if not isinstance(declaration, dict):
        raise ValueError('constraint must be a mapping')
    kind = declaration.get('kind')
    unit = UNITS[attribute]
    if kind == 'within' and 'nominal' in declaration:
        exact_fields(declaration, ('kind', 'nominal', 'tolerance_percent'))
        tolerance = decimal_number(declaration['tolerance_percent'])
        if not 0 < tolerance < 100:
            raise ValueError('value requirement needs positive tolerance below 100%')
        interval = attribute_interval(declaration['nominal'] + ' +/-' + declaration['tolerance_percent'] + '%', unit)
    elif kind in ('within', 'covers'):
        exact_fields(declaration, ('kind', 'min', 'max'))
        low, high = quantity(declaration['min'], unit), quantity(declaration['max'], unit)
        if low >= high:
            raise ValueError('singleton/reversed requirement: declare an explicit nonzero tolerance or range')
        interval = {'min': decimal_text(low), 'max': decimal_text(high), 'unit': unit}
    elif kind in ('at_least', 'at_most'):
        bound = 'min' if kind == 'at_least' else 'max'
        exact_fields(declaration, ('kind', bound))
        interval = {bound: decimal_text(quantity(declaration[bound], unit)), 'unit': unit}
    else:
        raise ValueError('unknown constraint kind; value requirement needs tolerance or interval')
    if unit != 'degC' and any(Decimal(interval[end]) < 0 for end in ('min', 'max') if end in interval):
        raise ValueError('negative physical requirement')
    return {'kind': kind, **interval}


def validate_requirements(requirements):
    exact_fields(requirements, ('schema', 'build_quantity', 'stock_floor', 'parts'))
    if requirements['schema'] != 'agent-ee.part-requirements/1':
        raise ValueError('unsupported part requirements schema')
    positive_integer(requirements['build_quantity'], 'build_quantity')
    positive_integer(requirements['stock_floor'], 'stock_floor', 0)
    if not isinstance(requirements['parts'], list) or not requirements['parts']:
        raise ValueError('expected nonempty parts list')
    parts, ids = [], set()
    for part in requirements['parts']:
        exact_fields(part, ('id', 'category', 'package', 'quantity_per_board', 'constraints'))
        part_id = nonempty(part['id'])
        if part_id in ids:
            raise ValueError('duplicate requirement id')
        ids.add(part_id)
        nonempty(part['package'])
        if part['category'] not in CATEGORY_ATTRIBUTES:
            raise ValueError('unsupported generic category')
        count = positive_integer(part['quantity_per_board'], 'quantity_per_board')
        needed = positive_integer(count * requirements['build_quantity'], 'required stock')
        constraints = part['constraints']
        if not isinstance(constraints, dict) or not constraints or set(constraints) - set(CATEGORY_ATTRIBUTES[part['category']]):
            raise ValueError('missing or unsupported category constraints')
        normalized = {attribute: constraint_interval(attribute, declaration)
                      for attribute, declaration in sorted(constraints.items())}
        value_attribute = VALUE_ATTRIBUTES.get(part['category'])
        if value_attribute is not None and (value_attribute not in normalized or normalized[value_attribute]['kind'] != 'within'):
            raise ValueError('passive value requirement needs a nonzero tolerance/range (within)')
        parts.append({**part, 'constraints': normalized, 'needed_stock': needed,
                      'minimum_stock': max(needed, requirements['stock_floor'])})
    return parts


def satisfies(candidate, requirement):
    low, high = Decimal(candidate['min']), Decimal(candidate['max'])
    kind = requirement['kind']
    if kind == 'within':
        return Decimal(requirement['min']) <= low <= high <= Decimal(requirement['max'])
    if kind == 'covers':
        return low <= Decimal(requirement['min']) <= Decimal(requirement['max']) <= high
    if kind == 'at_least':
        return low >= Decimal(requirement['min'])
    return high <= Decimal(requirement['max'])


def evaluate_row(row, part, allocated):
    reasons, matched = [], {}
    for field in ('category', 'package'):
        if row[field] != part[field]:
            reasons.append({'code': field + '_mismatch', 'unknown': False})
    if reasons:
        return {'lcsc': row['lcsc'], 'reasons': reasons}, None
    stock = row['stock']
    available = None if stock is None else stock - allocated.get(row['lcsc'], 0)
    if available is None:
        reasons.append({'code': 'unknown_stock', 'unknown': True})
    elif available < part['minimum_stock']:
        reasons.append({'code': 'low_stock', 'unknown': False, 'available': available,
                        'required': part['minimum_stock']})
    for attribute, requirement in part['constraints'].items():
        candidate = row['normalized'].get(attribute)
        value_without_tolerance = (attribute == VALUE_ATTRIBUTES.get(part['category'])
                                   and candidate is not None and candidate['min'] == candidate['max'])
        if candidate is None or value_without_tolerance:
            reasons.append({'code': 'unknown_attribute', 'attribute': attribute, 'unknown': True})
        elif not satisfies(candidate, requirement):
            reasons.append({'code': 'constraint_mismatch', 'attribute': attribute, 'unknown': False,
                            'candidate': candidate, 'requirement': requirement})
        else:
            matched[attribute] = {'candidate': candidate, 'requirement': requirement}
    if reasons:
        return {'lcsc': row['lcsc'], 'reasons': reasons}, None
    tier = 'Basic' if row['is_basic'] else 'Preferred' if row['is_preferred'] else 'Extended'
    return None, {'row': row, 'matched_constraints': matched, 'rank': {
        'library_tier': tier, 'price1': row['price1'], 'available_stock': available,
        'lcsc_number': int(row['lcsc'][1:])}}


def rank_key(candidate):
    rank = candidate['rank']
    price = rank['price1']
    return (('Basic', 'Preferred', 'Extended').index(rank['library_tier']), price is None,
            Decimal(price) if price is not None else Decimal(0),
            -rank['available_stock'], rank['lcsc_number'])


def deciding_rank(ranked):
    if not ranked:
        return {'reason': 'no candidate'}
    if len(ranked) == 1:
        return {'reason': 'only eligible candidate'}
    winner, runner_up = ranked[:2]
    first, second = rank_key(winner), rank_key(runner_up)
    deciding = next(index for index, pair in enumerate(zip(first, second)) if pair[0] != pair[1])
    return {'winner': winner['row']['lcsc'], 'runner_up': runner_up['row']['lcsc'],
            'deciding_key': RANK_ORDER[deciding]}


def evaluate_part(rows, part, allocated):
    rejected, ranked = [], []
    for row in rows:
        rejection, candidate = evaluate_row(row, part, allocated)
        if rejection is not None:
            rejected.append(rejection)
        else:
            ranked.append(candidate)
    counts = Counter(reason['code'] for rejection in rejected for reason in rejection['reasons'])
    return {'id': part['id'], 'constraints': part['constraints'],
            'needed_stock': part['needed_stock'], 'minimum_stock': part['minimum_stock'],
            'ranked': sorted(ranked, key=rank_key), 'rejected': rejected,
            'exclusion_counts': dict(sorted(counts.items())), 'excluded_rows': len(rejected)}


def select_parts(snapshot_directory, expected_manifest_sha256, requirements):
    """No writes, network, clock, or inference; output binds every input and decision."""
    snapshot = load_catalog(snapshot_directory, expected_manifest_sha256)
    return _select_snapshot(snapshot, expected_manifest_sha256, requirements)


def _select_snapshot(snapshot, expected_manifest_sha256, requirements):
    with localcontext() as context:
        context.prec = 80
        pending = validate_requirements(requirements)
        allocated, selections = {}, []
        while pending:
            evaluated = [(part, evaluate_part(snapshot['rows'], part, allocated)) for part in pending]
            part, evaluation = min(evaluated, key=lambda pair: (
                len(pair[1]['ranked']), -len(pair[0]['constraints']), pair[0]['id']))
            chosen = evaluation['ranked'][0]['row'] if evaluation['ranked'] else None
            evaluation.update(chosen=chosen, outcome='pass' if chosen is not None else 'unknown',
                              reason='ranked candidate' if chosen is not None else 'no candidate',
                              tie_break=deciding_rank(evaluation['ranked']),
                              selection_order=len(selections) + 1,
                              ordering={'eligible_candidates': len(evaluation['ranked']),
                                        'constraint_count': len(part['constraints']), 'id': part['id']})
            if chosen is not None:
                allocated[chosen['lcsc']] = allocated.get(chosen['lcsc'], 0) + part['needed_stock']
            selections.append(evaluation)
            pending.remove(part)
    manifest = snapshot['manifest']
    return {'schema': 'agent-ee.selection/1', 'execution': 'ok',
            'outcome': 'pass' if all(part['chosen'] is not None for part in selections) else 'unknown',
            'snapshot_manifest_sha256': expected_manifest_sha256,
            'snapshot_sha256': manifest['database']['sha256'], 'snapshot_date': manifest['snapshot_date'],
            'requirements_sha256': identity(requirements), 'currency': manifest['currency'],
            'build_quantity': requirements['build_quantity'], 'stock_floor': requirements['stock_floor'],
            'rank_order': RANK_ORDER.copy(),
            'selection_order': 'fewest_eligible_then_most_constraints_then_id; re-evaluated after allocation',
            'allocations': dict(sorted(allocated.items())), 'parts': selections}


def selection_check(snapshot_directory, expected_manifest_sha256, requirements):
    """Check a requirements object or captured Path, retaining verified snapshot identity."""
    snapshot = None
    try:
        snapshot = load_catalog(snapshot_directory, expected_manifest_sha256)
        if isinstance(requirements, Path):
            requirements = strict_json(requirements.read_text(encoding='utf-8'))
        return _select_snapshot(snapshot, expected_manifest_sha256, requirements)
    except (OSError, ValueError, sqlite3.Error, TypeError, KeyError) as exc:
        return {'schema': 'agent-ee.selection/1', 'execution': 'failed', 'outcome': 'unknown',
                'snapshot_manifest_sha256': expected_manifest_sha256, 'parts': [],
                'snapshot_date': snapshot['manifest']['snapshot_date'] if snapshot else None,
                'snapshot_sha256': snapshot['manifest']['database']['sha256'] if snapshot else None,
                'details': {'error': str(exc)}}
