"""Original component fixtures, verified PDF citations and sandboxed native STEP bounds."""
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from agent_ee.board_envelope import derive_requirements, obligation_coverage, validate
from agent_ee.candidate import capture
from agent_ee.checks import run_checks
from agent_ee.envelope_check import parameters
from agent_ee.storage import digest, read_json, write_json
from tests.test_esp32_footprint_keepout import multipage_text_pdf
from tests.toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]

BUILD = '''import pcbnew as k,sys
b=k.BOARD()
for a,z in (((0,0),(20,0)),((20,0),(20,20)),((20,20),(0,20)),((0,20),(0,0))):
    s=k.PCB_SHAPE(b)
    s.SetShape(k.SHAPE_T_SEGMENT)
    s.SetLayer(k.Edge_Cuts)
    s.SetStart(k.VECTOR2I(a[0]*1000000,a[1]*1000000))
    s.SetEnd(k.VECTOR2I(z[0]*1000000,z[1]*1000000))
    s.SetWidth(50000)
    b.Add(s)
fp=k.FOOTPRINT(b)
fp.SetFPID(k.LIB_ID('Owned','Capacitor'))
fp.SetReference('C1')
fp.SetValue('OWNED-CAP')
fp.SetPosition(k.VECTOR2I(10000000,10000000))
b.Add(fp)
k.SaveBoard(sys.argv[1],b)
'''


class ComponentHeightTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True, tools=('pdftotext',))
        self.tmp = tempfile.TemporaryDirectory(prefix='ee182-height-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.board = self.source / 'board.kicad_pcb'
        subprocess.run(['/usr/bin/python3', '-B', '-c', BUILD, str(self.board)], check=True,
                       capture_output=True, timeout=60)
        self.envelope = read_json(ROOT / 'fixtures/board-envelope/rc.envelope.json')
        self.envelope['max_component_height_mm'] = 12
        write_json(self.source / 'envelope.json', self.envelope)
        (self.source / 'targets.md').write_text('Every installed component is at most 12 mm high.\n')
        self.params = dict(criterion='component-height/1', envelope='envelope.json')
        self.count = 0

    def fact(self, value='13', **overrides):
        multipage_text_pdf(self.source / 'height.pdf', 1, 1, [f'Owned capacitor installed maximum height {value} mm'])
        fact = dict(id='height-C1', kind='component_height', part_number='OWNED-CAP',
                    footprint_id='Owned:Capacitor', value=value, unit='mm',
                    measurement='installed_maximum_above_board', conditions='Owned original capacitor fixture',
                    source=dict(path='height.pdf', sha256=digest(self.source / 'height.pdf'), page=1,
                                locator='Owned dimension', quote=f'Owned capacitor installed maximum height {value} mm'))
        fact.update(overrides)
        write_json(self.source / 'facts.json', dict(schema='agent-ee.datasheet-facts/1', facts=[fact]))
        self.params.update(facts_check='facts', height_facts={'C1': 'height-C1'})

    def run_height(self):
        self.count += 1
        specs = []
        if 'facts_check' in self.params:
            version = subprocess.run(['/usr/bin/pdftotext', '-v'], capture_output=True,
                                     text=True, check=True).stderr.splitlines()[0]
            specs.append(dict(id='facts', kind='datasheet_facts', params=dict(facts='facts.json',
                extractor=dict(path='/usr/bin/pdftotext', sha256=digest('/usr/bin/pdftotext'), version=version))))
        specs.append(dict(id='height', kind='board_envelope', params=self.params))
        scope = dict(schema='agent-ee.scope/1', name='owned-height-contract', scope='Sourced installed height',
                     board='board.kicad_pcb', checks=specs)
        scope['inputs'] = [dict(path=str(p), at=p.relative_to(self.source).as_posix())
                           for p in self.source.rglob('*') if p.is_file()]
        write_json(self.root / 'scope.json', scope)
        run = self.root / f'run-{self.count}'
        run.mkdir()
        manifest = capture(self.root / 'scope.json', run)
        from agent_ee.capabilities import describe_check
        descriptor = describe_check(specs[-1], scope=scope, check_specs=specs,
                                    files=manifest['files'], inputs=run / 'inputs')
        self.assertEqual(descriptor['status'], 'supported', descriptor)
        results = {r['id']: r for r in run_checks(run, time.monotonic() + 120)}
        result = results['height']
        self.assertEqual(result['details']['board_sha256'], digest(self.board), result)
        self.assertIn('work/height/envelope-judgment.json', result['evidence'])
        self.assertEqual(descriptor['predicates'], ['envelope:component-height/1'])
        self.descriptor = descriptor
        self.last_run = run
        return result

    def test_component_height_unknown_without_source(self):
        result = self.run_height()
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'))
        self.assertEqual(result['details']['measured'][0]['ref'], 'C1')
        self.assertIn('no height source', result['summary'])
        # Assembly exclusions and the known AMPSEAL identity grant no exception.
        self.board.write_text(self.board.read_text().replace('"C1"', '"J1"').replace('"OWNED-CAP"', '"776228-1"'))
        result = self.run_height()
        self.assertEqual(result['outcome'], 'unknown')
        self.assertEqual(result['details']['measured'][0]['ref'], 'J1')

    def test_component_height_defect(self):
        self.fact()
        result = self.run_height()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'))
        self.assertEqual(result['details']['measured'][0]['height_mm'], '13')
        self.assertEqual(result['details']['defects'], ['C1: installed height 13 mm exceeds 12 mm'])

    def test_height_uses_partnumber_binding_with_nominal_display_value(self):
        self.fact()
        # Add the binding to the existing footprint through the native API.
        script = '''import pcbnew as k,sys
b=k.LoadBoard(sys.argv[1])
fp=list(b.GetFootprints())[0]
fp.SetValue('100uF')
fp.SetField('Partnumber','OWNED-CAP')
k.SaveBoard(sys.argv[1],b)
'''
        subprocess.run(['/usr/bin/python3', '-B', '-c', script, str(self.board)], check=True,
                       capture_output=True, timeout=60)
        result = self.run_height()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertEqual(result['details']['measured'][0]['part_number'], 'OWNED-CAP')
        self.fact(part_number='100uF')
        self.assertEqual(self.run_height()['outcome'], 'unknown')

    def test_datasheet_inclusive_limit_and_one_nm_over(self):
        for height, outcome in (('12', 'pass'), ('12.000001', 'defect')):
            with self.subTest(height=height):
                self.fact(height)
                result = self.run_height()
                self.assertEqual((result['execution'], result['outcome']), ('ok', outcome))

    def test_height_fact_must_be_verified_and_applicable(self):
        for overrides in (dict(part_number='OTHER'), dict(footprint_id='Owned:Other'),
                          dict(kind='axial_reference_sum'), dict(unit='in'), dict(measurement='nominal'),
                          dict(value='NaN'), dict(value='0')):
            with self.subTest(overrides=overrides):
                self.fact(**overrides)
                result = self.run_height()
                self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'))
        self.fact()
        facts = read_json(self.source / 'facts.json')
        facts['facts'][0]['source']['quote'] = 'absent quote 13 mm'
        write_json(self.source / 'facts.json', facts)
        result = self.run_height()
        self.assertEqual(result['outcome'], 'unknown')

    def model(self, offset=2, rotation=0, filename='body.step', hide=False):
        # Export our original board solid only, then reuse it as an original test body.
        source_model = self.source / 'body.step'
        if not source_model.exists():
            subprocess.run(['kicad-cli', 'pcb', 'export', 'step', '--board-only', '-f',
                            '-o', str(source_model), str(self.board)], check=True,
                           capture_output=True, timeout=60)
        script = '''import pcbnew as k,sys
b=k.LoadBoard(sys.argv[1])
fp=list(b.GetFootprints())[0]
fp.Models().clear()
m=k.FP_3DMODEL()
m.m_Filename=sys.argv[2]
m.m_Offset=k.VECTOR3D(0,0,float(sys.argv[3]))
m.m_Rotation=k.VECTOR3D(float(sys.argv[4]),0,0)
m.m_Show=sys.argv[5]=='show'
fp.Models().push_back(m)
k.SaveBoard(sys.argv[1],b)
'''
        subprocess.run(['/usr/bin/python3', '-B', '-c', script, str(self.board), filename,
                        str(offset), str(rotation), 'hide' if hide else 'show'], check=True,
                       capture_output=True, timeout=60)

    def test_native_step_bounds_pass_defect_and_unknowns(self):
        self.model()
        result = self.run_height()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertEqual(result['details']['measured'][0]['source'], 'native_STEP_bounds')
        self.assertIn('body.step', self.descriptor['immutable_inputs'])
        self.assertIn('work/height/height-model-bounds.json', result['evidence'])
        self.model(offset=14)
        result = self.run_height()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        for kwargs in (dict(rotation=12), dict(hide=True), dict(filename='missing.step')):
            with self.subTest(kwargs=kwargs):
                self.model(**kwargs)
                self.assertEqual(self.run_height()['outcome'], 'unknown')
        self.model()
        (self.source / 'body.step').write_text('invalid original STEP fixture\n')
        self.assertEqual(self.run_height()['outcome'], 'unknown')

    def test_stock_receipt_hash_and_bottom_model(self):
        from tests.test_fab_consistency import stock_library
        self.model()
        relative = 'Owned.3dshapes/body.step'
        files = stock_library([relative], content={relative: (self.source / 'body.step').read_bytes()})
        # stock_library is an original receipt builder, never a vendor-file capture.
        for rel, data in files.items():
            target = self.source / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        receipt = next(rel for rel in files if rel.endswith('.agent-ee-models.json'))
        # Replace the builder's placeholder digest with the native original body hash.
        record = read_json(self.source / receipt)
        record['files'][relative]['sha256'] = digest(self.source / 'body.step')
        record['files'][relative]['size_bytes'] = (self.source / 'body.step').stat().st_size
        write_json(self.source / receipt, record)
        self.params['model_libraries'] = {'KICAD10_3DMODEL_DIR': receipt}
        self.model(filename='${KICAD10_3DMODEL_DIR}/' + relative)
        script = '''import pcbnew as k,sys
b=k.LoadBoard(sys.argv[1])
fp=list(b.GetFootprints())[0]
fp.Flip(fp.GetPosition(),False)
k.SaveBoard(sys.argv[1],b)
'''
        subprocess.run(['/usr/bin/python3', '-B', '-c', script, str(self.board)], check=True,
                       capture_output=True, timeout=60)
        result = self.run_height()
        self.assertEqual(result['outcome'], 'pass', result)
        self.assertIn(receipt, self.descriptor['immutable_inputs'])
        member = str(Path(receipt).parent / relative)
        self.assertIn(member, self.descriptor['immutable_inputs'])
        (self.source / member).write_text('changed captured original model\n')
        self.assertEqual(self.run_height()['outcome'], 'unknown')

    def test_empty_inventory_and_mixed_defect_unknown(self):
        self.fact()
        addition = '(footprint "Owned:Connector" (layer "F.Cu") (at 5 5) ' \
                   '(property "Reference" "J1") (property "Value" "776228-1"))'
        self.board.write_text(self.board.read_text().rstrip()[:-1] + addition + ')\n')
        result = self.run_height()
        self.assertEqual(result['outcome'], 'unknown')
        self.assertEqual({r['ref']: r['status'] for r in result['details']['measured']},
                         {'C1': 'defect', 'J1': 'unknown'})
        self.assertTrue(result['details']['defects'])
        # A real native empty board cannot pass universal height by vacuity.
        script = '''import pcbnew as k,sys
b=k.BOARD()
k.SaveBoard(sys.argv[1],b)
'''
        subprocess.run(['/usr/bin/python3', '-B', '-c', script, str(self.board)], check=True,
                       capture_output=True, timeout=60)
        self.params.pop('facts_check')
        self.params.pop('height_facts')
        result = self.run_height()
        self.assertEqual(result['outcome'], 'unknown')
        self.assertIn('empty native footprint inventory', result['summary'])

    def test_height_declaration_and_binding(self):
        record = validate(json.dumps(self.envelope).encode())
        derived = derive_requirements(record, 'envelope.json')
        item = next(r for r in derived['requirements'] if r['source']['locator'] == '/max_component_height_mm')
        mapped = {k: item[k] for k in ('id', 'statement', 'source', 'intent')} | {'checks': ['height']}
        scope = dict(schema='agent-ee.scope/3', checks=[dict(id='height', kind='board_envelope', params=self.params)],
                     baseline=dict(requirements=[mapped]))
        coverage = obligation_coverage(scope, derived, record, {'envelope.json': record['envelope_sha256']})
        self.assertEqual(next(r for r in coverage if r['id'] == item['id'])['status'], 'bound')
        for limit in (True, 0, -1, '12', 0.0000001):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                validate(json.dumps({**self.envelope, 'max_component_height_mm': limit}).encode())
        for params in (dict(criterion='component-height/1'), {**self.params, 'exempt_refs': ['J1']},
                       {**self.params, 'height_facts': {'C1': 'height'}}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                parameters(params)

    def test_scope3_freezes_height_envelope_and_rejects_wrong_binding(self):
        from tests.test_envelope_check import PodEnvelopeAcceptanceTests
        fixture = PodEnvelopeAcceptanceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        scope = fixture.scope()
        fixture.envelope['max_component_height_mm'] = 12
        fixture.save_envelope()
        record = validate((fixture.source / 'envelope.json').read_bytes())
        derived = derive_requirements(record, 'envelope.json')
        item = next(r for r in derived['requirements'] if r['source']['locator'] == '/max_component_height_mm')
        scope['baseline']['requirements'].append({k: item[k] for k in ('id', 'statement', 'source', 'intent')}
                                                | {'checks': ['height']})
        scope['checks'].append(dict(id='height', kind='board_envelope', params=self.params))
        run, manifest = fixture.capture(scope)
        baseline = read_json(run / 'baseline.json')
        self.assertEqual(baseline['files']['envelope.json'], manifest['files']['envelope.json'])
        self.assertEqual(baseline['coverage']['checks']['height']['predicates'], ['envelope:component-height/1'])
        self.assertEqual(next(c for c in baseline['envelope']['coverage'] if c['id'] == item['id'])['status'], 'bound')
        scope['checks'][-1]['params'] = dict(criterion='outline/1', envelope='envelope.json')
        with self.assertRaisesRegex(ValueError, 'exact criterion'):
            fixture.capture(scope)

    def test_model_dependencies_do_not_change_with_candidate_footprints(self):
        self.model()
        self.assertEqual(self.run_height()['outcome'], 'pass')
        populated = self.descriptor['immutable_inputs']
        script = 'import pcbnew as k,sys\nb=k.BOARD()\nk.SaveBoard(sys.argv[1],b)\n'
        subprocess.run(['/usr/bin/python3', '-B', '-c', script, str(self.board)], check=True,
                       capture_output=True, timeout=60)
        self.assertEqual(self.run_height()['outcome'], 'unknown')
        self.assertEqual(self.descriptor['immutable_inputs'], populated)
        self.assertIn('body.step', populated)


if __name__ == '__main__':
    unittest.main()
