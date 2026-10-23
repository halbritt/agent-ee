"""AGENTEE-388 S1: manual source-bound physical intent compiler on the TJA1051 pin-map fixture."""
import copy
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from agent_ee import blocks, electrical_locality, locality_provenance, physical_intent, topology_rules
from agent_ee.baseline import freeze_evidence
from agent_ee.candidate import capture
from agent_ee.native_netlist import read_native_inventory, read_sheet_names
from agent_ee.checks import run_checks
from agent_ee.physical_intent import IntentRefused, compile_and_publish, compile_plan, verify_receipt
from agent_ee.storage import digest, json_bytes, read_json, write_json
from toolchain import fome_mc_store, require_toolchain

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'fixtures/datasheet-pins'
PAD = {'2': 'd384d75b-23f5-4869-a74b-fc10a1501f78', '3': '71a4f0ed-17a4-4ccd-b6b0-f2b36c7a75c6',
       '6': '25e34c9d-c7d8-4b9f-a397-bb8ea7ae0b30', '7': '5732b332-7e34-4444-bcc3-950537ac6288'}
# Original authored additions for the locality and defect boards (not library or third-party data).
C1_VCC, C1_GND = '7d0c3a51-2b8e-4e62-9a0f-3f1d5c2b9a01', '7d0c3a51-2b8e-4e62-9a0f-3f1d5c2b9a02'
DUP7 = '7d0c3a51-2b8e-4e62-9a0f-3f1d5c2b9a07'
C1 = f'''	(footprint "pins:C2"
		(layer "F.Cu")
		(uuid "7d0c3a51-2b8e-4e62-9a0f-3f1d5c2b9a00")
		(at 60 50)
		(property "Reference" "C1" (at 0 0 0) (layer "F.SilkS"))
		(pad "1" smd rect (at -1 0) (size 1 1) (layers "F.Cu" "F.Mask") (net "VCC") (uuid "{C1_VCC}"))
		(pad "2" smd rect (at 1 0) (size 1 1) (layers "F.Cu" "F.Mask") (net "GND") (uuid "{C1_GND}"))
	)
	(gr_rect'''
OWNER_TEXT = ('D-1 (owner of record): keep the C1 supply pad within 3 mm of U1 VCC.\n'
              'D-2 (owner of record): keep the C1 supply pad close to U1 VCC.\n'
              'D-3 (owner of record): keep the C1 supply pad within 3 mm, never beyond 5 mm, of U1 VCC.\n'
              'D-4 (owner of record): keep the C1 supply pad within 120 mil of U1 VCC.\n'
              'D-5 (Trent): keep the C1 supply pad within 999 mm of U1 VCC.\n'
              'D-6 (owner of record): keep the C1 supply pad within -3 mm of U1 VCC.\n'
              'D-7 (owner of record): keep the C1 supply pad within 1,003 mm of U1 VCC.\n'
              'D-8 (owner of record): keep the C1 supply pad within 3 mm and 999 MM of U1 VCC.\n'
              'D-9 (owner of record): keep the C1 supply pad within 3mm of U1 VCC.\n'
              'D-10 (owner of record): keep the C1 supply pad within 0.5 mm of U1 VCC.\n'
              'D-11 (owner of record): keep the C1 supply pad within 1/3 mm of U1 VCC.\n'
              'D-12 (owner of record): keep the C1 supply pad within 1\u2019234 mm of U1 VCC.\n'
              'D-13 (owner of record): keep the C1 supply pad within 3 mm and 999 yd of U1 VCC.\n')
# Review ee388c-numeric H1 sweep: every separator between two digits, before every unit, refuses
# unless the whole lexeme is a supported length (only ``1.3 mm``).
SEPARATORS = ('/', '\u2019', "'", ',', '.', ' ', '\u00a0', '\u2009')
SWEEP_UNITS = ('mm', 'MM', 'yd', 'ft', 'cm', 'um')
SWEEP = [(f'S-{i}', f'within 1{sep}3 {unit} of U1 VCC', sep, unit)
         for i, (sep, unit) in enumerate((s, u) for s in SEPARATORS for u in SWEEP_UNITS)]
OWNER_TEXT += ''.join(f'{locator} (owner of record): keep the C1 supply pad {quote}.\n' for locator, quote, _, _ in SWEEP)
LAYOUT_TEXT = 'Layout note L-2: place the decoupling capacitor within 3 mm of the VCC pin.\n'
# Review ee388e-510-r1r5 H2: authored datasheet layout notes; each line is one numeric record.
DATASHEET_LINES = {'N-1': ('within -3 mm', 3_000_000), 'N-2': ('within 1,003 mm', 3_000_000),
                   'N-3': ('within 1/3 mm', 3_000_000), 'N-4': ('within 1\u2019234 mm', 234_000_000),
                   'N-5': ('within 3 mm and 999 yd', 3_000_000), 'N-6': ('within 3 mm and 4 mm', 3_000_000),
                   'N-7': ('within 3 mm', 3_000_000), 'N-8': ('within 0.5 mm', 500_000)}
DATASHEET_TEXT = ''.join(f'Note {n}: place the capacitor {q} of the VCC pin.\n' for n, (q, _) in DATASHEET_LINES.items())
# Review ee388e-510-r1r5 H3: an authored unnetted mechanical footprint for footprint-identity boards.
H1_PAD = '7d0c3a51-2b8e-4e62-9a0f-3f1d5c2b9a10'
U1_UUID = '33863c18-80e0-4310-9450-9194c18489ae'
# An authored two-level hierarchy of the control fixture: U1 moves unchanged into child sheet
# ``can``; an authored root-sheet C1 with unconnected pins stays beside it.
ROOT_SHEET, CAN_SHEET, CAN_FILE = ('031a9db2-1b24-5396-99ec-b70acb857120', '5b2f6c1e-9a4d-4c3b-8e2f-1d7a6c5b4e02',
                                   '5b2f6c1e-9a4d-4c3b-8e2f-1d7a6c5b4e01')
TOP_SHEET = f'''(kicad_sch (version 20250114) (generator "eeschema") (uuid "{ROOT_SHEET}") (paper "A4")
  (lib_symbols (symbol "pins:C" (pin_numbers hide) (in_bom yes) (on_board yes)
    (property "Reference" "C" (at 0 3 0) (effects (font (size 1 1))))
    (property "Value" "C" (at 0 -3 0) (effects (font (size 1 1))))
    (property "Footprint" "pins:C2" (at 0 0 0) (effects (font (size 1 1)) hide))
    (symbol "C_1_1" (pin passive line (at -2.54 0 0) (length 1.27) (name "~" (effects (font (size 1 1))))
      (number "1" (effects (font (size 1 1)))))
      (pin passive line (at 2.54 0 180) (length 1.27) (name "~" (effects (font (size 1 1))))
      (number "2" (effects (font (size 1 1))))))))
  (no_connect (at 17.78 20.32) (uuid "5b2f6c1e-9a4d-4c3b-8e2f-1d7a6c5b4e04"))
  (no_connect (at 22.86 20.32) (uuid "5b2f6c1e-9a4d-4c3b-8e2f-1d7a6c5b4e05"))
  (symbol (lib_id "pins:C") (at 20.32 20.32 0) (unit 1) (in_bom yes) (on_board yes) (dnp no)
    (uuid "5b2f6c1e-9a4d-4c3b-8e2f-1d7a6c5b4e03")
    (property "Reference" "C1" (at 20.32 17 0) (effects (font (size 1 1))))
    (property "Value" "C" (at 20.32 24 0) (effects (font (size 1 1))))
    (property "Footprint" "pins:C2" (at 20.32 26 0) (effects (font (size 1 1)) hide))
    (instances (project "pins" (path "/{ROOT_SHEET}" (reference "C1") (unit 1)))))
  (sheet (at 60.96 30.48) (size 20.32 10.16) (stroke (width 0.1524) (type solid)) (fill (color 0 0 0 0.0000))
    (uuid "{CAN_SHEET}")
    (property "Sheetname" "can" (at 60.96 29.7684 0) (effects (font (size 1.27 1.27)) (justify left bottom)))
    (property "Sheetfile" "can.kicad_sch" (at 60.96 41.2246 0) (effects (font (size 1.27 1.27)) (justify left top)))
    (instances (project "pins" (path "/{ROOT_SHEET}" (page "2")))))
  (sheet_instances (path "/" (page "1"))))
'''


def mechanical(uuid):
    return f'''\t(footprint "pins:MH"
\t\t(layer "F.Cu")
\t\t(uuid "{uuid}")
\t\t(at 80 50)
\t\t(property "Reference" "H1" (at 0 0 0) (layer "F.SilkS"))
\t\t(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Mask") (uuid "{H1_PAD}"))
\t)
\t(gr_rect'''


def lock(board, form):
    """U1 with ``form`` inserted before its footprint UUID."""
    needle = f'(layer "F.Cu")\n\t\t(uuid "{U1_UUID}'
    assert needle in board
    return board.replace(needle, f'(layer "F.Cu")\n\t\t{form}\n\t\t(uuid "{U1_UUID}', 1)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def terminal(tid, pin, function, fact, **extra):
    return {'id': tid, 'reference': 'U1', 'pin': pin, 'pad_uuid': PAD[pin], 'function': function,
            'fact': fact, 'net': f'unconnected-(U1-{function}-Pad{pin})', 'class': 'ic',
            'requirement': 'pins', **extra}


class PhysicalIntentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True, tools=('pdftotext',))
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.control = cls.fixture_run('control')
        cls.swapped = cls.fixture_run('swapped')
        board = (FIXTURE / 'control/pins.kicad_pcb').read_text()
        local = (board.replace('(net "unconnected-(U1-VCC-Pad3)")', '(net "VCC")')
                 .replace('(net "unconnected-(U1-GND-Pad2)")', '(net "GND")')
                 .replace('\t(gr_rect', C1, 1))
        cls.local = cls.board_run('local', local, {'owner-decisions.md': OWNER_TEXT, 'layout-notes.md': LAYOUT_TEXT,
                                                   'datasheet-notes.md': DATASHEET_TEXT})
        dup = (f'\t\t(pad "7" smd rect (at 5 2) (size 1 1) (layers "F.Cu" "F.Mask") '
               f'(net "unconnected-(U1-CANH-Pad7)") (uuid "{DUP7}"))\n\t\t(embedded_fonts no)')
        defect = (board.replace('(layer "F.Cu")\n\t\t(uuid "33863c18', '(layer "F.Cu")\n\t\t(locked yes)\n\t\t(uuid "33863c18', 1)
                  .replace('\t\t(embedded_fonts no)', dup, 1))
        cls.defect = cls.board_run('defect', defect, {})
        # Child-sheet fixture: U1 in sheet /can/, C1 on the root sheet, both on the board.
        flat = (FIXTURE / 'control/pins.kicad_sch').read_text()
        child = (flat.replace(f'(uuid "{ROOT_SHEET}")', f'(uuid "{CAN_FILE}")', 1)
                 .replace(f'(path "/{ROOT_SHEET}" (reference "U1")', f'(path "/{ROOT_SHEET}/{CAN_SHEET}" (reference "U1")'))
        hier = (board.replace(f'(path "/{ROOT_SHEET}/', f'(path "/{CAN_SHEET}/', 1)
                .replace('\t(gr_rect', C1.replace('(net "VCC")', '(net "unconnected-(C1-Pad1)")')
                         .replace('(net "GND")', '(net "unconnected-(C1-Pad2)")'), 1))
        cls.hier = cls.board_run('hier', hier, {}, {'pins.kicad_sch': TOP_SHEET, 'can.kicad_sch': child})

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def checked(cls, scope_path, name):
        run = cls.root / name
        run.mkdir()
        capture(scope_path, run)
        results = run_checks(run, time.monotonic() + 120)
        freeze_evidence(run, results)
        return run

    @classmethod
    def fixture_run(cls, name):
        """The existing fixture scope, unchanged, captured and checked as a sealed run."""
        return cls.checked(FIXTURE / f'{name}.scope.json', name)

    @classmethod
    def board_run(cls, name, board, records, sheets=None):
        """The fixture with an authored board edit; native checks limited to netlist/facts/pin-map."""
        project = cls.root / f'{name}-src/project'
        shutil.copytree(FIXTURE / 'control', project)
        (project / 'pins.kicad_pcb').write_text(board)
        for rel, text in (sheets or {}).items():
            (project / rel).write_text(text)
        scope = read_json(FIXTURE / 'control.scope.json')
        scope['inputs'] = [{'path': str(project), 'at': 'project'}] + [
            {**i, 'path': str((FIXTURE / i['path']).resolve()) if not i['path'].startswith('/') else i['path']}
            for i in scope['inputs'] if i['at'] != 'project']
        for rel, text in records.items():
            (project.parent / rel).write_text(text)
            scope['inputs'].append({'path': str(project.parent / rel), 'at': rel})
        scope['checks'] = [c for c in scope['checks'] if c['kind'] not in ('erc', 'drc')]
        scope['baseline']['requirements'][0]['checks'] = ['netlist', 'facts', 'pin-map']
        write_json(project.parent / 'scope.json', scope)
        return cls.checked(project.parent / 'scope.json', name)

    def sources(self, run, records=()):
        manifest = read_json(run / 'candidate.json')
        return {'candidate_id': manifest['candidate_id'],
                'evidence_sha256': digest(run / 'work/verification/evidence.json'),
                'pin_map_check': 'pin-map', 'board_sha256': digest(run / 'inputs/project/pins.kicad_pcb'),
                'netlist_sha256': digest(run / 'work/netlist/design.xml'),
                'facts_sha256': digest(run / 'work/facts/facts.json'),
                'pin_map_sha256': digest(run / 'work/pin-map/pin-map.json'),
                **({'records': {r: manifest['files'][r] for r in records}} if records else {})}

    def intent(self, run=None, terminals=None, **extra):
        run = run or self.control
        return {'schema': physical_intent.SCHEMA, 'mode': 'manual',
                'sources': self.sources(run, extra.pop('records', ())),
                'terminals': terminals if terminals is not None else
                    [terminal('u1-canl', '6', 'CANL', 'canl'), terminal('u1-canh', '7', 'CANH', 'canh')],
                **extra}

    def refused(self, run, intent, pattern):
        out = self.root / f'out-{self.id().rsplit(".", 1)[-1]}-{len(list(self.root.glob("out-*")))}'
        with self.assertRaisesRegex(IntentRefused, pattern):
            compile_and_publish(run, intent, out)
        self.assertFalse(out.exists(), 'a refused intent must publish nothing')
        self.assertEqual([p for p in self.root.iterdir() if p.name.startswith(f'.{out.name}.')], [])

    # ------------------------------------------------------------ positive

    def test_correct_proposal_compiles_deterministic_digest_bound_package(self):
        intent = self.intent()
        started = time.monotonic()
        first = compile_plan(self.control, intent)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 10, 'provisional pure compilation budget is 10 s')
        self.assertEqual(json_bytes(first), json_bytes(compile_plan(self.control, copy.deepcopy(intent))))
        topology = first['contracts/topology-rules.json']
        self.assertEqual(topology['classes'], {'pins:TJA1051T': {'class': 'ic', 'terminals': {'CANL': '6', 'CANH': '7'}}})
        self.assertEqual([(r['type'], r['terminal']['pin'], r['net']) for r in topology['rules']],
                         [('pin_net', '6', 'unconnected-(U1-CANL-Pad6)'), ('pin_net', '7', 'unconnected-(U1-CANH-Pad7)')])
        receipt = first['receipt.json']
        self.assertEqual(receipt['sources'], {**intent['sources'], 'records': {}})
        self.assertEqual(receipt['intent_sha256'], physical_intent.identity(intent))
        self.assertEqual(receipt['compiler_sha256'], digest(physical_intent.__file__))
        self.assertEqual(receipt['artifacts'], {'contracts/topology-rules.json': physical_intent.identity(topology)})
        self.assertEqual(receipt['assertions']['u1-canl'], ['/contracts/topology-rules.json#/rules/0'])
        canl = receipt['terminals'][0]
        self.assertEqual((canl['pin'], canl['pad_uuid'], canl['fact_id'], canl['native_name'], canl['part_number']),
                         ('6', PAD['6'], 'canl', 'CANL', 'TJA1051T'))
        self.assertEqual(canl['fact_source']['page'], 4)
        self.assertIn('KICADROUTE-91', receipt['activation'])
        self.assertEqual(receipt['semantic_completion'], 'supported-subset')
        body = {k: v for k, v in receipt.items() if k != 'package_id'}
        self.assertEqual(receipt['package_id'], physical_intent.identity(body))
        # The package digest binds the inputs: a different intent yields a different package.
        other = self.intent(terminals=[terminal('u1-canl', '6', 'CANL', 'canl')])
        self.assertNotEqual(compile_plan(self.control, other)['receipt.json']['package_id'], receipt['package_id'])
        a, b = self.root / 'pkg-a', self.root / 'pkg-b'
        compile_and_publish(self.control, intent, a)
        compile_and_publish(self.control, intent, b)
        files = sorted(p.relative_to(a).as_posix() for p in a.rglob('*') if p.is_file())
        self.assertEqual(files, ['contracts/topology-rules.json', 'receipt.json'])
        self.assertTrue(all((a / f).read_bytes() == (b / f).read_bytes() for f in files))
        self.assertEqual(verify_receipt(a, self.control, intent)['package_id'], receipt['package_id'])
        with self.assertRaisesRegex(IntentRefused, 'exists'):
            compile_and_publish(self.control, intent, a)
        tampered = read_json(a / 'contracts/topology-rules.json')
        tampered['rules'][0]['terminal']['pin'] = '7'
        write_json(a / 'contracts/topology-rules.json', tampered)
        with self.assertRaisesRegex(IntentRefused, 'differs from recompilation'):
            verify_receipt(a, self.control, intent)
        (b / 'extra.json').write_text('{}')
        with self.assertRaisesRegex(IntentRefused, 'files differ'):
            verify_receipt(b, self.control, intent)

    # ----------------------------------------------------- semantic pin join

    def test_pin7_claiming_canl_is_rejected_before_publication(self):
        # Native U1: pin 6 = CANL, pin 7 = CANH; the verified CANL fact binds pin 6.
        wrong = terminal('u1-canl', '7', 'CANL', 'canl', net='unconnected-(U1-CANH-Pad7)')
        self.refused(self.control, self.intent(terminals=[wrong]),
                     'verified CANL fact binds U1 pin 6, not proposed pin 7')
        # Citing the pin-7 fact under the wrong function also refuses.
        self.refused(self.control, self.intent(terminals=[terminal('x', '7', 'CANL', 'canh')]),
                     "states 'CANH', not 'CANL'")

    def test_unverified_inapplicable_or_defective_pin_evidence_refuses(self):
        self.refused(self.control, self.intent(terminals=[terminal('x', '6', 'CANL', 'missing')]),
                     'not a verified pin_function fact')
        # The native swapped symbol is a pin_map defect: no function can bind on it.
        self.refused(self.swapped, self.intent(self.swapped), 'pin_map evidence is not a complete pass')

    def test_duplicate_pins_and_pad_numbers_refuse(self):
        dup = [terminal('a', '6', 'CANL', 'canl'), terminal('b', '6', 'CANL', 'canl')]
        self.refused(self.control, self.intent(terminals=dup), 'duplicate terminal pin')
        self.refused(self.defect, self.intent(self.defect, terminals=[terminal('h', '7', 'CANH', 'canh')]),
                     'has 2 pads numbered 7')
        # Pin 6 is unique on that board and still compiles there.
        self.assertIn('receipt.json', compile_plan(self.defect, self.intent(
            self.defect, terminals=[terminal('l', '6', 'CANL', 'canl')])))

    def test_stale_source_and_pad_uuid_refuse(self):
        self.refused(self.control, self.intent(terminals=[terminal('x', '6', 'CANL', 'canl', pad_uuid=PAD['7'])]),
                     'stale pad UUID')
        swapped_uuid = 'e47875a2-f4f8-4b90-96b9-b0613bc5f1c3'  # pad 6 of the other board
        self.refused(self.control, self.intent(terminals=[terminal('x', '6', 'CANL', 'canl', pad_uuid=swapped_uuid)]),
                     'stale pad UUID')
        intent = self.intent()
        self.refused(self.local, intent, 'stale source: candidate identity')
        for key in ('board_sha256', 'netlist_sha256', 'facts_sha256', 'pin_map_sha256', 'evidence_sha256'):
            with self.subTest(key=key):
                stale = copy.deepcopy(intent)
                stale['sources'][key] = '0' * 64
                self.refused(self.control, stale, 'stale source')
        tampered = self.root / 'tampered'
        shutil.copytree(self.control, tampered, symlinks=True)
        xml = tampered / 'work/netlist/design.xml'
        xml.write_text(xml.read_text().replace('CANL', 'CANX'))
        self.refused(tampered, intent, 'review run evidence is not intact')

    def test_failed_topology_and_requirement_scope_refuse(self):
        wrong_net = terminal('x', '6', 'CANL', 'canl', net='unconnected-(U1-CANH-Pad7)')
        self.refused(self.control, self.intent(terminals=[wrong_net]), 'failed topology')
        self.refused(self.control, self.intent(terminals=[terminal('x', '6', 'CANL', 'canl', requirement='invented')]),
                     'not a baseline requirement')

    def test_conflicting_membership_and_fixed_moves_refuse(self):
        blocks = [{'id': 'can', 'references': ['U1']}, {'id': 'power', 'references': ['C1', 'U1']}]
        self.refused(self.local, self.intent(self.local, blocks=blocks), 'conflicting membership')
        self.refused(self.defect, self.intent(self.defect, terminals=[terminal('l', '6', 'CANL', 'canl')],
                                              moves=[{'reference': 'U1', 'allow': ['translate']}]),
                     'conflicting fixed move: native U1 is locked')
        moves = compile_plan(self.local, self.intent(self.local, moves=[{'reference': 'C1',
                                                                       'allow': ['translate', 'rotate']}]))
        self.assertEqual(moves['receipt.json']['allowed_moves'], [{'reference': 'C1', 'allow': ['rotate', 'translate']}])

    # ------------------------------------------------------------- locality

    def pair(self, provenance=None, limit=3_000_000, net='VCC'):
        pair = {'from': {'reference': 'U1', 'pad': '3', 'net': net, 'uuid': PAD['3']},
                'to': {'reference': 'C1', 'pad': '1', 'net': net, 'uuid': C1_VCC},
                'max_distance_nm': limit, 'citation': 'authored test limit'}
        if provenance:
            pair['provenance'] = provenance
        return pair

    def locality(self, supply):
        # Review 510 R1: every emitted role is executable, so the return pair declares its bound too.
        ground = {'from': {'reference': 'U1', 'pad': '2', 'net': 'GND', 'uuid': PAD['2']},
                  'to': {'reference': 'C1', 'pad': '2', 'net': 'GND', 'uuid': C1_GND},
                  'max_distance_nm': 3_000_000, 'citation': 'authored test limit'}
        return [{'id': 'u1-decoupling', 'metric': 'pad-centre-to-centre', 'supply': supply, 'return': ground}]

    def owner(self, owner='owner of record', text=OWNER_TEXT, locator='D-1', quote='within 3 mm of U1 VCC'):
        return {'class': 'owner', 'basis': locator, 'decision_reference': {
            'path': 'owner-decisions.md', 'sha256': sha(text), 'locator': locator,
            'quote': quote, 'owner': owner}}

    def datasheet(self, value_nm=3_000_000):
        return {'class': 'datasheet', 'basis': 'L-2', 'derivation': {'kind': 'numeric', 'value_nm': value_nm, 'source': {
            'path': 'layout-notes.md', 'sha256': sha(LAYOUT_TEXT), 'locator': 'L-2', 'quote': 'within 3 mm'}}}

    def test_sourced_locality_compiles_unchanged_without_guessed_semantics(self):
        records = ('layout-notes.md', 'owner-decisions.md')
        # AGENTEE-348 H4 migration: a manufacturer bound is a feasibility limit only when cited.
        manufacturer = {'class': 'manufacturer', 'basis': 'captured JLCPCB record', 'citation': {
            'kind': 'manufacturer_source', 'manufacturer': 'Original fixture fabricator', 'path': 'layout-notes.md',
            'sha256': sha(LAYOUT_TEXT), 'locator': 'L-2', 'quote': 'within 3 mm'}}
        for provenance, expected in ((self.datasheet(), 'hard'), (None, 'advisory'), (manufacturer, 'feasibility')):
            with self.subTest(provenance=provenance and provenance['class']):
                authored = self.locality(self.pair(provenance))
                intent = self.intent(self.local, locality=copy.deepcopy(authored), records=records)
                out = compile_plan(self.local, intent)
                document = out['contracts/electrical-locality.json']
                # locality/1 has no fact or function fields: the authored supply/return is emitted as written.
                self.assertEqual(document['obligations'], authored)
                self.assertEqual(set(document['source_records']), set(records))
                self.assertEqual(out['receipt.json']['locality_qualification'][0]['enforcement'], expected)
                self.assertIn('/contracts/electrical-locality.json#/obligations/0',
                              out['receipt.json']['assertions']['u1-decoupling'])
                # The compiled package is a snapshot: later edits of the caller's intent do not reach it.
                intent['locality'][0]['supply']['max_distance_nm'] = 1
                self.assertEqual(document['obligations'], authored)
                self.assertEqual(out['receipt.json']['artifacts']['contracts/electrical-locality.json'],
                                 physical_intent.identity(document))

    def test_owner_maximum_is_bound_but_never_hard(self):
        # COORDINATOR RULING (agent-449, H1): no in-process owner authentication exists until
        # AGENTEE-348, so a sourced owner maximum compiles to authority_unavailable, never hard.
        records = ('owner-decisions.md',)
        authored = self.locality(self.pair(self.owner()))
        out = compile_plan(self.local, self.intent(self.local, locality=copy.deepcopy(authored), records=records))
        supply = out['contracts/electrical-locality.json']['obligations'][0]['supply']
        self.assertEqual(supply['max_distance_nm'], 3_000_000)
        self.assertEqual(supply['provenance']['authority'], 'authority_unavailable')
        self.assertIn('AGENTEE-348', supply['provenance']['authority_reason'])
        self.assertNotIn('decision_reference', supply['provenance'])
        self.assertEqual(supply['provenance']['unauthenticated_decision_reference'],
                         authored[0]['supply']['provenance']['decision_reference'])
        # The emitted contract never grades as an enforcing owner maximum downstream.
        source_records = out['contracts/electrical-locality.json']['source_records']
        self.assertEqual(locality_provenance.classify(supply, source_records)['enforcement'], 'advisory')
        qualification = out['receipt.json']['locality_qualification'][0]
        self.assertEqual((qualification['class'], qualification['enforcement'], qualification['owner'],
                          qualification['stated_max_distance_nm']),
                         ('owner', 'authority_unavailable', 'owner of record', 3_000_000))
        self.assertIn('AGENTEE-348', qualification['reason'])
        self.assertTrue(any('authority_unavailable' in x for x in out['receipt.json']['limitations']))
        # A captured decision stated by someone else is bound to that identity, still never hard.
        other = compile_plan(self.local, self.intent(self.local, records=records, locality=self.locality(self.pair(
            self.owner(owner='Trent', locator='D-5', quote='within 999 mm of U1 VCC'), limit=999_000_000))))
        self.assertEqual(other['receipt.json']['locality_qualification'][0]['enforcement'], 'authority_unavailable')

    def test_owner_identity_must_be_stated_by_the_captured_decision(self):
        records = ('owner-decisions.md',)
        cases = [(self.owner(owner='Mallory'), "stated by 'owner of record', not 'Mallory'"),  # review H1 probe
                 (self.owner(owner='Trent'), "stated by 'owner of record', not 'Trent'"),
                 (self.owner(owner='owner'), "stated by 'owner of record', not 'owner'"),
                 (self.owner(locator='D-9'), 'states no single decision D-9')]
        for provenance, pattern in cases:
            with self.subTest(provenance=provenance['decision_reference']):
                self.refused(self.local, self.intent(self.local, locality=self.locality(self.pair(provenance)),
                                                     records=records), pattern)

    def test_owner_maximum_must_equal_the_decided_value(self):
        records = ('owner-decisions.md',)
        cases = [(self.pair(self.owner(), limit=999_000_000), 'states 3000000 nm, not declared max_distance_nm 999000000'),
                 (self.pair(self.owner(), limit=2_000_000), 'states 3000000 nm, not declared'),
                 (self.pair(self.owner(locator='D-2', quote='close to U1 VCC')), 'states no single resolvable maximum'),
                 (self.pair(self.owner(locator='D-3', quote='never beyond 5 mm')), 'states no single resolvable maximum'),
                 (self.pair(self.owner(locator='D-4', quote='within 120 mil')), 'unit that cannot be resolved')]
        missing = self.pair(self.owner())
        del missing['max_distance_nm']
        cases.append((missing, 'no max_distance_nm is declared'))
        for pair, pattern in cases:
            with self.subTest(pattern=pattern):
                self.refused(self.local, self.intent(self.local, locality=self.locality(pair), records=records),
                             f'numeric mismatch: .*{pattern}')

    def test_owner_lengths_resolve_as_complete_lexemes(self):
        # Review ee388b-hold H1: a signed, grouped or second unresolved length is refused whole,
        # never read as a positive suffix (3 mm) or dropped, although owner maxima stay advisory.
        records = ('owner-decisions.md',)
        cases = [('D-6', 'within -3 mm of U1 VCC', "unresolvable length '-3 mm'"),
                 ('D-7', 'within 1,003 mm of U1 VCC', "unresolvable length '1,003 mm'"),
                 ('D-8', 'within 3 mm and 999 MM of U1 VCC', "length '999 MM' in a unit that cannot be resolved"),
                 # Review ee388c-numeric H1: a fraction, U+2019 grouping and a second unsupported length.
                 ('D-11', 'within 1/3 mm of U1 VCC', "unresolvable length '1/3 mm'"),
                 ('D-12', 'within 1\u2019234 mm of U1 VCC', "unresolvable length '1\u2019234 mm'"),
                 ('D-13', 'within 3 mm and 999 yd of U1 VCC', "length '999 yd' in a unit that cannot be resolved")]
        for locator, quote, pattern in cases:
            with self.subTest(locator=locator):
                self.refused(self.local, self.intent(self.local, records=records, locality=self.locality(
                    self.pair(self.owner(locator=locator, quote=quote)))), f'numeric mismatch: .*{pattern}')
        # Complete supported lexemes still bind their exact stated value.
        for locator, quote, nm in (('D-1', 'within 3 mm of U1 VCC', 3_000_000),
                                   ('D-9', 'within 3mm of U1 VCC', 3_000_000),
                                   ('D-10', 'within 0.5 mm of U1 VCC', 500_000)):
            with self.subTest(locator=locator):
                out = compile_plan(self.local, self.intent(self.local, records=records, locality=self.locality(
                    self.pair(self.owner(locator=locator, quote=quote), limit=nm))))
                qualification = out['receipt.json']['locality_qualification'][0]
                self.assertEqual((qualification['enforcement'], qualification['stated_max_distance_nm']),
                                 ('authority_unavailable', nm))
        stated = physical_intent._stated_lengths
        for text in ('2-3 mm', '\u2212 3 mm', '1 003 mm', '1\u2009003 mm', '003 mm', '1e3 mm', '3. mm', '.5 mm',
                     'x3 mm', '\uff13 mm', '3 millimetres', '3 mm\u00b2', '3 Mm', '3"', '3 mm (3000 um)',
                     '1/3 mm', '1\u2019234 mm', '3 mm and 999 yd', '3 mm and 999 mms', '1^3 mm', '3\u00a0mm',
                     '3MM', '3yd', 'mm3 or 3 mm', '3 mm, not 3', "U1's 3 mm", '3 more pads', '(3 mm)', '+3 mm',
                     '- 3 mm', '3 mm-5 mm', '\u0663 mm', '\u00bd mm', 'U\u0661 3 mm'):
            with self.subTest(text=text), self.assertRaisesRegex(IntentRefused, 'numeric mismatch'):
                stated(f'within {text} of U1 VCC', 'D-0', 'probe')
        # Digits inside identifiers are allowlisted; a sentence mark may close a length or identifier.
        self.assertEqual(stated('keep C128, R-0, D-1 and U1-VCC within 3 mm. Then 3mm; U1:', 'D-0', 'probe'),
                         [('3', 'mm'), ('3', 'mm')])

    def test_owner_length_separator_unit_sweep(self):
        # Review ee388c-numeric H1: end to end, each separator x unit form refuses with no output;
        # the one complete supported lexeme (1.3 mm) binds its whole value, never a suffix.
        records = ('owner-decisions.md',)
        for locator, quote, sep, unit in SWEEP:
            with self.subTest(quote=quote):
                pair = lambda nm: self.locality(self.pair(self.owner(locator=locator, quote=quote), limit=nm))
                self.refused(self.local, self.intent(self.local, records=records, locality=pair(3_000_000)),
                             'numeric mismatch')
                if (sep, unit) == ('.', 'mm'):
                    out = compile_plan(self.local, self.intent(self.local, records=records, locality=pair(1_300_000)))
                    self.assertEqual(out['receipt.json']['locality_qualification'][0]['stated_max_distance_nm'],
                                     1_300_000)

    def test_spoofed_owner_authority_refuses(self):
        records = ('owner-decisions.md',)
        cases = [(self.owner(owner='agent-449'), 'owner authority not established'),
                 (self.owner(owner='design owner'), 'owner authority not established'),
                 ({**self.owner(), 'decision_reference': {**self.owner()['decision_reference'],
                                                          'quote': 'owner approved 9 mm'}},
                  'owner authority not established'),
                 (self.owner(text='forged decision text'), 'stale source record')]
        for provenance, pattern in cases:
            with self.subTest(pattern=pattern, provenance=provenance['decision_reference']):
                self.refused(self.local, self.intent(self.local, locality=self.locality(self.pair(provenance)),
                                                     records=records), pattern)
        # An owner reference to a record the run did not capture is not authority either.
        self.refused(self.local, self.intent(self.local, locality=self.locality(self.pair(self.owner()))),
                     'owner authority not established')
        forged = self.intent(self.local, locality=self.locality(self.pair(self.owner())), records=records)
        forged['sources']['records']['owner-decisions.md'] = '1' * 64
        self.refused(self.local, forged, 'stale source: record owner-decisions.md')

    def test_numeric_mismatch_refuses(self):
        records = ('layout-notes.md',)
        self.refused(self.local, self.intent(self.local, records=records,
                                             locality=self.locality(self.pair(self.datasheet(), limit=2_000_000))),
                     'numeric mismatch')
        self.refused(self.local, self.intent(self.local, records=records,
                                             locality=self.locality(self.pair(self.datasheet(value_nm=2_000_000),
                                                                              limit=2_000_000))),
                     'numeric mismatch')

    def test_locality_endpoints_bind_native_pads(self):
        stale = self.pair()
        stale['to']['uuid'] = C1_GND
        self.refused(self.local, self.intent(self.local, locality=self.locality(stale)), 'stale pad UUID')
        self.refused(self.local, self.intent(self.local, locality=self.locality(self.pair(net='GND'))),
                     "native pad net 'VCC' differs")
        bare = self.pair()
        del bare['from']['uuid']
        self.refused(self.local, self.intent(self.local, locality=self.locality(bare)), 'captured native pad uuid')

    def stale_package(self, run, complete, incomplete, contract, document, pattern):
        """A package a laxer compiler published for ``incomplete`` fails verify_receipt."""
        compiled = compile_plan(run, complete)
        compiled[contract] = document
        out = physical_intent.publish(compiled, self.root / f'stale-{self.id().rsplit(".", 1)[-1]}-'
                                                            f'{len(list(self.root.glob("stale-*")))}')
        with self.assertRaisesRegex(IntentRefused, pattern):
            verify_receipt(out, run, incomplete)

    def test_complete_locality_executes_in_the_real_consumers(self):
        # Review 510 R1 positive: the emitted contract builds the real PlacementIndex and grades
        # every role against a native placement capture of the same sealed board.
        from agent_ee.placement_probe import extract
        records = ('layout-notes.md',)
        out = self.root / 'locality-consumer'
        intent = self.intent(self.local, records=records, locality=self.locality(self.pair(self.datasheet())))
        compile_and_publish(self.local, intent, out)
        verify_receipt(out, self.local, intent)
        document = read_json(out / 'contracts/electrical-locality.json')
        index = electrical_locality.PlacementIndex(document)
        self.assertEqual(sorted((own['pad'], peer['reference'], limit, hard) for own, peer, limit, hard
                                in index.pairs['U1']), [('2', 'C1', 3_000_000, False), ('3', 'C1', 3_000_000, True)])
        probe = extract(self.local / 'inputs/project/pins.kicad_pcb')
        graded = electrical_locality.grade(probe, document)
        self.assertEqual(graded['unknown'], [])
        self.assertEqual(sorted(r['role'] for r in graded['measured']), ['return', 'supply'])
        # The authored fixture puts C1 about 14 mm from U1: the hard datasheet supply bound is a
        # determinate defect and the unsourced return stays an advisory, both from native geometry.
        self.assertEqual(graded['status'], 'defect')
        self.assertEqual([(d['kind'], d['role']) for d in graded['defects']], [('electrical_locality_distance', 'supply')])
        self.assertEqual([(a['role'], a['exceeded']) for a in graded['advisories']], [('return', True)])

    def test_incomplete_or_ungradable_locality_refuses(self):
        # Review 510 R1 / review ee388d H1: a role without its bound crashed PlacementIndex after
        # publication and receipt verification. It now refuses before output and at verify_receipt.
        for key in ('max_distance_nm', 'citation'):
            with self.subTest(missing=key):
                incomplete = self.locality(self.pair())
                del incomplete[0]['return'][key]
                self.refused(self.local, self.intent(self.local, locality=incomplete),
                             f'locality u1-decoupling.return: incomplete locality: no declared {key}')
                bad = self.intent(self.local, locality=incomplete)
                document = {'schema': electrical_locality.SCHEMA, 'obligations': incomplete}
                self.stale_package(self.local, self.intent(self.local, locality=self.locality(self.pair())), bad,
                                   'contracts/electrical-locality.json', document, 'incomplete locality')
        unbounded = self.locality(self.pair())
        del unbounded[0]['return']['max_distance_nm']
        with self.assertRaises(KeyError):  # the actual consumer failure the refusal prevents
            electrical_locality.PlacementIndex({'schema': electrical_locality.SCHEMA, 'obligations': unbounded})
        metric = self.locality(self.pair())
        metric[0]['metric'] = 'routed-length'
        self.refused(self.local, self.intent(self.local, locality=metric), "unsupported metric 'routed-length'")
        mixed = self.locality(self.pair())
        mixed[0]['return']['to'] = {'reference': 'C1', 'pad': '1', 'net': 'VCC', 'uuid': C1_VCC}
        mixed[0]['supply']['to'] = {'reference': 'C1', 'pad': '2', 'net': 'GND', 'uuid': C1_GND}
        self.refused(self.local, self.intent(self.local, locality=mixed), 'endpoints declare different nets')

    def test_invented_layout_scale_never_becomes_hard(self):
        # Review 510 R2 / review ee388d H2: a captured 3 mm quote plus an authored 1 nm/unit scale
        # compiled to a hard 1 nm datasheet maximum. Layout derivations now refuse before output.
        records = ('layout-notes.md',)
        layout = self.datasheet()
        layout['derivation'].update(kind='layout', distance_units=1, scale_nm_per_unit=1,
                                    scale_reference='invented scale')
        del layout['derivation']['value_nm']
        invented = self.locality(self.pair(layout, limit=1))
        bad = self.intent(self.local, records=records, locality=invented)
        self.refused(self.local, bad, "unverifiable derivation kind 'layout'")
        # A plausible scale is still authored prose, not captured evidence.
        plausible = copy.deepcopy(layout)
        plausible['derivation'].update(distance_units=30, scale_nm_per_unit=100_000,
                                       scale_reference='3 mm scale bar measures 30 units')
        self.refused(self.local, self.intent(self.local, records=records,
                                             locality=self.locality(self.pair(plausible))),
                     "unverifiable derivation kind 'layout'")
        document = {'schema': electrical_locality.SCHEMA, 'obligations': invented,
                    'source_records': {'layout-notes.md': LAYOUT_TEXT}}
        # AGENTEE-348 round-2 H3 (was 'hard'): the shared classifier no longer grants an
        # authored layout scale authority; the captured quote does not state 1 nm.
        self.assertEqual(locality_provenance.classify(invented[0]['supply'], document['source_records'])['enforcement'],
                         'advisory')
        self.stale_package(self.local, self.intent(self.local, records=records,
                                                   locality=self.locality(self.pair(self.datasheet()))),
                           bad, 'contracts/electrical-locality.json', document, "derivation kind 'layout'")
        # The valid numeric positive keeps its hard maximum; its mismatch controls are unchanged.
        out = compile_plan(self.local, self.intent(self.local, records=records,
                                                   locality=self.locality(self.pair(self.datasheet()))))
        self.assertEqual(out['receipt.json']['locality_qualification'][0]['enforcement'], 'hard')

    def test_allocation_is_admitted_by_actual_block_derivation(self):
        # Review 510 R3 / review ee388d H3: a locked U1 compiled into a block without a moves entry,
        # although blocks.derive refuses it. Admission now runs the actual derive.
        locked = self.intent(self.defect, terminals=[terminal('l', '6', 'CANL', 'canl')],
                             blocks=[{'id': 'locked', 'references': ['U1']}])
        self.refused(self.defect, locked, 'refused by blocks.derive: locked footprints cannot be rigid-block members')
        unlocked = self.intent(terminals=[terminal('l', '6', 'CANL', 'canl')],
                               blocks=[{'id': 'locked', 'references': ['U1']}])
        self.stale_package(self.defect, self.intent(self.defect, terminals=[terminal('l', '6', 'CANL', 'canl')]),
                           locked, 'contracts/block-allocation.json',
                           {'schema': blocks.ALLOCATION, 'blocks': [{'id': 'locked', 'references': ['U1']}]},
                           'locked footprints')
        # A netted board-only footprint is not an on-board native component.
        self.refused(self.local, self.intent(self.local, blocks=[{'id': 'power', 'references': ['C1', 'U1']}]),
                     'refused by blocks.derive: C1: board footprint is not an on-board native component')
        # Positive: the compiled allocation survives the actual derive on a pcbnew placement capture.
        from agent_ee.placement_probe import extract
        intent = self.intent(blocks=[{'id': 'can', 'references': ['U1'], 'anchor': 'U1'}])
        out = self.root / 'allocation-consumer'
        compile_and_publish(self.control, intent, out)
        receipt = verify_receipt(out, self.control, intent)
        raw = (out / 'contracts/block-allocation.json').read_bytes()
        self.assertEqual(read_json(out / 'contracts/block-allocation.json'),
                         {'schema': blocks.ALLOCATION, 'blocks': [{'id': 'can', 'references': ['U1'], 'anchor': 'U1'}]})
        netlist = self.control / 'work/netlist/design.xml'
        derived = blocks.derive(read_native_inventory(netlist), read_sheet_names(netlist),
                                extract(self.control / 'inputs/project/pins.kicad_pcb'),
                                inventory_sha256=digest(netlist), allocation_raw=raw)
        self.assertEqual({k: derived[k] for k in ('allocation_sha256', 'blocks', 'residual')},
                         receipt['allocation_derivation'])
        self.assertEqual(derived['blocks'][0]['references'], ['U1'])
        self.assertIn('receipt.json', compile_plan(self.control, unlocked))

    def test_child_sheet_membership_is_admitted_by_actual_derivation(self):
        # Native hierarchy (review ee388h gap): U1 sits in child sheet /can/ of a real KiCad
        # export; C1 is on the root sheet. A child-sheet component in neither a block nor the
        # declared residual set is refused by the actual blocks.derive, before output.
        netlist = self.hier / 'work/netlist/design.xml'
        self.assertEqual({r: c['sheet'] for r, c in read_native_inventory(netlist)['components'].items()},
                         {'C1': '/', 'U1': f'/{CAN_SHEET}/'})
        self.assertEqual(read_sheet_names(netlist), {'/': '/', f'/{CAN_SHEET}/': '/can/'})
        power = [{'id': 'power', 'references': ['C1']}]
        self.refused(self.hier, self.intent(self.hier, blocks=power),
                     'child-sheet components neither allocated to a block nor declared residual: U1')
        self.stale_package(self.hier, self.intent(self.hier), self.intent(self.hier, blocks=power),
                           'contracts/block-allocation.json', {'schema': blocks.ALLOCATION, 'blocks': power},
                           'neither allocated to a block nor declared residual: U1')
        # Declaring U1 residual, or allocating it to a block, compiles; derive agrees on pcbnew.
        from agent_ee.placement_probe import extract
        probe = extract(self.hier / 'inputs/project/pins.kicad_pcb')
        for name, extra, expected_blocks, expected_residual in (
                ('residual', {'blocks': power, 'residual': ['U1']}, [('power', ['C1'])],
                 [{'reference': 'U1', 'reason': 'allocated-residual'}]),
                ('block', {'blocks': [{'id': 'can', 'references': ['U1']}]}, [('can', ['U1'])],
                 [{'reference': 'C1', 'reason': 'root-sheet'}])):
            with self.subTest(name):
                intent = self.intent(self.hier, **extra)
                out = self.root / f'hier-{name}'
                compile_and_publish(self.hier, intent, out)
                receipt = verify_receipt(out, self.hier, intent)
                raw = (out / 'contracts/block-allocation.json').read_bytes()
                derived = blocks.derive(read_native_inventory(netlist), read_sheet_names(netlist), probe,
                                        inventory_sha256=digest(netlist), allocation_raw=raw)
                self.assertEqual({k: derived[k] for k in ('allocation_sha256', 'blocks', 'residual')},
                                 receipt['allocation_derivation'])
                self.assertEqual([(b['id'], b['references']) for b in derived['blocks']], expected_blocks)
                self.assertEqual(derived['residual'], expected_residual)
                if 'residual' in extra:
                    self.assertEqual(read_json(out / 'contracts/block-allocation.json')['residual'], ['U1'])
                    self.assertEqual(receipt['assertions']['allocation:residual'],
                                     ['/contracts/block-allocation.json#/residual'])
        # The residual set is membership: it cannot also hold a block member or a phantom.
        self.refused(self.hier, self.intent(self.hier, blocks=[{'id': 'can', 'references': ['U1']}], residual=['U1']),
                     'conflicting membership: U1 is in block can and the residual set')
        self.refused(self.hier, self.intent(self.hier, residual=['U1']), 'residual needs blocks')
        self.refused(self.hier, self.intent(self.hier, blocks=power, residual=['U1', 'U1']),
                     'duplicate residual reference')
        self.refused(self.hier, self.intent(self.hier, blocks=power, residual=['U1'],
                                            terminals=[terminal('allocation:residual', '6', 'CANL', 'canl')]),
                     'duplicate assertion id: allocation:residual')

    def test_phantom_l1_is_refused_wherever_it_is_named(self):
        # Rev5 phantom-L1 control: L1 is neither on the native board nor in the native netlist.
        # Naming it as a terminal, locality endpoint, block or residual member, move or join refuses.
        phantom = '7d0c3a51-2b8e-4e62-9a0f-3f1d5c2b9a1f'
        self.refused(self.control, self.intent(terminals=[terminal('l1', '6', 'CANL', 'canl', reference='L1')]),
                     'pin_map applicability does not bind fact canl to L1')
        endpoint = self.locality(self.pair())
        endpoint[0]['supply']['to'] = {'reference': 'L1', 'pad': '1', 'net': 'VCC', 'uuid': phantom}
        endpoint[0]['return']['to'] = {'reference': 'L1', 'pad': '2', 'net': 'GND', 'uuid': phantom[:-1] + 'e'}
        self.refused(self.local, self.intent(self.local, locality=endpoint), 'native board has no footprint L1')
        self.refused(self.control, self.intent(blocks=[{'id': 'power', 'references': ['U1', 'L1']}]),
                     'block power: native board has no footprint L1')
        self.refused(self.control, self.intent(blocks=[{'id': 'can', 'references': ['U1']}], residual=['L1']),
                     'residual: native board has no footprint L1')
        self.refused(self.control, self.intent(moves=[{'reference': 'L1', 'allow': ['translate']}]),
                     'move: native board has no footprint L1')
        joined = {'id': 'kelvin', 'kind': 'kelvin', 'required': True, 'requirement': 'pins',
                  'terminals': ['u1-canl'], 'joins': [phantom], 'statement': 'sense at L1'}
        self.refused(self.control, self.intent(obligations=[joined]), 'stale join pad UUIDs')

    def noted(self, locator, value_nm, quote=None):
        quote = quote or f'Note {locator}: place the capacitor {DATASHEET_LINES[locator][0]} of the VCC pin.'
        return {'class': 'datasheet', 'basis': locator, 'derivation': {'kind': 'numeric', 'value_nm': value_nm, 'source': {
            'path': 'datasheet-notes.md', 'sha256': sha(DATASHEET_TEXT), 'locator': locator, 'quote': quote}}}

    def test_datasheet_numeric_binds_the_complete_record_line(self):
        # Review ee388e-510-r1r5 H2: the shared classifier accepted any matching token, so signed,
        # grouped, fractional and two-length datasheet lines published hard suffix maxima.
        records = ('datasheet-notes.md',)
        for locator, pattern in (('N-1', "signed length '-3 mm'|unresolvable length '-3"),
                                 ('N-2', "unresolvable length '1,003"), ('N-3', "unresolvable length '1/3"),
                                 ('N-4', "unresolvable length '1\u2019234"), ('N-5', "'999 yd'"),
                                 ('N-6', 'no single resolvable maximum')):
            value = DATASHEET_LINES[locator][1]
            bad = self.intent(self.local, records=records,
                              locality=self.locality(self.pair(self.noted(locator, value), limit=value)))
            with self.subTest(locator=locator):
                # AGENTEE-348 round-2 H1 (was 'hard' for every line): the shared classifier now reads
                # whole literals, so signed, grouped and fractional lines (N-1..N-4) no longer certify
                # a suffix. Third-HOLD grammar (was 'hard' for N-5): '999 yd' is outside the closed
                # grammar, so the whole quote fails closed. N-6 states 3 mm and 4 mm, both in grammar;
                # the compiler seam still refuses it.
                self.assertEqual(locality_provenance.classify(bad['locality'][0]['supply'],
                                                              {'datasheet-notes.md': DATASHEET_TEXT})['enforcement'],
                                 'advisory' if locator in ('N-1', 'N-2', 'N-3', 'N-4', 'N-5') else 'hard')
                self.refused(self.local, bad, f'numeric mismatch: record {locator} states .*{pattern}')
                document = {'schema': electrical_locality.SCHEMA, 'obligations': bad['locality'],
                            'source_records': {'datasheet-notes.md': DATASHEET_TEXT}}
                self.stale_package(self.local, self.intent(self.local, records=records, locality=self.locality(
                    self.pair(self.noted('N-7', 3_000_000)))), bad, 'contracts/electrical-locality.json',
                    document, 'numeric mismatch')
        # A quote cut out of a larger expression, or one occurring twice, binds nothing.
        # The quote binds only on the one line that carries its locator as a whole token.
        for locator, quote, value, pattern in (
                ('N-1', '3 mm', 3_000_000, 'whole tokens'), ('N-4', '234 mm', 234_000_000, 'whole tokens'),
                ('N-2', '003 mm', 3_000_000, 'whole tokens'), ('N-5', 'within 3 mm', 3_000_000, "'999 yd'"),
                ('N-8', 'within 3 mm of', 3_000_000, 'not exactly one captured line'),
                ('N', 'within 3 mm of', 3_000_000, 'not exactly one captured line'),
                ('N-6', 'within 3 mm and 4 mm of', 3_000_000, 'no single resolvable maximum')):
            with self.subTest(locator=locator, quote=quote):
                self.refused(self.local, self.intent(self.local, records=records, locality=self.locality(
                    self.pair(self.noted(locator, value, quote), limit=value))), f'numeric mismatch: .*{pattern}')
        self.refused(self.local, self.intent(self.local, records=records, locality=self.locality(
            self.pair(self.noted('N-7', 2_000_000), limit=2_000_000))), 'numeric mismatch')
        # Matching single-length controls keep their hard maxima in the actual PlacementIndex.
        for locator in ('N-7', 'N-8'):
            value = DATASHEET_LINES[locator][1]
            with self.subTest(positive=locator):
                intent = self.intent(self.local, records=records,
                                     locality=self.locality(self.pair(self.noted(locator, value), limit=value)))
                out = self.root / f'datasheet-{locator}'
                compile_and_publish(self.local, intent, out)
                receipt = verify_receipt(out, self.local, intent)
                self.assertEqual(receipt['locality_qualification'][0]['enforcement'], 'hard')
                index = electrical_locality.PlacementIndex(read_json(out / 'contracts/electrical-locality.json'))
                self.assertIn(('3', 'C1', value, True),
                              [(own['pad'], peer['reference'], limit, hard) for own, peer, limit, hard in index.pairs['U1']])

    def test_native_lock_forms_follow_pcbnew(self):
        # Review ee388e-510-r1r5 H1: ``(locked)`` and ``(locked true)`` are locked to pcbnew but were
        # read as unlocked, so U1 entered a rigid block that the actual blocks.derive refuses.
        from agent_ee.placement_probe import extract
        board = (FIXTURE / 'control/pins.kicad_pcb').read_text()
        canl = [terminal('l', '6', 'CANL', 'canl')]
        block = [{'id': 'locked', 'references': ['U1']}]
        netlist_of = lambda run: run / 'work/netlist/design.xml'
        forms = {'empty': ('(locked)', True), 'true': ('(locked true)', True), 'yes': ('(locked yes)', True),
                 'no': ('(locked no)', False), 'false': ('(locked false)', False)}
        atom = board.replace('(footprint "pins:SO8"', '(footprint "pins:SO8" locked', 1)
        runs = {name: (self.board_run(f'lock-{name}', lock(board, form), {}), locked)
                for name, (form, locked) in forms.items()}
        runs['atom'] = (self.board_run('lock-atom', atom, {}), True)
        for name, (run, locked) in runs.items():
            with self.subTest(form=name):
                path = run / 'inputs/project/pins.kicad_pcb'
                probe = extract(path)
                self.assertEqual(physical_intent.native_board(path)['U1']['locked'], locked)
                self.assertEqual([f['locked'] for f in probe['footprints'] if f['ref'] == 'U1'], [locked])
                allocation = self.intent(run, terminals=canl, blocks=block)
                fixed = self.intent(run, terminals=canl, moves=[{'reference': 'U1', 'allow': ['translate']}])
                if locked:
                    self.refused(run, allocation, 'refused by blocks.derive: locked footprints cannot be rigid-block')
                    self.refused(run, fixed, 'conflicting fixed move: native U1 is locked')
                    self.stale_package(run, self.intent(run, terminals=canl), allocation,
                                       'contracts/block-allocation.json',
                                       {'schema': blocks.ALLOCATION, 'blocks': block}, 'locked footprints')
                    with self.assertRaisesRegex(ValueError, 'locked footprints cannot be rigid-block members'):
                        blocks.derive(read_native_inventory(netlist_of(run)), read_sheet_names(netlist_of(run)), probe,
                                      inventory_sha256=digest(netlist_of(run)),
                                      allocation_raw=json_bytes({'schema': blocks.ALLOCATION, 'blocks': block}))
                else:
                    out = self.root / f'lock-{name}-allocation'
                    compile_and_publish(run, allocation, out)
                    receipt = verify_receipt(out, run, allocation)
                    derived = blocks.derive(read_native_inventory(netlist_of(run)), read_sheet_names(netlist_of(run)),
                                            probe, inventory_sha256=digest(netlist_of(run)),
                                            allocation_raw=(out / 'contracts/block-allocation.json').read_bytes())
                    self.assertEqual({k: derived[k] for k in ('allocation_sha256', 'blocks', 'residual')},
                                     receipt['allocation_derivation'])
                    self.assertIn('receipt.json', compile_plan(run, fixed))
        # Unknown, quoted or repeated lock declarations refuse; none defaults to unlocked.
        for name, edited, pattern in (
                ('repeated', lock(board, '(locked)(locked no)'), 'ambiguous lock state'),
                ('value', lock(board, '(locked maybe)'), 'unrecognized lock form'),
                ('quoted', lock(board, '(locked "no")'), 'quoted lock value'),
                ('atom', board.replace('(footprint "pins:SO8"', '(footprint "pins:SO8" bogus', 1),
                 "unrecognized footprint atoms \\['bogus'\\]")):
            with self.subTest(refused=name), self.assertRaisesRegex(IntentRefused, pattern):
                path = self.root / f'lock-{name}.kicad_pcb'
                path.write_text(edited)
                physical_intent.native_board(path)

    def test_footprint_identity_is_the_native_footprint_uuid(self):
        # Review ee388e-510-r1r5 H3: the footprint UUID was overwritten by the last pad UUID.
        from agent_ee.placement_probe import extract
        for run in (self.control, self.local, self.defect):
            with self.subTest(run=run.name):
                path = run / 'inputs/project/pins.kicad_pcb'
                self.assertEqual({r: (f['uuid'], f['fpid'], f['locked'])
                                  for r, f in physical_intent.native_board(path).items()},
                                 {f['ref']: (f['uuid'], f['fpid'], f['locked']) for f in extract(path)['footprints']})
        board = (FIXTURE / 'control/pins.kicad_pcb').read_text()
        canl = [terminal('l', '6', 'CANL', 'canl')]
        duplicate = self.board_run('fp-duplicate', board.replace('\t(gr_rect', mechanical(U1_UUID), 1), {})
        path = duplicate / 'inputs/project/pins.kicad_pcb'
        self.assertEqual(sorted(f['uuid'] for f in extract(path)['footprints']), [U1_UUID, U1_UUID])
        bad = self.intent(duplicate, terminals=canl, blocks=[{'id': 'can', 'references': ['U1']}])
        self.refused(duplicate, bad, 'missing or ambiguous footprint identity: native U1 and H1 share')
        # The package an earlier compiler published from the control board's identities.
        out = self.root / 'fp-duplicate-stale'
        physical_intent.publish(compile_plan(self.control, self.intent(
            terminals=canl, blocks=[{'id': 'can', 'references': ['U1']}])), out)
        with self.assertRaisesRegex(IntentRefused, 'missing or ambiguous footprint identity'):
            verify_receipt(out, duplicate, bad)
        netlist = duplicate / 'work/netlist/design.xml'
        with self.assertRaisesRegex(ValueError, 'missing or ambiguous footprint identity'):
            blocks.derive(read_native_inventory(netlist), read_sheet_names(netlist), extract(path),
                          inventory_sha256=digest(netlist), allocation_raw=json_bytes(
                              {'schema': blocks.ALLOCATION, 'blocks': [{'id': 'can', 'references': ['U1']}]}))
        missing = self.root / 'fp-missing.kicad_pcb'
        missing.write_text(board.replace(f'\t\t(uuid "{U1_UUID}")\n', '', 1))
        with self.assertRaisesRegex(IntentRefused, 'missing or ambiguous footprint identity: native U1 has no single'):
            physical_intent.native_board(missing)
        # A distinct mechanical footprint keeps the ordinary allocation positive.
        distinct = self.board_run('fp-distinct', board.replace(
            '\t(gr_rect', mechanical('7d0c3a51-2b8e-4e62-9a0f-3f1d5c2b9a11'), 1), {})
        good = self.intent(distinct, terminals=canl, blocks=[{'id': 'can', 'references': ['U1']}])
        out = self.root / 'fp-distinct-allocation'
        compile_and_publish(distinct, good, out)
        verify_receipt(out, distinct, good)

    def test_native_admission_refuses_boards_pcbnew_does_not_load(self):
        # Review ee388f-native-forms H1: a quoted keyword or a nested UUID published and verified
        # although pcbnew.LoadBoard returns None. The board must load natively before any board
        # fact is read, and the typed text parse refuses the same forms on its own.
        import pcbnew
        board = (FIXTURE / 'control/pins.kicad_pcb').read_text()
        u1 = f'(uuid "{U1_UUID}")'
        second = '00000000-0000-4000-8000-000000000001'
        canl = [terminal('l', '6', 'CANL', 'canl')]
        block = [{'id': 'can', 'references': ['U1']}]
        # name: (board text, pcbnew loads it, text-parse refusal or None where the text parse reads no such node)
        cases = {
            'quoted-lock-key': (lock(board, '("locked" no)'), False, 'node without a bare keyword'),
            'nested-uuid': (board.replace(u1, f'(uuid "{U1_UUID}" (uuid "{second}"))', 1), False, 'malformed uuid node'),
            'quoted-uuid-key': (board.replace(u1, f'("uuid" "{U1_UUID}")', 1), False, 'node without a bare keyword'),
            'quoted-lock-value': (lock(board, '(locked "no")'), False, 'quoted lock value'),
            'quoted-lock-yes': (lock(board, '(locked "yes")'), False, 'quoted lock value'),
            'quoted-flag': (board.replace('(footprint "pins:SO8"', '(footprint "pins:SO8" "locked"', 1), False,
                            'unrecognized footprint atoms'),
            'nested-lock': (lock(board, '(locked (no))'), False, 'unrecognized lock form'),
            'unknown-node': (lock(board, '(frobnicate 1)'), False, None),
            # pcbnew loads these; the duplicated identity nodes are ambiguous to the text parse.
            'duplicate-uuid': (board.replace(u1, u1 + u1, 1), True, 'missing or ambiguous footprint identity'),
            'duplicate-pad-uuid': (board.replace(f'(uuid "{PAD["6"]}")', f'(uuid "{PAD["6"]}")(uuid "{PAD["6"]}")', 1),
                                   True, 'a pad has 2 uuid nodes'),
            # pcbnew invents an identity for a pad without one; the text has none, so they disagree.
            'missing-pad-uuid': (board.replace(f'(uuid "{PAD["6"]}")', '', 1), True, None),
            # ee388j: pad 7 renamed 6 beside the pad 6 without identity; the two pads numbered 6 compare
            # by number and UUID, one missing, and must refuse as typed disagreement, never TypeError.
            'missing-pad-uuid-renumbered': (board.replace(f'(uuid "{PAD["6"]}")', '', 1)
                                            .replace('(pad "7"', '(pad "6"', 1), True, None),
        }
        good = self.intent(terminals=canl, blocks=block)
        for name, (text, loads, parse) in cases.items():
            with self.subTest(form=name):
                run = self.board_run(f'admission-{name}', text, {})
                path = run / 'inputs/project/pins.kicad_pcb'
                self.assertEqual(pcbnew.LoadBoard(str(path)) is not None, loads)
                if parse:
                    with self.assertRaisesRegex(IntentRefused, parse):
                        physical_intent.native_board(path)
                else:
                    self.assertIn('U1', physical_intent.native_board(path))
                pattern = (parse if parse and loads else
                           'native admission: pcbnew and the board text disagree on footprints \\[\'U1\'\\]' if loads
                           else 'native admission: pcbnew does not load the bound board')
                intent = self.intent(run, terminals=canl, blocks=block)
                self.refused(run, intent, pattern)
                # A package an earlier compiler published from a loadable board's identities.
                out = physical_intent.publish(compile_plan(self.control, good), self.root / f'admission-stale-{name}')
                with self.assertRaisesRegex(IntentRefused, pattern):
                    verify_receipt(out, run, intent)
        # Every valid lock form still loads natively and compiles (test_native_lock_forms_follow_pcbnew).
        self.assertIn('receipt.json', compile_plan(self.control, good))

    # ------------------------------------------- unsupported and diagnostic

    def test_kelvin_and_current_loop_obligations_stay_visible(self):
        obligations = [
            {'id': 'can-kelvin', 'kind': 'kelvin', 'required': True, 'requirement': 'pins',
             'terminals': ['u1-canl', 'u1-canh'], 'joins': [PAD['6']], 'forbidden_shared': [['u1-canl', 'u1-canh']],
             'statement': 'fixture-only force/sense obligation', 'proxy': 'u1-decoupling'},
            {'id': 'supply-loop', 'kind': 'current_loop', 'required': False, 'requirement': 'pins',
             'terminals': ['u1-canl'], 'statement': 'fixture-only loop obligation'}]
        out = compile_plan(self.local, self.intent(self.local, locality=self.locality(self.pair()),
                                                   obligations=copy.deepcopy(obligations)))
        receipt = out['receipt.json']
        self.assertEqual([(o['id'], o['status']) for o in receipt['unsupported']],
                         [('can-kelvin', 'unsupported'), ('supply-loop', 'unsupported')])
        # A passing-capable pad-distance proxy is not electrical compliance.
        self.assertIn('not electrical compliance', receipt['unsupported'][0]['proxy_note'])
        self.assertEqual((receipt['semantic_completion'], receipt['required_unsupported']),
                         ('unsupported', ['can-kelvin']))
        self.assertTrue(any('nearest-pad distance is not electrical compliance' in x for x in receipt['limitations']))
        self.assertNotIn('can-kelvin', receipt['assertions'])
        optional = compile_plan(self.local, self.intent(self.local, obligations=obligations[1:]))['receipt.json']
        self.assertEqual(optional['semantic_completion'], 'supported-subset')
        self.assertEqual(optional['unsupported'][0]['status'], 'unsupported')
        stale = copy.deepcopy(obligations[:1])
        stale[0]['joins'] = ['00000000-0000-4000-8000-000000000000']
        del stale[0]['proxy']
        self.refused(self.local, self.intent(self.local, obligations=stale), 'stale join pad UUIDs')
        self.refused(self.local, self.intent(self.local, obligations=[{**obligations[1], 'terminals': ['ghost']}]),
                     'unresolved terminals')

    def test_assertion_ids_share_one_namespace(self):
        canl = self.locality(self.pair())
        canl[0]['id'] = 'u1-canl'  # review H3 probe: already the CANL terminal
        self.refused(self.local, self.intent(self.local, locality=canl), 'duplicate assertion id: u1-canl')
        block = self.locality(self.pair())
        block[0]['id'] = 'block:can'
        self.refused(self.local, self.intent(self.local, locality=block, blocks=[{'id': 'can', 'references': ['U1']}]),
                     'duplicate assertion id: block:can')
        self.refused(self.control, self.intent(diagnostics=[
            {'id': 'u1-canh', 'kind': 'access', 'status': 'unknown', 'statement': 'pad 7 escape unknown'}]),
            'duplicate assertion id: u1-canh')
        self.refused(self.local, self.intent(self.local, locality=self.locality(self.pair()), obligations=[
            {'id': 'u1-decoupling', 'kind': 'current_loop', 'required': False, 'requirement': 'pins',
             'terminals': ['u1-canl'], 'statement': 'fixture-only loop obligation'}]),
            'duplicate assertion id: u1-decoupling')
        # Distinct ids keep every mapping. The local board's netted C1 is board-only, which the actual
        # blocks.derive refuses, so the allocation mapping is checked on the control board.
        receipt = compile_plan(self.local, self.intent(self.local, locality=self.locality(self.pair())))['receipt.json']
        self.assertEqual(receipt['assertions'], {
            'u1-canh': ['/contracts/topology-rules.json#/rules/1'],
            'u1-canl': ['/contracts/topology-rules.json#/rules/0'],
            'u1-decoupling': ['/contracts/electrical-locality.json#/obligations/0']})
        receipt = compile_plan(self.control, self.intent(blocks=[{'id': 'can', 'references': ['U1']}]))['receipt.json']
        self.assertEqual(receipt['assertions'], {
            'block:can': ['/contracts/block-allocation.json#/blocks/0'],
            'u1-canh': ['/contracts/topology-rules.json#/rules/1'],
            'u1-canl': ['/contracts/topology-rules.json#/rules/0']})

    def test_access_and_cut_stay_diagnostic(self):
        diagnostics = [{'id': 'u1-access', 'kind': 'access', 'status': 'unknown', 'statement': 'pad 6 escape unknown'},
                       {'id': 'cut-1', 'kind': 'cut', 'status': 'observed', 'statement': 'cut demand exceeds capacity'}]
        receipt = compile_plan(self.control, self.intent(diagnostics=diagnostics))['receipt.json']
        self.assertEqual([(d['id'], d['status'], d['authority']) for d in receipt['diagnostics']],
                         [('u1-access', 'unknown', 'diagnostic'), ('cut-1', 'observed', 'diagnostic')])
        self.assertFalse(set(receipt['assertions']) & {'u1-access', 'cut-1'})
        self.refused(self.control, self.intent(diagnostics=[{**diagnostics[1], 'status': 'proof'}]), 'not a proof')

    # ----------------------------------------------------------- mode/scope

    def test_manual_mode_preserved_and_not_wired(self):
        intent = self.intent()
        out = self.root / 'disabled'
        self.assertIsNone(compile_and_publish(self.control, intent, out, mode='disabled'))
        self.assertFalse(out.exists())
        self.refused(self.control, intent | {'mode': 'architect'}, 'mode manual')
        with self.assertRaisesRegex(IntentRefused, 'mode must be one of'):
            compile_plan(self.control, intent, mode='architect')
        users = [p.name for p in (ROOT / 'src/agent_ee').glob('*.py')
                 if p.name != 'physical_intent.py' and 'physical_intent' in p.read_text()]
        self.assertEqual(users, [], 'S1 must not be wired into a default, runtime, campaign or routing path')

    def test_fixture_dispositions_are_typed_and_tested(self):
        dispositions = physical_intent.FIXTURE_DISPOSITIONS
        self.assertEqual(set(dispositions), {'tps25982', 'phantom-l1', 'native-repeated-functions', 'child-sheet'})
        tests = {name for cls in (PhysicalIntentTests, PodEquivalenceTests) for name in dir(cls)
                 if name.startswith('test_')}
        for name, disposition in dispositions.items():
            with self.subTest(name):
                if disposition['status'] == 'supported':
                    self.assertTrue(disposition['tests'] and set(disposition['tests']) <= tests)
                else:
                    self.assertEqual((disposition['status'], set(disposition)), ('unsupported', {'status', 'reason'}))
                    self.assertIn('owner gate', disposition['reason'])

    def test_failed_publication_leaves_nothing(self):
        compiled = compile_plan(self.control, self.intent())
        compiled['contracts/zz-broken.json'] = {'value': float('nan')}
        out = self.root / 'broken'
        with self.assertRaises(ValueError):
            physical_intent.publish(compiled, out)
        self.assertFalse(out.exists())
        self.assertEqual([p for p in self.root.iterdir() if p.name.startswith('.broken.')], [])



