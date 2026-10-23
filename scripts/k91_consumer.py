"""The production routing consumer as the K91 harness boundary (AGENTEE-442, ruling a01d6f91).

The protected boundary is campaign.run_implementation_steps: run_step, then
routing_adapter.verify_campaign_receipt, then stage_receipts.adopt, all called
by that function in its own order. consume() calls it for one routing step and
records each layer:

- `consumer`: the production call itself. It raises when it refuses, or
  returns after verify and adopt succeeded;
- `consumer_run_step`, `consumer_verify` and `consumer_adopt`: what the
  production consumer did at each layer (run_step is the executed or the
  replayed step). Pass-through observers record each call as made with its outcome
  (the exception is re-raised unchanged, the value returned unchanged), or as
  not made when the consumer never called it. They never decide anything;
- `design_before` and `design_after`: the digest of the consumer's design tree
  around the call;
- `direct_adopt`: the bare stage adoption on a DIFFERENT disposable design
  copy (`direct_design`), a diagnostic that cannot mutate the design used to
  observe the consumer.

Replay plumbing (replayed_run_step) may stand in for run_step only. It returns
a retained receipt already placed at the receipt directory the consumer
computes, so the receipt's own sandbox command, binding and mounts are checked
by verify_campaign_receipt exactly as on a live run. Verification, adoption
and their order are never replaced.

pinned() is the isolated proposed pin in the production shape: a copy of the
real adapter source whose PIN and PINNED_CODE name the proposed commit, used
as ADAPTER_SOURCE (mounted at the production path, the same command), with
the in-process constants patched to match (the test_routing_campaign
precedent). The production pin and defaults on disk are unchanged.
"""
import contextlib
import hashlib
import json
import traceback
from pathlib import Path
from unittest.mock import patch

from agent_ee import campaign, kicad_route_attempt as attempt, routing_adapter, stage_receipts
from agent_ee.storage import digest

CANDIDATE_ID = 'k91-native-positive'
PLAN = [{'kind': 'routing', 'receipt_index': 1}]
STEP_DIR = 'receipts/1-kicad-route-builtin'
NOT_CALLED = 'not called by campaign.run_implementation_steps'


def exception_chain(exc):
    """The full exception context chain, outermost first: each exception's type, message and origin
    frame (the frame that raised it), and its frames (file:function, outer to inner). The chain follows
    __cause__, else __context__ (recorded even when suppressed), so a finalization error raised while
    another exception was handled keeps that exception visible."""
    chain, seen = [], set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        frames = traceback.extract_tb(exc.__traceback__)
        origin = frames[-1] if frames else None
        chain.append({'type': type(exc).__name__, 'message': str(exc),
                      'origin': None if origin is None else {
                          'file': Path(origin.filename).name, 'function': origin.name, 'line': origin.lineno},
                      'frames': [f'{Path(f.filename).name}:{f.name}' for f in frames],
                      'link': 'cause' if exc.__cause__ is not None else 'context' if exc.__context__ is not None
                      else None})
        exc = exc.__cause__ if exc.__cause__ is not None else exc.__context__
    return chain


def failure(exc):
    """The refused-call record: the outermost error and the whole chain (never just accepted: false)."""
    return {'attempted': True, 'accepted': False, 'error': f'{type(exc).__name__}: {exc}',
            'exception': exception_chain(exc)}


def observe(fn):
    """An observed call: {'attempted': True, 'accepted': True, 'value'} or failure(exc) (k91_classify schema)."""
    try:
        return {'attempted': True, 'accepted': True, 'value': fn()}
    except Exception as exc:  # recorded with its full chain; the classifier decides, never this
        return failure(exc)


def not_made(reason):
    return {'attempted': False, 'reason': reason}


def tree_sha256(root):
    files, other = stage_receipts.file_tree(root)
    return hashlib.sha256(json.dumps({'files': files, 'other': other}, sort_keys=True).encode()).hexdigest()


