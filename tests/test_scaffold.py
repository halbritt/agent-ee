"""Empty native scaffold and campaign contracts over original synthetic inputs.

Native tools and seals are real. Campaign inference uses scripted test responses;
these tests do not establish model quality, component suitability or release.
"""
import contextlib
import copy
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_ee.candidate import capture
from agent_ee.scaffold import scaffold
from agent_ee.storage import digest, read_json, write_json
from agent_ee.symbol_library import children, parse_sexpr
# Shared existing fixture builders use discovery-style sibling imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_parts_subset import captured_fixture
from toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]


class ScaffoldFixture:
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='agent-ee-87-')))
        self.source = self.root / 'source'
        self.source.mkdir()
        self.parts = captured_fixture(self, self.source, trio=True)
        self.envelope = read_json(ROOT / 'fixtures/board-envelope/rc.envelope.json')
        write_json(self.source / 'envelope.json', self.envelope)
        (self.source / 'project').mkdir()
        self.project = dict(meta=dict(version=1, filename='design.kicad_pro'),
            board=dict(design_settings=dict(rules=dict(min_clearance=0.25, min_track_width=0.2))),
            net_settings=dict(meta=dict(version=3), classes=[dict(name='Default', clearance=0.25,
                track_width=0.2, via_diameter=0.6, via_drill=0.3)]))
        write_json(self.source / 'project/design.kicad_pro', self.project)
        (self.source / 'targets.md').write_text('Synthetic RC target: native validity and required R1/C1/J1.\n')
        (self.source / 'reference.md').write_text('Original synthetic RC planning fixture, not hardware evidence.\n')
        self.scope = dict(schema='agent-ee.scope/2', name='scaffold contract',
            scope='Native validity and nonempty RC content for an original synthetic fixture.',
            limitations=['Envelope is generation input only; complete envelope acceptance is not configured.'],
            inputs=[dict(path='source', at='.')], schematic='project/design.kicad_sch',
            board='project/design.kicad_pcb', mutable_inputs=['project/design.kicad_sch', 'project/design.kicad_pcb'],
            checks=[dict(id='erc', kind='erc'), dict(id='drc', kind='drc')], requirements=['requirements.md'],
            baseline=dict(requirements=[dict(id='native', statement='Native ERC and DRC pass.',
                source=dict(path='requirements.md', locator='line 1'), checks=['erc', 'drc'])],
                targets=['targets.md'], reference_architectures=['reference.md']))
        # Each destination is explicit; the existing capture contract forbids '.'.
        self.scope['inputs'] = [dict(path='source/' + p.relative_to(self.source).as_posix(),
            at=p.relative_to(self.source).as_posix()) for p in sorted(self.source.rglob('*')) if p.is_file()]
        self.scope_path = self.root / 'planning.scope.json'
        write_json(self.scope_path, self.scope)

    def build(self, name='scaffold'):
        return scaffold(self.source / 'envelope.json', self.source / 'parts.json',
                        self.root / name, scope_path=self.scope_path)

    def staged(self):
        for check in self.scope['checks']:
            check['stage'] = 'schematic' if check['kind'] == 'erc' else 'placement'
        self.scope['stages'] = dict(schema='agent-ee.stages/1', order=['schematic', 'placement'],
            subjects=dict(schematic=dict(path=self.scope['schematic'], kind='symbols', references=['R1', 'C1', 'J1']),
                          placement=dict(path=self.scope['board'], kind='footprints', references=['R1', 'C1', 'J1'])),
            finding_retry_limit=3)
        write_json(self.scope_path, self.scope)