# The captured pod (AGENTEE-388 S1 equivalence target): fome-mc main at the Rev5 inspection.
# Every input is read from the pinned objects of a local fome-mc store into a temporary run;
# no pod design file is copied into this repository.
POD_COMMIT = '19522e37de01103a93fe171a09fe624d8aba6bad'
POD_SOURCE = 'telemetry-pod/expansion/storage-microsd/project/'
POD_ITERATION = 'telemetry-pod/expansion/placement-candidate/iteration-7/'
POD_DATASHEET = ('telemetry-pod/expansion/parts-closure/recovered-pilot/power-source/acquisition-tracker/'
                 'f0068c8d96247b629703e6eacc46990097dff6779f4204d95b88ed3118e22827.pdf')
POD_DATASHEET_SHA256 = 'a045677f8bb2b173e62689f5a7998a3c4afb7b1b9949a1d0e82e67803771b37a'
POD_FILES = {  # store path -> (run path, sha256 of the bytes; gzip members are hashed decompressed)
    POD_SOURCE + 'telemetry-pod.kicad_sch': ('project/telemetry-pod.kicad_sch',
                                             '4da27afc8d9cd7ff3d1fd8e23f855747072c050d25601b89fb3909b688e49f23'),
    POD_SOURCE + 'telemetry-pod.kicad_pro': ('project/telemetry-pod.kicad_pro',
                                             'f9f6eadeaef8268b4fd1b05dd8f134c069da687e40da598c54125a0d819ecd5e'),
    POD_SOURCE + 'sym-lib-table': ('project/sym-lib-table',
                                   '037d8481bbc29e3aabc4d521da024d4f7c5a1e9d3632a0506e39b3f941720856'),
    POD_SOURCE + 'fp-lib-table': ('project/fp-lib-table',
                                  '095b5688975942411897ddeec05831d72aa0f40cdee173ecfeb60d98dd9f579f'),
    POD_ITERATION + 'inputs/seed.kicad_pcb.gz': ('project/telemetry-pod.kicad_pcb',
                                                 '21bbf8efdfaf848f1a9494846a6fb1bdf31d19581daaf2c5fb601a26ffba8ecc'),
    POD_DATASHEET: ('tps7b4253.pdf', POD_DATASHEET_SHA256),
}
POD_REVIEWED = {  # reviewed iteration-7 contracts, read for comparison only
    'allocation': (POD_ITERATION + 'inputs/allocation.json',
                   '30ff53342642fb31505df3531a34198bb43780dbc738771650314acacea31f25'),
    'derived': (POD_ITERATION + 'inputs/derived.json', '03aa3baf855f2b04e6b16072b66f73bd648ab28569f3c8dc6408e5c3d897c29c'),
    'policy': (POD_ITERATION + 'inputs/policy.json', '98dbdcd9c6f6634675306fdfff754938095489083a1dd53fe697c6b44a2693ea'),
    'measured': (POD_ITERATION + 'evidence/obligations-measured.json', None),
    'candidate': (POD_ITERATION + 'candidate.kicad_pcb.gz',
                  '35f242c804038b7ec48740e6f894339e1c152fea95e6a15ddfe77b402030cba9'),
}
# Authored facts: the DDA (8-pin HSOIC) pinout of the captured TI TPS7B4253-Q1 datasheet names
# GND at pins 3 and 6, a native repeated physical function on pod U6 and U12.
POD_FACTS = [{'id': f'gnd-{pin}', 'part_number': 'TPS7B4253QDDARQ1', 'kind': 'pin_function', 'pin': pin,
              'value': 'GND', 'unit': '', 'conditions': 'DDA PowerPAD 8-pin HSOIC',
              'source': {'path': 'tps7b4253.pdf', 'sha256': POD_DATASHEET_SHA256, 'page': 3,
                  'locator': f'Figure 4-1, DDA PowerPAD package, pin {pin}', 'quote': quote}}
             for pin, quote in (('3', 'GND      3'), ('6', '6    GND'))]