@contextlib.contextmanager
def pinned(out, pin, code):
    """The production adapter, pinned to the proposed commit, in its production shape."""
    source = Path(attempt.__file__).read_text()
    source = source.replace('PIN = ' + repr(attempt.PIN), 'PIN = ' + repr(pin), 1)
    start, end = source.index('PINNED_CODE = '), source.index('RESULT_SCHEMA = ')
    copy = Path(out) / 'pinned-adapter/kicad_route_attempt.py'
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_text(source[:start] + f'PINNED_CODE = {code!r}\n' + source[end:])
    with patch.object(attempt, 'PIN', pin), patch.object(attempt, 'PINNED_CODE', code), \
            patch.object(routing_adapter, 'ADAPTER_SOURCE', copy):
        yield {'path': str(copy), 'sha256': digest(copy)}


def declaration(checkout, *, project, board, schematic, name, budget, timeout, policy):
    return {'schema': 'agent-ee.routing-adapter/1', 'project': project, 'board': board, 'schematic': schematic,
            'name': name, 'label': 'k91-native-positive', 'checkout': str(Path(checkout).resolve()),
            'budget': budget, 'timeout': timeout, 'experiment_policy': policy}


def binding(decl):
    """routing_adapter.campaign_binding without the campaign scope checks: the same production
    validation (validate_declaration, declaration_spec, validate_campaign_step with preflight)."""
    spec = routing_adapter.validate_campaign_step(
        routing_adapter.declaration_spec(routing_adapter.validate_declaration(decl)))
    return {'spec': spec, 'tool': stage_receipts.observe_tool(spec), 'pin': attempt.PIN,
            'adapter_sha256': digest(routing_adapter.ADAPTER_SOURCE)}


def runtime_binding(good, checkout):
    """The good binding's step with another runtime mounted at the checkout path, bound to what that
    runtime is observed to be (not produced by binding(), whose preflight refuses it)."""
    spec = json.loads(json.dumps(good['spec']))
    spec['mounts'] = [{'source': str(Path(checkout).resolve()), 'at': m['at']}
                      if m['at'] == attempt.SANDBOX['checkout'] else m for m in spec['mounts']]
    spec = routing_adapter.validate_campaign_step(spec, host=False)
    return {**good, 'spec': spec, 'tool': stage_receipts.observe_tool(spec)}


def replayed_run_step(work):
    """A run_step stand-in returning the retained receipt already placed at work/STEP_DIR, as written."""
    def run_step(receipt_dir, design, spec, expected, *, deadline=None, cancellation=None):
        if Path(receipt_dir) != Path(work) / STEP_DIR:
            raise ValueError(f'replay expected {Path(work) / STEP_DIR}, the consumer asked for {receipt_dir}')
        # A live run_step returns the receipt it wrote; it is not re-validated here, so any later
        # change to the retained run is left for the consumer's own verify_campaign_receipt to find.
        receipt = json.loads((Path(receipt_dir) / 'receipt.json').read_text())
        if (not isinstance(receipt, dict) or receipt.get('spec') != stage_receipts.validate_step(spec, host=False)
                or receipt.get('binding') != expected):
            raise ValueError('replayed receipt is not the step and binding the consumer asked for')
        return receipt
    return run_step


@contextlib.contextmanager
def observed_layers():
    """Pass-through observers of verify_campaign_receipt and adopt as the consumer calls them."""
    calls = {'run_step': [], 'verify': [], 'adopt': []}
    run_step, verify, adopt = stage_receipts.run_step, routing_adapter.verify_campaign_receipt, stage_receipts.adopt

    def wrap(name, fn):
        def observer(*args, **kwargs):
            try:
                value = fn(*args, **kwargs)
            except Exception as exc:
                calls[name].append(failure(exc))
                raise
            calls[name].append({'attempted': True, 'accepted': True,
                                'value': value if name != 'run_step' else (value or {}).get('receipt_id')})
            return value
        return observer

    with patch.object(stage_receipts, 'run_step', wrap('run_step', run_step)), \
            patch.object(routing_adapter, 'verify_campaign_receipt', wrap('verify', verify)), \
            patch.object(stage_receipts, 'adopt', wrap('adopt', adopt)):
        yield calls


