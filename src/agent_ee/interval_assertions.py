"""Worst-case interval verification of owner-declared design assertions.

A captured agent-ee.interval-assertions/1 document declares named parameters
(captured requirement intervals, or pinned catalog part intervals and ratings)
and algebraic assertions over them. Each assertion is evaluated forward over
the parameter box with exact rational interval arithmetic; there is no
symbolic back-solving and no binary floating point in any bound.

Correlation rule (declared as ``shared-symbol/independent-parameters``): one
parameter name denotes one value wherever it occurs, and distinct parameters
vary independently over their whole intervals (worst case). A pass requires the
outer enclosure inside the target. A defect requires an attained parameter
corner outside the target. Anything else, including an unbounded operand or a
division by an interval containing zero, is unknown and never a pass.
"""
import argparse
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from fractions import Fraction
import hashlib
from itertools import product
from pathlib import Path
import re
import sqlite3

from .candidate import safe_relative
from .catalog import UNITS, decimal_number, exact_fields, load_catalog, nonempty, quantity, strict_json
from .storage import write_json
from .copper_features import is_copper
from .probe_features import is_probe

SCHEMA = 'agent-ee.interval-assertions/1'
RESULT_SCHEMA = 'agent-ee.interval-assertion-result/1'
CORRELATION = 'shared-symbol/independent-parameters'
PREDICATE = 'interval-assertion:'
TIMEOUT = 300
MAX_SYMBOLS = 12
MAX_ENTRIES = 256
MAX_EXPRESSION = 512
MAX_DEPTH = 64
NAME = re.compile(r'[a-z][a-z0-9_]{0,31}')
ASSERTION_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}')
TOKEN = re.compile(r'\s*(?:(?P<number>(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)|(?P<name>[A-Za-z_][A-Za-z0-9_]*)|(?P<op>[-+*/()]))')
# Exponents of (V, A, s); every other supported unit is derived from them.
DIMENSIONS = {'1': (0, 0, 0), 'V': (1, 0, 0), 'A': (0, 1, 0), 's': (0, 0, 1), 'Hz': (0, 0, -1),
              'ohm': (1, -1, 0), 'F': (-1, 1, 1), 'H': (1, -1, 1), 'W': (1, 1, 0)}
ROLES = ('toleranced', 'rating')
LIMIT = ('Forward worst-case arithmetic over declared relations and captured interval claims only: '
         'the owner-declared equation is not derived from the schematic, catalog claims are not '
         'authenticated datasheet facts, and no temperature, aging, loading or physical validation is implied.')


class Unbounded(ValueError):
    """An interval operation has no finite enclosure."""


# ---------------------------------------------------------------- expressions

def parse_expression(text):
    """Parse + - * / ( ) over names and dimensionless decimal literals."""
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_EXPRESSION:
        raise ValueError(f'expression must be nonempty text of at most {MAX_EXPRESSION} characters')
    tokens, position = [], 0
    while text[position:].strip():
        match = TOKEN.match(text, position)
        if match is None:
            raise ValueError(f'malformed expression {text!r} at offset {position}')
        tokens.append((match.lastgroup, match[match.lastgroup]))
        position = match.end()
    tokens.append(('end', None))
    index = 0

    def peek():
        return tokens[index]

    def take():
        nonlocal index
        index += 1
        return tokens[index - 1]

    def expression(depth):
        if depth > MAX_DEPTH:
            raise ValueError('expression nests too deeply')
        node = term(depth)
        while peek() in (('op', '+'), ('op', '-')):
            node = (take()[1], node, term(depth))
        return node

    def term(depth):
        node = factor(depth)
        while peek() in (('op', '*'), ('op', '/')):
            node = (take()[1], node, factor(depth))
        return node

    def factor(depth):
        if depth > MAX_DEPTH:
            raise ValueError('expression nests too deeply')
        kind, value = take()
        if (kind, value) == ('op', '-'):
            return ('neg', factor(depth + 1))
        if (kind, value) == ('op', '('):
            node = expression(depth + 1)
            if take() != ('op', ')'):
                raise ValueError(f'unbalanced parenthesis in {text!r}')
            return node
        if kind == 'number':
            return ('num', Fraction(decimal_number(value)))
        if kind == 'name':
            if not NAME.fullmatch(value):
                raise ValueError(f'invalid parameter name {value!r} in {text!r}')
            return ('sym', value)
        raise ValueError(f'malformed expression {text!r}: unexpected {value or "end"!r}')

    node = expression(0)
    if peek() != ('end', None):
        raise ValueError(f'malformed expression {text!r}: unexpected {peek()[1]!r}')
    return node


