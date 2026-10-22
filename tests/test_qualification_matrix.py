"""AGENTEE-188 qualification mutation matrix: every case against every consumer.

Consumers: the validator (verify_contract), the capability, coverage (compile_coverage) and the
compiler (compile_io_map given the bundle's CSV, history and captured bytes; it accepts a bundle only
if it would emit exactly that contract, modulo invariant order, and exactly those record bytes).
Each case states the expected verdict per consumer; refusal cases must refuse in all four. Only a
ValueError is a refusal: any other exception is a crash and fails the case.

`python3 -m tests.test_qualification_matrix` prints the per-consumer verdict table and runtime. The
module also runs against older revisions (it adapts to their validator signature) so the table shows
which cases they accepted.
"""
import copy
import hashlib
import inspect
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from agent_ee import hardware_interface as hi
from agent_ee import io_map
from agent_ee.capabilities import compile_coverage
from agent_ee.storage import digest, json_bytes
from tests import test_io_map as io_tests
from tests.test_io_map import SOURCE, probe_bindings
from tests.toolchain import require_toolchain

CONSUMERS = ('validator', 'capability', 'coverage', 'compiler')


class _Missing:
    """Absent (no key, no bytes); survives deepcopy so `is MISSING` stays meaningful."""

    def __deepcopy__(self, memo):
        return self

    def __repr__(self):
        return 'MISSING'


MISSING = _Missing()
CSV_PATH, RECORD_PATH = 'io-map.csv', 'qualification.json'
ORIGIN_BODY = b'captured historical source\n'
OTHER_BODY = b'accidentally different source\n'
ITEM4 = b'owner item 4\n'
NEW_API = hasattr(hi, 'verify_contract')
COMPILE_CAPTURED = 'captured' in inspect.signature(io_map.compile_io_map).parameters
# The shared fixture constants, not the TestCase class itself (which unittest would collect again here).
RAW = io_tests.HistoricalQualificationTests.RAW
HISTORY = io_tests.HistoricalQualificationTests.HISTORY
SCOPE = io_tests.HistoricalQualificationTests.SCOPE


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Bundle:
    """Everything a consumer can be handed: the captured files, the scope pin and the compiler inputs."""

    def __init__(self, *, contract, record, history, compile_kwargs=None, extra=None, legacy=False):
        self.csv = RAW
        self.contract, self.record, self.history = contract, record, history
        self.compile_kwargs = compile_kwargs or {}
        self.pin = copy.deepcopy(contract['qualification_record']) if 'qualification_record' in contract else MISSING
        self.extra = dict(extra or {})
        self.uncaptured, self.deleted = set(), set()
        self.legacy = legacy

    def copy(self):
        return copy.deepcopy(self)

    def record_value(self):
        return json.loads(self.record)

    def set_record(self, value, *, repin=True, raw=None):
        self.record = raw if raw is not None else json_bytes(value)
        if repin:
            self.repin()

    def repin(self):
        """Re-declare the pin in contract and scope, so the schema rather than the digest must refuse."""
        self.contract['qualification_record']['sha256'] = sha(self.record)
        self.pin = copy.deepcopy(self.contract['qualification_record'])

    def captured(self):
        files = {CSV_PATH: self.csv, **self.extra}
        return {k: v for k, v in files.items() if k not in self.uncaptured and k not in self.deleted}

    def captured_record(self):
        """The record bytes a consumer can obtain from the capture (None when omitted or not captured)."""
        return None if RECORD_PATH in self.uncaptured or RECORD_PATH in self.deleted else self.record


def history(**origin):
    h = copy.deepcopy(HISTORY)
    h['origin'].update(origin)
    return h


def compile_bundle(hist, extra=None):
    extra = dict(extra or {})
    kwargs = dict(bindings=probe_bindings(), history=hist, record_path=RECORD_PATH, report={})
    if COMPILE_CAPTURED:
        kwargs['captured'] = {k: v for k, v in extra.items() if k != hist['origin']['path']} or None
    if hist['origin']['path'] in extra:
        kwargs['origin_bytes'] = extra[hist['origin']['path']]
    contract = io_map.compile_io_map(RAW, csv_path=CSV_PATH, requirement='interface', source=SOURCE, **kwargs)
    record = kwargs['report'].get('qualification_record') or json_bytes(io_map.build_record(contract, hist))
    return Bundle(contract=contract, record=record, history=hist, extra=extra)


def legacy_bundle(**compile_kwargs):
    contract = io_map.compile_io_map(RAW, csv_path=CSV_PATH, requirement='interface', source=SOURCE,
                                     bindings=probe_bindings(), **compile_kwargs)
    return Bundle(contract=contract, record=None, history=None, compile_kwargs=compile_kwargs, legacy=True)


SOURCED = dict(path='item4.md', sha256=sha(ITEM4), locator='item 4')
BASES = {
    'bound': lambda: compile_bundle(history()),
    'bound-verified': lambda: compile_bundle(history(path='origin.csv', sha256=sha(ORIGIN_BODY)),
                                             {'origin.csv': ORIGIN_BODY}),
    'bound-mixed': lambda: compile_bundle(dict(history(), sourced={'POD-IF-A': SOURCED}), {'item4.md': ITEM4}),
    'legacy': lambda: legacy_bundle(),
    'legacy-explicit': lambda: legacy_bundle(qualifications={}),
}
_CACHE = {}


