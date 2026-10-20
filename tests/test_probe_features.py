"""Owned six-pad tracer mixed with purchased parts; no pod acceptance claim."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest
import uuid

from agent_ee.storage import digest, identity, read_json, write_json
from agent_ee import probe_features as pf
from agent_ee.parts_manifest import qualify_manifest
from agent_ee.binding_fill import fill
from tests.test_parts_subset import captured_fixture
from tests.test_approved_parts import named_fixture
from tests.toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / 'tests/fixtures/probe-features'


def mixed_fixture(test, root):
    parts = captured_fixture(test, root)
    named_fixture(root, parts)
    parts['parts'][0]['instances'] = [{'sheet': '/', 'reference': 'R' + str(i)} for i in range(1, 4)]
    freeze = read_json(FROZEN / 'freeze.json')
    for path, sha in freeze.items():
        test.assertEqual(digest(FROZEN / path), sha)
    for path, dest in [('PodFeatures.kicad_sym', 'PodFeatures.kicad_sym'),
                       ('PogoPad_D1.5mm.kicad_mod', 'PodFeatures.pretty/PogoPad_D1.5mm.kicad_mod')]:
        target = root / dest
        target.parent.mkdir(exist_ok=True)
        target.write_bytes((FROZEN / path).read_bytes())
    proposal = read_json(FROZEN / 'proposal.json')['physical']
    library = {'name': 'PodFeatures', 'path': 'PodFeatures.kicad_sym', 'sha256': digest(root / 'PodFeatures.kicad_sym')}
    fp = {'path': 'PodFeatures.pretty/PogoPad_D1.5mm.kicad_mod',
          'sha256': digest(root / 'PodFeatures.pretty/PogoPad_D1.5mm.kicad_mod')}
    assignments = read_json(root / 'assignments.json')
    assignments['libraries'].append(library)
    assignments['components'].append({'reference': 'uart-pogo', 'lib_id': 'PodFeatures:PogoPad',
        'footprint': 'PodFeatures:PogoPad_D1.5mm', 'assembly': 'smd', 'connector': None})
    write_json(root / 'assignments.json', assignments)
    cad = read_json(root / 'cad.json')
    cad['libraries'].append({'name': 'PodFeatures', 'members': [fp]})
    write_json(root / 'cad.json', cad)
    for key in ('manifest', 'assignments'):
        parts['cad'][key]['sha256'] = digest(root / parts['cad'][key]['path'])
    closure = [fp] + [m for lib in cad['libraries'] if lib['name'] != 'PodFeatures' for m in lib['members']]
    closure += [{k: lib[k] for k in ('path', 'sha256')} for lib in assignments['libraries']]
    parts['cad']['members_sha256'] = identity(sorted(closure, key=lambda r: r['path']))
    parts['parts'].append({'kind': pf.KIND, 'id': 'uart-pogo', 'role': 'UART programming and sense',
        'required': True, 'instances': [{'sheet': '/', 'reference': row['reference'], 'net': row['net'], 'native_net': '/' + row['net']} for row in proposal],
        'lib_id': 'PodFeatures:PogoPad', 'footprint': 'PodFeatures:PogoPad_D1.5mm',
        'probe': {'symbol': {k: library[k] for k in ('path', 'sha256')}, 'footprint': fp,
                  'pin': {'number': '1', 'name': 'PROBE', 'electrical_type': 'passive'}}})
    # A source-declared exact list, separate from the writer's identity properties.
    (root / 'requirements.md').write_text('Owned mixed test declaration: three purchased resistors and six required probes.\n' +
        '\n'.join(row['reference'] + ' pad 1 on ' + row['net'] for row in proposal) + '\n')
    parts['declaration_source']['sha256'] = digest(root / 'requirements.md')
    write_json(root / 'parts.json', parts)
    intent = {'schema': 'agent-ee.connectivity-intent/1', 'libraries': assignments['libraries'],
        'components': [], 'nets': [], 'no_connects': []}
    refs = {'schema': 'agent-ee.part-refs/1', 'components': {}}
    for i in range(1, 4):
        cid = 'resistor' + str(i)
        intent['components'].append({'id': cid, 'reference': 'R' + str(i), 'lib_id': 'Owned:R',
            'footprint': 'Owned:R2', 'value': '10k', 'units': [1]})
        refs['components'][cid] = {'part_ref': 'resistor'}
    for i, row in enumerate(proposal):
        intent['components'].append({'id': row['component'], 'reference': row['reference'],
            'lib_id': row['lib_id'], 'footprint': row['footprint'], 'value': 'PogoPad', 'units': [1]})
        refs['components'][row['component']] = {'part_ref': 'uart-pogo'}
        intent['nets'].append({'id': 'net' + str(i), 'name': row['net'], 'terminals': [
            {'component': row['component'], 'pin': {'number': '1'}},
            {'component': 'resistor' + str(i // 2 + 1), 'pin': {'number': str(i % 2 + 1)}}]})
    deviations = {'schema': 'agent-ee.part-deviations/1', 'deviations': []}
    qualify_manifest(parts, root)
    return parts, intent, refs, deviations


class ProbeContractTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.parts, self.intent, self.refs, self.deviations = mixed_fixture(self, self.root)

    def filled(self):
        return fill(self.root, self.intent, self.parts, self.refs, self.deviations, captures='.')[0]

    def test_marker_encoding_roundtrip_and_legacy_identity(self):
        from agent_ee.storage import canonical
        record = pf.declarations(self.parts, self.root)['TP1']
        for value in (pf.marker(record), canonical(record).decode()):
            self.assertEqual(pf.parse_marker(value), record)
            self.assertTrue(pf.matches_marker(value, record))
            self.assertFalse(pf.matches_marker(value, dict(record, reference='TP99')))
        self.assertNotIn(':', pf.marker(record))
        self.assertLess(len(pf.marker(record)), 4096)
        for value in (None, '', 'agent-ee-probe-v2-AAAA'):
            self.assertFalse(pf.matches_marker(value, record))

    def test_encoded_marker_rejects_malformed_payload_and_schema(self):
        import base64
        from agent_ee.storage import canonical
        record = pf.declarations(self.parts, self.root)['TP1']
        invalid = ['!', 'AAAA', '/w==', 'e30=',
                   base64.b64encode(canonical(dict(record, schema='unknown'))).decode(),
                   base64.b64encode(canonical(dict(record, extra=True))).decode()]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                pf.parse_marker(pf.MARKER_PREFIX + payload)

    def test_explicit_mixed_declaration_and_fill_without_supplier_claims(self):
        filled = self.filled()
        probes = pf.declarations(self.parts, self.root)
        self.assertEqual(set(probes), {'TP' + str(i) for i in range(1, 7)})
        for comp in filled['components']:
            if comp['reference'] in probes:
                self.assertEqual(comp['part_binding'], {pf.PROPERTY: pf.marker(probes[comp['reference']])})
            else:
                self.assertEqual(comp['part_binding']['LCSC'], 'C38695')

    def test_saved_pad_net_is_the_board_form_of_the_declared_native_net(self):
        """AGENTEE-220: KiCad saves a literal slash as {slash}; sheet separators stay literal."""
        import xml.etree.ElementTree as ET
        from agent_ee.board_net_names import from_xml
        from agent_ee.scaffold import sexpr
        from agent_ee.schematic_emit import Quoted as Q
        base = pf.declarations(self.parts, self.root)['TP1']
        for declared, saved, accepted in [('A/B', 'A{slash}B', True), ('A/B', 'A/B', False),
                                          ('/POD/TX', '/POD/TX', True), ('/POD/TX', '/POD{slash}TX', False),
                                          ('unconnected-(TP1-SDA/~{SPI_CS}-Pad1)',
                                           'unconnected-(TP1-SDA{slash}~{SPI_CS}-Pad1)', True)]:
            record = dict(base, native_net=declared)
            if accepted:
                root = ET.fromstring('<export><design><sheet name="/POD/" tstamps="/1/"/></design><components/>'
                                     '<nets><net code="1" name=%s/></nets></export>' % json.dumps(declared))
                self.assertEqual(from_xml(root)[declared], saved)
            prop = lambda name, value, layer: ['property', Q(name), Q(value), ['at', '0', '0', '0'], ['layer', Q(layer)]]
            board = sexpr(['kicad_pcb', ['version', '20260206'], ['generator', Q('pcbnew')],
                ['footprint', Q('PodFeatures:PogoPad_D1.5mm'), ['layer', Q('F.Cu')],
                 prop('Reference', 'TP1', 'F.SilkS'), prop('Value', 'PogoPad', 'F.Fab'),
                 prop('Footprint', 'PodFeatures:PogoPad_D1.5mm', 'F.Fab'),
                 prop(pf.PROPERTY, pf.marker(record), 'F.Fab'),
                 ['attr', 'smd', 'exclude_from_bom', 'exclude_from_pos_files'],
                 ['pad', Q('1'), 'smd', 'circle', ['at', '0', '0'], ['size', '1.5', '1.5'],
                  ['layers', Q('F.Cu'), Q('F.Mask')], ['solder_mask_margin', '0.05'], ['net', '1', Q(saved)]]]])
            with self.subTest(declared=declared, saved=saved):
                if accepted:
                    self.assertEqual(pf.validate_board(board, {'TP1': record}), {'TP1'})
                else:
                    with self.assertRaisesRegex(ValueError, 'probe saved pad net differs'):
                        pf.validate_board(board, {'TP1': record})

    def test_purchased_part_masquerading_as_probe_feature_refused(self):
        for name, value in [('lcsc', 'C123'), ('lcsc', 'NO_LCSC'), ('stock', 1), ('datasheet', {}),
                            ('selection', {}), ('required', False)]:
            bad = copy.deepcopy(self.parts)
            bad['parts'][1][name] = value
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                qualify_manifest(bad, self.root)
        bad = copy.deepcopy(self.parts)
        bad['parts'][1]['probe']['symbol'] = {k: self.intent['libraries'][0][k] for k in ('path', 'sha256')}
        bad['parts'][1]['lib_id'] = 'Owned:R'
        with self.assertRaisesRegex(ValueError, 'in_bom|pin identity'):
            qualify_manifest(bad, self.root)

    def test_changed_feature_bytes_refused(self):
        for path in ('PodFeatures.kicad_sym', 'PodFeatures.pretty/PogoPad_D1.5mm.kicad_mod'):
            target = self.root / path
            old = target.read_bytes()
            target.write_bytes(old + b'\n')
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'hash mismatch'):
                self.filled()
            target.write_bytes(old)

    def test_probe_source_closure_must_be_captured_and_immutable(self):
        from agent_ee.part_binding import capability
        files = {p.relative_to(self.root).as_posix(): digest(p) for p in self.root.rglob('*') if p.is_file()}
        files['probe.kicad_sch'] = '0' * 64
        params = {'probe_manifest': 'parts.json', 'netlist_check': 'netlist', 'schematic': 'probe.kicad_sch'}
        inputs = capability(params, {}, files, self.root)['immutable_inputs']
        self.assertIn('parts.json', inputs)
        self.assertIn('PodFeatures.kicad_sym', inputs)
        self.assertIn('PodFeatures.pretty/PogoPad_D1.5mm.kicad_mod', inputs)
        for path in inputs:
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'captured and immutable'):
                capability(params, {'mutable_inputs': [path]}, files, self.root)
        files.pop('PodFeatures.kicad_sym')
        with self.assertRaisesRegex(ValueError, 'captured and immutable'):
            capability(params, {}, files, self.root)
        check = {'id': 'parts', 'kind': 'parts_subset', 'params': {
            'manifest': 'parts.json', 'netlist_check': 'netlist', 'requirement_id': 'membership'}}
        self.assertEqual(pf.membership_check(params, {'checks': [check]}), 'parts')
        for checks in ([], [check, copy.deepcopy(check)], [{**check, 'params': {
                **check['params'], 'manifest': 'different.json'}}], [{**check, 'params': {
                **check['params'], 'netlist_check': 'different'}}]):
            with self.subTest(checks=checks), self.assertRaisesRegex(ValueError, 'matching parts_subset'):
                pf.membership_check(params, {'checks': checks})

    def test_missing_feature_refused_during_binding_fill(self):
        self.intent['components'] = [c for c in self.intent['components'] if c['reference'] != 'TP1']
        del self.refs['components']['debug/tp1']
        self.intent['nets'].pop(0)
        self.intent['no_connects'].append({'component': 'resistor1', 'pin': {'number': '1'}})
        with self.assertRaisesRegex(ValueError, r'missing probe feature\(s\): TP1'):
            self.filled()

    def test_rc_only_producer_refuses_probe_features(self):
        from agent_ee.rung1_parts import fields_for_rung
        params = {'resistor': {'ref': 'R1'}, 'capacitor': {'ref': 'C1'},
                  'header': {'ref': 'J1', 'assembly': 'assembled'}}
        with self.assertRaisesRegex(ValueError, 'does not support bare_copper_probe'):
            fields_for_rung(self.parts, qualify_manifest(self.parts, self.root), params)

    def test_interval_catalog_cannot_resolve_probe_as_purchased_part(self):
        from tests.test_interval_assertions import Workspace, parts_manifest
        workspace = self.root / 'interval'; workspace.mkdir()
        ws = Workspace(workspace)
        path = parts_manifest(ws, {'R1': 'C900001', 'R2': 'C900003'})
        parts = read_json(workspace / path)
        parts['parts'].append(copy.deepcopy(self.parts['parts'][1]))
        write_json(workspace / path, parts)
        document = copy.deepcopy(ws.document)
        document['parameters']['r2'] = {'source': 'catalog', 'part': 'uart-pogo',
                                       'attribute': 'resistance', 'role': 'toleranced'}
        report = ws.evaluate(document, {**ws.params(), 'parts_manifest': path})
        self.assertNotEqual(report['outcome'], 'pass', report)
        self.assertIn('part absent', str(report))

    def test_exact_net_and_reference_allocation_required(self):
        self.intent['nets'][0]['name'] = 'WRONG'
        with self.assertRaisesRegex(ValueError, 'probe intent net differs: TP1'):
            self.filled()
        self.intent['nets'][0]['name'] = 'POD_UART_TX'
        self.intent['components'][3]['reference'] = 'TP99'
        with self.assertRaisesRegex(ValueError, r'missing probe feature\(s\): TP1'):
            self.filled()

    def test_extra_copper_drill_paste_pins_and_offboard_refused(self):
        from agent_ee.symbol_library import parse_sexpr
        original = parse_sexpr((FROZEN / 'PogoPad_D1.5mm.kicad_mod').read_bytes())
        mutations = [lambda n: n.append(['zone']),
            lambda n: n.append(['fp_line', ['start', '0', '0'], ['end', '1', '0'], ['layer', 'F.Cu']]),
            lambda n: n.append(copy.deepcopy(next(c for c in n if isinstance(c, list) and c[0] == 'pad')))]
        for mutate in mutations:
            node = copy.deepcopy(original); mutate(node)
            with self.assertRaises(ValueError):
                pf.footprint_signature(node, '1')
        from agent_ee.symbol_library import children
        for extra in (['drill', '1'], ['layers', 'F.Cu', 'F.Mask', 'F.Paste'], ['primitives']):
            node = copy.deepcopy(original)
            children(node, 'pad')[0].append(extra)
            with self.assertRaises(ValueError):
                pf.footprint_signature(node, '1')
        from agent_ee.symbol_library import parse_library
        symbol = parse_library((FROZEN / 'PodFeatures.kicad_sym').read_bytes())['PogoPad']
        pin = self.parts['parts'][1]['probe']['pin']
        added_pin = copy.deepcopy(symbol)
        unit = children(added_pin, 'symbol')[0]
        second = copy.deepcopy(children(unit, 'pin')[0])
        children(second, 'number')[0][1] = '2'
        unit.append(second)
        with self.assertRaisesRegex(ValueError, 'pin identity differs'):
            pf.symbol_signature(added_pin, 'PodFeatures:PogoPad', pin)
        for flag, value in [('on_board', 'no'), ('in_bom', 'yes')]:
            changed = copy.deepcopy(symbol); children(changed, flag)[0][1] = value
            with self.assertRaisesRegex(ValueError, 'in_bom=no/on_board=yes'):
                pf.symbol_signature(changed, 'PodFeatures:PogoPad', pin)


class NativeProbeTests(unittest.TestCase):
    setUp = ProbeContractTests.setUp
    filled = ProbeContractTests.filled
    def native(self):
        require_toolchain(native=True)
        from agent_ee.schematic_emitter import emit_schematic
        intent = self.filled()
        libraries = {lib['path']: (self.root / lib['path']).read_bytes() for lib in intent['libraries']}
        result, files = emit_schematic(intent, libraries, {'schema': 'agent-ee.schematic-layout/1',
            'namespace': str(uuid.UUID('81ae10f4-1c43-4370-88c2-32c41b90fd70')), 'project': 'probe', 'transforms': {}})
        self.assertEqual(result['execution'], 'ok', result)
        # The same mixed declaration first qualifies through the empty scaffold.
        from agent_ee.scaffold import scaffold
        project = self.root / 'project'; project.mkdir()
        shutil.copyfile(ROOT / 'fixtures/netclass-completeness/native.kicad_pro', project / 'probe.kicad_pro')
        envelope = read_json(ROOT / 'fixtures/board-envelope/rc.envelope.json')
        envelope['fixed_footprints'] = []
        envelope['outline']['edges'] = [
            {'name': 'top', 'start': [0, 0], 'end': [80, 0]},
            {'name': 'right', 'start': [80, 0], 'end': [80, 50]},
            {'name': 'bottom', 'start': [80, 50], 'end': [0, 50]},
            {'name': 'left', 'start': [0, 50], 'end': [0, 0]}]
        write_json(self.root / 'envelope.json', envelope)
        scope = {'schema': 'agent-ee.scope/2', 'name': 'Owned mixed probe fixture',
            'scope': 'Captured manufacture-feature pipeline contract only; no hardware acceptance.',
            'schematic': 'project/probe.kicad_sch', 'board': 'project/probe.kicad_pcb',
            'mutable_inputs': ['project/probe.kicad_sch', 'project/probe.kicad_pcb'],
            'inputs': [{'path': str(p), 'at': p.relative_to(self.root).as_posix()}
                       for p in sorted(self.root.rglob('*')) if p.is_file()],
            'checks': [{'id': 'netlist', 'kind': 'netlist'}, {'id': 'parts', 'kind': 'parts_subset',
                'params': {'manifest': 'parts.json', 'netlist_check': 'netlist', 'requirement_id': 'probe-membership'}}],
            'baseline': {'requirements': [{'id': 'probe-membership', 'statement': 'All declared instances remain present.',
                'source': {'path': 'requirements.md', 'locator': 'line 1'}, 'checks': ['netlist', 'parts']}],
                'targets': ['requirements.md'], 'reference_architectures': ['requirements.md']}}
        write_json(self.root / 'scope.json', scope)
        receipt = scaffold(self.root / 'envelope.json', self.root / 'parts.json', self.root / 'scaffold',
                           scope_path=self.root / 'scope.json')
        self.assertEqual(receipt['status'], 'completed')
        self.candidate = self.root / 'scaffold/inputs'
        self.assertEqual(read_json(self.candidate / 'parts.json'), self.parts)
        project = self.candidate / 'project'
        self.empty_board = (project / 'probe.kicad_pcb').read_bytes()
        for path, data in files.items():
            target = project / path; target.parent.mkdir(exist_ok=True, parents=True); target.write_bytes(data)
        shutil.copyfile(ROOT / 'fixtures/netclass-completeness/native.kicad_pro', project / 'probe.kicad_pro')
        from tests.test_schematic_emitter import run_native
        work = self.root / 'netlist'
        run_native(project, work, 'kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                   '-o', '/work/design.xml', '/candidate/probe.kicad_sch')
        xml = work / 'design.xml'
        from agent_ee.parts_subset import evaluate
        shutil.copyfile(project / 'probe.kicad_sch', self.root / 'probe.kicad_sch')
        report = evaluate(self.root, 'parts.json', xml, ['probe.kicad_sch'])
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report)
        return project, xml

    def test_native_six_features_schematic_board_and_fab(self):
        project, xml = self.native()
        from agent_ee.board_build import read_netlist, construct_isolated, read_envelope, BoardBuildError, ToolFailure
        envelope = read_envelope({'schema': 'agent-ee.rung1-envelope/0-experimental',
            'outline_mm': {'x': 0, 'y': 0, 'w': 80, 'h': 50}, 'copper_layers': 2,
            'stackup': 'owned test template', 'fixed_refs': [
                {'ref': 'TP' + str(i), 'x_mm': 10 + i * 5, 'y_mm': 10, 'rotation_deg': 0, 'side': 'F'} for i in range(1, 7)] + [
                {'ref': 'R' + str(i), 'x_mm': 10 + i * 10, 'y_mm': 25, 'rotation_deg': 0, 'side': 'F'} for i in range(1, 4)]})
        board = project / 'probe.kicad_pcb'
        spec = {'netlist': read_netlist(xml.read_bytes()), 'envelope': envelope,
            'template': str(ROOT / 'fixtures/rc/control/rc_filter.kicad_pcb'),
            'footprints': {p['footprint']: str(self.root / p['probe']['footprint']['path']) if pf.is_probe(p)
                           else str(self.root / 'Owned.pretty/R2.kicad_mod') for p in self.parts['parts']},
            'output': str(board), 'seed_hashes': [digest(xml)]}
        try:
            construct_isolated(spec, self.root / 'construct')
        except (BoardBuildError, ToolFailure) as exc:
            self.fail(str(exc) + '\n' + (self.root / 'construct/construct.stderr.txt').read_text())
        probes = pf.declarations(self.parts, self.root)
        self.assertEqual(pf.validate_board(board.read_bytes(), probes), set(probes))
        from agent_ee.native_netlist import read_native_inventory
        from agent_ee.part_binding import validate_part_bindings
        report = validate_part_bindings((project / 'probe.kicad_sch').read_bytes(), board.read_bytes(),
            require_datasheet=True, probes=probes, native_graph=read_native_inventory(xml))
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report)
        strict = validate_part_bindings((project / 'probe.kicad_sch').read_bytes(), board.read_bytes(),
            require_params=True, probes=probes, native_graph=read_native_inventory(xml))
        self.assertEqual(strict['outcome'], 'unknown', strict)
        self.assertEqual({row['reference'] for row in strict['missing_bindings']}, {'R1', 'R2', 'R3'})
        from agent_ee.fab_export import board_facts, part_bindings, assembled_parts
        facts = board_facts(board.read_bytes(), probes)
        self.assertEqual(facts['unsupported'], [])
        parts, disagreements, problems = assembled_parts(part_bindings(xml, probes)[0], facts['footprints'])
        self.assertEqual((disagreements, problems), ([], []))
        self.assertEqual(set(parts), {'R1', 'R2', 'R3'})
        self.reject_native_mutations(project, board, probes)
        self.annotation(xml, probes)
        self.full_fab(project, probes)

    def reject_native_mutations(self, project, board, probes):
        from agent_ee.symbol_library import children
        from agent_ee.scaffold import sexpr
        from agent_ee.schematic_emit import parse_quoted, Quoted as Q
        from agent_ee.fab_export import board_facts
        from tests.test_schematic_emitter import run_native
        original = board.read_bytes()
        def copper(fp):
            fp.append(['fp_line', ['start', '0', '0'], ['end', '1', '0'],
                       ['stroke', ['width', '.2'], ['type', 'default']], ['layer', Q('F.Cu')]])
        def drilled(fp):
            pad = children(fp, 'pad')[0]
            pad[2] = 'thru_hole'
            pad.append(['drill', '.3'])
        def marker_property(fp):
            return next(p for p in children(fp, 'property') if p[1] == pf.PROPERTY)
        def tampered_marker(fp):
            prop = marker_property(fp)
            prop[2] = Q(pf.marker(dict(pf.parse_marker(prop[2]), native_net='/WRONG')))
        edits = {
            'missing_marker': lambda fp: fp.remove(marker_property(fp)),
            'tampered_marker': tampered_marker,
            'extra_copper': copper,
            'extra_pad': lambda fp: fp.append(copy.deepcopy(children(fp, 'pad')[0])),
            'drill': drilled,
            'paste': lambda fp: children(children(fp, 'pad')[0], 'layers')[0].append(Q('F.Paste')),
            'hidden_copper': lambda fp: children(children(fp, 'pad')[0], 'layers')[0].append(Q('B.Cu')),
            'DNP': lambda fp: children(fp, 'attr')[0].append('dnp'),
            'off_board': lambda fp: children(fp, 'attr')[0].append('board_only'),
            'supplier': lambda fp: fp.append(['property', Q('LCSC'), Q('C123'), ['at', '0', '0', '0'],
                                            ['layer', Q('F.Fab')], ['effects', ['font', ['size', '1', '1']]]]),
        }
        for name, mutate in edits.items():
            with self.subTest(saved_native_mutation=name):
                tree = parse_quoted(original)
                fp = next(fp for fp in children(tree, 'footprint') if pf.properties(fp)['Reference'] == 'TP1')
                mutate(fp)
                (project / 'mutated.kicad_pcb').write_bytes(sexpr(tree))
                work = self.root / ('native-mutation-' + name)
                run_native(project, work, '/usr/bin/python3', '-c',
                    'import pcbnew; b=pcbnew.LoadBoard("/candidate/mutated.kicad_pcb"); '
                    'assert pcbnew.SaveBoard("/work/saved.kicad_pcb",b)')
                data = (work / 'saved.kicad_pcb').read_bytes()
                with self.assertRaises(ValueError):
                    pf.validate_board(sexpr(tree), probes)
                with self.assertRaises(ValueError):
                    pf.validate_board(data, probes)
        # An ordinary purchased BOM exclusion cannot use the feature exemption.
        tree = parse_quoted(original)
        fp = next(fp for fp in children(tree, 'footprint') if pf.properties(fp)['Reference'] == 'R1')
        children(fp, 'attr')[0].append('exclude_from_bom')
        unsupported = board_facts(sexpr(tree), probes)['unsupported']
        self.assertTrue(any('R1' in message and 'non-DNP exclusions' in message for message in unsupported), unsupported)

    def annotation(self, xml, probes):
        from agent_ee.forward_annotation import plan, apply
        empty = self.root / 'empty.kicad_pcb'; empty.write_bytes(self.empty_board)
        manifest = read_json(self.root / 'cad.json')
        planned = plan(empty, xml, manifest, self.root, self.root / 'annotation-plan')
        self.assertEqual(planned['execution'], 'ok', planned)
        output = self.root / 'annotated.kicad_pcb'
        result = apply(planned, empty, xml, manifest, self.root, output, self.root / 'annotation-apply')
        self.assertEqual(result['execution'], 'ok', result)
        self.assertEqual(pf.validate_board(output.read_bytes(), probes), set(probes))
        # Changed geometry survives native parsing but cannot survive replanning.
        data = output.read_bytes().replace(b'(size 1.5 1.5)', b'(size 1.6 1.6)', 1)
        self.assertNotEqual(data, output.read_bytes())
        output.write_bytes(data)
        rejected = plan(output, xml, manifest, self.root, self.root / 'annotation-bad')
        self.assertEqual(rejected['execution'], 'failed', rejected)
        self.assertEqual(rejected['findings'][0]['code'], 'probe_feature_mismatch')

    def test_missing_feature_refused_by_name(self):
        from agent_ee.parts_subset import compare_inventory
        project, xml = self.native()
        from agent_ee.native_netlist import read_native_inventory
        graph = read_native_inventory(xml)
        from agent_ee.pin_contracts import _bindings
        rows, coverage = _bindings(self.parts, {}, graph, {}, {})
        self.assertEqual(rows, [])
        probe_rows = [r for r in coverage if r['reference'].startswith('TP')]
        self.assertEqual(len(probe_rows), 6)
        self.assertTrue(all(r['status'] == 'unsupported' and 'not purchased-part electrical evidence' in r['reason']
                            for r in probe_rows), coverage)
        del graph['components']['TP1']
        report = compare_inventory(self.parts, graph, [], probes=pf.declarations(self.parts, self.root))
        row = next(row for row in report['matches'] if row['reference'] == 'TP1')
        self.assertEqual(row['status'], 'defect')
        self.assertEqual(row['findings'][0]['code'], 'required_absent')
        from agent_ee.schematic_emit import parse_quoted
        from agent_ee.symbol_library import children
        from agent_ee.scaffold import sexpr
        tree = parse_quoted((project / 'probe.kicad_sch').read_bytes())
        missing = next(s for s in children(tree, 'symbol') if pf.properties(s)['Reference'] == 'TP1')
        tree.remove(missing)
        (project / 'probe.kicad_sch').write_bytes(sexpr(tree))
        report = self.reexport(project, 'missing')
        self.assertNotEqual(report['outcome'], 'pass', report)
        self.assertIn('TP1', str(report['findings']))

    def test_swapped_feature_and_marker_without_source_refused_by_name(self):
        from agent_ee.parts_subset import compare_inventory
        from agent_ee.native_netlist import read_native_inventory
        project, xml = self.native()
        graph = read_native_inventory(xml)
        graph['components']['TP1']['footprint'] = 'Owned:R2'
        report = compare_inventory(self.parts, graph, [], probes=pf.declarations(self.parts, self.root))
        row = next(row for row in report['matches'] if row['reference'] == 'TP1')
        self.assertEqual(row['status'], 'defect')
        self.assertIn('TP1: probe native identity', row['findings'][0]['message'])
        from agent_ee.part_binding import validate_part_bindings
        binding = validate_part_bindings((project / 'probe.kicad_sch').read_bytes())
        self.assertEqual(binding['outcome'], 'defect')
        self.assertTrue(any('immutable source declaration' in r['reason'] for r in binding['invalid_bindings']))
        from agent_ee.schematic_emit import parse_quoted, Quoted as Q
        from agent_ee.symbol_library import children
        from agent_ee.scaffold import sexpr
        tree = parse_quoted((project / 'probe.kicad_sch').read_bytes())
        swapped = next(s for s in children(tree, 'symbol') if pf.properties(s)['Reference'] == 'TP1')
        prop = next(p for p in children(swapped, 'property') if p[1] == 'Footprint')
        prop[2] = Q('Owned:R2')
        (project / 'probe.kicad_sch').write_bytes(sexpr(tree))
        report = self.reexport(project, 'swapped')
        self.assertNotEqual(report['outcome'], 'pass', report)
        self.assertIn('TP1', str(report['findings']))

    def reexport(self, project, label):
        from tests.test_schematic_emitter import run_native
        from agent_ee.parts_subset import evaluate
        work = self.root / ('netlist-' + label)
        run_native(project, work, 'kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                   '-o', '/work/design.xml', '/candidate/probe.kicad_sch')
        shutil.copyfile(project / 'probe.kicad_sch', self.root / 'probe.kicad_sch')
        return evaluate(self.root, 'parts.json', work / 'design.xml', ['probe.kicad_sch'])

    def full_fab(self, project, probes):
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        scope = read_json(self.root / 'scaffold/scope.json')
        scope['checks'] += [{'id': 'binding', 'kind': 'part_binding', 'params': {
            'probe_manifest': 'parts.json', 'netlist_check': 'netlist', 'require_datasheet': True}},
            {'id': 'geometry', 'kind': 'geometry'},
            {'id': 'fab', 'kind': 'fab_export', 'params': {'netlist_check': 'netlist', 'probe_manifest': 'parts.json'}},
            {'id': 'consistency', 'kind': 'fab_consistency', 'params': {'export_check': 'fab'}}]
        scope['baseline']['requirements'][0]['checks'] += ['binding', 'geometry', 'fab', 'consistency']
        write_json(self.root / 'scaffold/scope.json', scope)
        run = self.root / 'fab-run'; run.mkdir()
        capture(self.root / 'scaffold/scope.json', run)
        results = run_checks(run, time.monotonic() + 300)
        self.assertEqual([(r['id'], r['execution'], r['outcome']) for r in results],
                         [('netlist', 'ok', 'info'), ('parts', 'ok', 'pass'),
                          ('binding', 'ok', 'pass'), ('geometry', 'ok', 'info'),
                          ('fab', 'ok', 'pass'), ('consistency', 'ok', 'pass')], results)
        fab = run / 'work/fab/fab'
        import csv
        bom = list(csv.reader((fab / 'bom.csv').read_text().splitlines()))[1:]
        cpl = list(csv.reader((fab / 'cpl.csv').read_text().splitlines()))[1:]
        self.assertEqual({r for row in bom for r in row[0].split(',')}, {'R1', 'R2', 'R3'})
        self.assertEqual({row[0] for row in cpl}, {'R1', 'R2', 'R3'})
        self.assertEqual(read_json(fab / 'manifest.json')['probe_features'], probes)
        copper = next((fab / 'gerbers').glob('*F_Cu.gtl')).read_text()
        mask = next((fab / 'gerbers').glob('*F_Mask.gts')).read_text()
        paste = next((fab / 'gerbers').glob('*F_Paste.gtp')).read_text()
        for ref in probes:
            self.assertIn(ref, copper)
            self.assertIn(ref, mask)
            self.assertNotIn(ref, paste)
        # The terminal producer must admit the same sealed probe sources as standalone export.
        from agent_ee.baseline import load_baseline, freeze_evidence, verify_evidence
        package_scope = copy.deepcopy(scope)
        package_scope['schema'] = 'agent-ee.scope/3'
        package_scope['checks'].pop()
        package_scope['checks'][-1]['kind'] = 'fab_package'
        req = package_scope['baseline']['requirements'][0]
        req['checks'].remove('consistency')
        req['intent'] = 'acceptance'
        write_json(self.root / 'scaffold/package.scope.json', package_scope)
        package_run = self.root / 'package-run'; package_run.mkdir()
        capture(self.root / 'scaffold/package.scope.json', package_run)
        package_results = run_checks(package_run, time.monotonic() + 300)
        package = package_results[-1]
        self.assertEqual((package['execution'], package['outcome']), ('ok', 'pass'), package)
        self.assertEqual(read_json(package_run / 'work/fab/fab/manifest.json')['probe_features'], probes)
        baseline = load_baseline(package_run)
        closure = pf.immutable_inputs(package_scope['checks'][-1]['params'],
                                     read_json(package_run / 'candidate.json')['files'],
                                     package_run / 'inputs', package_scope)
        self.assertTrue(set(closure) <= set(baseline['files']))
        self.assertTrue(set(closure) <= set(baseline['coverage']['checks']['fab']['immutable_inputs']))
        self.assertIn({'check_id': 'parts', 'kind': 'parts_subset'},
                      baseline['coverage']['checks']['fab']['prerequisites'])
        from agent_ee.baseline import compile_baseline
        legacy = copy.deepcopy(package_scope)
        legacy['schema'] = 'agent-ee.scope/2'
        for legacy_req in legacy['baseline']['requirements']:
            legacy_req.pop('intent', None)
        legacy_baseline = compile_baseline(legacy, read_json(package_run / 'candidate.json')['files'],
                                           package_run / 'inputs')
        self.assertTrue(set(closure) <= set(legacy_baseline['files']))
        from agent_ee.capabilities import describe_check
        files = read_json(package_run / 'candidate.json')['files']
        def describe(changed):
            return describe_check(changed['checks'][-1], check_specs=changed['checks'],
                                  files=files, scope=changed, inputs=package_run / 'inputs')
        for missing in ('parts', 'parts-mismatch'):
            changed = copy.deepcopy(package_scope)
            if missing == 'parts':
                changed['checks'] = [c for c in changed['checks'] if c['id'] != 'parts']
            else:
                next(c for c in changed['checks'] if c['id'] == 'parts')['params']['manifest'] = 'other.json'
            self.assertEqual(describe(changed)['status'], 'unsupported')
        changed = copy.deepcopy(package_scope)
        changed['mutable_inputs'].append('parts.json')
        self.assertEqual(describe(changed)['status'], 'unsupported')
        freeze_evidence(package_run, package_results)
        verify_evidence(package_run, package_results, required=True)
        # KiCad's independent parity check sees all six required physical features.
        from tests.test_schematic_emitter import run_native
        work = self.root / 'parity'
        run_native(project, work, 'kicad-cli', 'pcb', 'drc', '--schematic-parity', '--format', 'json',
                   '--severity-all', '-o', '/work/drc.json', '/candidate/probe.kicad_pcb')
        drc = read_json(work / 'drc.json')
        from agent_ee.board_build import PARITY_KEYS
        self.assertFalse([v for v in drc['schematic_parity'] if v['type'] in PARITY_KEYS], drc)
        # Exercise the actual consumer guard against damaged prerequisite evidence.
        from agent_ee.checks import Ctx, CheckError
        spec = next(s for s in scope['checks'] if s['id'] == 'binding')
        ctx = Ctx(run, read_json(run / 'candidate.json'), spec, time.monotonic() + 60)
        source = run / 'work/parts/parts-subset.json'
        original = source.read_bytes()
        for field, value in [('outcome', 'defect'), ('coverage', 'missing'),
                             ('manifest_sha256', '0' * 64), ('native_xml_sha256', '0' * 64)]:
            with self.subTest(damaged_parts_subset=field):
                report = json.loads(original); report[field] = value
                write_json(source, report)
                try:
                    with self.assertRaisesRegex(CheckError, 'source-bound parts_subset'):
                        pf.load_for_check(ctx)
                finally:
                    source.write_bytes(original)
