"""Generated repair with real parts receipts and explicitly synthetic review seats.

These controls exercise provenance and convergence, not electrical qualification.
The parts producer runs in its ordinary sandbox before the protocol fixture
installs recorded check/provider boundaries.
"""
import copy
import os
from pathlib import Path
import shutil
import time
import unittest
from unittest.mock import patch

from agent_ee import build_stages, campaign, native_run, parts_stage, run_record
from agent_ee.baseline import compile_baseline, load_baseline
from agent_ee.check_record import CheckRecord
from agent_ee.passive_ratings import load_provenance
from agent_ee.rating_lineage import strip
from agent_ee.stage_receipts import adopt, run_step, tree_identity
from agent_ee.storage import digest, read_json, write_json
from tests import test_generated_addr as addr_protocol
from tests import test_parts_stage as parts_fixture
from tests.toolchain import require_toolchain


class GeneratedAddrRatingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        fixture = parts_fixture.PartsProducerTests()
        cls.addClassCleanup(fixture.doCleanups)
        fixture.setUp()
        evidence = os.environ.get('AGENT_EE_COMPLETION_EVIDENCE')
        if evidence:
            cls.addClassCleanup(shutil.copytree, fixture.root,
                                Path(evidence)/'generated-addr-rating-producers')
        from tests.test_approved_parts import named_fixture
        for index, lcsc, mpn, attributes in (
            (0, 'C38', 'SYNTHETIC-10K', [('Resistance', '10kΩ'), ('Power(Watts)', '100mW')]),
            (1, 'C39', 'SYNTHETIC-100N', [('Capacitance', '100nF'), ('Voltage Rating', '25V')]),
        ):
            named_fixture(fixture.bundle, fixture.manifest, index=index, lcsc=lcsc, mpn=mpn,
                package='0402', attributes=[dict(attribute_name_en=k, attribute_value_name=v)
                                           for k, v in attributes])
        write_json(fixture.bundle/'ratings.json', dict(schema='agent-ee.passive-rating-sources/1',
            catalog=True, facts=None, extractor=None))
        from tests.test_assembly_readiness import _write_sqlite_catalog
        (fixture.bundle/'assembly-catalog').mkdir()
        _write_sqlite_catalog(fixture.bundle/'assembly-catalog', [dict(lcsc=lcsc, mpn=mpn, stock=1000)
            for lcsc, mpn in [('C38', 'SYNTHETIC-10K'), ('C39', 'SYNTHETIC-100N'), ('C3', 'J-C3')]])
        fixture.save()
        cls.produced = []
        with native_run.using(deadline=time.monotonic()+90):
            for number in range(2):
                candidate = fixture.root/f'produced-{number}'
                shutil.copytree(fixture.bundle, candidate)
                target = candidate/build_stages.GENERATED
                target.parent.mkdir()
                target.write_text('{}\n')
                declaration = parts_stage.configuration(Path(__file__).resolve().parents[1]/'src',
                    bundle='.', manifest='parts.json', circuit='circuit.json', layout='layout.json',
                    ratings='ratings.json', output=build_stages.GENERATED)
                output = fixture.root/f'receipt-{number}'
                receipt = run_step(output, candidate, declaration, dict(implementation=1,
                    source_candidate_id=tree_identity(candidate)['sha256'], receipt_index=0))
                if receipt['status'] != 'completed':
                    raise AssertionError((output/'run/console.log').read_text())
                adopt(output, receipt, candidate)
                build_stages._capture_ratings(output, receipt, candidate)
                load_provenance(candidate, build_stages._rating_declaration(candidate))
                cls.produced.append(candidate)
        if digest(cls.produced[0]/build_stages.RATINGS/'receipt.json') == digest(
                cls.produced[1]/build_stages.RATINGS/'receipt.json'):
            raise AssertionError('independent real producer receipts must differ')

    def setUp(self):
        self.protocol = addr_protocol.GeneratedAddrTests()
        self.addCleanup(self.protocol.doCleanups)
        self.protocol.setUp()
        # The extra assembly check declares KiCad as a prerequisite. Review
        # execution remains the explicitly scripted protocol boundary.
        self.protocol.host['runtime_files']['kicad-cli'] = dict(
            self.protocol.host['runtime_files']['/usr/bin/python3'])
        evidence = os.environ.get('AGENT_EE_COMPLETION_EVIDENCE')
        if evidence:
            self.addCleanup(shutil.copytree, self.protocol.root,
                            Path(evidence)/('rated-'+self._testMethodName))
        self.enterContext(patch('agent_ee.checks.run_checks', side_effect=self.checks))
        self.first = self.review('rated-first', self.produced[0], defect=True)
        self.protocol.first = self.first
        self.protocol.select()

    def checks(self, run, deadline, **kwargs):
        records = self.protocol.checks(run, deadline, **kwargs)
        manifest = read_json(run/'candidate.json')
        raw = run/'work/assembly/protocol.json'
        write_json(raw, dict(boundary='Scripted stock/native check; rating receipts are real producer outputs.'))
        record = CheckRecord('assembly', 'assembly_readiness', manifest['candidate_id'],
            execution='ok', outcome='pass', summary='Synthetic protocol assembly outcome',
            evidence=['work/assembly/protocol.json'])
        write_json(run/'checks/assembly.json', record)
        return [*records, record]

    def review(self, name, produced, *, defect=False, mutation=None):
        protocol = self.protocol
        source = protocol.root/(name+'-source')
        shutil.copytree(protocol.scope['inputs'][0]['path'], source)
        shutil.copytree(produced, source/'parts')
        scope = copy.deepcopy(protocol.scope)
        scope['inputs'][0]['path'] = str(source)
        declaration = build_stages._rating_declaration(source/'parts')
        declaration = {**declaration, 'receipt': 'design/parts/'+declaration['receipt'],
                       'report': 'design/parts/'+declaration['report']}
        scope['checks'].append(dict(id='assembly', kind='assembly_readiness', params=dict(
            catalog='design/parts/assembly-catalog/catalog-manifest.json', boards=1,
            rating_provenance=declaration)))
        scope['baseline']['requirements'].append(dict(id='RATINGS', statement='Receipted passive ratings.',
            source=dict(path='design/requirements', locator='whole'), checks=['assembly']))
        if mutation is not None:
            mutation(source, scope)
        protocol.fail = defect
        scope_path = protocol.root/(name+'-scope.json')
        write_json(scope_path, scope)
        run = protocol.root/name
        run_record.run_review(scope_path, run, 90, host=protocol.host, max_schema_resubmissions=0)
        self.assertEqual(run_record.verify(run, host=protocol.host)['outcome'],
                         'defects_or_unresolved' if defect else 'criteria_satisfied')
        return run

    def authored(self, run):
        manifest = read_json(run/'candidate.json')
        return compile_baseline(strip(manifest['scope']), manifest['files'], run/'inputs')

    def verify_successor(self, successor):
        return campaign.verify_generated_addr(self.protocol.root/'repair', successor=successor,
                                               host=self.protocol.host)

    def test_fresh_real_rating_receipts_preserve_complete_authored_baseline(self):
        successor = self.review('rated-successor', self.produced[1])
        raw_first = load_baseline(self.first)
        self.assertNotEqual(raw_first, load_baseline(successor))
        self.assertEqual(self.authored(self.first), self.authored(successor))
        before = tree_identity(self.protocol.root/'repair')
        result = self.verify_successor(successor)
        self.assertEqual(result['state'], 'criteria_satisfied')
        self.assertEqual(len(result['history']), 2)
        self.assertEqual(tree_identity(self.protocol.root/'repair'), before)
        self.assertEqual(read_json(self.protocol.root/'repair/state.json')['baseline'], raw_first)

    def test_authored_criteria_and_source_changes_remain_refusals(self):
        def source_change(source, scope):
            (source/'requirements').write_text('A weakened source requirement.')
        def criterion_change(source, scope):
            scope['checks'][-1]['params']['boards'] = 2
        for number, mutation in enumerate((source_change, criterion_change)):
            with self.subTest(mutation=mutation.__name__):
                successor = self.review(f'changed-authored-{number}', self.produced[1], mutation=mutation)
                with self.assertRaises(ValueError):
                    self.verify_successor(successor)

    def test_rating_declaration_presence_and_paths_remain_bound(self):
        def missing(source, scope):
            del scope['checks'][-1]['params']['rating_provenance']
        def relocated(source, scope):
            old = source/'parts'/build_stages.RATINGS
            shutil.copytree(old, source/'parts/relocated-ratings')
            declaration = scope['checks'][-1]['params']['rating_provenance']
            declaration.update(receipt='design/parts/relocated-ratings/receipt.json',
                               report='design/parts/relocated-ratings/report.json')
        for number, mutation in enumerate((missing, relocated)):
            with self.subTest(mutation=mutation.__name__):
                successor = self.review(f'changed-declaration-{number}', self.produced[1], mutation=mutation)
                self.assertEqual(self.authored(self.first), self.authored(successor))
                with self.assertRaises(ValueError):
                    self.verify_successor(successor)

    @staticmethod
    def corrupt_receipt(source, scope):
        path = source/'parts'/build_stages.RATINGS/'receipt.json'
        receipt = read_json(path)
        receipt['binding']['receipt_index'] += 1
        write_json(path, receipt)
        scope['checks'][-1]['params']['rating_provenance']['receipt_sha256'] = digest(path)

    @staticmethod
    def corrupt_report(source, scope):
        path = source/'parts'/build_stages.RATINGS/'report.json'
        path.write_bytes(path.read_bytes()+b'\n')

    def test_captured_corrupt_receipt_and_report_cannot_be_normalized_away(self):
        for number, mutation in enumerate((self.corrupt_receipt, self.corrupt_report)):
            with self.subTest(mutation=mutation.__name__):
                successor = self.review(f'corrupt-provenance-{number}', self.produced[1], mutation=mutation)
                self.assertEqual(self.authored(self.first), self.authored(successor))
                with self.assertRaises(ValueError):
                    self.verify_successor(successor)

    def test_corrupt_first_provenance_cannot_be_normalized_away(self):
        successor = self.review('valid-successor', self.produced[1])
        for number, mutation in enumerate((self.corrupt_receipt, self.corrupt_report)):
            with self.subTest(mutation=mutation.__name__):
                first = self.review(f'corrupt-first-{number}', self.produced[0],
                                    defect=True, mutation=mutation)
                self.assertEqual(self.authored(first), self.authored(successor))
                root = self.protocol.root/f'repair-corrupt-first-{number}'
                campaign.select_generated_addr(root, first, definition=self.protocol.definition,
                    launch_environment=self.protocol.environment, deadline=self.protocol.deadline,
                    cancellation=self.protocol.cancel, host=self.protocol.host)
                with self.assertRaises(ValueError):
                    campaign.verify_generated_addr(root, successor=successor, host=self.protocol.host)