def symbols(node):
    if node[0] == 'sym':
        return {node[1]}
    return set().union(*(symbols(child) for child in node[1:] if isinstance(child, tuple)))


def dimension(node, dims):
    op = node[0]
    if op == 'num':
        return DIMENSIONS['1']
    if op == 'sym':
        return dims[node[1]]
    if op == 'neg':
        return dimension(node[1], dims)
    left, right = dimension(node[1], dims), dimension(node[2], dims)
    if op in '+-':
        if left != right:
            raise ValueError('expression adds or subtracts quantities of different units')
        return left
    sign = 1 if op == '*' else -1
    return tuple(a + sign * b for a, b in zip(left, right))


# ---------------------------------------------------------------- interval arithmetic

ZERO = (Fraction(0), Fraction(0))
ONE = (Fraction(1), Fraction(1))


def i_add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def i_neg(a):
    return (-a[1], -a[0])


def i_mul(a, b):
    products = (a[0] * b[0], a[0] * b[1], a[1] * b[0], a[1] * b[1])
    return (min(products), max(products))


def i_div(a, b):
    if b[0] <= 0 <= b[1]:
        raise Unbounded('division by an interval containing zero')
    return i_mul(a, (1 / b[1], 1 / b[0]))


def i_square(a):
    low, high = sorted((abs(a[0]), abs(a[1])))
    return (Fraction(0) if a[0] <= 0 <= a[1] else low * low, high * high)


def forward(node, box):
    """Natural interval enclosure and forward-mode interval partial derivatives."""
    op = node[0]
    if op == 'num':
        return (node[1], node[1]), {}
    if op == 'sym':
        return box[node[1]], {node[1]: ONE}
    if op == 'neg':
        value, grad = forward(node[1], box)
        return i_neg(value), {k: i_neg(d) for k, d in grad.items()}
    (u, du), (v, dv) = forward(node[1], box), forward(node[2], box)
    keys = du.keys() | dv.keys()
    if op == '+':
        return i_add(u, v), {k: i_add(du.get(k, ZERO), dv.get(k, ZERO)) for k in keys}
    if op == '-':
        return i_add(u, i_neg(v)), {k: i_add(du.get(k, ZERO), i_neg(dv.get(k, ZERO))) for k in keys}
    if op == '*':
        return i_mul(u, v), {k: i_add(i_mul(du.get(k, ZERO), v), i_mul(u, dv.get(k, ZERO))) for k in keys}
    value = i_div(u, v)
    square = i_square(v)
    return value, {k: i_div(i_add(i_mul(du.get(k, ZERO), v), i_neg(i_mul(u, dv.get(k, ZERO)))), square)
                   for k in keys}


def point(node, assignment):
    return forward(node, {k: (x, x) for k, x in assignment.items()})[0][0]


def enclose(node, box):
    """Sound enclosure of the range; monotone parameters are pinned to their extreme endpoints.

    A parameter whose derivative enclosure is sign-definite over the whole box
    attains each extreme at an endpoint, which removes the dependency
    overestimate of repeated occurrences (the shared-symbol correlation).
    """
    value, grad = forward(node, box)
    low_box, high_box, monotonicity = dict(box), dict(box), {}
    for name, (low, high) in sorted(box.items()):
        derivative = grad.get(name, ZERO)
        if low == high:
            monotonicity[name] = 'fixed'
        elif derivative[0] >= 0:
            low_box[name], high_box[name] = (low, low), (high, high)
            monotonicity[name] = 'nondecreasing'
        elif derivative[1] <= 0:
            low_box[name], high_box[name] = (high, high), (low, low)
            monotonicity[name] = 'nonincreasing'
        else:
            monotonicity[name] = 'unproven'
    low = max(value[0], forward(node, low_box)[0][0])
    high = min(value[1], forward(node, high_box)[0][1])
    return (low, high), monotonicity


