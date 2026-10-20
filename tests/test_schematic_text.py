"""Native, measured readability regressions using original small symbols."""
import json
import tempfile
import unittest
from pathlib import Path

from agent_ee.executor import execute
from agent_ee.schematic_emitter import emit_schematic, publish
from agent_ee.schematic_text import collisions, measured_text, overlap, place_native_text, stroke_text
from tests.schematic_text_fixture import inputs
from tests.toolchain import require_toolchain


class StrokeBoundsTests(unittest.TestCase):
    def svg(self, path='M1 2 L3 4', extra='', desc='label'):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="30mm" height="20mm" viewBox="0 0 30 20">'
                f'<g style="stroke-width:0.2"><g class="stroked-text" {extra}>'
                f'<desc>{desc}</desc><path d="{path}"/></g></g></svg>')

    def test_ink_bounds_include_inherited_stroke_radius(self):
        self.assertEqual(stroke_text(self.svg())[0]['box'], [0.9, 1.9, 3.1, 4.1])
        self.assertFalse(overlap([0, 0, 1, 1], [1, 0, 2, 1]))
        self.assertTrue(overlap([0, 0, 1.01, 1], [1, 0, 2, 1]))

    def test_unknown_geometry_and_missing_ink_fail_closed(self):
        for data in (self.svg('M1 2 C3 4 5 6 7 8'), self.svg(''),
                     self.svg(extra='transform="scale(2)"'), self.svg(desc=''),
                     self.svg().replace('0 0 30 20', '0 0 3 2'),
                     self.svg().replace('<path d=', '<path transform="scale(2)" d='),
                     '<svg xmlns="http://www.w3.org/2000/svg" width="1mm"/>'):
            with self.subTest(svg=data), self.assertRaises(ValueError):
                stroke_text(data)

    def test_text_matching_requires_all_visible_fields_and_labels(self):
        intent, captured, layout = inputs()
        result, files = emit_schematic(intent, captured, layout)
        self.assertEqual(result['execution'], 'ok', result)
        with self.assertRaisesRegex(ValueError, 'missing rendered text'):
            measured_text(files['text.kicad_sch'], self.svg())


class NativeTextTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def emit_and_measure(self, tag, rotation=0, mirror=None, presentation='functional'):
        intent, captured, layout = inputs(rotation, mirror)
        layout['presentation'] = presentation
        result, files = emit_schematic(intent, captured, layout)
        self.assertEqual(result['execution'], 'ok', result)
        self.assertEqual(emit_schematic(intent, captured, layout)[1], files)
        project = self.root / tag
        publish(files, project)
        for name, argv in (
                ('svg', ['sch', 'export', 'svg', '--exclude-drawing-sheet', '-o', '/work/', '/candidate/text.kicad_sch']),
                ('netlist', ['sch', 'export', 'netlist', '--format', 'kicadxml', '-o', '/work/design.xml', '/candidate/text.kicad_sch']),
                ('erc', ['sch', 'erc', '--format', 'json', '--severity-all', '-o', '/work/erc.json', '/candidate/text.kicad_sch'])):
            record = execute(project, self.root / (tag + '-' + name), ['kicad-cli', *argv], timeout=60)
            self.assertEqual(record['exit_code'], 0, record)
        svg = (self.root / (tag + '-svg/text.svg')).read_bytes()
        texts = measured_text(files['text.kicad_sch'], svg)
        return texts, files

    def test_native_rotated_detached_parts_and_testpoint_have_no_overlaps(self):
        # The same deliberately crowded library fields remain defective in rows;
        # functional placement must repair their geometry without hiding text.
        before, rows = self.emit_and_measure('before', presentation='rows')
        self.assertTrue(collisions(before))
        from agent_ee.native_netlist import read_native_inventory
        reference = read_native_inventory(self.root / 'before-netlist/design.xml')
        for rotation, mirror in ((0, None), (90, None), (180, None), (270, None), (90, 'x'), (270, 'y')):
            tag = f'after-{rotation}-{mirror}'
            with self.subTest(rotation=rotation, mirror=mirror):
                texts, files = self.emit_and_measure(tag, rotation, mirror)
                self.assertEqual(collisions(texts), [])
                fields = [t for t in texts if t['kind'] == 'field']
                self.assertEqual({(t['reference'], t['field']) for t in fields},
                                 {('R18', 'Reference'), ('R18', 'Value'), ('TP1', 'Reference'), ('TP1', 'Value'),
                                  ('TP2', 'Reference'), ('TP2', 'Value')})
                actual = read_native_inventory(self.root / (tag + '-netlist/design.xml'))
                self.assertEqual(actual, reference)
                before_erc = json.loads((self.root / 'before-erc/erc.json').read_text())
                after_erc = json.loads((self.root / (tag + '-erc/erc.json')).read_text())
                # Library-table warnings retain the same type and affected text;
                # native coordinates and filenames are presentation metadata.
                def issues(report):
                    return sorted((v['type'], v['description']) for s in report['sheets'] for v in s['violations'])
                self.assertEqual(issues(after_erc), issues(before_erc))


    def test_opt_in_native_gate_retains_receipts_and_refuses_pin_text_overlap(self):
        intent, captured, layout = inputs()
        result, files = emit_schematic(intent, captured, layout, native_text_work=self.root / 'clean-native')
        self.assertEqual(result['execution'], 'ok', result)
        report = json.loads((self.root / 'clean-native/text-placement.json').read_text())
        self.assertEqual(report['after'], [])
        # Rows retain the original crowded pin-name drawing. The native gate
        # must refuse that unsupported repair rather than silently accept it.
        result, files = emit_schematic(intent, captured, dict(layout, presentation='rows'),
                                       native_text_work=self.root / 'crowded-native')
        self.assertIsNone(files)
        self.assertEqual(result['outcome'], 'unknown')
        self.assertTrue(any(f['code'] == 'presentation_text' for f in result['findings']), result)

    def test_native_extents_drive_field_movement_before_publication(self):
        from agent_ee.schematic_emitter import _dump, _scan
        from agent_ee.symbol_library import children
        intent, captured, layout = inputs()
        _, files = emit_schematic(intent, captured, layout)
        tree = _scan(files['text.kicad_sch'])
        symbol = next(n for n in children(tree, 'symbol')
                      if any(p[1:3] == ['Reference', 'R18'] for p in children(n, 'property')))
        value = next(p for p in children(symbol, 'property') if p[1] == 'Value')
        label = next(n for n in children(tree, 'label') if n[1] == 'INPUT_VOLTAGE')
        children(value, 'at')[0][1:3] = children(label, 'at')[0][1:3]
        draft = dict(files, **{'text.kicad_sch': ('\n'.join(_dump(tree)) + '\n').encode()})
        corrected = place_native_text(draft, self.root / 'move-native')
        report = json.loads((self.root / 'move-native/text-placement.json').read_text())
        self.assertTrue(report['before'])
        self.assertTrue(report['moved'])
        self.assertEqual(report['after'], [])
        self.assertNotEqual(draft, corrected)
        # The native tool's input draft is immutable; only new return bytes move.
        self.assertEqual((self.root / 'move-native/draft-0/text.kicad_sch').read_bytes(), draft['text.kicad_sch'])
