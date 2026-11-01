"""Frozen package testimony reaches adjudication without changing candidate inputs."""
import copy
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest import mock

from agent_ee import build_plan, build_stages, qualification, requirement_map, retained_run, spec_record
from agent_ee.candidate import capture
from agent_ee.stage_receipts import tree_identity
from agent_ee.storage import digest, identity, json_bytes, read_json, write_json
from tests.test_build_plan import declaration


class JudgmentContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.build = self.tmp / 'build'
        source = self.build / 'source'
        source.mkdir(parents=True)
        spec = b'# Board\r\n\r\n## Acceptance\r\n\r\n1. Native ERC shall pass.\r\n2. Assess return paths.\r\n'
        record = spec_record.capture(spec, 'TEST', {'Acceptance': 'AC'})
        first, second = record['requirements']
        declaration_map = dict(schema=requirement_map.SCHEMA_2,
            spec_record_sha256=hashlib.sha256(json_bytes(record)).hexdigest(),
            scope=dict(schema='agent-ee.scope/3', name='Synthetic context control', scope='No board acceptance',
                       inputs=[dict(path='.', at='project')], board='board.kicad_pcb', schematic='board.kicad_sch', skip_visuals=True,
                       stages=dict(schema='agent-ee.stages/1', order=['verification'], finding_retry_limit=2,
                                   subjects=dict(verification=dict(kind='symbols', path='board.kicad_sch', references=['R1'])))),
            requirements=[dict(id=first['id'], checks=[dict(id='erc', kind='erc', params={}, stage='verification')]),
                          dict(id=second['id'], judgment=dict(record='return.json'))], prose=[])
        obligations = dict(schema=requirement_map.OBLIGATIONS_SCHEMA,
            spec_sha256=hashlib.sha256(spec).hexdigest(), clauses=[
                dict(id=row['id'], spans=[dict(byte_start=row['locator']['byte_start'],
                    byte_end=row['locator']['byte_end'], text_sha256=row['text_sha256'], obligation=kind)])
                for row, kind in [(first, 'executable'), (second, 'judgment')]])
        (source / 'spec.md').write_bytes(spec)
        for name, value in [('map.json', declaration_map), ('record.json', record), ('obligations.json', obligations),
                            ('envelope.json', dict(stackup=dict(thickness_mm=1.2), layer_count=2,
                                fab_profile=dict(sha256='a' * 64), max_component_height_mm=8))]:
            write_json(source / name, value)
        fixture = Path(__file__).resolve().parents[1] / 'fixtures/rc/control'
        for suffix in ('kicad_pcb', 'kicad_sch', 'kicad_pro'):
            shutil.copyfile(fixture / ('rc_filter.' + suffix), source / ('board.' + suffix))
        for name in ('sym-lib-table', 'fp-lib-table'):
            shutil.copyfile(fixture / name, source / name)
        definition = dict(harness='codex', model='scripted-test', effort='high', timeout_s=30)
        self.profile = dict(schema='agent-ee.profile/1', name='Synthetic context transport',
            reviewers=[dict(definition, role=role) for role in ('circuit', 'physical')], adjudicator=definition)
        write_json(source / 'profile.json', self.profile)
        self.plan = declaration()
        self.plan.update(schema=build_plan.TYPED_SCHEMA, judgments='judgments', review_profile='profile.json')
        self.plan['requirements']['obligations'] = 'obligations.json'
        write_json(self.build / 'build-plan.json', self.plan)
        scope, receipt = requirement_map.compile_map(json_bytes(declaration_map), json_bytes(record), spec,
            files=requirement_map.input_inventory(source), obligations_raw=json_bytes(obligations))
        pins = dict(plan_sha256=digest(self.build / 'build-plan.json'), source=build_plan.source_inventory(source, self.plan), runtime={})
        report = dict(schema=retained_run.REPORT_SCHEMA, inputs=pins, identity=identity(pins),
                      completed=[], attempts=[], status='running', accepted=False)
        for index, name in enumerate(retained_run.STAGES[:-1], 1):
            stage = self.build / 'stages' / f'{index:02d}-{name}'
            candidate = stage / 'candidate'
            shutil.copytree(source, candidate)
            provenance = candidate / requirement_map.PROVENANCE
            provenance.mkdir()
            for capture_name, original in [('spec.md', 'spec.md'), ('spec-record.json', 'record.json'),
                                           ('requirement-map.json', 'map.json'), ('obligations.json', 'obligations.json')]:
                shutil.copyfile(source / original, provenance / capture_name)
            if name == 'requirements':
                write_json(stage / 'accounting.json', dict(requirements=receipt['requirements']))
            if name == 'scaffold':
                write_json(stage / 'requirements.json', receipt)
                write_json(stage / 'product/scope.json', scope)
                self.authored_scope = stage / 'product/scope.json'
            report['completed'].append(dict(stage=name, directory=str(stage.relative_to(self.build)),
                candidate=str(candidate.relative_to(self.build)), tree=tree_identity(stage)))
        report['candidate'] = report['completed'][-1]['candidate']
        self.stage = self.build / 'stages/07-package'
        self.candidate = self.stage / 'candidate'
        shutil.copytree(candidate, self.candidate)
        self.binding = dict(board_sha256=digest(self.candidate / 'board.kicad_pcb'), scope_sha256=digest(self.authored_scope))
        document = dict(schema='agent-ee.engineering-judgment/1', requirement=second['id'],
            span=dict(byte_start=second['locator']['byte_start'], byte_end=second['locator']['byte_end'], text_sha256=second['text_sha256']),
            conclusion='Synthetic source-bound testimony, not a passing check.', assumptions=['No physical qualification.'],
            sources=[dict(kind='engineering', citation=dict(path='spec.md', sha256=digest(source / 'spec.md'),
                quote=spec.decode('utf-8'), locator='Exact synthetic CRLF source'))],
            inputs=[dict(path='spec.md', sha256=digest(source / 'spec.md'))],
            applicability=qualification.applicability_facts(self.candidate, self.plan), candidate=self.binding,
            author=dict(agent='author', model='synthetic', date_utc='2026-10-31T00:00:00Z'), unresolved_defects=[])
        editable = self.tmp / 'editable'
        path = editable / 'judgments/return.json'
        write_json(path, document)
        write_json(editable / 'judgments/verification/return.json', dict(schema='agent-ee.judgment-verification/1',
            judgment_sha256=digest(path), verifier=dict(agent='verifier', model='synthetic', date_utc='2026-10-31T01:00:00Z'),
            checked=list(qualification.WITNESS_CHECKS), result='concur', defects=[]))
        qualification.freeze_judgments(editable, self.candidate, self.plan, self.stage)
        report['attempts'].append(dict(stage='package', directory=str(self.stage.relative_to(self.build)), status='running', identity=report['identity']))
        write_json(self.build / 'build-report.json', report)
        self.run = self.tmp / 'review'
        self.run.mkdir()
        write_json(self.tmp / 'scope.json', build_stages.candidate_scope(scope, self.candidate))
        self.manifest = capture(self.tmp / 'scope.json', self.run, source / 'profile.json')
        self.run_id = 'synthetic-context-run'
        write_json(self.run / 'result.json', dict(candidate_id=self.manifest['candidate_id'], run_id=self.run_id))

    def capture_context(self):
        from agent_ee import judgment_context
        link = judgment_context.capture(self.run,
            judgment_context.Source(self.build, self.stage, self.candidate), self.manifest,
            self.run_id, time.monotonic() + 30)
        result = read_json(self.run / 'result.json')
        write_json(self.run / 'result.json', dict(result, judgment_context=link))
        return judgment_context.read(self.run)

    def test_frozen_testimony_replays_without_the_source_build_and_keeps_candidate_identity(self):
        from agent_ee import judgment_context
        before = (self.run / 'candidate.json').read_bytes()
        context = self.capture_context()
        row, = context['judgments']
        self.assertEqual((row['status'], row['witness']), ('recorded', 'concur'))
        self.assertEqual(row['record']['candidate'], self.binding)
        self.assertEqual((self.run / 'candidate.json').read_bytes(), before)
        self.assertFalse(any('judgments' in name for name in self.manifest['files']))
        shutil.rmtree(self.build)
        self.assertEqual(judgment_context.read(self.run), context)

    def test_only_adjudication_can_read_and_cite_frozen_testimony(self):
        from agent_ee import harnesses, judgment_context, reviewers
        from agent_ee.findings import validate_citation
        self.capture_context()
        packet = reviewers.build_packet(self.run, [], self.run_id)
        before = (self.run / 'packet.json').read_bytes()
        path = 'adjudication-judgments/judgments/return.json'
        citation = dict(path=path, pointer='/conclusion', locator='Frozen synthetic judgment')
        for role in ('circuit', 'physical', 'rf'):
            schema = reviewers.evidence_schema(self.run, packet, role)
            self.assertNotIn(path, str(schema))
            self.assertNotIn((self.run / 'adjudication-judgments', '/evidence/adjudication-judgments'),
                             harnesses.evidence_mounts(self.run, role))
        with self.assertRaises(ValueError):
            validate_citation(citation, self.run, require_structured=True)
        self.assertIn(path, str(reviewers.evidence_schema(self.run, packet, 'adjudicator')))
        self.assertIn((self.run / 'adjudication-judgments', '/evidence/adjudication-judgments'),
                      harnesses.evidence_mounts(self.run, 'adjudicator'))
        self.assertEqual(validate_citation(citation, self.run, require_structured=True,
            adjudication_paths=judgment_context.citation_paths(self.run)), citation)
        self.assertEqual((self.run / 'packet.json').read_bytes(), before)

    def test_context_mutation_during_successful_call_refuses_the_response(self):
        from agent_ee.review_run import structured_call
        self.capture_context()
        def provider(prompt):
            path = self.run / 'adjudication-judgments/judgments/return.json'
            document = read_json(path)
            document['conclusion'] = 'Different testimony after launch'
            write_json(path, document)
            return {'execution': 'ok'}, {'decisions': []}
        record, response = structured_call(self.run, 'adjudicator', 'Synthetic provider boundary', {},
                                            provider, lambda value: value)
        self.assertEqual(record['execution'], 'refused')
        self.assertIsNone(response)
        self.assertIn('inputs changed', record['error'])

    def test_resealed_context_cannot_change_receipt_scope_or_applicability(self):
        from agent_ee import judgment_context
        self.capture_context()
        directory = self.run / judgment_context.DIRECTORY
        original = {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob('*') if p.is_file()}
        for name in ('requirements.json', 'accounting.json', 'scope.json', 'applicability-inventory.json'):
            with self.subTest(name=name):
                for path, raw in original.items():
                    (directory / path).write_bytes(raw)
                doc = read_json(directory / name)
                if name == 'requirements.json':
                    doc['requirements'][1]['mapping']['record'] = 'other.json'
                elif name == 'accounting.json':
                    doc['requirements'][1]['id'] = 'OTHER'
                elif name == 'scope.json':
                    doc['checks'][0]['params'] = {'different_criterion': True}
                else:
                    doc['inputs']['spec.md'] = 'f' * 64
                write_json(directory / name, doc)
                manifest = read_json(directory / 'manifest.json')
                manifest['files'][name] = digest(directory / name)
                write_json(directory / 'manifest.json', manifest)
                result = read_json(self.run / 'result.json')
                result['judgment_context']['sha256'] = digest(directory / 'manifest.json')
                write_json(self.run / 'result.json', result)
                with self.assertRaises(ValueError):
                    judgment_context.read(self.run)

    def test_missing_dissenting_and_duplicate_witnesses_keep_their_admission_status(self):
        from agent_ee import judgment_context
        editable = self.tmp / 'editable'
        witness = editable / 'judgments/verification/return.json'
        original = witness.read_bytes()
        for mode, expected in [('missing', ('pending', 'missing')), ('dissent', ('failed', 'dissent')),
                               ('duplicate', ('failed', 'dissent'))]:
            with self.subTest(mode=mode):
                witness.write_bytes(original)
                if mode == 'missing':
                    witness.unlink()
                elif mode == 'dissent':
                    doc = read_json(witness)
                    doc['result'] = 'dissent'
                    write_json(witness, doc)
                else:
                    witness.with_name('duplicate.json').write_bytes(original)
                shutil.rmtree(self.stage / 'judgments')
                qualification.freeze_judgments(editable, self.candidate, self.plan, self.stage)
                context_dir = self.run / judgment_context.DIRECTORY
                if context_dir.exists():
                    shutil.rmtree(context_dir)
                write_json(self.run / 'result.json', dict(candidate_id=self.manifest['candidate_id'], run_id=self.run_id))
                row, = self.capture_context()['judgments']
                self.assertEqual((row['status'], row['witness']), expected)

    def test_capture_refuses_changed_nonboard_input_expiry_and_unbounded_sources(self):
        from agent_ee import executor, judgment_context, native_run
        source = judgment_context.Source(self.build, self.stage, self.candidate)
        with self.assertRaises(native_run.DeadlineExhausted):
            judgment_context.capture(self.run, source, self.manifest, self.run_id, time.monotonic() - 1)
        cancelled = executor.Cancellation()
        cancelled.cancel()
        with native_run.using(cancel=cancelled), self.assertRaises(native_run.DeadlineExhausted):
            judgment_context.capture(self.run, source, self.manifest, self.run_id, time.monotonic() + 30)
        self.assertFalse((self.run / judgment_context.DIRECTORY).exists())
        path = self.candidate / 'envelope.json'
        original = path.read_bytes()
        path.write_bytes(original + b'\n')
        with self.assertRaises(ValueError):
            judgment_context.capture(self.run, source, self.manifest, self.run_id, time.monotonic() + 30)
        path.write_bytes(original)
        directory = self.stage / 'judgments'
        for mode in ('symlink', 'oversized', 'directories'):
            with self.subTest(mode=mode):
                extra = directory / 'extra'
                if mode == 'symlink':
                    extra.symlink_to(directory / 'return.json')
                elif mode == 'oversized':
                    with extra.open('wb') as stream:
                        stream.truncate(judgment_context.MAX_BYTES + 1)
                else:
                    extra.mkdir()
                    for index in range(judgment_context.MAX_ENTRIES + 1):
                        (extra / str(index)).mkdir()
                try:
                    with self.assertRaises(ValueError):
                        judgment_context.capture(self.run, source, self.manifest, self.run_id, time.monotonic() + 30)
                    self.assertFalse((self.run / judgment_context.DIRECTORY).exists())
                finally:
                    if mode == 'directories':
                        shutil.rmtree(extra)
                    else:
                        extra.unlink()

    def test_inventory_cancellation_during_hashing_releases_held_descriptors(self):
        from agent_ee import stage_receipts
        directory = self.tmp / 'inventory'
        directory.mkdir()
        with (directory / 'large').open('wb') as stream:
            stream.truncate(16 * 1024 * 1024)
        for inventory in (stage_receipts.file_tree, stage_receipts.tree_identity):
            with self.subTest(inventory=inventory.__name__):
                remaining = 4
                def cancel_during_scan():
                    nonlocal remaining
                    remaining -= 1
                    if remaining == 0:
                        raise InterruptedError('cancelled inventory')
                descriptors = len(os.listdir('/proc/self/fd'))
                with self.assertRaisesRegex(InterruptedError, 'cancelled inventory'):
                    inventory(directory, checkpoint=cancel_during_scan)
                self.assertEqual(len(os.listdir('/proc/self/fd')), descriptors)