def base(name):
    if name not in _CACHE:
        _CACHE[name] = BASES[name]()
    return _CACHE[name].copy()


# ---- consumers -------------------------------------------------------------------------------------

def authority_label(authority, history):
    history = history or {}
    return {'bound': bool(history.get('qualification_bound')), 'sourced': list(authority['sourced']),
            'unqualified': list(authority['unqualified-carryover']),
            'origins': dict(history.get('origin_verification') or {})}


def run_validator(b):
    contract = copy.deepcopy(b.contract)
    if NEW_API:
        contract, report = hi.verify_contract(contract, qualification=b.captured_record(), captured=b.captured())
        return authority_label(hi.requirement_authority(contract), report)
    hi.validate_contract(contract, qualification=b.captured_record())
    return None


def capture_dir(b, tmp):
    root = Path(tmp)
    (root / CSV_PATH).write_bytes(b.csv)
    (root / 'requirements.md').write_bytes(b'Structural interface\n')
    (root / 'contract.json').write_bytes(json_bytes(b.contract))
    if b.record is not None:
        (root / RECORD_PATH).write_bytes(b.record)
    for name, data in b.extra.items():
        (root / name).write_bytes(data)
    for name in ('interface.kicad_sch', 'interface.kicad_pcb'):
        (root / name).write_bytes(b'synthetic inventory placeholder')
    files = {p.name: digest(p) for p in root.iterdir() if p.name not in b.uncaptured}
    for name in b.deleted:
        (root / name).unlink()
    params = dict(contract='contract.json', netlist_check='netlist')
    if b.pin is not MISSING:
        params['qualification_record'] = copy.deepcopy(b.pin)
    scope = dict(copy.deepcopy(SCOPE), schematic='interface.kicad_sch', board='interface.kicad_pcb')
    scope['checks'] = [dict(id='netlist', kind='netlist'), dict(id='interface', kind='hardware_interface',
                                                                params=params)]
    scope['baseline']['requirements'][0]['checks'] = ['netlist', 'interface']
    return scope, files, root


def run_capability(b):
    with tempfile.TemporaryDirectory() as tmp:
        scope, files, root = capture_dir(b, tmp)
        d = hi.capability(scope['checks'][1]['params'], scope, files, root, 'interface')
        return authority_label(d['requirement_authority'], d['requirement_history'])


def run_coverage(b):
    with tempfile.TemporaryDirectory() as tmp:
        scope, files, root = capture_dir(b, tmp)
        check = compile_coverage(scope, files, root)['checks']['interface']
        q = check.get('requirement_qualification', {'sourced': [], 'unqualified-carryover': []})
        return authority_label(q, q)


def canonical(contract):
    value = copy.deepcopy(contract)
    if isinstance(value, dict) and isinstance(value.get('invariants'), list):
        value['invariants'] = sorted(value['invariants'], key=lambda i: json.dumps(i, sort_keys=True))
    return json_bytes(value)


def run_compiler(b):
    raw = b.captured().get(CSV_PATH)
    if raw is None:
        raise ValueError('compiler: io_map CSV is not captured')
    if b.legacy:
        if b.captured_record() is not None or b.pin is not MISSING:
            raise ValueError('compiler: legacy compilation declares no qualification record')
        contract = io_map.compile_io_map(raw, csv_path=CSV_PATH, requirement='interface', source=SOURCE,
                                         bindings=probe_bindings(), **b.compile_kwargs)
        record, report = None, None
    else:
        hist = b.history
        report = {}
        kwargs = dict(bindings=probe_bindings(), history=copy.deepcopy(hist), record_path=RECORD_PATH, report=report)
        captured = {k: v for k, v in b.captured().items() if k != CSV_PATH}
        origin = hist['origin']['path'] if isinstance(hist, dict) and isinstance(hist.get('origin'), dict) else None
        if origin in captured:
            kwargs['origin_bytes'] = captured.pop(origin)
        if COMPILE_CAPTURED:
            kwargs['captured'] = captured or None
        contract = io_map.compile_io_map(raw, csv_path=CSV_PATH, requirement='interface', source=SOURCE,
                                         **kwargs)
        record = report.get('qualification_record') or json_bytes(io_map.build_record(contract, hist))
    if canonical(contract) != canonical(b.contract) or record != b.captured_record():
        raise ValueError('compiler would not emit this contract and record from the captured inputs')
    if report is None:
        return authority_label(hi.requirement_authority(contract), None)
    return authority_label(hi.requirement_authority(contract), report.get('qualification')) if NEW_API else None


RUNNERS = dict(validator=run_validator, capability=run_capability, coverage=run_coverage, compiler=run_compiler)


def verdict(consumer, bundle):
    try:
        label = RUNNERS[consumer](bundle)
    except ValueError as exc:
        return 'refuse', str(exc)
    except Exception as exc:  # a crash is never a refusal
        return 'crash', f'{type(exc).__name__}: {exc}'
    return 'accept', label


# ---- cases -----------------------------------------------------------------------------------------

CASES = []


def case(name, build, expect='refuse', label=None, na=(), probe=None):
    """expect: 'refuse' in every consumer, or 'accept' with the agreed label; `na` consumers do not apply."""
    CASES.append(dict(name=name, build=build, expect={c: ('n/a' if c in na else expect) for c in CONSUMERS},
                      label=label, probe=probe))


def at(value, path):
    for key in path:
        value = value[key]
    return value