def render(value, rounding):
    """Exact decimal text when terminating; otherwise 15 digits rounded outward."""
    with localcontext() as context:
        context.prec = 80
        exact = Decimal(value.numerator) / Decimal(value.denominator)
        if Fraction(exact) == value:
            text = format(exact.normalize(), 'f')
            return text, False
        context.prec, context.rounding = 15, rounding
        return format(Decimal(value.numerator) / Decimal(value.denominator), 'f'), True


def interval_record(low, high, unit):
    record = {'min': None, 'max': None, 'unit': unit, 'rational': [None, None], 'rounded_outward': False}
    for index, (key, value, rounding) in enumerate((('min', low, ROUND_FLOOR), ('max', high, ROUND_CEILING))):
        if value is not None:
            record[key], rounded = render(value, rounding)
            record['rational'][index] = str(value)
            record['rounded_outward'] |= rounded
    return record


# ---------------------------------------------------------------- document

def magnitude(text, unit, label):
    if not isinstance(text, str):
        raise ValueError(f'{label} must be quantity text')
    return Fraction(decimal_number(text.strip()) if unit == '1' else quantity(text, unit))


def declared_interval(spec, unit, label, *, target=False):
    """Return (low, high); a missing tolerance or side is None (unbounded)."""
    keys = set(spec) if isinstance(spec, dict) else None
    if keys == {'nominal', 'tolerance_percent'}:
        nominal = magnitude(spec['nominal'], unit, label + ' nominal')
        tolerance = Fraction(decimal_number(spec['tolerance_percent'])) if isinstance(spec['tolerance_percent'], str) else None
        if tolerance is None or not 0 <= tolerance < 100:
            raise ValueError(f'{label} tolerance_percent must be decimal text in [0, 100)')
        delta = abs(nominal) * tolerance / 100
        return nominal - delta, nominal + delta
    if keys == {'min', 'max'}:
        low, high = magnitude(spec['min'], unit, label + ' min'), magnitude(spec['max'], unit, label + ' max')
        if low > high:
            raise ValueError(f'{label} has reversed bounds')
        return low, high
    if keys == {'min'}:
        return magnitude(spec['min'], unit, label + ' min'), None
    if keys == {'max'}:
        return None, magnitude(spec['max'], unit, label + ' max')
    if keys == {'nominal'} and not target:
        magnitude(spec['nominal'], unit, label + ' nominal')
        return None, None
    raise ValueError(f'{label} needs nominal+tolerance_percent, min+max, min or max')


def validate_parameter(name, spec):
    if not NAME.fullmatch(name) or not isinstance(spec, dict):
        raise ValueError(f'invalid parameter {name!r}')
    source = spec.get('source')
    if source == 'declared':
        unit = spec.get('unit')
        if unit not in DIMENSIONS:
            raise ValueError(f'parameter {name}: unsupported unit {unit!r}')
        safe_requirement(spec.get('requirement'))
        quantity_spec = {k: v for k, v in spec.items() if k not in ('source', 'requirement', 'unit')}
        declared_interval(quantity_spec, unit, f'parameter {name}')
        return {**spec, 'unit': unit}
    if source == 'catalog':
        keys = set(spec) - {'reference'}
        if keys not in ({'source', 'lcsc', 'attribute', 'role'}, {'source', 'part', 'attribute', 'role'}):
            raise ValueError(f'parameter {name}: catalog source needs lcsc or part, attribute and role')
        if 'lcsc' in spec and not (isinstance(spec['lcsc'], str) and re.fullmatch('C[1-9][0-9]*', spec['lcsc'])):
            raise ValueError(f'parameter {name}: invalid LCSC')
        if 'part' in spec:
            nonempty(spec['part'])
        if 'reference' in spec:
            nonempty(spec['reference'])
        unit = UNITS.get(spec['attribute'])
        if unit not in DIMENSIONS:
            raise ValueError(f'parameter {name}: unsupported catalog attribute {spec["attribute"]!r}')
        if spec['role'] not in ROLES:
            raise ValueError(f'parameter {name}: role must be toleranced or rating')
        return {**spec, 'unit': unit}
    raise ValueError(f'parameter {name}: source must be declared or catalog')


