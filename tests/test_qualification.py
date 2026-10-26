"""Independent build claims and custody of sealed stage evidence (AGENTEE-521)."""
import copy
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from agent_ee import build_workflow as build, qualification, retained_run
from agent_ee.stage_receipts import tree_identity
from agent_ee.storage import digest, identity, read_json, write_json



class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        source = self.root / 'source'
        source.mkdir()
        (source / 'spec.md').write_text('Synthetic custody control; no board release.\n')
        stage = self.root / 'stages/01-requirements'
        candidate = stage / 'candidate'
        candidate.mkdir(parents=True)
        write_json(stage / 'scope.json', dict(checks=[]))
        write_json(stage / 'accounting.json', dict(requirements=[dict(id='R1', mode='checks',
            locator=dict(byte_start=0, byte_end=10), mapping=[dict(id='erc', kind='erc')])]))
        pin = dict(plan_sha256='a' * 64, source=build._inventory(source), runtime={})
        self.report = dict(schema=build.REPORT_SCHEMA, inputs=pin, identity=identity(pin),
            completed=[dict(stage='requirements', directory=str(stage.relative_to(self.root)),
                            candidate=str(candidate.relative_to(self.root)), tree=tree_identity(stage))],
            attempts=[], candidate=str(candidate.relative_to(self.root)), status='checkpoint', accepted=False)
        self.save()

    def save(self):
        write_json(self.root / 'build-report.json', self.report)

    def test_checkpoint_never_implies_checks_review_or_file_release(self):
        before = tree_identity(self.root / 'stages')
        result = qualification.assess(self.root)
        self.assertEqual(result['mode'], 'legacy')
        self.assertTrue(result['claims_withheld'])
        self.assertEqual(result['coverage']['R1']['status'], 'unknown')
        for dimension in ('checks', 'file_release', 'engineering_review', 'operational', 'judgments'):
            self.assertEqual(result['dimensions'][dimension]['status'], 'not_assessed')
        for dimension in ('supplier', 'physical'):
            self.assertEqual(result['dimensions'][dimension]['status'], 'pending')
        self.assertFalse(result['accepted'])
        self.assertEqual(before, tree_identity(self.root / 'stages'))
        self.assertEqual(read_json(self.root / 'qualification.json'), result)

    def test_altered_or_unsealed_stage_never_produces_qualification(self):
        (self.root / self.report['completed'][0]['directory'] / 'scope.json').write_text('{}')
        with self.assertRaisesRegex(qualification.QualificationError, 'stage identity changed'):
            qualification.assess(self.root)
        self.assertFalse((self.root / 'qualification.json').exists())

    def complete_control(self, *, typed=False):
        accounting = read_json(self.root / self.report['completed'][0]['directory'] / 'accounting.json')
        if typed:
            row = accounting['requirements'][0]
            text_hash = hashlib.sha256((self.root / 'source/spec.md').read_bytes()[:10]).hexdigest()
            row.update(obligation='executable', bound_ids=['erc'], text_sha256=text_hash,
                       obligation_text_sha256=text_hash)
            first = self.report['completed'][0]
            write_json(self.root / first['directory'] / 'accounting.json', accounting)
            first['tree'] = tree_identity(self.root / first['directory'])
            plan = dict(schema=build.TYPED_SCHEMA, source='inputs',
                requirements=dict(map='map.json', record='record.json', spec='spec.md', obligations='obligations.json'),
                parts=dict(manifest='parts.json', circuit='circuit.json', layout='layout.json'),
                envelope='envelope.json', placement=dict(intent='intent.json', groups='groups.json',
                    budget_s=10, search={}, router={}))
            write_json(self.root / 'build-plan.json', plan)
            for name in ('map.json', 'record.json', 'obligations.json'):
                write_json(self.root / 'source' / name, {})
            self.report['inputs']['plan_sha256'] = digest(self.root / 'build-plan.json')
            self.report['inputs']['source'] = build._inventory(self.root / 'source')
            self.report['identity'] = identity(self.report['inputs'])
        for index, stage in enumerate(build.STAGES[1:], 2):
            directory = self.root / 'stages' / f'{index:02d}-{stage}'
            candidate = directory / 'candidate'
            candidate.mkdir(parents=True)
            if stage == 'scaffold':
                receipt = dict(schema='agent-ee.requirement-map-receipt/' + ('2' if typed else '1'),
                               requirements=accounting['requirements'])
                if typed:
                    receipt.update({key: digest(self.root / 'source' / plan['requirements'][name]) for name, key in (
                        ('map', 'map_sha256'), ('record', 'spec_record_sha256'), ('spec', 'spec_sha256'),
                        ('obligations', 'obligations_sha256'))})
                write_json(directory / 'requirements.json', receipt)
            if stage == 'package':
                extra = {}
                if typed:
                    (directory / 'judgments').mkdir()
                    shutil.copyfile(self.root / 'source/spec.md', candidate / 'spec.md')
                    write_json(candidate / 'envelope.json', dict(stackup=dict(thickness_mm=1.2),
                        layer_count=2, fab_profile=dict(sha256='a' * 64), max_component_height_mm=8))
                    write_json(directory / 'judgment-inventory.json', {})
                    write_json(directory / 'applicability-inventory.json', dict(inputs=build._inventory(candidate),
                        values=qualification.applicability_facts(candidate, plan)))
                    extra['judgment_inventory_sha256'] = digest(directory / 'judgment-inventory.json')
                write_json(directory / 'accepted-package.json', dict(artifacts={}, native_files={},
                    physical_validation_performed=False, supplier_preview_performed=False, **extra))
            self.report['completed'].append(dict(stage=stage, directory=str(directory.relative_to(self.root)),
                candidate=str(candidate.relative_to(self.root)), tree=tree_identity(directory)))
        self.report.update(status='assessing', accepted=False, candidate=self.report['completed'][-1]['candidate'])
        self.save()

    def test_seven_sealed_directories_and_valid_package_json_cannot_grant_release(self):
        self.complete_control()
        result = qualification.assess(self.root)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['dimensions']['file_release']['status'], 'pending')
        self.assertEqual(result['dimensions']['checks']['status'], 'not_assessed')
        self.assertEqual(result['dimensions']['engineering_review']['status'], 'not_assessed')

    def test_operational_resume_criterion_uses_its_own_journal_and_verified_receipts(self):
        # This is a journal/custody control; it deliberately has no check or file-release evidence.
        self.complete_control(typed=True)
        result = qualification.assess(self.root)
        self.assertEqual(result['dimensions']['operational']['status'], 'pending')
        directory = self.report['completed'][1]['directory']
        self.report['attempts'] = [dict(stage='parts', directory='failed-parts', status='interrupted', identity=self.report['identity']),
                                   dict(stage='parts', directory=directory, status='completed', identity=self.report['identity'])]
        self.save()
        result = qualification.assess(self.root)
        self.assertEqual(result['dimensions']['operational']['status'], 'pass')
        self.assertFalse(result['accepted'])
        self.report['attempts'][1]['identity'] = 'changed identity'
        self.save()
        self.assertEqual(qualification.assess(self.root)['dimensions']['operational']['status'], 'pending')

    def test_recorded_judgment_requires_bound_concurrence_and_remains_separate_from_review(self):
        self.complete_control(typed=True)
        scaffold = self.report['completed'][2]
        receipt_path = self.root / scaffold['directory'] / 'requirements.json'
        receipt = read_json(receipt_path)
        entry = receipt['requirements'][0]
        entry.update(mode='judgment', obligation='judgment', bound_ids=[], mapping=dict(record='decision.json'))
        write_json(receipt_path, receipt)
        scaffold['tree'] = tree_identity(receipt_path.parent)
        first = self.report['completed'][0]
        write_json(self.root / first['directory'] / 'accounting.json', dict(requirements=receipt['requirements']))
        first['tree'] = tree_identity(self.root / first['directory'])
        package = self.report['completed'][-1]
        stage = self.root / package['directory']
        spec = (self.root / package['candidate'] / 'spec.md').read_text()
        pin = hashlib.sha256(spec.encode()).hexdigest()
        document = dict(schema='agent-ee.engineering-judgment/1', requirement='R1',
            span=dict(byte_start=0, byte_end=10, text_sha256=entry['obligation_text_sha256']),
            conclusion='This is a source-bound synthetic admission control.', assumptions=[],
            sources=[dict(kind='engineering', citation=dict(path='spec.md', sha256=pin, quote=spec, locator='control'))],
            inputs=[dict(path='spec.md', sha256=pin)], applicability=dict(board_thickness_mm=1.2), candidate=None,
            author=dict(agent='author', model='synthetic', date_utc='2026-10-25T00:00:00Z'), unresolved_defects=[])
        path = stage / 'judgments/decision.json'
        write_json(path, document)
        witness = dict(schema='agent-ee.judgment-verification/1', judgment_sha256=digest(path),
            verifier=dict(agent='independent', model='synthetic', date_utc='2026-10-25T01:00:00Z'),
            checked=list(qualification.WITNESS_CHECKS), result='concur', defects=[])
        witness_path = stage / 'judgments/verification/witness.json'
        write_json(witness_path, witness)
        for expected in ('pass', 'failed'):
            write_json(stage / 'judgment-inventory.json', build._inventory(stage / 'judgments'))
            marker = read_json(stage / 'accepted-package.json')
            marker['judgment_inventory_sha256'] = digest(stage / 'judgment-inventory.json')
            write_json(stage / 'accepted-package.json', marker)
            package['tree'] = tree_identity(stage)
            self.save()
            result = qualification.assess(self.root)
            self.assertEqual(result['dimensions']['judgments']['status'], expected, result)
            self.assertEqual(result['dimensions']['file_release']['status'],
                             'pending' if expected == 'pass' else 'failed', result)
            self.assertEqual(result['dimensions']['engineering_review']['status'], 'not_assessed')
            self.assertEqual(result['dimensions']['operational']['status'], 'pending')
            self.assertFalse(result['accepted'])
            witness['result'] = 'dissent'
            write_json(witness_path, witness)

    def test_typed_receipt_cannot_omit_or_relabel_a_source_span(self):
        self.complete_control(typed=True)
        scaffold = self.report['completed'][2]
        path = self.root / scaffold['directory'] / 'requirements.json'
        original = read_json(path)
        for mutation in ('omit', 'mode', 'span_hash', 'input_hash'):
            receipt = copy.deepcopy(original)
            if mutation == 'omit': receipt['requirements'] = []
            if mutation == 'mode': receipt['requirements'][0]['mode'] = 'judgment'
            if mutation == 'span_hash': receipt['requirements'][0]['obligation_text_sha256'] = '0' * 64
            if mutation == 'input_hash': receipt['map_sha256'] = '0' * 64
            write_json(path, receipt)
            scaffold['tree'] = tree_identity(path.parent)
            self.save()
            with self.subTest(mutation=mutation), self.assertRaisesRegex(qualification.QualificationError, 'receipt differs'):
                qualification.assess(self.root)


class BuildQualificationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source = self.root / 'inputs'
        self.source.mkdir()
        fixture = Path(__file__).resolve().parents[1] / 'fixtures/telemetry-pod'
        for name in ('spec.md', 'spec-record.json', 'requirement-map.json'):
            shutil.copyfile(fixture / name, self.source / name)
        write_json(self.source / 'intent.json', {})
        write_json(self.source / 'groups.json', {'groups': []})
        self.plan = dict(schema=build.SCHEMA, source='inputs',
            requirements=dict(map='requirement-map.json', record='spec-record.json', spec='spec.md'),
            parts=dict(manifest='parts.json', circuit='circuit.json', layout='layout.json'),
            envelope='envelope.json', placement=dict(intent='intent.json', groups='groups.json',
                budget_s=10, search={}, router={}))
        self.plan_path = self.root / 'build.json'
        write_json(self.plan_path, self.plan)
        self.out = self.root / 'run'

    def test_public_checkpoint_writes_independent_qualification_and_resumes_assessment(self):
        result = build.run(self.plan_path, self.out, through='requirements')
        self.assertEqual(result['status'], 'checkpoint', result)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['qualification']['dimensions']['checks'], 'not_assessed')
        self.assertEqual((self.out / 'build-plan.json').read_bytes(), self.plan_path.read_bytes())
        self.assertEqual(result['inputs']['plan_sha256'], digest(self.out / 'build-plan.json'))
        frozen = tree_identity(self.out / 'stages')
        (self.out / 'qualification.json').write_text('{"accepted":true}')
        result = build.run(self.plan_path, self.out, through='requirements', resume=True)
        self.assertEqual(result['status'], 'checkpoint')
        self.assertFalse(read_json(self.out / 'qualification.json')['accepted'])
        self.assertEqual(frozen, tree_identity(self.out / 'stages'))

    def test_cancelled_build_resumes_on_the_existing_retained_directory(self):
        _, _, plan_raw, plan, source, files = build.preflight_build(self.plan_path, self.out)
        self.out.mkdir()
        pin = dict(plan_sha256=hashlib.sha256(plan_raw).hexdigest(), source=files, runtime=build._runtime())
        retained_run.initialize(self.out, pin)
        (self.out / 'build-plan.json').write_bytes(plan_raw)
        retained_run.capture_source(self.out, source, plan, files)
        directory_inode = self.out.stat().st_ino
        (self.out / 'CANCEL').touch()
        cancelled = build.run(self.plan_path, self.out, through='requirements', resume=True)
        self.assertEqual(cancelled['status'], 'interrupted', cancelled)
        self.assertEqual(cancelled['completed'], [])
        self.assertFalse((self.out / 'stages').exists())
        (self.out / 'CANCEL').unlink()
        resumed = build.run(self.plan_path, self.out, through='requirements', resume=True)
        self.assertEqual(resumed['status'], 'checkpoint', resumed)
        self.assertEqual([row.name for row in retained_run.completed(self.out)], ['requirements'])
        self.assertTrue(retained_run.accounting(self.out)['requirements'])
        self.assertEqual(self.out.stat().st_ino, directory_inode)
        self.assertFalse(resumed['accepted'])

    def typed(self):
        declaration = read_json(self.source / 'requirement-map.json')
        declaration['schema'] = 'agent-ee.requirement-map/2'
        record = read_json(self.source / 'spec-record.json')
        spec = (self.source / 'spec.md').read_bytes()
        clauses = []
        for group in ('requirements', 'prose'):
            mapping = {row['id']: row for row in declaration[group]}
            for row in record['requirements' if group == 'requirements' else 'prose']:
                kind = ('context' if 'context' in mapping[row['id']] else
                        'physical' if 'bench' in mapping[row['id']] else 'executable')
                start, end = (row['locator'][key] for key in ('byte_start', 'byte_end'))
                clauses.append(dict(id=row['id'], spans=[dict(byte_start=start, byte_end=end,
                    text_sha256=hashlib.sha256(spec[start:end]).hexdigest(), obligation=kind)]))
        write_json(self.source / 'requirement-map.json', declaration)
        write_json(self.source / 'obligations.json', dict(schema='agent-ee.obligations/1',
            spec_sha256=hashlib.sha256(spec).hexdigest(), clauses=clauses))
        self.plan['schema'] = build.TYPED_SCHEMA
        self.plan['requirements']['obligations'] = 'obligations.json'
        self.plan['judgments'] = 'judgments'
        write_json(self.plan_path, self.plan)

    def test_typed_checkpoint_excludes_editable_judgments_from_every_receipted_copy(self):
        self.typed()
        directory = self.source / 'judgments'
        directory.mkdir()
        (directory / 'draft.json').write_text('draft not yet offered for package admission')
        os.mkfifo(directory / 'unfinished-draft')  # Must not be opened or validated before package freeze.
        result = build.run(self.plan_path, self.out, through='requirements')
        self.assertEqual(result['status'], 'checkpoint', result)
        self.assertEqual(result['qualification']['mode'], 'typed')
        self.assertFalse((self.out / 'source/judgments').exists())
        self.assertFalse(any(path.is_file() for path in (self.out / 'stages').rglob('draft.json')))
        before = tree_identity(self.out / 'stages')
        (directory / 'draft.json').write_text('a revision before the first package freeze')
        result = build.run(self.plan_path, self.out, through='requirements', resume=True)
        self.assertEqual(result['status'], 'checkpoint', result)
        self.assertEqual(before, tree_identity(self.out / 'stages'))

    def test_changed_live_source_refuses_without_rewriting_retained_records(self):
        build.run(self.plan_path, self.out, through='requirements')
        before = tree_identity(self.out / 'stages')
        records = {name: (self.out / name).read_bytes() for name in ('build-report.json', 'qualification.json')}
        with (self.source / 'spec.md').open('a') as stream:
            stream.write('\nchanged input\n')
        with self.assertRaisesRegex(ValueError, 'resume identity differs'):
            build.run(self.plan_path, self.out, through='requirements', resume=True)
        self.assertEqual(before, tree_identity(self.out / 'stages'))
        self.assertEqual(records, {name: (self.out / name).read_bytes() for name in records})
        refusals = list((self.out / 'resume-refusals').glob('*.json'))
        self.assertEqual(len(refusals), 1)
        refusal = read_json(refusals[0])
        self.assertEqual(refusal['kind'], 'invocation_identity')
        self.assertNotEqual(refusal['attempted_identity'], refusal['retained_identity'])
        self.assertEqual(refusal['retained']['build-report.json'], hashlib.sha256(records['build-report.json']).hexdigest())

    def test_judgment_change_after_a_failed_package_freeze_requires_a_new_run(self):
        self.typed()
        write_json(self.source / 'envelope.json', dict(stackup=dict(thickness_mm=1.2), layer_count=2,
            fab_profile=dict(sha256='a' * 64), max_component_height_mm=8))
        write_json(self.source / 'judgments/record.json', {'draft': 'frozen authored bytes'})
        report = build.run(self.plan_path, self.out, through='requirements')
        self.assertEqual(report['status'], 'checkpoint', report)
        stage = self.out / 'stages/07-package-failed'
        stage.mkdir()
        qualification.freeze_judgments(self.source, self.out / report['candidate'], self.plan, stage)
        report['judgment_inventory'] = dict(path=str((stage / 'judgment-inventory.json').relative_to(self.out)),
                                            sha256=digest(stage / 'judgment-inventory.json'))
        report['attempts'].append(dict(stage='package', status='failed', directory=str(stage.relative_to(self.out)),
                                       identity=report['identity']))
        write_json(self.out / 'build-report.json', report)
        frozen = tree_identity(self.out / 'stages')
        records = {name: (self.out / name).read_bytes() for name in ('build-report.json', 'qualification.json')}
        write_json(self.source / 'judgments/record.json', {'draft': 'changed after freeze'})
        with self.assertRaisesRegex(ValueError, 'judgment inputs changed after package'):
            build.run(self.plan_path, self.out, through='requirements', resume=True)
        self.assertEqual(frozen, tree_identity(self.out / 'stages'))
        self.assertEqual(records, {name: (self.out / name).read_bytes() for name in records})
        refusal, = (self.out / 'resume-refusals').glob('*.json')
        self.assertEqual(read_json(refusal)['kind'], 'judgment_inputs')
        self.assertIn('judgments_sha256', read_json(refusal)['attempted'])

    def test_plan_and_real_runtime_changes_refuse_with_nonzero_cli_without_rewriting_records(self):
        build.run(self.plan_path, self.out, through='requirements')
        before = {name: (self.out / name).read_bytes() for name in ('build-report.json', 'qualification.json')}
        original_plan = self.plan_path.read_bytes()
        command = [sys.executable, '-m', 'agent_ee.cli', 'build', 'run', str(self.plan_path),
                   '--out', str(self.out), '--resume', '--through', 'requirements']
        self.plan['verification_timeout_s'] = 30
        write_json(self.plan_path, self.plan)
        changed_plan = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(changed_plan.returncode, 0, changed_plan.stdout)
        self.assertIn('resume identity differs', changed_plan.stderr)
        self.assertEqual(before, {name: (self.out / name).read_bytes() for name in before})
        self.plan_path.write_bytes(original_plan)
        # Execute a second real implementation with one inert source-byte change;
        # runtime identity is observed by the CLI, not supplied by a patched helper.
        runtime_source = self.root / 'other-runtime'
        shutil.copytree(Path(build.__file__).parent, runtime_source / 'agent_ee')
        with (runtime_source / 'agent_ee/build_workflow.py').open('a') as stream:
            stream.write('\n# Runtime identity regression control.\n')
        changed_runtime = subprocess.run(command, env=dict(os.environ, PYTHONPATH=str(runtime_source)),
                                         capture_output=True, text=True, timeout=30)
        self.assertNotEqual(changed_runtime.returncode, 0, changed_runtime.stdout)
        self.assertIn('resume identity differs', changed_runtime.stderr)
        self.assertEqual(before, {name: (self.out / name).read_bytes() for name in before})
        refusals = [read_json(p) for p in (self.out / 'resume-refusals').glob('*.json')]
        self.assertEqual(len(refusals), 2)
        self.assertTrue(all(r['kind'] == 'invocation_identity' for r in refusals))

    def test_retained_corruption_withdraws_claim_and_preserves_exact_superseded_records(self):
        report = build.run(self.plan_path, self.out, through='requirements')
        before = {name: (self.out / name).read_bytes() for name in ('build-report.json', 'qualification.json')}
        scope = self.out / report['completed'][0]['directory'] / 'scope.json'
        scope.write_bytes(scope.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'stage identity changed'):
            build.run(self.plan_path, self.out, through='requirements', resume=True)
        refused = read_json(self.out / 'build-report.json')
        self.assertFalse(refused['accepted'])
        self.assertEqual(refused['qualification']['dimensions']['operational'], 'failed')
        archived = self.out / refused['supersession']['path']
        self.assertEqual(before, {name: (archived / name).read_bytes() for name in before})
        self.assertEqual(refused['completed'], report['completed'])
        self.assertEqual(refused['attempts'], report['attempts'])

    def test_missing_historical_plan_refuses_migration_without_publishing_new_claims(self):
        report = build.run(self.plan_path, self.out, through='requirements')
        report.pop('qualification')
        write_json(self.out / 'build-report.json', report)
        (self.out / 'build-plan.json').unlink()
        (self.out / 'qualification.json').unlink()
        before = (self.out / 'build-report.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'historical build.*migration is unsupported'):
            build.run(self.plan_path, self.out, through='requirements', resume=True)
        self.assertEqual(before, (self.out / 'build-report.json').read_bytes())
        self.assertFalse((self.out / 'qualification.json').exists())
        self.assertFalse((self.out / 'build-plan.json').exists())
        refusal, = (self.out / 'resume-refusals').glob('*.json')
        self.assertEqual(read_json(refusal)['kind'], 'historical_migration')

    def test_judgments_cannot_hide_an_earlier_input_or_generated_namespace(self):
        self.typed()
        for directory, plane in [('.', None), ('build-generated', None), ('requirements-map/subdir', None),
                                  ('judgments', 'judgments/planes.json')]:
            plan = copy.deepcopy(self.plan)
            plan['judgments'] = directory
            if plane:
                plan['placement']['planes'] = plane
            with self.subTest(directory=directory, plane=plane), self.assertRaisesRegex(ValueError, 'judgments'):
                build.validate_plan(plan)

    def test_assessing_crash_with_all_stages_resumes_assessment_without_reexecuting_producers(self):
        report = build.run(self.plan_path, self.out, through='requirements')
        accounting = read_json(self.out / report['completed'][0]['directory'] / 'accounting.json')
        for index, stage in enumerate(build.STAGES[1:], 2):
            directory = self.out / 'stages' / f'{index:02d}-{stage}'
            candidate = directory / 'candidate'
            candidate.mkdir(parents=True)
            if stage == 'scaffold':
                write_json(directory / 'requirements.json', dict(schema='agent-ee.requirement-map-receipt/1',
                    requirements=accounting['requirements']))
            if stage == 'package':
                write_json(directory / 'accepted-package.json', dict(artifacts={}, native_files={},
                    physical_validation_performed=False, supplier_preview_performed=False))
            report['completed'].append(dict(stage=stage, directory=str(directory.relative_to(self.out)),
                candidate=str(candidate.relative_to(self.out)), tree=tree_identity(directory)))
        report.update(candidate=report['completed'][-1]['candidate'], status='assessing', accepted=False)
        write_json(self.out / 'build-report.json', report)
        before = tree_identity(self.out / 'stages')
        result = build.run(self.plan_path, self.out, resume=True)
        self.assertEqual(result['attempts'], report['attempts'])
        self.assertEqual(result['completed'], report['completed'])
        self.assertEqual(before, tree_identity(self.out / 'stages'))
        self.assertEqual(result['status'], 'pending')  # No genuine final native checks or package gate.
        self.assertFalse(result['accepted'])


class JudgmentTests(unittest.TestCase):
    def setUp(self):
        text = 'Engineering decision for this synthetic control: nominal board thickness 1.2 mm.'
        self.source_hash = hashlib.sha256(text.encode()).hexdigest()
        self.span = dict(byte_start=0, byte_end=len(text.encode()), text_sha256=self.source_hash)
        self.document = dict(schema='agent-ee.engineering-judgment/1', requirement='REQ-HEIGHT',
            span=self.span, conclusion='Selected nominal thickness is applicable to this control.',
            assumptions=['The captured envelope is the selected design.'],
            sources=[dict(kind='engineering', citation=dict(path='source.txt', sha256=self.source_hash,
                         quote=text, locator='entire control'))],
            inputs=[dict(path='source.txt', sha256=self.source_hash)],
            applicability=dict(board_thickness_mm=1.2), candidate=None,
            author=dict(agent='writer', model='test-model', date_utc='2026-10-25T00:00:00Z'),
            unresolved_defects=[])
        self.witness = dict(schema='agent-ee.judgment-verification/1', judgment_sha256='a' * 64,
            verifier=dict(agent='verifier', model='test-model', date_utc='2026-10-25T01:00:00Z'),
            checked=list(qualification.WITNESS_CHECKS), result='concur', defects=[])
        self.args = dict(requirement='REQ-HEIGHT', span=self.span, files={'source.txt': self.source_hash},
            source_text={'source.txt': text}, applicability=dict(board_thickness_mm=1.2),
            binding=dict(board_sha256='b' * 64, scope_sha256='c' * 64), judgment_sha256='a' * 64)

    def test_bound_testimony_is_recorded_and_missing_witness_stays_pending(self):
        result = qualification.admit_judgment(self.document, witness=self.witness, **self.args)
        self.assertEqual(result, dict(status='recorded', witness='concur'))
        result = qualification.admit_judgment(self.document, **self.args)
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(result['witness'], 'missing')

    def test_stale_dissented_or_self_witnessed_judgment_never_admits(self):
        for change in ('input', 'source', 'applicability', 'unknown_condition', 'candidate',
                       'defect', 'self_witness', 'witness_hash', 'dissent'):
            document, witness = copy.deepcopy(self.document), copy.deepcopy(self.witness)
            if change == 'input': document['inputs'][0]['sha256'] = '0' * 64
            if change == 'source': document['sources'][0]['citation']['quote'] = 'not present'
            if change == 'applicability': document['applicability']['board_thickness_mm'] = 1.6
            if change == 'unknown_condition': document['applicability']['invented_condition'] = 1.2
            if change == 'candidate': document['candidate'] = dict(board_sha256='0' * 64, scope_sha256='c' * 64)
            if change == 'defect': document['unresolved_defects'] = ['known wrong polarity']
            if change == 'self_witness': witness['verifier']['agent'] = 'writer'
            if change == 'witness_hash': witness['judgment_sha256'] = '0' * 64
            if change == 'dissent': witness['result'] = 'dissent'
            with self.subTest(change=change):
                result = qualification.admit_judgment(document, witness=witness, **self.args)
                self.assertEqual(result['status'], 'failed', result)


class NativeReviewQualificationTests(unittest.TestCase):
    def test_real_passing_export_checks_do_not_imply_review_or_file_release(self):
        from agent_ee.run_record import run_review
        from tests.toolchain import require_toolchain
        require_toolchain(native=True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = Path(__file__).resolve().parents[1] / 'fixtures/fab-export'
            source = root / 'source'
            shutil.copytree(fixture, source / 'design')
            scope = read_json(fixture / 'control.scope.json')
            scope['inputs'][0]['path'] = str(source / 'design')
            completed = []
            for index, stage in enumerate(build.STAGES, 1):
                directory = root / 'stages' / f'{index:02d}-{stage}'
                candidate = directory / 'candidate'
                shutil.copytree(source, candidate)
                if stage == 'scaffold':
                    write_json(directory / 'product/scope.json', scope)
                    write_json(directory / 'requirements.json', dict(schema='agent-ee.requirement-map-receipt/1',
                        requirements=[dict(id='R-fab', mode='checks', locator={}, mapping=scope['checks'])]))
                if stage == 'package':
                    write_json(directory / 'scope.json', scope)
                    run_review(directory / 'scope.json', directory / 'review', 120,
                               checks_only=True, output_stream=io.StringIO())
                    write_json(directory / 'accepted-package.json', dict(artifacts={}, native_files={},
                        physical_validation_performed=False, supplier_preview_performed=False))
                completed.append(dict(stage=stage, directory=str(directory.relative_to(root)),
                    candidate=str(candidate.relative_to(root)), tree=tree_identity(directory)))
            pin = dict(plan_sha256='a' * 64, source=build._inventory(source), runtime={})
            write_json(root / 'build-report.json', dict(schema=build.REPORT_SCHEMA, inputs=pin,
                identity=identity(pin), status='assessing', completed=completed, attempts=[],
                candidate=completed[-1]['candidate'], accepted=False))
            before = tree_identity(root / 'stages')
            result = qualification.assess(root)
            self.assertEqual(result['dimensions']['checks']['status'], 'pass', result)
            self.assertEqual(result['dimensions']['engineering_review']['status'], 'not_assessed')
            self.assertEqual(result['dimensions']['file_release']['status'], 'failed')
            self.assertFalse(result['accepted'])
            self.assertEqual(result['coverage']['R-fab']['status'], 'unknown')
            self.assertEqual({row['class'] for row in result['methods']}, {'native', 'topology'})
            self.assertEqual(before, tree_identity(root / 'stages'))
            failed = root / 'stages/07-package-failed'
            candidate = failed / 'candidate'
            shutil.copytree(source, candidate)
            board = candidate / 'design/fab.kicad_pcb'
            raw = board.read_text()
            self.assertIn('(property "Reference" "R1"', raw)
            board.write_text(raw.replace('(property "Reference" "R1"', '(property "Reference" "R99"', 1))
            bad_scope = copy.deepcopy(scope)
            bad_scope['inputs'][0]['path'] = str(candidate / 'design')
            write_json(failed / 'scope.json', bad_scope)
            run_review(failed / 'scope.json', failed / 'review', 120, checks_only=True, output_stream=io.StringIO())
            completed = completed[:-1]
            write_json(root / 'build-report.json', dict(schema=build.REPORT_SCHEMA, inputs=pin,
                identity=identity(pin), status='failed', completed=completed,
                attempts=[dict(stage='package', directory=str(failed.relative_to(root)),
                               status='failed', identity=identity(pin))],
                candidate=completed[-1]['candidate'], accepted=False))
            result = qualification.assess(root)
            self.assertEqual(result['dimensions']['checks']['status'], 'failed')
            self.assertEqual(result['dimensions']['file_release']['status'], 'failed')
            self.assertFalse(result['accepted'])
