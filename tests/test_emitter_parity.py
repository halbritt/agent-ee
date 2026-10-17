"""Emitted schematic -> native KiCad XML -> AGENTEE-41 parity (40 -> 41 gate).

The comparison authority is the separately owned ``agent_ee.netlist_parity``
(AGENTEE-41) run on the actual ``kicad-cli`` export of emitted files. The
intent fixture has no power symbols: 41 has no power-symbol policy yet, and
KiCad's netlist omits ``#`` references (see docs/schematic-emitter.md).
Anchors are computed here from the frozen identity contract, not read back
from emitter output. The parity modules are internal dependencies: their
absence is an import error here, never a skip.
"""
import json
import re
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from test_schematic_emitter import captures, load, native_project, run_native
from agent_ee.connectivity_ir import resolve_intent
from agent_ee.native_netlist import read_native_inventory
from agent_ee.netlist_parity import compare, resolved_inventory, validate_policy
from agent_ee.schematic_emitter import emit_schematic
from toolchain import require_toolchain


class EmitterParityTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = Path(tempfile.mkdtemp(prefix='agent-ee-emitter-parity-'))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.intent = load('regulator-core.intent.json')
        self.layout = load('regulator-core.layout.json')

    def native(self, intent, layout, label):
        result, files = emit_schematic(intent, captures(intent), layout)
        self.assertEqual(result['execution'], 'ok', result['findings'])
        project = native_project(files, self.tmp / label)
        work = self.tmp / label / 'work'
        run_native(project, work, 'kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                   '-o', '/work/design.xml', f'/candidate/{layout["project"]}.kicad_sch')
        return read_native_inventory(work / 'design.xml')

    def compare(self, actual, predicate, multiset='exactly', **extra):
        expected = resolved_inventory(resolve_intent(self.intent, captures(self.intent)))
        policy = validate_policy(dict(source=dict(kind='intent', path='regulator-core.intent.json'),
                                      netlist_check='netlist', predicate=predicate, multiset=multiset, **extra))
        return compare(expected, actual, policy)

    def anchors(self, namespace):
        ns = uuid.UUID(namespace)
        return {cid: {'sheet': '/', 'uuid': str(uuid.uuid5(ns, json.dumps(['symbol', cid, 1], separators=(',', ':'))))}
                for cid in ('j_in', 'reg', 'c_out')}

    def test_emitted_native_export_passes_every_predicate(self):
        actual = self.native(self.intent, self.layout, 'base')
        self.assertEqual(self.compare(actual, 'components')['outcome'], 'pass')
        self.assertEqual(self.compare(actual, 'topology')['outcome'], 'pass')
        report = self.compare(actual, 'instance', anchors=self.anchors(self.layout['namespace']),
                              instance_components=['j_in', 'reg', 'c_out'])
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'))
        self.assertEqual(report['component_matches'], {'c_out': ['C1'], 'j_in': ['J1'], 'reg': ['U1']})

    def mutated(self, edit, label, layout=None):
        intent = json.loads(json.dumps(self.intent))
        edit(intent)
        return self.native(intent, layout or self.layout, label)

    def test_omitted_shorted_and_swapped_terminals_fail_topology(self):
        def omit(i):
            vin = next(n for n in i['nets'] if n['id'] == 'vin')
            i['no_connects'] += [t for t in vin['terminals'] if t['component'] == 'reg']
            vin['terminals'] = [t for t in vin['terminals'] if t['component'] != 'reg'] + [
                {'component': 'c_out', 'pin': {'number': '1'}}]
            vout = next(n for n in i['nets'] if n['id'] == 'vout')
            vout['terminals'] = [t for t in vout['terminals'] if t['component'] != 'c_out']
        def short(i):
            vin, vout = (next(n for n in i['nets'] if n['id'] == k) for k in ('vin', 'vout'))
            vin['terminals'] += vout['terminals']
            i['nets'].remove(vout)
        def swap(i):
            vin, vout = (next(n for n in i['nets'] if n['id'] == k) for k in ('vin', 'vout'))
            vin['terminals'][0]['pin']['number'], vout['terminals'][0]['pin']['number'] = '3', '1'
        for label, edit in (('omit', omit), ('short', short), ('swap', swap)):
            with self.subTest(label):
                report = self.compare(self.mutated(edit, label), 'topology')
                self.assertEqual((report['execution'], report['outcome']), ('ok', 'defect'))

    def test_extra_identical_part_fails_component_multiset(self):
        def extra(i):
            i['components'].append(dict(next(c for c in i['components'] if c['id'] == 'c_out'),
                                        id='c_extra', reference='C2'))
            for net, pin in (('vout', '1'), ('gnd', '2')):
                next(n for n in i['nets'] if n['id'] == net)['terminals'].append(
                    {'component': 'c_extra', 'pin': {'number': pin}})
        actual = self.mutated(extra, 'extra')
        report = self.compare(actual, 'components')
        self.assertEqual((report['outcome'], report['extra_components']),
                         ('defect', [{'key': ['OwnedGen:CAP', '10u', True], 'count': 1}]))

    def test_altered_instance_identity_fails_instance_predicate(self):
        layout = dict(self.layout, namespace='11111111-2222-5333-8444-555555555555')
        actual = self.mutated(lambda i: None, 'uuid', layout)
        self.assertEqual(self.compare(actual, 'topology')['outcome'], 'pass')
        report = self.compare(actual, 'instance', anchors=self.anchors(self.layout['namespace']),
                              instance_components=['j_in', 'reg', 'c_out'])
        self.assertNotEqual(report['outcome'], 'pass')
        self.assertEqual(report['execution'], 'unsupported')


class HierarchyParityTests(unittest.TestCase):
    """Emitted two-level files -> native export -> 41 parity against a fresh v2 resolution."""

    def setUp(self):
        require_toolchain(native=True)
        self.tmp = Path(tempfile.mkdtemp(prefix='agent-ee-hierarchy-parity-'))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.intent = load('two_level.intent.json')
        self.layout = load('two_level.layout.json')
        result, self.files = emit_schematic(self.intent, captures(self.intent), self.layout)
        self.assertEqual(result['execution'], 'ok', result['findings'])

    def export(self, files, label):
        project = native_project(files, self.tmp / label)
        work = self.tmp / label / 'work'
        run_native(project, work, 'kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                   '-o', '/work/design.xml', '/candidate/two_level.kicad_sch')
        return read_native_inventory(work / 'design.xml')

    def compare(self, actual, predicate, **extra):
        expected = resolved_inventory(resolve_intent(self.intent, captures(self.intent)))
        policy = validate_policy(dict(source=dict(kind='intent', path='two_level.intent.json'),
                                      netlist_check='netlist', predicate=predicate, multiset='exactly', **extra))
        return compare(expected, actual, policy)

    def anchors(self):
        """Native sheet path and symbol UUID per scoped component, from the identity contract."""
        ns = uuid.UUID(self.layout['namespace'])
        ident = lambda *parts: str(uuid.uuid5(ns, json.dumps(list(parts), separators=(',', ':'))))
        files = {s['id']: s['file'] for s in self.intent['hierarchy']}
        paths = {'root': '/', 'filter': '/' + ident('sheet', files['root'], 'filter') + '/'}
        return {json.dumps([c['sheet'], c['id']], separators=(',', ':')):
                {'sheet': paths[c['sheet']], 'uuid': ident('symbol', files[c['sheet']], c['id'], 1)}
                for c in self.intent['components']}

    def test_emitted_hierarchy_passes_every_predicate(self):
        actual = self.export(self.files, 'base')
        self.assertEqual(self.compare(actual, 'components')['outcome'], 'pass')
        self.assertEqual(self.compare(actual, 'topology')['outcome'], 'pass')
        anchors = self.anchors()
        report = self.compare(actual, 'instance', anchors=anchors, instance_components=sorted(anchors))
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'))
        refs = {json.dumps([c['sheet'], c['id']], separators=(',', ':')): [c['reference']]
                for c in self.intent['components']}
        self.assertEqual(report['component_matches'], refs)
        # Swapped anchors on the emitted export: topology still passes, instance does not.
        swapped = dict(anchors)
        a, b = '["filter","c_gnd"]', '["filter","c_local"]'
        swapped[a], swapped[b] = anchors[b], anchors[a]
        report = self.compare(actual, 'instance', anchors=swapped, instance_components=sorted(anchors))
        self.assertNotEqual(report['outcome'], 'pass')

    def test_native_mutations_fail_topology(self):
        root, child = (self.files[n].decode() for n in ('two_level.kicad_sch', 'filter.kicad_sch'))
        pin = re.search(r'\t\t\(pin "IN" passive\n.*?\n\t\t\)\n', root, re.S).group(0)
        variants = {
            # The child's internal LOCAL_SAME labels renamed onto its hierarchical GND.
            'shorted_internal_label': {'filter.kicad_sch': child.replace('(label "LOCAL_SAME"', '(label "GND"')},
            'deleted_sheet_pin': {'two_level.kicad_sch': root.replace(pin, '', 1)},
        }
        for label, change in variants.items():
            with self.subTest(label):
                files = dict(self.files, **{k: v.encode() for k, v in change.items()})
                self.assertNotEqual(files, self.files)
                report = self.compare(self.export(files, label), 'topology')
                self.assertEqual((report['execution'], report['outcome']), ('ok', 'defect'))


if __name__ == '__main__':
    unittest.main()
