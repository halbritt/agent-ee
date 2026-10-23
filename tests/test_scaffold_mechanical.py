"""Captured mechanical inputs survive scaffold preparation and native content writing."""
from pathlib import Path
import unittest

from agent_ee import board_envelope
from agent_ee.scaffold import write_envelope_content
from agent_ee.storage import digest, read_json, write_json
from test_scaffold import ScaffoldFixture, EnvelopeScaffoldFixture
from test_mechanical_reservations import document, reservation, simple_capture, SOURCE_PATH, SOURCE_TEXT
from toolchain import require_toolchain


def add_mechanical(fixture):
    source = fixture.source
    (source / SOURCE_PATH).parent.mkdir(parents=True, exist_ok=True)
    (source / SOURCE_PATH).write_text(SOURCE_TEXT)
    (source / 'source.kicad_pcb').write_bytes(b'Immutable original-board binding fixture.\n')
    facts = simple_capture()
    facts['board_sha256'] = digest(source / 'source.kicad_pcb')
    declared = document(facts, [reservation()])
    write_json(source / 'mechanical.json', declared)
    fixture.envelope['mechanical_reservations'] = {
        'path': 'mechanical.json', 'sha256': digest(source / 'mechanical.json')}
    write_json(source / 'envelope.json', fixture.envelope)
    fixture.scope['inputs'] = [dict(path='source/' + p.relative_to(source).as_posix(),
        at=p.relative_to(source).as_posix()) for p in sorted(source.rglob('*')) if p.is_file()]
    write_json(fixture.scope_path, fixture.scope)


class CapturedScaffoldTests(ScaffoldFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        add_mechanical(self)

    def test_scope_scaffold_keeps_authenticated_mechanical_inputs(self):
        receipt = self.build()
        inputs = self.root / 'scaffold/inputs'
        files = receipt['inputs']['files']
        record = board_envelope.validate((inputs / 'envelope.json').read_bytes(), captured=(files, inputs))
        self.assertEqual(record['mechanical_reservations']['sha256'], files['mechanical.json'])
        self.assertEqual((inputs / 'mechanical.json').read_bytes(), (self.source / 'mechanical.json').read_bytes())

    def test_missing_mechanical_capture_publishes_no_scaffold(self):
        self.scope['inputs'] = [item for item in self.scope['inputs'] if item['at'] != 'mechanical.json']
        write_json(self.scope_path, self.scope)
        with self.assertRaisesRegex(ValueError, 'mechanical_reservations document.*captured sha256'):
            self.build()
        self.assertFalse((self.root / 'scaffold').exists())

    def test_stale_and_wrong_mechanical_file_publish_no_scaffold(self):
        path = self.source / 'mechanical.json'
        original = path.read_bytes()
        for name, replacement in [('stale', original + b'\n'), ('wrong', b'{}\n')]:
            with self.subTest(name=name):
                path.write_bytes(replacement)
                with self.assertRaisesRegex(ValueError, 'mechanical_reservations document.*captured sha256'):
                    self.build(name)
                self.assertFalse((self.root / name).exists())
        path.write_bytes(original)

    def test_net_constraint_emission_uses_authenticated_envelope_sources(self):
        from agent_ee.net_constraints import emit
        from test_net_constraints import TEMPLATE
        raw = (self.source / 'envelope.json').read_bytes()
        constraints = (Path(__file__).resolve().parents[1] / 'fixtures/net-constraints/rc.constraints.json').read_bytes()
        capture = board_envelope.source_inventory(self.source)
        with self.assertRaisesRegex(ValueError, 'requires captured immutable inputs'):
            emit(constraints, TEMPLATE, envelope_raw=raw)
        artifacts = emit(constraints, TEMPLATE, envelope_raw=raw, captured=capture)
        self.assertIn('project', artifacts)
        (self.source / 'mechanical.json').write_bytes(b'{}\n')
        with self.assertRaises(ValueError):
            emit(constraints, TEMPLATE, envelope_raw=raw, captured=capture)

    def test_connector_capability_accepts_capture_and_refuses_changed_reservations(self):
        from agent_ee.capabilities import describe_check
        self.build()
        inputs = self.root / 'scaffold/inputs'
        files, _ = board_envelope.source_inventory(inputs)
        scope = read_json(self.root / 'scaffold/scope.json')
        spec = dict(id='connector', kind='placement', params=dict(
            criterion='connector-edge/1', envelope='envelope.json', reference='J1', drc_check='drc'))
        checks = scope['checks'] + [spec]
        result = describe_check(spec, check_specs=checks, files=files, scope=scope, inputs=inputs)
        self.assertEqual(result['status'], 'supported', result)
        (inputs / 'mechanical.json').write_bytes(b'{}\n')
        refused = describe_check(spec, check_specs=checks, files=files, scope=scope, inputs=inputs)
        self.assertEqual(refused['status'], 'unsupported', refused)

    def test_round_trip_uses_the_supplied_capture_and_refuses_stale_sources(self):
        self.build()
        inputs = self.root / 'scaffold/inputs'
        captured = board_envelope.source_inventory(inputs)
        raw = (inputs / 'envelope.json').read_bytes()
        pcb = (inputs / 'project/design.kicad_pcb').read_bytes()
        with self.assertRaisesRegex(ValueError, 'requires captured immutable inputs'):
            board_envelope.round_trip(raw, pcb)
        trip = board_envelope.round_trip(raw, pcb, captured=captured)
        self.assertEqual(next(item for item in trip['fields'] if item['field'] == '/outline')['status'], 'match')
        (inputs / SOURCE_PATH).write_text('Wrong source bytes')
        with self.assertRaises(ValueError):
            board_envelope.round_trip(raw, pcb, captured=captured)


class CapturedContentTests(EnvelopeScaffoldFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        add_mechanical(self)

    def test_native_content_stage_accepts_captured_mechanical_reservations(self):
        require_toolchain(native=True)
        inputs, path, receipt = self.content()
        self.assert_completed(path, receipt)
        self.assertEqual(digest(inputs / 'mechanical.json'), self.envelope['mechanical_reservations']['sha256'])

    def test_native_content_refuses_changed_or_missing_reservation_sources(self):
        require_toolchain(native=True)
        self.build()
        inputs = self.root / 'scaffold/inputs'
        board = inputs / 'project/design.kicad_pcb'
        original = board.read_bytes()
        capture = board_envelope.source_inventory(inputs)
        with self.assertRaisesRegex(ValueError, 'capture root differs'):
            write_envelope_content(inputs, 'envelope.json', 'parts.json', 'project/design.kicad_pcb',
                                   captured=(capture[0], self.source))
        self.assertEqual(board.read_bytes(), original)
        source = inputs / 'mechanical.json'
        source.write_bytes(source.read_bytes() + b'\n')
        with self.assertRaises(ValueError):
            write_envelope_content(inputs, 'envelope.json', 'parts.json', 'project/design.kicad_pcb',
                                   captured=capture)
        self.assertEqual(board.read_bytes(), original)
        source.unlink()
        with self.assertRaises(ValueError):
            write_envelope_content(inputs, 'envelope.json', 'parts.json', 'project/design.kicad_pcb')
        self.assertEqual(board.read_bytes(), original)
