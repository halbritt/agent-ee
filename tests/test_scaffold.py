"""Empty native scaffold and campaign contracts over original synthetic inputs.

Native tools and seals are real. Campaign inference uses scripted test responses;
these tests do not establish model quality, component suitability or release.
"""
import contextlib
import copy
import io
import os
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

    def split_library(self):
        from agent_ee.storage import identity
        manifest = read_json(self.source / 'cad.json')
        member = manifest['libraries'][0]['members'][-1]
        old = member['path']
        member['path'] = 'secondary/' + old
        target = self.source / member['path']
        target.parent.mkdir(parents=True)
        target.write_bytes((self.source / old).read_bytes())
        write_json(self.source / 'cad.json', manifest)
        self.parts['cad']['manifest']['sha256'] = digest(self.source / 'cad.json')
        assignments = read_json(self.source / 'assignments.json')
        blobs = [dict(path=x['path'], sha256=x['sha256']) for x in assignments['libraries']]
        blobs += manifest['libraries'][0]['members']
        self.parts['cad']['members_sha256'] = identity(sorted(blobs, key=lambda x: x['path']))
        write_json(self.source / 'parts.json', self.parts)
        self.scope['inputs'] = [dict(path='source/' + p.relative_to(self.source).as_posix(),
            at=p.relative_to(self.source).as_posix()) for p in sorted(self.source.rglob('*')) if p.is_file()]
        write_json(self.scope_path, self.scope)
        return manifest

    def test_split_library_copies_are_exact_portable_immutable_and_receipted(self):
        self.split_library()
        receipt = self.build()
        inputs = self.root / 'scaffold/inputs'
        mapping = read_json(inputs / 'project/captured-footprints/manifest.json')
        self.assertEqual(mapping['schema'], 'agent-ee.scaffold-library-copies/1')
        self.assertEqual(len(mapping['copies']), 2)
        self.assertEqual(mapping['manifest'], self.parts['cad']['manifest'])
        for row in mapping['copies']:
            self.assertEqual((inputs / row['path']).read_bytes(), (self.source / row['source']['path']).read_bytes())
            self.assertEqual(digest(inputs / row['path']), row['source']['sha256'])
            self.assertEqual(receipt['outputs']['inputs/' + row['path']], row['source']['sha256'])
            self.assertEqual((inputs / row['source']['path']).read_bytes(), (self.source / row['source']['path']).read_bytes())
        self.assertIn('inputs/project/captured-footprints/manifest.json', receipt['outputs'])
        scope = read_json(self.root / 'scaffold/scope.json')
        self.assertEqual(scope['mutable_inputs'], sorted(self.scope['mutable_inputs']))
        table = parse_sexpr((inputs / 'project/fp-lib-table').read_bytes())
        row = children(table, 'lib')[0]
        self.assertEqual(children(row, 'name')[0][1], 'Owned')
        self.assertEqual(children(row, 'uri')[0][1], '${KIPRJMOD}/captured-footprints/Owned.pretty')
        require_toolchain(native=True)
        # Resolve the generated table URI using native footprint loading after relocation.
        relocated = self.root / 'relocated'
        shutil.copytree(inputs, relocated)
        script = """import pcbnew,sys
from pathlib import Path
from agent_ee.symbol_library import parse_sexpr,children
p=Path(sys.argv[1]); table=parse_sexpr((p/'fp-lib-table').read_bytes())
for lib in children(table,'lib'):
    path=p/children(lib,'uri')[0][1].removeprefix('${KIPRJMOD}/')
    for name in ('R2','PinHeader_1x03'):
        f=pcbnew.FootprintLoad(str(path),name)
        assert f is not None and f.GetPadCount()>0,name
"""
        subprocess.run(['/usr/bin/python3', '-c', script, str(relocated / 'project')], check=True)
        self.assertEqual(receipt, self.build('repeat'))

    def test_split_library_duplicate_name_and_hash_tamper_refuse(self):
        from agent_ee.scaffold import library_tables
        manifest = self.split_library()
        original = copy.deepcopy(manifest)
        member = manifest['libraries'][0]['members'][0]
        duplicate = dict(member, path='another/' + member['path'])
        target = self.source / duplicate['path']; target.parent.mkdir(parents=True)
        target.write_bytes((self.source / member['path']).read_bytes() + b'\n')
        duplicate['sha256'] = digest(target)
        manifest['libraries'][0]['members'].append(duplicate)
        write_json(self.source / 'cad.json', manifest)
        self.parts['cad']['manifest']['sha256'] = digest(self.source / 'cad.json')
        with self.assertRaisesRegex(ValueError, 'duplicate.*footprint'):
            library_tables(self.parts, self.source, 'project')
        write_json(self.source / 'cad.json', original)
        self.parts['cad']['manifest']['sha256'] = digest(self.source / 'cad.json')
        (self.source / member['path']).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'hash'):
            library_tables(self.parts, self.source, 'project')

    def test_split_library_generated_namespace_collision_publishes_nothing(self):
        self.split_library()
        path = self.source / 'project/captured-footprints/Owned.pretty/injected.kicad_mod'
        path.parent.mkdir(parents=True); path.write_bytes(b'preserve me')
        self.scope['inputs'].append(dict(path='source/' + path.relative_to(self.source).as_posix(),
                                         at=path.relative_to(self.source).as_posix()))
        write_json(self.scope_path, self.scope)
        with self.assertRaisesRegex(ValueError, 'collid'):
            self.build()
        self.assertEqual(path.read_bytes(), b'preserve me')
        self.assertFalse((self.root / 'scaffold').exists())

    def test_library_table_cannot_overwrite_unqualified_captured_file(self):
        path = self.source / 'project/fp-lib-table'
        path.write_bytes(b'captured table sentinel')
        self.scope['inputs'].append(dict(path='source/project/fp-lib-table', at='project/fp-lib-table'))
        write_json(self.scope_path, self.scope)
        with self.assertRaisesRegex(ValueError, 'collid'):
            self.build()
        self.assertEqual(path.read_bytes(), b'captured table sentinel')
        self.assertFalse((self.root / 'scaffold').exists())

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
        # Dispatch is scripted, so the codex executable is fingerprinted and
        # preflighted but never launched. A test-owned native ELF and its tool
        # host keep this test independent of a host codex install (the image has none).
        harness = self.root / 'codex-harness'
        harness.mkdir()
        for name in ('codex', 'codex-code-mode-host'):
            shutil.copy2('/usr/bin/true', harness / name)
        self.enterContext(patch.dict(os.environ, {'AGENT_EE_CODEX_BINARY': str(harness / 'codex')}))
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
                    evidence=[dict(path='checks/' + f['local_id'] + '.json', locator='outcome', pointer='/outcome')])
                    for f in result['findings']]
                return dict(execution='ok'), dict(schema='agent-ee.decisions/2', **binding, decisions=decisions)
            self.assertNotIn('"initial_reviews"', prompt)
            return dict(execution='ok'), dict(schema='agent-ee.review/2', **binding,
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
        # The writer's inventory violation is the structured call's invariant: a
        # recorded, retained refusal with no resubmission and no proposal.
        self.assertEqual(calls.count('implementation'), 1)
        self.assertEqual(state['error'], 'ValueError: implementation execution failed; no provider fallback: '
                                         'implementation changed the captured file inventory')
        work = self.root / 'campaign/implementations/1'
        record = read_json(work / 'implementation-execution.json')
        self.assertEqual((record['execution'], record['error'], record['resubmissions_used']),
                         ('refused', 'implementation changed the captured file inventory', 0))
        self.assertEqual([(a['execution'], a['error']) for a in record['attempts']],
                         [('refused', 'implementation changed the captured file inventory')])
        archive = work / record['attempts'][0]['path']
        self.assertEqual(read_json(archive / 'execution.json')['execution'], 'refused')
        self.assertEqual(read_json(archive / 'response.json'), dict(summary='scripted inventory violation'))
        self.assertEqual((archive / 'review-work/implementation/design/project/added.txt').read_text(), 'test refusal')
        self.assertEqual(state['implementation_records'], {'1': digest(work / 'implementation-execution.json')})
        self.assertFalse((work / 'proposal.scope.json').exists())
        self.assertEqual([c['execution'] for c in result['checks']], ['ok', 'ok'])
        self.assertEqual([c['details']['reason'] for c in result['checks']], ['stage_not_reached', 'stage_not_reached'])
        self.assertTrue(result['adjudicated'])
        self.assertTrue((self.root / 'campaign/runs/1/work/verification/evidence.json').is_file())


class EnvelopeScaffoldFixture(ScaffoldFixture):
    """Pod-sized mechanics with original stand-ins, not production part approval.

    J1 stands for AMPSEAL, J2 for SMA, U1 for IMU and U2 for the radio.
    The two-pad radio is authored here; no vendor CAD bytes enter the tree.
    """
    def setUp(self):
        import uuid
        from agent_ee.part_selection import selection_check
        from agent_ee.storage import identity
        from agent_ee.footprint_library import esp32_s3_wroom_1_datasheet_facts
        from test_esp32_footprint_keepout import multipage_text_pdf
        require_toolchain(native=True, tools=('pdftotext',))
        super().setUp()
        pod = ROOT / 'fixtures/telemetry-pod'
        self.envelope = read_json(pod / 'envelope.json')
        self.envelope['obligations'] = []  # Only declared mechanics are this fixture's scope.
        shutil.copytree(pod / 'sources', self.source / 'sources')
        shutil.copyfile(pod / 'fab-profile.json', self.source / 'fab-profile.json')
        self.envelope['mounting_holes'] = [dict(id=f'H{i}', at=[x, y], drill_mm=3.2,
            plated=False, tolerance_mm=0) for i, (x, y) in enumerate(((4,4),(76,4),(76,46),(4,46)),1)]
        self.envelope['fixed_footprints'] = [dict(reference=ref, side=side, edge=edge,
            position_mm=along, inset_mm=inset, rotation_deg=angle, insertion=insertion, tolerance_mm=0)
            for ref, side, edge, along, inset, angle, insertion in (
                ('J1','front','left',30,2,90,'through_edge'),
                ('J2','back','right',15,1,270,'through_edge'),
                ('U1','front','top',50,30,180,'from_top'),
                ('U2','front','top',40,30,90,'from_top'))]
        self.envelope['keepouts'] = [dict(id='fixture-clearance', polygon=[[10,35],[12,35],[12,37],[10,37]],
            layers=['F.Cu','In1.Cu','In2.Cu','B.Cu'],
            forbid=['tracks','vias','pads','copper_pours','footprints'])]
        self.envelope['keepouts'].append(dict(id='board-clearance', polygon=[[60,30],[62,30],[62,32],[60,32]],
            layers=['B.Cu'], forbid=['tracks','vias']))
        self.parts['parts'][0]['instances'] = [dict(sheet='/', reference='U1')]
        self.parts['parts'][1]['instances'] = [dict(sheet='/', reference='J2')]
        radio = copy.deepcopy(self.parts['parts'][0])
        radio.update(id='radio', selection_id='radio', role='owned radio mechanics stand-in',
                     footprint='RF_Module:ESP32-S3-WROOM-1', instances=[dict(sheet='/', reference='U2')])
        self.parts['parts'].append(radio)
        requirements = read_json(self.source / 'selection-requirements.json')
        requirements['parts'].append(dict(requirements['parts'][0], id='radio'))
        write_json(self.source / 'selection-requirements.json', requirements)
        selection = selection_check(self.source / 'snapshot', self.parts['catalog']['manifest_sha256'], requirements)
        self.assertEqual(selection['outcome'], 'pass', selection)
        write_json(self.source / 'selection.json', selection)
        for name in ('requirements', 'result'):
            blob = self.parts['selection'][name]
            blob['sha256'] = digest(self.source / blob['path'])
        data = (self.source / 'Owned.pretty/R2.kicad_mod').read_bytes().replace(b'"R2"', b'"ESP32-S3-WROOM-1"', 1)
        (self.source / 'RF_Module.pretty').mkdir()
        path = 'RF_Module.pretty/ESP32-S3-WROOM-1.kicad_mod'
        (self.source / path).write_bytes(data)
        cad = read_json(self.source / 'cad.json')
        cad['libraries'].append(dict(name='RF_Module', members=[dict(path=path, sha256=digest(self.source / path))]))
        write_json(self.source / 'cad.json', cad)
        assignments = read_json(self.source / 'assignments.json')
        library = self.source / 'Owned.kicad_sym'
        library.write_bytes(library.read_bytes().replace(b'(property "Value"',
            b'(property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)))) (property "Value"'))
        assignments['libraries'][0]['sha256'] = digest(library)
        assignments['components'].append(dict(assignments['components'][0], reference='radio', footprint=radio['footprint']))
        write_json(self.source / 'assignments.json', assignments)
        for name in ('manifest', 'assignments'):
            blob = self.parts['cad'][name]
            blob['sha256'] = digest(self.source / blob['path'])
        members = [m for lib in cad['libraries'] for m in lib['members']]
        self.parts['cad']['members_sha256'] = identity(sorted(
            [*members, dict(path='Owned.kicad_sym', sha256=digest(self.source / 'Owned.kicad_sym'))],
            key=lambda m: m['path']))
        write_json(self.source / 'parts.json', self.parts)
        self.intent = dict(schema='agent-ee.connectivity-intent/1', libraries=assignments['libraries'],
            components=[dict(id=p['id'], reference=p['instances'][0]['reference'], lib_id=p['lib_id'],
                footprint=p['footprint'], value=p['mpn'], units=[1]) for p in self.parts['parts']],
            nets=[], no_connects=[dict(component=p['id'], pin=dict(number=n)) for p in self.parts['parts']
                for n in ('123' if p['id']=='header' else '12')])
        write_json(self.source / 'intent.json', self.intent)
        write_json(self.source / 'layout.json', dict(schema='agent-ee.schematic-layout/1',
            namespace=str(uuid.uuid5(uuid.NAMESPACE_URL, 'owned-ee177')), project='design', transforms={}))
        multipage_text_pdf(self.source / 'owned.pdf',45,45,['18','25.5','6'])
        self.facts = esp32_s3_wroom_1_datasheet_facts(pdf_path='owned.pdf',pdf_sha256=digest(self.source / 'owned.pdf'))
        write_json(self.source / 'facts.json',self.facts)
        self.save()
        self.count = 0

    def save(self):
        write_json(self.source / 'envelope.json',self.envelope)
        self.scope['inputs'] = [dict(path='source/'+p.relative_to(self.source).as_posix(),
            at=p.relative_to(self.source).as_posix()) for p in sorted(self.source.rglob('*')) if p.is_file()]
        write_json(self.scope_path,self.scope)

    def acceptance_scope(self, inputs):
        """Complete scope/3 mechanics checks for the original pod-sized stand-in."""
        from agent_ee.board_envelope import validate, derive_requirements, source_inventory
        record = validate((inputs / 'envelope.json').read_bytes(),
            profile=(inputs / 'fab-profile.json').read_bytes(),captured=source_inventory(inputs))
        requirements = derive_requirements(record,'envelope.json')['requirements']
        version = subprocess.run(['/usr/bin/pdftotext','-v'],capture_output=True,text=True,check=True).stderr.splitlines()[0]
        checks = [dict(id='facts',kind='datasheet_facts',params=dict(facts='facts.json',extractor=dict(
            path='/usr/bin/pdftotext',sha256=digest('/usr/bin/pdftotext'),version=version))),
            dict(id='drc',kind='drc'),
            dict(id='fab',kind='fab_profile',params=dict(fab_profile='fab-profile.json'))]
        for req in requirements:
            field = req['source']['locator'].split('/')[1]
            cid = req['id'].removeprefix('R-envelope-telemetry-pod-').lower()
            if field == 'fab_profile':
                cid = 'fab'
            elif field == 'fixed_footprints':
                ref = record[field][int(req['source']['locator'].split('/')[-1])]['reference']
                checks.append(dict(id=cid,kind='placement',params=dict(
                    criterion='connector-edge/1',envelope='envelope.json',reference=ref,drc_check='drc')))
            else:
                criterion = {'outline':'outline/1','layer_count':'layers/1','stackup':'stackup/1',
                    'mounting_holes':'mounting-holes/1','keepouts':'keepouts/1'}[field]
                params = dict(criterion=criterion,envelope='envelope.json')
                if field in ('mounting_holes','keepouts'):
                    params['id'] = record[field][int(req['source']['locator'].split('/')[-1])]['id']
                checks.append(dict(id=cid,kind='board_envelope',params=params))
            req['checks'] = [cid]
            del req['field_sha256'],req['check_kinds']
        requirements.append(dict(id='R-facts',statement='Verify cited mechanical dimensions.',intent='informational',
            source=dict(path='targets.md',locator='line 1'),checks=['facts']))
        requirements.append(dict(id='native-drc',statement='Native DRC passes.',intent='acceptance',
            source=dict(path='targets.md',locator='line 1'),checks=['drc']))
        scope = read_json(self.root / 'content/scope.json')
        scope.update(schema='agent-ee.scope/3',checks=checks,envelope=dict(path='envelope.json',profile='fab-profile.json',dialog=None),
            baseline=dict(requirements=requirements,targets=['targets.md'],reference_architectures=['reference.md']))
        return scope

    def content(self, name='content', **overrides):
        from agent_ee.scaffold import content_step
        from agent_ee.stage_receipts import run_step, adopt
        self.build(name)
        inputs = self.root / name / 'inputs'
        args = dict(intent='intent.json',layout='layout.json',imu_reference='U1',antenna_facts='facts.json')
        args.update(overrides)
        spec = content_step(ROOT,'envelope.json','parts.json','project/design.kicad_pcb',**args)
        path = self.root / (name+'-receipt')
        receipt = run_step(path,inputs,spec,{'pre_receipt':1})
        if receipt['status']=='completed':
            adopt(path,receipt,inputs)
        return inputs, path, receipt

    def assert_completed(self, path, receipt):
        self.assertEqual(receipt['status'],'completed', (receipt['violations'], (path / 'run/console.log').read_text()))


class EnvelopeScaffoldTests(EnvelopeScaffoldFixture, unittest.TestCase):
    def test_envelope_content(self):
        from agent_ee.forward_annotation import board_facts
        from agent_ee.executor import execute
        from agent_ee.board_envelope import validate
        from agent_ee.envelope_check import hole_verdict, keepout_verdict
        inputs,path,receipt = self.content()
        self.assert_completed(path,receipt)
        pcb = inputs / 'project/design.kicad_pcb'
        native = board_facts(pcb,self.root / 'facts')
        fixed = {fp['reference']:fp for fp in native['footprints'] if fp['path']}
        expected = {'J1':([2000000,20000000],90,'F.Cu'), 'J2':([79000000,15000000],270,'B.Cu'),
                    'U1':([50000000,30000000],180,'F.Cu'), 'U2':([40000000,30000000],90,'F.Cu')}
        for ref,(at,angle,layer) in expected.items():
            self.assertEqual((fixed[ref]['position_nm'],fixed[ref]['orientation_deg']%360,fixed[ref]['layer']),
                             (at,angle,layer))
        record = execute(inputs,self.root / 'probe',['/usr/bin/python3','-B','-m','agent_ee.envelope_probe',
            '/candidate/project/design.kicad_pcb','--out','/work/native.json'])
        self.assertEqual(record['execution'],'ok',record)
        native = read_json(self.root / 'probe/native.json')
        declaration = validate((inputs / 'envelope.json').read_bytes())
        self.assertEqual(len(native['footprints']),8)
        for hole in declaration['mounting_holes']:
            self.assertEqual(hole_verdict(hole,native)['status'],'pass')
        for area in declaration['keepouts']:
            result = keepout_verdict(area,native,self.facts)
            self.assertEqual(result['status'],'pass',result)
        self.assertTrue(all(fp['locked'] for fp in native['footprints']))
        self.assertEqual(len([a for a in native['rule_areas'] if a['owner']=='board']),2)
        parsed = parse_sexpr(pcb.read_bytes())
        holes = [fp for fp in children(parsed,'footprint') if children(fp,'attr') and 'board_only' in children(fp,'attr')[0]]
        self.assertEqual(len(holes),4)
        self.assertTrue(all(not children(fp,'path') for fp in holes))
        text, = children(parsed,'gr_text')
        self.assertIn('IMU_AXES',text[1])
        self.assertIn('X forward  Y left  Z up',text[1])
        self.assertEqual(children(text,'layer')[0][1],'F.SilkS')
        self.assertEqual(children(text,'at')[0][1:3],['50','34'])
        other,other_path,other_receipt = self.content('other')
        self.assert_completed(other_path,other_receipt)
        self.assertEqual(pcb.read_bytes(),(other / 'project/design.kicad_pcb').read_bytes())

    def test_mounting_hole_identities_bind_after_native_reload(self):
        from agent_ee.part_binding import validate_part_bindings
        for plated in (False, True):
            with self.subTest(plated=plated):
                for hole in self.envelope['mounting_holes']:
                    hole['plated'] = plated
                self.save()
                inputs, path, receipt = self.content('plated' if plated else 'bare')
                self.assert_completed(path, receipt)
                board = inputs / 'project/design.kicad_pcb'
                before = board.read_bytes()
                reloaded = self.root / ('pth.kicad_pcb' if plated else 'npth.kicad_pcb')
                subprocess.run(['/usr/bin/python3', '-B', '-c',
                    'import pcbnew,sys; pcbnew.SaveBoard(sys.argv[2],pcbnew.LoadBoard(sys.argv[1]))',
                    str(board), str(reloaded)], check=True, capture_output=True)
                drc_path = self.root / ('pth-drc.json' if plated else 'npth-drc.json')
                subprocess.run(['kicad-cli', 'pcb', 'drc', '--format', 'json',
                    '--output', str(drc_path), str(board)], check=True, capture_output=True)
                self.assertNotIn('lib_footprint_issues',
                    {v['type'] for v in read_json(drc_path)['violations']})
                report = validate_part_bindings(pcb_bytes=reloaded.read_bytes())
                self.assertEqual(report['execution'], 'ok', report)
                holes = {f'H{i}' for i in range(1, 5)}
                self.assertEqual(set(report['board_components']), holes | {'J1', 'J2', 'U1', 'U2'})
                self.assertEqual(set(report['virtual_references']), set() if plated else holes)
                for ref in holes:
                    component = report['board_components'][ref]
                    self.assertEqual(component['lib_id'], 'AgentEE_Mechanical_MountingHole_' +
                                     ('PTH' if plated else 'NPTH'))
                    self.assertEqual(component['has_copper_pads'], plated)
                    if plated:
                        self.assertEqual(report['reference_outcomes'][ref], 'unknown')
                # Reload and binding are read-only with respect to the produced source.
                self.assertEqual(board.read_bytes(), before)
                original = children(parse_sexpr(before), 'footprint')
                saved = children(parse_sexpr(reloaded.read_bytes()), 'footprint')
                def hole_geometry(footprints):
                    return {next(p[2] for p in children(fp, 'property') if p[1] == 'Reference'):
                            [node for node in fp[2:] if isinstance(node, list) and
                             node[0] in ('at', 'pad', 'fp_rect')]
                            for fp in footprints if fp[1].startswith('AgentEE_Mechanical_')}
                self.assertEqual(hole_geometry(original), hole_geometry(saved))
                if not plated:
                    from agent_ee.scaffold import sexpr
                    from agent_ee.schematic_emit import parse_quoted
                    from test_part_binding import build_synthetic_pcb, build_synthetic_sch, valid_r1_props
                    root = parse_quoted(reloaded.read_bytes())
                    root = [node for node in root if not isinstance(node, list) or
                            node[0] != 'footprint' or node[1].startswith('AgentEE_Mechanical_')]
                    bare = validate_part_bindings(pcb_bytes=sexpr(root))
                    self.assertEqual((bare['execution'], bare['outcome']), ('ok', 'info'))
                    self.assertEqual(bare['evaluated_references'], [])
                    props = valid_r1_props()
                    resistor = dict(properties=props, pads=[
                        '(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))'])
                    root.extend(children(parse_quoted(build_synthetic_pcb([resistor])), 'footprint'))
                    mixed = validate_part_bindings(build_synthetic_sch([{'properties': props}]), sexpr(root))
                    self.assertEqual((mixed['execution'], mixed['outcome']), ('ok', 'pass'), mixed)
                    self.assertEqual(mixed['evaluated_references'], ['R1'])
                    self.assertEqual(set(mixed['virtual_references']), holes)

    def test_pad_layers_survive_fresh_process_reload(self):
        """LSET.AllCuMask() is shared native state; a producer that mutates it
        makes KiCad fold *.Mask into *.Cu on save. Only a reload in a new process
        shows the loss, so producer, reload and expectation never share one."""
        inputs,path,receipt = self.content()
        self.assert_completed(path,receipt)
        direct = self.root / 'direct/inputs'
        self.build('direct')
        env = dict(os.environ,PYTHONPATH=str(ROOT / 'src'))
        produce = ("import json,sys,pcbnew\n"
            "from agent_ee.scaffold import write_envelope_content\n"
            "before=list(pcbnew.LSET.AllCuMask().Seq())\n"
            "write_envelope_content(sys.argv[1],'envelope.json','parts.json','project/design.kicad_pcb',"
            "intent_rel='intent.json',layout_rel='layout.json',imu_reference='U1',antenna_facts_rel='facts.json')\n"
            "json.dump(dict(before=before,after=list(pcbnew.LSET.AllCuMask().Seq())),open(sys.argv[2],'w'))\n")
        subprocess.run(['/usr/bin/python3','-B','-c',produce,str(direct),str(self.root / 'mask.json')],
                       env=env,capture_output=True,check=True)
        pcb = inputs / 'project/design.kicad_pcb'
        self.assertEqual((direct / 'project/design.kicad_pcb').read_bytes(),pcb.read_bytes())
        reload = ("import json,sys,pcbnew\n"
            "board=pcbnew.LoadBoard(sys.argv[1])\n"
            "json.dump({fp.GetReference()+'.'+p.GetNumber():sorted(pcbnew.BOARD.GetStandardLayerName(l)"
            " for l in p.GetLayerSet().Seq()) for fp in board.GetFootprints() for p in fp.Pads()},open(sys.argv[2],'w'))\n")
        subprocess.run(['/usr/bin/python3','-B','-c',reload,str(pcb),str(self.root / 'pads.json')],
                       env=env,capture_output=True,check=True)
        copper = ['F.Cu','B.Cu',*[f'In{i}.Cu' for i in range(1,31)]]
        through = sorted([*copper,'F.Mask','B.Mask'])
        # Sorted names coincide with KiCad's serialized order for the SMD sides.
        expected = {**{f'J1.{n}':through for n in '123'},**{f'H{i}.':through for i in range(1,5)},
                    **{f'{ref}.{n}':['F.Cu','F.Mask','F.Paste'] for ref in ('U1','U2') for n in '12'},
                    **{f'J2.{n}':['B.Cu','B.Mask','B.Paste'] for n in '12'}}
        self.assertEqual(read_json(self.root / 'pads.json'),expected)
        serialized = {}
        for fp in children(parse_sexpr(pcb.read_bytes()),'footprint'):
            ref = next(p[2] for p in children(fp,'property') if p[1]=='Reference')
            for pad in children(fp,'pad'):
                serialized[ref+'.'+pad[1]] = children(pad,'layers')[0][1:]
        self.assertEqual(serialized,{key:['*.Cu','*.Mask'] if value is through else value
                                     for key,value in expected.items()})
        mask = read_json(self.root / 'mask.json')
        self.assertEqual(mask['after'],mask['before'])
        self.assertEqual(len(mask['before']),32)

    def test_forward_annotation_retains_fixed(self):
        from agent_ee import forward_annotation as fa
        from agent_ee.executor import execute
        from agent_ee.schematic_emitter import emit_schematic
        inputs,path,receipt = self.content()
        self.assert_completed(path,receipt)
        result,files = emit_schematic(self.intent,{'Owned.kicad_sym':(inputs / 'Owned.kicad_sym').read_bytes()},
                                     read_json(inputs / 'layout.json'))
        self.assertEqual(result['execution'],'ok',result)
        (inputs / 'project/design.kicad_sch').write_bytes(files['design.kicad_sch'])
        record = execute(inputs,self.root / 'xml',['kicad-cli','sch','export','netlist','--format','kicadxml',
            '-o','/work/native.xml','/candidate/project/design.kicad_sch'])
        self.assertEqual(record['execution'],'ok',record)
        board = inputs / 'project/design.kicad_pcb'
        xml = self.root / 'xml/native.xml'
        manifest = read_json(inputs / 'cad.json')
        plan = fa.plan(board,xml,manifest,inputs,self.root / 'annotation-plan')
        self.assertEqual(plan['execution'],'ok',plan)
        self.assertEqual((plan['counts']['retain'],plan['counts']['add'],plan['counts']['remove'],plan['counts']['board_only']),
                         (4,0,0,4))
        out = inputs / 'project/annotated.kicad_pcb'
        result = fa.apply(plan,board,xml,manifest,inputs,out,self.root / 'annotation-apply')
        self.assertEqual(result['execution'],'ok',result)
        before = fa.board_facts(board,self.root / 'before')
        after = fa.board_facts(out,self.root / 'after')
        for key in ('uuid','path','position_nm','orientation_deg','layer'):
            self.assertEqual({fp['reference']:fp[key] for fp in before['footprints']},
                             {fp['reference']:fp[key] for fp in after['footprints']},key)
        self.assertEqual(len(children(parse_sexpr(out.read_bytes()),'gr_text')),1)

    def test_registered_envelope_checks_pass(self):
        import time
        from agent_ee.checks import run_checks
        inputs,path,receipt = self.content()
        self.assert_completed(path,receipt)
        scope = self.acceptance_scope(inputs)
        write_json(self.root / 'content/scope.json',scope)
        run = self.root / 'checks'
        run.mkdir()
        capture(self.root / 'content/scope.json',run)
        results = {r['id']:r for r in run_checks(run,time.monotonic()+300)}
        for cid in ['facts','outline','layer-count','stackup',*[f'hole-h{i}' for i in range(1,5)],
                    'keepout-fixture-clearance','keepout-board-clearance',*[f'fixed-{ref}' for ref in ('j1','j2','u1','u2')]]:
            self.assertEqual((results[cid]['execution'],results[cid]['outcome']),('ok','pass'),results[cid])

    def test_pre_receipt_verifies(self):
        from agent_ee.campaign import run_campaign, verify_campaign
        inputs,path,receipt = self.content()
        self.assert_completed(path,receipt)
        definition = dict(harness='codex',model='test',effort='high',timeout_s=10)
        profile = dict(schema='agent-ee.profile/1',name='scripted-envelope-receipt-test',
            reviewers=[dict(**definition,role='circuit'),dict(**definition,role='physical')],adjudicator=definition)
        config = dict(schema='agent-ee.campaign/1',scope='content/scope.json',review_profile=profile,
            implementation=definition,max_iterations=1,max_seconds=300,pre_receipts=[dict(
                path=str(path / 'receipt.json'),sha256=digest(path / 'receipt.json'),binding={'pre_receipt':1})])
        write_json(self.root / 'campaign.json',config)
        def scripted(run,label,definition,prompt,schema,timeout,**kwargs):
            packet = read_json(Path(run) / 'packet.json')
            binding = {k:packet[k] for k in ('candidate_id','run_id')}
            if label == 'adjudicator':
                result = read_json(Path(run) / 'result.json')
                return dict(execution='ok'),dict(schema='agent-ee.decisions/2',**binding,decisions=[dict(
                    finding_id=f['id'],disposition='accepted',reason='Sealed native fixture violation.',evidence=[dict(
                        path='checks/'+f['local_id']+'.json',locator='outcome',pointer='/outcome')]) for f in result['findings']])
            self.assertNotEqual(label,'implementation')
            return dict(execution='ok'),dict(schema='agent-ee.review/2',**binding,packet_sha256=packet['packet_sha256'],
                role=label,coverage=['receipt protocol'],limitations=['Scripted inference; native tools are real.'],findings=[])
        with patch('agent_ee.reviewers.dispatch',side_effect=scripted),contextlib.redirect_stdout(io.StringIO()):
            run_campaign(self.root / 'campaign.json',self.root / 'campaign')
        state = verify_campaign(self.root / 'campaign')
        self.assertEqual(state['pre_receipts'],{'pre-receipts/1/receipt.json':digest(path / 'receipt.json')})
        first = read_json(self.root / 'campaign/runs/1/candidate.json')
        self.assertEqual(first['files']['project/design.kicad_pcb'],receipt['outputs']['project/design.kicad_pcb'])
        imported = self.root / 'campaign/pre-receipts/1/receipt.json'
        imported.write_bytes(imported.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError,'pre_receipt changed'):
            verify_campaign(self.root / 'campaign')
        # A valid receipt for different bytes cannot bind to run 1.
        board = inputs / 'project/design.kicad_pcb'
        board.write_bytes(board.read_bytes()+b'\n')
        with patch('agent_ee.reviewers.dispatch',side_effect=scripted),contextlib.redirect_stdout(io.StringIO()):
            run_campaign(self.root / 'campaign.json',self.root / 'mismatch-campaign')
        failed = read_json(self.root / 'mismatch-campaign/state.json')
        self.assertIn('pre_receipt output differs from first capture',failed['error'])

    def test_legacy_dynamic_antenna_remains_refused(self):
        # AGENTEE-211 withdrew the former dimension-to-clearance derivation.
        # Generic mechanical/receipt tests above use an ordinary explicit polygon.
        self.envelope['keepouts'][0] = dict(id='antenna',
            antenna=dict(reference='U2',facts_check='facts'),
            layers=['F.Cu','In1.Cu','In2.Cu','B.Cu'],
            forbid=['tracks','vias','pads','copper_pours','footprints'])
        self.save()
        for name,args,message in [('legacy',{},'placement qualification unresolved'),
                                  ('missing-facts',dict(antenna_facts=None),'requires captured antenna facts')]:
            inputs,path,receipt = self.content(name,**args)
            self.assertEqual(receipt['status'],'failed')
            self.assertIn(message,(path / 'run/console.log').read_text())
            self.assertEqual(receipt['inputs']['project/design.kicad_pcb'],
                             digest(path / 'run/design/project/design.kicad_pcb'))

    def test_wrong_identity_and_nonempty_board_are_refused(self):
        from agent_ee.scaffold import content_step
        from agent_ee.stage_receipts import run_step
        inputs,path,receipt = self.content()
        self.assert_completed(path,receipt)
        repeat = self.root / 'repeat-receipt'
        spec = content_step(ROOT,'envelope.json','parts.json','project/design.kicad_pcb',
            intent='intent.json',layout='layout.json',imu_reference='U1',antenna_facts='facts.json')
        refused = run_step(repeat,inputs,spec,{'pre_receipt':1})
        self.assertEqual(refused['status'],'failed')
        self.assertIn('pristine outline scaffold',(repeat / 'run/console.log').read_text())
        self.assertEqual(refused['inputs']['project/design.kicad_pcb'],digest(repeat / 'run/design/project/design.kicad_pcb'))
        self.intent['components'][0]['footprint'] = 'Owned:PinHeader_1x03'
        write_json(self.source / 'intent.json',self.intent)
        self.save()
        inputs,path,receipt = self.content('wrong-cad')
        self.assertEqual(receipt['status'],'failed')
        self.assertIn('CAD identity differs',(path / 'run/console.log').read_text())
        self.assertEqual(receipt['inputs']['project/design.kicad_pcb'],digest(path / 'run/design/project/design.kicad_pcb'))
        (self.source / 'Owned.pretty/R2.kicad_mod').write_bytes(b'not a captured footprint')
        self.save()
        with self.assertRaisesRegex(ValueError,'hash mismatch'):
            self.build('changed-member')
        self.assertFalse((self.root / 'changed-member').exists())

    def test_content_refusals_leave_board_unchanged(self):
        for label,overrides,message in (
            ('missing-layout',dict(layout=None),'require captured intent'),
            ('unknown-imu',dict(imu_reference='U99'),'declared fixed footprint')):
            with self.subTest(label=label):
                inputs,path,receipt = self.content(label,**overrides)
                self.assertEqual(receipt['status'],'failed')
                self.assertIn(message,(path / 'run/console.log').read_text())
                self.assertEqual(receipt['inputs']['project/design.kicad_pcb'],digest(inputs / 'project/design.kicad_pcb'))
                self.assertEqual(receipt['inputs']['project/design.kicad_pcb'],
                                 digest(path / 'run/design/project/design.kicad_pcb'))


if __name__ == '__main__':
    unittest.main()