def mutate_record(base_name, path, op, repin=True):
    def build():
        b = base(base_name)
        value = b.record_value()
        if not path:
            b.set_record(None, repin=repin, raw=op)
            return b
        parent, key = at(value, path[:-1]), path[-1]
        if op is MISSING:
            del parent[key]
        else:
            parent[key] = copy.deepcopy(op)
        b.set_record(value, repin=repin)
        return b
    return build


def mutate_contract(base_name, fn, *, record_fn=None, repin=False):
    def build():
        b = base(base_name)
        fn(b.contract)
        if record_fn is not None:
            value = b.record_value()
            record_fn(value)
            b.set_record(value, repin=repin)
        elif repin:
            b.repin()
        return b
    return build


def each_citation(fn):
    def apply(contract):
        for invariant in contract['invariants']:
            fn(invariant)
    return apply


def each_row(fn):
    def apply(record):
        for row in record['rows']:
            fn(row)
    return apply


def bundle_with(base_name, fn):
    def build():
        b = base(base_name)
        fn(b)
        return b
    return build


UNQ = ['POD-IF-A', 'POD-IF-B']
ok_bound = dict(bound=True, sourced=[], unqualified=UNQ, origins={'v9/io-map.csv': 'declared-unverified'})
ok_verified = dict(bound=True, sourced=[], unqualified=UNQ, origins={'origin.csv': 'verified'})
ok_mixed = dict(bound=True, sourced=['POD-IF-A'], unqualified=['POD-IF-B'],
                origins={'v9/io-map.csv': 'declared-unverified'})
ok_legacy = dict(bound=False, sourced=[], unqualified=UNQ, origins={})
ok_legacy_empty = dict(bound=False, sourced=[], unqualified=[], origins={})

# positives
case('positive/correct-record', lambda: base('bound'), 'accept', ok_bound)
case('positive/all-matching-origins-verified', lambda: base('bound-verified'), 'accept', ok_verified)
case('positive/mixed-sourced-captured', lambda: base('bound-mixed'), 'accept', ok_mixed)
case('positive/uncaptured-origin-control', bundle_with('bound-verified', lambda b: b.uncaptured.add('origin.csv')),
     'accept', dict(ok_verified, origins={'origin.csv': 'declared-unverified'}))
case('positive/legacy-bare-ids', lambda: base('legacy'), 'accept', ok_legacy)
case('positive/legacy-explicit', lambda: base('legacy-explicit'), 'accept', ok_legacy)
case('positive/invariant-order-independent', mutate_contract('bound', lambda c: c['invariants'].reverse()),
     'accept', ok_bound, probe='r1 probe.py invariant-order-independent; r2 bound_probe.py')

# the record as a whole
for label, raw in (('null', b'null'), ('null-newline', b'null\n'), ('empty-bytes', b''), ('empty-object', b'{}'),
                   ('empty-list', b'[]'), ('wrong-type-string', b'"record"'), ('wrong-type-number', b'1'),
                   ('wrong-type-true', b'true'), ('not-json', b'{not json'), ('non-utf8', b'\xff{}'),
                   ('duplicate-key', b'{"schema": 1, "schema": 2}'), ('nan', b'NaN')):
    case(f'record/{label}/repinned', mutate_record('bound', (), raw),
         probe='r3 null_probe.py original' if label == 'null' else None)


def _null_emptied():
    b = base('bound')
    for invariant in b.contract['invariants']:
        invariant['io_map']['requirements'] = []
    b.set_record(None, raw=b'null')
    return b


case('record/null/emptied-citations', _null_emptied, probe='r3 null_probe.py empty-citations')
case('record/omitted-pin-unchanged', bundle_with('bound', lambda b: setattr(b, 'record', None)),
     probe='r2 record_pin_probe.py bound-record-omitted')


def _omitted_emptied(b):
    b.record = None
    for invariant in b.contract['invariants']:
        invariant['io_map']['requirements'] = []


case('record/omitted-emptied-citations', bundle_with('bound', _omitted_emptied),
     probe='r2 record_pin_probe.py bound-record-omitted')
case('record/deleted-from-capture', bundle_with('bound', lambda b: b.deleted.add(RECORD_PATH)),
     probe='r2 bound_probe.py record-missing')
case('record/not-in-manifest', bundle_with('bound', lambda b: b.uncaptured.add(RECORD_PATH)),
     probe='r2 bound_probe.py record-not-captured')
case('record/substituted-pin-unchanged', bundle_with('bound', lambda b: setattr(
    b, 'record', base('bound-verified').record)))
case('record/changed-revision-pin-unchanged', mutate_record('bound', ('rows', 0, 'origin', 'revision'),
                                                            'accidentally wrong revision', repin=False),
     probe='r2 record_pin_probe.py origin-changed')
for field in ('path', 'revision', 'sha256', 'locator'):
    case(f'record/missing-origin-{field}/pin-unchanged', mutate_record(
        'bound', ('rows', 0, 'origin', field), MISSING, repin=False), probe=f'r2 bound_probe.py missing-origin-{field}')