def safe_requirement(value):
    if not isinstance(value, str) or not ASSERTION_ID.fullmatch(value):
        raise ValueError(f'invalid requirement id {value!r}')
    return value


def validate_document(document):
    """Validate and compile the complete document; malformed input raises ValueError."""
    exact_fields(document, ('schema', 'correlation', 'parameters', 'assertions'))
    if document['schema'] != SCHEMA:
        raise ValueError('unsupported interval assertion schema')
    if document['correlation'] != CORRELATION:
        raise ValueError(f'unsupported correlation rule; only {CORRELATION} is defined')
    parameters = document['parameters']
    if not isinstance(parameters, dict) or not parameters or len(parameters) > MAX_ENTRIES:
        raise ValueError('nonempty bounded parameters required')
    parameters = {name: validate_parameter(name, spec) for name, spec in parameters.items()}
    dims = {name: DIMENSIONS[p['unit']] for name, p in parameters.items()}
    assertions = document['assertions']
    if not isinstance(assertions, list) or not assertions or len(assertions) > MAX_ENTRIES:
        raise ValueError('nonempty bounded assertions required')
    compiled, ids = [], set()
    for assertion in assertions:
        if not isinstance(assertion, dict):
            raise ValueError('assertion must be an object')
        if not isinstance(assertion.get('id'), str) or not ASSERTION_ID.fullmatch(assertion['id']) or assertion['id'] in ids:
            raise ValueError(f'invalid or duplicate assertion id {assertion.get("id")!r}')
        ids.add(assertion['id'])
        label = f'assertion {assertion["id"]}'
        unit = assertion.get('unit')
        if unit not in DIMENSIONS:
            raise ValueError(f'{label}: unsupported unit {unit!r}')
        safe_requirement(assertion.get('requirement'))
        if 'expression' in assertion:
            exact_fields(assertion, ('id', 'requirement', 'expression', 'unit', 'target'))
            node = parse_expression(assertion['expression'])
            target = declared_interval(assertion['target'], unit, label + ' target', target=True)
            sides = None
        else:
            exact_fields(assertion, ('id', 'requirement', 'lhs', 'op', 'rhs', 'unit'))
            if assertion['op'] not in ('<=', '>='):
                raise ValueError(f'{label}: relation op must be <= or >=')
            sides = {'lhs': parse_expression(assertion['lhs']), 'rhs': parse_expression(assertion['rhs'])}
            small, large = ('lhs', 'rhs') if assertion['op'] == '<=' else ('rhs', 'lhs')
            node = ('-', sides[large], sides[small])
            target = (Fraction(0), None)
        names = symbols(node)
        unknown = sorted(names - parameters.keys())
        if unknown:
            raise ValueError(f'{label}: undeclared parameters {", ".join(unknown)}')
        if len(names) > MAX_SYMBOLS:
            raise ValueError(f'{label}: more than {MAX_SYMBOLS} parameters')
        trees = [node] if sides is None else list(sides.values())
        for tree in trees:
            if dimension(tree, dims) != DIMENSIONS[unit]:
                raise ValueError(f'{label}: expression unit differs from declared unit {unit}')
        compiled.append({**assertion, 'node': node, 'sides': sides, 'target_interval': target,
                         'symbols': sorted(names)})
    return {'parameters': parameters, 'assertions': compiled}


def cited_requirements(compiled):
    cited = {a['requirement'] for a in compiled['assertions']}
    cited.update(p['requirement'] for p in compiled['parameters'].values() if p['source'] == 'declared')
    return sorted(cited)


def load_document(raw):
    return validate_document(strict_json(raw.decode('utf-8')))


# ---------------------------------------------------------------- capability

