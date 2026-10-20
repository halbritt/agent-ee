"""Rung-1 requirement record to scope/2 compilation (AGENTEE-100)."""
import contextlib
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))  # Also run as tests.test_requirement_scope.

from agent_ee.baseline import compile_baseline
from agent_ee.candidate import capture
from agent_ee.checks import run_checks
from agent_ee.requirement_scope import COMPILER, Refused, compile_file, compile_record, main
from agent_ee.storage import json_bytes, read_json, write_json
from tests.toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / 'fixtures/rung1/requirements.record.json'
SPEC = ROOT / 'fixtures/rung1/spec.md'
GOLDEN = ROOT / 'fixtures/rung1/compiled.scope.json'
CLOSED_LOOP = ROOT / 'fixtures/closed-loop/control.scope.json'
REFERENCE = ROOT / 'fixtures/rc/control'


def record():
    return json.loads(RECORD.read_text())


def compiled(value, spec=None):
    return compile_record(json.dumps(value).encode(), SPEC.read_bytes() if spec is None else spec)


def requirement(value, rid):
    return next(r for r in value['requirements'] if r['id'] == rid)


class CompileTests(unittest.TestCase):
    def refused(self, value, spec=None):
        with self.assertRaises(Refused) as caught:
            compiled(value, spec)
        return {(f['code'], f['id']) for f in caught.exception.findings}

    def test_rung1_record_compiles_byte_identically_to_the_committed_scope(self):
        first = compile_file(RECORD)
        second = compile_file(RECORD)
        self.assertEqual(json_bytes(first[2]), json_bytes(second[2]))
        self.assertEqual(first[3], second[3])
        self.assertEqual(json_bytes(first[2]), GOLDEN.read_bytes())
        receipt = first[3]
        self.assertEqual(receipt['compiler']['version'], COMPILER)
        self.assertEqual(receipt['record_sha256'], hashlib.sha256(RECORD.read_bytes()).hexdigest())
        self.assertEqual(receipt['spec_sha256'], hashlib.sha256(SPEC.read_bytes()).hexdigest())
        self.assertEqual(receipt['scope_sha256'], hashlib.sha256(GOLDEN.read_bytes()).hexdigest())
        self.assertEqual([r['id'] for r in receipt['requirements']],
                         ['R-native', 'R-width', 'R-response', 'R-fab', 'R-parts'])
        self.assertTrue(all(r['source'] is not None for r in receipt['requirements']))

    def test_every_requirement_maps_to_a_check_and_compile_baseline_accepts(self):
        _, spec_raw, scope, receipt = compile_file(RECORD)
        self.assertEqual(scope['schema'], 'agent-ee.scope/2')
        self.assertEqual([(c['id'], c['kind']) for c in scope['checks']], [
            ('erc', 'erc'), ('drc', 'drc'), ('netlist', 'netlist'), ('geometry', 'geometry'),
            ('rc-step', 'rc_transient'), ('fab', 'fab_export')])
        requirements = scope['baseline']['requirements']
        self.assertTrue(all(r['checks'] for r in requirements))
        self.assertEqual({c for r in requirements for c in r['checks']}, {c['id'] for c in scope['checks']})
        self.assertEqual(requirement(scope['baseline'], 'R-fab')['checks'], ['fab'])
        self.assertTrue(all(r['source']['path'] == 'requirements/spec.md' for r in requirements))
        geometry = scope['checks'][3]['params']
        self.assertEqual((geometry['criterion'], geometry['min_track_width_mm']), ('copper-width/1', 0.2))
        rc = scope['checks'][4]['params']
        self.assertEqual(rc['netlist_check'], 'netlist')
        self.assertEqual(rc['measurements'][0], {'name': 'vout_1ms', 'at_ms': 1.0, 'min': 0.62, 'max': 0.65})
        self.assertEqual(scope['checks'][5]['params'], {'netlist_check': 'netlist'})
        # Paths name the empty-candidate scaffold, never the reference fixture.
        self.assertEqual(scope['inputs'][0], {'path': 'scaffold/project', 'at': 'project'})
        self.assertEqual(scope['board'], 'project/rung1_rc.kicad_pcb')
        self.assertNotIn('fixtures', json.dumps(scope))
        files = {'requirements/spec.md': hashlib.sha256(spec_raw).hexdigest(),
                 'requirements/record.json': hashlib.sha256(RECORD.read_bytes()).hexdigest(),
                 'project/rung1_rc.kicad_pcb': '0' * 64}
        self.assertEqual(compile_baseline(scope, files)['baseline_id'], receipt['baseline_id'])
        self.assertEqual(sum(1 for item in scope['limitations'] if item.startswith('Not checked: ')), 3)

    def test_removing_the_r_fab_mapping_is_an_unmapped_requirement(self):
        value = record()
        requirement(value, 'R-fab')['checks'] = []
        self.assertEqual(self.refused(value), {('unmapped_requirement', 'R-fab')})
        # The same edit applied to a hand-written scope passes compile_baseline silently,
        # because R-parts still covers the fab check; the compiler does not drop it.
        scope = compile_file(RECORD)[2]
        scope['baseline']['requirements'] = [r for r in scope['baseline']['requirements'] if r['id'] != 'R-fab']
        files = {'requirements/spec.md': '0' * 64, 'requirements/record.json': '0' * 64}
        self.assertIsNotNone(compile_baseline(scope, files))

    def test_unmapped_and_undeclared_checks_are_refused(self):
        value = record()
        for item in value['requirements']:
            item['checks'] = [c for c in item['checks'] if c != 'fab']
        self.assertEqual(self.refused(value), {('unmapped_requirement', 'R-fab'), ('unmapped_check', 'fab')})
        value = record()
        requirement(value, 'R-width')['checks'] = ['geometry', 'copper']
        self.assertEqual(self.refused(value), {('unknown_check', 'R-width')})

    def test_locators_must_be_unique_single_sentence_spec_quotes(self):
        value = record()
        requirement(value, 'R-width')['locator'] = 'Copper tracks must be at least 0.25 mm wide.'
        self.assertIn(('locator', 'R-width'), self.refused(value))
        value = record()
        requirement(value, 'R-fab')['locator'] = 'R1'
        self.assertIn(('locator', 'R-fab'), self.refused(value))
        value = record()
        requirement(value, 'R-width')['locator'] = 'must export. Copper tracks'
        self.assertIn(('locator', 'R-width'), self.refused(value))
        value = record()
        requirement(value, 'R-width')['locator'] = 'Copper  tracks must be at least 0.20 mm wide.'
        with self.assertRaises(ValueError):
            compiled(value)

    def test_every_spec_sentence_must_be_cited(self):
        value = record()
        value['unchecked'] = [n for n in value['unchecked'] if 'rectangle' not in n['locator']]
        self.assertEqual(self.refused(value), {('uncited_sentence', 'sentence[6]')})
        value = record()
        value['context'] = []
        self.assertEqual(self.refused(value), {('uncited_sentence', 'sentence[0]')})

    def test_declared_bounds_must_be_written_in_a_cited_quote(self):
        value = record()
        value['checks'][4]['bounds'][0]['max'] = 0.66
        self.assertEqual(self.refused(value), {('unsourced_bound', 'rc-step')})
        value = record()
        value['checks'][3]['min_track_width_mm'] = 0.25
        self.assertEqual(self.refused(value), {('unsourced_bound', 'geometry')})
        # A bound cited only through a different requirement's quote does not count.
        value = record()
        requirement(value, 'R-response')['checks'] = ['netlist']
        requirement(value, 'R-parts')['checks'] = ['netlist', 'rc-step', 'fab']
        self.assertEqual(self.refused(value), {('unsourced_bound', 'rc-step')})

    def test_spec_hash_and_prerequisite_order_are_bound(self):
        spec = SPEC.read_bytes() + b'\n'
        self.assertEqual(self.refused(record(), spec), {('spec_hash', 'spec')})
        value = record()
        value['checks'] = [value['checks'][5]] + value['checks'][:5]
        self.assertEqual(self.refused(value), {('prerequisite', 'fab')})

    def test_compiled_checks_must_be_statically_supported(self):
        value = record()
        value['checks'][4]['stop_ms'] = 0.5
        self.assertEqual(self.refused(value), {('unsupported_check', 'rc-step')})

    def test_malformed_records_are_refused_before_compilation(self):
        cases = []
        value = record()
        value['checks'][0]['params'] = {}
        cases.append(value)
        value = record()
        value['checks'][3]['kind'] = 'placement'
        cases.append(value)
        value = record()
        value['checks'][3]['min_track_width_mm'] = '0.2'
        cases.append(value)
        value = record()
        value['checks'][4]['step_v'] = True
        cases.append(value)
        value = record()
        value['extra'] = 1
        cases.append(value)
        value = record()
        value['schema'] = 'agent-ee.requirement-record/2'
        cases.append(value)
        value = record()
        value['requirements'].append(dict(value['requirements'][0]))
        cases.append(value)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError) as caught:
                compiled(case)
            self.assertNotIsInstance(caught.exception, Refused)
        with self.assertRaises(ValueError):
            compile_record(b'{"schema": "a", "schema": "b"}', SPEC.read_bytes())

    def test_cli_writes_a_new_directory_and_refuses_without_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'compiled'
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(main([str(RECORD), '--out', str(out)]), 0)
            self.assertEqual(json.loads(stdout.getvalue())['baseline_id'],
                             read_json(out / 'receipt.json')['baseline_id'])
            self.assertEqual((out / 'scope.json').read_bytes(), GOLDEN.read_bytes())
            self.assertEqual((out / 'scaffold/requirements/spec.md').read_bytes(), SPEC.read_bytes())
            self.assertEqual((out / 'scaffold/requirements/record.json').read_bytes(), RECORD.read_bytes())
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                main([str(RECORD), '--out', str(out)])
            self.assertEqual(caught.exception.code, 2)
            value = record()
            requirement(value, 'R-fab')['checks'] = []
            broken = Path(tmp) / 'broken.record.json'
            broken.write_text(json.dumps(value))
            (Path(tmp) / 'spec.md').write_bytes(SPEC.read_bytes())
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(main([str(broken), '--out', str(Path(tmp) / 'refused')]), 1)
            report = json.loads(stdout.getvalue())
            self.assertEqual([(f['code'], f['id']) for f in report['findings']], [('unmapped_requirement', 'R-fab')])
            self.assertFalse((Path(tmp) / 'refused').exists())


