"""Manual source-bound physical intent compiler (AGENTEE-388 S1, opt-in, not wired at runtime).

A manually authored ``agent-ee.physical-intent/1`` document is compiled against one
sealed review run into the existing executable contracts: topology-rules/1 pin
bindings, electrical-locality/1 obligations and block-allocation/1 membership.
Terminal functions are joined to the run's own verified datasheet facts and
``pin_map`` comparisons, the native netlist and the native board pad UUIDs. The
compiler adds no fact, function, net role or authority; it refuses the whole
package on any contradiction and publishes a complete directory atomically or
nothing. Kelvin and current-loop obligations have no owned checker and stay
visible as unsupported. Runtime activation remains gated by KICADROUTE-91.
"""
import itertools
import json
import re
import shutil
import tempfile
from pathlib import Path

from fractions import Fraction

from . import blocks, electrical_locality, locality_provenance, topology_rules
from .baseline import load_baseline, verify_evidence
from .candidate import safe_relative, verify_snapshot
from .native_netlist import read_native_inventory
from .storage import digest, identity, json_bytes, read_json, rename_noreplace
from .symbol_library import LibraryError, children, parse_sexpr

SCHEMA = 'agent-ee.physical-intent/1'
RECEIPT_SCHEMA = 'agent-ee.physical-intent-receipt/1'
MODES = ('manual', 'disabled')
ACTIVATION = ('manual opt-in compiler only; no default, runtime, campaign or routing consumer. '
              'Runtime activation and deployment remain gated by KICADROUTE-91.')