def capability(params, scope, files, inputs, cid):
    if not isinstance(params, dict) or set(params) not in (
            {'assertions'}, {'assertions', 'snapshot', 'manifest_sha256'},
            {'assertions', 'snapshot', 'manifest_sha256', 'parts_manifest'}):
        raise ValueError('interval_assertions needs assertions, optional snapshot+manifest_sha256 and optional parts_manifest')
    path = str(safe_relative(params['assertions']))
    paths = [path]
    if 'snapshot' in params:
        if not isinstance(params['manifest_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', params['manifest_sha256']):
            raise ValueError('manifest_sha256 requires SHA-256')
        snapshot = str(safe_relative(params['snapshot']))
        paths += [snapshot + '/manifest.json', snapshot + '/catalog.sqlite3']
    if 'parts_manifest' in params:
        paths.append(str(safe_relative(params['parts_manifest'])))
    if path != params['assertions'] or any(p not in files for p in paths):
        raise ValueError('interval assertions and their catalog/parts dependencies must be captured')
    if inputs is None:
        raise ValueError('interval assertions require captured inputs')
    compiled = load_document((Path(inputs) / path).read_bytes())
    parts = None
    if 'parts_manifest' in params:
        from .parts_manifest import load_manifest
        manifest = load_manifest(inputs, params['parts_manifest'])
        if manifest['catalog'] is None:
            raise ValueError('parts manifest has no catalog snapshot for interval assertions')
        if (manifest['catalog']['snapshot'] != params['snapshot']
                or manifest['catalog']['manifest_sha256'] != params['manifest_sha256']):
            raise ValueError('parts manifest pins a different catalog snapshot')
        parts = {part['id'] for part in manifest['parts'] if not is_probe(part) and not is_copper(part)}
    for name, parameter in compiled['parameters'].items():
        if parameter['source'] == 'catalog' and 'snapshot' not in params:
            raise ValueError(f'parameter {name} needs a pinned catalog snapshot')
        if 'part' in parameter and (parts is None or parameter['part'] not in parts):
            raise ValueError(f'parameter {name} names a part absent from a captured parts manifest')
    requirements = {r['id']: r for r in scope.get('baseline', {}).get('requirements', [])}
    for rid in cited_requirements(compiled):
        requirement = requirements.get(rid)
        if requirement is None or cid not in requirement['checks'] or requirement['source']['path'] != path:
            raise ValueError(f'requirement {rid} must be a baseline requirement sourced from {path} and bound to {cid}')
    return {'immutable_inputs': sorted(paths),
            'predicates': [PREDICATE + a['id'] for a in compiled['assertions']]}


# ---------------------------------------------------------------- evaluation

def resolve(name, parameter, document_input, catalog, rows, parts):
    """Return (interval or None, origin, unknown reason or None)."""
    if parameter['source'] == 'declared':
        origin = {'source': 'declared', 'requirement': parameter['requirement'], **document_input}
        spec = {k: v for k, v in parameter.items() if k not in ('source', 'requirement', 'unit')}
        low, high = declared_interval(spec, parameter['unit'], f'parameter {name}')
        if low is None or high is None:
            return None, origin, 'declared parameter has no tolerance or bound (unbounded)'
        return (low, high), origin, None
    lcsc = parameter.get('lcsc') or parts['lcsc'][parameter['part']]
    origin = {'source': 'catalog', 'lcsc': lcsc, 'attribute': parameter['attribute'], 'role': parameter['role'],
              **{key: parameter[key] for key in ('part', 'reference') if key in parameter},
              **({'parts_manifest': parts['input']} if 'part' in parameter else {}),
              'catalog': catalog, 'claim': None, 'row_provenance': None}
    row = rows.get(lcsc)
    if row is None:
        return None, origin, 'LCSC is absent from the pinned catalog snapshot'
    origin.update(claim=row['attributes'].get(parameter['attribute']), row_provenance=row['provenance'])
    claim = row['normalized'].get(parameter['attribute'])
    if claim is None:
        return None, origin, f'catalog row has no interpretable {parameter["attribute"]} claim'
    low, high = Fraction(Decimal(claim['min'])), Fraction(Decimal(claim['max']))
    if parameter['role'] == 'toleranced' and low == high:
        return None, origin, f'catalog {parameter["attribute"]} claim has no tolerance (unbounded)'
    return (low, high), origin, None


def corners(node, box):
    """Exact values at every endpoint combination of the non-degenerate parameters."""
    varying = [name for name in sorted(box) if box[name][0] != box[name][1]]
    results = []
    for ends in product((0, 1), repeat=len(varying)):
        assignment = {name: box[name][0] for name in box}
        endpoints = {name: 'fixed' for name in box}
        for name, end in zip(varying, ends):
            assignment[name] = box[name][end]
            endpoints[name] = ('min', 'max')[end]
        results.append((point(node, assignment), assignment, endpoints))
    return results


def corner_record(corner, unit):
    value, assignment, endpoints = corner
    return {'value': interval_record(value, value, unit)['min'], 'rational': str(value),
            'parameters': {name: {'value': render(assignment[name], ROUND_FLOOR)[0],
                                  'rational': str(assignment[name]), 'endpoint': endpoints[name]}
                           for name in sorted(assignment)}}


def outside(value, target):
    low, high = target
    if low is not None and value < low:
        return low - value
    if high is not None and value > high:
        return value - high
    return None


def evaluate_assertion(assertion, operands):
    target = assertion['target_interval']
    record = {'id': assertion['id'], 'requirement': assertion['requirement'], 'unit': assertion['unit'],
              'target': interval_record(*target, assertion['unit']),
              'operands': {name: operands[name]['origin'] for name in assertion['symbols']},
              'computed': None, 'corner_hull': None, 'exact': False, 'monotonicity': {},
              'bounds': None, 'witness': None, 'contributions': [], 'reasons': []}
    if 'expression' in assertion:
        record['expression'] = assertion['expression']
    else:
        record['relation'] = {key: assertion[key] for key in ('lhs', 'op', 'rhs')}
    missing = [name for name in assertion['symbols'] if operands[name]['interval'] is None]
    if missing:
        record['reasons'] = [f'operand {name} unknown: {operands[name]["unknown"]}' for name in missing]
        record['verdict'] = 'unknown'
        return record
    box = {name: operands[name]['interval'] for name in assertion['symbols']}
    node = assertion['node']
    try:
        (low, high), monotonicity = enclose(node, box)
        if assertion['sides'] is not None:
            record['relation'].update({side: interval_record(*enclose(tree, {
                name: box[name] for name in symbols(tree)})[0], assertion['unit'])
                for side, tree in assertion['sides'].items()})
    except Unbounded as exc:
        record['reasons'] = [f'unbounded: {exc}']
        record['verdict'] = 'unknown'
        return record
    attained = corners(node, box)
    low_corner = min(attained, key=lambda c: c[0])
    high_corner = max(attained, key=lambda c: c[0])
    exact = (low, high) == (low_corner[0], high_corner[0])
    unit = assertion['unit']
    record.update(computed=interval_record(low, high, unit), monotonicity=monotonicity, exact=exact,
                  corner_hull=interval_record(low_corner[0], high_corner[0], unit),
                  bounds={'min': corner_record(low_corner, unit) if low == low_corner[0] else None,
                          'max': corner_record(high_corner, unit) if high == high_corner[0] else None})
    if outside(low, target) is None and outside(high, target) is None:
        record['verdict'] = 'pass'
        return record
    violations = [(outside(c[0], target), index, c) for index, c in enumerate(attained) if outside(c[0], target) is not None]
    if not violations:
        record['verdict'] = 'unknown'
        record['reasons'] = ['enclosure exceeds the target but no attained parameter corner violates it; '
                             'monotonicity could not be proven for: '
                             + ', '.join(n for n, m in monotonicity.items() if m == 'unproven')]
        return record
    _, _, witness = max(violations, key=lambda v: (v[0], -v[1]))
    value, assignment, _ = witness
    contributions = []
    for name in sorted(box):
        if box[name][0] != box[name][1]:
            flipped = dict(assignment, **{name: box[name][1] if assignment[name] == box[name][0] else box[name][0]})
            contributions.append((abs(value - point(node, flipped)), name))
    contributions.sort(key=lambda c: (-c[0], c[1]))
    record.update(verdict='defect', witness=corner_record(witness, unit),
                  contributions=[{'parameter': name, 'origin': operands[name]['origin'],
                                  'swing': render(swing, ROUND_CEILING)[0], 'rational': str(swing)}
                                 for swing, name in contributions])
    return record


def evaluate(compiled, document_input, catalog=None, rows=None, parts=None):
    operands = {}
    for name, parameter in sorted(compiled['parameters'].items()):
        interval, origin, unknown = resolve(name, parameter, document_input, catalog, rows or {}, parts)
        operands[name] = {'interval': interval, 'origin': origin, 'unknown': unknown}
    units = {name: p['unit'] for name, p in compiled['parameters'].items()}
    assertions = [evaluate_assertion(a, operands) for a in compiled['assertions']]
    verdicts = {a['verdict'] for a in assertions}
    outcome = 'defect' if 'defect' in verdicts else 'unknown' if 'unknown' in verdicts else 'pass'
    return {'execution': 'ok', 'outcome': outcome,
            'parameters': {name: {'unit': units[name], 'origin': o['origin'], 'unknown': o['unknown'],
                                  'interval': None if o['interval'] is None else interval_record(*o['interval'], units[name])}
                           for name, o in operands.items()},
            'assertions': assertions}


def evaluate_inputs(root, params):
    """Evaluate captured inputs under ``root``; failures are recorded, never passed."""
    root = Path(root)
    raw = (root / params['assertions']).read_bytes()
    document_input = {'document': params['assertions'], 'document_sha256': hashlib.sha256(raw).hexdigest()}
    report = {'schema': RESULT_SCHEMA, 'correlation': CORRELATION,
              'inputs': {'assertions': {'path': params['assertions'], 'sha256': document_input['document_sha256']},
                         'catalog': None, 'parts_manifest': None}}
    try:
        compiled = load_document(raw)
    except (ValueError, UnicodeDecodeError, TypeError, KeyError) as exc:
        return {**report, 'execution': 'malformed', 'outcome': 'unknown', 'error': str(exc)}
    try:
        catalog = rows = parts = None
        if 'snapshot' in params:
            snapshot = load_catalog(root / params['snapshot'], params['manifest_sha256'])
            catalog = {'snapshot': params['snapshot'], 'manifest_sha256': params['manifest_sha256'],
                       'database_sha256': snapshot['manifest']['database']['sha256'],
                       'snapshot_date': snapshot['manifest']['snapshot_date']}
            rows = {row['lcsc']: row for row in snapshot['rows']}
            report['inputs']['catalog'] = catalog
        if 'parts_manifest' in params:
            from .parts_manifest import load_manifest, read_captured
            manifest = load_manifest(root, params['parts_manifest'])
            if manifest['catalog'] is None:
                raise ValueError('parts manifest has no catalog snapshot for interval assertions')
            if (manifest['catalog']['snapshot'] != params['snapshot']
                    or manifest['catalog']['manifest_sha256'] != params['manifest_sha256']
                    or manifest['catalog']['database_sha256'] != catalog['database_sha256']):
                raise ValueError('parts manifest pins a different catalog snapshot')
            parts_input = {'path': params['parts_manifest'],
                           'sha256': hashlib.sha256(read_captured(root, params['parts_manifest'])).hexdigest()}
            parts = {'input': parts_input, 'lcsc': {part['id']: part['lcsc'] for part in manifest['parts']
                                                 if not is_probe(part) and not is_copper(part)}}
            report['inputs']['parts_manifest'] = parts_input
        for name, parameter in compiled['parameters'].items():
            if parameter['source'] == 'catalog' and rows is None:
                raise ValueError(f'parameter {name} needs a pinned catalog snapshot')
            if 'part' in parameter and (parts is None or parameter['part'] not in parts['lcsc']):
                raise ValueError(f'parameter {name} names a part absent from the parts manifest')
    except (OSError, ValueError, sqlite3.Error, TypeError, KeyError) as exc:
        return {**report, 'execution': 'failed', 'outcome': 'unknown', 'error': str(exc)}
    return {**report, **evaluate(compiled, document_input, catalog, rows, parts)}


# ---------------------------------------------------------------- check

def check_interval_assertions(ctx):
    from .checks import CheckError
    try:
        capability(ctx.params, ctx.scope, ctx.manifest['files'], ctx.inputs, ctx.cid)
    except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise CheckError('malformed', str(exc)) from exc
    argv = ['/usr/bin/python3', '-m', 'agent_ee.interval_assertions', '/candidate', ctx.params['assertions'],
            '--out', '/work/intervals.json']
    if 'snapshot' in ctx.params:
        argv += ['--snapshot', ctx.params['snapshot'], '--manifest-sha256', ctx.params['manifest_sha256']]
    if 'parts_manifest' in ctx.params:
        argv += ['--parts-manifest', ctx.params['parts_manifest']]
    try:
        ctx.execute(argv, TIMEOUT)
    finally:
        ctx.evidence('intervals.json')
    report = ctx.load_json('intervals.json', 'interval assertions')
    try:
        expected = hashlib.sha256((ctx.inputs / ctx.params['assertions']).read_bytes()).hexdigest()
        if (report.get('schema') != RESULT_SCHEMA or report.get('correlation') != CORRELATION
                or report['inputs']['assertions'] != {'path': ctx.params['assertions'], 'sha256': expected}):
            raise ValueError('interval report is not bound to the captured assertion document')
        ctx.result['details']['report'] = report
        ctx.result['limitations'].append(LIMIT)
        if report.get('execution') != 'ok':
            raise CheckError(report['execution'] if report.get('execution') in ('malformed', 'failed') else 'malformed',
                             'interval assertions not evaluated: ' + str(report.get('error')))
        assertions = report['assertions']
        verdicts = {a['id']: a['verdict'] for a in assertions}
        declared = [a['id'] for a in load_document((ctx.inputs / ctx.params['assertions']).read_bytes())['assertions']]
        aggregate = 'defect' if 'defect' in verdicts.values() else 'unknown' if 'unknown' in verdicts.values() else 'pass'
        if (list(verdicts) != declared or report['outcome'] != aggregate
                or not set(verdicts.values()) <= {'pass', 'defect', 'unknown'}):
            raise ValueError('interval report verdicts are incomplete or inconsistent')
        requirements = {}
        for a in assertions:
            requirements.setdefault(a['requirement'], set()).add(a['verdict'])
        lines = []
        for a in assertions:
            if a['verdict'] == 'defect':
                top = a['contributions'][0] if a['contributions'] else None
                named = f"; largest swing {top['parameter']} ({describe(top['origin'])})" if top else ''
                lines.append(f"{a['id']} ({a['requirement']}) computed {span(a['computed'])} outside "
                             f"target {span(a['target'])}{named}")
            elif a['verdict'] == 'unknown':
                lines.append(f"{a['id']} ({a['requirement']}) unknown: {'; '.join(a['reasons'])}")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise CheckError('malformed', str(exc)) from exc
    ctx.result['details']['requirements'] = {rid: sorted(v) for rid, v in requirements.items()}
    ctx.result.update(execution='ok', outcome=aggregate,
                      summary=('all declared interval assertions hold over the worst case' if aggregate == 'pass'
                               else 'interval assertions: ' + ' | '.join(lines)))


def span(record):
    return f"[{record['min'] if record['min'] is not None else '-inf'}, " \
           f"{record['max'] if record['max'] is not None else '+inf'}] {record['unit']}"


def describe(origin):
    if origin['source'] == 'declared':
        return f"requirement {origin['requirement']}"
    label = origin.get('reference') or origin.get('part')
    return ', '.join(filter(None, (label, origin['lcsc'], origin['attribute'])))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root')
    parser.add_argument('assertions')
    parser.add_argument('--snapshot')
    parser.add_argument('--manifest-sha256')
    parser.add_argument('--parts-manifest')
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    params = {'assertions': args.assertions}
    if args.snapshot is not None:
        params.update(snapshot=args.snapshot, manifest_sha256=args.manifest_sha256)
    if args.parts_manifest is not None:
        params['parts_manifest'] = args.parts_manifest
    write_json(Path(args.out), evaluate_inputs(args.root, params))


if __name__ == '__main__':
    main()
