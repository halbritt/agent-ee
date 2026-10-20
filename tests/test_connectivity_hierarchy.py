"""Original scoped fixtures; electrical expectations are independent of labels."""
import copy
import hashlib
import json
import unittest

from agent_ee.connectivity_ir import canonical_resolved, resolve_intent, validate_resolved
from test_connectivity_ir import LIB, intent


def hierarchy_intent():
    raw = intent()
    raw.update(schema='agent-ee.connectivity-intent/2', hierarchy=[
        dict(id='root', parent=None, local_id='root', file='main.kicad_sch'),
        dict(id='child', parent='root', local_id='child', file='child.kicad_sch')],
        ports=[dict(child='child', id='p', name='SIGNAL', direction='passive', parent_net='n', child_net='n')])
    raw['components'] = [dict(raw['components'][0], sheet=s, reference=r) for s, r in [('root', 'R1'), ('child', 'R2')]]
    raw['nets'] = [dict(sheet=s, id='n', name='SIGNAL', terminals=[dict(component='r', pin={'number': '1'})]) for s in ('root', 'child')]
    raw['no_connects'] = [dict(sheet=s, component='r', pin={'number': '2'}) for s in ('root', 'child')]
    return raw


class HierarchyTests(unittest.TestCase):
    def test_scoped_component_bom_policy_survives_resolution(self):
        from agent_ee.connectivity_ir import _seal
        raw = hierarchy_intent()
        raw['components'][0]['in_bom'] = False
        result = resolve_intent(raw, {'Owned.kicad_sym': LIB})
        validate_resolved(result)
        comp = next(c for c in result['components'] if c['reference'] == raw['components'][0]['reference'])
        self.assertIs(comp['in_bom'], False)
        invalid = copy.deepcopy(result)
        invalid['components'][0]['in_bom'] = 'no'
        invalid.pop('canonical_sha256')
        with self.assertRaisesRegex(ValueError, 'in_bom must be a boolean'):
            validate_resolved(_seal(invalid))

    def resolve(self, raw=None):
        return resolve_intent(hierarchy_intent() if raw is None else raw, {'Owned.kicad_sym': LIB})

    def test_explicit_port_unites_single_pin_segments(self):
        result = self.resolve()
        self.assertEqual((result['execution'], result['outcome'], result['coverage']), ('ok', 'pass', 'complete'), result)
        validate_resolved(result)
        self.assertEqual(result['schema'], 'agent-ee.resolved-connectivity/2')
        self.assertEqual(result['nets'][0]['terminals'], [dict(component='["child","r"]', pin='1'), dict(component='["root","r"]', pin='1')])
        self.assertEqual(len(result['local_nets']), 2)
        self.assertEqual({c['sheet'] for c in result['components']}, {'root', 'child'})

    def test_child_port_cannot_alias_distinct_local_physical_class(self):
        raw = hierarchy_intent()
        raw['no_connects'] = []
        raw['nets'] += [dict(sheet=s, id='other', name='LOCAL',
                            terminals=[dict(component='r', pin={'number': '2'})])
                        for s in ('root', 'child')]
        raw['ports'].append(dict(raw['ports'][0], id='q', name='OTHER',
                                 parent_net='other', child_net='other'))
        validate_resolved(self.resolve(raw))
        raw['ports'][0]['name'] = 'LOCAL'
        result = self.resolve(raw)
        self.assertEqual((result['execution'], result['outcome'], result['coverage']),
                         ('ok', 'defect', 'complete'), result)
        self.assertIn('distinct physical net', result['findings'][0]['message'])

    def test_generated_label_collision_and_rehashed_record_are_rejected(self):
        raw = hierarchy_intent()
        raw['components'].append(dict(raw['components'][1], id='internal', reference='R3'))
        raw['nets'].append(dict(sheet='child', id='internal', name='', terminals=[
            dict(component='internal', pin={'number': n}) for n in ('1', '2')]))
        record = self.resolve(raw)
        validate_resolved(record)
        generated = next(n['label'] for n in record['local_nets'] if n['id'] == 'internal')
        for name in (generated, 'LOCAL'):
            candidate = copy.deepcopy(raw)
            if name == 'LOCAL':
                candidate['nets'][-1]['name'] = name
            passing = self.resolve(candidate)
            validate_resolved(passing)
            passing['ports'][0]['name'] = name
            passing.pop('canonical_sha256')
            passing['canonical_sha256'] = hashlib.sha256(canonical_resolved(passing)).hexdigest()
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, 'distinct physical net'):
                    validate_resolved(passing)
                candidate['ports'][0]['name'] = name
                result = self.resolve(candidate)
                self.assertEqual((result['execution'], result['outcome'], result['coverage']),
                                 ('ok', 'defect', 'complete'), result)

    def test_port_alias_own_class_and_unrelated_sheet_labels_are_allowed(self):
        raw = hierarchy_intent()
        # The child port may alias its own generated label.
        raw['nets'][1]['name'] = ''
        result = self.resolve(raw)
        generated = next(n['label'] for n in result['local_nets'] if n['sheet'] == 'child')
        raw['ports'][0]['name'] = generated
        validate_resolved(self.resolve(raw))
        # A different physical class on the parent is outside the child label scope.
        raw = hierarchy_intent()
        raw['components'].append(dict(raw['components'][0], id='internal', reference='R3'))
        raw['nets'].append(dict(sheet='root', id='internal', name='PARENT_ONLY', terminals=[
            dict(component='internal', pin={'number': n}) for n in ('1', '2')]))
        raw['ports'][0]['name'] = 'PARENT_ONLY'
        validate_resolved(self.resolve(raw))

    def test_port_only_leaf_cannot_be_decorative(self):
        raw = hierarchy_intent()
        raw['nets'][0]['terminals'].append(dict(component='r', pin={'number': '2'}))
        raw['nets'][1]['terminals'] = []
        raw['no_connects'] = [dict(sheet='child', component='r', pin={'number': n}) for n in ('1', '2')]
        result = self.resolve(raw)
        self.assertEqual(result['outcome'], 'defect', result)
        self.assertIn('unused_port', {f['code'] for f in result['findings']})

    def test_pass_through_and_siblings_without_parent_pins(self):
        raw = hierarchy_intent()
        raw['hierarchy'].extend([
            dict(id='middle', parent='root', local_id='middle', file='middle.kicad_sch'),
            dict(id='leaf', parent='middle', local_id='leaf', file='leaf.kicad_sch')])
        raw['components'] = [dict(c, sheet='leaf') if c['sheet'] == 'root' else c for c in raw['components']]
        raw['no_connects'] = [dict(t, sheet='leaf') if t['sheet'] == 'root' else t for t in raw['no_connects']]
        raw['nets'][0]['terminals'] = []
        raw['nets'] += [dict(sheet='middle', id='n', name='SIGNAL', terminals=[]),
                        dict(sheet='leaf', id='n', name='SIGNAL', terminals=[dict(component='r', pin={'number': '1'})])]
        raw['ports'] += [dict(raw['ports'][0], child='middle'), dict(raw['ports'][0], child='leaf')]
        result = self.resolve(raw)
        validate_resolved(result)
        self.assertEqual({(t['component'], t['pin']) for t in result['nets'][0]['terminals']},
                         {('["child","r"]', '1'), ('["leaf","r"]', '1')})
        broken = copy.deepcopy(raw)
        broken['ports'].pop()
        result = self.resolve(broken)
        self.assertEqual(result['outcome'], 'defect')
        self.assertIn('singleton_net', {f['code'] for f in result['findings']})

    def test_same_named_local_nets_do_not_join_and_empty_labels_are_distinct(self):
        raw = hierarchy_intent()
        raw['ports'] = []
        raw['no_connects'] = []
        for net in raw['nets']:
            net['terminals'].append(dict(component='r', pin={'number': '2'}))
        result = self.resolve(raw)
        validate_resolved(result)
        self.assertEqual(len(result['nets']), 2)
        self.assertTrue(all(len(n['terminals']) == 2 for n in result['nets']))
        for net in raw['nets']:
            net['name'] = ''
        result = self.resolve(raw)
        validate_resolved(result)
        labels = [n['label'] for n in result['local_nets']]
        self.assertEqual(len(set(labels)), 2)
        self.assertTrue(all(n.startswith('__agent_ee_') for n in labels))

    def test_group_selector_reuses_captured_resolver(self):
        raw = hierarchy_intent()
        raw['ports'] = []
        raw['no_connects'] = []
        for net in raw['nets']:
            net['terminals'] = [dict(component='r', pin={'name': '~'})]
        result = self.resolve(raw)
        validate_resolved(result)
        self.assertEqual([len(n['terminals']) for n in result['nets']], [2, 2])

    def test_order_is_not_identity_but_port_binding_is(self):
        raw = hierarchy_intent()
        for sheet in ('root', 'child'):
            raw['nets'].append(dict(sheet=sheet, id='second', name='SECOND', terminals=[dict(component='r', pin={'number': '2'})]))
        raw['no_connects'] = []
        raw['ports'].append(dict(raw['ports'][0], id='q', name='PORT_SECOND', parent_net='second', child_net='second'))
        raw['ports'][0]['name'] = 'PORT_FIRST'
        first = self.resolve(raw)
        validate_resolved(first)
        for key in ('libraries', 'components', 'nets', 'hierarchy', 'ports', 'no_connects'):
            raw[key].reverse()
        for net in raw['nets']:
            net['terminals'].reverse()
        self.assertEqual(canonical_resolved(first), canonical_resolved(self.resolve(raw)))
        raw['ports'][0]['child_net'], raw['ports'][1]['child_net'] = raw['ports'][1]['child_net'], raw['ports'][0]['child_net']
        changed = self.resolve(raw)
        validate_resolved(changed)
        self.assertNotEqual(first['canonical_sha256'], changed['canonical_sha256'])
        self.assertNotEqual(first['nets'], changed['nets'])

    def test_scoped_identifiers_are_injective_with_punctuation(self):
        raw = hierarchy_intent()
        raw['components'][0]['id'] = 'r/"[]\\'
        raw['nets'][0]['terminals'][0]['component'] = raw['components'][0]['id']
        raw['no_connects'][0]['component'] = raw['components'][0]['id']
        result = self.resolve(raw)
        validate_resolved(result)
        self.assertEqual({tuple(json.loads(c['id'])) for c in result['components']},
                         {('root', 'r/"[]\\'), ('child', 'r')})

    def test_missing_port_stays_open_and_unknown_pin_is_never_reassigned(self):
        raw = hierarchy_intent()
        raw['ports'] = []
        result = self.resolve(raw)
        self.assertEqual(result['outcome'], 'defect')
        self.assertEqual(sum(f['code'] == 'singleton_net' for f in result['findings']), 2)
        raw = hierarchy_intent()
        raw['nets'][1]['terminals'][0]['pin'] = {'number': '99'}
        result = self.resolve(raw)
        finding = next(f for f in result['findings'] if f['code'] == 'unknown_pin')
        self.assertIn('R2', finding['message'])
        self.assertIn('child', finding['location'])
        self.assertEqual([p['number'] for p in finding['available_pins']], ['1', '2'])

    def test_nc_collision_and_repeated_terminal_remain_defects(self):
        for nc in (True, False):
            raw = hierarchy_intent()
            if nc:
                raw['no_connects'].append(dict(sheet='root', component='r', pin={'number': '1'}))
            else:
                raw['nets'][0]['terminals'].append(copy.deepcopy(raw['nets'][0]['terminals'][0]))
            with self.subTest(nc=nc):
                result = self.resolve(raw)
                self.assertEqual(result['outcome'], 'defect')
                self.assertIn('duplicate_terminal', {f['code'] for f in result['findings']})

    def test_dangling_port_only_net_is_rejected(self):
        raw = hierarchy_intent()
        for net in raw['nets']:
            net['terminals'] = []
        raw['no_connects'] += [dict(sheet=s, component='r', pin={'number': '1'}) for s in ('root', 'child')]
        result = self.resolve(raw)
        self.assertEqual(result['outcome'], 'defect')
        self.assertIn('singleton_net', {f['code'] for f in result['findings']})

    def test_structurally_invalid_trees_ports_scopes_and_paths_rejected(self):
        mutations = [
            lambda r: r['hierarchy'][1].update(parent='child'),
            lambda r: r['hierarchy'][1].update(parent='missing'),
            lambda r: r['hierarchy'][1].update(parent=None),
            lambda r: r['hierarchy'][0].update(local_id='wrong'),
            lambda r: r['hierarchy'].append(dict(id='other', parent='root', local_id='child', file='other.kicad_sch')),
            lambda r: r['components'][0].update(sheet='unknown'),
            lambda r: r['components'][1].update(reference='R1'),
            lambda r: r['nets'][0]['terminals'][0].update(component='["child","r"]'),
            lambda r: r['ports'][0].update(child='root'),
            lambda r: r['ports'][0].update(child_net='missing'),
            lambda r: r['ports'][0].update(direction='power_in'),
            lambda r: r['ports'].append(dict(r['ports'][0], id='q')),
            lambda r: r['ports'].append(dict(r['ports'][0], name='OTHER')),
            lambda r: r['ports'].append(dict(r['ports'][0], id='q', name='OTHER')),
            lambda r: r['nets'].append(dict(r['nets'][0], id='another')),
            lambda r: r['nets'][0].update(name='__agent_ee_collision'),
        ]
        for path in ('/x.kicad_sch', '../x.kicad_sch', './x.kicad_sch', 'x//y.kicad_sch', 'x\\y.kicad_sch', 'board.kicad_pcb', 'main.kicad_sch/x.kicad_sch'):
            mutations.append(lambda r, path=path: r['hierarchy'][1].update(file=path))
        for index, mutate in enumerate(mutations):
            raw = hierarchy_intent()
            mutate(raw)
            with self.subTest(index=index):
                result = self.resolve(raw)
                self.assertNotEqual(result['outcome'], 'pass', result)
                with self.assertRaises(ValueError):
                    validate_resolved(result)

    def test_repeated_file_and_resource_limits_are_unsupported(self):
        raw = hierarchy_intent()
        raw['hierarchy'][1]['file'] = 'main.kicad_sch'
        self.assertEqual(self.resolve(raw)['execution'], 'unsupported')
        raw = hierarchy_intent()
        raw['hierarchy'] += [dict(id=str(i), parent='root', local_id=str(i), file=f'{i}.kicad_sch') for i in range(255)]
        self.assertEqual(self.resolve(raw)['execution'], 'unsupported')
        raw = hierarchy_intent()
        raw['components'] = [dict(raw['components'][0], id=str(i), reference=f'R{i}') for i in range(129)]
        self.assertEqual(self.resolve(raw)['execution'], 'unsupported')
        raw = hierarchy_intent()
        parent = 'child'
        for i in range(16):
            name = str(i)
            raw['hierarchy'].append(dict(id=name, parent=parent, local_id=name, file=f'{name}.kicad_sch'))
            parent = name
        self.assertEqual(self.resolve(raw)['execution'], 'unsupported')

    def test_rehashed_forged_structure_is_not_authority(self):
        mutations = [
            lambda r: r['components'][0].update(sheet='root'),
            lambda r: r['components'][0].update(local_id='different'),
            lambda r: r['local_nets'][0].update(physical_net='forged'),
            lambda r: r['local_nets'][0].update(label='forged'),
            lambda r: r['local_nets'][0]['terminals'][0].update(component='["root","r"]'),
            lambda r: r['nets'][0]['terminals'].pop(),
            lambda r: r['ports'].clear(),
            lambda r: r['hierarchy'][0].update(parent='child'),
            lambda r: r['components'][0]['pins'][0].update(electrical_type='invented'),
            lambda r: r['no_connects'].append(copy.deepcopy(r['nets'][0]['terminals'][0])),
            lambda r: r.update(unexpected=True),
        ]
        for index, mutate in enumerate(mutations):
            record = self.resolve()
            mutate(record)
            record.pop('canonical_sha256')
            record['canonical_sha256'] = hashlib.sha256(canonical_resolved(record)).hexdigest()
            with self.subTest(index=index), self.assertRaises(ValueError):
                validate_resolved(record)

    def test_power_is_explicitly_unsupported_and_capture_failure_dominates(self):
        raw = hierarchy_intent()
        library = LIB.replace(b'(symbol "R"', b'(symbol "R" (power)', 1)
        raw['libraries'][0]['sha256'] = hashlib.sha256(library).hexdigest()
        result = resolve_intent(raw, {'Owned.kicad_sym': library})
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'))
        raw['libraries'].append(dict(name='Absent', path='Absent.kicad_sym', sha256='0' * 64))
        result = resolve_intent(raw, {'Owned.kicad_sym': library})
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'))
        self.assertTrue({'hierarchical_power_unsupported', 'library_coverage'} <= {f['code'] for f in result['findings']})

    def test_flat_v1_bytes_unchanged_from_authoritative_baseline(self):
        # Baseline69d7bbf; independently recorded before extending hierarchy.
        record = resolve_intent(intent(), {'Owned.kicad_sym': LIB})
        self.assertEqual(record['canonical_sha256'], '001c9c04c4ba03043cd561fba4c2b8ff67b98176172132e7e722f94fde0c7b8d')
        self.assertEqual(hashlib.sha256(canonical_resolved(record)).hexdigest(), 'b75316eadcd15ef6e82d2bb4ea98211a7063380fec979427c393476774c6b446')

    def test_cli_v2_reads_declared_captures_and_refuses_partial_resolution(self):
        import tempfile
        from pathlib import Path
        from agent_ee.connectivity_ir import main
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'intent.json').write_text(json.dumps(hierarchy_intent()))
            (root / 'Owned.kicad_sym').write_bytes(LIB)
            self.assertEqual(main([str(root / 'intent.json'), '--captures', tmp, '--out', str(root / 'out')]), 0)
            validate_resolved(json.loads((root / 'out/resolved.json').read_bytes()))
            (root / 'Owned.kicad_sym').write_bytes(LIB + b' ')
            self.assertEqual(main([str(root / 'intent.json'), '--captures', tmp, '--out', str(root / 'bad')]), 1)
            self.assertEqual(json.loads((root / 'bad/resolved.json').read_bytes())['execution'], 'failed')

    def test_non_mapping_capture_is_failed_coverage(self):
        result = resolve_intent(hierarchy_intent(), None)
        self.assertEqual((result['execution'], result['outcome'], result['coverage']), ('failed', 'unknown', 'missing'))

    def test_physical_pin_limit_exact_boundary(self):
        raw = hierarchy_intent()
        raw['hierarchy'] = [raw['hierarchy'][0]]
        raw['components'] = [raw['components'][0]]
        raw['ports'] = []
        raw['nets'] = [dict(sheet='root', id='all', name='ALL', terminals=[dict(component='r', pin={'name': 'A'})])]
        raw['no_connects'] = []
        for count, expected in ((8192, ('ok', 'pass')), (8193, ('unsupported', 'unknown'))):
            library = ('(kicad_symbol_lib (symbol "R" (symbol "R_1_1"' + ''.join(
                f'(pin passive line (at 0 0 0) (name "A") (number "{i}"))' for i in range(count)) + ')))').encode()
            raw['libraries'][0]['sha256'] = hashlib.sha256(library).hexdigest()
            with self.subTest(count=count):
                result = resolve_intent(raw, {'Owned.kicad_sym': library})
                self.assertEqual((result['execution'], result['outcome']), expected, result['findings'])
                if expected[1] == 'pass':
                    validate_resolved(result)
                    self.assertEqual(len(result['nets'][0]['terminals']), count)

    def test_scope_identifier_amplification_is_bounded(self):
        raw = hierarchy_intent()
        raw['hierarchy'][1]['local_id'] = 'x' * 257
        self.assertEqual(self.resolve(raw)['execution'], 'unsupported')

    def test_tree_caps_accept_exact_occurrence_and_depth_boundaries(self):
        raw = hierarchy_intent()
        raw['hierarchy'] += [dict(id=str(i), parent='root', local_id=str(i), file=f'{i}.kicad_sch') for i in range(254)]
        validate_resolved(self.resolve(raw))
        raw = hierarchy_intent()
        parent = 'child'
        for i in range(15):
            name = str(i)
            raw['hierarchy'].append(dict(id=name, parent=parent, local_id=name, file=f'{name}.kicad_sch'))
            parent = name
        validate_resolved(self.resolve(raw))

    def test_v2_flat_physical_projection_matches_v1(self):
        flat = intent()
        flat['no_connects'] = []
        flat['nets'] = [dict(id='n', name='LOCAL', terminals=[dict(component='r', pin={'number': n}) for n in ('1', '2')])]
        legacy = resolve_intent(flat, {'Owned.kicad_sym': LIB})
        raw = dict(flat, schema='agent-ee.connectivity-intent/2',
                   hierarchy=[dict(id='root', parent=None, local_id='root', file='main.kicad_sch')], ports=[])
        raw['components'] = [dict(c, sheet='root') for c in flat['components']]
        raw['nets'] = [dict(n, sheet='root') for n in flat['nets']]
        result = self.resolve(raw)
        validate_resolved(result)
        self.assertEqual(result['components'][0]['pins'], legacy['components'][0]['pins'])
        self.assertEqual(result['nets'][0]['terminals'], [dict(t, component='["root","r"]') for t in legacy['nets'][0]['terminals']])

    def test_array_resource_exhaustion_is_unsupported_not_partial(self):
        raw = hierarchy_intent()
        raw['ports'] *= 10001
        self.assertEqual(self.resolve(raw)['execution'], 'unsupported')