def layer(calls):
    """One layer's record: the single observed call, not made, or malformed (two calls)."""
    if not calls:
        return not_made(NOT_CALLED)
    return calls[0] if len(calls) == 1 else {'malformed': calls}


def consume(work, design, bind, *, deadline=None, replay=False, direct_design=None):
    """campaign.run_implementation_steps for one routing step, observed per layer."""
    work, design = Path(work), Path(design)
    manifest = {'files': stage_receipts.file_tree(design)[0], 'candidate_id': CANDIDATE_ID, 'scope': {}}
    state = {'routing_binding': bind, 'stage_receipts': {}}
    before = tree_sha256(design)
    with contextlib.ExitStack() as stack:
        if replay:
            stack.enter_context(patch.object(stage_receipts, 'run_step', replayed_run_step(work)))
        calls = stack.enter_context(observed_layers())
        consumer = observe(lambda: campaign.run_implementation_steps(work, design, manifest, state, deadline, PLAN))
    record = {'consumer': consumer, 'run_step': 'replayed' if replay else 'executed',
              'consumer_run_step': layer(calls['run_step']),
              'consumer_verify': layer(calls['verify']), 'consumer_adopt': layer(calls['adopt']),
              'consumer_design': str(design), 'design_before': before, 'design_after': tree_sha256(design)}
    receipt_dir = work / STEP_DIR
    if direct_design is not None:
        direct_design = Path(direct_design)
        record['direct_design'] = str(direct_design)
        record['direct_adopt'] = observe(lambda: stage_receipts.adopt(
            receipt_dir, stage_receipts.validate_receipt(receipt_dir), direct_design)) \
            if (receipt_dir / 'receipt.json').is_file() else not_made('no receipt was retained')
    return record


@contextlib.contextmanager
def disposable_receipt(positive_work):
    """A disposable copy of the positive's work directory AT ITS OWN PATH (the receipt's sandbox command
    names that path); the original is set aside and restored byte for byte afterwards."""
    import shutil
    positive_work = Path(positive_work)
    kept = positive_work.with_name(positive_work.name + '.kept')
    positive_work.rename(kept)
    try:
        shutil.copytree(kept, positive_work, symlinks=True)
        yield positive_work
    finally:
        if positive_work.exists():
            shutil.rmtree(positive_work)
        kept.rename(positive_work)


