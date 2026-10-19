"""Component choice presence on original synthetic native designs (AGENTEE-166).

Named MPN strings are test declarations, not vendor symbols or suitability proof.
The owned native-binding generator supplies all symbol geometry and pin data.
"""
import copy
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from agent_ee.baseline import freeze_evidence, load_baseline, verify_evidence
from agent_ee.candidate import capture
from agent_ee.capabilities import describe_check
from agent_ee.checks import Ctx, CheckError, check_part_decisions, run_checks
from agent_ee.native_netlist import read_native_inventory
from agent_ee.part_decisions import compile_decisions, evaluate
from agent_ee.spec_record import capture as capture_spec
from agent_ee.storage import json_bytes, read_json, write_json
from test_native_binding import FIXTURE, GEN
from toolchain import require_toolchain

PARTS = [('CAN', 'TJA1051T/3', 'C38695'), ('Protection', 'PESD1CAN-UX', 'C907839'),
         ('IMU', 'LSM6DSO32', None), ('Radio', 'ESP32-S3-WROOM-1-N8', None),
         ('GNSS', 'NEO-M9N', None), ('ADC', 'MCP3204', None), ('Harness', '776228-1', None)]


def inputs():
    spec = ('# Component decisions\n\n| Function | Decision |\n| --- | --- |\n' + ''.join(
        f'| {function} | {mpn}' + (f' ({lcsc})' if lcsc else '') + ' |\n'
        for function, mpn, lcsc in PARTS)).encode()
    record = capture_spec(spec, 'TEST', {'Component decisions': 'CD'}, 'spec.md')
    declaration = {'section': 'Component decisions', 'decisions': [
        {'requirement_id': row['id'], 'function': function, 'mpn': mpn, 'lcsc': lcsc,
         'symbol_hint': None, 'footprint_hint': None}
        for row, (function, mpn, lcsc) in zip(record['requirements'], PARTS)], 'exclusions': []}
    policy = compile_decisions(json_bytes(record), spec, declaration)
    return spec, json_bytes(record), policy


