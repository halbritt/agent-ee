"""Native export must account for every captured symbol occurrence (AGENTEE-117)."""
import copy
import json
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from agent_ee.candidate import capture
from agent_ee.checks import parse_xml_netlist, run_checks
from agent_ee.native_netlist import hierarchy_gaps, read_native_instances, read_native_inventory
from agent_ee.storage import write_json
from tests.test_native_binding import FIXTURE, GEN
from tests.toolchain import require_toolchain


class NativeCoverageTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-netlist-coverage-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_hierarchy(self, broken=False, multi_unit=False):
        sheets = GEN.hierarchy()
        if multi_unit:
            second = GEN._symbol('U2B', 'Dual', 'Owned:SOT5EP', ('yes', 'yes', 'no'), 2, 220,
                [('binding', [(f'/{GEN.uid("root")}', 'U2', 2)])])
            sheets['binding.kicad_sch'] = sheets['binding.kicad_sch'].rstrip()[:-1] + second + ')\n'
        if broken:
            sheets[GEN.CHILD] = sheets[GEN.CHILD].replace('(in_bom yes)', '(in_bom maybe)', 1)
        for name, text in sheets.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        (self.root / 'requirements.md').write_text('R1 pins must be on distinct nets.\n')
        policy = {'schema': 'agent-ee.netlist-extra-policy/1',
            'requirement': {'id': 'distinct', 'source': {'path': 'requirements.md', 'locator': 'line 1'}},
            'classes': {'Owned:R': {'class': 'resistor', 'pins': ['1', '2']}}, 'overrides': [],
            'exemptions': [{'component': {'sheet': '/', 'uuid': GEN.uid(ref)},
                'symbol': 'Owned:' + symbol, 'reason': 'Outside two-terminal coverage'}
                for ref, symbol in [('U1', 'Tri'), ('U2', 'Dual'), ('U3', 'Child'), ('J1', 'Conn4')]],
            'rail_pairs': [
                {'id': 'r1-pins', 'endpoints': [
                    {'sheet': '/', 'uuid': GEN.uid('R1'), 'pin': pin} for pin in ('1', '2')]}]}
        write_json(self.root / 'policy.json', policy)
        scope = {'schema': 'agent-ee.scope/3', 'schematic': 'binding.kicad_sch',
            'inputs': [{'path': name, 'at': name} for name in [*sheets, 'policy.json', 'requirements.md']],
            'requirements': ['requirements.md'],
            'checks': [{'id': 'netlist', 'kind': 'netlist'}, {'id': 'extra', 'kind': 'netlist_extra',
                'params': {'netlist_check': 'netlist', 'policy': 'policy.json'}}],
            'baseline': {'requirements': [{'id': 'distinct', 'statement': 'Distinct nets',
                'source': policy['requirement']['source'], 'checks': ['netlist', 'extra'],
                'intent': 'acceptance'}], 'targets': ['requirements.md'],
                'reference_architectures': ['requirements.md']}}
        write_json(self.root / 'scope.json', scope)
        run = self.root / 'run'
        run.mkdir()
        capture(self.root / 'scope.json', run)
        results = run_checks(run, time.monotonic() + 120)
        return run, results

    def test_unbroken_repeated_child_sheet_exports_all_instances(self):
        run, results = self.run_hierarchy()
        self.assertEqual((results[0]['execution'], results[0]['outcome']), ('ok', 'info'), results)
        refs = results[0]['details']['refs']
        self.assertTrue({'R11', 'R12', 'R2', 'R3', 'R4'} <= set(refs), refs)
        self.assertEqual(results[0]['details']['sheet_instances'], 3)
        self.assertEqual((results[1]['execution'], results[1]['outcome']), ('ok', 'pass'), results)
        self.assertTrue((run / 'work/netlist/connectivity.json').is_file())

    def test_all_placed_units_of_one_component_are_matched(self):
        run, results = self.run_hierarchy(multi_unit=True)
        self.assertEqual((results[0]['execution'], results[0]['outcome']), ('ok', 'info'), results)
        graph = parse_xml_netlist(run / 'work/netlist/design.xml')
        self.assertEqual(set(graph['components']['U2']['uuid'].split()), {GEN.uid('U2'), GEN.uid('U2B')})

    def test_silently_unreadable_child_refuses_netlist_and_consumer(self):
        run, results = self.run_hierarchy(broken=True)
        netlist, consumer = results
        execution = json.loads((run / 'work/netlist/execution.json').read_text())
        self.assertEqual(execution['exit_code'], 0, execution)
        # KiCad still lists three sheets: sheet-count parity cannot detect the loss.
        native = ET.parse(run / 'work/netlist/design.xml').getroot()
        self.assertEqual(len(native.findall('./design/sheet')), 3)
        self.assertNotIn('R11', [c.get('ref') for c in native.findall('./components/comp')])
        self.assertEqual((netlist['execution'], netlist['outcome']), ('failed', 'unknown'), netlist)
        self.assertEqual(netlist['details']['missing_refs'], ['R11', 'R12'])
        for ref in ('R11', 'R12'):
            self.assertIn(ref, netlist['summary'])
        self.assertFalse((run / 'work/netlist/connectivity.json').exists())
        self.assertEqual((consumer['execution'], consumer['outcome']), ('failed', 'unknown'), consumer)
        self.assertFalse((run / 'work/extra/topology.json').exists())


