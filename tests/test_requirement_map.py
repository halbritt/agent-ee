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
from agent_ee.requirement_map import (PROVENANCE, compile_map, compile_map_file, decode,
                                      write_map_outputs)
from agent_ee.requirement_scope import Refused, main, sha256
from agent_ee.storage import digest, json_bytes, read_json, write_json
from toolchain import require_toolchain

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
                     'not a captured calculation'):
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