def deviation():
    return {'requirement_id': 'TEST-CD-GNSS', 'replacement': {'mpn': 'NEO-M8N', 'lcsc': None},
            'reason': 'Test substitution requiring independent suitability review.',
            'evidence_refs': [{'path': 'evidence.txt', 'locator': 'comparison'}]}


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.spec, self.record, self.policy = inputs()
        self.empty = {'schema': 'agent-ee.part-deviations/1', 'deviations': []}
        (self.root / 'evidence.txt').write_text('Original synthetic comparison; not engineering approval.\n')
        # Modify an owned native export, retaining its strict library/unit schema.
        tree = ET.parse(FIXTURE / 'control.xml')
        parent = tree.find('components')
        seed = copy.deepcopy(parent.find('comp'))
        for child in list(parent):
            parent.remove(child)
        tree.find('nets').clear()
        for number, (_, mpn, lcsc) in enumerate(PARTS, 1):
            comp = copy.deepcopy(seed)
            comp.set('ref', f'U{number}')
            comp.find('tstamps').text = f'owned-component-{number}'
            for name, value in [('Manufacturer', 'Original test manufacturer'), ('Partnumber', mpn), ('LCSC', lcsc)]:
                if value:
                    ET.SubElement(comp.find('fields'), 'field', name=name).text = value
                    ET.SubElement(comp, 'property', name=name, value=value)
            parent.append(comp)
        self.xml = self.root / 'native.xml'
        tree.write(self.xml)
        self.tree = tree
        self.components = read_native_inventory(self.xml)['components']

    def result(self, changes=None):
        annotation = {'schema': 'agent-ee.part-deviations/1', 'deviations': changes or []}
        return evaluate(self.policy, annotation, self.components, self.root, {'evidence.txt': {}})

    def test_all_present(self):
        result = self.result()
        self.assertEqual(result['outcome'], 'pass')
        self.assertEqual(len(result['records']), 7)
        self.assertEqual(result['records'][0]['matches'][0]['fields']['LCSC'], 'C38695')

    def test_missing_decision_defect(self):
        del self.components['U2']
        self.assertEqual(self.result()['records'][1]['violations'], ['decision_absent'])
        self.assertEqual(self.result()['outcome'], 'defect')

    def test_display_value_is_not_identity_and_dnp_off_board_do_not_count(self):
        for mutate in (lambda c: c['binding_fields'].update(Partnumber=None),
                       lambda c: c.update(populated=False), lambda c: c.update(on_board=False),
                       lambda c: c['binding_fields'].update(LCSC='C1')):
            with self.subTest(mutation=mutate):
                self.components = read_native_inventory(self.xml)['components']
                self.components['U1']['value'] = 'TJA1051T/3'
                mutate(self.components['U1'])
                self.assertEqual(self.result()['outcome'], 'defect')

    def test_deviation_accepted(self):
        self.components['U5']['binding_fields']['Partnumber'] = 'NEO-M8N'
        self.components['U5']['value'] = 'NEO-M9N'
        result = self.result([deviation()])
        self.assertEqual(result['outcome'], 'pass')
        self.assertIn('independent suitability', result['records'][4]['deviation']['reason'])
        self.assertEqual(len(result['deviations'][0]['evidence'][0]['sha256']), 64)

    def test_deviation_without_reason_or_evidence_defect(self):
        for field, value in [('reason', ''), ('evidence_refs', []),
                             ('evidence_refs', [{'path': 'absent', 'locator': 'x'}]),
                             ('evidence_refs', [{'path': '../escape', 'locator': 'x'}])]:
            item = deviation()
            item[field] = value
            with self.subTest(field=field, value=value):
                result = self.result([item])
                self.assertEqual(result['outcome'], 'defect')
                self.assertTrue(result['deviation_defects'])
        item = deviation()
        del item['reason']
        self.assertEqual(self.result([item])['outcome'], 'defect')

    def test_absent_replacement_and_unknown_decision_are_defects(self):
        self.assertEqual(self.result([deviation()])['records'][4]['violations'], ['replacement_absent'])
        item = deviation()
        item['requirement_id'] = 'UNKNOWN'
        self.assertTrue(self.result([item])['deviation_defects'])

    def test_duplicate_and_ambiguous_deviations_are_defects(self):
        item = deviation()
        self.assertTrue(self.result([item, item])['deviation_defects'])
        self.policy['declaration']['decisions'].append(dict(self.policy['declaration']['decisions'][4], mpn='OTHER'))
        self.assertTrue(self.result([item])['deviation_defects'])
        item['original'] = {'mpn': 'NEO-M9N', 'lcsc': None}
        self.assertFalse(self.result([item])['deviation_defects'])

    def test_source_drift_omission_and_non_source_identity_refused(self):
        with self.assertRaises(ValueError):
            compile_decisions(self.record, self.spec + b'\n', self.policy['declaration'])
        for mutation in (lambda d: d['decisions'].pop(),
                         lambda d: d['decisions'][0].update(mpn='MCP2562FD'),
                         lambda d: d['decisions'][0].update(mpn='TJA1051'),
                         lambda d: d['decisions'][0].update(function='different')):
            declaration = copy.deepcopy(self.policy['declaration'])
            mutation(declaration)
            with self.assertRaises(ValueError):
                compile_decisions(self.record, self.spec, declaration)

    def test_explicit_normalization_and_exclusions_remain_visible(self):
        declaration = copy.deepcopy(self.policy['declaration'])
        declaration['decisions'][0].update(mpn='TJA1051T/3,118', normalization={
            'source_mpn': 'TJA1051T/3', 'reason': 'Explicit package-ordering suffix; review required.'})
        removed = declaration['decisions'].pop()
        declaration['exclusions'] = [{'requirement_id': removed['requirement_id'], 'reason': 'Test excluded row',
                                     'classification': 'unresolved_part_decision', 'evidence_refs': []}]
        policy = compile_decisions(self.record, self.spec, declaration)
        self.assertEqual(policy['declaration'], declaration)
        self.assertEqual(len(policy['source_rows']), 7)
        result = evaluate(policy, self.empty, self.components, self.root, {})
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'))
        self.assertEqual(result['unresolved'][0]['requirement_id'], removed['requirement_id'])
        # Explicit non-part classification is inspectable, not a semantic judgment.
        policy['declaration']['exclusions'][0].update(classification='non_part_constraint',
            evidence_refs=[{'path': 'evidence.txt', 'locator': 'original synthetic classification'}])
        # Reset the normalized ordering identity so this assertion isolates classification.
        policy['declaration']['decisions'][0]['mpn'] = 'TJA1051T/3'
        result = evaluate(policy, self.empty, self.components, self.root, {'evidence.txt': {}})
        self.assertEqual(result['outcome'], 'pass')
        self.assertEqual(result['exclusions'][0]['status'], 'outside_predicate')
        self.assertEqual(len(result['exclusions'][0]['evidence'][0]['sha256']), 64)
        policy['declaration']['exclusions'][0]['evidence_refs'] = []
        with self.assertRaisesRegex(ValueError, 'classification requires'):
            compile_decisions(self.record, self.spec, policy['declaration'])

    def test_non_part_classification_evidence_is_immutable(self):
        from agent_ee.part_decisions import capability
        spec = self.spec + b'| Board | Four layers |\n'
        record = capture_spec(spec, 'TEST', {'Component decisions': 'CD'}, 'spec.md')
        declaration = copy.deepcopy(self.policy['declaration'])
        declaration['exclusions'] = [{'requirement_id': 'TEST-CD-BOARD', 'classification': 'non_part_constraint',
            'reason': 'Board construction is outside native component identity equality.',
            'evidence_refs': [{'path': 'evidence.txt', 'locator': 'construction obligation'}]}]
        policy = compile_decisions(json_bytes(record), spec, declaration)
        (self.root / 'spec.md').write_bytes(spec)
        write_json(self.root / 'record.json', record)
        write_json(self.root / 'decisions.json', policy)
        params = {'netlist_check': 'native', 'decisions': 'decisions.json', 'spec_record': 'record.json',
                  'spec': 'spec.md', 'deviations': None}
        files = {p: {} for p in ('spec.md', 'record.json', 'decisions.json', 'evidence.txt')}
        scope = {'baseline': {'requirements': [{'id': d['requirement_id'], 'checks': ['parts'],
                  'source': {'path': 'spec.md'}} for d in declaration['decisions']]}}
        descriptor = capability(params, scope, files, self.root, 'parts')
        self.assertIn('evidence.txt', descriptor['immutable_inputs'])
        self.assertEqual(evaluate(policy, self.empty, self.components, self.root, files)['outcome'], 'pass')
        (self.root / 'evidence.txt').unlink()
        with self.assertRaises(OSError):
            capability(params, scope, files, self.root, 'parts')

    def test_canonical_pod_source_keeps_unresolved_parts_unknown(self):
        source = Path(__file__).resolve().parents[1] / 'fixtures/telemetry-pod'
        record = read_json(source / 'spec-record.json')
        rows = [r for r in record['requirements'] if r['section'] == 'Component decisions']
        # This deliberately partial projection proves omitted source choices stay
        # unresolved; it is not an actual pod mapping or acceptance fixture.
        can = next(r for r in rows if r['id'] == 'POD-CD-CAN-TRANSCEIVER')
        decisions = [{'requirement_id': can['id'], 'function': can['cells']['Function'],
            'mpn': mpn, 'lcsc': lcsc, 'symbol_hint': None, 'footprint_hint': None}
            for mpn, lcsc in [('TJA1051T/3', 'C38695'), ('PESD1CAN-UX', 'C907839'), (None, 'C22787')]]
        declaration = {'section': 'Component decisions', 'decisions': decisions, 'exclusions': [
            {'requirement_id': r['id'], 'classification': 'unresolved_part_decision',
             'reason': 'Deliberately unprojected in this negative source-binding test.', 'evidence_refs': []}
            for r in rows if r != can]}
        policy = compile_decisions((source / 'spec-record.json').read_bytes(),
                                   (source / 'spec.md').read_bytes(), declaration)
        report = evaluate(policy, self.empty, self.components, self.root, {})
        self.assertEqual(report['outcome'], 'unknown')
        self.assertEqual(len(report['unresolved']), 7)
        self.assertEqual(len(report['records']), 3)
        declaration['decisions'][1]['mpn'] = 'MCP2562FD'  # Appears only under Not chosen.
        with self.assertRaisesRegex(ValueError, 'absent from source Decision'):
            compile_decisions((source / 'spec-record.json').read_bytes(),
                              (source / 'spec.md').read_bytes(), declaration)

    def test_native_field_and_property_forms_and_contradictions(self):
        component = self.tree.find('./components/comp')
        component.remove(component.find("property[@name='Partnumber']"))
        self.tree.write(self.xml)
        self.assertEqual(read_native_inventory(self.xml)['components']['U1']['binding_fields']['Partnumber'], 'TJA1051T/3')
        ET.SubElement(component, 'property', name='Partnumber', value='OTHER')
        self.tree.write(self.xml)
        with self.assertRaisesRegex(ValueError, 'contradictory'):
            read_native_inventory(self.xml)
        component.remove(component.find("property[@name='Partnumber']"))
        ET.SubElement(component.find('fields'), 'field', name='Partnumber').text = 'TJA1051T/3'
        self.tree.write(self.xml)
        with self.assertRaises(ValueError):
            read_native_inventory(self.xml)


class NativeDecisionTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_native(self):
        spec, record, policy = inputs()
        body = []
        for number, (_, mpn, lcsc) in enumerate(PARTS, 1):
            ref = f'U{number}'
            symbol = GEN._symbol(ref, 'Tri', 'Owned:SOT3', ('yes', 'yes', 'no'), 1, 20 + number * 20,
                                 GEN._root_entries(ref, 1, 'control'))
            symbol = symbol[:-1] + GEN.prop('Partnumber', mpn) + GEN.prop('Manufacturer', 'Owned')
            if lcsc:
                symbol += GEN.prop('LCSC', lcsc)
            body.append(symbol + ')')
        (self.root / 'binding.kicad_sch').write_text(GEN._sheet_file(GEN.uid('root'), ''.join(body)))
        (self.root / 'spec.md').write_bytes(spec)
        (self.root / 'spec-record.json').write_bytes(record)
        write_json(self.root / 'decisions.json', policy)
        write_json(self.root / 'deviations.json', {'schema': 'agent-ee.part-deviations/1', 'deviations': []})
        scope = {'schema': 'agent-ee.scope/3', 'schematic': 'binding.kicad_sch',
                 'inputs': [{'path': str(self.root / p), 'at': p} for p in (
                     'binding.kicad_sch', 'spec.md', 'spec-record.json', 'decisions.json', 'deviations.json')],
                 'requirements': ['spec.md'], 'checks': [{'id': 'native', 'kind': 'netlist'},
                     {'id': 'parts', 'kind': 'part_decisions', 'params': {'netlist_check': 'native',
                         'decisions': 'decisions.json', 'spec_record': 'spec-record.json',
                         'spec': 'spec.md', 'deviations': 'deviations.json'}}],
                 'baseline': {'targets': ['spec.md'], 'reference_architectures': ['spec.md'],
                     'requirements': [{'id': r['id'], 'statement': r['text'],
                         'source': {'path': 'spec.md', 'locator': r['id']}, 'checks': ['native', 'parts'],
                         'intent': 'acceptance'} for r in policy['source_rows']]}}
        write_json(self.root / 'scope.json', scope)
        run = self.root / 'run'
        run.mkdir()
        capture(self.root / 'scope.json', run)
        checks = run_checks(run, time.monotonic() + 120)
        self.assertEqual(checks[0]['execution'], 'ok', checks[0])
        return run, checks

    def test_native_fields_baseline_and_sealed_report(self):
        run, checks = self.run_native()
        self.assertEqual((checks[1]['execution'], checks[1]['outcome']), ('ok', 'pass'), checks[1])
        self.assertEqual(len(checks[1]['details']['records']), 7)
        baseline = load_baseline(run)
        immutable = set(baseline['files'])
        self.assertTrue({'spec.md', 'spec-record.json', 'decisions.json'} <= immutable)
        self.assertNotIn('deviations.json', immutable)
        self.assertIn('work/parts/part_decisions.json', checks[1]['evidence'])
        freeze_evidence(run, checks)
        verify_evidence(run, checks, required=True)
        manifest = read_json(run / 'candidate.json')
        descriptor = describe_check(manifest['scope']['checks'][1], check_specs=manifest['scope']['checks'],
                                    scope=manifest['scope'], files=manifest['files'], inputs=run / 'inputs')
        self.assertEqual(descriptor['status'], 'supported', descriptor)
        self.assertEqual(len(descriptor['predicates']), 7)

    def test_unreadable_netlist_unknown(self):
        run, checks = self.run_native()
        self.assertEqual(checks[1]['outcome'], 'pass', checks[1])
        (run / 'work/native/design.xml').write_text('unreadable native output')
        manifest = read_json(run / 'candidate.json')
        ctx = Ctx(run, manifest, manifest['scope']['checks'][1], time.monotonic() + 30)
        with self.assertRaises(CheckError) as raised:
            check_part_decisions(ctx)
        self.assertEqual(raised.exception.execution, 'failed')
        self.assertEqual(ctx.result['outcome'], 'unknown')


if __name__ == '__main__':
    unittest.main()