MOVES = frozenset(('translate', 'rotate', 'flip'))
UNSUPPORTED_KINDS = frozenset(('kelvin', 'current_loop'))
DIAGNOSTIC_KINDS = frozenset(('access', 'cut'))
DIAGNOSTIC_STATUSES = frozenset(('unknown', 'observed'))
UUID = re.compile('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
SHA256 = re.compile('[0-9a-f]{64}')
# Stated lengths in a captured decision are read as complete lexemes: a maximal run of
# digits and grouping punctuation, then the word or mark that follows it. A word in the
# length vocabulary (any case, by its leading letters) makes the lexeme a stated length; only an unsigned plain
# decimal in exactly ``nm`` or ``mm`` resolves, every other stated length refuses.
NUMERAL = re.compile(r"\d[\d.,_\u00a0\u2009\u202f]*")
UNIT = re.compile(r'\s*([^\W\d_]\w*|["\'\u2032\u2033])')
LENGTH_WORD = re.compile(r'(?:[nuµμmcdk]?m|mils?|thou|in|inch(?:es)?|ft|foot|feet|microns?|'
                         r'(?:nano|micro|milli|centi|deci|kilo)?met(?:er|re)s?|["\'\u2032\u2033])')
DECIMAL = re.compile(r'(?:0|[1-9][0-9]*)(?:\.[0-9]+)?')
SIGNS = '+-\u2212\u2013\u00b1~<>\u2264\u2265'
NM_PER = {'nm': 1, 'mm': 1000000}
OWNER_AUTHORITY = ('owner and manufacturer authors cannot be authenticated in-process: agents run as the '
                   "owner's Unix user and no signing boundary exists until AGENTEE-348, so a sourced owner "
                   'maximum is published as authority_unavailable and never enforces')
CONTRACTS = {'topology': 'contracts/topology-rules.json', 'locality': 'contracts/electrical-locality.json',
             'allocation': 'contracts/block-allocation.json'}
LIMITATIONS = (
    'Terminal functions are joined only to verified pin_function facts under the run\'s own '
    'engineer-declared pin_map applicability; citation verification proves text occurrence, not '
    'row, conditions or engineering interpretation.',
    'Locality limits are pad-centre distances graded by electrical-locality/1; a nearest-pad '
    'distance is not electrical compliance and proves no current path, loop area or Kelvin '
    'connection.',
    'Kelvin and current-loop obligations have no owned routed-graph checker; they remain '
    'unsupported and prevent semantic completion when required.',
    'Access witnesses and cut inequalities are diagnostics only; an unknown access stays '
    'unknown and a cut inequality is not a routability or infeasibility proof.',
    'An owner-sourced maximum is bound to the identity and value its captured decision states, '
    'but it is authority_unavailable and advisory: ' + OWNER_AUTHORITY + '.',
    'Allowed moves are checked against native locked footprints only; no existing executable '
    'consumer reads them in this slice.',
    ACTIVATION,
)


class IntentRefused(ValueError):
    """The intent cannot compile; nothing is published."""


def _refuse(message):
    raise IntentRefused(message)


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        _refuse(f'{label} must be nonempty text')
    return value


def _keys(value, required, optional=(), label='entry'):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        _refuse(f'{label} needs {sorted(required)}' + (f', optional {sorted(optional)}' if optional else ''))
    return value


def _list(value, label, *, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        _refuse(f'{label} must be a {"nonempty " if nonempty else ""}list')
    return value


def _unique(items, label):
    seen = set()
    for item in items:
        if item in seen:
            _refuse(f'duplicate {label}: {item}')
        seen.add(item)


def validate_plan(intent):
    """Shape of the authored document; source binding happens in compile_plan."""
    _keys(intent, ('schema', 'mode', 'sources', 'terminals'),
          ('blocks', 'moves', 'locality', 'obligations', 'diagnostics'), 'physical intent')
    if intent['schema'] != SCHEMA:
        _refuse(f'expected {SCHEMA}')
    if intent['mode'] != 'manual':
        _refuse('physical intent document must declare mode manual')
    sources = _keys(intent['sources'], ('candidate_id', 'evidence_sha256', 'pin_map_check', 'board_sha256',
                                        'netlist_sha256', 'facts_sha256', 'pin_map_sha256'), ('records',),
                    'sources')
    for key in ('candidate_id', 'evidence_sha256', 'board_sha256', 'netlist_sha256', 'facts_sha256',
                'pin_map_sha256'):
        if not isinstance(sources[key], str) or not SHA256.fullmatch(sources[key]):
            _refuse(f'sources.{key} must be a lowercase SHA-256')
    _text(sources['pin_map_check'], 'sources.pin_map_check')
    records = sources.get('records', {})
    if not isinstance(records, dict) or any(not isinstance(v, str) or not SHA256.fullmatch(v)
                                            for v in records.values()):
        _refuse('sources.records must map captured input paths to SHA-256')
    for rel in records:
        if str(safe_relative(rel)) != rel:
            _refuse('sources.records paths must be normalized relative paths')
    terminals = _list(intent['terminals'], 'terminals', nonempty=True)
    for terminal in terminals:
        _keys(terminal, ('id', 'reference', 'pin', 'pad_uuid', 'function', 'fact', 'net', 'class',
                         'requirement'), (), 'terminal')
        for key in ('id', 'reference', 'pin', 'function', 'fact', 'net', 'requirement'):
            _text(terminal[key], f'terminal {key}')
        if not isinstance(terminal['pad_uuid'], str) or not UUID.fullmatch(terminal['pad_uuid']):
            _refuse('terminal pad_uuid must be a canonical captured UUID')
        if terminal['class'] not in topology_rules.CLASSES:
            _refuse(f'terminal class must be one of {sorted(topology_rules.CLASSES)}')
    _unique([t['id'] for t in terminals], 'terminal id')
    _unique([(t['reference'], t['pin']) for t in terminals], 'terminal pin')
    _unique([t['pad_uuid'] for t in terminals], 'terminal pad UUID')
    for block in _list(intent.get('blocks', []), 'blocks'):
        _keys(block, ('id', 'references'), ('anchor',), 'block')
        _text(block['id'], 'block id')
        for ref in _list(block['references'], 'block references', nonempty=True):
            _text(ref, 'block reference')
    for move in _list(intent.get('moves', []), 'moves'):
        _keys(move, ('reference', 'allow'), (), 'move')
        _text(move['reference'], 'move reference')
        allow = _list(move['allow'], 'move allow', nonempty=True)
        if any(a not in MOVES for a in allow) or len(set(allow)) != len(allow):
            _refuse(f'move allow must be distinct members of {sorted(MOVES)}')
    _unique([m['reference'] for m in intent.get('moves', [])], 'move reference')
    if intent.get('locality'):
        electrical_locality.validate({'schema': electrical_locality.SCHEMA, 'obligations': intent['locality']})
    for obligation in _list(intent.get('obligations', []), 'obligations'):
        _keys(obligation, ('id', 'kind', 'required', 'requirement', 'terminals', 'statement'),
              ('joins', 'forbidden_shared', 'proxy'), 'obligation')
        _text(obligation['id'], 'obligation id')
        _text(obligation['statement'], 'obligation statement')
        _text(obligation['requirement'], 'obligation requirement')
        if obligation['kind'] not in UNSUPPORTED_KINDS:
            _refuse(f'obligation kind must be one of {sorted(UNSUPPORTED_KINDS)}')
        if type(obligation['required']) is not bool:
            _refuse('obligation required must be boolean')
        for tid in _list(obligation['terminals'], 'obligation terminals', nonempty=True):
            _text(tid, 'obligation terminal')
        for pad in _list(obligation.get('joins', []), 'obligation joins'):
            if not isinstance(pad, str) or not UUID.fullmatch(pad):
                _refuse('obligation joins must be canonical captured pad UUIDs')
        for edge in _list(obligation.get('forbidden_shared', []), 'obligation forbidden_shared'):
            if not isinstance(edge, list) or len(edge) != 2 or any(not isinstance(t, str) for t in edge):
                _refuse('forbidden_shared entries must be terminal id pairs')
        if 'proxy' in obligation:
            _text(obligation['proxy'], 'obligation proxy')
    _unique([o['id'] for o in intent.get('obligations', [])], 'obligation id')
    for diagnostic in _list(intent.get('diagnostics', []), 'diagnostics'):
        _keys(diagnostic, ('id', 'kind', 'status', 'statement'), (), 'diagnostic')
        _text(diagnostic['id'], 'diagnostic id')
        _text(diagnostic['statement'], 'diagnostic statement')
        if diagnostic['kind'] not in DIAGNOSTIC_KINDS:
            _refuse(f'diagnostic kind must be one of {sorted(DIAGNOSTIC_KINDS)}')
        if diagnostic['status'] not in DIAGNOSTIC_STATUSES:
            _refuse('a diagnostic is unknown or observed; an access witness or cut inequality is not a proof')
    _unique([d['id'] for d in intent.get('diagnostics', [])], 'diagnostic id')
    # One receipt namespace: an assertion id never names two compiled or reported assertions.
    _unique([t['id'] for t in terminals] + [o['id'] for o in intent.get('locality') or []]
            + [f'block:{b["id"]}' for b in intent.get('blocks', [])]
            + [o['id'] for o in intent.get('obligations', [])] + [d['id'] for d in intent.get('diagnostics', [])],
            'assertion id')
    return intent


# ------------------------------------------------------------------ sources

def _run_sources(run, intent):
    """Sealed run evidence named by the intent; every bound hash must be current."""
    run = Path(run)
    try:
        manifest = verify_snapshot(run)
        baseline = load_baseline(run)
        if baseline is None:
            _refuse('compiler requires a run with an immutable baseline and sealed evidence')
        scope = manifest['scope']
        checks = [read_json(run / 'checks' / f"{spec['id']}.json") for spec in scope['checks']]
        verify_evidence(run, checks, required=True)
    except IntentRefused:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        _refuse(f'review run evidence is not intact: {exc}')
    sources = intent['sources']
    if manifest['candidate_id'] != sources['candidate_id']:
        _refuse('stale source: candidate identity differs from the intent')
    seal = run / 'work/verification/evidence.json'
    if digest(seal) != sources['evidence_sha256']:
        _refuse('stale source: evidence seal differs from the intent')
    results = {c['id']: c for c in checks}
    pin_spec = next((s for s in scope['checks'] if s['id'] == sources['pin_map_check']), None)
    if pin_spec is None or pin_spec['kind'] != 'pin_map':
        _refuse('sources.pin_map_check must name a pin_map check of the run')
    pin_result = results[pin_spec['id']]
    if (pin_result.get('execution'), pin_result.get('outcome')) != ('ok', 'pass'):
        _refuse('pin_map evidence is not a complete pass; no terminal function can bind')
    facts_id, netlist_id = pin_spec['params']['facts_check'], pin_spec['params']['netlist_check']
    paths = {'board': run / 'inputs' / str(safe_relative(scope['board'])),
             'netlist': run / 'work' / netlist_id / 'design.xml',
             'facts': run / 'work' / facts_id / 'facts.json',
             'pin_map': run / 'work' / pin_spec['id'] / 'pin-map.json'}
    for name, path in paths.items():
        if not path.is_file() or digest(path) != sources[f'{name}_sha256']:
            _refuse(f'stale source: {name} bytes differ from the intent')
    records = {}
    for rel, sha in sorted(sources.get('records', {}).items()):
        if manifest['files'].get(rel) != sha:
            _refuse(f'stale source: record {rel} is not the captured input the intent names')
        records[rel] = (run / 'inputs' / rel).read_text(encoding='utf-8')
    requirements = {r['id'] for r in baseline['requirements']}
    return {'manifest': manifest, 'paths': paths, 'records': records, 'requirements': requirements,
            'pin_map': read_json(paths['pin_map']), 'facts': read_json(paths['facts'])}


def _atoms(node):
    return [x for x in node[1:] if isinstance(x, str)]


def native_board(path):
    """Footprints by reference: lock state and pads (number, UUID, net) from the native board."""
    try:
        root = parse_sexpr(Path(path).read_bytes())
    except LibraryError as exc:
        _refuse(f'native board unreadable: {exc}')
    if root[0] != 'kicad_pcb':
        _refuse('native board is not a kicad_pcb')
    footprints = {}
    for fp in children(root, 'footprint'):
        refs = [_atoms(p)[1] for p in children(fp, 'property') if _atoms(p)[:1] == ['Reference']]
        if len(refs) != 1:
            _refuse('native footprint lacks exactly one Reference')
        if refs[0] in footprints:
            _refuse(f'native board duplicates reference {refs[0]}')
        locked = 'locked' in _atoms(fp) or any(_atoms(n) == ['yes'] for n in children(fp, 'locked'))
        pads = []
        for pad in children(fp, 'pad'):
            uuids, nets = children(pad, 'uuid'), children(pad, 'net')
            pads.append({'number': _atoms(pad)[0] if _atoms(pad) else '',
                         'uuid': _atoms(uuids[0])[0] if len(uuids) == 1 and _atoms(uuids[0]) else None,
                         'net': _atoms(nets[0])[-1] if len(nets) == 1 and _atoms(nets[0]) else ''})
        footprints[refs[0]] = {'locked': locked, 'pads': pads}
    return footprints


def _pad(board, reference, number, uuid, label):
    if reference not in board:
        _refuse(f'{label}: native board has no footprint {reference}')
    numbered = [p for p in board[reference]['pads'] if p['number'] == number]
    if len(numbered) != 1:
        _refuse(f'{label}: native {reference} has {len(numbered)} pads numbered {number}; '
                'duplicate or missing pad numbers cannot bind one terminal')
    owners = [(ref, p) for ref, fp in board.items() for p in fp['pads'] if p['uuid'] == uuid]
    if len(owners) != 1 or owners[0][1] is not numbered[0]:
        _refuse(f'{label}: stale pad UUID {uuid} is not native {reference} pad {number}')
    return numbered[0]


# ---------------------------------------------------------------- compilation

def _terminals(intent, src, board, native):
    """Join each terminal to a verified fact, its pin_map comparison and native identities."""
    verified = {r['id']: r['fact'] for r in src['facts'].get('facts', []) if r.get('status') == 'verified'}
    comparisons = src['pin_map'].get('comparisons', [])
    resolved, classes, rules = [], {}, []
    for t in intent['terminals']:
        label = f'terminal {t["id"]}'
        if t['requirement'] not in src['requirements']:
            _refuse(f'{label}: requirement {t["requirement"]!r} is not a baseline requirement')
        fact = verified.get(t['fact'])
        if fact is None or fact.get('kind') != 'pin_function':
            _refuse(f'{label}: fact {t["fact"]!r} is not a verified pin_function fact of the run')
        if fact['value'] != t['function']:
            _refuse(f'{label}: verified fact {t["fact"]} states {fact["value"]!r}, not {t["function"]!r}')
        matches = [c for c in comparisons if c.get('reference') == t['reference'] and c.get('fact_id') == t['fact']]
        if len(matches) != 1:
            _refuse(f'{label}: pin_map applicability does not bind fact {t["fact"]} to {t["reference"]}')
        comparison = matches[0]
        if comparison['pin'] != t['pin'] or fact['pin'] != t['pin']:
            _refuse(f'{label}: verified {t["function"]} fact binds {t["reference"]} pin {fact["pin"]}, '
                    f'not proposed pin {t["pin"]}')
        if comparison.get('matches') is not True or comparison.get('native_name') != t['function']:
            _refuse(f'{label}: native symbol pin name does not match the verified fact')
        pad = _pad(board, t['reference'], t['pin'], t['pad_uuid'], label)
        component = native['components'].get(t['reference'])
        if component is None or t['pin'] not in component['pins']:
            _refuse(f'{label}: native netlist has no {t["reference"]} pin {t["pin"]}')
        nid = native['memberships'].get((t['reference'], t['pin']))
        native_net = native['nets'][nid]['name'] if nid is not None else None
        if pad['net'] != native_net:
            _refuse(f'{label}: native board pad net {pad["net"]!r} differs from netlist net {native_net!r}')
        declaration = classes.setdefault(component['lib_id'], {'class': t['class'], 'terminals': {}})
        if declaration['class'] != t['class']:
            _refuse(f'{label}: conflicting class for symbol {component["lib_id"]}')
        if declaration['terminals'].setdefault(t['function'], t['pin']) != t['pin']:
            _refuse(f'{label}: symbol {component["lib_id"]} binds {t["function"]} to two pins')
        rules.append({'id': t['id'], 'requirement': t['requirement'], 'type': 'pin_net',
                      'terminal': {'reference': t['reference'], 'pin': t['pin']}, 'net': t['net']})
        resolved.append({'id': t['id'], 'reference': t['reference'], 'pin': t['pin'], 'pad_uuid': t['pad_uuid'],
                         'function': t['function'], 'net': native_net, 'fact_id': t['fact'],
                         'part_number': fact['part_number'], 'native_name': comparison['native_name'],
                         'fact_source': {k: fact['source'][k] for k in ('path', 'sha256', 'page', 'locator')}})
    document = {'schema': topology_rules.SCHEMA, 'classes': classes, 'rules': rules}
    try:
        topology_rules.validate_rules(document)
    except ValueError as exc:
        _refuse(f'topology contract invalid: {exc}')
    report = topology_rules.evaluate(document, native)
    if report['outcome'] != 'pass':
        failed = [(r['id'], r['status'], r.get('reasons', r.get('actual_net', {}).get('name')))
                  for r in report['records'] if r['status'] != 'satisfied']
        _refuse(f'failed topology against native netlist: {failed}')
    return resolved, document


def _stated_lengths(text, locator, label):
    """Every length stated in decision text as (decimal, unit); refuse any it cannot resolve whole.

    A signed, ranged, grouped, digit-separated, exponent or non-ASCII numeral, or a length unit
    other than exactly ``nm`` or ``mm``, is unresolvable: no positive suffix is extracted and
    no stated length is dropped.
    """
    lengths = []
    for numeral in NUMERAL.finditer(text):
        unit = UNIT.match(text, numeral.end())
        if not unit:
            continue
        word = unit[1] if not unit[1][0].isalpha() else ''.join(itertools.takewhile(str.isalpha, unit[1]))
        if not LENGTH_WORD.fullmatch(word.casefold()):
            continue
        number, before = numeral[0], text[:numeral.start()]
        lexeme = re.search(r'\S*$', before)[0] + text[numeral.start():unit.end()]
        if unit[1] not in NM_PER:
            _refuse(f'{label}: numeric mismatch: decision {locator} states a length {lexeme!r} '
                    'in a unit that cannot be resolved to nm')
        prior = before.rstrip()[-1:]
        if (not DECIMAL.fullmatch(number) or before[-1:].isalnum() or before[-1:] in tuple('._')
                or prior and (prior in SIGNS or prior.isdigit())
                or prior in tuple(',.') and before.rstrip()[-2:-1].isdigit()):
            _refuse(f'{label}: numeric mismatch: decision {locator} states an unresolvable length {lexeme!r}: '
                    'only an unsigned plain decimal resolves')
        lengths.append((number, unit[1]))
    return lengths


def _owner_decision(pair, records, label):
    """The cited captured decision must state this owner and exactly this maximum.

    A decision is one record line ``<locator> (<owner identity>): <text with the quote>``.
    The statement locates the decision; it does not authenticate its author.
    """
    decision = pair['provenance']['decision_reference']
    head = re.compile(rf'\s*{re.escape(decision["locator"])}\s*\(([^()]+)\)\s*:')
    stated = [(m[1].strip(), line[m.end():]) for line in records[decision['path']].splitlines()
              if decision['quote'] in line and (m := head.match(line))]
    if len(stated) != 1:
        _refuse(f'{label}: owner authority not established: the captured record states no single decision '
                f'{decision["locator"]} with an owner identity and the quote')
    owner, text = stated[0]
    if decision['owner'] != owner:
        _refuse(f'{label}: owner authority not established: decision {decision["locator"]} is stated by '
                f'{owner!r}, not {decision["owner"]!r}')
    values = {Fraction(n) * NM_PER[unit] for n, unit in _stated_lengths(text, decision['locator'], label)}
    if len(values) != 1 or next(iter(values)).denominator != 1:
        _refuse(f'{label}: numeric mismatch: decision {decision["locator"]} states no single resolvable maximum')
    value = int(next(iter(values)))
    if 'max_distance_nm' not in pair:
        _refuse(f'{label}: numeric mismatch: owner decision states {value} nm but no max_distance_nm is declared')
    if pair['max_distance_nm'] != value:
        _refuse(f'{label}: numeric mismatch: decision {decision["locator"]} states {value} nm, '
                f'not declared max_distance_nm {pair["max_distance_nm"]}')
    return owner, value


def _locality(intent, src, board):
    authored = intent.get('locality', [])
    if not authored:
        return None, []
    document = {'schema': electrical_locality.SCHEMA, 'obligations': authored}
    if src['records']:
        document['source_records'] = src['records']
    electrical_locality.validate(document)
    obligations = json.loads(json_bytes(authored))
    qualification = []
    for obligation in obligations:
        for role in electrical_locality.roles(obligation):
            pair = obligation[role]
            label = f'locality {obligation["id"]}.{role}'
            for end in ('from', 'to'):
                endpoint = pair[end]
                if 'uuid' not in endpoint:
                    _refuse(f'{label}: endpoint needs the captured native pad uuid')
                pad = _pad(board, endpoint['reference'], endpoint['pad'], endpoint['uuid'], label)
                if pad['net'] != endpoint['net']:
                    _refuse(f'{label}: native pad net {pad["net"]!r} differs from declared {endpoint["net"]!r}')
            authority = locality_provenance.classify(pair, src['records'])
            declared = pair.get('provenance', {}).get('class', 'default')
            if authority['enforcement'] == 'integrity':
                _refuse(f'{label}: stale source record: {authority["reason"]}')
            if declared == 'owner' and authority['enforcement'] != 'hard':
                _refuse(f'{label}: owner authority not established by a captured, verified owner decision')
            if declared == 'datasheet' and authority['enforcement'] != 'hard':
                _refuse(f'{label}: numeric mismatch: sourced derivation does not equal declared max_distance_nm')
            entry = {'obligation': obligation['id'], 'role': role, 'class': authority['class'],
                     'enforcement': authority['enforcement'], 'reason': authority['reason']}
            if declared == 'owner':
                owner, value = _owner_decision(pair, src['records'], label)
                # No in-process owner authentication: the cited decision is retained but moved
                # out of the decision_reference slot locality_provenance reads as authority.
                provenance = {k: v for k, v in pair['provenance'].items() if k != 'decision_reference'}
                pair['provenance'] = {**provenance, 'authority': 'authority_unavailable',
                                      'authority_reason': OWNER_AUTHORITY,
                                      'unauthenticated_decision_reference': pair['provenance']['decision_reference']}
                entry.update(enforcement='authority_unavailable', reason=OWNER_AUTHORITY,
                             owner=owner, stated_max_distance_nm=value)
            if (locality_provenance.classify(pair, src['records'])['enforcement'] == 'hard'
                    and pair['provenance']['class'] != 'datasheet'):
                _refuse(f'{label}: only a verified datasheet derivation may compile to a hard maximum')
            qualification.append(entry)
    document['obligations'] = obligations
    electrical_locality.validate(document)
    return document, qualification


def _allocation(intent, board):
    entries = intent.get('blocks', [])
    if not entries:
        return None
    owner = {}
    for block in entries:
        for ref in block['references']:
            if ref not in board:
                _refuse(f'block {block["id"]}: native board has no footprint {ref}')
            if ref in owner:
                _refuse(f'conflicting membership: {ref} is in blocks {owner[ref]} and {block["id"]}')
            owner[ref] = block['id']
        if 'anchor' in block and block['anchor'] not in block['references']:
            _refuse(f'block {block["id"]}: anchor must be a member')
    _unique([b['id'] for b in entries], 'block id')
    document = {'schema': blocks.ALLOCATION, 'blocks': entries}
    try:
        blocks._allocation(json_bytes(document))
    except ValueError as exc:
        _refuse(f'block allocation invalid: {exc}')
    return document


def _moves(intent, board):
    for move in intent.get('moves', []):
        if move['reference'] not in board:
            _refuse(f'move: native board has no footprint {move["reference"]}')
        if board[move['reference']]['locked']:
            _refuse(f'conflicting fixed move: native {move["reference"]} is locked')
    return sorted(({'reference': m['reference'], 'allow': sorted(m['allow'])} for m in intent.get('moves', [])),
                  key=lambda m: m['reference'])


def _unsupported(intent, src, board, terminals, locality):
    by_id = {t['id']: t for t in terminals}
    pads = {p['uuid'] for fp in board.values() for p in fp['pads']}
    proxies = {o['id'] for o in (locality or {}).get('obligations', [])}
    out = []
    for obligation in intent.get('obligations', []):
        label = f'obligation {obligation["id"]}'
        if obligation['requirement'] not in src['requirements']:
            _refuse(f'{label}: requirement {obligation["requirement"]!r} is not a baseline requirement')
        missing = [t for t in obligation['terminals'] + [x for e in obligation.get('forbidden_shared', []) for x in e]
                   if t not in by_id]
        if missing:
            _refuse(f'{label}: unresolved terminals {sorted(set(missing))}')
        stale = [p for p in obligation.get('joins', []) if p not in pads]
        if stale:
            _refuse(f'{label}: stale join pad UUIDs {stale}')
        if 'proxy' in obligation and obligation['proxy'] not in proxies:
            _refuse(f'{label}: proxy names no compiled locality obligation')
        record = {**obligation, 'status': 'unsupported',
                  'reason': f'no owned routed-graph predicate enforces {obligation["kind"]} obligations'}
        if 'proxy' in obligation:
            record['proxy_note'] = 'a pad-centre locality proxy is not electrical compliance'
        out.append(record)
    return out


def compile_plan(run, intent, *, mode='manual'):
    """Pure compilation; returns {relative path: JSON value} or None when disabled. Writes nothing."""
    if mode not in MODES:
        _refuse(f'physical intent mode must be one of {list(MODES)}')
    if mode == 'disabled':
        return None
    try:
        intent = json.loads(json_bytes(intent))  # a private snapshot; artifacts never alias the caller
    except (TypeError, ValueError) as exc:
        _refuse(f'physical intent must be plain JSON: {exc}')
    validate_plan(intent)
    src = _run_sources(run, intent)
    board = native_board(src['paths']['board'])
    try:
        native = read_native_inventory(src['paths']['netlist'])
    except (OSError, ValueError) as exc:
        _refuse(f'native netlist unreadable: {exc}')
    terminals, topology = _terminals(intent, src, board, native)
    locality, qualification = _locality(intent, src, board)
    allocation = _allocation(intent, board)
    moves = _moves(intent, board)
    unsupported = _unsupported(intent, src, board, terminals, locality)
    artifacts = {CONTRACTS['topology']: topology}
    bound = [(t['id'], f'/{CONTRACTS["topology"]}#/rules/{i}') for i, t in enumerate(terminals)]
    if locality:
        artifacts[CONTRACTS['locality']] = locality
        bound += [(o['id'], f'/{CONTRACTS["locality"]}#/obligations/{i}')
                  for i, o in enumerate(locality['obligations'])]
    if allocation:
        artifacts[CONTRACTS['allocation']] = allocation
        bound += [(f'block:{b["id"]}', f'/{CONTRACTS["allocation"]}#/blocks/{i}')
                  for i, b in enumerate(allocation['blocks'])]
    pointers = {}
    for key, pointer in bound:
        if key in pointers:
            _refuse(f'assertion id {key} names two compiled assertions')
        pointers[key] = [pointer]
    required_open = sorted(o['id'] for o in unsupported if o['required'])
    receipt = {
        'schema': RECEIPT_SCHEMA, 'mode': 'manual', 'activation': ACTIVATION,
        'compiler_sha256': digest(__file__), 'intent_sha256': identity(intent),
        'sources': {**{k: v for k, v in intent['sources'].items() if k != 'records'},
                    'records': dict(sorted(intent['sources'].get('records', {}).items()))},
        'artifacts': {rel: identity(value) for rel, value in sorted(artifacts.items())},
        'assertions': dict(sorted(pointers.items())), 'terminals': terminals,
        'locality_qualification': qualification, 'allowed_moves': moves,
        'unsupported': unsupported,
        'diagnostics': [{**d, 'authority': 'diagnostic'} for d in intent.get('diagnostics', [])],
        'semantic_completion': 'unsupported' if required_open else 'supported-subset',
        'required_unsupported': required_open, 'limitations': list(LIMITATIONS)}
    receipt['package_id'] = identity(receipt)
    return {**artifacts, 'receipt.json': receipt}


def publish(compiled, out):
    """Write the complete package to a staging sibling, then rename it into place; never replace."""
    out = Path(out)
    if out.exists() or out.is_symlink():
        _refuse(f'compiled package destination exists: {out}')
    staging = Path(tempfile.mkdtemp(prefix=f'.{out.name}.', dir=out.parent))
    try:
        for rel, value in compiled.items():
            path = staging / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(json_bytes(value))
        rename_noreplace(staging, out)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return out


def compile_and_publish(run, intent, out, *, mode='manual'):
    """Compile completely, then publish atomically; a refusal publishes nothing."""
    compiled = compile_plan(run, intent, mode=mode)
    return None if compiled is None else publish(compiled, out)


def verify_receipt(package, run, intent):
    """Recompile from current sources; the package must equal it byte for byte."""
    package = Path(package)
    expected = compile_plan(run, intent)
    present = sorted(p.relative_to(package).as_posix() for p in package.rglob('*')
                     if p.is_file() or p.is_symlink())
    if present != sorted(expected):
        _refuse('compiled package files differ from recompilation')
    for rel, value in expected.items():
        path = package / rel
        if path.is_symlink() or path.read_bytes() != json_bytes(value):
            _refuse(f'compiled package {rel} differs from recompilation')
    return expected['receipt.json']