class ScaffoldTests(ScaffoldFixture, unittest.TestCase):
    def test_two_outputs_are_byte_identical_and_capture_has_same_identity(self):
        first, second = self.build('a'), self.build('b')
        inventories = [{p.relative_to(self.root / name).as_posix(): p.read_bytes()
                       for p in (self.root / name).rglob('*') if p.is_file()} for name in ('a', 'b')]
        self.assertEqual(inventories[0], inventories[1])
        ids = []
        for name in ('a', 'b'):
            run = self.root / ('capture-' + name)
            run.mkdir()
            ids.append(capture(self.root / name / 'scope.json', run)['candidate_id'])
        self.assertEqual(ids, [first['candidate_id'], second['candidate_id']])
        self.assertEqual(first['inputs']['files']['parts.json'], digest(self.source / 'parts.json'))
        for path, pin in first['outputs'].items():
            self.assertEqual(pin, digest(self.root / 'a' / path))
        self.assertIn('scaffold.py', first['tool']['policy_files'])
        scope = read_json(self.root / 'a/scope.json')
        self.assertEqual(scope['baseline'], self.scope['baseline'])
        self.assertEqual(scope['checks'], self.scope['checks'])
        pro = read_json(self.root / 'a/inputs/project/design.kicad_pro')
        self.assertEqual(pro['board'], self.project['board'])
        self.assertEqual(pro['net_settings'], self.project['net_settings'])

    def test_board_matches_declared_outline_layers_and_stackup_with_no_content(self):
        from agent_ee.board_envelope import round_trip
        self.build()
        pcb = (self.root / 'scaffold/inputs/project/design.kicad_pcb').read_bytes()
        node = parse_sexpr(pcb)
        for tag in ('footprint', 'segment', 'via', 'zone', 'net'):
            self.assertEqual(children(node, tag), [], tag)
        trip = round_trip((self.source / 'envelope.json').read_bytes(), pcb)
        statuses = {item['field']: item['status'] for item in trip['fields']}
        self.assertEqual(statuses['/outline'], 'match')
        self.assertEqual(statuses['/layer_count'], 'match')
        self.assertEqual(statuses['/stackup/thickness_mm'], 'match')
        for index in range(3):
            self.assertEqual(statuses[f'/stackup/layers/{index}/thickness_mm'], 'match')
        # Copper weight has no native field and cannot be promoted to acceptance.
        self.assertEqual(statuses['/stackup/layers/0/copper_weight_oz'], 'not_in_board')
        self.assertEqual(len(children(node, 'gr_line')), 4)

    def test_library_tables_resolve_only_into_captured_libraries(self):
        self.build()
        project = self.root / 'scaffold/inputs/project'
        for name in ('sym-lib-table', 'fp-lib-table'):
            node = parse_sexpr((project / name).read_bytes())
            self.assertEqual(node[0], {'sym-lib-table': 'sym_lib_table', 'fp-lib-table': 'fp_lib_table'}[name])
            for lib in children(node, 'lib'):
                uri = children(lib, 'uri')[0][1]
                self.assertTrue(uri.startswith('${KIPRJMOD}/'))
                target = project / uri.removeprefix('${KIPRJMOD}/')
                self.assertTrue(target.exists(), target)
                self.assertTrue(target.resolve().is_relative_to(project.parent.resolve()))
        self.assertNotIn('project/design.kicad_pro', read_json(self.root / 'scaffold/scope.json')['mutable_inputs'])

    def test_precreates_nested_child_sheets_with_exact_native_instance_ids(self):
        import uuid
        a, b = str(uuid.uuid5(uuid.NAMESPACE_URL, 'owned-block-a')), str(uuid.uuid5(uuid.NAMESPACE_URL, 'owned-block-b'))
        self.parts['parts'][1]['instances'][0]['sheet'] = '/' + a + '/' + b + '/'
        write_json(self.source / 'parts.json', self.parts)
        receipt = self.build()
        self.assertEqual(set(receipt['sheets']), {'/', '/' + a + '/', '/' + a + '/' + b + '/'})
        scope = read_json(self.root / 'scaffold/scope.json')
        for path in receipt['sheets'].values():
            self.assertIn(path, scope['mutable_inputs'])
            node = parse_sexpr((self.root / 'scaffold/inputs' / path).read_bytes())
            self.assertEqual(children(node, 'symbol'), [])
        root = parse_sexpr((self.root / 'scaffold/inputs' / receipt['sheets']['/']).read_bytes())
        self.assertEqual(children(children(root, 'sheet')[0], 'uuid'), [['uuid', a]])

    def test_noncanonical_native_paths_are_refused_before_publishing(self):
        self.scope['schematic'] = 'project//design.kicad_sch'
        write_json(self.scope_path, self.scope)
        with self.assertRaisesRegex(ValueError, 'canonical native design paths'):
            self.build()
        self.assertFalse((self.root / 'scaffold').exists())

    def test_bad_parts_pin_is_refused_without_publishing_output(self):
        (self.source / 'Owned.kicad_sym').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.build()
        self.assertFalse((self.root / 'scaffold').exists())

    def test_existing_output_and_recursive_source_output_are_refused(self):
        self.build()
        before = (self.root / 'scaffold/scope.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'must not exist'):
            self.build()
        self.assertEqual(before, (self.root / 'scaffold/scope.json').read_bytes())
        self.scope['inputs'] = [dict(path='source', at='captured')]
        self.scope['schematic'] = 'captured/project/design.kicad_sch'
        self.scope['board'] = 'captured/project/design.kicad_pcb'
        write_json(self.scope_path, self.scope)
        with self.assertRaisesRegex(ValueError, 'outside every source'):
            scaffold(self.source / 'envelope.json', self.source / 'parts.json', self.source / 'out', scope_path=self.scope_path)
        self.assertFalse((self.source / 'out').exists())

    def test_design_writer_still_refuses_added_deleted_and_immutable_files(self):
        from agent_ee.campaign import check_proposal, mutable_inputs
        self.build()
        run = self.root / 'captured'
        run.mkdir()
        manifest = capture(self.root / 'scaffold/scope.json', run)
        baseline = read_json(run / 'baseline.json')
        state = dict(baseline=baseline, mutable_inputs=mutable_inputs(manifest, baseline))
        design = self.root / 'design'
        shutil.copytree(run / 'inputs', design)
        extra = design / 'project/extra.kicad_sch'
        extra.write_text('extra')
        with self.assertRaisesRegex(ValueError, 'implementation changed the captured file inventory'):
            check_proposal(design, manifest, state)
        extra.unlink()
        board = design / manifest['scope']['board']
        data = board.read_bytes()
        board.unlink()
        with self.assertRaisesRegex(ValueError, 'implementation changed the captured file inventory'):
            check_proposal(design, manifest, state)
        board.write_bytes(data)
        rules = design / 'project/design.kicad_pro'
        rules.write_bytes(rules.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'immutable verification input'):
            check_proposal(design, manifest, state)

    def test_uncovered_binding_is_refused_instead_of_weakening_scope(self):
        self.scope['schema'] = 'agent-ee.scope/3'
        self.scope['envelope'] = dict(path='envelope.json', profile=None, dialog=None)
        for req in self.scope['baseline']['requirements']:
            req['intent'] = 'acceptance'
        write_json(self.scope_path, self.scope)
        with self.assertRaisesRegex(ValueError, 'envelope obligations lack executable coverage'):
            self.build()
        self.assertFalse((self.root / 'scaffold').exists())


class NativeScaffoldTests(ScaffoldFixture, unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        super().setUp()

    def test_cli_scaffold_and_review_run_actual_erc_drc_and_public_verify(self):
        proc = subprocess.run([sys.executable, '-m', 'agent_ee.cli', 'scaffold', str(self.source / 'envelope.json'),
            str(self.source / 'parts.json'), '--scope', str(self.scope_path), '--out', str(self.root / 'cli')],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = subprocess.run([sys.executable, '-m', 'agent_ee.cli', 'review', str(self.root / 'cli/scope.json'),
            '--out', str(self.root / 'native'), '--checks-only'], capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 1, proc.stderr)  # No independent review was requested.
        from agent_ee.cli import verify
        result = verify(self.root / 'native')
        self.assertEqual([(c['id'], c['execution'], c['outcome']) for c in result['checks']],
                         [('erc', 'ok', 'pass'), ('drc', 'ok', 'pass')])
        for check in result['checks']:
            self.assertTrue(check['evidence'])
        self.assertEqual(result['candidate_id'], read_json(self.root / 'cli/scaffold-receipt.json')['candidate_id'])

    def test_native_erc_loads_every_precreated_nested_sheet(self):
        import uuid
        from agent_ee.cli import run_review, verify
        child = str(uuid.uuid5(uuid.NAMESPACE_URL, 'owned-nested-child'))
        leaf = str(uuid.uuid5(uuid.NAMESPACE_URL, 'owned-nested-leaf'))
        self.parts['parts'][1]['instances'][0]['sheet'] = '/' + child + '/' + leaf + '/'
        write_json(self.source / 'parts.json', self.parts)
        self.build()
        with contextlib.redirect_stdout(io.StringIO()):
            run_review(self.root / 'scaffold/scope.json', self.root / 'hierarchy', 300, checks_only=True)
        result = verify(self.root / 'hierarchy')
        erc = result['checks'][0]
        self.assertEqual((erc['execution'], erc['outcome']), ('ok', 'pass'), erc)
        report = read_json(self.root / 'hierarchy/work/erc/erc.json')
        self.assertEqual(len(report['sheets']), 3)
        self.assertEqual(result['checks'][1]['execution'], 'ok')

    def test_native_four_layer_board_keeps_inner_layers_and_no_design_content(self):
        from agent_ee.executor import execute
        self.envelope['layer_count'] = 4
        self.envelope['stackup']['layers'] = [
            dict(name='F.Cu', kind='copper', thickness_mm=0.035, copper_weight_oz=1),
            dict(name='top-core', kind='dielectric', thickness_mm=0.5),
            dict(name='In1.Cu', kind='copper', thickness_mm=0.035, copper_weight_oz=1),
            dict(name='middle-core', kind='dielectric', thickness_mm=0.46),
            dict(name='In2.Cu', kind='copper', thickness_mm=0.035, copper_weight_oz=1),
            dict(name='bottom-core', kind='dielectric', thickness_mm=0.5),
            dict(name='B.Cu', kind='copper', thickness_mm=0.035, copper_weight_oz=1)]
        write_json(self.source / 'envelope.json', self.envelope)
        self.build()
        work = self.root / 'four-layer-probe'
        script = ('import json, pcbnew; b=pcbnew.LoadBoard("/candidate/project/design.kicad_pcb"); '
                  'print(json.dumps(dict(layers=b.GetCopperLayerCount(), '
                  'footprints=len(list(b.GetFootprints())), tracks=len(list(b.GetTracks())), '
                  'names=[b.GetLayerName(x) for x in b.GetEnabledLayers().CuStack()])))')
        result = execute(self.root / 'scaffold/inputs', work, ['/usr/bin/python3', '-c', script], timeout=60)
        self.assertEqual(result['execution'], 'ok', result)
        import json
        native = json.loads((work / 'stdout.log').read_text())
        self.assertEqual(native, dict(layers=4, footprints=0, tracks=0,
                                     names=['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']))

    def test_native_populated_library_lookup_distinguishes_broken_uri_controls(self):
        from agent_ee.executor import execute
        from test_parts_subset import subset_generator
        self.build()
        original = subset_generator().trio()
        old_root = children(parse_sexpr(original.encode()), 'uuid')[0][1]
        inputs = self.root / 'scaffold/inputs'
        new_root = children(parse_sexpr((inputs / 'project/design.kicad_sch').read_bytes()), 'uuid')[0][1]
        content = original.replace(old_root, new_root).replace('(project "binding"', '(project "design"')
        observed = {}
        for label, table, before, after in (
            ('captured', None, None, None),
            ('missing-symbols', 'sym-lib-table', '../Owned.kicad_sym', '../absent.kicad_sym'),
            ('missing-footprints', 'fp-lib-table', '../Owned.pretty', '../absent.pretty')):
            candidate = self.root / label / 'inputs'
            shutil.copytree(inputs, candidate)
            (candidate / 'project/design.kicad_sch').write_text(content)
            if table:
                path = candidate / 'project' / table
                self.assertIn(before, path.read_text())
                path.write_text(path.read_text().replace(before, after))
            work = self.root / label / 'work'
            record = execute(candidate, work, ['kicad-cli', 'sch', 'erc', '--format', 'json',
                '--severity-all', '--units', 'mm', '-o', '/work/erc.json',
                '/candidate/project/design.kicad_sch'], timeout=120)
            self.assertEqual(record['execution'], 'ok', record)
            report = read_json(work / 'erc.json')
            observed[label] = [v['type'] for sheet in report['sheets'] for v in sheet['violations']]
        # This fixture deliberately has pin/grid defects; only library lookup
        # is under test. Missing output or a failed tool never reaches here.
        libraries = {'lib_symbol_issues', 'footprint_link_issues'}
        self.assertFalse(libraries.intersection(observed['captured']))
        self.assertEqual(observed['missing-symbols'].count('lib_symbol_issues'), 3)
        self.assertEqual(observed['missing-footprints'].count('footprint_link_issues'), 3)
        for codes in observed.values():
            self.assertEqual(sorted(c for c in codes if c not in libraries), sorted(observed['captured']))

    def test_campaign_reaches_first_repair_with_native_stage_witnesses(self):
        from agent_ee.campaign import run_campaign
        from agent_ee.cli import verify
        self.staged()
        self.build()
        definition = dict(harness='codex', model='test', effort='high', timeout_s=10)
        profile = dict(schema='agent-ee.profile/1', name='scripted-scaffold-test',
            reviewers=[dict(**definition, role='circuit'), dict(**definition, role='physical')], adjudicator=definition)
        config = dict(schema='agent-ee.campaign/1', scope='scaffold/scope.json', review_profile=profile,
                      implementation=definition, max_iterations=2, max_seconds=300)
        write_json(self.root / 'campaign.json', config)
        calls = []
        def scripted(run, label, definition, prompt, schema, timeout, *, cancellation=None, executable=None):
            run = Path(run)
            calls.append(label)
            if label == 'implementation':
                state = read_json(self.root / 'campaign/state.json')
                self.assertEqual(state['state'], 'repair')
                self.assertEqual(len(state['history']), 1)
                self.assertEqual(state['history'][0]['earliest_failing_stage'], 'schematic')
                # Deliberately violate inventory to stop after observing the authorized repair boundary.
                (run / 'review-work/implementation/design/project/added.txt').write_text('test refusal')
                return dict(execution='ok'), dict(summary='scripted inventory violation')
            packet = read_json(run / 'packet.json')
            binding = {k: packet[k] for k in ('candidate_id', 'run_id')}
            if label == 'adjudicator':
                result = read_json(run / 'result.json')
                decisions = [dict(finding_id=f['id'], disposition='accepted', reason='Sealed expected content absent.',
                    evidence=[dict(path='checks/' + f['local_id'] + '.json', locator='outcome')])
                    for f in result['findings']]
                return dict(execution='ok'), dict(schema='agent-ee.decisions/1', **binding, decisions=decisions)
            self.assertNotIn('"initial_reviews"', prompt)
            return dict(execution='ok'), dict(schema='agent-ee.review/1', **binding,
                packet_sha256=packet['packet_sha256'], role=label, coverage=['native presence'],
                limitations=['scripted test response'], findings=[])
        # Only inference is scripted; runtime fingerprints, native checks,
        # preflight sandboxing, sealing, adjudication and writer guards remain real.
        with patch('agent_ee.reviewers.dispatch', side_effect=scripted), contextlib.redirect_stdout(io.StringIO()):
            status = run_campaign(self.root / 'campaign.json', self.root / 'campaign')
            result = verify(self.root / 'campaign/runs/1')
        state = read_json(self.root / 'campaign/state.json')
        self.assertEqual(status, 1, state)
        self.assertIn('implementation', calls, dict(state=state['state'], history=state['history'],
            adjudication=read_json(self.root / 'campaign/runs/1/adjudicator-execution.json')))
        # AGENTEE-115 checks the proposal invariant inside the bounded call and names the refusal there.
        self.assertEqual(state['error'], 'ValueError: implementation execution failed; no provider fallback: '
                                         'implementation changed the captured file inventory')
        self.assertEqual([c['execution'] for c in result['checks']], ['ok', 'ok'])
        self.assertEqual([c['details']['reason'] for c in result['checks']], ['stage_not_reached', 'stage_not_reached'])
        self.assertTrue(result['adjudicated'])
        self.assertTrue((self.root / 'campaign/runs/1/work/verification/evidence.json').is_file())


if __name__ == '__main__':
    unittest.main()