def pod_store():
    home = Path.home() / 'git/fome-mc'
    return fome_mc_store(os.environ.get('AGENT_EE_FOME_MC_CHECKOUT', str(home)), POD_COMMIT)


def pod_blob(store, path, expected):
    data = subprocess.run(['git', '-C', str(store), 'cat-file', 'blob', f'{POD_COMMIT}:{path}'],
                          capture_output=True, check=True).stdout
    data = gzip.decompress(data) if path.endswith('.gz') else data
    if expected is not None:
        assert hashlib.sha256(data).hexdigest() == expected, f'pinned fome-mc {path} changed'
    return data


class PodEquivalenceTests(unittest.TestCase):
    """The captured pod intent compiles back to the reviewed 17-block / 12-obligation contracts.

    The run captures the pod's microSD source schematic and the iteration-7 seed board from the
    pinned fome-mc store, exports the native netlist and checks the authored TPS7B4253 facts.
    The intent is the reviewed allocation (blocks and residual) and pad obligations, each
    endpoint bound to its native pad UUID, plus the repeated GND terminals of U6 and U12.
    """

    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True, tools=('pdftotext',))
        store = pod_store()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        src = cls.root / 'src'
        for path, (rel, expected) in POD_FILES.items():
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            (src / rel).write_bytes(pod_blob(store, path, expected))
        cls.reviewed = {}
        for name, (path, expected) in POD_REVIEWED.items():
            data = pod_blob(store, path, expected)
            cls.reviewed[name] = data if name == 'candidate' else json.loads(data)
        cls.allocation_raw = pod_blob(store, *POD_REVIEWED['allocation'])
        write_json(src / 'facts.json', {'schema': 'agent-ee.datasheet-facts/1', 'facts': POD_FACTS})
        (src / 'requirements.md').write_text('# Pod intent equivalence\n\nThe captured pod intent compiles to its '
                                             'reviewed block and locality contracts.\n')
        control = read_json(FIXTURE / 'control.scope.json')
        facts = next(c for c in control['checks'] if c['kind'] == 'datasheet_facts')
        scope = {'schema': control['schema'], 'name': 'pod intent equivalence',
                 'scope': 'Compile the captured pod intent against its native source and seed board.',
                 'inputs': [{'path': str(src / 'project'), 'at': 'project'},
                            *({'path': str(src / f), 'at': f} for f in ('facts.json', 'requirements.md', 'tps7b4253.pdf')),
                            next(i for i in control['inputs'] if i['at'] == 'stock/template')],
                 'schematic': 'project/telemetry-pod.kicad_sch', 'board': 'project/telemetry-pod.kicad_pcb',
                 'requirements': ['requirements.md'],
                 'checks': [{'id': 'netlist', 'kind': 'netlist'}, facts,
                            {'id': 'pin-map', 'kind': 'pin_map', 'params': {
                                'facts_check': 'facts', 'netlist_check': 'netlist',
                                'symbols': [{'reference': ref, 'part_number': 'TPS7B4253QDDARQ1',
                                             'facts': ['gnd-3', 'gnd-6']} for ref in ('U6', 'U12')]}}],
                 'baseline': {'requirements': [{'id': 'pod', 'statement': 'Captured pod intent equivalence.',
                                                'source': {'path': 'requirements.md', 'locator': 'Pod intent equivalence'},
                                                'checks': ['netlist', 'facts', 'pin-map']}],
                              'targets': ['requirements.md'], 'reference_architectures': ['requirements.md']}}
        write_json(src / 'scope.json', scope)
        cls.pod = cls.root / 'run'
        cls.pod.mkdir()
        capture(src / 'scope.json', cls.pod)
        results = run_checks(cls.pod, time.monotonic() + 300)
        freeze_evidence(cls.pod, results)
        cls.board = physical_intent.native_board(cls.pod / 'inputs/project/telemetry-pod.kicad_pcb')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def pad_uuid(self, reference, number):
        pads = [p for p in self.board[reference]['pads'] if p['number'] == number]
        self.assertEqual(len(pads), 1, f'{reference}.{number}')
        return pads[0]['uuid']

    def intent(self, **changes):
        run = self.pod
        manifest = read_json(run / 'candidate.json')
        locality = copy.deepcopy(self.reviewed['policy']['electrical_locality']['obligations'])
        for obligation in locality:
            for role in electrical_locality.roles(obligation):
                for end in ('from', 'to'):
                    endpoint = obligation[role][end]
                    endpoint['uuid'] = self.pad_uuid(endpoint['reference'], endpoint['pad'])
        terminals = [{'id': f'{ref.lower()}-gnd-{pin}', 'reference': ref, 'pin': pin,
                      'pad_uuid': self.pad_uuid(ref, pin), 'function': 'GND', 'fact': f'gnd-{pin}',
                      'net': '/GND', 'class': 'ic', 'requirement': 'pod'}
                     for ref in ('U6', 'U12') for pin in ('3', '6')]
        intent = {'schema': physical_intent.SCHEMA, 'mode': 'manual',
                  'sources': {'candidate_id': manifest['candidate_id'],
                              'evidence_sha256': digest(run / 'work/verification/evidence.json'),
                              'pin_map_check': 'pin-map',
                              'board_sha256': digest(run / 'inputs/project/telemetry-pod.kicad_pcb'),
                              'netlist_sha256': digest(run / 'work/netlist/design.xml'),
                              'facts_sha256': digest(run / 'work/facts/facts.json'),
                              'pin_map_sha256': digest(run / 'work/pin-map/pin-map.json')},
                  'terminals': terminals,
                  'blocks': copy.deepcopy(self.reviewed['allocation']['blocks']),
                  'residual': list(self.reviewed['allocation']['residual']),
                  'locality': locality}
        intent.update(changes)
        return intent

    def test_captured_pod_intent_compiles_to_the_reviewed_contracts(self):
        intent = self.intent()
        started = time.monotonic()
        compiled = compile_plan(self.pod, intent)
        elapsed = time.monotonic() - started
        # Provisional pure-compilation budget; netlist export and PDF extraction ran in the review run.
        self.assertLess(elapsed, 10, f'provisional 10 s pod compilation budget: {elapsed:.3f} s')
        receipt = compiled['receipt.json']
        reviewed, derived = self.reviewed['allocation'], self.reviewed['derived']
        # 17 blocks and the residual set: the emitted allocation is the reviewed document.
        allocation = compiled['contracts/block-allocation.json']
        self.assertEqual(len(allocation['blocks']), 17)
        self.assertEqual(allocation, reviewed)
        # The actual blocks.derive on the run's netlist and the digest-bound seed reproduces the
        # reviewed derivation exactly: blocks, members, anchors and residual reasons.
        self.assertEqual(receipt['allocation_derivation']['blocks'], derived['blocks'])
        self.assertEqual(receipt['allocation_derivation']['residual'], derived['residual'])
        self.assertEqual((len(derived['blocks']), len(derived['residual'])), (17, 53))
        self.assertEqual(derived['board_sha256'], intent['sources']['board_sha256'])
        # Exact mismatches, reported rather than hidden: the compiled file is canonical JSON, so its
        # SHA-256 is not the reviewed file's; derive over the reviewed bytes gives the reviewed hash.
        # The reviewed native.xml (inventory 7791d443...) is not committed; the run re-exports it.
        self.assertNotEqual(receipt['allocation_derivation']['allocation_sha256'], derived['allocation_sha256'])
        self.assertEqual(receipt['allocation_derivation']['allocation_sha256'],
                         hashlib.sha256(json_bytes(allocation)).hexdigest())
        netlist = self.pod / 'work/netlist/design.xml'
        from agent_ee.placement_probe import extract
        seed = extract(self.pod / 'inputs/project/telemetry-pod.kicad_pcb')
        again = blocks.derive(read_native_inventory(netlist), read_sheet_names(netlist), seed,
                              inventory_sha256=derived['inventory_sha256'], allocation_raw=self.allocation_raw)
        self.assertEqual(again, derived)
        self.assertNotEqual(digest(netlist), derived['inventory_sha256'])
        # 12 obligations / 26 pairs: the emitted locality is the reviewed one plus native pad UUIDs.
        locality = compiled['contracts/electrical-locality.json']
        stripped = copy.deepcopy(locality['obligations'])
        for obligation in stripped:
            for role in electrical_locality.roles(obligation):
                for end in ('from', 'to'):
                    del obligation[role][end]['uuid']
        policy = self.reviewed['policy']['electrical_locality']
        self.assertEqual(stripped, policy['obligations'])
        self.assertEqual(len(stripped), 12)
        self.assertEqual(len(receipt['locality_qualification']), 26)
        self.assertEqual({q['enforcement'] for q in receipt['locality_qualification']}, {'advisory'})
        # Graded on the reviewed iteration-7 candidate board, the compiled contract reproduces the
        # reviewed measurements: all 26 pairs within their (advisory) bounds, identical distances.
        board = self.root / 'candidate.kicad_pcb'
        board.write_bytes(self.reviewed['candidate'])
        probe = extract(board)
        compiled_grade = electrical_locality.grade(probe, locality)
        reviewed_grade = electrical_locality.grade(probe, policy)
        def rows(graded):
            return [(r['obligation'], r['role'], r['distance_squared_nm'], r['constraint_provenance'])
                    for r in graded['measured']]
        self.assertEqual(rows(compiled_grade), rows(reviewed_grade))
        self.assertEqual((compiled_grade['status'], compiled_grade['defects'], compiled_grade['unknown']), ('info', [], []))
        self.assertEqual([a['exceeded'] for a in compiled_grade['advisories']], [False] * 26)
        self.assertEqual([(r['obligation'], r['role'], round(r['distance_mm'], 6)) for r in compiled_grade['measured']],
                         [(r['obligation'], r['role'], r['distance_mm']) for r in self.reviewed['measured']['rows']])
        # Native repeated physical function: U6/U12 GND at pins 3 and 6 keep distinct identities.
        topology = compiled['contracts/topology-rules.json']
        self.assertEqual(topology['classes'], {'PodPower:TPS7B4253QDDARQ1': {
            'class': 'ic', 'terminals': {'GND@3': '3', 'GND@6': '6'}}})
        self.assertEqual(topology_rules.evaluate(topology, read_native_inventory(netlist))['outcome'], 'pass')
        self.assertEqual(len(receipt['assertions']), 4 + 12 + 17 + 1)
        out = self.root / 'pod-package'
        compile_and_publish(self.pod, intent, out)
        self.assertEqual(verify_receipt(out, self.pod, intent)['package_id'], receipt['package_id'])

    def test_wrong_pins_on_the_captured_power_device_refuse(self):
        # Rev5 invalid-pin classes on a captured device: a verified function on another pin, a
        # function the cited fact does not state, an unverified fact and a repeated pad.
        def gnd(pin, fact, **extra):
            return {'id': f'u6-{pin}', 'reference': 'U6', 'pin': pin, 'pad_uuid': self.pad_uuid('U6', pin),
                    'function': 'GND', 'fact': fact, 'net': '/GND', 'class': 'ic', 'requirement': 'pod', **extra}
        for terminals, pattern in (
                ([gnd('7', 'gnd-6')], 'verified GND fact binds U6 pin 6, not proposed pin 7'),
                ([gnd('8', 'gnd-3', function='IN')], "states 'GND', not 'IN'"),
                ([gnd('3', 'missing')], 'not a verified pin_function fact'),
                ([gnd('3', 'gnd-3'), gnd('3', 'gnd-3', id='again')], 'duplicate terminal pin')):
            with self.subTest(pattern):
                with self.assertRaisesRegex(IntentRefused, pattern):
                    compile_plan(self.pod, self.intent(terminals=terminals))
        # The pod is one root sheet: a footprint dropped from the declared residual set is still
        # reported by derive (root-sheet residual), never silently lost.
        intent = self.intent()
        intent['residual'] = [r for r in intent['residual'] if r != 'U1']
        derivation = compile_plan(self.pod, intent)['receipt.json']['allocation_derivation']
        self.assertIn({'reference': 'U1', 'reason': 'root-sheet'}, derivation['residual'])


