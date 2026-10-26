"""Polarized-part pin-1 polarity: sealed-netlist fallback and supplemental evidence (AGENTEE-524 D1)."""
import copy
import hashlib
import json
import re
import shutil
import time
import unittest
from pathlib import Path

from agent_ee import cpl_rotation
from agent_ee.baseline import compile_baseline, freeze_evidence, verify_evidence
from agent_ee.candidate import capture
from agent_ee.checks import run_checks
from agent_ee.cpl_rotation import SCHEMA, feature_ids, validate_record
from agent_ee.storage import digest, read_json, write_json
from tests.receipt_diagnostics import ReceiptTemporaryDirectory
from tests.toolchain import require_toolchain

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/cpl-rotation'
EVIDENCE_REL = 'design/polarity/C910020.polarity.json'
RATIONALE = ('ENGINEERING RATIONALE (owned fixture): the manufacturer source states the cathode is the banded end; '
             'the captured package draws its band feature next to pad 1 and the symbol bar sits at pin 1.')


def strip_pinfunction(board_text, ref='D1'):
    """The board with `ref`'s pad pinfunction records removed (the acquisition situation)."""
    i = board_text.index(f'"{ref}"')
    start, end = board_text.rfind('(footprint', 0, i), board_text.find('(footprint', i)
    block = board_text[start:end]
    stripped = re.sub(r'\n\s*\(pinfunction "[^"]*"\)', '', block)
    assert stripped != block
    return board_text[:start] + stripped + board_text[end:]


def with_nets(board_text, ref='D1', nets=('/D1_1', '/D1_2')):
    """The owned board (which carries no nets) with `ref`'s pads 1 and 2 on the given net names,
    declared in the board net table so pcbnew loads them; the names are the fixture netlist's."""
    i = board_text.index(f'"{ref}"')
    start, end = board_text.rfind('(footprint', 0, i), board_text.find('(footprint', i)
    block = board_text[start:end]
    for pad, (code, name) in zip(('1', '2'), enumerate(nets, start=1)):
        block, count = re.subn(rf'(\(pad "{pad}"(?:(?!\(pad ).)*?\(layers "[^\n]*\))',
                               rf'\g<1>\n\t\t\t(net {code} "{name}")', block, count=1, flags=re.S)
        assert count == 1, pad
    first = board_text.index('(footprint')
    table = ''.join(f'\t(net {code} "{name}")\n' for code, name in enumerate(nets, start=1))
    return board_text[:first] + table + board_text[first:start] + block + board_text[end:]


def set_pinfunction(board_text, ref, pad, name):
    i = board_text.index(f'"{ref}"')
    start, end = board_text.rfind('(footprint', 0, i), board_text.find('(footprint', i)
    block = board_text[start:end]
    changed, count = re.subn(rf'(\(pad "{pad}"(?:(?!\(pad ).)*?\(pinfunction ")[^"]*(")', rf'\g<1>{name}\2', block,
                             count=1, flags=re.S)
    assert count == 1
    return board_text[:start] + changed + board_text[end:]