class NativeHierarchyLabelTests(unittest.TestCase):
    def test_native_child_label_alias_collision_and_positive_control(self):
        from unittest.mock import patch
        import xml.etree.ElementTree as ET
        from test_hardware_interface import NativeInterfaceTests, generator

        fixture = NativeInterfaceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        original = generator.schematic('disconnect').replace('(label "OUT"', '(label "LOCAL_SAME"')
        for name, joined in (('LOCAL_SAME', True), ('INDEPENDENT', False)):
            schematic = original[:-2] + (
                f'(hierarchical_label "{name}" (shape passive) (at 120 50 0) '
                f'(effects (font (size 1 1))) (uuid "{generator.uid("collision-hlabel")}")))\n')
            with self.subTest(name=name), patch.object(generator, 'schematic', return_value=schematic):
                run, checks = fixture.run_variant()
                self.assertEqual(checks['netlist']['execution'], 'ok', checks)
                root = ET.parse(run / 'work/netlist/design.xml').getroot()
                probe_nets = [n for n in root.findall('./nets/net')
                              if any(p.get('ref') == 'PROBE' for p in n.findall('node'))]
                self.assertEqual(len(probe_nets), 1)
                refs = {n.get('ref') for n in probe_nets[0].findall('node')}
                self.assertEqual(refs, {'PROBE', 'R1', 'C1', 'RLOAD', 'X1'} if joined else {'PROBE'})
