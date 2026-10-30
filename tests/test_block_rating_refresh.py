"""Stock-response refreshes preserve component semantics and both rating receipts."""
import copy
from pathlib import Path
import shutil
import tempfile
import unittest

from agent_ee.storage import digest, read_json, write_json
from tests.test_passive_ratings import PassiveRatingTests, MPN, POWER, SUPPLY
from tests.test_approved_parts import named_fixture


class BlockRatingRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from agent_ee.binding_fill import configuration
        from agent_ee.parts_manifest import qualify_manifest
        from agent_ee.stage_receipts import run_step
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.fixture = Path(cls.temporary.name) / 'fixture'
        cls.fields = {}
        for label in ('current', 'source'):
            helper = PassiveRatingTests()
            helper.setUp()
            try:
                if label == 'current':
                    named_fixture(helper.root, helper.parts, mpn=MPN, package='0603',
                                  attributes=[POWER, SUPPLY], refresh_note='new stock response')
                for name, value in [('intent.json', helper.intent), ('parts.json', helper.parts),
                                    ('refs.json', helper.refs), ('deviations.json', helper.deviations),
                                    ('ratings.json', helper.ratings)]:
                    write_json(helper.root/name, value)
                spec = configuration(Path(__file__).resolve().parents[1]/'src', intent='intent.json',
                    manifest='parts.json', part_refs='refs.json', deviations='deviations.json', captures='.',
                    dependencies=sorted(qualify_manifest(helper.parts, helper.root)['dependencies']),
                    ratings='ratings.json')
                step = Path(cls.temporary.name)/('step-'+label)
                receipt = run_step(step, helper.root, spec,
                    {'implementation':1, 'source_candidate_id':'owned-rating-refresh', 'receipt_index':1})
                if receipt['status'] != 'completed':
                    raise AssertionError((step/'run/console.log').read_text())
                target = cls.fixture if label=='current' else cls.fixture/'block'
                shutil.copytree(helper.root, target)
                rating = target/'build-generated/parts-ratings'
                rating.mkdir(parents=True)
                shutil.copyfile(step/'receipt.json', rating/'receipt.json')
                shutil.copyfile(step/'run/out/binding-fill.json', rating/'report.json')
                cls.fields[label] = read_json(step/'run/out/intent.json')['components'][0]['part_binding']
            finally:
                helper.doCleanups()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)/'inputs'
        shutil.copytree(self.fixture, self.root)
        self.files = {p.relative_to(self.root).as_posix():digest(p)
                      for p in self.root.rglob('*') if p.is_file()}
        self.declaration = dict(receipt='build-generated/parts-ratings/receipt.json',
            report='build-generated/parts-ratings/report.json',
            receipt_sha256=self.files['build-generated/parts-ratings/receipt.json'])
        before = dict(reference='R1', lib_id='Device:R', uuid='owned-same-uuid', value='10k',
                      properties=copy.deepcopy(self.fields['source']))
        after = {**before, 'properties':copy.deepcopy(self.fields['current'])}
        self.changes = [dict(reference='R1', severity='defect', before=before, after=after)]
        self.selected = [dict(reference='R1', delivered='R1')]

    def evaluate(self):
        from agent_ee.block_composition import catalog_rating_refreshes
        return catalog_rating_refreshes(self.root, self.files, 'block', self.selected,
                                       self.changes, self.declaration)

    def test_fresh_receipted_catalog_bytes_preserve_exact_part_and_rating(self):
        original = copy.deepcopy(self.changes)
        changes, refreshes = self.evaluate()
        self.assertEqual(changes, [])
        self.assertEqual(len(refreshes), 1)
        self.assertEqual(refreshes[0]['reference'], 'R1')
        self.assertEqual(refreshes[0]['native_change'], original[0])
        self.assertNotEqual(refreshes[0]['before_record_sha256'], refreshes[0]['after_record_sha256'])
        self.assertEqual(self.changes, original)

    def test_other_native_changes_and_missing_rating_fields_remain_defects(self):
        original = copy.deepcopy(self.changes)
        for key, value in [('value','20k'), ('uuid','another'), ('footprint','Other')]:
            self.changes = copy.deepcopy(original)
            self.changes[0]['after'][key] = value
            self.assertEqual(self.evaluate(), (self.changes, []))
        for key, value in [('Power','250mW'), ('Partnumber','OTHER'), ('AGENT_EE_Ratings',None)]:
            self.changes = copy.deepcopy(original)
            if value is None:
                del self.changes[0]['after']['properties'][key]
            else:
                self.changes[0]['after']['properties'][key] = value
            self.assertEqual(self.evaluate(), (self.changes, []))

    def test_both_receipts_reports_and_catalog_bytes_must_be_captured_and_unchanged(self):
        for prefix in ('', 'block/'):
            for suffix in ('build-generated/parts-ratings/receipt.json',
                           'build-generated/parts-ratings/report.json',
                           'named/C38695/responses/C38695.json'):
                name = prefix+suffix
                with self.subTest(name=name):
                    pin = self.files.pop(name)
                    with self.assertRaises(ValueError):
                        self.evaluate()
                    self.files[name] = pin
                    path = self.root/name
                    raw = path.read_bytes()
                    path.write_bytes(raw+b'\n')
                    with self.assertRaises(ValueError):
                        self.evaluate()
                    path.write_bytes(raw)

    def test_unreceipted_native_record_hash_is_never_a_refresh(self):
        import json
        for side in ('before','after'):
            original = self.changes[0][side]['properties']['AGENT_EE_Ratings']
            field = json.loads(original)
            field['record_sha256'] = '0'*64
            self.changes[0][side]['properties']['AGENT_EE_Ratings'] = json.dumps(field)
            with self.assertRaisesRegex(ValueError, 'receipted'):
                self.evaluate()
            self.changes[0][side]['properties']['AGENT_EE_Ratings'] = original

    def test_absent_declared_current_provenance_cannot_enable_refresh(self):
        self.declaration = None
        self.assertEqual(self.evaluate(), (self.changes, []))
    def test_receipted_changes_beyond_catalog_digest_remain_defects(self):
        import json
        from agent_ee.storage import identity
        report_path = self.root/self.declaration['report']
        receipt_path = self.root/self.declaration['receipt']
        original_report, original_receipt = read_json(report_path), read_json(receipt_path)
        original_changes = copy.deepcopy(self.changes)
        mutations = [lambda r:r['part'].update(id='different-part'),
                     lambda r:r['ratings']['Power'].update(si='0.25'),
                     lambda r:r['ratings']['Power'].update(unit='different-unit'),
                     lambda r:r['ratings']['Power']['sources'][0].update(attribute='Other attribute'),
                     lambda r:r.update(limitations=[])]
        for mutate in mutations:
            report, receipt = copy.deepcopy(original_report), copy.deepcopy(original_receipt)
            entry = report['ratings']['derived']['R1']
            mutate(entry['record'])
            entry['record_sha256'] = identity(entry['record'])
            write_json(report_path, report)
            receipt['artifacts']['out/binding-fill.json'] = digest(report_path)
            receipt['identity'] = identity({k:v for k,v in receipt.items() if k!='identity'})
            write_json(receipt_path, receipt)
            for name in ('receipt','report'):
                self.files[self.declaration[name]] = digest(self.root/self.declaration[name])
            self.declaration['receipt_sha256'] = digest(receipt_path)
            self.changes = copy.deepcopy(original_changes)
            props = self.changes[0]['after']['properties']
            field = json.loads(props['AGENT_EE_Ratings'])
            field['record_sha256'] = entry['record_sha256']
            props['AGENT_EE_Ratings'] = json.dumps(field)
            self.assertEqual(self.evaluate(), (self.changes, []))

    def test_changed_datasheet_digest_is_not_a_catalog_refresh(self):
        import json
        from agent_ee.storage import identity
        # Deliberately resealed negative fixtures: even internally consistent
        # receipts cannot make a datasheet digest change an allowed stock refresh.
        for side, prefix in (('before','block/'), ('after','')):
            report_path = self.root/(prefix+'build-generated/parts-ratings/report.json')
            receipt_path = self.root/(prefix+'build-generated/parts-ratings/receipt.json')
            report, receipt = read_json(report_path), read_json(receipt_path)
            entry = report['ratings']['derived']['R1']
            response = entry['record']['ratings']['Power']['sources'][0]
            entry['record']['ratings']['Power']['sources'][0] = dict(kind='datasheet',
                id='owned-family', fact='power', path=response['path'], sha256=response['sha256'], page=1)
            entry['record_sha256'] = identity(entry['record'])
            write_json(report_path, report)
            receipt['artifacts']['out/binding-fill.json'] = digest(report_path)
            receipt['identity'] = identity({k:v for k,v in receipt.items() if k!='identity'})
            write_json(receipt_path, receipt)
            for path in (receipt_path, report_path):
                self.files[path.relative_to(self.root).as_posix()] = digest(path)
            if side=='after':
                self.declaration['receipt_sha256'] = digest(receipt_path)
            props = self.changes[0][side]['properties']
            field = json.loads(props['AGENT_EE_Ratings'])
            field['record_sha256'] = entry['record_sha256']
            props['AGENT_EE_Ratings'] = json.dumps(field)
        self.assertEqual(self.evaluate(), (self.changes, []))
