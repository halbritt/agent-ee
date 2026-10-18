"""Owned fixtures for reference provenance; no copied third-party design assets."""
import tempfile
import subprocess
import unittest
from pathlib import Path

from agent_ee.reference_blocks import block_contract, validate_block
from tests.block_parts_fixture import parts_manifest
from agent_ee.storage import digest, write_json


SHEET = b'''(kicad_sch (version 20250114) (generator "eeschema")
 (hierarchical_label "VIN" (shape input) (at 10 10 0)
  (effects (font (size 1.27 1.27)))
  (uuid "cb914ae0-18b3-43de-8045-9d41a1731b16")))'''


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def block(root):
    (root / 'sheet.kicad_sch').write_bytes(SHEET)
    write_json(root / 'parts.json', parts_manifest())
    record = dict(schema='agent-ee.block/1', id='owned', version='1', role='power',
        source=dict(repository='local-owned', commit='a' * 40,
                    paths={'sheet.kicad_sch': 'sheet.kicad_sch'}, license='Owned test fixture'),
        sheet='sheet.kicad_sch', parts_manifest='parts.json',
        boundary_ports=[dict(name='VIN', net_name='VIN', direction='input',
                             voltage_domain='supply', current_a=1)],
        requirements=[dict(id='R-port', statement='VIN is an input', checks=['ports'])],
        files={p: digest(root / p) for p in ('sheet.kicad_sch', 'parts.json')}, runs=[])
    write_json(root / 'block-contract.json', block_contract(record))
    record['files']['block-contract.json'] = digest(root / 'block-contract.json')
    write_json(root / 'block.json', record)
    return record


