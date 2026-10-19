"""AGENTEE-43 continuity and failure boundaries with original native fixtures."""
import copy
import hashlib
import json
import tempfile
import unittest
import uuid
from decimal import Decimal
from pathlib import Path

from agent_ee import schematic_emitter as emitter
from agent_ee.schematic_regenerate import regenerate_schematic
from agent_ee.symbol_library import children, parse_sexpr
from test_schematic_emitter import captures, load, native_project, native_netlist, run_native, tables
from toolchain import require_toolchain


def serialize(tree):
    return ('\n'.join(emitter._dump(tree)) + '\n').encode()


def symbols(data):
    return {next(p[2] for p in children(s, 'property') if p[1] == 'Reference'): s
            for s in children(parse_sexpr(data), 'symbol')}


def property_(node, name):
    return next(p for p in children(node, 'property') if p[1] == name)


def add_cap(intent, cid='a_new', reference='C'):
    cap = copy.deepcopy(next(c for c in intent['components'] if c['id'] == 'c_out'))
    cap.update(id=cid, reference=reference)
    intent['components'].append(cap)
    for net in intent['nets']:
        for term in list(net['terminals']):
            if term['component'] == 'c_out':
                net['terminals'].append({**copy.deepcopy(term), 'component': cid})


class RegenerationFixture(unittest.TestCase):
    def setUp(self):
        self.intent, self.layout = load('regulator.intent.json'), load('regulator.layout.json')
        self.captures = captures(self.intent)
        receipt, self.files = emitter.emit_schematic(self.intent, self.captures, self.layout)
        self.assertEqual(receipt['execution'], 'ok')
        self.sch = self.files['regulator.kicad_sch']

    def update(self, intent=None, previous=None, previous_intent=None):
        return regenerate_schematic(self.sch if previous is None else previous,
            self.intent if previous_intent is None else previous_intent,
            self.intent if intent is None else intent, self.captures, self.layout)

    def success(self, *args, **kwargs):
        report, files = self.update(*args, **kwargs)
        self.assertEqual(report['execution'], 'ok', report)
        self.assertEqual(report['outcome'], 'info')
        return report, files

    def refused(self, code, *args, **kwargs):
        report, files = self.update(*args, **kwargs)
        self.assertIsNone(files, report)
        self.assertEqual(report['outcome'], 'unknown')
        self.assertIn(code, [f['code'] for f in report['findings']], report)
        return report

