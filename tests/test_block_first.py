"""Block-first admission uses real sandboxed native checks and sealed captures."""
import tempfile
from unittest.mock import patch
import unittest
from pathlib import Path

from agent_ee.block_first import verify_block, integrate
from agent_ee.storage import read_json, write_json
from block_first_fixture import fixture
from toolchain import require_toolchain


class BlockFirstTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-block-first-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scopes, self.top = fixture(self.root)
        self.runs = {sheet: self.root / f'run{i}' for i, sheet in enumerate(self.scopes)}

    def verify(self):
        for sheet, scope in self.scopes.items():
            result = verify_block(scope, self.runs[sheet])
            self.assertEqual(result['status'], 'verified', self.diagnostic(result))

    def diagnostic(self, result):
        return result.get('reason', [(c['id'], c['execution'], c['outcome'], c['summary'])
                                    for c in result.get('checks', [])])

    def refused(self, blocks=None, name='integration'):
        result = integrate(self.top, self.runs if blocks is None else blocks, self.root/name)
        self.assertNotEqual(result['status'], 'verified', result)
        self.assertFalse((self.root/name/'design').exists())
        return result

    def test_each_block_has_hash_and_native_results_then_integration(self):
        self.verify()
        for sheet, run in self.runs.items():
            result = read_json(run / 'block-verification.json')
            from agent_ee.storage import digest
            self.assertEqual(result['sheet_sha256'], digest(self.root / sheet))
            self.assertEqual([(c['kind'], c['execution'], c['outcome']) for c in result['checks']],
                             [('erc', 'ok', 'pass'), ('netlist', 'ok', 'info'),
                              ('netlist_parity', 'ok', 'pass')])
            self.assertEqual(result['ports'], {'A': 'passive', 'B': 'passive'})
        result = integrate(self.top, self.runs, self.root / 'integration')
        self.assertEqual(result['status'], 'verified', self.diagnostic(result))
        for sheet in self.scopes:
            self.assertEqual((self.root/'integration/design'/sheet).read_bytes(), (self.root/sheet).read_bytes())
        self.assertTrue((self.root/'integration/work/erc/erc.json').is_file())
        self.assertEqual(len(result['blocks']), 2)

    def test_missing_and_extra_block_coverage_refuses_before_integrated_checks(self):
        for label, blocks in [('none', {}), ('partial', dict(list(self.runs.items())[:1])),
                              ('extra', dict(self.runs, extra=self.root/'absent'))]:
            with self.subTest(label=label):
                result = self.refused(blocks, label)
                self.assertEqual(result['status'], 'missing_coverage')
                self.assertIn('every child sheet', result['reason'])
                self.assertFalse((self.root/label/'checks').exists())

    def test_block_erc_defect_and_parity_defect_each_refuse_integration(self):
        first, second = self.scopes
        # A real native ERC defect which leaves the physical topology intact.
        harness = self.root/'project/verify1.kicad_sch'
        original = harness.read_text()
        from block_first_fixture import uid
        harness.write_text(original[:-1] +
            f'(wire (pts (xy 101.6 101.6) (xy 106.68 101.6)) (stroke (width 0) (type default)) '
            f'(uuid "{uid("dangling")}")))')
        erc = verify_block(self.scopes[first], self.runs[first])
        self.assertEqual(erc['status'], 'defect', self.diagnostic(erc))
        self.assertEqual(erc['checks'][0]['outcome'], 'defect')
        self.assertEqual(erc['checks'][-1]['outcome'], 'pass')
        # A value mutation is an independent exact-parity defect.
        child = self.root/second
        child.write_text(child.read_text().replace('"1k"', '"2k"'))
        parity = verify_block(self.scopes[second], self.runs[second])
        self.assertEqual(parity['status'], 'defect', self.diagnostic(parity))
        self.assertEqual(parity['checks'][0]['outcome'], 'pass')
        self.assertEqual(parity['checks'][-1]['outcome'], 'defect')
        result = self.refused()
        self.assertEqual(result['status'], 'defect', result)
        self.assertTrue(all(b['status'] == 'defect' for b in result['blocks'].values()))
        self.assertFalse((self.root/'integration/checks').exists())

    def test_failed_native_worker_is_missing_coverage(self):
        from agent_ee.executor import execute
        def fail_erc(inputs, work, argv, **kwargs):
            return execute(inputs, work, ['/bin/false'] if argv[1:3] == ['sch', 'erc'] else argv, **kwargs)
        first = next(iter(self.scopes))
        with patch('agent_ee.executor.execute', side_effect=fail_erc):
            report = verify_block(self.scopes[first], self.runs[first])
        self.assertEqual(report['status'], 'missing_coverage', self.diagnostic(report))
        erc = report['checks'][0]
        self.assertEqual(erc['outcome'], 'unknown')
        execution = read_json(self.runs[first]/'work/erc/execution.json')
        self.assertEqual((execution['argv'], execution['exit_code']), (['/bin/false'], 1))
        result = self.refused()
        self.assertEqual(result['blocks'][first]['status'], 'missing_coverage')

    def test_crash_and_interruption_leave_missing_worker_records(self):
        for index, failure in enumerate((RuntimeError('worker crash'), KeyboardInterrupt())):
            sheet = list(self.scopes)[index]
            with patch('agent_ee.block_first.run_checks', side_effect=failure):
                with self.assertRaises(type(failure)):
                    verify_block(self.scopes[sheet], self.runs[sheet])
            report = read_json(self.runs[sheet]/'block-verification.json')
            self.assertEqual(report['status'], 'missing_coverage')
        result = self.refused()
        self.assertEqual(result['status'], 'missing_coverage')
        self.assertTrue(all(b['status'] == 'missing_coverage' for b in result['blocks'].values()))
        self.assertFalse((self.root/'integration/checks').exists())

    def test_interface_change_invalidates_then_requires_fresh_block_evidence(self):
        self.verify()
        first = next(iter(self.scopes))
        child = self.root/first
        child.write_text(child.read_text().replace('(hierarchical_label "A" (shape passive)',
                                                   '(hierarchical_label "A" (shape output)'))
        # Keep the newly proposed parent consistent; stale child evidence still fails.
        for rel in ('project/top.kicad_sch', 'project/verify1.kicad_sch'):
            path = self.root/rel
            path.write_text(path.read_text().replace('(pin "A" passive', '(pin "A" output', 1))
        result = self.refused(name='stale')
        self.assertEqual(result['blocks'][first]['status'], 'missing_coverage')
        self.assertIn('re-check required', result['blocks'][first]['reason'])
        self.assertEqual(result['recheck_required'], [first])
        self.assertFalse((self.root/'stale/checks').exists())
        old = read_json(self.runs[first]/'block-verification.json')
        self.runs[first] = self.root/'recheck'
        report = verify_block(self.scopes[first], self.runs[first])
        self.assertEqual(report['status'], 'verified', self.diagnostic(report))
        self.assertNotEqual(report['candidate_id'], old['candidate_id'])
        result = integrate(self.top, self.runs, self.root/'fresh')
        self.assertEqual(result['status'], 'verified', self.diagnostic(result))

    def test_changed_contract_or_golden_input_is_not_inherited(self):
        self.verify()
        for rel in ('block1.md', 'block1.xml', 'project/Owned.kicad_sym'):
            with self.subTest(rel=rel):
                path = self.root/rel
                old = path.read_bytes()
                path.write_bytes(old+b'\n')
                result = self.refused(name='changed-'+path.name)
                first = next(iter(self.scopes))
                self.assertIn(rel, result['blocks'][first]['reason'])
                path.write_bytes(old)

    def test_parent_pin_mismatch_is_missing_interface_coverage(self):
        self.verify()
        top = self.root/'project/top.kicad_sch'
        top.write_text(top.read_text().replace('(pin "A" passive', '(pin "A" output', 1))
        result = self.refused()
        first = next(iter(self.scopes))
        self.assertIn('parent sheet pins differ', result['blocks'][first]['reason'])
        self.assertFalse((self.root/'integration/checks').exists())

    def test_snapshot_check_artifact_and_saved_status_tampering_refused(self):
        self.verify()
        first = next(iter(self.scopes))
        run = self.runs[first]
        for rel in ('inputs/'+first, 'work/erc/erc.json', 'checks/parity.json', 'block-verification.json'):
            with self.subTest(rel=rel):
                path = run/rel
                old = path.read_bytes()
                path.write_bytes(old+b'\n')
                # Whitespace in a worker report is harmless; change its actual status.
                if rel == 'block-verification.json':
                    report = read_json(path)
                    report['status'] = 'missing_coverage'
                    write_json(path, report)
                result = self.refused(name='tamper-'+path.name)
                self.assertEqual(result['blocks'][first]['status'], 'missing_coverage')
                path.write_bytes(old)

    def test_integration_still_runs_its_own_native_checks(self):
        self.verify()
        expected = self.root/'top.xml'
        expected.write_bytes(expected.read_bytes().replace(b'owned-ic', b'different-ic'))
        result = self.refused()
        self.assertEqual(result['status'], 'defect', self.diagnostic(result))
        self.assertEqual(result['checks'][-1]['outcome'], 'defect')
        self.assertTrue(all(b['status'] == 'verified' for b in result['blocks'].values()))

    def test_deadline_never_reports_a_worker_done(self):
        first = next(iter(self.scopes))
        result = verify_block(self.scopes[first], self.runs[first], timeout_s=.000001)
        self.assertEqual(result['status'], 'missing_coverage')
        self.assertTrue(all(c['outcome'] == 'unknown' for c in result['checks']))
        self.assertTrue(all(c['execution'] == 'interrupted' for c in result['checks']))

    def test_scope2_uses_same_native_gate_and_immutable_baseline(self):
        first = next(iter(self.scopes))
        scope = read_json(self.scopes[first])
        scope['schema'] = 'agent-ee.scope/2'
        for req in scope['baseline']['requirements']:
            req.pop('intent')
        write_json(self.scopes[first], scope)
        report = verify_block(self.scopes[first], self.runs[first])
        self.assertEqual(report['status'], 'verified', self.diagnostic(report))
        self.assertEqual(read_json(self.runs[first]/'baseline.json')['schema'], 'agent-ee.baseline/1')

    def test_existing_output_and_invalid_timeouts_are_refused(self):
        first = next(iter(self.scopes))
        self.runs[first].mkdir()
        (self.runs[first]/'sentinel').write_text('preserve')
        with self.assertRaises(FileExistsError):
            verify_block(self.scopes[first], self.runs[first])
        self.assertEqual((self.runs[first]/'sentinel').read_text(), 'preserve')
        for seconds in (0, -1, float('nan'), float('inf'), True):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                verify_block(self.scopes[first], self.root/'invalid', timeout_s=seconds)
        self.assertFalse((self.root/'invalid').exists())

    def test_missing_required_check_is_refused(self):
        first = next(iter(self.scopes))
        document = read_json(self.scopes[first])
        document['checks'] = [c for c in document['checks'] if c['kind'] != 'erc']
        document['baseline']['requirements'][1]['checks'].remove('erc')
        write_json(self.scopes[first], document)
        report = verify_block(self.scopes[first], self.runs[first])
        self.assertEqual(report['status'], 'missing_coverage')
        self.assertIn('requires ERC', report['reason'])
        self.assertFalse((self.runs[first]/'checks').exists())


    def test_missing_ports_and_flat_subject_are_refused(self):
        first = next(iter(self.scopes))
        original = read_json(self.scopes[first])
        document = dict(original, schematic=first)
        write_json(self.scopes[first], document)
        result = verify_block(self.scopes[first], self.root/'flat')
        self.assertEqual(result['status'], 'missing_coverage')
        self.assertIn('one standalone child', result['reason'])
        write_json(self.scopes[first], original)
        from agent_ee.schematic_emit import parse_quoted, format_sexpr
        from agent_ee.symbol_library import children
        child = self.root/first
        tree = parse_quoted(child.read_bytes())
        tree[:] = [n for n in tree if n not in children(tree, 'hierarchical_label')]
        child.write_text('\n'.join(format_sexpr(tree)))
        result = verify_block(self.scopes[first], self.root/'no-ports')
        self.assertEqual(result['status'], 'missing_coverage')
        self.assertIn('hierarchical boundary ports', result['reason'])

    def test_nested_block_hierarchy_is_refused(self):
        from agent_ee.schematic_emit import parse_quoted, format_sexpr
        from agent_ee.symbol_library import children
        top = self.root/'project/top.kicad_sch'
        tree = parse_quoted(top.read_bytes())
        nested = children(tree, 'sheet')[1]
        tree.remove(nested)
        top.write_text('\n'.join(format_sexpr(tree)))
        child = self.root/next(iter(self.scopes))
        subject = parse_quoted(child.read_bytes())
        subject.append(nested)
        child.write_text('\n'.join(format_sexpr(subject)))
        result = self.refused()
        self.assertIn('direct standalone child sheets', result['reason'])
        self.assertFalse((self.root/'integration/checks').exists())

    def test_repeated_child_occurrences_cannot_share_one_block_result(self):
        from agent_ee.schematic_emit import parse_quoted, format_sexpr
        from agent_ee.symbol_library import children
        from block_first_fixture import uid
        import copy
        top = self.root/'project/top.kicad_sch'
        tree = parse_quoted(top.read_bytes())
        node = copy.deepcopy(children(tree, 'sheet')[0])
        children(node, 'uuid')[0][1] = uid('duplicate-sheet')
        for prop in children(node, 'property'):
            if prop[1] == 'Sheetname':
                prop[2] = 'repeated'
        tree.append(node)
        top.write_text('\n'.join(format_sexpr(tree)))
        result = self.refused()
        self.assertIn('no repeated sheets', result['reason'])
        self.assertFalse((self.root/'integration/checks').exists())

    def test_missing_native_output_has_no_success_fallback(self):
        from agent_ee.executor import execute
        def empty_erc(inputs, work, argv, **kwargs):
            return execute(inputs, work, ['/bin/true'] if argv[1:3] == ['sch', 'erc'] else argv, **kwargs)
        first = next(iter(self.scopes))
        with patch('agent_ee.executor.execute', side_effect=empty_erc):
            report = verify_block(self.scopes[first], self.runs[first])
        self.assertEqual(report['status'], 'missing_coverage', self.diagnostic(report))
        erc = report['checks'][0]
        self.assertEqual((erc['execution'], erc['outcome']), ('malformed', 'unknown'))
        self.assertIn('was not written', erc['summary'])
        self.assertEqual(read_json(self.runs[first]/'work/erc/execution.json')['exit_code'], 0)

    def test_partial_publication_never_appears_as_an_integrated_design(self):
        self.verify()
        import shutil
        copytree = shutil.copytree
        def partial(source, destination, *args, **kwargs):
            if Path(destination).name == 'pending-design':
                Path(destination).mkdir()
                (Path(destination)/'incomplete').write_text('incomplete')
                raise OSError('disk full')
            return copytree(source, destination, *args, **kwargs)
        with patch('agent_ee.block_first.shutil.copytree', side_effect=partial):
            result = self.refused()
        self.assertEqual(result['status'], 'missing_coverage')
        self.assertIn('disk full', result['reason'])
        self.assertTrue((self.root/'integration/pending-design/incomplete').is_file())

    def test_harness_virtual_power_flags_are_allowed_but_physical_parts_are_not(self):
        from agent_ee.schematic_emit import parse_quoted, format_sexpr
        from agent_ee.symbol_library import children
        from block_first_fixture import prop, uid
        first = next(iter(self.scopes))
        harness = self.root/'project/verify1.kicad_sch'
        tree = parse_quoted(harness.read_bytes())
        # An original virtual power source supplies a harness rail. It is omitted
        # by KiCad's physical netlist, while ERC checks its electrical presence.
        definition = ('(symbol "Rail" (power) (pin_names (offset 0)) (in_bom no) (on_board no) '
            + prop('Reference', '#FLG') + prop('Value', 'Rail')
            + '(symbol "Rail_1_1" (pin power_out line (at 0 0 0) (length 0) '
              '(name "pwr" (effects (font (size 1.27 1.27)))) '
              '(number "1" (effects (font (size 1.27 1.27)))))))')
        library = self.root/'project/Owned.kicad_sym'
        library.write_text(library.read_text()[:-1]+definition+')')
        embedded = parse_quoted(definition.encode())
        from agent_ee.schematic_emit import Quoted
        embedded[1] = Quoted('Owned:Rail')
        children(tree, 'lib_symbols')[0].append(embedded)
        tree.append(parse_quoted((f'(symbol (lib_id "Owned:Rail") (at 10.16 66.04 0) (unit 1) '
            f'(in_bom no) (on_board no) (uuid "{uid("flag")}")'
            + prop('Reference', '#FLG01') + prop('Value', 'Rail')
            + f'(pin "1" (uuid "{uid("flagpin")}")) '
              f'(instances (project "verify1" (path "/{uid("verify1")}" '
              '(reference "#FLG01") (unit 1)))))').encode()))
        tree[:] = [n for n in tree if not (isinstance(n, list) and n[0]=='no_connect'
                   and children(n, 'at')[0][1:3] == ['20.32', '66.04'])]
        tree.append(parse_quoted((f'(wire (pts (xy 10.16 66.04) (xy 20.32 66.04)) '
             f'(stroke (width 0) (type default)) (uuid "{uid("flagwire")}"))').encode()))
        harness.write_text('\n'.join(format_sexpr(tree)))
        result = verify_block(self.scopes[first], self.root/'virtual')
        self.assertEqual(result['status'], 'verified', self.diagnostic(result))
        self.assertEqual(result['checks'][0]['outcome'], 'pass')
        self.assertEqual(result['checks'][1]['details']['refs'], ['R1', 'U1'])
        # A physical part in the harness cannot borrow a child's successful run.
        child = parse_quoted((self.root/first).read_bytes())
        import copy
        physical = copy.deepcopy(children(child, 'symbol')[1])
        for project in children(children(physical, 'instances')[0], 'project'):
            if project[1] == 'verify1':
                children(project, 'path')[0][1] = Quoted('/'+uid('verify1'))
        tree.append(physical)
        children(tree, 'lib_symbols')[0].append(copy.deepcopy(children(children(child, 'lib_symbols')[0], 'symbol')[1]))
        child.remove(children(child, 'symbol')[1])
        harness.write_text('\n'.join(format_sexpr(tree)))
        (self.root/first).write_text('\n'.join(format_sexpr(child)))
        result = verify_block(self.scopes[first], self.root/'physical')
        self.assertEqual(result['status'], 'missing_coverage')
        self.assertIn('no physical components', result['reason'])

    def test_cli_checks_and_admits_the_owned_hierarchy(self):
        import os
        import subprocess
        import sys
        for sheet, scope in self.scopes.items():
            rec = subprocess.run([sys.executable, '-m', 'agent_ee.block_first', 'verify',
                '--scope', str(scope), '--out', str(self.runs[sheet])],
                capture_output=True, text=True, timeout=120, env=os.environ.copy())
            self.assertEqual((rec.returncode, rec.stdout.strip()), (0, 'verified'), rec.stderr)
        argv = [sys.executable, '-m', 'agent_ee.block_first', 'integrate', '--scope', str(self.top),
                '--out', str(self.root/'cli')]
        for sheet, run in self.runs.items():
            argv.extend(['--block', sheet+'='+str(run)])
        rec = subprocess.run(argv, capture_output=True, text=True, timeout=120, env=os.environ.copy())
        self.assertEqual((rec.returncode, rec.stdout.strip()), (0, 'verified'), rec.stderr)
        self.assertTrue((self.root/'cli/design/project/top.kicad_sch').is_file())
        rec = subprocess.run([sys.executable, '-m', 'agent_ee.block_first', 'verify',
                '--scope', str(self.root/'absent.scope.json'), '--out', str(self.root/'cli-failed')],
                capture_output=True, text=True, timeout=120, env=os.environ.copy())
        self.assertEqual((rec.returncode, rec.stdout.strip()), (1, 'missing_coverage'), rec.stderr)



if __name__ == '__main__':
    unittest.main()
