"""Requirement-map accounting and ordinary scope/3 acceptance boundaries."""
import contextlib
import copy
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_ee.baseline import compile_baseline
from agent_ee.capabilities import KINDS, compile_coverage
from agent_ee.candidate import capture
from agent_ee.cli import run_review, validate_scope
from agent_ee.requirement_map import (KIND_CLASS, OBLIGATIONS_SCHEMA, PROVENANCE, RECEIPT_SCHEMA,
                                      RECEIPT_SCHEMA_2, SCHEMA_2, compile_map, compile_map_file,
                                      coverage_view, decode, method_class, write_map_outputs)
from agent_ee.requirement_scope import Refused, main, sha256
from agent_ee.storage import digest, json_bytes, read_json, write_json
from tests.toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]
POD = ROOT / 'fixtures/telemetry-pod'
MAP = POD / 'requirement-map.json'
RECORD = POD / 'spec-record.json'
SPEC = POD / 'spec.md'


def mapped(declaration, rid):
    return next(e for e in declaration['requirements'] + declaration['prose'] if e['id'] == rid)


class RequirementMapTests(unittest.TestCase):
    def setUp(self):
        self.value = read_json(MAP)
        self.record_raw = RECORD.read_bytes()
        self.spec_raw = SPEC.read_bytes()
        self.record = read_json(RECORD)
        self.files = {self.value['scope'][p]: None for p in ('schematic', 'board')}

    def compile(self, value=None, **kwargs):
        return compile_map(json_bytes(self.value if value is None else value), self.record_raw,
                           self.spec_raw, files=self.files, **kwargs)

    def refused(self, code, rid):
        with self.assertRaises(Refused) as caught:
            self.compile()
        self.assertIn((code, rid), {(f['code'], f['id']) for f in caught.exception.findings})

    def test_requirement_map_complete(self):
        scope, receipt = self.compile()
        expected = {e['id'] for e in self.record['requirements'] + self.record['prose']}
        self.assertEqual({e['id'] for e in receipt['requirements']}, expected)
        self.assertEqual(len(receipt['requirements']), 48)
        # Probe every required ID, rather than a single convenient omission.
        for entry in list(self.value['requirements']):
            with self.subTest(rid=entry['id']):
                broken = copy.deepcopy(self.value)
                broken['requirements'].remove(entry)
                with self.assertRaises(Refused) as caught:
                    self.compile(broken)
                self.assertIn(('unmapped_requirement', entry['id']),
                              {(f['code'], f['id']) for f in caught.exception.findings})

    def test_unknown_kind_refused(self):
        mapped(self.value, 'POD-AC-3')['checks'][0]['kind'] = 'invented_radio_pass'
        self.refused('unknown_kind', 'POD-AC-3')

    def test_limitation_not_a_check(self):
        scope, receipt = self.compile()
        unchecked = [e['id'] for e in receipt['requirements'] if e['mode'] in ('limitation', 'bench')]
        self.assertTrue(unchecked)
        self.assertEqual([c['id'] for c in scope['checks']], ['erc', 'drc'])
        for rid in unchecked:
            self.assertTrue(any(s.startswith(rid + ':') for s in scope['limitations']), rid)
            self.assertNotIn(rid, {r['id'] for r in scope['baseline']['requirements']})
        files = self.provenance_files()
        coverage = compile_coverage(scope, files)
        self.assertEqual(set(coverage['requirements']), {'POD-AC-3'})
        # A limitation cannot be smuggled into acceptance with empty coverage.
        scope['baseline']['requirements'].append({
            'id': 'POD-IF-BLUETOOTH-LE', 'intent': 'acceptance', 'statement': 'BLE range',
            'source': {'path': f'{PROVENANCE}/spec.md', 'locator': 'range'}, 'checks': []})
        with self.assertRaisesRegex(ValueError, 'POD-IF-BLUETOOTH-LE.*no executable bound coverage'):
            compile_coverage(scope, files)

    def provenance_files(self):
        return {**self.files, f'{PROVENANCE}/spec.md': sha256(self.spec_raw),
                f'{PROVENANCE}/spec-record.json': sha256(self.record_raw),
                f'{PROVENANCE}/requirement-map.json': sha256(json_bytes(self.value))}

    def test_gnss_50_ohm_impedance_is_an_explicit_limitation_without_a_check(self):
        scope, receipt = self.compile()
        rid = 'POD-IF-GNSS-ANTENNA'
        entry = next(e for e in receipt['requirements'] if e['id'] == rid)
        self.assertEqual(entry['mode'], 'limitation')
        limitation, = [s for s in scope['limitations'] if s.startswith(rid + ':')]
        for fact in ('50 ohm', 'Impedance remains unverified', 'JLC04161H-7628',
                     'https://jlcpcb.com/pcb-impedance-calculator/',
                     'three W1 calculation submissions', 'solver status 6 and null geometry',
                     'single authorized W2 continuation', 'solver status 0 and valid geometry',
                     'HTTP acceptance is not a successful calculation',
                     'rf-net-constraints.json', 'upper W2 width 0.321716796875 mm',
                     '0.335 mm', 'recorded_not_enforced', '/POD_GNSS_RF',
                     'Unmatched constraint nets are refused',
                     'RF maximum length remains unbound', '15 mm is a test value only'):
            self.assertIn(fact, limitation)
        self.assertNotIn(rid, compile_coverage(scope, self.provenance_files())['requirements'])
        self.assertEqual([c['id'] for c in scope['checks']], ['erc', 'drc'])

    def test_pod_scope_validates(self):
        scope, receipt = self.compile()
        validate_scope(scope)
        baseline = compile_baseline(scope, self.provenance_files())
        self.assertEqual(receipt['baseline_id'], baseline['baseline_id'])
        self.assertEqual(scope['schema'], 'agent-ee.scope/3')
        self.assertEqual(baseline['schema'], 'agent-ee.baseline/2')
        self.assertEqual(receipt, self.compile()[1])
        for entry in receipt['requirements']:
            if entry['mode'] == 'checks':
                for check in entry['checks']:
                    self.assertIn(check['kind'], KINDS)
                    self.assertTrue(check['can_evaluate_bound'])
                    self.assertTrue(check['predicates'])
        changed_files = self.provenance_files()
        changed_files[f'{PROVENANCE}/requirement-map.json'] = 'f' * 64
        self.assertNotEqual(compile_baseline(scope, changed_files)['baseline_id'], receipt['baseline_id'])

    def test_pod_bench_items_cannot_be_checks_or_limitations(self):
        for rid in ('POD-AC-8', 'POD-AC-9'):
            for mode, detail in (('checks', mapped(self.value, 'POD-AC-3')['checks']),
                                 ('limitation', {'reason': 'bench deferred'})):
                broken = copy.deepcopy(self.value)
                entry = mapped(broken, rid)
                entry.clear()
                entry.update(id=rid, **{mode: detail})
                with self.subTest(rid=rid, mode=mode), self.assertRaisesRegex(Refused, rid + '.*bench'):
                    self.compile(broken)
        scope, receipt = self.compile()
        for rid in ('POD-AC-8', 'POD-AC-9'):
            entry = next(e for e in receipt['requirements'] if e['id'] == rid)
            self.assertEqual(entry['mode'], 'bench')
            self.assertEqual(set(entry['mapping']), {'criterion', 'procedure'})

    def test_duplicate_unknown_and_multiple_dispositions_refused(self):
        self.value['requirements'].append(copy.deepcopy(self.value['requirements'][0]))
        self.refused('duplicate_mapping', self.value['requirements'][0]['id'])
        self.value = read_json(MAP)
        self.value['requirements'][0]['id'] = 'POD-INVENTED'
        self.refused('unknown_requirement', 'POD-INVENTED')
        self.value = read_json(MAP)
        self.value['requirements'][0]['bench'] = {'criterion': 'x', 'procedure': 'y'}
        self.refused('mapping', self.value['requirements'][0]['id'])
        self.value = read_json(MAP)
        mapped(self.value, 'POD-AC-3')['checks'] = []
        self.refused('unmapped_requirement', 'POD-AC-3')

    def test_existing_predicate_validators_and_prerequisites_are_used(self):
        mapped(self.value, 'POD-AC-3')['checks'][1] = {
            'id': 'width', 'kind': 'geometry', 'stage': 'verification',
            'params': {'criterion': 'copper-width/1', 'covered_primitives': ['PCB_TRACK', 'PCB_ARC'],
                       'exclude_nets': [], 'min_track_width_mm': -1}}
        with self.assertRaisesRegex(ValueError, 'unsupported capability width'):
            self.compile()
        self.value = read_json(MAP)
        mapped(self.value, 'POD-AC-3')['checks'] = [
            {'id': 'netlist', 'kind': 'netlist', 'stage': 'verification', 'params': {}}]
        with self.assertRaisesRegex(ValueError, 'POD-AC-3.*no executable bound coverage'):
            self.compile()
        self.value = read_json(MAP)
        mapped(self.value, 'POD-AC-3')['checks'].append({
            'id': 'fab', 'kind': 'fab_export', 'stage': 'verification', 'params': {'netlist_check': 'absent'}})
        with self.assertRaisesRegex(ValueError, 'unsupported capability fab'):
            self.compile()

    def test_no_check_vocabulary_copy_shared_checks_and_more_registered_kinds(self):
        # A kind absent from the legacy six-kind compiler still uses its actual
        # capability contract. No new registration or shadow validator is needed.
        params = {}
        for rid in ('POD-CD-MCU-AND-RADIO', 'POD-CD-CAN-TRANSCEIVER'):
            entry = mapped(self.value, rid)
            entry.clear()
            entry.update(id=rid, checks=[{'id': 'bindings', 'kind': 'part_binding',
                                         'params': params, 'stage': 'verification'}])
        scope, receipt = self.compile()
        self.assertEqual([c['id'] for c in scope['checks']], ['bindings', 'erc', 'drc'])
        for rid in ('POD-CD-MCU-AND-RADIO', 'POD-CD-CAN-TRANSCEIVER'):
            self.assertEqual(next(r for r in scope['baseline']['requirements'] if r['id'] == rid)['checks'], ['bindings'])
        mapped(self.value, 'POD-CD-CAN-TRANSCEIVER')['checks'][0]['params'] = {'wrong': True}
        self.refused('duplicate_check', 'POD-CD-CAN-TRANSCEIVER')

    def test_all_limitations_never_compile_to_an_empty_pass(self):
        entry = mapped(self.value, 'POD-AC-3')
        entry.clear()
        entry.update(id='POD-AC-3', limitation={'reason': 'No captured policy'})
        with self.assertRaisesRegex(ValueError, 'requires at least one acceptance requirement'):
            self.compile()

    def test_hardware_interface_uses_captured_contract_and_exact_source_binding(self):
        rid = 'POD-IF-CAN1'
        source = next(r for r in self.record['requirements'] if r['id'] == rid)
        entry = mapped(self.value, rid)
        entry.clear()
        entry.update(id=rid, checks=[
            {'id': 'netlist', 'kind': 'netlist', 'stage': 'verification', 'params': {}},
            {'id': 'can', 'kind': 'hardware_interface', 'stage': 'verification',
             'params': {'netlist_check': 'netlist', 'contract': 'contract.json'}}])
        contract = read_json(ROOT / 'fixtures/hardware-interface/contract.json')
        for invariant in contract['invariants']:
            invariant.update(requirement=rid, source={'path': f'{PROVENANCE}/spec.md',
                             'locator': json.dumps(source['locator'], sort_keys=True)})
        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp)
            for field, name in (('schematic', 'interface.kicad_sch'), ('board', 'interface.kicad_pcb')):
                destination = inputs / self.value['scope'][field]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / 'fixtures/hardware-interface' / name, destination)
            write_json(inputs / 'contract.json', contract)
            files = {p.relative_to(inputs).as_posix(): digest(p) for p in inputs.rglob('*') if p.is_file()}
            scope, receipt = compile_map(json_bytes(self.value), self.record_raw, self.spec_raw,
                                         files=files, inputs=inputs)
            descriptor = next(e for e in receipt['requirements'] if e['id'] == rid)['checks'][1]
            self.assertTrue(descriptor['can_evaluate_bound'])
            self.assertIn('contract.json', descriptor['immutable_inputs'])
            self.assertFalse((inputs / PROVENANCE).exists())
            (inputs / 'contract.json').write_bytes(b'{}')
            with self.assertRaisesRegex(ValueError, 'hash changed'):
                compile_map(json_bytes(self.value), self.record_raw, self.spec_raw, files=files, inputs=inputs)

    def test_source_change_before_output_and_malformed_cli_leave_no_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'map.json'
            path.write_bytes(MAP.read_bytes())
            scope, receipt = compile_map_file(path, RECORD, SPEC)
            path.write_bytes(path.read_bytes() + b'\n')
            with self.assertRaisesRegex(ValueError, 'changed after compilation'):
                write_map_outputs(root / 'changed', path, RECORD, SPEC, scope, receipt)
            self.assertFalse((root / 'changed').exists())
            for value in ({}, {'schema': 'agent-ee.requirement-map/1', 'scope': None,
                               'requirements': [], 'prose': [], 'spec_record_sha256': ''}):
                write_json(path, value)
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                    main([str(path), '--spec-record', str(RECORD), '--spec', str(SPEC),
                          '--out', str(root / 'malformed')])
                self.assertEqual(caught.exception.code, 2)
                self.assertFalse((root / 'malformed').exists())

    def test_prose_requires_explicit_accounting_no_heading_promotion(self):
        for paragraph in self.record['prose']:
            broken = copy.deepcopy(self.value)
            broken['prose'].remove(mapped(broken, paragraph['id']))
            with self.subTest(rid=paragraph['id']), self.assertRaisesRegex(Refused, paragraph['id']):
                self.compile(broken)
        scope, receipt = self.compile()
        self.assertEqual(next(e for e in receipt['requirements'] if e['id'] == 'POD-AC-P1')['mode'], 'context')
        # Revision-2 supply statements are ordinary limitations, with no
        # contradictory-source or rationale-only escape hatch.
        for rid in ('POD-FN-P2', 'POD-IF-SENSOR-SUPPLY'):
            self.assertEqual(next(e for e in receipt['requirements'] if e['id'] == rid)['mode'], 'limitation')

    def test_prose_checks_keep_the_authored_map_order(self):
        for rid, cid in (('POD-FN-P1', 'first-prose'), ('POD-FN-P2', 'second-prose')):
            entry = mapped(self.value, rid)
            entry.clear()
            entry.update(id=rid, checks=[{'id': cid, 'kind': 'erc', 'params': {}, 'stage': 'verification'}])
        # Move the second paragraph's declaration before the first without
        # changing the immutable spec or using a compiler-inferred sort.
        second = mapped(self.value, 'POD-FN-P2')
        self.value['prose'].remove(second)
        self.value['prose'].insert(0, second)
        scope, _ = self.compile()
        self.assertEqual([c['id'] for c in scope['checks']],
                         ['erc', 'drc', 'second-prose', 'first-prose'])

    def spans(self):
        paragraph = next(p for p in self.record['prose'] if p['id'] == 'POD-FN-P2')
        start, end = (paragraph['locator'][k] for k in ('byte_start', 'byte_end'))
        split = self.spec_raw.index(b'. ', start, end) + 1
        entry = mapped(self.value, 'POD-FN-P2')
        entry.clear()
        entry.update(id='POD-FN-P2', spans=[
            {'byte_start': start, 'byte_end': split,
             'mapping': {'id': 'POD-FN-P2-RELAY', 'limitation': {'reason': 'Firmware forwarding unverified'}}},
            {'byte_start': split + 1, 'byte_end': end,
             'mapping': {'id': 'POD-FN-P2-ANALOG', 'limitation': {'reason': 'Ratiometry unverified'}}}])
        return entry['spans']

    def requirement_spans(self, rid='POD-AC-3'):
        source = next(r for r in self.record['requirements'] if r['id'] == rid)
        start, end = (source['locator'][k] for k in ('byte_start', 'byte_end'))
        entry = mapped(self.value, rid)
        if rid == 'POD-AC-3':
            split = self.spec_raw.index(b'. ', start, end) + 1
            spans = [dict(byte_start=start, byte_end=split,
                          mapping=dict(id=rid + '-NATIVE', checks=copy.deepcopy(entry['checks']))),
                     dict(byte_start=split + 1, byte_end=end,
                          mapping=dict(id=rid + '-WAIVERS', limitation=dict(reason='Disposition not verified')))]
        else:
            spans = [dict(byte_start=start, byte_end=end,
                          mapping=dict(id=rid + '-PHYSICAL', bench=copy.deepcopy(entry['bench'])))]
        entry.clear()
        entry.update(id=rid, spans=spans)
        return spans

    def test_requirement_clauses_keep_partial_coverage_and_exact_source(self):
        spans = self.requirement_spans()
        scope, receipt = self.compile()
        parts = [r for r in receipt['requirements'] if r.get('parent_id') == 'POD-AC-3']
        self.assertEqual([r['id'] for r in parts], ['POD-AC-3-NATIVE', 'POD-AC-3-WAIVERS'])
        self.assertEqual([r['mode'] for r in parts], ['checks', 'limitation'])
        self.assertEqual([r['id'] for r in scope['baseline']['requirements']], ['POD-AC-3-NATIVE'])
        self.assertTrue(any(s.startswith('POD-AC-3-WAIVERS: NOT CHECKED:') for s in scope['limitations']))
        for part, span in zip(parts, spans):
            raw = self.spec_raw[span['byte_start']:span['byte_end']]
            self.assertEqual(part['text_sha256'], sha256(raw))
            self.assertEqual(part['locator']['byte_start'], span['byte_start'])
            self.assertEqual(part['locator']['byte_end'], span['byte_end'])
        requirement, = scope['baseline']['requirements']
        self.assertEqual(requirement['statement'].encode(), self.spec_raw[spans[0]['byte_start']:spans[0]['byte_end']])
        self.assertEqual(json.loads(requirement['source']['locator']), parts[0]['locator'])

    def test_requirement_clause_gaps_overlap_empty_and_outside_refuse(self):
        for mutation, code in (
                (lambda s: s[0].update(byte_start=s[0]['byte_start'] + 1), 'uncovered_requirement'),
                (lambda s: s[1].update(byte_start=s[0]['byte_end'] - 1), 'requirement_span'),
                (lambda s: s[1].update(byte_end=s[1]['byte_end'] + 1), 'requirement_span'),
                (lambda s: s[0].update(byte_end=s[0]['byte_start']), 'requirement_span'),
                (lambda s: s[0].update(byte_start=True), 'requirement_span'),
                (lambda s: s.pop(), 'uncovered_requirement')):
            self.value = read_json(MAP)
            spans = self.requirement_spans()
            mutation(spans)
            with self.subTest(code=code):
                self.refused(code, 'POD-AC-3')

    def test_requirement_clause_cannot_reclassify_as_context(self):
        span = self.requirement_spans()[1]
        span['mapping'] = dict(id='POD-AC-3-WAIVERS', context=dict(reason='Not checked'))
        self.refused('mapping', 'POD-AC-3-WAIVERS')

    def test_clause_ids_cannot_alias_parent_other_sources_or_each_other(self):
        for rid in ['POD-AC-3', 'POD-IF-CAN1', 'POD-FN-P2', 'POD-AC-3-NATIVE']:
            self.value = read_json(MAP)
            self.requirement_spans()[1]['mapping']['id'] = rid
            with self.subTest(id=rid):
                self.refused('duplicate_mapping', rid)

    def test_bench_only_parent_restriction_survives_partition(self):
        for rid in ['POD-AC-8', 'POD-AC-9']:
            self.value = read_json(MAP)
            self.requirement_spans(rid)
            _, receipt = self.compile()
            child = next(r for r in receipt['requirements'] if r.get('parent_id') == rid)
            self.assertEqual(child['mode'], 'bench')
            for mode in ['checks', 'limitation']:
                self.value = read_json(MAP)
                span = self.requirement_spans(rid)[0]
                span['mapping'] = dict(id=rid + '-PHYSICAL', **{mode:
                    mapped(self.value, 'POD-AC-3')['checks'] if mode == 'checks' else dict(reason='Deferred')})
                with self.subTest(id=rid, mode=mode):
                    self.refused('bench_only', rid)

    def test_declared_prose_spans_retain_exact_locators_hashes_and_parent(self):
        self.spans()
        _, receipt = self.compile()
        parts = [e for e in receipt['requirements'] if e.get('parent_id') == 'POD-FN-P2']
        self.assertEqual(len(parts), 2)
        for part in parts:
            first, last = (part['locator'][k] for k in ('byte_start', 'byte_end'))
            self.assertEqual(part['text_sha256'], sha256(self.spec_raw[first:last]))

    def test_uncovered_overlapping_outside_and_colliding_prose_spans_refused(self):
        spans = self.spans()
        spans[0]['byte_start'] += 1
        self.refused('uncovered_prose', 'POD-FN-P2')
        self.value = read_json(MAP)
        spans = self.spans()
        spans[1]['byte_start'] = spans[0]['byte_end'] - 1
        self.refused('prose_span', 'POD-FN-P2')
        self.value = read_json(MAP)
        spans = self.spans()
        spans[1]['byte_end'] += 1
        self.refused('prose_span', 'POD-FN-P2')
        self.value = read_json(MAP)
        spans = self.spans()
        spans[1]['mapping']['id'] = 'POD-IF-CAN1'
        self.refused('duplicate_mapping', 'POD-IF-CAN1')

    def test_changed_sources_strict_json_and_reserved_destinations_refused(self):
        self.value['spec_record_sha256'] = 'f' * 64
        self.refused('spec_record_hash', 'spec-record')
        self.value = read_json(MAP)
        self.spec_raw += b'\n'
        with self.assertRaisesRegex(ValueError, 'spec bytes differ'):
            self.compile()
        self.spec_raw = SPEC.read_bytes()
        with self.assertRaisesRegex(ValueError, 'duplicate JSON key'):
            decode(b'{"schema": 1, "schema": 2}')
        with self.assertRaises(ValueError):
            decode(b'{"x": NaN}')
        self.files[f'{PROVENANCE}/untrusted'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'reserved'):
            self.compile()
        del self.files[f'{PROVENANCE}/untrusted']
        self.value['scope']['inputs'].append({'path': 'x', 'at': PROVENANCE})
        with self.assertRaisesRegex(ValueError, 'reserved'):
            self.compile()

    def test_cli_outputs_and_refusal_without_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'compiled'
            argv = [str(MAP), '--spec-record', str(RECORD), '--spec', str(SPEC), '--out', str(output)]
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(main(argv), 0)
            receipt = read_json(output / 'receipt.json')
            self.assertEqual(receipt['scope_sha256'], digest(output / 'scope.json'))
            self.assertEqual(json.loads(stdout.getvalue())['baseline_id'], receipt['baseline_id'])
            for name, path in (('spec.md', SPEC), ('spec-record.json', RECORD), ('requirement-map.json', MAP)):
                self.assertEqual((output / PROVENANCE / name).read_bytes(), path.read_bytes())
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                main(argv)
            self.assertEqual(caught.exception.code, 2)
            broken = Path(tmp) / 'broken.json'
            self.value['requirements'].pop(0)
            write_json(broken, self.value)
            argv[0], argv[-1] = str(broken), str(Path(tmp) / 'refused')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(argv), 1)
            self.assertFalse((Path(tmp) / 'refused').exists())

    def test_module_cli_refusal_is_structured_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            declaration = read_json(MAP)
            declaration['requirements'].pop(0)
            write_json(root / 'broken.json', declaration)
            process = subprocess.run(
                [sys.executable, '-m', 'agent_ee.requirement_scope', str(root / 'broken.json'),
                 '--spec-record', str(RECORD), '--spec', str(SPEC), '--out', str(root / 'refused')],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, 1, process.stderr)
            self.assertEqual(process.stderr, '')
            report = json.loads(process.stdout)
            self.assertEqual(report['outcome'], 'refused')
            self.assertEqual(report['findings'][0]['id'], read_json(MAP)['requirements'][0]['id'])
            self.assertFalse((root / 'refused').exists())