class RepeatedFunctionTests(unittest.TestCase):
    """Review 510 R5 / review ee388d H4: repeated physical functions keep distinct identities.

    Source/native acquisition is synthetic (explicit dictionaries); the real terminal join,
    topology contract validator and topology evaluator run. No real-device pinout is claimed.
    """
    UUIDS = [f'00000000-0000-4000-8000-{i:012d}' for i in range(1, 6)]

    def setUp(self):
        def fact(pin, function):
            return {'kind': 'pin_function', 'value': function, 'pin': pin, 'part_number': 'P',
                    'source': {'path': 'p.pdf', 'sha256': 'a' * 64, 'page': 1, 'locator': 'table'}}
        pins = {'1': 'GND', '2': 'GND', '3': 'VCC', '4': 'VCC', '5': 'OUT'}
        self.src = {'requirements': {'r'},
                    'facts': {'facts': [{'id': f'f{p}', 'status': 'verified', 'fact': fact(p, f)} for p, f in pins.items()]},
                    'pin_map': {'comparisons': [{'reference': 'U1', 'fact_id': f'f{p}', 'pin': p, 'native_name': f,
                                                 'matches': True} for p, f in pins.items()]}}
        nets = {'GND': '1', 'VCC': '2', 'OUT': '3'}
        self.board = {'U1': {'locked': False, 'pads': [{'number': p, 'uuid': self.UUIDS[int(p) - 1], 'net': f}
                                                       for p, f in pins.items()]}}
        self.native = {'components': {'U1': {'pins': list(pins), 'lib_id': 'L:P', 'populated': True, 'value': 'P'}},
                       'memberships': {('U1', p): nets[f] for p, f in pins.items()},
                       'nets': {nid: {'name': name, 'locator': nid,
                                      'terminals': [('U1', p) for p, f in pins.items() if f == name]}
                                for name, nid in nets.items()}}
        self.terminals = [{'id': f'u1-{p}', 'reference': 'U1', 'pin': p, 'pad_uuid': self.UUIDS[int(p) - 1],
                           'function': f, 'fact': f'f{p}', 'net': f, 'class': 'ic', 'requirement': 'r'}
                          for p, f in pins.items()]

    def compile(self, terminals):
        return physical_intent._terminals({'terminals': terminals}, self.src, self.board, self.native)

    def test_two_gnd_and_two_vcc_pins_compile_with_distinct_identities(self):
        resolved, document = self.compile(self.terminals)
        self.assertEqual(document['classes'], {'L:P': {'class': 'ic', 'terminals': {
            'GND@1': '1', 'GND@2': '2', 'VCC@3': '3', 'VCC@4': '4', 'OUT': '5'}}})
        self.assertEqual([(t['pin'], t['function'], t['class_terminal'], t['fact_id']) for t in resolved],
                         [('1', 'GND', 'GND@1', 'f1'), ('2', 'GND', 'GND@2', 'f2'), ('3', 'VCC', 'VCC@3', 'f3'),
                          ('4', 'VCC', 'VCC@4', 'f4'), ('5', 'OUT', 'OUT', 'f5')])
        topology_rules.validate_rules(document)
        self.assertEqual(topology_rules.evaluate(document, self.native)['outcome'], 'pass')
        # The alias depends only on the bound pins, never on authoring order.
        self.assertEqual(self.compile(self.terminals[::-1])[1]['classes'], document['classes'])

    def test_contradictory_or_wrong_repeated_identity_still_refuses(self):
        # A wrong pin for a repeated function is still a wrong-pin refusal.
        wrong = [dict(self.terminals[1], pin='3', pad_uuid=self.UUIDS[2])]
        with self.assertRaisesRegex(IntentRefused, 'verified GND fact binds U1 pin 2, not proposed pin 3'):
            self.compile(wrong)
        # Two references of one symbol binding the same physical pin to two functions contradict.
        self.native['components']['U2'] = dict(self.native['components']['U1'])
        self.board['U2'] = {'locked': False, 'pads': [{'number': '1', 'uuid': '00000000-0000-4000-8000-000000000099',
                                                       'net': 'VCC'}]}
        self.native['memberships'][('U2', '1')] = '2'
        self.src['facts']['facts'].append({'id': 'g1', 'status': 'verified', 'fact': {
            **self.src['facts']['facts'][2]['fact'], 'pin': '1'}})
        self.src['pin_map']['comparisons'].append({'reference': 'U2', 'fact_id': 'g1', 'pin': '1',
                                                   'native_name': 'VCC', 'matches': True})
        u2 = {**self.terminals[2], 'id': 'u2-1', 'reference': 'U2', 'pin': '1', 'fact': 'g1',
              'pad_uuid': '00000000-0000-4000-8000-000000000099'}
        with self.assertRaisesRegex(IntentRefused, 'contradictory pin identity: symbol L:P pin 1 is bound to GND and VCC'):
            self.compile(self.terminals[:1] + [u2])
        # A generated alias never shadows another bound function name.
        self.src['facts']['facts'][4]['fact']['value'] = 'GND@1'
        self.src['pin_map']['comparisons'][4]['native_name'] = 'GND@1'
        clash = self.terminals[:2] + [dict(self.terminals[4], function='GND@1')]
        with self.assertRaisesRegex(IntentRefused, 'terminal aliases collide'):
            self.compile(clash)


if __name__ == '__main__':
    unittest.main()
