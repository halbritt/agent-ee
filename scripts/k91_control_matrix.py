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
import importlib
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
BOARD = 'project/rc.kicad_pcb'
SIDECAR = 'routing/attempt/routability-experiment.json'
REFUSED, NOT_REFUSED, INCOMPLETE = 'refused', 'not_refused', 'incomplete'
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
        self.rows, self.count = [], 0
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
                self.slot_row(f'{slot}: {mode}', slot, self.plumbing(slot, mode), INCOMPLETE,
                              'round-3 H4' if mode in PLUMBING[:3] else 'mandate (5)')
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
        self.check_row('round-1 mismatch-replay: the committed not_run report verifies to the recorded value', True,
                       self.mismatch_replay(manifest), 'round2/mismatch-replay.py')
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

    def mismatch_replay(self, manifest):
        from agent_ee import routing_adapter
        evidence = ROOT / 'evidence/k91-native-positive/neg-runtime'
        report = json.loads((evidence / 'adapter-report.json').read_text())
        pin = report['adapter']['compatibility'].split('@')[1]
        run = self.fresh('mismatch-replay') / 'run'
        (run / 'routing').mkdir(parents=True)
        shutil.copyfile(evidence / 'adapter-report.json', run / 'routing/adapter-report.json')
        return routing_adapter.verify(run, layout={'pin': pin}) == manifest['controls']['a_mismatched_runtime']['verify']['value']


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