class ReferenceBoardTests(unittest.TestCase):
    """Checks-only runs on fixtures/rc/control: compiled scope versus the hand-written one."""

    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-requirement-scope-')
        root = Path(cls.tmp.name)
        out = root / 'compiled'
        with contextlib.redirect_stdout(io.StringIO()):
            assert main([str(RECORD), '--out', str(out)]) == 0
        cls.receipt = read_json(out / 'receipt.json')
        scope = read_json(out / 'scope.json')
        scope['inputs'][0]['path'] = str(REFERENCE)
        scope['inputs'][-1]['path'] = str(out / 'scaffold/requirements')
        scope['schematic'], scope['board'] = 'project/rc_filter.kicad_sch', 'project/rc_filter.kicad_pcb'
        scope['mutable_inputs'] = [scope['schematic'], scope['board']]
        control = read_json(CLOSED_LOOP)
        for item in control['inputs']:
            if not Path(item['path']).is_absolute():
                item['path'] = str((CLOSED_LOOP.parent / item['path']).resolve())
        cls.runs = {}
        for name, value in (('compiled', scope), ('control', control)):
            write_json(root / f'{name}.scope.json', value)
            run = root / f'{name}-run'
            run.mkdir()
            capture(root / f'{name}.scope.json', run)
            cls.runs[name] = run, {r['id']: r for r in run_checks(run, time.monotonic() + 900)}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def outcomes(self, name):
        return {cid: (r['execution'], r['outcome']) for cid, r in self.runs[name][1].items()}

    def test_native_checks_pass_and_match_the_hand_written_scope(self):
        compiled, control = self.outcomes('compiled'), self.outcomes('control')
        for cid in ('erc', 'drc', 'geometry', 'rc-step'):
            self.assertEqual(compiled[cid], ('ok', 'pass'), self.runs['compiled'][1][cid])
        for cid in ('erc', 'drc', 'netlist', 'geometry', 'rc-step'):
            self.assertEqual(compiled[cid], control[cid], cid)
        geometry = self.runs['compiled'][1]['geometry']['details']
        self.assertEqual(geometry['criterion'], 'copper-width/1')
        self.assertEqual(geometry['counts']['unsupported'], 0)
        response = self.runs['compiled'][1]['rc-step']['details']['measurements']
        self.assertEqual(response, self.runs['control'][1]['rc-step']['details']['measurements'])

    def test_fab_export_reports_exactly_the_three_unbound_bom_lines(self):
        run, results = self.runs['compiled']
        fab = results['fab']
        self.assertEqual((fab['execution'], fab['outcome']), ('unsupported', 'unknown'), fab)
        self.assertEqual([(p['reference'], p['reason']) for p in fab['details']['flagged_parts']],
                         [(ref, 'missing part-binding properties: LCSC, Manufacturer, Partnumber')
                          for ref in ('C1', 'J1', 'R1')])
        rows = list(csv.reader(io.StringIO((run / 'work/fab/fab.incomplete/bom.csv').read_text())))
        self.assertEqual([(row[0], row[6]) for row in rows[1:]], [('C1', ''), ('J1', ''), ('R1', '')])
        self.assertFalse((run / 'work/fab/fab').exists())

    def test_capture_reproduces_the_compiled_baseline_id(self):
        run = self.runs['compiled'][0]
        self.assertEqual(read_json(run / 'baseline.json')['baseline_id'], self.receipt['baseline_id'])


if __name__ == '__main__':
    unittest.main()
