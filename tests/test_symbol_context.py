"""Owned selected-symbol context and literal exact pin selectors."""
import hashlib
import unittest

from agent_ee.symbol_context import build_context, check_pin_references


LIBRARY = b'''(kicad_symbol_lib (version 20231120) (generator "owned")
(symbol "Base" (property "Description" "Owned amplifier")
(symbol "Base_0_1" (rectangle (start -2 -2) (end 2 2) (stroke (width 0) (type default)) (fill (type none))))
(symbol "Base_1_1"
(pin power_in line (at -4 1 0) (length 2) (name "GND") (number "1"))
(pin power_in line (at -4 -1 0) (length 2) (name "GND") (number "2"))
(pin passive line (at 4 0 180) (length 2) (name "~") (number "3"))))
(symbol "Child" (extends "Base") (property "Description" "Owned child")))'''


def libraries(data=LIBRARY):
    return [dict(name='Owned', path='owned.kicad_sym', sha256=hashlib.sha256(data).hexdigest())]


class ContextTests(unittest.TestCase):
    def context(self, selection='Owned:Child', data=LIBRARY):
        return build_context(libraries(data), {'owned.kicad_sym': data}, selection)

    def test_child_retains_every_named_and_unnamed_number_and_parent_source(self):
        result = self.context()
        self.assertEqual(result['execution'], 'ok')
        self.assertEqual(result['selection'], 'Owned:Child')
        self.assertEqual([(p['number'], p['name']) for p in result['pins']], [('1', 'GND'), ('2', 'GND'), ('3', '~')])
        self.assertEqual(result['description'], 'Owned child')
        self.assertEqual([s['symbol'] for s in result['sources']], ['Base', 'Child'])
        self.assertEqual(result['bbox_by_unit']['1']['bounds_nm'], [-4000000, -2000000, 4000000, 2000000])
        self.assertEqual(result['sources'][0]['sha256'], libraries()[0]['sha256'])

    def test_missing_pin_name_is_defect_without_substitution(self):
        result = check_pin_references(libraries(), {'owned.kicad_sym': LIBRARY},
            [dict(reference='U1', lib_id='Owned:Child', pin={'name': 'GND_typo'})])
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'))
        self.assertEqual(result['references'][0]['available_pins'],
                         [{'number': '1', 'name': 'GND'}, {'number': '2', 'name': 'GND'}, {'number': '3', 'name': '~'}])

    def test_name_group_and_number_lookup_keep_all_pins(self):
        result = check_pin_references(libraries(), {'owned.kicad_sym': LIBRARY},
            [dict(reference='U1', lib_id='Owned:Child', pin={'name': 'GND'}),
             dict(reference='U1', lib_id='Owned:Child', pin={'number': '3'})])
        self.assertEqual(result['outcome'], 'pass')
        self.assertEqual([r['numbers'] for r in result['references']], [['1', '2'], ['3']])

    def test_missing_symbol_and_shell_metacharacters_are_literal_unknown(self):
        for selection in ('Owned:Absent', 'Owned:$(touch /tmp/owned);*'):
            result = self.context(selection)
            self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'))

    def test_context_is_deterministic_and_hash_drift_fails(self):
        self.assertEqual(self.context(), self.context())
        result = build_context(libraries(), {'owned.kicad_sym': LIBRARY + b' '}, 'Owned:Child')
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'))

    def test_unqualified_geometry_does_not_invent_bbox_or_lose_pins(self):
        data = LIBRARY.replace(b'(rectangle (start -2 -2) (end 2 2)', b'(arc (start -2 -2) (mid 0 3) (end 2 2)')
        result = self.context(data=data)
        self.assertEqual(len(result['pins']), 3)
        self.assertEqual(result['bbox_by_unit']['1']['coverage'], 'unsupported')
        self.assertIsNone(result['bbox_by_unit']['1']['bounds_nm'])
        # Pin existence remains independently qualified despite unavailable bbox.
        refs = check_pin_references(libraries(data), {'owned.kicad_sym': data},
            [dict(reference='U1', lib_id='Owned:Child', pin={'number': '1'})])
        self.assertEqual(refs['outcome'], 'pass')

    def test_cycle_missing_parent_and_unsupported_override_are_unknown(self):
        for data in (
            b'(kicad_symbol_lib (symbol "Child" (extends "Child")))',
            b'(kicad_symbol_lib (symbol "Child" (extends "Absent")))',
            LIBRARY.replace(b'(symbol "Child" (extends "Base")',
                            b'(symbol "Child" (extends "Base") (symbol "Child_1_1")'),
        ):
            result = self.context(data=data)
            self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'))

    def test_common_unit_and_multiunit_pins_do_not_collapse(self):
        data = b'''(kicad_symbol_lib (symbol "Child"
(symbol "Child_0_1" (pin power_in line (at 0 0 0) (length 1) (name "GND") (number "5")))
(symbol "Child_1_1" (pin input line (at 1 0 0) (length 1) (name "IN") (number "1")))
(symbol "Child_2_1" (pin input line (at 2 0 0) (length 1) (name "IN") (number "2")))))'''
        result = self.context(data=data)
        self.assertEqual(result['execution'], 'ok')
        self.assertEqual([(p['unit'], p['number']) for p in result['pins']], [(0, '5'), (1, '1'), (2, '2')])
        self.assertEqual(set(result['bbox_by_unit']), {'1', '2'})
        self.assertEqual(result['bbox_by_unit']['2']['bounds_nm'], [0, 0, 3000000, 0])

    def test_named_offline_tools_preserve_literal_selection_and_hashes(self):
        from agent_ee.library_tools import dispatch_tool
        request = dict(libraries=libraries(), selection='Owned:Child')
        first = dispatch_tool('find-symbol', request, {'owned.kicad_sym': LIBRARY})
        second = dispatch_tool('list-pins', request, {'owned.kicad_sym': LIBRARY})
        self.assertEqual(first['result']['pins'], second['result']['pins'])
        self.assertEqual(first['request_sha256'], second['request_sha256'])
        self.assertEqual(first['result']['sources'][0]['sha256'], libraries()[0]['sha256'])
        request['selection'] = 'Owned:$(touch /tmp/nope);*'
        missing = dispatch_tool('find-symbol', request, {'owned.kicad_sym': LIBRARY})
        self.assertEqual(missing['result']['outcome'], 'unknown')

    def test_no_fuzzy_symbol_or_pin_substitution(self):
        for selection in ('Owned:Ch*', 'Owned:Chil?', 'Owned:child', 'Owned:Child ', 'Child'):
            result = self.context(selection)
            self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'), selection)
            self.assertEqual(result['pins'], [])
        selectors = ({'name': 'gnd'}, {'name': ' GND'}, {'name': 'G*'}, {'name': '1'},
                     {'number': '03'}, {'number': '~'})
        result = check_pin_references(libraries(), {'owned.kicad_sym': LIBRARY},
            [dict(reference='U1', lib_id='Owned:Child', pin=pin) for pin in selectors])
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'))
        self.assertEqual([(r['status'], r['numbers']) for r in result['references']],
                         [('defect', [])] * len(selectors))

    def test_symbol_name_with_shell_metacharacters_is_found_literally(self):
        name = 'Odd;$(id)*|` '
        data = LIBRARY.replace(b'(symbol "Child" (extends "Base")',
                               b'(symbol "' + name.encode() + b'" (extends "Base")')
        result = self.context('Owned:' + name, data)
        self.assertEqual(result['execution'], 'ok')
        self.assertEqual(result['selection'], 'Owned:' + name)
        self.assertEqual([s['symbol'] for s in result['sources']], ['Base', name])
        self.assertEqual([p['number'] for p in result['pins']], ['1', '2', '3'])

    def test_find_footprint_is_exact_literal_and_cites_member_hash(self):
        from agent_ee.library_tools import dispatch_tool
        data = b'''(footprint "SOT3" (version 20240108) (generator "owned") (layer "F.Cu")
(pad "1" smd rect (at -1 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
(pad "2" smd rect (at 1 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask")))'''
        member = dict(path='owned.pretty/SOT3.kicad_mod', sha256=hashlib.sha256(data).hexdigest())
        manifest = dict(schema='agent-ee.footprint-libraries/1', complete=True,
                        libraries=[dict(name='Owned', members=[member])])
        captures = {member['path']: data}
        found = dispatch_tool('find-footprint', dict(manifest=manifest, query='Owned:SOT3'), captures)
        self.assertEqual(found['tool'], 'find-footprint')
        self.assertEqual(found['result']['execution'], 'ok')
        self.assertEqual([m['lib_id'] for m in found['result']['matches']], ['Owned:SOT3'])
        self.assertEqual(found['result']['matches'][0]['source'], member)
        for query in ('Owned:SOT*', 'Owned:sot3', 'SOT3', 'Owned:SOT3;$(id)'):
            result = dispatch_tool('find-footprint', dict(manifest=manifest, query=query), captures)['result']
            self.assertEqual((result['execution'], result['coverage'], result['matches']), ('ok', 'complete', []))
        drifted = dispatch_tool('find-footprint', dict(manifest=manifest, query='Owned:SOT3'),
                                {member['path']: data + b' '})['result']
        self.assertEqual((drifted['execution'], drifted['outcome'], drifted['matches']), ('failed', 'unknown', []))
        with self.assertRaises(ValueError):
            dispatch_tool('find-footprint', dict(manifest=manifest, query=['Owned:SOT3']), captures)
        with self.assertRaises(ValueError):
            dispatch_tool('find-pins', dict(libraries=libraries(), selection='Owned:Child'), {})

    def test_library_tool_cli_is_literal_stable_and_never_overwrites(self):
        import json
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        src = Path(__file__).resolve().parents[1] / 'src'
        env = dict(os.environ, PYTHONPATH=str(src))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'captured').mkdir()
            (root / 'captured/owned.kicad_sym').write_bytes(LIBRARY)

            def run(selection, out):
                (root / 'request.json').write_text(json.dumps(dict(libraries=libraries(), selection=selection)))
                return subprocess.run([sys.executable, '-m', 'agent_ee.library_tools', 'list-pins',
                                       str(root / 'request.json'), '--captures', str(root / 'captured'),
                                       '--out', str(root / out)], cwd=root, env=env,
                                      capture_output=True, text=True, timeout=60)
            for out in ('first.json', 'second.json'):
                proc = run('Owned:Child', out)
                self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual((root / 'first.json').read_bytes(), (root / 'second.json').read_bytes())
            answer = json.loads((root / 'first.json').read_text())
            self.assertEqual([p['number'] for p in answer['result']['pins']], ['1', '2', '3'])
            proc = run('Owned:$(touch should-not-exist);*', 'missing.json')
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertFalse((root / 'should-not-exist').exists())
            self.assertEqual(json.loads((root / 'missing.json').read_text())['result']['outcome'], 'unknown')
            before = (root / 'first.json').read_bytes()
            self.assertEqual(run('Owned:Child', 'first.json').returncode, 2)
            self.assertEqual((root / 'first.json').read_bytes(), before)


