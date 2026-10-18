"""Emitted schematic -> native KiCad XML -> AGENTEE-41 parity (40 -> 41 gate).

The comparison authority is the separately owned ``agent_ee.netlist_parity``
(AGENTEE-41) run on the actual ``kicad-cli`` export of emitted files. The
regulator test includes its declared power symbols and flags; KiCad's netlist
omits their virtual ``#`` references (see docs/schematic-emitter.md).
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

from test_schematic_emitter import captures, load, native_project, run_native, tables
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

    def native(self, intent, layout, label, *, erc=False):
        result, files = emit_schematic(intent, captures(intent), layout, **(tables() if erc else {}))
        self.assertEqual(result['execution'], 'ok', result['findings'])
        project = native_project(files, self.tmp / label)
        work = self.tmp / label / 'work'
        if erc:
            run_native(project, work / 'erc', 'kicad-cli', 'sch', 'erc', '--format', 'json', '--severity-all',
                       '--units', 'mm', '-o', '/work/erc.json', f'/candidate/{layout["project"]}.kicad_sch')
            report = json.loads((work / 'erc' / 'erc.json').read_text())
            self.assertEqual([v for s in report['sheets'] for v in s['violations']], [])
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

    def test_power_regulator_passes_erc_and_every_physical_parity_predicate(self):
        self.intent, self.layout = load('regulator.intent.json'), load('regulator.layout.json')
        actual = self.native(self.intent, self.layout, 'power-regulator', erc=True)
        self.assertEqual(sorted(actual['components']), ['C1', 'J1', 'U1'])
        for predicate in ('components', 'topology', 'instance'):
            with self.subTest(predicate=predicate):
                extra = dict(anchors=self.anchors(self.layout['namespace']),
                             instance_components=['j_in', 'reg', 'c_out']) if predicate == 'instance' else {}
                report = self.compare(actual, predicate, **extra)
                self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report)
                self.assertEqual({n['reference'] for n in report['normalized_power_symbols']},
                                 {'#PWR01', '#FLG01', '#FLG02'})
        def move_capacitor(intent):
            vin, vout = (next(n for n in intent['nets'] if n['id'] == k) for k in ('vin', 'vout'))
            terminal = next(t for t in vout['terminals'] if t['component'] == 'c_out')
            vout['terminals'].remove(terminal)
            vin['terminals'].append(terminal)
        changed = self.mutated(move_capacitor, 'power-regulator-wrong-binding')
        report = self.compare(changed, 'topology')
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'defect'), report)
        report = self.compare(actual, 'instance', instance_components=['gnd'])
        self.assertEqual((report['execution'], report['outcome']), ('unsupported', 'unknown'), report)

    def test_power_regulator_coordinator_evidence_seals_normalization(self):
        import time
        from agent_ee.baseline import freeze_evidence, verify_evidence
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        from agent_ee.storage import read_json, write_json
        intent, layout = load('regulator.intent.json'), load('regulator.layout.json')
        result, files = emit_schematic(intent, captures(intent), layout, **tables())
        self.assertEqual(result['execution'], 'ok', result['findings'])
        project = native_project(files, self.tmp / 'coordinator')
        # Intent paths are relative to the captured candidate, including library paths.
        for lib in intent['libraries']:
            lib['path'] = 'project/' + lib['path']
        write_json(project / 'intent.json', intent)
        (project / 'requirements.md').write_text('Preserve regulator physical connectivity and native ERC.\n')
        scope = {
            'schema': 'agent-ee.scope/3', 'name': 'regulator', 'scope': 'Owned regulator emitter.',
            'limitations': [], 'schematic': 'project/regulator.kicad_sch',
            'inputs': [{'path': str(project), 'at': 'project'}], 'requirements': ['project/requirements.md'],
            'checks': [{'id': 'erc', 'kind': 'erc'}, {'id': 'netlist', 'kind': 'netlist'},
                       {'id': 'parity', 'kind': 'netlist_parity', 'params': {
                           'source': {'kind': 'intent', 'path': 'project/intent.json'},
                           'netlist_check': 'netlist', 'predicate': 'topology', 'multiset': 'exactly'}}],
            'baseline': {'requirements': [{'id': 'connectivity', 'statement': 'Preserve connectivity.',
                'source': {'path': 'project/requirements.md', 'locator': 'line 1'},
                'checks': ['erc', 'netlist', 'parity'], 'intent': 'acceptance'}],
                'targets': ['project/requirements.md'], 'reference_architectures': ['project/intent.json']}}
        write_json(self.tmp / 'scope.json', scope)
        run = self.tmp / 'run'
        run.mkdir()
        capture(self.tmp / 'scope.json', run)
        checks = run_checks(run, time.monotonic() + 120)
        self.assertEqual([(c['execution'], c['outcome']) for c in checks],
                         [('ok', 'pass'), ('ok', 'info'), ('ok', 'pass')], checks)
        report = read_json(run / 'work/parity/parity.json')
        self.assertEqual({n['component'] for n in report['normalized_power_symbols']},
                         {'gnd', 'flag_gnd', 'flag_vin'})
        freeze_evidence(run, checks)
        verify_evidence(run, checks, required=True)

    def staged_run(self, label, edit=None):
        """Captured staged run (AGENTEE-48/88) whose schematic subject is the emitted regulator."""
        import time
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        from agent_ee.storage import write_json
        intent, layout = load('regulator.intent.json'), load('regulator.layout.json')
        result, files = emit_schematic(intent, captures(intent), layout, **tables())
        self.assertEqual(result['execution'], 'ok', result['findings'])
        if edit:
            files = dict(files, **{'regulator.kicad_sch': edit(files['regulator.kicad_sch'])})
        root = self.tmp / label
        project = native_project(files, root)
        for lib in intent['libraries']:
            lib['path'] = 'project/' + lib['path']
        write_json(project / 'intent.json', intent)
        (project / 'requirements.md').write_text('Preserve regulator physical connectivity and native ERC.\n')
        checks = [{'id': 'erc', 'kind': 'erc', 'stage': 'schematic'},
                  {'id': 'netlist', 'kind': 'netlist', 'stage': 'schematic'},
                  {'id': 'parity', 'kind': 'netlist_parity', 'stage': 'schematic', 'params': {
                      'source': {'kind': 'intent', 'path': 'project/intent.json'},
                      'netlist_check': 'netlist', 'predicate': 'topology', 'multiset': 'exactly'}}]
        scope = {
            'schema': 'agent-ee.scope/3', 'name': 'regulator-staged', 'scope': 'Owned regulator emitter, staged.',
            'limitations': [], 'schematic': 'project/regulator.kicad_sch',
            'inputs': [{'path': str(project), 'at': 'project'}], 'requirements': ['project/requirements.md'],
            'checks': checks,
            'stages': {'schema': 'agent-ee.stages/1', 'order': ['schematic'], 'finding_retry_limit': 4,
                       'subjects': {'schematic': {'path': 'project/regulator.kicad_sch', 'kind': 'symbols',
                                                  'references': ['C1', 'J1', 'U1']}}},
            'baseline': {'requirements': [{'id': 'connectivity', 'statement': 'Preserve connectivity.',
                'source': {'path': 'project/requirements.md', 'locator': 'line 1'},
                'checks': ['erc', 'netlist', 'parity'], 'intent': 'acceptance'}],
                'targets': ['project/requirements.md'], 'reference_architectures': ['project/intent.json']}}
        write_json(root / 'scope.json', scope)
        run = root / 'run'
        run.mkdir()
        capture(root / 'scope.json', run)
        return run, run_checks(run, time.monotonic() + 240)

    def test_staged_presence_probe_accepts_emitted_power_symbols(self):
        from agent_ee.baseline import freeze_evidence, verify_evidence
        from agent_ee.storage import read_json
        run, checks = self.staged_run('staged')
        self.assertEqual([(c['execution'], c['outcome']) for c in checks],
                         [('ok', 'pass'), ('ok', 'info'), ('ok', 'pass')], checks)
        for check in checks:
            probe = run / 'work' / check['id'] / 'stage-subject'
            self.assertEqual(read_json(probe / 'references.json'), ['C1', 'J1', 'U1'])
            self.assertEqual(read_json(probe / 'native-observation.json')['native_references'], ['C1', 'J1', 'U1'])
            witness = read_json(run / 'work' / check['id'] / 'stage-subject.json')
            self.assertEqual((witness['observed'], witness['missing']), (['C1', 'J1', 'U1'], []))
        report = read_json(run / 'work/parity/parity.json')
        self.assertEqual({n['component'] for n in report['normalized_power_symbols']},
                         {'gnd', 'flag_gnd', 'flag_vin'})
        freeze_evidence(run, checks)
        verify_evidence(run, checks, required=True)

    def test_staged_presence_probe_refuses_an_ordinary_part_marked_virtual(self):
        # The emitter writes only power symbols and flags in_bom/on_board no. An ordinary
        # C1 rewritten that way drops out of the bounded inventory, but KiCad's netlist
        # export still lists it, so the probe refuses the disagreement (fail closed).
        def hidden(data):
            text = data.decode()
            physical, virtual = '(in_bom yes)\n\t\t(on_board yes)', '(in_bom no)\n\t\t(on_board no)'
            reference = text.index('(property "Reference" "C1"')
            flags = text.index(physical, text.rindex('\n\t(symbol\n', 0, reference))
            self.assertLess(flags, reference)
            return (text[:flags] + virtual + text[flags + len(physical):]).encode()
        run, checks = self.staged_run('staged-hidden', hidden)
        self.assertEqual([c['outcome'] for c in checks], ['unknown'] * 3, checks)
        self.assertTrue(all(c['execution'] != 'ok' for c in checks), checks)
        self.assertIn('bounded inventory disagrees with native reader',
                      (run / 'work/erc/stage-subject/console.log').read_text())

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