class PolarityCase(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        tmp = ReceiptTemporaryDirectory(prefix='agent-ee-cpl-polarity-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runs = 0

    def source(self, *, board=None, unnamed_supplier=False):
        """A fixture copy; optionally the board text replaced and C910020's symbol pins renamed 1/2."""
        self.runs += 1
        source = self.root / f'source{self.runs}'
        shutil.copytree(FIXTURE, source)
        if board is not None:
            (source / 'cpl.kicad_pcb').write_text(board)
        if unnamed_supplier:
            raw_path = source / 'supplier/C910020.raweasy.json'
            raw = json.loads(raw_path.read_bytes())
            shapes = []
            for shape in raw['dataStr']['shape']:
                if shape.startswith('P~'):
                    shape = shape.replace('~K~', '~1~').replace('~A~', '~2~')
                shapes.append(shape)
            raw['dataStr']['shape'] = shapes
            data = (json.dumps(raw, indent=1, sort_keys=True) + '\n').encode()
            raw_path.write_bytes(data)
            prov_path = source / 'supplier/C910020.provenance.json'
            prov = read_json(prov_path)
            prov.update(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
            write_json(prov_path, prov)
        return source

    def evidence(self, source, **overrides):
        raw = json.loads((source / 'supplier/C910020.raweasy.json').read_bytes())
        package_ids = sorted(feature_ids(raw['packageDetail']['dataStr']['shape']))
        symbol_ids = sorted(feature_ids(raw['dataStr']['shape']))
        record = {'schema': SCHEMA, 'lcsc': 'C910020', 'mpn': 'OFW-D1', 'manufacturer': 'Owned Fixture Works',
                  'supplier_capture_sha256': digest(source / 'supplier/C910020.raweasy.json'),
                  'pin_polarity': {'1': 'cathode', '2': 'anode'},
                  'manufacturer_source': {'path': 'design/requirements.md', 'sha256': digest(source / 'requirements.md'),
                                          'page': 1, 'locator': 'owned fixture requirements, polarity paragraph',
                                          'statement': 'Polarity: Cathode Band'},
                  'package_feature_ids': package_ids[:1], 'symbol_feature_ids': symbol_ids[:1],
                  'rationale': RATIONALE, 'limitation': 'supplier drawing fidelity is assumed, not verified'}
        record.update(overrides)
        return record

    def scope(self, source, evidence=None):
        scope = read_json(FIXTURE / 'control.scope.json')
        scope['schema'] = 'agent-ee.scope/3'
        scope['inputs'][0]['path'] = str(source)
        scope['baseline']['requirements'][0]['intent'] = 'acceptance'
        if evidence is not None:
            (source / 'polarity').mkdir(exist_ok=True)
            (source / 'polarity/C910020.polarity.json').write_text(json.dumps(evidence, indent=1, sort_keys=True) + '\n')
            scope['inputs'][0]['include'].append('polarity/C910020.polarity.json')
            next(c for c in scope['checks'] if c['kind'] == 'cpl_rotation')['params']['polarity_evidence'] = [EVIDENCE_REL]
        return scope

    def run_case(self, scope):
        case = self.root / f'case{self.runs}'
        case.mkdir()
        write_json(case / 'scope.json', scope)
        run_dir = case / 'run'
        run_dir.mkdir()
        capture(case / 'scope.json', run_dir)
        results = run_checks(run_dir, time.monotonic() + 300)
        freeze_evidence(run_dir, results)
        verify_evidence(run_dir, results, required=True)
        self.run_dir = run_dir
        return results[-1]

    def d1(self, result):
        report = read_json(self.run_dir / 'work/cpl/cpl-rotation.json')
        return next(r for r in report['designators'] if r['designator'] == 'D1')


class IdentityBoundaryTests(unittest.TestCase):
    """The identity prerequisites of both fallbacks: missing, empty or mismatched bindings never pass."""

    def setUp(self):
        from decimal import Decimal
        self.Decimal = Decimal
        self.board = strip_pinfunction((FIXTURE / 'cpl.kicad_pcb').read_text()).encode()
        self.found = cpl_rotation.board_footprints(self.board)
        raw = (FIXTURE / 'supplier/C910020.raweasy.json').read_bytes()
        self.suppliers = {'C910020': {'raw': raw, 'sha256': hashlib.sha256(raw).hexdigest(),
                                      'provenance': 'design/supplier/C910020.provenance.json'}}

    def pin1(self, *, netlist_lcsc='C910020', partnumber='OFW-D1', evidence=None, suppliers=None):
        record, notes = {'lcsc': 'C910020'}, []
        netlist = {'lcsc': netlist_lcsc, 'mpn': 'OFW-D1', 'pins': {'1': 'K', '2': 'A'},
                   'nets': {'1': '/D1_1', '2': '/D1_2'}}
        cpl_rotation._pin1(record, notes, self.found, None, 'D1', self.found['D1'], suppliers or self.suppliers,
                           self.Decimal(1000), self.Decimal(0), netlist=netlist,
                           pad_nets={'1': '/D1_1', '2': '/D1_2'}, partnumber=partnumber, evidence=evidence)
        return record['polarity'], [n['mode'] for n in notes], notes

    def test_netlist_fallback_needs_a_present_equal_lcsc_binding(self):
        polarity, modes, _ = self.pin1()
        self.assertEqual((polarity['board'], polarity['board_basis'], modes), ('cathode', 'sealed_netlist_libpart', []))
        for binding in (None, '', 'C999999'):
            with self.subTest(binding=binding):
                polarity, modes, notes = self.pin1(netlist_lcsc=binding)
                self.assertIsNone(polarity['board'])
                self.assertEqual(polarity['board_basis'], 'board_pad_pinfunction')
                self.assertEqual(modes, ['polarity_unknown'])
                self.assertIn('BOM binding' if not binding else 'the BOM to', notes[0]['message'])

    def test_evidence_needs_a_present_equal_bom_partnumber(self):
        raw = json.loads(self.suppliers['C910020']['raw'])
        unnamed = {**self.suppliers, 'C910020': {**self.suppliers['C910020']}}
        shapes = [sh.replace('~K~', '~1~').replace('~A~', '~2~') if sh.startswith('P~') else sh
                  for sh in raw['dataStr']['shape']]
        data = json.dumps({**raw, 'dataStr': {**raw['dataStr'], 'shape': shapes}}).encode()
        unnamed['C910020'].update(raw=data, sha256=hashlib.sha256(data).hexdigest())
        evidence = {'lcsc': 'C910020', 'mpn': 'OFW-D1', 'supplier_capture_sha256': unnamed['C910020']['sha256'],
                    'pin_polarity': {'1': 'cathode', '2': 'anode'}, 'path': 'design/polarity/C910020.polarity.json',
                    'manufacturer_source': {'path': 'design/requirements.md'}, 'package_feature_ids': ['d1'],
                    'symbol_feature_ids': ['gpin1'], 'limitation': 'fixture'}
        polarity, modes, _ = self.pin1(evidence=evidence, suppliers=unnamed)
        self.assertEqual((polarity['supplier'], polarity['supplier_basis'], modes),
                         ('cathode', 'supplemental_component_polarity_evidence', []))
        for partnumber in (None, '', 'OFW-D9'):
            with self.subTest(partnumber=partnumber):
                polarity, modes, notes = self.pin1(evidence=evidence, suppliers=unnamed, partnumber=partnumber)
                self.assertIsNone(polarity['supplier'])
                self.assertEqual(modes, ['polarity_unknown'])
                self.assertIn('Partnumber' if not partnumber else 'differs from BOM part', notes[0]['message'])


class NetlistFallbackTests(PolarityCase):
    def test_named_pad_pinfunction_path_is_unchanged(self):
        result = self.run_case(self.scope(self.source()))
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        d1 = self.d1(result)
        self.assertEqual((d1['polarity']['board'], d1['polarity']['supplier']), ('cathode', 'cathode'))
        self.assertEqual((d1['polarity']['board_basis'], d1['polarity']['supplier_basis']),
                         ('board_pad_pinfunction', 'supplier_symbol_pin_name'))
        self.assertIsNone(d1['polarity']['evidence'])

    def test_missing_pad_pinfunction_without_pad_nets_stays_unknown(self):
        board = strip_pinfunction((FIXTURE / 'cpl.kicad_pcb').read_text())
        result = self.run_case(self.scope(self.source(board=board)))
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        d1 = self.d1(result)
        self.assertIn('polarity_unknown', {m['mode'] for m in d1['modes']})
        self.assertIn('lacks the pin-1 net', d1['modes'][0]['message'])

    def test_missing_pad_pinfunction_falls_back_to_the_sealed_netlist(self):
        board = with_nets(strip_pinfunction((FIXTURE / 'cpl.kicad_pcb').read_text()))
        result = self.run_case(self.scope(self.source(board=board)))
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        d1 = self.d1(result)
        self.assertEqual(d1['polarity']['board'], 'cathode')
        self.assertEqual(d1['polarity']['board_basis'], 'sealed_netlist_libpart')
        self.assertEqual(d1['polarity']['board_pin1_function'], 'K')
        self.assertIn('work/netlist/design.xml', result['evidence'])

    def test_contradicting_pad_pinfunction_is_a_defect_not_overridden(self):
        board = set_pinfunction((FIXTURE / 'cpl.kicad_pcb').read_text(), 'D1', '1', 'A')
        result = self.run_case(self.scope(self.source(board=board)))
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        d1 = self.d1(result)
        modes = {m['mode'] for m in d1['modes']}
        self.assertIn('polarity_conflict', modes)
        self.assertEqual(d1['polarity']['board_basis'], 'board_pad_pinfunction')

    def test_swapped_pad_net_mapping_refuses_the_fallback(self):
        board = with_nets(strip_pinfunction((FIXTURE / 'cpl.kicad_pcb').read_text()), nets=('/D1_2', '/D1_1'))
        result = self.run_case(self.scope(self.source(board=board)))
        d1 = self.d1(result)
        self.assertIn('polarity_conflict', {m['mode'] for m in d1['modes']})
        self.assertEqual(result['outcome'], 'defect')


class SupplementalEvidenceTests(PolarityCase):
    def test_unnamed_supplier_pins_stay_unknown_without_evidence(self):
        result = self.run_case(self.scope(self.source(unnamed_supplier=True)))
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        d1 = self.d1(result)
        self.assertIn('polarity_unknown', {m['mode'] for m in d1['modes']})
        self.assertEqual(d1['polarity']['supplier_pin1_name'], '1')

    def test_valid_evidence_supplies_supplier_polarity_with_exposed_basis(self):
        source = self.source(unnamed_supplier=True)
        scope = self.scope(source, self.evidence(source))
        files = {f"design/{name}": digest(source / name) for name in scope['inputs'][0]['include']}
        descriptor = compile_baseline(scope, files)['coverage']['checks']['cpl']
        self.assertIn(EVIDENCE_REL, descriptor['immutable_inputs'])
        result = self.run_case(scope)
        from agent_ee.baseline import load_baseline
        sealed = load_baseline(self.run_dir)['coverage']['checks']['cpl']['immutable_inputs']
        self.assertIn('design/requirements.md', sealed)  # the manufacturer source is immutable too
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        d1 = self.d1(result)
        self.assertEqual((d1['polarity']['board'], d1['polarity']['supplier']), ('cathode', 'cathode'))
        self.assertEqual(d1['polarity']['supplier_basis'], 'supplemental_component_polarity_evidence')
        self.assertEqual(d1['polarity']['evidence']['path'], EVIDENCE_REL)
        self.assertEqual(d1['polarity']['evidence']['manufacturer_source']['statement'], 'Polarity: Cathode Band')
        self.assertIn('C910020', result['details']['polarity_evidence'])
        self.assertIn(EVIDENCE_REL, result['evidence'] + list(read_json(self.run_dir / 'candidate.json')['files']))

    def test_evidence_contradicting_named_supplier_polarity_is_a_defect(self):
        source = self.source()  # supplier symbol names K/A
        result = self.run_case(self.scope(source, self.evidence(source, pin_polarity={'1': 'anode', '2': 'cathode'})))
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertIn('polarity_conflict', {m['mode'] for m in self.d1(result)['modes']})

    def test_evidence_contradicting_board_polarity_is_a_defect(self):
        source = self.source(unnamed_supplier=True)
        result = self.run_case(self.scope(source, self.evidence(source, pin_polarity={'1': 'anode', '2': 'cathode'})))
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        d1 = self.d1(result)
        self.assertIn('polarity_conflict', {m['mode'] for m in d1['modes']})
        self.assertEqual((d1['polarity']['board'], d1['polarity']['supplier']), ('cathode', 'anode'))

    def test_wrong_mpn_keeps_polarity_unknown(self):
        source = self.source(unnamed_supplier=True)
        result = self.run_case(self.scope(source, self.evidence(source, mpn='OFW-D9')))
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
        d1 = self.d1(result)
        self.assertIn('polarity_unknown', {m['mode'] for m in d1['modes']})
        self.assertIn('MPN', d1['modes'][0]['message'])

    def test_stale_or_underspecified_evidence_refuses(self):
        source = self.source(unnamed_supplier=True)
        good = self.evidence(source)
        cases = {
            'stale capture digest': {'supplier_capture_sha256': '0' * 64},
            'stale manufacturer source': {'manufacturer_source': {**good['manufacturer_source'], 'sha256': '1' * 64}},
            'absent feature id': {'package_feature_ids': ['gge999999']},
            'unknown lcsc': {'lcsc': 'C910002', 'supplier_capture_sha256': digest(source / 'supplier/C910002.raweasy.json')},
            'thin rationale': {'rationale': 'band = cathode'},
            'no pin 1': {'pin_polarity': {'2': 'anode'}},
            'uniform polarity': {'pin_polarity': {'1': 'cathode', '2': 'cathode'}},
            'extra field': {'reviewer': 'x'},
        }
        suppliers = cpl_rotation.load_suppliers(lambda p: (source / p.split('/', 1)[1]).read_bytes(),
                                                ['design/supplier/C910020.provenance.json',
                                                 'design/supplier/C910002.provenance.json'])
        for name, change in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                validate_record({**copy.deepcopy(good), **change}, suppliers,
                                lambda p: (source / p.split('/', 1)[1]).read_bytes())
        # Capture refuses a stale record before any check runs.
        with self.assertRaisesRegex(ValueError, 'another supplier capture digest'):
            self.run_case(self.scope(source, {**good, 'supplier_capture_sha256': '0' * 64}))


if __name__ == '__main__':
    unittest.main()