class TypedMapTests(unittest.TestCase):
    """AGENTEE-521 part A: obligations/1 spans, map/2 modes, receipt/2 and coverage_view."""

    def setUp(self):
        self.value = read_json(MAP)
        self.value['schema'] = SCHEMA_2
        self.record_raw = RECORD.read_bytes()
        self.spec_raw = SPEC.read_bytes()
        self.record = read_json(RECORD)
        self.files = {self.value['scope'][p]: None for p in ('schematic', 'board')}
        self.obligations = self.derive_obligations()

    def derive_obligations(self):
        """One whole-clause span per captured clause, kinds taken from the fixture's dispositions."""
        clauses = []
        for source in self.record['requirements']:
            entry = mapped(self.value, source['id'])
            kind = 'physical' if 'bench' in entry else 'executable'
            clauses.append(dict(id=source['id'], spans=[self.span(source, kind)]))
        for source in self.record['prose']:
            entry = mapped(self.value, source['id'])
            kind = 'context' if 'context' in entry else 'physical' if 'bench' in entry else 'executable'
            clauses.append(dict(id=source['id'], spans=[self.span(source, kind)]))
        return dict(schema=OBLIGATIONS_SCHEMA, spec_sha256=sha256(self.spec_raw), clauses=clauses)

    def span(self, source, kind, start=None, end=None):
        start = source['locator']['byte_start'] if start is None else start
        end = source['locator']['byte_end'] if end is None else end
        return dict(byte_start=start, byte_end=end, text_sha256=sha256(self.spec_raw[start:end]), obligation=kind)

    def clause(self, rid):
        return next(c for c in self.obligations['clauses'] if c['id'] == rid)

    def source(self, rid):
        return next(r for r in self.record['requirements'] + self.record['prose'] if r['id'] == rid)

    def compile(self, value=None, obligations=None):
        obligations = self.obligations if obligations is None else obligations
        return compile_map(json_bytes(self.value if value is None else value), self.record_raw, self.spec_raw,
                           files=self.files, obligations_raw=None if obligations is False else json_bytes(obligations))

    def refused(self, code, rid, **kwargs):
        with self.assertRaises(Refused) as caught:
            self.compile(**kwargs)
        self.assertIn((code, rid), {(f['code'], f['id']) for f in caught.exception.findings})

    def legacy(self):
        """The unmodified committed map/1 fixture, the compatibility control."""
        return compile_map(MAP.read_bytes(), self.record_raw, self.spec_raw, files=self.files)

    def test_typed_map_compiles_to_the_same_scope_with_a_bound_receipt(self):
        scope, receipt = self.compile()
        legacy_scope, legacy_receipt = self.legacy()
        self.assertEqual(json_bytes(scope), json_bytes(legacy_scope))
        self.assertEqual(legacy_receipt['schema'], RECEIPT_SCHEMA)
        self.assertNotIn('obligation', legacy_receipt['requirements'][0])
        self.assertNotIn('method_binding', legacy_receipt)
        self.assertEqual(receipt['schema'], RECEIPT_SCHEMA_2)
        self.assertEqual(receipt['method_binding'], 'typed')
        self.assertEqual(receipt['obligations_sha256'], sha256(json_bytes(self.obligations)))
        self.assertEqual(receipt['scope_sha256'], legacy_receipt['scope_sha256'])
        # The obligations bytes are a captured provenance input, so the baseline identity differs.
        self.assertNotEqual(receipt['baseline_id'], legacy_receipt['baseline_id'])
        for row in receipt['requirements']:
            with self.subTest(rid=row['id']):
                self.assertIn(row['obligation'], ('executable', 'physical', 'context'))
                self.assertEqual(row['obligation_text_sha256'], row['text_sha256'])
                self.assertEqual(bool(row['bound_ids']), row['mode'] == 'checks')
        native = next(r for r in receipt['requirements'] if r['id'] == 'POD-AC-3')
        self.assertEqual(native['bound_ids'], [c['id'] for c in mapped(self.value, 'POD-AC-3')['checks']])

    def test_schema_and_obligation_presence_must_agree(self):
        self.refused('obligations_required', 'obligations', obligations=False)
        value = copy.deepcopy(self.value)
        value['schema'] = 'agent-ee.requirement-map/1'
        self.refused('obligations_unexpected', 'obligations', value=value)
        # Typed modes are not a map/1 vocabulary: the existing exactly-one refusal stands.
        entry = mapped(value, 'POD-FN-LEAN-AND-PITCH-ANGLE')
        entry.clear()
        entry.update(id='POD-FN-LEAN-AND-PITCH-ANGLE', operational=dict(criterion='public_build_and_resume'))
        with self.assertRaises(Refused) as caught:
            compile_map(json_bytes(value), self.record_raw, self.spec_raw, files=self.files)
        self.assertIn(('mapping', 'POD-FN-LEAN-AND-PITCH-ANGLE'),
                      {(f['code'], f['id']) for f in caught.exception.findings})

    def test_operational_mode_needs_a_supported_criterion_and_stays_visibly_pending(self):
        rid = 'POD-FN-LEAN-AND-PITCH-ANGLE'
        entry = mapped(self.value, rid)
        entry.clear()
        entry.update(id=rid, operational=dict(criterion='public_build_and_resume'))
        self.clause(rid)['spans'][0]['obligation'] = 'operational'
        scope, receipt = self.compile()
        row = next(r for r in receipt['requirements'] if r['id'] == rid)
        self.assertEqual((row['mode'], row['obligation'], row['bound_ids']), ('operational', 'operational', []))
        self.assertTrue(any(s.startswith(rid + ': OPERATIONAL PENDING: public_build_and_resume')
                            for s in scope['limitations']))
        self.assertNotIn(rid, [r['id'] for r in scope['baseline']['requirements']])
        for criterion in ('resume', 'A prose sentence saying the build resumed', ''):
            with self.subTest(criterion=criterion):
                entry['operational'] = dict(criterion=criterion)
                self.refused('unsupported_criterion', rid)
        entry['operational'] = dict(criterion='public_build_and_resume')
        self.clause(rid)['spans'][0]['obligation'] = 'executable'
        self.refused('incompatible_evidence_method', rid)

    def test_mixed_method_spans_bind_each_clause_byte_range(self):
        rid = 'POD-AC-3'
        source = self.source(rid)
        start, end = (source['locator'][k] for k in ('byte_start', 'byte_end'))
        split = self.spec_raw.index(b'. ', start, end) + 1
        entry = mapped(self.value, rid)
        checks = copy.deepcopy(entry['checks'])
        entry.clear()
        entry.update(id=rid, spans=[
            dict(byte_start=start, byte_end=split, mapping=dict(id=rid + '-NATIVE', checks=checks)),
            dict(byte_start=split + 1, byte_end=end,
                 mapping=dict(id=rid + '-JUDGMENT', judgment=dict(record='judgments/pod-ac-3.json')))])
        self.clause(rid)['spans'] = [self.span(source, 'executable', start, split),
                                     self.span(source, 'judgment', split + 1, end)]
        scope, receipt = self.compile()
        parts = [r for r in receipt['requirements'] if r.get('parent_id') == rid]
        self.assertEqual([(r['id'], r['mode'], r['obligation']) for r in parts],
                         [(rid + '-NATIVE', 'checks', 'executable'), (rid + '-JUDGMENT', 'judgment', 'judgment')])
        for part, (a, b) in zip(parts, ((start, split), (split + 1, end))):
            self.assertEqual(part['obligation_text_sha256'], sha256(self.spec_raw[a:b]))
            self.assertEqual((part['locator']['byte_start'], part['locator']['byte_end']), (a, b))
        self.assertEqual([r['id'] for r in scope['baseline']['requirements']], [rid + '-NATIVE'])
        self.assertTrue(any(s.startswith(rid + '-JUDGMENT: JUDGMENT PENDING: record judgments/pod-ac-3.json')
                            for s in scope['limitations']))
        # A map may not merge the two obligation spans back into one clause mapping.
        entry.clear()
        entry.update(id=rid, checks=checks)
        self.refused('span_alignment', rid)
        # Nor may it split an obligation span that was authored whole.
        self.clause(rid)['spans'] = [self.span(source, 'executable')]
        entry.clear()
        entry.update(id=rid, spans=[
            dict(byte_start=start, byte_end=split, mapping=dict(id=rid + '-NATIVE', checks=checks)),
            dict(byte_start=split + 1, byte_end=end,
                 mapping=dict(id=rid + '-REST', limitation=dict(reason='Disposition not verified')))])
        self.refused('span_alignment', rid + '-NATIVE')

    def test_obligation_negatives_refuse_with_the_clause_named(self):
        rid, bench = 'POD-AC-3', 'POD-AC-8'
        prose = next(r['id'] for r in self.record['prose'] if 'limitation' in mapped(self.value, r['id']))
        source = self.source(rid)
        start, end = (source['locator'][k] for k in ('byte_start', 'byte_end'))
        split = self.spec_raw.index(b'. ', start, end) + 1

        def drop(o):
            o['clauses'] = [c for c in o['clauses'] if c['id'] != rid]

        def overlap(o):
            c = next(c for c in o['clauses'] if c['id'] == rid)
            c['spans'] = [self.span(source, 'executable', start, split + 4),
                          self.span(source, 'executable', split + 1, end)]

        def gap(o):
            c = next(c for c in o['clauses'] if c['id'] == rid)
            c['spans'] = [self.span(source, 'executable', start, split - 3),
                          self.span(source, 'executable', split + 1, end)]

        def outside(o):
            c = next(c for c in o['clauses'] if c['id'] == rid)
            c['spans'] = [self.span(source, 'executable', start, end + 1)]

        def drift(o):
            c = next(c for c in o['clauses'] if c['id'] == rid)
            c['spans'][0]['text_sha256'] = sha256(b'edited clause')

        def wrong_kind(o):
            next(c for c in o['clauses'] if c['id'] == rid)['spans'][0]['obligation'] = 'physical'

        def unknown_kind(o):
            next(c for c in o['clauses'] if c['id'] == rid)['spans'][0]['obligation'] = 'any-method'

        def context_for_requirement(o):
            next(c for c in o['clauses'] if c['id'] == rid)['spans'][0]['obligation'] = 'context'

        def context_for_prose_mapped_as_limitation(o):
            next(c for c in o['clauses'] if c['id'] == prose)['spans'][0]['obligation'] = 'context'

        def bench_item_not_physical(o):
            next(c for c in o['clauses'] if c['id'] == bench)['spans'][0]['obligation'] = 'executable'

        def unknown_id(o):
            o['clauses'].append(dict(id='POD-NOPE-1', spans=[self.span(source, 'executable')]))

        def duplicate(o):
            o['clauses'].append(copy.deepcopy(next(c for c in o['clauses'] if c['id'] == rid)))

        def spec_hash(o):
            o['spec_sha256'] = sha256(b'other spec')

        cases = ((drop, 'obligation_missing', rid), (overlap, 'obligation_partition', rid),
                 (gap, 'obligation_partition', rid), (outside, 'obligation_partition', rid),
                 (drift, 'obligation_text_drift', rid), (wrong_kind, 'incompatible_evidence_method', rid),
                 (unknown_kind, 'obligation_kind', rid), (context_for_requirement, 'obligation_kind', rid),
                 (context_for_prose_mapped_as_limitation, 'incompatible_evidence_method', prose),
                 (bench_item_not_physical, 'bench_only', bench),
                 (unknown_id, 'obligation_unknown_id', 'POD-NOPE-1'), (duplicate, 'duplicate_mapping', rid),
                 (spec_hash, 'obligation_spec', 'obligations'))
        for mutate, code, named in cases:
            with self.subTest(case=mutate.__name__):
                obligations = copy.deepcopy(self.obligations)
                mutate(obligations)
                self.refused(code, named, obligations=obligations)

    def test_coverage_view_classifies_executed_kinds_and_keeps_typed_modes_pending(self):
        rid = 'POD-AC-3'
        op = 'POD-FN-LEAN-AND-PITCH-ANGLE'
        entry = mapped(self.value, op)
        entry.clear()
        entry.update(id=op, operational=dict(criterion='public_build_and_resume'))
        self.clause(op)['spans'][0]['obligation'] = 'operational'
        scope, receipt = self.compile()
        kinds = {c['id']: c['kind'] for c in scope['checks']}
        bound = next(r for r in receipt['requirements'] if r['id'] == rid)['bound_ids']
        self.assertTrue(bound)
        rows = [dict(id=cid, kind=kinds[cid], execution='ok', outcome='pass') for cid in bound]
        view = coverage_view(receipt, rows)
        self.assertEqual(view[rid]['status'], 'pass')
        executed = view[rid]['spans'][0]['executed']
        self.assertEqual([e['class'] for e in executed], [KIND_CLASS[kinds[cid]] for cid in bound])
        self.assertEqual(view[op]['status'], 'pending')
        self.assertIn('build consumer', view[op]['spans'][0]['reason'])
        self.assertEqual(view['POD-AC-8']['status'], 'pending')
        context = next(r['id'] for r in receipt['requirements'] if r['mode'] == 'context')
        self.assertEqual(view[context]['status'], 'not_applicable')
        limitation = next(r['id'] for r in receipt['requirements'] if r['mode'] == 'limitation')
        self.assertEqual(view[limitation]['status'], 'failed')
        self.assertTrue(view[limitation]['spans'][0]['reason'].startswith('NOT CHECKED: '))
        # One defect fails the clause; a missing row is pending; an unknown executed kind covers nothing.
        failed = coverage_view(receipt, [dict(r, outcome='defect') if r['id'] == bound[0] else r for r in rows])
        self.assertEqual(failed[rid]['status'], 'failed')
        self.assertEqual(coverage_view(receipt, rows[1:])[rid]['status'], 'pending')
        unknown = coverage_view(receipt, [dict(r, kind='not-a-kind') if r['id'] == bound[0] else r for r in rows])
        self.assertEqual(unknown[rid]['status'], 'unknown')
        self.assertIn('unclassified', unknown[rid]['spans'][0]['reason'])
        self.assertEqual(method_class('not-a-kind'), 'unclassified')
        self.assertEqual(coverage_view(receipt, [dict(r, execution='unsupported', outcome='unknown')
                                                 for r in rows])[rid]['status'], 'unknown')
        with self.assertRaises(ValueError):
            coverage_view(receipt, rows + rows[:1])
        # A receipt/1 carries no binding: every clause is unknown, never silently passing.
        _, legacy_receipt = self.legacy()
        legacy_view = coverage_view(legacy_receipt, rows)
        self.assertEqual({v['status'] for v in legacy_view.values()}, {'unknown'})
        self.assertIn('legacy map/1', legacy_view[rid]['spans'][0]['reason'])

    def test_file_entry_points_carry_obligations_into_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            map_path, obligations_path = root / 'requirement-map.json', root / 'obligations.json'
            write_json(map_path, self.value)
            write_json(obligations_path, self.obligations)
            scope, receipt = compile_map_file(map_path, RECORD, SPEC, obligations_path=obligations_path)
            self.assertEqual(receipt['schema'], RECEIPT_SCHEMA_2)
            with self.assertRaises(Refused):
                compile_map_file(map_path, RECORD, SPEC)
            write_map_outputs(root / 'out', map_path, RECORD, SPEC, scope, receipt, obligations_path=obligations_path)
            self.assertEqual((root / 'out' / PROVENANCE / 'obligations.json').read_bytes(),
                             obligations_path.read_bytes())
            with self.assertRaises(ValueError):
                write_map_outputs(root / 'out2', map_path, RECORD, SPEC, scope, receipt)
            self.assertFalse((root / 'out2').exists())
            legacy_scope, legacy_receipt = self.legacy()
            with self.assertRaises(ValueError):
                write_map_outputs(root / 'out3', MAP, RECORD, SPEC, legacy_scope, legacy_receipt,
                                  obligations_path=obligations_path)