class NativeSymbolContextTests(unittest.TestCase):
    def test_native_unconnected_pin_inventory_matches_context(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        import subprocess
        import tempfile
        import xml.etree.ElementTree as ET
        from pathlib import Path
        from agent_ee.executor import sandbox_argv
        # Original complete device, deliberately no wires and all pins unconnected.
        # Native XML libparts must still enumerate all physical numbers.
        pins = ''.join(f'(pin passive line (at 0 {i*2.54} 0) (length 2.54) '
                       f'(name "{name}" (effects (font (size 1 1)))) '
                       f'(number "{i+1}" (effects (font (size 1 1)))))'
                       for i, name in enumerate(('GND', 'GND', '~')))
        prop = lambda k, v: f'(property "{k}" "{v}" (at 0 0 0) (effects (font (size 1 1))))'
        body = f'(in_bom yes) (on_board yes) {prop("Reference", "U")} {prop("Value", "Native")} (symbol "Native_1_1" {pins})'
        library = f'(kicad_symbol_lib (version 20231120) (generator "owned") (symbol "Native" {body}))'.encode()
        root_id = 'b9c01a2b-0e10-4bb4-b377-446ba28173df'
        component_id = 'dd09c907-e1da-41f5-be47-ed524a28bfcb'
        schematic = f'''(kicad_sch (version 20250114) (generator "eeschema") (uuid "{root_id}") (paper "A4")
(lib_symbols (symbol "Owned:Native" {body}))
(symbol (lib_id "Owned:Native") (at 50 50 0) (unit 1) (in_bom yes) (on_board yes) (dnp no)
(uuid "{component_id}") {prop("Reference", "U1")} {prop("Value", "Native")}
(instances (project "native" (path "/{root_id}" (reference "U1") (unit 1)))))))'''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs, work = root / 'inputs', root / 'work'
            inputs.mkdir()
            work.mkdir()
            (inputs / 'native.kicad_sch').write_text(schematic)
            command = sandbox_argv(inputs, work, ['kicad-cli', 'sch', 'export', 'netlist',
                '--format', 'kicadxml', '-o', '/work/native.xml', '/candidate/native.kicad_sch'])
            proc = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            xml = ET.parse(work / 'native.xml')
            native = sorted((p.get('num'), p.get('name')) for p in xml.findall('./libparts/libpart/pins/pin'))
        context = build_context(libraries(library), {'owned.kicad_sym': library}, 'Owned:Native')
        self.assertEqual(context['execution'], 'ok')
        # Native netlist renders the KiCad '~' no-name sentinel as empty.
        self.assertEqual(native, [('1', 'GND'), ('2', 'GND'), ('3', '')])
        self.assertEqual([(p['number'], p['name']) for p in context['pins']],
                         [('1', 'GND'), ('2', 'GND'), ('3', '~')])
        self.assertEqual([p['number'] for p in context['pins']], [number for number, _ in native])

    def test_offline_cli_is_literal_and_missing_selection_is_unknown(self):
        from toolchain import require_toolchain
        require_toolchain()
        import shutil
        import tempfile
        from pathlib import Path
        from agent_ee import executor, symbol_library
        from agent_ee.symbol_checks import check_symbol_pins
        from agent_ee.checks import Ctx, CheckError
        from agent_ee.storage import write_json, read_json
        import time
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = root / 'inputs'
            inputs.mkdir()
            (inputs / 'owned.kicad_sym').write_bytes(LIBRARY)
            # Assemble exact source files only when shared39 is supplied by a
            # separate read-only worktree during pre-integration qualification.
            overlay = root / 'app'
            shutil.copytree(Path(__file__).resolve().parents[1] / 'src', overlay)
            shutil.copyfile(symbol_library.__file__, overlay / 'agent_ee/symbol_library.py')
            previous = executor.PACKAGE_ROOT
            executor.PACKAGE_ROOT = overlay
            self.addCleanup(setattr, executor, 'PACKAGE_ROOT', previous)
            for i, selection in enumerate(('Owned:Child', 'Owned:$(touch /work/should-not-exist);*')):
                query = dict(schema='agent-ee.symbol-query/1', libraries=libraries(), selection=None,
                             references=[dict(reference='U1', lib_id=selection, pin={'number': '4'})])
                write_json(inputs / 'query.json', query)
                spec = dict(id=f'pins{i}', kind='symbol_pins', params={'declaration': 'query.json'})
                manifest = dict(scope={'checks': [spec]}, files={'query.json': 'unused', 'owned.kicad_sym': 'unused'},
                                candidate_id='owned-candidate')
                ctx = Ctx(root, manifest, spec, time.monotonic() + 60)
                if i == 0:
                    check_symbol_pins(ctx)
                    self.assertEqual((ctx.result['execution'], ctx.result['outcome']), ('ok', 'defect'))
                    self.assertEqual(ctx.result['details']['report']['references'][0]['available_pins'][2]['number'], '3')
                else:
                    with self.assertRaises(CheckError):
                        check_symbol_pins(ctx)
                    self.assertFalse((ctx.work / 'should-not-exist').exists())
                    self.assertEqual(read_json(ctx.work / 'pins.json')['outcome'], 'unknown')
                    self.assertIn(f'work/{ctx.cid}/pins.json', ctx.result['evidence'])


class SymbolPipelineTests(unittest.TestCase):
    def test_scope3_pin_control_defect_missing_symbol_and_seal(self):
        from toolchain import require_toolchain
        require_toolchain()
        import shutil
        import tempfile
        import time
        from pathlib import Path
        from agent_ee import executor, symbol_library
        from agent_ee.candidate import capture, verify_snapshot, verify_source
        from agent_ee.checks import run_checks
        from agent_ee.baseline import freeze_evidence, validate_witnesses
        from agent_ee.storage import write_json, read_json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'
            source.mkdir()
            (source / 'owned.kicad_sym').write_bytes(LIBRARY)
            for name in ('board', 'schematic', 'req', 'target', 'reference'):
                (source / name).write_text('Owned declared pin-reference fixture: ' + name)
            overlay = root / 'app'
            shutil.copytree(Path(__file__).resolve().parents[1] / 'src', overlay)
            shutil.copyfile(symbol_library.__file__, overlay / 'agent_ee/symbol_library.py')
            previous = executor.PACKAGE_ROOT
            executor.PACKAGE_ROOT = overlay
            self.addCleanup(setattr, executor, 'PACKAGE_ROOT', previous)
            lib = dict(libraries()[0], path='data/owned.kicad_sym')
            query = dict(schema='agent-ee.symbol-query/1', libraries=[lib], selection=None, references=[])
            spec = dict(id='pins', kind='symbol_pins', params=dict(declaration='data/query.json'))
            scope = dict(schema='agent-ee.scope/3', name='owned-pins', scope='Declared pin existence only',
                board='data/board', schematic='data/schematic', inputs=[dict(path='source', at='data')],
                checks=[spec], baseline=dict(requirements=[dict(id='R1', intent='acceptance',
                    statement='Declared selected-symbol pin exists',
                    source=dict(path='data/req', locator='R1'), checks=['pins'])],
                    targets=['data/target'], reference_architectures=['data/reference']))
            write_json(root / 'scope.json', scope)
            baselines = []
            for i, (symbol, selector, expected) in enumerate((
                ('Owned:Child', {'number': '3'}, ('ok', 'pass')),
                ('Owned:Child', {'name': 'GND_typo'}, ('ok', 'defect')),
                ('Owned:Absent', {'number': '3'}, ('failed', 'unknown')),
            )):
                query['references'] = [dict(reference='U1', lib_id=symbol, pin=selector)]
                write_json(source / 'query.json', query)
                run = root / f'run{i}'
                run.mkdir()
                capture(root / 'scope.json', run)
                baselines.append(read_json(run / 'baseline.json')['baseline_id'])
                results = run_checks(run, time.monotonic() + 60)
                self.assertEqual((results[0]['execution'], results[0]['outcome']), expected, results)
                freeze_evidence(run, results)
                verify_snapshot(run)
                if expected[1] == 'defect':
                    witness = [dict(requirement_id='R1', check_id='pins')]
                    evidence = [dict(path='checks/pins.json', locator='outcome')]
                    validate_witnesses(run, witness, evidence)
                    report = read_json(run / 'work/pins/pins.json')
                    report['references'][0]['available_pins'] = []
                    write_json(run / 'work/pins/pins.json', report)
                    with self.assertRaisesRegex(ValueError, 'evidence changed'):
                        validate_witnesses(run, witness, evidence)
            self.assertEqual(len(set(baselines)), 1)
            (source / 'owned.kicad_sym').write_bytes(LIBRARY + b' ')
            with self.assertRaises(ValueError):
                verify_source(run)
            (run / 'inputs/data/owned.kicad_sym').write_bytes(LIBRARY + b' ')
            with self.assertRaises(ValueError):
                verify_snapshot(run)