class NativeInstanceReaderTests(unittest.TestCase):
    def read_variant(self, edit):
        with tempfile.TemporaryDirectory() as tmp:
            tree = ET.parse(FIXTURE / 'control.xml')
            edit(tree.getroot())
            path = Path(tmp) / 'design.xml'
            tree.write(path, encoding='utf-8')
            return read_native_instances(path)

    def test_identity_projection_does_not_require_pins(self):
        def pinless(root):
            for pins in root.findall('./libparts/libpart/pins'):
                pins.clear()
            for units in root.findall('./components/comp/units'):
                units.clear()
        inventory = self.read_variant(pinless)
        self.assertEqual(hierarchy_gaps({'binding.kicad_sch': GEN.schematic().encode()},
                                      'binding.kicad_sch', inventory), [])

    def test_identity_projection_does_not_apply_topology_search_component_limit(self):
        def large(root):
            components = root.find('components')
            for n in range(150):
                node = copy.deepcopy(components[0])
                node.set('ref', f'X{n}')
                node.find('tstamps').text = GEN.uid(f'extra-{n}')
                components.append(node)
        self.assertEqual(len(self.read_variant(large)['components']), 158)

    def test_duplicate_reference_or_occurrence_uuid_is_rejected(self):
        def duplicate(root):
            root.find('components').append(copy.deepcopy(root.find('./components/comp')))
        with self.assertRaisesRegex(ValueError, 'duplicate or undeclared'):
            self.read_variant(duplicate)
        def reused_uuid(root):
            nodes = root.findall('./components/comp')
            nodes[1].find('tstamps').text = nodes[0].findtext('tstamps')
        with self.assertRaisesRegex(ValueError, 'duplicate or undeclared'):
            self.read_variant(reused_uuid)


class HierarchyGapTests(unittest.TestCase):
    def setUp(self):
        self.sheets = {'binding.kicad_sch': GEN.schematic().encode()}
        self.graph = read_native_inventory(FIXTURE / 'control.xml')

    def gaps(self):
        return hierarchy_gaps(self.sheets, 'binding.kicad_sch', self.graph)

    def test_control_includes_dnp_board_and_bom_exclusions_but_omits_virtual_power(self):
        self.assertEqual(self.gaps(), [])

    def test_missing_nonvirtual_references_cannot_hide_behind_membership_flags(self):
        for ref in ('R1', 'R2', 'R3', 'R4'):
            with self.subTest(ref=ref):
                original = self.graph['components'].pop(ref)
                self.assertEqual([(r['problem'], r['references']) for r in self.gaps()],
                                 [('unexplained_omission', [ref])])
                self.graph['components'][ref] = original

    def test_extra_native_component_is_refused_even_with_no_missing_reference(self):
        extra = copy.deepcopy(self.graph['components']['R1'])
        extra.update(reference='R99', uuid=GEN.uid('ghost'))
        self.graph['components']['R99'] = extra
        self.assertEqual([(r['problem'], r['references']) for r in self.gaps()],
                         [('native_identity_unmatched', ['R99'])])

    def test_wrong_reference_and_wrong_sheet_identity_are_refused(self):
        self.graph['components']['R99'] = self.graph['components'].pop('R1')
        gaps = self.gaps()
        self.assertEqual(gaps[0]['problem'], 'occurrence_identity_conflict')
        self.assertEqual(gaps[0]['references'], ['R1'])
        self.assertEqual(gaps[0]['native'], 'R99')
        self.graph['components']['R1'] = self.graph['components'].pop('R99')
        self.graph['components']['R1']['sheet'] = '/another-sheet/'
        self.assertEqual({r['problem'] for r in self.gaps()},
                         {'unexplained_omission', 'native_identity_unmatched'})

    def test_virtual_nonpower_and_unresolved_occurrences_fail_closed(self):
        self.sheets['binding.kicad_sch'] = GEN.schematic('virtual-nonpower').encode()
        self.assertEqual([(r['problem'], r['references']) for r in self.gaps()],
                         [('virtual_nonpower_symbol', ['#X1'])])
        self.sheets['binding.kicad_sch'] = GEN.schematic().replace(
            f'(path "/{GEN.uid("root")}" (reference "R1")',
            '(path "/unrelated" (reference "R1")').encode()
        del self.graph['components']['R1']
        self.assertEqual(self.gaps()[0]['problem'], 'missing_occurrence_identity')

    def test_sheet_inventory_mismatch_and_malformed_source_are_refused(self):
        from agent_ee.footprint_library import FootprintError
        self.graph['sheets'].append('/extra/')
        self.assertEqual(self.gaps()[0]['problem'], 'hierarchy_mismatch')
        self.sheets['binding.kicad_sch'] = b'(kicad_sch'
        with self.assertRaisesRegex(FootprintError, 'audit failed'):
            self.gaps()


if __name__ == '__main__':
    unittest.main()
