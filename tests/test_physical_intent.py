"""AGENTEE-388 S1: manual source-bound physical intent compiler on the TJA1051 pin-map fixture."""
import copy
import hashlib
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from agent_ee import locality_provenance, physical_intent
from agent_ee.baseline import freeze_evidence
from agent_ee.candidate import capture
from agent_ee.checks import run_checks
from agent_ee.physical_intent import IntentRefused, compile_and_publish, compile_plan, verify_receipt
from agent_ee.storage import digest, json_bytes, read_json, write_json
from toolchain import require_toolchain

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
              'D-10 (owner of record): keep the C1 supply pad within 0.5 mm of U1 VCC.\n')
LAYOUT_TEXT = 'Layout note L-2: place the decoupling capacitor within 3 mm of the VCC pin.\n'


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
        cls.local = cls.board_run('local', local, {'owner-decisions.md': OWNER_TEXT, 'layout-notes.md': LAYOUT_TEXT})
        dup = (f'\t\t(pad "7" smd rect (at 5 2) (size 1 1) (layers "F.Cu" "F.Mask") '
               f'(net "unconnected-(U1-CANH-Pad7)") (uuid "{DUP7}"))\n\t\t(embedded_fonts no)')
        defect = (board.replace('(layer "F.Cu")\n\t\t(uuid "33863c18', '(layer "F.Cu")\n\t\t(locked yes)\n\t\t(uuid "33863c18', 1)
                  .replace('\t\t(embedded_fonts no)', dup, 1))
        cls.defect = cls.board_run('defect', defect, {})

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
    def board_run(cls, name, board, records):
        """The fixture with an authored board edit; native checks limited to netlist/facts/pin-map."""
        project = cls.root / f'{name}-src/project'
        shutil.copytree(FIXTURE / 'control', project)
        (project / 'pins.kicad_pcb').write_text(board)
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
        ok = compile_plan(self.local, self.intent(self.local, blocks=[{'id': 'can', 'references': ['C1', 'U1'],
                                                                      'anchor': 'U1'}],
                                                  moves=[{'reference': 'C1', 'allow': ['translate', 'rotate']}]))
        self.assertEqual(ok['contracts/block-allocation.json'],
                         {'schema': 'agent-ee.block-allocation/1',
                          'blocks': [{'id': 'can', 'references': ['C1', 'U1'], 'anchor': 'U1'}]})
        self.assertEqual(ok['receipt.json']['allowed_moves'], [{'reference': 'C1', 'allow': ['rotate', 'translate']}])

    # ------------------------------------------------------------- locality

    def pair(self, provenance=None, limit=3_000_000, net='VCC'):
        pair = {'from': {'reference': 'U1', 'pad': '3', 'net': net, 'uuid': PAD['3']},
                'to': {'reference': 'C1', 'pad': '1', 'net': net, 'uuid': C1_VCC},
                'max_distance_nm': limit, 'citation': 'authored test limit'}
        if provenance:
            pair['provenance'] = provenance
        return pair

    def locality(self, supply):
        ground = {'from': {'reference': 'U1', 'pad': '2', 'net': 'GND', 'uuid': PAD['2']},
                  'to': {'reference': 'C1', 'pad': '2', 'net': 'GND', 'uuid': C1_GND}}
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
        manufacturer = {'class': 'manufacturer', 'basis': 'captured JLCPCB record'}
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
                 ('D-8', 'within 3 mm and 999 MM of U1 VCC', "length '999 MM' in a unit that cannot be resolved")]
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
                     'x3 mm', '\uff13 mm', '3 millimetres', '3 mm\u00b2', '3 Mm', '3"', '3 mm (3000 um)'):
            with self.subTest(text=text), self.assertRaisesRegex(IntentRefused, 'numeric mismatch'):
                stated(f'within {text} of U1 VCC', 'D-0', 'probe')
        self.assertEqual(stated('3 more pads within 3 mm of pin 4.', 'D-0', 'probe'), [('3', 'mm')])

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
        # Distinct ids keep every mapping.
        receipt = compile_plan(self.local, self.intent(self.local, locality=self.locality(self.pair()),
                                                       blocks=[{'id': 'can', 'references': ['U1']}]))['receipt.json']
        self.assertEqual(receipt['assertions'], {
            'block:can': ['/contracts/block-allocation.json#/blocks/0'],
            'u1-canh': ['/contracts/topology-rules.json#/rules/1'],
            'u1-canl': ['/contracts/topology-rules.json#/rules/0'],
            'u1-decoupling': ['/contracts/electrical-locality.json#/obligations/0']})

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

    def test_failed_publication_leaves_nothing(self):
        compiled = compile_plan(self.control, self.intent())
        compiled['contracts/zz-broken.json'] = {'value': float('nan')}
        out = self.root / 'broken'
        with self.assertRaises(ValueError):
            physical_intent.publish(compiled, out)
        self.assertFalse(out.exists())
        self.assertEqual([p for p in self.root.iterdir() if p.name.startswith('.broken.')], [])


if __name__ == '__main__':
    unittest.main()