def flip(path):
    """One flipped byte in the middle of a file; returns the record of the change."""
    path = Path(path)
    original = path.read_bytes()
    data = bytearray(original)
    data[len(data) // 2] ^= 0x01
    path.write_bytes(bytes(data))
    return {'offset': len(data) // 2, 'sha256_before': hashlib.sha256(original).hexdigest(),
            'sha256_after': hashlib.sha256(bytes(data)).hexdigest()}


def _low_level_verify(work, inputs=None):
    """routing_adapter.verify on the retained run: against the receipt, or (stale) against other inputs."""
    receipt_dir = Path(work) / STEP_DIR
    if inputs is not None:
        return observe(lambda: routing_adapter.verify(receipt_dir / 'run', inputs))
    return observe(lambda: routing_adapter.verify(receipt_dir / 'run',
                                                  receipt=stage_receipts.validate_receipt(receipt_dir)))


def run_controls(out, positive_work, decl, bind, mismatch, make_design, board, *, deadline=None):
    """The required negatives (a) runtime mismatch, (b) candidate-board tamper, (c) stale input, and the
    separate sidecar tamper, each through the production consumer. make_design(path) writes a fresh
    copy of the captured input at path and returns it; board is its board path (project/<board>)."""
    out, controls = Path(out), {}

    # (a) Another runtime: refused by the production binding (preflight) and by the production
    # consumer, which re-checks the live binding before run_step. The low-level observation of the
    # historical harness is kept apart: run_step executed directly (the router never starts), then
    # verify and the bare adoption on another design copy.
    root = out / 'control-a-runtime'
    host = observe(lambda: binding({**decl, 'checkout': str(Path(mismatch).resolve())}))
    if host['accepted']:
        host = {'attempted': True, 'accepted': True, 'value': None}
    work = root / '1'
    work.mkdir(parents=True)
    other = runtime_binding(bind, mismatch)
    record = consume(work, make_design(root / 'design'), other, deadline=deadline,
                     direct_design=make_design(root / 'direct-design'))
    low = root / 'low-level'
    low.mkdir()
    low_design = make_design(root / 'low-level-design')
    expected = {'implementation': 1, 'source_candidate_id': CANDIDATE_ID, 'receipt_index': 1}
    ran = observe(lambda: stage_receipts.run_step(low / STEP_DIR, low_design, other['spec'], expected,
                                                  deadline=deadline)['receipt_id'])
    report = low / STEP_DIR / 'run/routing/adapter-report.json'
    sandbox = json.loads(report.read_text()) if report.is_file() else None
    controls['a_mismatched_runtime'] = {**record, 'host_binding': host, 'low_level': {
        'run_step': ran, 'host_step': host, 'verify': _low_level_verify(low),
        'adopt': not_made('the low-level observation makes no consumer adoption'),
        'direct_adopt': observe(lambda: stage_receipts.adopt(
            low / STEP_DIR, stage_receipts.validate_receipt(low / STEP_DIR), make_design(root / 'low-level-direct'))),
        'sandbox': None if sandbox is None else {
            'classification': sandbox['semantics']['classification'], 'reasons': sandbox['semantics']['reasons'],
            'candidate': sandbox['candidate']}}}

    # (b) One byte of the delivered candidate board, and separately of the retained sidecar.
    target = next(iter(bind['spec']['writes'].values()))
    for name, rel in (('b_tampered_candidate', target), ('b_tampered_sidecar', 'routing/attempt/routability-experiment.json')):
        root = out / f'control-{name}'
        with disposable_receipt(positive_work) as work:
            path = work / STEP_DIR / 'run' / rel
            if not path.is_file():
                controls[name] = {'skipped': f'{rel} absent in the positive receipt', 'file': rel}
                continue
            change = flip(path)
            record = consume(work, make_design(root / 'design'), bind, deadline=deadline, replay=True,
                             direct_design=make_design(root / 'direct-design'))
            controls[name] = {**record, 'file': rel, **change, 'low_level': {
                'verify': _low_level_verify(work), 'adopt': not_made('the low-level observation makes no consumer adoption'),
                'direct_adopt': record['direct_adopt']}}

    # (c) A stale input: the input changed by one byte since the receipt was taken.
    root = out / 'control-c-stale'
    with disposable_receipt(positive_work) as work:
        design, direct = make_design(root / 'design'), make_design(root / 'direct-design')
        for tree in (design, direct):
            (tree / board).write_bytes((tree / board).read_bytes() + b'\n')
        record = consume(work, design, bind, deadline=deadline, replay=True, direct_design=direct)
        inputs = stage_receipts.file_tree(design)[0]
        receipt = json.loads((work / STEP_DIR / 'receipt.json').read_text())
        controls['c_stale_input'] = {**record, 'board_sha256': inputs[board],
                                     'receipt_board_sha256': receipt['inputs'].get(board), 'low_level': {
            'verify': _low_level_verify(work, inputs), 'adopt': not_made('the low-level observation makes no consumer adoption'),
            'direct_adopt': record['direct_adopt']}}
    return controls