class NativeMapTests(unittest.TestCase):
    def test_limitations_survive_capture_and_a_checks_only_run(self):
        require_toolchain(native=True)
        with tempfile.TemporaryDirectory(prefix='agent-ee-map-native-') as tmp:
            root = Path(tmp)
            captured = root / 'captured'
            shutil.copytree(ROOT / 'fixtures/rc/control', captured / 'project')
            declaration = read_json(MAP)
            scope = declaration['scope']
            scope.update(schematic='project/rc_filter.kicad_sch', board='project/rc_filter.kicad_pcb')
            scope['mutable_inputs'] = [scope['schematic'], scope['board']]
            scope['inputs'] = [{'path': str(captured / 'project'), 'at': 'project'}]
            scope['inputs'] += read_json(ROOT / 'fixtures/rc/control.scope.json')['inputs'][2:]
            # The map template's requirement inputs are immutable baseline sources.
            criteria = captured / 'pod-criteria.json'
            write_json(criteria, {str(i): ['POD-AC-3'] if i == 3 else [] for i in range(1, 10)})
            scope['inputs'].append({'path': str(criteria), 'at': 'pod-criteria.json'})
            scope.setdefault('requirements', []).append('pod-criteria.json')
            scope['stages']['subjects']['verification'] = {
                'path': scope['schematic'], 'kind': 'symbols', 'references': ['R1', 'C1', 'J1']}
            path = root / 'map.json'
            write_json(path, declaration)
            compiled, receipt = compile_map_file(path, RECORD, SPEC, inputs=captured)
            output = root / 'compiled'
            write_map_outputs(output, path, RECORD, SPEC, compiled, receipt)
            # The compiler's baseline identity is reproduced by ordinary capture.
            run = root / 'capture'
            run.mkdir()
            capture(output / 'scope.json', run)
            self.assertEqual(read_json(run / 'baseline.json')['baseline_id'], receipt['baseline_id'])
            with contextlib.redirect_stdout(io.StringIO()):
                run_review(output / 'scope.json', root / 'review', 120, checks_only=True)
            result = read_json(root / 'review/result.json')
            self.assertEqual([(c['id'], c['execution'], c['outcome']) for c in result['checks']],
                             [('erc', 'ok', 'pass'), ('drc', 'ok', 'pass')])
            for limitation in compiled['limitations']:
                self.assertIn(limitation, result['limitations'])
            self.assertTrue(any(s.startswith('POD-AC-8: BENCH REQUIRED:') for s in result['limitations']))
            self.assertFalse(any(c['id'] in ('POD-AC-8', 'POD-AC-9') for c in result['checks']))
            from agent_ee.evidence_index import build_index, render_index
            index = build_index(root / 'review', 'pod-criteria.json')
            self.assertEqual(index['limitations'][:len(compiled['limitations'])], compiled['limitations'])
            for limitation in compiled['limitations']:
                self.assertIn(limitation, render_index(index))
            self.assertFalse(index['acceptance'])
            self.assertEqual([row['status'] for row in index['criteria'][7:]], ['not_met', 'not_met'])


if __name__ == '__main__':
    unittest.main()
