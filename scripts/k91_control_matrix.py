#!/usr/bin/env python3
"""The K91 harness control matrix: the lane's regression gate (AGENTEE-442, third-HOLD root redesign).

    k91_control_matrix.py [--scripts DIR] [--json OUT]

Every row runs the ACTUAL production routing consumer, campaign.run_implementation_steps
(run_step, verify_campaign_receipt, adopt, unstubbed), through DIR/k91_consumer.py on the
synthetic fixture router, and judges the result with DIR/k91_classify.py v2_verdict. DIR
defaults to this checkout's scripts/; pointing it at another tree's scripts (for example
57083ef8) runs the same rows against that classifier. Only the API both share is used.

Rows:
- positives: the executed positive and the untouched replay (verify AND adopt accepted);
- each intended control (runtime mismatch, candidate-board tamper, sidecar tamper, stale input);
- in EACH control slot: the untouched replay, a wrong replay binding, invalid receipt JSON, a
  missing receipt.json, a campaign finalization error, an unrelated exception, missing fields,
  a skipped consumer call and the round-2 skipped-adoption shape;
- every round-1 to round-3 review counterexample (reviews/ee442-native-positive-b-sol*.md and
  the reviewer probes replay.py, round2/adjudicate.py, round2/mismatch-replay.py,
  round2/consumer-order.py, round3/adjudicate-r3.py, round3/refusal-reasons/reason_probe.py).

Each slot row also states the expected controls_refused when it stands in for that control
while the other required controls are the intended refusals. Exit 0 when every row has its
expected outcome; 1 otherwise, listing the rows that differ.
"""
import argparse
import copy
import hashlib
import importlib
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / 'src', ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

SLOTS = ('a_mismatched_runtime', 'b_tampered_candidate', 'b_tampered_sidecar', 'c_stale_input')
REQUIRED = ('a_mismatched_runtime', 'b_tampered_candidate', 'c_stale_input')
# The adapter that wrote and committed evidence/k91-native-positive (round 1), before the staged
# original (2c37f645); its verifier classifies that record. ERA_MODULES are the ones its verify imports,
# committed byte-exact under ERA_FIXTURE (manifest.json: each git blob id and sha256), so the gate needs
# no git history; tests/test_k91_era_fixture.py binds the files to the blobs.
ERA = '7552f6db72ed53d5439144d145835160c206f0cb'
ERA_FIXTURE = ROOT / 'tests/fixtures/k91-era-7552f6db'
ERA_PACKAGE = 'k91_era_adapter'
ERA_MODULES = ('routing_adapter', 'kicad_route_attempt', 'storage')
BOARD = 'project/rc.kicad_pcb'
SIDECAR = 'routing/attempt/routability-experiment.json'
REFUSED, NOT_REFUSED, INCOMPLETE = 'refused', 'not_refused', 'incomplete'
OBSERVATIONS = ('consumer', 'consumer_run_step', 'consumer_verify', 'consumer_adopt', 'direct_adopt', 'host_binding')
ATTACKS = ('wrong_replay_binding', 'invalid_receipt_json', 'missing_receipt')
PLUMBING = ('wrong_replay_binding', 'invalid_receipt_json', 'missing_receipt', 'finalization_error',
            'unrelated_exception')
MISSING = ('consumer', 'consumer_run_step', 'consumer_verify', 'consumer_adopt', 'direct_adopt', 'design_before',
           'design_after', 'consumer_design', 'direct_design', 'consumer.exception', 'run_step')
PRECONDITION_FIELDS = {'a_mismatched_runtime': ('host_binding',), 'b_tampered_candidate': ('sha256_before', 'file'),
                       'b_tampered_sidecar': ('sha256_after', 'file'), 'c_stale_input': ('receipt_board_sha256',)}


def load(scripts):
    sys.path.insert(0, str(Path(scripts).resolve()))
    for name in ('k91_consumer', 'k91_classify'):
        sys.modules.pop(name, None)
    return importlib.import_module('k91_consumer'), importlib.import_module('k91_classify')