class ReferenceBlockTests(unittest.TestCase):
    def test_cli_unexpected_failure_has_distinct_exit_and_diagnostic(self):
        import contextlib
        import io
        import json
        from unittest.mock import patch
        from agent_ee.reference_blocks import main
        output, errors = io.StringIO(), io.StringIO()
        with patch('agent_ee.reference_blocks.validate_block', side_effect=RuntimeError('control crash')), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(main(['validate', 'unused']), 3)
        self.assertEqual(json.loads(output.getvalue())['status'], 'error')
        self.assertIn('RuntimeError: control crash', errors.getvalue())

    def test_parts_manifest_uses_shared_validator(self):
        import copy
        from agent_ee.parts_manifest import validate_manifest
        validate_manifest(parts_manifest())
        mutations = [lambda d: d.pop('cad'),
                     lambda d: d.update(parts=[]),
                     lambda d: d['parts'][0].update(lcsc='NOT-AN-LCSC'),
                     lambda d: d['parts'][0].pop('instances'),
                     lambda d: d['parts'][0].update(required='yes'),
                     lambda d: d['parts'].append(copy.deepcopy(d['parts'][0]))]
        for mutate in mutations:
            with self.subTest(mutation=mutate), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                record = block(root)
                document = parts_manifest()
                mutate(document)
                with self.assertRaises(ValueError):
                    validate_manifest(document)
                write_json(root / 'parts.json', document)
                record['files']['parts.json'] = digest(root / 'parts.json')
                write_json(root / 'block.json', record)
                with self.assertRaises(ValueError):
                    validate_block(root)

    def test_port_refusal_names_missing_extra_and_wrong_direction(self):
        for name, direction, message in [('ABSENT', 'input', 'ABSENT'),
                                          ('VIN', 'output', 'VIN'),
                                          (None, None, 'VIN')]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                record = block(root)
                record['boundary_ports'] = ([dict(record['boundary_ports'][0], name=name, direction=direction)]
                                            if name else [])
                write_json(root / 'block-contract.json', block_contract(record))
                record['files']['block-contract.json'] = digest(root / 'block-contract.json')
                write_json(root / 'block.json', record)
                with self.assertRaisesRegex(ValueError, message):
                    validate_block(root)

    def test_capture_without_run_is_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            block(root)
            report = validate_block(root)
            self.assertEqual(report['status'], 'unverified')
            self.assertEqual(report['covered_requirements'], [])

    def test_principal_metadata_and_payload_boundaries(self):
        import copy
        cases = [
            ('unknown field', lambda r: r.update(status='verified')),
            ('missing port', lambda r: r['boundary_ports'][0].update(name='ABSENT')),
            ('wrong direction', lambda r: r['boundary_ports'][0].update(direction='output')),
            ('duplicate port', lambda r: r['boundary_ports'].append(copy.deepcopy(r['boundary_ports'][0]))),
            ('omitted port', lambda r: r.update(boundary_ports=[])),
            ('changed declared domain', lambda r: r['boundary_ports'][0].update(voltage_domain='invented')),
            ('negative current', lambda r: r['boundary_ports'][0].update(current_a=-1)),
            ('boolean current', lambda r: r['boundary_ports'][0].update(current_a=True)),
            ('nonfinite current', lambda r: r['boundary_ports'][0].update(current_a=float('inf'))),
            ('missing checks', lambda r: r['requirements'][0].update(checks=[])),
            ('duplicate requirement', lambda r: r['requirements'].append(copy.deepcopy(r['requirements'][0]))),
            ('unpinned source', lambda r: r['source'].update(commit='HEAD')),
            ('unsafe path', lambda r: r['files'].update({'../escape': '0'*64})),
            ('missing source path', lambda r: r['source'].update(paths={})),
            ('copied status', lambda r: r.update(runs=[dict(status='verified')])),
        ]
        for name, mutate in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                record = block(root)
                mutate(record)
                # Nonfinite JSON is tested as ingress bytes (writer rejects it).
                import json
                (root / 'block.json').write_text(json.dumps(record))
                with self.assertRaises(ValueError):
                    validate_block(root)
        for attack in ('changed sheet', 'extra file', 'symlink', 'wrong parts', 'nested metadata'):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                record = block(root)
                if attack == 'changed sheet':
                    (root / 'sheet.kicad_sch').write_bytes(SHEET + b'\n')
                elif attack == 'extra file':
                    (root / 'unlisted').write_text('unlisted')
                elif attack == 'symlink':
                    (root / 'link').symlink_to(root / 'sheet.kicad_sch')
                elif attack == 'nested metadata':
                    (root / 'nested').mkdir()
                    (root / 'nested/block.json').write_text('unlisted')
                else:
                    write_json(root / 'parts.json', {'schema': 'invented'})
                    record['files']['parts.json'] = digest(root / 'parts.json')
                    write_json(root / 'block.json', record)
                with self.assertRaises(ValueError):
                    validate_block(root)

    def test_offline_git_capture_pins_committed_bytes_and_refuses_links(self):
        from agent_ee.reference_blocks import capture_block
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / 'source'
            repo.mkdir()
            record = block(repo)
            git(repo, 'init', '-q')
            git(repo, 'add', '.')
            git(repo, '-c', 'user.name=Owned', '-c', 'user.email=owned@example.invalid', 'commit', '-qm', 'Owned fixture')
            commit = git(repo, 'rev-parse', 'HEAD')
            (repo / 'sheet.kicad_sch').write_text('dirty source must not be captured')
            report = capture_block(record, repo, commit, root / 'capture', parts=repo / 'parts.json')
            self.assertEqual(report['status'], 'unverified')
            self.assertEqual((root / 'capture/sheet.kicad_sch').read_bytes(), SHEET)
            from agent_ee.storage import read_json
            captured = read_json(root / 'capture/block.json')
            self.assertEqual(captured['source']['commit'], commit)
            self.assertEqual((repo / 'sheet.kicad_sch').read_text(), 'dirty source must not be captured')
            # A local replacement ref must not change the meaning of a pinned commit.
            (repo / 'sheet.kicad_sch').write_bytes(SHEET + b'\n')
            git(repo, 'add', '.')
            git(repo, '-c', 'user.name=Owned', '-c', 'user.email=owned@example.invalid', 'commit', '-qm', 'Replacement fixture')
            replacement = git(repo, 'rev-parse', 'HEAD')
            git(repo, 'replace', commit, replacement)
            capture_block(record, repo, commit, root / 'unreplaced', parts=repo / 'parts.json')
            self.assertEqual((root / 'unreplaced/sheet.kicad_sch').read_bytes(), SHEET)
            with self.assertRaises(FileExistsError):
                capture_block(record, repo, commit, root / 'capture', parts=repo / 'parts.json')
            (repo / 'sheet.kicad_sch').unlink()
            (repo / 'sheet.kicad_sch').symlink_to('parts.json')
            git(repo, 'add', '.')
            git(repo, '-c', 'user.name=Owned', '-c', 'user.email=owned@example.invalid', 'commit', '-qm', 'Link fixture')
            with self.assertRaisesRegex(ValueError, 'regular committed file'):
                capture_block(record, repo, git(repo, 'rev-parse', 'HEAD'), root / 'bad', parts=repo / 'parts.json')

    def test_missing_promised_blob_is_refused_without_contacting_remote(self):
        import shlex
        from agent_ee.reference_blocks import capture_block
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / 'source'
            repo.mkdir()
            record = block(repo)
            git(repo, 'init', '-q')
            git(repo, 'add', '.')
            git(repo, '-c', 'user.name=Owned', '-c', 'user.email=owned@example.invalid', 'commit', '-qm', 'Owned fixture')
            commit = git(repo, 'rev-parse', 'HEAD')
            git(repo, 'config', 'uploadpack.allowFilter', 'true')
            clone = root / 'partial'
            git(root, 'clone', '--filter=blob:none', '--no-checkout', repo.as_uri(), str(clone))
            missing = git(clone, 'rev-list', '--objects', '--missing=print', '--all')
            blob = git(repo, 'rev-parse', commit + ':sheet.kicad_sch')
            self.assertIn('?' + blob, missing.splitlines())
            # A real local transport observer catches lazy fetch without network I/O.
            contacted = root / 'remote-contacted'
            upload = root / 'upload-pack'
            upload.write_text('#!/bin/sh\nprintf fetched > ' + shlex.quote(str(contacted)) +
                              '\nexec git-upload-pack "$@"\n')
            upload.chmod(0o700)
            git(clone, 'config', 'remote.origin.uploadpack', str(upload))
            with self.assertRaises(subprocess.CalledProcessError):
                capture_block(record, clone, commit, root / 'capture', parts=repo / 'parts.json')
            self.assertFalse(contacted.exists(), 'offline capture contacted its promisor remote')
            self.assertFalse((root / 'capture').exists())