# every record field: null, empty, wrong type, missing key (re-pinned so the schema must refuse)
STR, INT, LIST, DICT = '', 0, [], {}
FIELDS = [
    ('bound', ('schema',), STR), ('bound', ('io_map',), DICT), ('bound', ('io_map', 'path'), STR),
    ('bound', ('io_map', 'sha256'), STR), ('bound', ('rows',), LIST), ('bound', ('rows', 0), DICT),
    ('bound', ('rows', 0, 'row'), INT), ('bound', ('rows', 0, 'connector_pin'), STR),
    ('bound', ('rows', 0, 'signal'), STR), ('bound', ('rows', 0, 'external_destination'), STR),
    ('bound', ('rows', 0, 'requirements'), LIST), ('bound', ('rows', 0, 'requirements', 0), DICT),
    ('bound', ('rows', 0, 'requirements', 0, 'id'), STR),
    ('bound', ('rows', 0, 'requirements', 0, 'qualification'), STR),
    ('bound', ('rows', 0, 'origin'), DICT), ('bound', ('rows', 0, 'origin', 'path'), STR),
    ('bound', ('rows', 0, 'origin', 'revision'), STR), ('bound', ('rows', 0, 'origin', 'sha256'), STR),
    ('bound', ('rows', 0, 'origin', 'locator'), STR),
    ('bound-mixed', ('rows', 0, 'requirements', 0, 'source'), DICT),
    ('bound-mixed', ('rows', 0, 'requirements', 0, 'source', 'path'), STR),
    ('bound-mixed', ('rows', 0, 'requirements', 0, 'source', 'sha256'), STR),
    ('bound-mixed', ('rows', 0, 'requirements', 0, 'source', 'locator'), STR),
]
WRONG = {type(STR): 7, type(INT): '2', type(LIST): {'x': 1}, type(DICT): ['x']}
for base_name, path, empty in FIELDS:
    dotted = '.'.join(str(p) for p in path)
    for label, op in (('null', None), ('empty', empty), ('wrong-type', WRONG[type(empty)]), ('missing', MISSING)):
        case(f'field/{base_name}/{dotted}/{label}', mutate_record(base_name, path, op))

# contract declaration and scope pin
for label, op in (('null', None), ('empty', {}), ('wrong-type', 'qualification.json'),
                  ('missing-sha256', dict(path=RECORD_PATH)), ('sha256-null', dict(path=RECORD_PATH, sha256=None)),
                  ('path-null', dict(path=None, sha256='a' * 64))):
    case(f'declaration/{label}', mutate_contract('bound', lambda c, op=op: c.update(qualification_record=op)))
case('declaration/stripped-under-pinned-scope', mutate_contract('bound', lambda c: c.pop('qualification_record')),
     probe='r2 bound_probe.py legacy-standin')
# The scope pin is an input of the capability and coverage only; the validator and compiler have no scope.
for label, op in (('null', None), ('empty', {}), ('wrong-type', 'qualification.json'), ('missing', MISSING),
                  ('other-digest', dict(path=RECORD_PATH, sha256='b' * 64))):
    case(f'scope-pin/{label}', bundle_with('bound', lambda b, op=op: setattr(b, 'pin', copy.deepcopy(op))),
         na=('validator', 'compiler'))

# contract citations changed, record and CSV unchanged
csv_sha = sha(RAW)
UPGRADE_CSV = dict(path=CSV_PATH, sha256=csv_sha, locator='inherited Notes token')
CITATION_MODES = {
    'bare-ids': lambda i: i['io_map'].update(requirements=[r['id'] if isinstance(r, dict) else r
                                                           for r in i['io_map']['requirements']]),
    'empty-citations': lambda i: i['io_map'].update(requirements=[]),
    'strip-io-map': lambda i: i.pop('io_map'),
    'upgrade-from-csv': lambda i: i['io_map'].update(requirements=[
        dict(id=r['id'], qualification='sourced', source=dict(UPGRADE_CSV)) for r in i['io_map']['requirements']]),
    'changed-description': lambda i: i['io_map'].update(external_destination='accidental current-authority description'),
    'changed-signal': lambda i: i['io_map'].update(signal='CHANGED'),
}
for mode, fn in CITATION_MODES.items():
    case(f'contract/{mode}', mutate_contract('bound', each_citation(fn)),
         probe=f'r1 probe.py {mode}; r2 bound_probe.py {mode}' if mode != 'changed-signal' else 'r2 bound_probe.py changed-signal')
case('contract/strip-one-io-map', mutate_contract('bound', lambda c: c['invariants'][0].pop('io_map')))
case('contract/remove-one-id', mutate_contract('bound', lambda c: c['invariants'][0]['io_map'].update(
    requirements=c['invariants'][0]['io_map']['requirements'][1:])))
case('contract/add-id', mutate_contract('bound', lambda c: c['invariants'][0]['io_map']['requirements'].append(
    dict(id='POD-IF-Z', qualification='unqualified-carryover'))))
case('contract/upgrade-to-captured-source', mutate_contract('bound-mixed', each_citation(
    lambda i: i['io_map'].update(requirements=[dict(id=r['id'], qualification='sourced', source=dict(SOURCED))
                                               for r in i['io_map']['requirements']]))))
case('contract/unqualified-with-original-source', mutate_contract('bound', lambda c: c['invariants'][0]['io_map'][
    'requirements'][0].update(source=dict(path=CSV_PATH, sha256=csv_sha, locator='original v9 row'))),
     probe='r1 probe.py historical-unqualified-with-original-source')
case('contract/row-number-changed', mutate_contract('bound', each_citation(
    lambda i: i['io_map'].update(row=i['io_map']['row'] + 10))))