class Matrix:
    def __init__(self, k, c, root):
        from tests.kicad_route_fixture import FAKE_PIN, make_candidate, make_checkout
        from tests.test_routing_experiment_boundary import CURRENT
        self.k, self.c, self.root, self.make = k, c, Path(root), make_candidate
        self.rows, self.count, self.attacks = [], 0, {}
        checkout, code = make_checkout(self.root / 'checkout', {'name': 'complete'})
        self.mismatch, _ = make_checkout(self.root / 'mismatch', {'name': 'complete'})
        with (self.mismatch / 'src/kicad_route/__init__.py').open('a') as handle:
            handle.write('# another runtime\n')
        self.pinned = k.pinned(self.root, FAKE_PIN, code)
        self.pinned.__enter__()
        self.decl = k.declaration(checkout, project='project', board='rc.kicad_pcb', schematic='rc.kicad_sch',
                                  name='rc', budget=5, timeout=30, policy=CURRENT)
        self.bind = k.binding(self.decl)
        self.work = self.root / 'positive' / '1'
        self.work.mkdir(parents=True)
        self.positive = k.consume(self.work, make_candidate(self.root / 'positive/design'), self.bind)
        self.receipt = json.loads((self.work / k.STEP_DIR / 'receipt.json').read_text())
        self.positive['delivered_board_sha256'] = self.receipt['outputs'][BOARD]
        self.target = next(iter(self.bind['spec']['writes'].values()))
        self.kept = k.tree_sha256(self.work)
        self.intended = k.run_controls(self.root / 'controls', self.work, self.decl, self.bind, self.mismatch,
                                       make_candidate, BOARD)

    def close(self):
        self.pinned.__exit__(None, None, None)

    def fresh(self, label):
        self.count += 1
        return self.root / f'row-{self.count}-{label}'

    # -- evaluation -------------------------------------------------------------------------------
    def verdict(self, slot=None, record=None, controls=None):
        controls = dict(self.intended if controls is None else controls)
        if slot is not None:
            controls[slot] = record
        return self.c.v2_verdict({'positive': self.positive, 'controls': controls})

    def slot_row(self, row_id, slot, record, expected, source):
        verdict = self.verdict(slot, record)
        expected_aggregate = expected == REFUSED if slot in REQUIRED else True
        self.rows.append({'id': row_id, 'slot': slot, 'source': source,
                          'expected': expected, 'actual': verdict['controls'][slot],
                          'expected_controls_refused': expected_aggregate,
                          'actual_controls_refused': verdict['controls_refused'],
                          'why': (verdict.get('not_refused_because') or {}).get(slot)})

    def check_row(self, row_id, expected, actual, source):
        self.rows.append({'id': row_id, 'slot': None, 'source': source, 'expected': expected, 'actual': actual})

    # -- record builders --------------------------------------------------------------------------
    def replayed(self, slot, mode=None, bind=None):
        """The slot's mutation (tamper or stale input), then a plumbing failure, through the consumer."""
        k, base = self.k, self.fresh(f'{slot}-{mode}')
        extra = {}
        with k.disposable_receipt(self.work) as work:
            step = work / k.STEP_DIR
            if slot in ('b_tampered_candidate', 'b_tampered_sidecar'):
                rel = self.target if slot == 'b_tampered_candidate' else SIDECAR
                extra = {'file': rel, **k.flip(step / 'run' / rel)}
            design, direct = self.make(base / 'design'), self.make(base / 'direct')
            if slot == 'c_stale_input':
                for tree in (design, direct):
                    (tree / BOARD).write_bytes((tree / BOARD).read_bytes() + b'\n')
                extra = {'board_sha256': self.k.stage_receipts.file_tree(design)[0][BOARD],
                         'receipt_board_sha256': self.receipt['inputs'][BOARD]}
            receipt = step / 'receipt.json'
            if mode == 'wrong_replay_binding':
                doc = json.loads(receipt.read_text())
                doc['binding']['source_candidate_id'] = 'unrelated replay input'
                receipt.write_text(json.dumps(doc))
            elif mode == 'invalid_receipt_json':
                receipt.write_text('{not JSON')
            elif mode == 'missing_receipt':
                receipt.unlink()
            elif mode == 'finalization_error':
                (work / 'receipts' / 'stray').write_text('not a receipt directory\n')
            elif mode == 'missing_candidate_file':
                (step / 'run' / self.target).unlink()
            record = k.consume(work, design, bind or self.bind, replay=True, direct_design=direct)
        assert k.tree_sha256(self.work) == self.kept, 'the positive receipt was not restored'
        return {**record, **extra}

    def runtime(self, mode):
        """A runtime-slot record: the intended host binding refusal plus the consumer under the failure."""
        k, base = self.k, self.fresh(f'runtime-{mode}')
        host = self.intended['a_mismatched_runtime']['host_binding']
        if mode in ('wrong_replay_binding', 'invalid_receipt_json', 'missing_receipt'):
            record = self.replayed('a_mismatched_runtime', mode)
            return {**record, 'host_binding': host}
        work = base / '1'
        (work / 'receipts').mkdir(parents=True)
        if mode == 'finalization_error':
            (work / 'receipts' / 'stray').write_text('not a receipt directory\n')
            bind = k.runtime_binding(self.bind, self.mismatch)
        else:  # unrelated_exception: the qualifying runtime, a binding whose adapter digest is stale
            bind = {**self.bind, 'adapter_sha256': '0' * 64}
        record = k.consume(work, self.make(base / 'design'), bind, direct_design=self.make(base / 'direct'))
        return {**record, 'host_binding': host}

    def plumbing(self, slot, mode):
        if slot == 'a_mismatched_runtime':
            return self.runtime(mode)
        if mode == 'unrelated_exception':
            return self.replayed(slot, None, bind={**self.bind, 'adapter_sha256': '0' * 64})
        return self.replayed(slot, mode)

    # -- the rows ---------------------------------------------------------------------------------
    def run(self):
        k, c = self.k, self.c
        verdict = self.verdict()
        self.check_row('positive_executed: verify AND adopt accepted', True, verdict['positive_accepted'],
                       'mandate (5); round-3 adjudicate-r3.py')
        untouched = self.replayed(None)
        replay_positive = {**untouched, 'delivered_board_sha256': self.positive['delivered_board_sha256']}
        self.check_row('positive_untouched_replay: verify AND adopt accepted', True,
                       c.v2_verdict({'positive': replay_positive, 'controls': self.intended})['positive_accepted'],
                       'round-3 reason_probe.py UNTOUCHED')
        self.check_row('all_intended_controls: controls_refused', True, verdict['controls_refused'],
                       'round-3 reason_probe.py INTENDED')
        for slot in SLOTS:
            self.slot_row(f'{slot}: intended refusal', slot, self.intended[slot], REFUSED, 'round-3 adjudication table')
            self.slot_row(f'{slot}: untouched replay', slot, untouched if slot != 'a_mismatched_runtime'
                          else {**untouched, 'host_binding': self.intended[slot]['host_binding']},
                          NOT_REFUSED, 'round-3 adjudicate-r3.py replay')
            for mode in PLUMBING:
                record = self.plumbing(slot, mode)
                self.attacks[slot, mode] = record
                self.slot_row(f'{slot}: {mode}', slot, record, INCOMPLETE,
                              'round-3 H4; agent-510 e5c23190 (2)' if mode in ATTACKS else 'mandate (5)')
            for key in MISSING + PRECONDITION_FIELDS[slot]:
                record = copy.deepcopy(self.intended[slot])
                if key == 'consumer.exception':
                    record['consumer'].pop('exception', None)
                else:
                    record.pop(key, None)
                self.slot_row(f'{slot}: missing {key}', slot, record, INCOMPLETE,
                              'round-2 H2; round-3 adjudicate-r3.py missing keys')
            record = copy.deepcopy(self.intended[slot])
            record['consumer'] = {'attempted': False, 'reason': 'the consumer was not called'}
            self.slot_row(f'{slot}: skipped consumer call', slot, record, INCOMPLETE, 'mandate (5)')
            round2 = {'verify': {'attempted': True, 'accepted': False, 'error': 'verify refused'},
                      'adopt': {'attempted': False, 'reason': 'verify refused'},
                      'direct_adopt': {'attempted': True, 'accepted': True, 'value': None}}
            self.slot_row(f'{slot}: round-2 skipped adoption, bare adopt succeeded', slot, round2, INCOMPLETE,
                          'round-2 H1; round2/adjudicate.py')
            record = copy.deepcopy(self.intended[slot])
            record['consumer_adopt'] = {'attempted': False}
            self.slot_row(f'{slot}: malformed skip record', slot, record, INCOMPLETE, 'round-3 adjudicate-r3.py')
        # agent-510 e5c23190 (1): the original replay error survives under campaign finalization's error,
        # in the consumer's chain and in the run_step layer; (2): each attack blocks the aggregate.
        for slot in REQUIRED:
            for mode in ATTACKS:
                record = self.attacks[slot, mode]
                self.check_row(f'{slot}: {mode} keeps the original replay error in the consumer chain', True,
                               original_kept(record, mode), 'agent-510 e5c23190 (1)')
            self.check_row(f'{slot}: wrong_replay_binding outer error is the finalization mask', True,
                           (self.attacks[slot, 'wrong_replay_binding']['consumer'].get('error') or '')
                           == 'ValueError: stage receipt identity mismatch', 'agent-510 e5c23190 (1); round-3 H4')
        for mode in ATTACKS:
            controls = {**self.intended, **{slot: self.attacks[slot, mode] for slot in REQUIRED}}
            self.check_row(f'aggregate: {mode} in every required slot blocks controls_refused', False,
                           self.verdict(controls=controls)['controls_refused'], 'agent-510 e5c23190 (2)')
        self.chain_completeness()
        self.slot_row('b_tampered_candidate: candidate file missing, no tamper shown', 'b_tampered_candidate',
                      self.replayed(None, 'missing_candidate_file'), INCOMPLETE,
                      'round-3 adjudicate-r3.py missing actual file')
        self.slot_row('b_tampered_candidate: absent (no candidate delivered)', 'b_tampered_candidate',
                      {'skipped': f'{self.target} absent in the positive receipt'}, INCOMPLETE,
                      'round-1 (9352b0f1 had no candidate)')
        # Round-1 historical record and its replayed mismatch return.
        manifest = json.loads((ROOT / 'evidence/k91-native-positive/evidence-manifest.json').read_text())
        self.check_row('round-1 historical: v1 verdict reproduces the record', manifest['verdict'],
                       c.v1_verdict(manifest), 'round-1 replay.py')
        historical = c.v2_verdict(c.v1_controls_as_v2(manifest))
        self.check_row('round-1 historical: no required control refused at the boundary', False,
                       historical['controls_refused'], 'round-1/2 review')
        # The round-1 record predates the staged original (2c37f645): it is classified by the verifier of
        # its own era, the adapter that wrote and committed it, and today's verifier refuses it.
        self.check_row('round-1 mismatch-replay: the committed not_run report verifies to the recorded value '
                       f'under its era verifier ({ERA[:8]}, pre-staging)', True,
                       self.mismatch_replay(manifest, self.era_adapter()), 'round2/mismatch-replay.py')
        self.check_row('round-1 mismatch-replay: the era verifier refuses an edited copy of the report',
                       'ValueError: routing adapter report identity mismatch',
                       self.outcome(lambda: self.mismatch_replay(
                           manifest, self.era_adapter(), edit=lambda r: r['semantics'].update(reasons=[]))),
                       'ee442 composition')
        from agent_ee import routing_adapter
        self.check_row('round-1 mismatch-replay: today\'s verifier refuses the pre-staging report at its own pin',
                       'ValueError: routing attempt inputs have unknown or missing fields',
                       self.outcome(lambda: self.mismatch_replay(manifest, routing_adapter)), 'ee442 composition')
        self.check_row('round-1 mismatch-replay: today\'s verifier refuses the pre-staging report as stale at the '
                       'production pin', 'StaleRoutingEvidence',
                       self.outcome(lambda: self.mismatch_replay(manifest, routing_adapter, pin=None)).split(':')[0],
                       'ee442 composition')
        base = c.v1_controls_as_v2(manifest)['controls']['a_mismatched_runtime']
        for key in ('router_exit', 'candidate_sha256'):
            record = copy.deepcopy(base)
            del record['verify']['value'][key]
            self.check_row(f'round-2 low level: not_run without {key} is not a refusal', True,
                           c.runtime_state(record) != REFUSED, 'round2/adjudicate.py')
        order = [self.positive[k_]['attempted'] for k_ in ('consumer_run_step', 'consumer_verify', 'consumer_adopt')]
        board = [self.intended['b_tampered_candidate'][k_]['attempted']
                 for k_ in ('consumer_run_step', 'consumer_verify', 'consumer_adopt')]
        self.check_row('round-2 consumer order: positive made run_step, verify, adopt; board refusal skipped adopt',
                       [[True, True, True], [True, True, False]], [order, board], 'round2/consumer-order.py')
        self.check_row('round-3 order independence', True,
                       c.v2_verdict({'positive': self.positive,
                                     'controls': dict(reversed(list(self.intended.items())))}) == verdict,
                       'round-3 adjudicate-r3.py')
        self.check_row('sidecar bare adoption success stays visible', True,
                       self.intended['b_tampered_sidecar']['direct_adopt']['accepted'] is True, 'ruling a01d6f91')
        return self.rows

    def chain_completeness(self):
        """Review round 4 H1: a truncated, dangling, prematurely ended or cyclic exception chain is INCOMPLETE.

        The truncated-tail case is the round-4 Sol reviewer's (reviews/ee442-native-positive-b-sol-r4.md, H1;
        probe root-review/own_probes.py): the actual controls are run while a real unrelated PermissionError
        is being handled, so every chain ends in that captured context; then only that unrelated tail is
        removed. The remaining links are genuine contract refusals, but the last kept link still names a
        context, so the chain is incomplete. The other malformations are built on the intended records."""
        source = 'round-4 H1; root-review/own_probes.py (Sol reviewer)'
        try:
            raise PermissionError('review unrelated outer operation')
        except PermissionError:
            contextual = self.k.run_controls(self.root / 'contextual', self.work, self.decl, self.bind,
                                             self.mismatch, self.make, BOARD)
        truncated_all, interior_all = {}, {}
        for slot in SLOTS:
            full = contextual[slot]
            self.slot_row(f'{slot}: full chain with captured unrelated context', slot, full, INCOMPLETE, source)
            truncated = copy.deepcopy(full)
            for key in OBSERVATIONS:
                chain = (truncated.get(key) or {}).get('exception') or []
                if chain and chain[-1]['type'] == 'PermissionError':
                    chain.pop()
            truncated_all[slot] = truncated
            # agent-510 f7b88794 (a): the inverse of the truncated tail, on the same captured-context record.
            # Every observation keeps all its links, but its first link is null-terminated while further
            # links follow (an interior null). The chain is incomplete, so the control is INCOMPLETE.
            interior = copy.deepcopy(full)
            for key in OBSERVATIONS:
                chain = (interior.get(key) or {}).get('exception') or []
                if len(chain) > 1:
                    chain[0]['link'] = None
            interior_all[slot] = interior
            consumer_chain = interior['consumer'].get('exception') or []
            self.check_row(f'{slot}: interior-null chain has a null link before further links', True,
                           len(consumer_chain) > 1 and consumer_chain[0]['link'] is None, 'agent-510 f7b88794 (a)')
            self.slot_row(f'{slot}: interior null in the captured-context chain (every observation)', slot,
                          interior, INCOMPLETE, 'agent-510 f7b88794 (a)')
            self.check_row(f'{slot}: truncated chain keeps a non-null last link', 'context',
                           (truncated['consumer'].get('exception') or [{}])[-1].get('link'), source)
            self.slot_row(f'{slot}: truncated chain (unrelated context tail removed)', slot, truncated,
                          INCOMPLETE, source)
            for label, change in (('dangling link (terminal names a cause)', self.dangling),
                                  ('missing terminal null', self.unterminated),
                                  ('interior null ends the chain early', self.interior_null),
                                  ('cyclic chain', self.cyclic)):
                record = copy.deepcopy(full if label.startswith('interior') else self.intended[slot])
                chain = record['consumer'].get('exception')  # absent in trees that recorded no chain (57083ef8)
                if chain:
                    change(chain)
                self.slot_row(f'{slot}: {label}', slot, record, INCOMPLETE, 'round-4 H1')
        controls = {**self.intended, **{slot: truncated_all[slot] for slot in REQUIRED}}
        self.check_row('aggregate: truncated chains in every required slot block controls_refused', False,
                       self.verdict(controls=controls)['controls_refused'], source)
        # The same inverse case where every link is a genuine contract refusal: the board's two-link
        # verify-plus-finalization chain ['context', None] with its first link null-terminated early.
        board = copy.deepcopy(self.intended['b_tampered_candidate'])
        chain = board['consumer'].get('exception')
        if chain and len(chain) > 1:
            chain[0]['link'] = None
        self.slot_row('b_tampered_candidate: interior null on the intended two-link refusal chain',
                      'b_tampered_candidate', board, INCOMPLETE, 'agent-510 f7b88794 (a)')
        self.check_row('aggregate: interior null on the intended board chain blocks controls_refused', False,
                       self.verdict(controls={**self.intended, 'b_tampered_candidate': board})['controls_refused'],
                       'agent-510 f7b88794 (a)')
        controls = {**self.intended, **{slot: interior_all[slot] for slot in REQUIRED}}
        self.check_row('aggregate: interior-null chains in every required slot block controls_refused', False,
                       self.verdict(controls=controls)['controls_refused'], 'agent-510 f7b88794 (a)')

    @staticmethod
    def dangling(chain):
        chain[-1]['link'] = 'cause'

    @staticmethod
    def unterminated(chain):
        chain[-1]['link'] = 'context'

    @staticmethod
    def interior_null(chain):
        chain[0]['link'] = None

    @staticmethod
    def cyclic(chain):
        again = copy.deepcopy(chain[0])
        chain[-1]['link'] = 'context'
        again['link'] = None
        chain.append(again)

    def mismatch_replay(self, manifest, adapter, pin=True, edit=None):
        """Whether adapter.verify re-derives the recorded value from the committed not_run report: at the
        report's own pin (the round-2 replay), or at the adapter's production pin when pin is None. edit
        changes a copy of the report first; the committed bytes are only ever read."""
        evidence = ROOT / 'evidence/k91-native-positive/neg-runtime'
        report = json.loads((evidence / 'adapter-report.json').read_text())
        run = self.fresh('mismatch-replay') / 'run'
        (run / 'routing').mkdir(parents=True)
        shutil.copyfile(evidence / 'adapter-report.json', run / 'routing/adapter-report.json')
        if edit is not None:
            edit(report)
            (run / 'routing/adapter-report.json').write_text(json.dumps(report))
        layout = {'pin': report['adapter']['compatibility'].split('@')[1]} if pin else None
        return adapter.verify(run, layout=layout) == manifest['controls']['a_mismatched_runtime']['verify']['value']

    @staticmethod
    def outcome(call):
        try:
            return f'returned {call()!r}'
        except Exception as exc:
            return f'{type(exc).__name__}: {exc}'

    def era_adapter(self):
        """routing_adapter as committed at ERA, loaded from the committed era fixture into a private
        package: the verifier of the round-1 record's own era, never a fallback of today's."""
        package = self.fresh('era') / ERA_PACKAGE
        package.mkdir(parents=True)
        (package / '__init__.py').write_text('')
        for name, data in era_modules().items():
            (package / f'{name}.py').write_bytes(data)
        spec = importlib.util.spec_from_file_location(ERA_PACKAGE, package / '__init__.py',
                                                      submodule_search_locations=[str(package)])
        sys.modules[ERA_PACKAGE] = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(sys.modules[ERA_PACKAGE])
            return importlib.import_module(f'{ERA_PACKAGE}.routing_adapter')
        finally:
            for name in [n for n in sys.modules if n == ERA_PACKAGE or n.startswith(ERA_PACKAGE + '.')]:
                del sys.modules[name]