class NativeJudgmentContextTests(unittest.TestCase):
    setUp = JudgmentContextTests.setUp

    def test_package_review_transports_testimony_and_preserves_independent_deferral(self):
        from agent_ee import executor, harnesses, judgment_context, preflight, run_record
        from tests.toolchain import require_toolchain
        require_toolchain(native=True)
        script = '''import json, sys
from pathlib import Path
request = json.loads(sys.stdin.read())
path = Path('/evidence/adjudication-judgments/judgments/return.json')
if request['role'] == 'adjudicator':
    assert request['context_pointer_supplied']
    assert json.loads(path.read_text())['conclusion'].startswith('Synthetic source-bound')
    try:
        path.write_text('not writable')
    except OSError:
        pass
    else:
        raise AssertionError('context writable')
else:
    assert not path.exists()
print(json.dumps(request['response']))
'''
        observed = []
        def provider(run, role, definition, prompt, schema, timeout, *, cancellation=None, executable=None):
            packet = read_json(Path(run) / 'packet.json')
            if role == 'adjudicator':
                native_decisions = [dict(finding_id=row['id'], disposition='accepted',
                    reason='Retain the actual fixture native violation.',
                    evidence=[dict(path='checks/' + row['local_id'] + '.json', pointer='/summary',
                                   locator='Executed fixture check')])
                    for row in read_json(Path(run) / 'result.json')['findings'] if row['source'] == 'check']
                response = dict(schema='agent-ee.decisions/2', candidate_id=packet['candidate_id'],
                    run_id=packet['run_id'], decisions=[*native_decisions, dict(finding_id='circuit/return', disposition='deferred',
                        reason='Concurrence does not establish the missing physical evidence.',
                        evidence=[dict(path='adjudication-judgments/judgments/return.json', pointer='/conclusion',
                                       locator='Frozen testimony still excludes physical qualification')])])
            else:
                finding = dict(id='return', title='Physical evidence remains absent', location='Synthetic fixture',
                    condition='Physical operation', mechanism='No physical measurement', severity='major',
                    assessment='concern', evidence=[dict(path='inputs/spec.md', lines=[6, 6], locator='Return-path requirement')],
                    reasoning='This source-bound fixture cannot establish physical performance.')
                response = dict(schema='agent-ee.review/2', role=role, coverage=['Synthetic provider transport'],
                    limitations=[], findings=[finding] if role == 'circuit' else [],
                    **{key: packet[key] for key in ('candidate_id', 'run_id', 'packet_sha256')})
            work = Path(run) / 'review-work' / role
            execution = executor.execute(Path(run) / 'inputs', work, ['/usr/bin/python3', '-c', script],
                timeout=timeout, cancellation=cancellation, extra_ro=harnesses.evidence_mounts(run, role),
                stdin=json.dumps(dict(role=role, response=response,
                    context_pointer_supplied='/evidence/adjudication-judgments/' in prompt)))
            observed.append((role, execution['execution']))
            return execution, json.loads((work / 'stdout.log').read_text()) if execution['execution'] == 'ok' else None
        runtime = {name: dict(path='/usr/bin/true', sha256=digest('/usr/bin/true'))
                   for name in ('codex', 'claude', 'codex_tool_host', 'bwrap', '/usr/bin/python3', 'kicad-cli')}
        scope = build_stages.candidate_scope(read_json(self.authored_scope), self.candidate)
        with mock.patch.object(run_record, 'runtime_files', lambda **kw: copy.deepcopy(runtime)), \
                mock.patch.object(preflight, 'runtime_files', lambda **kw: copy.deepcopy(runtime)), \
                mock.patch('agent_ee.reviewers.dispatch', side_effect=provider), \
                retained_run.review_resubmissions(0), contextlib.redirect_stdout(io.StringIO()):
            result = retained_run.review_candidate(scope, self.stage, 120,
                profile=str(self.build / 'source/profile.json'),
                judgment_context=judgment_context.Source(self.build, self.stage, self.candidate))
            replay = run_record.verify(self.stage / 'review', current_source=False)
        self.assertTrue(all(execution == 'ok' for _, execution in observed), observed)
        self.assertEqual(result, replay)
        self.assertTrue(result['adjudicated'], dict(error=result.get('error'),
            checks=[{key: row[key] for key in ('id', 'execution', 'outcome', 'summary')} for row in result['checks']],
            adjudicator=read_json(self.stage / 'review/adjudicator-execution.json').get('error')))
        self.assertEqual(result['outcome'], 'defects_or_unresolved')
        self.assertEqual(next(row for row in result['findings'] if row['id'] == 'circuit/return')['disposition'], 'deferred')
        self.assertTrue(all(row['disposition'] == 'accepted' for row in result['findings'] if row['source'] == 'check'))
        self.assertEqual(judgment_context.read(self.stage / 'review')['judgments'][0]['status'], 'recorded')
        # A new artifact seal cannot erase the successful calls' context binding.
        run = self.stage / 'review'
        shutil.rmtree(run / judgment_context.DIRECTORY)
        del result['judgment_context']
        run_record.save_result(run, result)
        with mock.patch.object(run_record, 'runtime_files', lambda **kw: copy.deepcopy(runtime)), \
                mock.patch.object(preflight, 'runtime_files', lambda **kw: copy.deepcopy(runtime)), \
                self.assertRaisesRegex(ValueError, 'judgment context'):
            run_record.verify(run, current_source=False)