case('contract/io-map-sha-changed', mutate_contract('bound', each_citation(lambda i: i['io_map'].update(sha256='c' * 64))))

# contract and record changed consistently and re-pinned, CSV unchanged
STATUS_UPGRADE = dict(path='current.md', sha256=sha(b'current\n'), locator='item 4')
CONSISTENT = {
    'emptied-citations': (lambda i: i['io_map'].update(requirements=[]), lambda r: r.update(requirements=[])),
    'removed-id': (lambda i: i['io_map'].update(requirements=[e for e in i['io_map']['requirements'] if e['id'] != 'POD-IF-A']),
                   lambda r: r.update(requirements=[e for e in r['requirements'] if e['id'] != 'POD-IF-A'])),
    'changed-description': (lambda i: i['io_map'].update(external_destination='accidental description'),
                            lambda r: r.update(external_destination='accidental description')),
    'changed-signal': (lambda i: i['io_map'].update(signal='CHANGED'), lambda r: r.update(signal='CHANGED')),
    'upgrade-uncaptured-source': (
        lambda i: i['io_map'].update(requirements=[dict(id=e['id'], qualification='sourced', source=dict(STATUS_UPGRADE))
                                                   for e in i['io_map']['requirements']]),
        lambda r: r.update(requirements=[dict(id=e['id'], qualification='sourced', source=dict(STATUS_UPGRADE))
                                         for e in r['requirements']])),
    'upgrade-citing-csv': (
        lambda i: i['io_map'].update(requirements=[dict(id=e['id'], qualification='sourced', source=dict(UPGRADE_CSV))
                                                   for e in i['io_map']['requirements']]),
        lambda r: r.update(requirements=[dict(id=e['id'], qualification='sourced', source=dict(UPGRADE_CSV))
                                         for e in r['requirements']])),
}
for mode, (cfn, rfn) in CONSISTENT.items():
    case(f'consistent-repinned/{mode}', mutate_contract('bound', each_citation(cfn), record_fn=each_row(rfn), repin=True))
cfn, rfn = CONSISTENT['upgrade-uncaptured-source']
case('consistent-pin-unchanged/statuses-upgraded', mutate_contract('bound', each_citation(cfn), record_fn=each_row(rfn)),
     probe='r2 record_pin_probe.py statuses-upgraded')
case('csv/changed-bytes', bundle_with('bound', lambda b: setattr(b, 'csv', b.csv.replace(b'Harness CAN low', b'Harness CAN LOW'))))
case('csv/not-captured', bundle_with('bound', lambda b: b.uncaptured.add(CSV_PATH)))

# origins: conflicting declarations for one captured path, in both orders; mismatching captured bytes


def conflicting(order, field='sha256', uncaptured=False):
    def build():
        b = base('bound-verified')
        if uncaptured:
            b.uncaptured.add('origin.csv')
        value = b.record_value()
        value['rows'][1]['origin'][field] = sha(OTHER_BODY) if field == 'sha256' else 'v8'
        if order == 'reversed':
            value['rows'].reverse()
        elif order == 'bad-first':
            value['rows'][0], value['rows'][1] = value['rows'][1], value['rows'][0]
        b.set_record(value)
        return b
    return build


for order in ('as-is', 'reversed', 'bad-first'):
    case(f'origin/conflicting-sha256/{order}', conflicting(order), probe=f'r3 origin_probe.py reverse={order}')
    case(f'origin/conflicting-sha256-uncaptured/{order}', conflicting(order, uncaptured=True))
case('origin/conflicting-revision', conflicting('as-is', 'revision'))
case('origin/captured-bytes-differ', bundle_with('bound-verified', lambda b: b.extra.update({'origin.csv': OTHER_BODY})))
case('sourced/source-not-captured', bundle_with('bound-mixed', lambda b: b.uncaptured.add('item4.md')))
case('sourced/source-bytes-changed', bundle_with('bound-mixed', lambda b: b.extra.update({'item4.md': b'x'})))

# legacy: unbound contracts stay accepted, are reported not qualification-bound and cannot escape a pinned scope


def legacy_under_pin(name):
    def build():
        b = base(name)
        bound = base('bound')
        b.record, b.pin = bound.record, bound.pin
        return b
    return build


for name in ('legacy', 'legacy-explicit'):
    case(f'legacy/{name}/unpinned-under-pinned-scope', legacy_under_pin(name), probe='r2 bound_probe.py legacy-standin')
for mode in ('empty-citations', 'strip-io-map', 'changed-description'):
    case(f'legacy/legacy-explicit/{mode}', mutate_contract('legacy-explicit', each_citation(CITATION_MODES[mode])),
         'accept', ok_legacy_empty if mode != 'changed-description' else ok_legacy, na=('compiler',),
         probe=f'r1 probe.py {mode} (legacy: accepted, not qualification-bound)')
case('legacy/legacy-explicit/bare-ids', mutate_contract('legacy-explicit', each_citation(CITATION_MODES['bare-ids'])),
     'accept', ok_legacy, na=('compiler',), probe='r1 probe.py bare-ids (legacy: accepted, not qualification-bound)')
case('legacy/legacy-explicit/upgrade-from-csv', mutate_contract('legacy-explicit', each_citation(CITATION_MODES['upgrade-from-csv'])),
     probe='r1 probe.py upgrade-from-csv')