class RegenerationTests(RegenerationFixture):
    def test_noop_repeats_identical_bytes_without_mutating_inputs(self):
        before = copy.deepcopy((self.intent, self.captures, self.layout))
        report, files = self.success()
        self.assertEqual(files, self.files)
        self.assertTrue(report['diff']['equivalent'])
        self.assertEqual((self.intent, self.captures, self.layout), before)
        self.assertEqual(report['previous_schematic_sha256'], hashlib.sha256(self.sch).hexdigest())
        self.assertEqual(report['intent_sha256'], report['previous_intent_sha256'])

    def test_one_resistor_value_keeps_every_other_native_symbol_exact(self):
        # Original passive drawing: reuse the owned rectangle and two passive pins
        # as an authored RES symbol; no installed/vendor library is captured.
        path = 'lib/Owned_Gen.kicad_sym'
        library = emitter._scan(self.captures[path])
        res = copy.deepcopy(next(s for s in children(library, 'symbol') if s[1] == 'CAP'))
        res[1] = emitter.Q('RES')
        property_(res, 'Reference')[2] = emitter.Q('R')
        property_(res, 'Value')[2] = emitter.Q('RES')
        for unit in children(res, 'symbol'):
            unit[1] = emitter.Q(unit[1].replace('CAP_', 'RES_'))
            if unit[1] == 'RES_0_1':
                unit[2:] = [['rectangle', ['start', '-1.27', '1.27'], ['end', '1.27', '-1.27'],
                             ['stroke', ['width', '0.254'], ['type', 'default']], ['fill', ['type', 'none']]]]
        library.append(res)
        self.captures[path] = serialize(library)
        next(l for l in self.intent['libraries'] if l['path'] == path)['sha256'] = hashlib.sha256(self.captures[path]).hexdigest()
        resistor = next(c for c in self.intent['components'] if c['id'] == 'c_out')
        resistor.update(reference='R1', lib_id='OwnedGen:RES', value='1k')
        self.files = emitter.emit_schematic(self.intent, self.captures, self.layout)[1]
        self.sch = self.files['regulator.kicad_sch']
        new = copy.deepcopy(self.intent)
        next(c for c in new['components'] if c['id'] == 'c_out')['value'] = '2k'
        report, files = self.success(new)
        old, after = symbols(self.sch), symbols(files['regulator.kicad_sch'])
        for ref in old:
            if ref != 'R1':
                self.assertEqual(old[ref], after[ref])
            for tag in ('at', 'uuid'):
                self.assertEqual(children(old[ref], tag), children(after[ref], tag))
        self.assertEqual(property_(after['R1'], 'Value')[2], '2k')
        self.assertEqual(report['diff']['hashes']['reference']['placement'],
                         report['diff']['hashes']['subject']['placement'])
        self.assertEqual(report['diff']['typed']['transforms'], {})
        require_toolchain(native=True)
        with tempfile.TemporaryDirectory() as tmp:
            project = native_project({**files, **self.captures,
                **{p: b for p, b in emitter.emit_schematic(self.intent, self.captures, self.layout, **tables())[1].items()
                   if p.endswith('-table')}}, Path(tmp))
            work = Path(tmp) / 'work'
            run_native(project, work / 'erc', 'kicad-cli', 'sch', 'erc', '--format', 'json',
                '--severity-all', '-o', '/work/erc.json', '/candidate/regulator.kicad_sch')
            erc = json.loads((work / 'erc/erc.json').read_text())
            self.assertEqual([v for s in erc['sheets'] for v in s['violations']], [])
            run_native(project, work / 'netlist', 'kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                '-o', '/work/design.xml', '/candidate/regulator.kicad_sch')
            comps, _, _ = native_netlist(work / 'netlist/design.xml')
            self.assertEqual(comps['R1']['value'], '2k')

    def test_adding_before_first_component_keeps_refs_and_avoids_occupied_cells(self):
        new = copy.deepcopy(self.intent)
        add_cap(new)
        report, files = self.success(new)
        old, after = symbols(self.sch), symbols(files['regulator.kicad_sch'])
        for ref in old:
            self.assertEqual(old[ref], after[ref])
        self.assertEqual(set(after) - set(old), {'C2'})
        # Native symbol anchors are distinct, with a clearance beyond the old row.
        new_at = children(after['C2'], 'at')[0][1:3]
        for ref in old:
            self.assertNotEqual(new_at, children(after[ref], 'at')[0][1:3])
            dx = abs(Decimal(new_at[0]) - Decimal(children(after[ref], 'at')[0][1]))
            dy = abs(Decimal(new_at[1]) - Decimal(children(after[ref], 'at')[0][2]))
            # Every fixture body reaches less than 8 mm from its anchor. A
            # 16 mm separation on either axis excludes body collisions without
            # importing the placement planner's own bounds calculation.
            self.assertGreaterEqual(max(dx, dy), Decimal('16'))
        self.assertIn({'action': 'added', 'before': None, 'after': 'C2', 'unit': 1}, report['changes'])
        # Prefix-only references are retained through another generation, even if
        # another component sorts before the first prefix-only component.
        newer = copy.deepcopy(new)
        add_cap(newer, 'a_earlier', 'C')
        again, output = self.success(newer, previous=files['regulator.kicad_sch'],
                                      previous_intent=report['effective_intent'])
        refs = {c['id']: c['reference'] for c in again['effective_intent']['components']}
        self.assertEqual((refs['a_new'], refs['a_earlier']), ('C2', 'C3'))
        self.assertEqual(symbols(output['regulator.kicad_sch'])['C2'], after['C2'])

    def test_connectivity_match_reports_rename_and_keeps_uuid_when_id_changes(self):
        new = copy.deepcopy(self.intent)
        next(c for c in new['components'] if c['id'] == 'c_out').update(id='renamed_cap', reference='C9')
        for net in new['nets']:
            for term in net['terminals']:
                if term['component'] == 'c_out':
                    term['component'] = 'renamed_cap'
        report, files = self.success(new)
        self.assertIn({'action': 'renamed', 'before': 'C1', 'after': 'C9', 'unit': 1}, report['changes'])
        old, after = symbols(self.sch)['C1'], symbols(files['regulator.kicad_sch'])['C9']
        self.assertEqual(children(old, 'uuid'), children(after, 'uuid'))
        self.assertEqual(children(old, 'pin'), children(after, 'pin'))
        self.assertEqual(children(old, 'at'), children(after, 'at'))

    def test_one_connection_change_keeps_transform_and_updates_exact_label(self):
        new = copy.deepcopy(self.intent)
        term = next(t for n in new['nets'] if n['name'] == 'VOUT' for t in n['terminals'] if t['component'] == 'c_out')
        next(n for n in new['nets'] if n['name'] == 'VOUT')['terminals'].remove(term)
        next(n for n in new['nets'] if n['name'] == 'VIN')['terminals'].append(term)
        report, files = self.success(new)
        self.assertEqual(report['diff']['typed']['transforms'], {})
        self.assertNotEqual(files['regulator.kicad_sch'], self.sch)

    def test_simultaneous_forced_reference_and_connection_change_is_reported(self):
        new = copy.deepcopy(self.intent)
        next(c for c in new['components'] if c['id'] == 'c_out')['reference'] = 'C9'
        output = next(n for n in new['nets'] if n['name'] == 'VOUT')
        terminal = next(t for t in output['terminals'] if t['component'] == 'c_out')
        output['terminals'].remove(terminal)
        next(n for n in new['nets'] if n['name'] == 'VIN')['terminals'].append(terminal)
        report, files = self.success(new)
        self.assertIn({'action': 'renamed', 'before': 'C1', 'after': 'C9', 'unit': 1}, report['changes'])
        self.assertEqual(children(symbols(self.sch)['C1'], 'uuid'),
                         children(symbols(files['regulator.kicad_sch'])['C9'], 'uuid'))

    def test_retains_custom_fields_placement_rotation_root_symbol_and_pin_uuids(self):
        layout = {**self.layout, 'transforms': {'c_out': {'rotation': 90, 'mirror': 'x'}}}
        data = emitter.emit_schematic(self.intent, self.captures, layout)[1]['regulator.kicad_sch']
        tree = emitter._scan(data)
        # A human moved the whole drawing by 5.08 mm, keeping pin decorations
        # attached. Library-local coordinates and sheet frame stay untouched.
        for node in tree[1:]:
            if isinstance(node, list) and node[0] in ('symbol', 'label', 'global_label', 'no_connect'):
                for target in [node, *children(node, 'property')]:
                    for at in children(target, 'at'):
                        at[2] = str(Decimal(at[2]) + Decimal('5.08'))
        cap = next(s for s in children(tree, 'symbol') if property_(s, 'Reference')[2] == 'C1')
        cap.append(['property', emitter.Q('OwnerNote'), emitter.Q('keep me'), ['at', '100', '100', '0']])
        property_(cap, 'Value')[2] = emitter.Q('15u')  # human-only change
        children(cap, 'uuid')[0][1] = emitter.Q(str(uuid.uuid4()))
        children(children(cap, 'pin')[0], 'uuid')[0][1] = emitter.Q(str(uuid.uuid4()))
        root = children(tree, 'uuid')[0][1]
        new_root = emitter.Q(str(uuid.uuid4()))
        children(tree, 'uuid')[0][1] = new_root
        for s in children(tree, 'symbol'):
            path = children(children(children(s, 'instances')[0], 'project')[0], 'path')[0]
            path[1] = emitter.Q('/' + new_root)
        previous = serialize(tree)
        report, files = self.success(previous=previous)
        self.assertTrue(report['diff']['equivalent'], report['diff'])
        after = symbols(files['regulator.kicad_sch'])['C1']
        self.assertEqual(after, parse_sexpr(serialize(cap)))
        self.assertNotEqual(root, new_root)
        self.assertEqual(children(parse_sexpr(files['regulator.kicad_sch']), 'uuid')[0][1], new_root)
        new = copy.deepcopy(self.intent)
        next(c for c in new['components'] if c['id'] == 'c_out')['value'] = '22u'
        self.refused('human_edit_conflict', new, previous=previous)

    def test_conflicting_native_connectivity_and_flags_refuse_without_files(self):
        tree = emitter._scan(self.sch)
        label = children(tree, 'label')[0]
        label[1] = emitter.Q('HUMAN_NET')
        report = self.refused('human_edit_conflict', previous=serialize(tree))
        self.assertFalse(report['conflict_diff']['equivalent'])
        tree = emitter._scan(self.sch)
        children(children(tree, 'symbol')[0], 'on_board')[0][1] = 'no'
        self.refused('human_edit_conflict', previous=serialize(tree))

    def test_truncated_wrong_root_duplicate_uuid_and_missing_symbol_stop(self):
        for data in (self.sch[:-12], b'(kicad_pcb)', b'', b'\xff'):
            report, files = self.update(previous=data)
            self.assertIsNone(files)
            self.assertEqual(report['outcome'], 'unknown')
            self.assertTrue(report['findings'])
        tree = emitter._scan(self.sch)
        nodes = children(tree, 'symbol')
        children(nodes[0], 'uuid')[0][1] = children(nodes[1], 'uuid')[0][1]
        self.refused('previous_malformed', previous=serialize(tree))
        tree.remove(nodes[0])
        self.refused('human_edit_conflict', previous=serialize(tree))

    def test_new_symbol_uuid_collision_with_retained_human_uuid_is_refused(self):
        tree = emitter._scan(self.sch)
        children(children(tree, 'symbol')[0], 'uuid')[0][1] = emitter.Q(
            emitter._uuid(uuid.UUID(self.layout['namespace']), 'symbol', 'a_new', 1))
        new = copy.deepcopy(self.intent)
        add_cap(new)
        self.refused('previous_malformed', new, previous=serialize(tree))

    def test_malformed_intent_objects_are_diagnostics(self):
        for intent in ([], {'schema': 'agent-ee.connectivity-intent/1'},
                       {**self.intent, 'components': [None]}, b'{"schema":1,"schema":2}'):
            report, files = self.update(intent)
            self.assertIsNone(files)
            self.assertTrue(report['findings'])

    def test_exact_delivered_input_hashes_and_capacity_failure(self):
        raw = json.dumps(self.intent, indent=3).encode()
        report, files = self.success(raw)
        self.assertEqual(report['inputs']['intent'], hashlib.sha256(raw).hexdigest())
        self.assertNotEqual(report['inputs']['intent'], report['intent_sha256'])
        tree = emitter._scan(self.sch)
        # Preserve the entire electrical drawing but move it beyond A0. No
        # generated replacement is allowed to hide the unsupported capacity.
        for node in tree[1:]:
            if isinstance(node, list) and node[0] in ('symbol', 'label', 'global_label', 'no_connect'):
                for target in [node, *children(node, 'property')]:
                    for at in children(target, 'at'):
                        at[1] = str(Decimal(at[1]) + Decimal('2000'))
        self.refused('paper_overflow', previous=serialize(tree))

    def test_missing_changed_library_unresolved_intent_and_hierarchy_refuse(self):
        missing = copy.deepcopy(self.captures)
        self.captures.clear()
        self.refused('emission_rejected')
        self.captures.update(missing)
        self.captures[next(iter(missing))] += b'\n'
        self.refused('emission_rejected')
        self.captures.update(missing)
        new = copy.deepcopy(self.intent)
        new['no_connects'][0]['pin']['number'] = '999'
        self.refused('emission_rejected', new)
        self.refused('unsupported_hierarchy', {**new, 'schema': 'agent-ee.connectivity-intent/2'})

    def test_ambiguous_connectivity_match_refuses_and_reserved_refs_are_not_reused(self):
        before = copy.deepcopy(self.intent)
        add_cap(before, 'parallel', 'C2')
        old_sch = emitter.emit_schematic(before, self.captures, self.layout)[1]['regulator.kicad_sch']
        new = copy.deepcopy(before)
        for comp in new['components']:
            if comp['reference'] in ('C1', 'C2'):
                comp['reference'] = 'C8' if comp['reference'] == 'C1' else 'C9'
        self.refused('ambiguous_match', new, previous=old_sch, previous_intent=before)
        # Explicitly assigning a retained prefix binding to another component
        # must fail resolution, rather than silently renaming the existing part.
        new = copy.deepcopy(self.intent)
        next(c for c in new['components'] if c['id'] == 'c_out')['reference'] = 'C'
        add_cap(new, reference='C1')
        self.refused('emission_rejected', new)


class NativeRegenerationTests(RegenerationFixture):
    def native(self, files, root):
        project = native_project({**files, **self.captures,
            **{p: b for p, b in emitter.emit_schematic(self.intent, self.captures, self.layout, **tables())[1].items()
               if p.endswith('-table')}}, root)
        work = root / 'work'
        sch = '/candidate/regulator.kicad_sch'
        record = run_native(project, work / 'erc', 'kicad-cli', 'sch', 'erc', '--format', 'json',
            '--severity-all', '-o', '/work/erc.json', sch)
        erc = json.loads((work / 'erc/erc.json').read_text())
        self.assertEqual([v for s in erc['sheets'] for v in s['violations']], [], erc)
        self.assertIn('--unshare-net', record['sandbox_argv'])
        run_native(project, work / 'netlist', 'kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                   '-o', '/work/design.xml', sch)
        return native_netlist(work / 'netlist/design.xml')

    def test_removal_leaves_no_owned_labels_or_dangling_endpoints_and_native_erc_clean(self):
        require_toolchain(native=True)
        new = copy.deepcopy(self.intent)
        new['components'] = [c for c in new['components'] if c['id'] != 'c_out']
        for net in new['nets']:
            net['terminals'] = [t for t in net['terminals'] if t['component'] != 'c_out']
        report, files = self.success(new)
        self.assertIn({'action': 'removed', 'before': 'C1', 'after': None}, report['changes'])
        old_tree, tree = parse_sexpr(self.sch), parse_sexpr(files['regulator.kicad_sch'])
        cap = symbols(self.sch)['C1']
        for node in children(old_tree, 'symbol'):
            if node is not cap and property_(node, 'Reference')[2] != 'C1':
                self.assertEqual(node, symbols(files['regulator.kicad_sch'])[property_(node, 'Reference')[2]])
        self.assertEqual(len(children(old_tree, 'label')) - len(children(tree, 'label')), 1)
        self.assertEqual(len(children(old_tree, 'global_label')) - len(children(tree, 'global_label')), 1)
        self.assertEqual(children(old_tree, 'no_connect'), children(tree, 'no_connect'))
        with tempfile.TemporaryDirectory() as tmp:
            comps, nets, singles = self.native(files, Path(tmp))
        self.assertNotIn('C1', comps)
        self.assertEqual(set(comps), {'J1', 'U1'})
        self.assertEqual(nets['/VOUT'], [['J1', '3'], ['U1', '3']])
        self.assertEqual(singles, {'unconnected-(U1-NC-Pad4)': (['U1', '4'], 'no_connect')})

    def test_native_addition_and_value_change_keep_bound_references(self):
        require_toolchain(native=True)
        new = copy.deepcopy(self.intent)
        add_cap(new)
        next(c for c in new['components'] if c['id'] == 'c_out')['value'] = '22u'
        _, files = self.success(new)
        with tempfile.TemporaryDirectory() as tmp:
            comps, nets, singles = self.native(files, Path(tmp))
        self.assertEqual(set(comps), {'J1', 'U1', 'C1', 'C2'})
        self.assertEqual(nets['/VOUT'], [['C1', '1'], ['C2', '1'], ['J1', '3'], ['U1', '3']])
        for ref in ('J1', 'U1', 'C1'):
            self.assertEqual(comps[ref]['uuid'], children(symbols(self.sch)[ref], 'uuid')[0][1])

    def test_native_connection_repair_uses_retained_pin_endpoints(self):
        require_toolchain(native=True)
        new = copy.deepcopy(self.intent)
        output = next(n for n in new['nets'] if n['name'] == 'VOUT')
        term = next(t for t in output['terminals'] if t['component'] == 'c_out')
        output['terminals'].remove(term)
        next(n for n in new['nets'] if n['name'] == 'VIN')['terminals'].append(term)
        _, files = self.success(new)
        with tempfile.TemporaryDirectory() as tmp:
            comps, nets, singles = self.native(files, Path(tmp))
        self.assertEqual(nets['/VIN'], [['C1', '1'], ['J1', '1'], ['U1', '1']])
        self.assertEqual(nets['/VOUT'], [['J1', '3'], ['U1', '3']])


if __name__ == '__main__':
    unittest.main()