class NativeReferenceBlockTests(unittest.TestCase):
    def setUp(self):
        from toolchain import require_toolchain
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-89-native-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def prepare_run(self, *, checks_only=False, undeclared=False, shadow=False, hierarchy=False, layout=False, membership=True, foreign_parts=False):
        import contextlib
        import io
        from unittest.mock import patch
        from agent_ee.cli import run_review, runtime_files, verify
        from agent_ee.storage import read_json
        fixture = Path(__file__).resolve().parents[1] / 'fixtures/hardware-interface'
        from tests.test_parts_subset import captured_fixture, subset_generator
        generator = subset_generator()
        payload = self.root / 'block'
        payload.mkdir()
        record = block(payload)
        dependencies = self.root / 'dependencies'
        dependencies.mkdir()
        document = captured_fixture(self, dependencies)
        write_json(payload / 'parts.json', document)
        record['files']['parts.json'] = digest(payload / 'parts.json')
        sheet = generator.schematic().replace('(property "Value" "R"', '(property "Value" "10k"').rstrip()
        label = '''(hierarchical_label "IN" (shape input) (at 40 50 0)
          (effects (font (size 1 1))) (uuid "cb914ae0-18b3-43de-8045-9d41a1731b16"))'''
        if hierarchy:
            root_id = '5ba845cb-e953-48b0-998e-05c05ee05ecf'
            sheet_id = '230f756d-a550-43ca-8130-2eff9ea6c83e'
            sheet = sheet.replace('(project "binding"', '(project "root"').replace(
                '/' + generator.BASE.uid('root') + '"', '/' + root_id + '/' + sheet_id + '"')
        (payload / 'sheet.kicad_sch').write_text(sheet[:-1] + label + ')\n')
        record['boundary_ports'][0].update(name='IN', net_name='IN')
        if undeclared:
            record['boundary_ports'][0].update(current_a=None, voltage_domain=None)
        if shadow:
            (self.root / 'shadow.kicad_sch').write_bytes((payload / 'sheet.kicad_sch').read_bytes())
            data = (payload / 'sheet.kicad_sch').read_bytes()
            self.assertIn(b'10k', data)
            (payload / 'sheet.kicad_sch').write_bytes(data.replace(b'10k', b'200k'))
        record['files']['sheet.kicad_sch'] = digest(payload / 'sheet.kicad_sch')
        if layout:
            (payload / 'layout.kicad_pcb').write_bytes((fixture / 'interface.kicad_pcb').read_bytes())
            record['layout'] = 'layout.kicad_pcb'
            record['source']['paths']['layout.kicad_pcb'] = 'layout.kicad_pcb'
            record['files']['layout.kicad_pcb'] = digest(payload / 'layout.kicad_pcb')
        record['requirements'] = [dict(id='interface', statement='Preserve declared interface invariants.', checks=['interface'])]
        write_json(payload / 'block-contract.json', block_contract(record))
        record['files']['block-contract.json'] = digest(payload / 'block-contract.json')
        write_json(payload / 'block.json', record)
        scope = read_json(fixture / 'control.scope.json')
        scope['schema'] = 'agent-ee.scope/2'
        scope['name'] = 'Owned reference-block qualification'
        scope['scope'] = 'Native resistor identity and value in an original synthetic fixture.'
        scope['checks'] = scope['checks'][:2]
        scope['baseline']['requirements'] = scope['baseline']['requirements'][:1]
        del scope['baseline']['requirements'][0]['intent']
        scope['inputs'] = [dict(path='block/sheet.kicad_sch', at='interface.kicad_sch'),
                           dict(path='block/parts.json', at='parts.json'),
                           dict(path='block/block-contract.json', at='block-contract.json')]
        for name in ('requirements.md', 'contract.json', 'interface.kicad_pcb'):
            (self.root / name).write_bytes((fixture / name).read_bytes())
            scope['inputs'].append(dict(path=name, at=name))
        (self.root / 'requirements.md').write_text(
            '# Owned reference-block fixture\n\n## Structural interface\n\n'
            'The native resistor R1 must have value 10k. This is original synthetic test data.\n')
        contract = dict(schema='agent-ee.interface/1',
            components=dict(series=dict(locations=[dict(sheet='/', reference='R1')], symbols=['Owned:R'])),
            terminals={}, allowed_edits=[],
            invariants=[dict(id='series-value', requirement='interface',
                source=dict(path='requirements.md', locator='Structural interface'),
                kind='value', component='series', equals='10k')])
        write_json(self.root / 'contract.json', contract)
        if hierarchy:
            document['parts'][0]['instances'][0]['sheet'] = '/' + sheet_id + '/'
            (self.root / 'root.kicad_sch').write_text(f'''(kicad_sch (version 20250114)
              (generator "eeschema") (uuid "{root_id}") (paper "A4") (lib_symbols)
              (sheet (at 20 20) (size 20 20) (stroke (width 0) (type default))
                (fill (color 0 0 0 0)) (uuid "{sheet_id}")
                (property "Sheetname" "OwnedChild" (at 20 19 0) (effects (font (size 1 1))))
                (property "Sheetfile" "interface.kicad_sch" (at 20 41 0) (effects (font (size 1 1))))))''')
            scope['inputs'].append(dict(path='root.kicad_sch', at='root.kicad_sch'))
            scope['schematic'] = 'root.kicad_sch'
            contract = read_json(self.root / 'contract.json')
            for component in contract['components'].values():
                for loc in component['locations']:
                    loc['sheet'] = '/' + sheet_id + '/'
            for invariant in contract['invariants']:
                if invariant['kind'] == 'named_net':
                    invariant['names'] = ['/OwnedChild' + n for n in invariant['names']]
            write_json(self.root / 'contract.json', contract)
        document['declaration_source']['path'] = 'parts-requirements.md'
        for path in sorted(dependencies.rglob('*')):
            if path.is_file():
                relative = path.relative_to(dependencies).as_posix()
                if relative == 'parts.json':
                    continue
                scope['inputs'].append(dict(path='dependencies/' + relative,
                    at='parts-requirements.md' if relative == 'requirements.md' else relative))
        write_json(payload / 'parts.json', document)
        record['files']['parts.json'] = digest(payload / 'parts.json')
        write_json(payload / 'block.json', record)
        if foreign_parts:
            write_json(self.root / 'foreign-parts.json', document)
            scope['inputs'].append(dict(path='foreign-parts.json', at='foreign-parts.json'))
        if membership:
            scope['checks'].append(dict(id='parts', kind='parts_subset', params=dict(
                manifest='foreign-parts.json' if foreign_parts else 'parts.json',
                netlist_check='netlist', requirement_id='membership')))
            scope['baseline']['requirements'].append(dict(id='membership', statement='Declared parts membership',
                source={k: document['declaration_source'][k] for k in ('path', 'locator')}, checks=['parts']))
        if shadow:
            scope['inputs'].append(dict(path='shadow.kicad_sch', at='shadow.kicad_sch'))
            scope['schematic'] = 'shadow.kicad_sch'
        call = dict(harness='codex', model='scripted-fixture', effort='high', timeout_s=30)
        scope['review_profile'] = dict(schema='agent-ee.profile/1', name='owned-block-qualification',
            reviewers=[dict(**call, role=r) for r in ('circuit', 'physical')], adjudicator=call)
        write_json(self.root / 'scope.json', scope)
        def dispatch(run, label, definition, prompt, schema, timeout, **kwargs):
            packet = read_json(Path(run) / 'packet.json')
            identity = {k: packet[k] for k in ('candidate_id', 'run_id')}
            if label == 'adjudicator':
                return dict(execution='ok'), dict(schema='agent-ee.decisions/1', **identity, decisions=[])
            self.assertNotIn('"initial_reviews"', prompt)
            return dict(execution='ok'), dict(schema='agent-ee.review/1', **identity,
                packet_sha256=packet['packet_sha256'], role=label, coverage=['Owned fixture'],
                limitations=['Scripted transport, no model inference'], findings=[])
        runtime = runtime_files()
        for name in ('codex', 'codex_tool_host'):
            runtime[name] = dict(path='/usr/bin/true', sha256=digest('/usr/bin/true'))
        admission = patch('agent_ee.cli.runtime_files', return_value=runtime)
        admission.start()
        self.addCleanup(admission.stop)
        run = self.root / 'run'
        output = io.StringIO()
        with patch('agent_ee.reviewers.dispatch', side_effect=dispatch), contextlib.redirect_stdout(output):
            status = run_review(self.root / 'scope.json', run, 120, checks_only=checks_only)
        self.assertTrue((run / 'candidate.json').exists(), output.getvalue() + (run / 'REPORT.md').read_text())
        result = verify(run)
        self.assertEqual(status, 1 if checks_only else 0, result)
        self.assertEqual(result['checks'][0]['execution'], 'ok', result)
        self.assertEqual(result['checks'][1]['outcome'], 'pass', result)
        baseline = read_json(run / 'baseline.json')
        record['runs'] = [dict(run_id=result['run_id'], candidate_id=result['candidate_id'],
            baseline_id=baseline['baseline_id'], files={'sheet.kicad_sch': 'interface.kicad_sch', 'parts.json': 'parts.json', 'block-contract.json': 'block-contract.json'})]
        if layout:
            record['runs'][0]['files']['layout.kicad_pcb'] = 'interface.kicad_pcb'
        write_json(payload / 'block.json', record)
        return payload, record, run, result

    def test_defective_captured_block_cannot_inherit_native_shadow_pass(self):
        import time
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        from agent_ee.storage import read_json
        payload, record, run, result = self.prepare_run(shadow=True)
        self.assertEqual((result['state'], result['outcome']),
                         ('complete', 'criteria_satisfied'))
        report = validate_block(payload, {result['run_id']: run})
        self.assertEqual(report['status'], 'unverified')
        self.assertEqual(report['covered_requirements'], [])
        self.assertEqual(report['parts_membership']['status'], 'unverified')
        # Independent fresh native execution proves the captured block is defective.
        scope = read_json(self.root / 'scope.json')
        scope['schematic'] = 'interface.kicad_sch'
        write_json(self.root / 'actual.scope.json', scope)
        actual = self.root / 'actual'
        actual.mkdir()
        capture(self.root / 'actual.scope.json', actual)
        checks = run_checks(actual, time.monotonic() + 120)
        self.assertEqual(checks[0]['execution'], 'ok', checks)
        self.assertEqual((checks[1]['execution'], checks[1]['outcome']), ('ok', 'defect'), checks)

    def test_enclosing_native_hierarchy_qualifies_actual_block_occurrence(self):
        payload, record, run, result = self.prepare_run(hierarchy=True)
        report = validate_block(payload, {result['run_id']: run})
        self.assertEqual(report['status'], 'verified', report)
        self.assertEqual(report['covered_requirements'], ['interface'])

    def test_unchecked_optional_layout_remains_unverified(self):
        payload, record, run, result = self.prepare_run(layout=True)
        report = validate_block(payload, {result['run_id']: run})
        self.assertEqual(report['status'], 'unverified', report)
        self.assertEqual(report['covered_requirements'], [])

    def test_complete_sealed_run_and_tamper_refusal(self):
        from agent_ee.storage import read_json
        payload, record, run, result = self.prepare_run()
        locations = {result['run_id']: run}
        report = validate_block(payload, locations)
        self.assertEqual(report['status'], 'verified')
        self.assertEqual(report['parts_membership'], dict(status='verified', evidence=[dict(
            run_id=result['run_id'], check_id='parts', manifest_sha256=record['files']['parts.json'])]))
        seal = run / 'work/verification/evidence.json'
        original = seal.read_bytes()
        value = read_json(seal)
        value['candidate_id'] = '0' * 64
        write_json(seal, value)
        with self.assertRaises(ValueError):
            validate_block(payload, locations)
        seal.write_bytes(original)
        self.assertEqual(validate_block(payload, locations)['status'], 'verified')
        membership_path = run / 'work/parts/parts-subset.json'
        original_membership = membership_path.read_bytes()
        value = read_json(membership_path)
        value['manifest_sha256'] = '0' * 64
        write_json(membership_path, value)
        with self.assertRaises(ValueError):
            validate_block(payload, locations)
        membership_path.write_bytes(original_membership)
        self.assertEqual(validate_block(payload, locations)['status'], 'verified')
        # Sealed coordinator component witnesses cannot be rewritten to donate coverage.
        check_path = run / 'checks/interface.json'
        original_check = check_path.read_bytes()
        check = read_json(check_path)
        check['details']['invariants'][0]['actual']['components']['series']['sheet'] = '/foreign/'
        write_json(check_path, check)
        with self.assertRaises(ValueError):
            validate_block(payload, locations)
        check_path.write_bytes(original_check)
        self.assertEqual(validate_block(payload, locations)['status'], 'verified')
        # A valid but changed parts manifest cannot borrow the old membership.
        original_parts = (payload / 'parts.json').read_bytes()
        parts = read_json(payload / 'parts.json')
        parts['parts'][0]['stock'] = None
        write_json(payload / 'parts.json', parts)
        record['files']['parts.json'] = digest(payload / 'parts.json')
        write_json(payload / 'block.json', record)
        with self.assertRaisesRegex(ValueError, 'exact block bytes: parts.json'):
            validate_block(payload, locations)
        (payload / 'parts.json').write_bytes(original_parts)
        record['files']['parts.json'] = digest(payload / 'parts.json')
        write_json(payload / 'block.json', record)
        # Updating the block's own hashes cannot launder a changed sheet.
        (payload / 'sheet.kicad_sch').write_bytes((payload / 'sheet.kicad_sch').read_bytes() + b'\n')
        record['files']['sheet.kicad_sch'] = digest(payload / 'sheet.kicad_sch')
        write_json(payload / 'block.json', record)
        with self.assertRaisesRegex(ValueError, 'exact block bytes'):
            validate_block(payload, locations)

    def test_passing_interface_without_membership_remains_unverified(self):
        payload, record, run, result = self.prepare_run(membership=False)
        report = validate_block(payload, {result['run_id']: run})
        self.assertEqual(report['covered_requirements'], ['interface'])
        self.assertEqual(report['status'], 'unverified')
        self.assertEqual(report['parts_membership'], dict(status='unverified', evidence=[]))

    def test_membership_for_other_manifest_path_cannot_qualify_block(self):
        payload, record, run, result = self.prepare_run(foreign_parts=True)
        self.assertEqual(result['checks'][2]['outcome'], 'pass')
        report = validate_block(payload, {result['run_id']: run})
        self.assertEqual(report['status'], 'unverified')
        self.assertEqual(report['parts_membership']['evidence'], [])

    def test_cli_missing_run_state_is_refused_not_unverified(self):
        import contextlib
        import io
        import json
        from agent_ee.reference_blocks import main
        from agent_ee.storage import read_json
        payload, record, run, result = self.prepare_run()
        args = ['validate', str(payload), '--run', result['run_id'] + '=' + str(run)]
        for state, expected in [('complete', 0), ('missing', 2)]:
            if state == 'missing':
                stored = read_json(run / 'result.json')
                del stored['state']
                write_json(run / 'result.json', stored)
            output = io.StringIO()
            with self.subTest(state=state), contextlib.redirect_stdout(output):
                self.assertEqual(main(args), expected)
            self.assertEqual(json.loads(output.getvalue())['status'], 'verified' if expected == 0 else 'refused')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(['validate', str(payload)]), 1)
        self.assertEqual(json.loads(output.getvalue())['status'], 'unverified')

    def test_checks_only_is_unverified_and_missing_run_is_unverified(self):
        payload, record, run, result = self.prepare_run(checks_only=True)
        self.assertEqual(validate_block(payload, {result['run_id']: run})['status'], 'unverified')
        self.assertEqual(validate_block(payload)['status'], 'unverified')

    def test_foreign_candidate_baseline_mapping_and_requirement_refused(self):
        import copy
        payload, original, run, result = self.prepare_run()
        mutations = [lambda r: r['runs'][0].update(candidate_id='0'*64),
                     lambda r: r['runs'][0].update(baseline_id='0'*64),
                     lambda r: r['runs'][0].update(run_id='foreign'),
                     lambda r: r['runs'][0]['files'].pop('parts.json'),
                     lambda r: r['runs'][0]['files'].update({'parts.json':'missing.json'}),
                     lambda r: r['requirements'][0].update(statement='Changed requirement'),
                     lambda r: r['requirements'][0].update(checks=['invented'])]
        for mutate in mutations:
            record = copy.deepcopy(original)
            mutate(record)
            write_json(payload / 'block.json', record)
            with self.subTest(record=record), self.assertRaises(ValueError):
                validate_block(payload, {record['runs'][0]['run_id']: run})

    def test_changed_contract_with_refreshed_local_hash_is_not_run_coverage(self):
        payload, record, run, result = self.prepare_run()
        record['boundary_ports'][0]['current_a'] = 100
        write_json(payload / 'block-contract.json', block_contract(record))
        record['files']['block-contract.json'] = digest(payload / 'block-contract.json')
        write_json(payload / 'block.json', record)
        with self.assertRaisesRegex(ValueError, 'exact block bytes'):
            validate_block(payload, {result['run_id']: run})

    def test_native_hierarchy_export_agrees_with_explicit_child_ports(self):
        import time
        from agent_ee.candidate import capture
        from agent_ee.checks import run_checks
        from agent_ee.reference_blocks import hierarchy_ports
        from agent_ee.storage import read_json
        from xml.etree import ElementTree as ET
        root_id = '5ba845cb-e953-48b0-998e-05c05ee05ecf'
        sheet_id = '230f756d-a550-43ca-8130-2eff9ea6c83e'
        # Actual native hierarchy, authored for this test, not an inferred port.
        import importlib.util
        fixture = Path(__file__).resolve().parents[1] / 'fixtures/hardware-interface/generate.py'
        spec = importlib.util.spec_from_file_location('owned_hierarchy', fixture)
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        child = generator.schematic(resistor='100k').rstrip()
        child = child.replace('(project "interface"', '(project "root"').replace(
            '/' + generator.uid('root') + '"', '/' + root_id + '/' + sheet_id + '"')
        port = '(hierarchical_label "VIN" (shape input) (at 40 50 0) (effects (font (size 1 1))) (uuid "cb914ae0-18b3-43de-8045-9d41a1731b16"))'
        (self.root / 'child.kicad_sch').write_text(child[:-1] + port + ')\n')
        (self.root / 'root.kicad_sch').write_text(f'''(kicad_sch (version 20250114)
          (generator "eeschema") (uuid "{root_id}") (paper "A4") (lib_symbols)
          (sheet (at 20 20) (size 20 20) (stroke (width 0) (type default))
            (fill (color 0 0 0 0)) (uuid "{sheet_id}")
            (property "Sheetname" "OwnedChild" (at 20 19 0) (effects (font (size 1 1))))
            (property "Sheetfile" "child.kicad_sch" (at 20 41 0) (effects (font (size 1 1))))
            (pin "VIN" input (at 20 25 180) (effects (font (size 1 1)))
              (uuid "ed111b69-8438-49db-b21d-ecb68a8aa749"))))''')
        scope = dict(schema='agent-ee.scope/1', name='Owned hierarchy', scope='Native port qualification',
            inputs=[dict(path=p, at=p) for p in ('root.kicad_sch', 'child.kicad_sch')],
            schematic='root.kicad_sch', checks=[dict(id='native', kind='netlist')])
        write_json(self.root / 'scope.json', scope)
        run = self.root / 'hierarchy-run'
        run.mkdir()
        capture(self.root / 'scope.json', run)
        check, = run_checks(run, time.monotonic() + 60)
        self.assertEqual(check['execution'], 'ok', check)
        xml = ET.parse(run / 'work/native/design.xml')
        self.assertIn('/OwnedChild/', [node.get('name') for node in xml.findall('./design/sheet')])
        self.assertEqual(hierarchy_ports((run / 'inputs/child.kicad_sch').read_bytes()), {'VIN':'input'})
        self.assertEqual(read_json(run / 'candidate.json')['files']['child.kicad_sch'],
                         digest(self.root / 'child.kicad_sch'))

    def test_escalated_projection_does_not_inherit_passing_checks(self):
        from agent_ee.cli import save_result
        payload, record, run, result = self.prepare_run(checks_only=True)
        result['state'] = 'escalated'
        save_result(run, result)
        report = validate_block(payload, {result['run_id']:run})
        self.assertEqual(report['status'], 'unverified')
        self.assertEqual(report['runs'][0]['status'], 'unverified')

    def test_missing_additional_reference_cannot_be_hidden_by_passing_run(self):
        import copy
        payload, record, run, result = self.prepare_run()
        missing = copy.deepcopy(record['runs'][0])
        missing['run_id'] = 'unavailable-run'
        record['runs'].append(missing)
        write_json(payload / 'block.json', record)
        self.assertEqual(validate_block(payload, {result['run_id']: run})['status'], 'unverified')

    def test_unknown_electrical_declarations_are_not_qualified_by_run(self):
        payload, record, run, result = self.prepare_run(undeclared=True)
        report = validate_block(payload, {result['run_id']:run})
        self.assertEqual(report['status'], 'unverified')
        self.assertEqual(report['undeclared_ports'], ['IN'])