case('legacy/legacy-explicit/unqualified-with-original-source', mutate_contract(
    'legacy-explicit', lambda c: c['invariants'][0]['io_map']['requirements'][0].update(
        source=dict(path=CSV_PATH, sha256=csv_sha, locator='original v9 row'))),
     probe='r1 probe.py historical-unqualified-with-original-source')


# agent-521 1b7c9cc0 (1): runtime schema checks are unconditional. Values a type hint or a loose
# isinstance would let through (bool for int, float, numeric strings, whitespace, uppercase or short
# digests, unsafe paths, extra keys, near-miss enums, nested values) must refuse in every consumer.
RUNTIME = [
    ('bound', ('rows', 0, 'row'), True), ('bound', ('rows', 0, 'row'), 2.0), ('bound', ('rows', 0, 'row'), 1),
    ('bound', ('rows', 0, 'row'), -2), ('bound', ('rows', 0, 'row'), 3),
    ('bound', ('rows', 0, 'connector_pin'), '   '), ('bound', ('rows', 0, 'connector_pin'), 1),
    ('bound', ('rows', 0, 'signal'), ['CAN_H']), ('bound', ('rows', 0, 'external_destination'), {'text': 'x'}),
    ('bound', ('io_map', 'sha256'), 'A' * 64), ('bound', ('io_map', 'sha256'), 'a' * 63),
    ('bound', ('io_map', 'sha256'), 1), ('bound', ('io_map', 'path'), '/abs/io-map.csv'),
    ('bound', ('io_map', 'path'), '../io-map.csv'), ('bound', ('io_map', 'path'), '.'),
    ('bound', ('io_map', 'extra'), 'x'), ('bound', ('extra',), 'x'), ('bound', ('rows', 0, 'extra'), 'x'),
    ('bound', ('rows', 0, 'origin', 'extra'), 'x'), ('bound', ('rows', 0, 'requirements', 0, 'extra'), 'x'),
    ('bound', ('rows', 0, 'requirements', 0), 'POD-IF-A'),
    ('bound', ('rows', 0, 'requirements', 1), {'id': 'POD-IF-A', 'qualification': 'unqualified-carryover'}),
    ('bound', ('rows', 0, 'requirements', 0, 'qualification'), 'Sourced'),
    ('bound', ('rows', 0, 'requirements', 0, 'qualification'), ['unqualified-carryover']),
    ('bound', ('rows', 0, 'requirements', 0, 'qualification'), True),
    ('bound', ('rows', 0, 'requirements', 0, 'id'), {'id': 'POD-IF-A'}),
    ('bound', ('rows', 0, 'requirements', 0, 'id'), '  '),
    ('bound', ('rows', 0, 'origin', 'sha256'), 'A' * 64), ('bound', ('rows', 0, 'origin', 'sha256'), 0),
    ('bound', ('rows', 0, 'origin', 'path'), '/v9/io-map.csv'), ('bound', ('rows', 0, 'origin', 'path'), '../v9.csv'),
    ('bound', ('rows', 0, 'origin', 'revision'), ['v9']), ('bound', ('rows', 0, 'origin', 'revision'), 9),
    ('bound', ('rows', 0, 'origin', 'locator'), ' '), ('bound', ('schema',), 'agent-ee.io-map-qualification/2'),
    ('bound-mixed', ('rows', 0, 'requirements', 0, 'source', 'sha256'), 'A' * 64),
    ('bound-mixed', ('rows', 0, 'requirements', 0, 'source', 'path'), '/item4.md'),
    ('bound-mixed', ('rows', 0, 'requirements', 0, 'source', 'extra'), 'x'),
    ('bound-mixed', ('rows', 0, 'requirements', 0, 'qualification'), 'unqualified-carryover'),
]
for index, (base_name, path, value) in enumerate(RUNTIME):
    dotted = '.'.join(str(p) for p in path)
    case(f'runtime-type/{index:02d}/{base_name}/{dotted}={json.dumps(value)[:24]}', mutate_record(base_name, path, value))
for label, op in (('sha256-uppercase', dict(path=RECORD_PATH, sha256='A' * 64)),
                  ('path-absolute', dict(path='/qualification.json', sha256='a' * 64)),
                  ('extra-key', dict(path=RECORD_PATH, sha256='a' * 64, note='x')),
                  ('sha256-int', dict(path=RECORD_PATH, sha256=1))):
    case(f'runtime-type/declaration/{label}', mutate_contract('bound', lambda c, op=op: c.update(qualification_record=op)))
case('runtime-type/contract/citation-row-bool', mutate_contract('bound', each_citation(lambda i: i['io_map'].update(row=True))))
case('runtime-type/contract/citation-sha256-uppercase', mutate_contract('bound', each_citation(
    lambda i: i['io_map'].update(sha256=i['io_map']['sha256'].upper()))))

# agent-521 1b7c9cc0 (2): authenticated origin bytes stay distinct from qualification and authority.
BASES['bound-mixed-verified'] = lambda: compile_bundle(
    dict(history(path='origin.csv', sha256=sha(ORIGIN_BODY)), sourced={'POD-IF-A': SOURCED}),
    {'origin.csv': ORIGIN_BODY, 'item4.md': ITEM4})
case('authority/verified-origin-mixed', lambda: base('bound-mixed-verified'), 'accept',
     dict(bound=True, sourced=['POD-IF-A'], unqualified=['POD-IF-B'], origins={'origin.csv': 'verified'}))