def era_modules():
    """The ERA_MODULES bytes from ERA_FIXTURE, each refused unless its sha256 is the manifest's."""
    manifest = json.loads((ERA_FIXTURE / 'manifest.json').read_text())
    if manifest.get('commit') != ERA or sorted(manifest.get('modules', {})) != sorted(ERA_MODULES):
        raise ValueError('era fixture manifest does not name ERA and exactly ERA_MODULES')
    modules = {}
    for name in ERA_MODULES:
        data = (ERA_FIXTURE / f'{name}.py').read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest['modules'][name]['sha256']:
            raise ValueError(f'era fixture {name}.py differs from its manifest sha256')
        modules[name] = data
    return modules


ORIGINAL = {'wrong_replay_binding': ('ValueError', 'replayed receipt is not the step and binding the consumer asked for'),
            'invalid_receipt_json': ('JSONDecodeError', 'Expecting property name enclosed in double quotes'),
            'missing_receipt': ('FileNotFoundError', 'receipt.json')}


def original_kept(record, mode):
    """The attack's original replay error is a link of the consumer's recorded chain, raised through the
    replayed run_step, and the run_step layer recorded it too (not only the outer finalization error)."""
    kind, text = ORIGINAL[mode]

    def is_original(link):
        return (isinstance(link, dict) and link.get('type') == kind and text in (link.get('message') or '')
                and 'k91_consumer.py:run_step' in (link.get('frames') or []))
    chain = (record.get('consumer') or {}).get('exception') or []
    layer = (record.get('consumer_run_step') or {}).get('exception') or []
    return any(map(is_original, chain)) and bool(layer) and is_original(layer[-1])


def matches(row):
    return row['expected'] == row['actual'] and row.get('expected_controls_refused') == row.get('actual_controls_refused')


def run_matrix(scripts=ROOT / 'scripts'):
    k, c = load(scripts)
    with tempfile.TemporaryDirectory(prefix='agent-ee-k91-matrix-') as temp:
        matrix = Matrix(k, c, temp)
        try:
            return matrix.run()
        finally:
            matrix.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--scripts', default=str(ROOT / 'scripts'))
    ap.add_argument('--json')
    args = ap.parse_args(argv)
    rows = run_matrix(args.scripts)
    for row in rows:
        aggregate = '' if row['slot'] is None else \
            f" | controls_refused expected {row['expected_controls_refused']} actual {row['actual_controls_refused']}"
        print(f"{'OK  ' if matches(row) else 'DIFF'} | {row['id']} | expected {row['expected']} | actual {row['actual']}"
              f"{aggregate}")
    differ = [row['id'] for row in rows if not matches(row)]
    print(f'matrix: {len(rows)} rows, {len(rows) - len(differ)} as expected, {len(differ)} differ')
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2, default=str) + '\n')
    return 0 if not differ else 1


if __name__ == '__main__':
    sys.exit(main())