ORIGIN_AS_SOURCE = dict(path='origin.csv', sha256=sha(ORIGIN_BODY), locator='v9 table 2')
case('authority/sourced-cites-verified-origin', mutate_contract(
    'bound-verified', each_citation(lambda i: i['io_map'].update(requirements=[
        dict(id=e['id'], qualification='sourced', source=dict(ORIGIN_AS_SOURCE)) for e in i['io_map']['requirements']])),
    record_fn=each_row(lambda r: r.update(requirements=[
        dict(id=e['id'], qualification='sourced', source=dict(ORIGIN_AS_SOURCE)) for e in r['requirements']])),
    repin=True))
case('authority/sourced-cites-uncaptured-origin', mutate_contract(
    'bound', each_citation(lambda i: i['io_map'].update(requirements=[
        dict(id=e['id'], qualification='sourced', source=dict(path='v9/io-map.csv', sha256='a' * 64, locator='v9'))
        for e in i['io_map']['requirements']])),
    record_fn=each_row(lambda r: r.update(requirements=[
        dict(id=e['id'], qualification='sourced', source=dict(path='v9/io-map.csv', sha256='a' * 64, locator='v9'))
        for e in r['requirements']])),
    repin=True))


# ---- running ---------------------------------------------------------------------------------------

def evaluate(entry):
    bundle = entry['build']()
    return {c: verdict(c, bundle) for c in CONSUMERS}


def table(stream=sys.stdout):
    start = time.monotonic()
    failures = 0
    print('| case | ' + ' | '.join(CONSUMERS) + ' | expected | ok |', file=stream)
    print('|' + '---|' * (len(CONSUMERS) + 3), file=stream)
    for entry in CASES:
        observed = evaluate(entry)
        ok = not problems(entry, observed)
        failures += not ok
        expected = sorted({v for v in entry['expect'].values() if v != 'n/a'})
        print(f"| {entry['name']} | " + ' | '.join(
            'n/a' if entry['expect'][c] == 'n/a' else observed[c][0] for c in CONSUMERS)
              + f" | {'/'.join(expected)} | {'ok' if ok else 'FAIL'} |", file=stream)
    print(f'\ncases={len(CASES)} failures={failures} runtime={time.monotonic() - start:.1f}s '
          f'api={"verify_contract" if NEW_API else "validate_contract(legacy signature)"}', file=stream)
    return failures


def problems(entry, observed):
    found = []
    for consumer in CONSUMERS:
        expected = entry['expect'][consumer]
        if expected == 'n/a':
            continue
        kind, detail = observed[consumer]
        if kind != expected:
            found.append(f'{consumer}: expected {expected}, got {kind} ({detail})')
        elif kind == 'accept' and entry['label'] is not None and (detail is not None or NEW_API) and (
                detail != entry['label']):
            # an older revision's validator returns no report (detail None): only its verdict is compared
            found.append(f'{consumer}: label {detail} != {entry["label"]}')
    return found


class QualificationMatrixTests(unittest.TestCase):
    def test_matrix(self):
        self.assertGreaterEqual(len(CASES), 150)
        self.assertEqual(len({c['name'] for c in CASES}), len(CASES), 'case names are unique')
        for entry in CASES:
            with self.subTest(entry['name']):
                self.assertEqual(problems(entry, evaluate(entry)), [])

    def test_every_reviewer_probe_is_in_the_matrix(self):
        probes = ' '.join(c['probe'] or '' for c in CASES)
        for name in ('r1 probe.py bare-ids', 'r1 probe.py empty-citations', 'r1 probe.py strip-io-map',
                     'r1 probe.py upgrade-from-csv', 'r1 probe.py changed-description',
                     'r1 probe.py historical-unqualified-with-original-source', 'r1 probe.py invariant-order',
                     'r2 record_pin_probe.py origin-changed', 'r2 record_pin_probe.py statuses-upgraded',
                     'r2 record_pin_probe.py bound-record-omitted', 'r2 bound_probe.py changed-signal',
                     'r2 bound_probe.py legacy-standin', 'r2 bound_probe.py record-missing',
                     'r2 bound_probe.py record-not-captured', 'r2 bound_probe.py missing-origin-path',
                     'r3 null_probe.py original', 'r3 null_probe.py empty-citations',
                     'r3 origin_probe.py reverse=as-is', 'r3 origin_probe.py reverse=reversed',
                     'r3 origin_probe.py reverse=bad-first'):
            self.assertIn(name, probes)

    def test_probe_r1_all_unqualified_coverage_is_never_counted(self):
        """r1 probe.py all-unqualified-coverage: carry-over is reported, never qualified coverage."""
        with tempfile.TemporaryDirectory() as tmp:
            scope, files, root = capture_dir(base('bound'), tmp)
            coverage = compile_coverage(scope, files, root)
        cited = coverage['requirement_citations']
        self.assertEqual((cited['qualified'], cited['unqualified-carryover'], cited['counted_as_coverage']),
                         ([], {'interface': UNQ}, False))
        self.assertTrue(coverage['checks']['interface']['can_evaluate_bound'])

    def test_probe_r1_spec_record_traceability_separates_unqualified(self):
        """r1 probe.py spec-record-unqualified-traceability."""
        pod = Path(__file__).resolve().parents[1] / 'fixtures' / 'telemetry-pod'
        report = {}
        contract = io_map.compile_io_map((pod / 'io-map.csv').read_bytes(), csv_path='io-map.csv',
            requirement='POD-AC-1', source=dict(path='spec.md', locator='Acceptance criteria / item 1'),
            spec_record=json.loads((pod / 'spec-record.json').read_text()), qualifications={}, report=report)
        self.assertEqual(report['traceability']['qualified'], [])
        self.assertEqual(len(report['traceability']['unqualified-carryover']), 8)
        self.assertEqual(hi.requirement_authority(contract)['sourced'], [])

    def test_verified_origin_is_an_immutable_input(self):
        """r3 origin_probe.py origin-in-immutable-inputs: an authenticated origin is pinned for the run."""
        with tempfile.TemporaryDirectory() as tmp:
            scope, files, root = capture_dir(base('bound-verified'), tmp)
            descriptor = hi.capability(scope['checks'][1]['params'], scope, files, root, 'interface')
        self.assertIn('origin.csv', descriptor['immutable_inputs'])
        self.assertEqual({r['origin'] for r in descriptor['requirement_history']['rows']}, {'verified'})


    def test_loader_runs_on_every_call_and_accepts_only_bytes(self):
        """1b7c9cc0 (1): no pre-built or typed object bypasses the runtime schema."""
        b = base('bound')
        good = hi.load_qualification_record(b.contract['qualification_record'], b.record)
        forged = hi.QualificationRecord(good.path, good.sha256, good.raw, {'schema': None})
        for supplied in (good, forged, good.data, b.record.decode(), memoryview(b.record), [b.record]):
            with self.subTest(type(supplied).__name__), self.assertRaisesRegex(ValueError, 'exact bytes'):
                hi.verify_contract(copy.deepcopy(b.contract), qualification=supplied, captured=b.captured())
            with self.subTest('bind', type=type(supplied).__name__), self.assertRaisesRegex(ValueError, 'exact bytes'):
                hi.bind_qualification_record(copy.deepcopy(b.contract), supplied, b.captured())
        self.assertTrue(hi.verify_contract(copy.deepcopy(b.contract), qualification=bytearray(b.record),
                                           captured=b.captured())[1]['qualification_bound'])

    def test_verified_origin_confers_no_qualification(self):
        """1b7c9cc0 (2): authenticated origin bytes prove provenance, never a requirement's qualification."""
        for name in ('bound-verified', 'bound-mixed-verified'):
            b = base(name)
            with tempfile.TemporaryDirectory() as tmp:
                scope, files, root = capture_dir(b, tmp)
                descriptor = hi.capability(scope['checks'][1]['params'], scope, files, root, 'interface')
                coverage = compile_coverage(scope, files, root)
            rows = descriptor['requirement_history']['rows']
            self.assertEqual({r['origin'] for r in rows}, {'verified'})
            expected_sourced = ['POD-IF-A'] if name == 'bound-mixed-verified' else []
            for row in rows:
                for rid, status in row['requirements'].items():
                    self.assertEqual(status, 'sourced' if rid in expected_sourced else 'unqualified historical assertion')
            self.assertEqual(descriptor['requirement_authority']['sourced'], expected_sourced)
            cited = coverage['requirement_citations']
            self.assertEqual(cited['qualified'], expected_sourced)
            self.assertIn('POD-IF-B', cited['unqualified-carryover']['interface'])
            self.assertIs(cited['counted_as_coverage'], False)

    def test_uncaptured_origin_is_labelled_never_dropped(self):
        """1b7c9cc0 (2): every row keeps its origin, labelled declared-unverified, in every consumer."""
        b = base('bound-verified')
        b.uncaptured.add('origin.csv')
        record_rows = [r['row'] for r in b.record_value()['rows']]
        _, report = hi.verify_contract(copy.deepcopy(b.contract), qualification=b.record, captured=b.captured())
        with tempfile.TemporaryDirectory() as tmp:
            scope, files, root = capture_dir(b, tmp)
            descriptor = hi.capability(scope['checks'][1]['params'], scope, files, root, 'interface')
            detail = compile_coverage(scope, files, root)['checks']['interface']['requirement_qualification']
        for history in (report, descriptor['requirement_history'], detail):
            self.assertEqual(history['origin_verification'], {'origin.csv': 'declared-unverified'})
            self.assertEqual([r['row'] for r in history['rows']], record_rows)
            self.assertEqual({(r['origin_path'], r['origin']) for r in history['rows']},
                             {('origin.csv', 'declared-unverified')})
            self.assertNotIn('origin.csv', descriptor['immutable_inputs'])

class NativeQualificationMatrixTests(unittest.TestCase):
    """r1 native_probe.py: native pass keeps independent structural authority; carry-over is reported."""

    def test_native_reporting_legacy_and_bound(self):
        require_toolchain(native=True)
        runner = io_tests.NativeQualificationTests()
        try:
            for bound in (False, True):
                with self.subTest(bound=bound):
                    result = runner.run_native(bound)
                    self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
                    self.assertEqual(result['details']['requirement_qualification'],
                                     {'sourced': [], 'unqualified-carryover': UNQ})
                    self.assertTrue(any('never graded' in x for x in result['limitations']), result)
                    history = result['details']['requirement_history']
                    self.assertIs(history['qualification_bound'], bound)
                    if bound:
                        self.assertEqual(history['origin_verification'], {'v9/io-map.csv': 'declared-unverified'})
        finally:
            runner.doCleanups()


if __name__ == '__main__':
    raise SystemExit(1 if table() else 0)
