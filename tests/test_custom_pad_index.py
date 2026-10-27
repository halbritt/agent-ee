"""Owned custom-pad controls plus read-only, pinned pod capture qualification."""
import json
import os
import tempfile
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from agent_ee import native_run
from native_hooks import after
from agent_ee.footprint_library import canonical, index_footprints
from tests.test_footprint_library import capture, fixture, smd, owner_declarations, symbol_capture, declarations
from tests.toolchain import fome_mc_store, require_toolchain


# Original fixture geometry; stock KiCad bytes are never committed here.
PRIMITIVES = {
    'poly': '(gr_poly (pts (xy -1 -1) (xy 2 -1) (xy 2 1) (xy -1 1)) (width 0) (fill yes))',
    'rect': '(gr_rect (start -1 -1) (end 2 1) (width 0) (fill yes))',
    'circle': '(gr_circle (center 0 0) (end 2 0) (width 0) (fill yes))',
    'line': '(gr_line (start -2 0) (end 2 0) (width 0.4))',
}


def custom(primitive=PRIMITIVES['rect'], *, anchor='rect', at='0 0'):
    pad = smd('1', 0, shape='custom', extra='(zone_connect 2) '
              f'(options (clearance outline) (anchor {anchor})) (primitives {primitive}) ')
    return fixture('OwnedCustom', [pad.replace('(at 0 0)', f'(at {at})'), smd('2', 4)])


def indexed(text, **kwargs):
    manifest, files = capture(text, 'OwnedCustom')
    return index_footprints(manifest, files, **kwargs)


class CustomPadIndexTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)

    def test_each_supported_primitive_and_anchor_has_native_extent_and_number(self):
        expected = {'poly': [-1000000, -1000000, 2000000, 1000000],
                    'rect': [-1000000, -1000000, 2000000, 1000000],
                    'circle': [-2000000, -2000000, 2000000, 2000000],
                    'line': [-2200000, -500000, 2200000, 500000]}
        for kind, primitive in PRIMITIVES.items():
            for anchor in ('rect', 'circle'):
                with self.subTest(kind=kind, anchor=anchor):
                    result = indexed(custom(primitive, anchor=anchor))
                    self.assertEqual(result['coverage'], 'complete', result['findings'])
                    fp = result['footprints'][0]
                    self.assertEqual(fp['electrical_numbers'], ['1', '2'])
                    pad = next(p for p in fp['pads'] if p['number'] == '1')
                    self.assertEqual(pad['geometry'], 'native_custom')
                    self.assertEqual(pad['native_geometry']['number'], '1')
                    self.assertEqual(pad['native_geometry']['anchor'], anchor)
                    self.assertTrue(pad['native_geometry']['pcbnew_version'])
                    self.assertEqual(pad['native_geometry']['bbox_nm'], expected[kind])
                    self.assertEqual(pad['size_nm'], [1000000, 1000000])
        self.assertEqual(indexed(custom().replace('(zone_connect 2)', ''))['outcome'], 'unknown')

    def test_stroked_filled_and_unfilled_primitives_have_native_extents(self):
        for kind in ('poly', 'rect', 'circle'):
            for fill in ('yes', 'no'):
                with self.subTest(kind=kind, fill=fill):
                    primitive = PRIMITIVES[kind].replace('(width 0)', '(width 0.4)').replace('(fill yes)', f'(fill {fill})')
                    result = indexed(custom(primitive))
                    if fill == 'no':
                        self.assert_multi_outline_refusal(result)
                        continue
                    self.assertEqual(result['execution'], 'ok', result['findings'])
                    pad = next(p for p in result['footprints'][0]['pads'] if p['number'] == '1')
                    # KiCad's stroked circle bounding box conservatively adds 1 nm.
                    expected = [-2200001, -2200001, 2200001, 2200001] if kind == 'circle' else [-1200000, -1200000, 2200000, 1200000]
                    self.assertEqual(pad['native_geometry']['bbox_nm'], expected)

    def assert_multi_outline_refusal(self, result):
        self.assertEqual((result['execution'], result['outcome'], result['coverage']),
                         ('failed', 'unknown', 'missing'))
        self.assertEqual(result['footprints'], [])
        self.assertEqual(result['findings'][0]['code'], 'library_coverage')
        self.assertIn("custom pad '1'", result['findings'][0]['message'])
        self.assertIn('2 outlines', result['findings'][0]['message'])

    def test_unfilled_contours_connected_to_anchor_have_native_extents(self):
        for kind in ('poly', 'rect', 'circle'):
            primitive = PRIMITIVES[kind].replace('(width 0)', '(width 0.4)').replace('(fill yes)', '(fill no)')
            if kind == 'circle':
                primitive = primitive.replace('(center 0 0)', '(center 1.5 0)').replace('(end 2 0)', '(end 3.5 0)')
            else:
                primitive = primitive.replace('-1 -1', '-1 -0.5').replace('2 -1', '2 -0.5')
            for layer in ('F', 'B'):
                with self.subTest(kind=kind, layer=layer):
                    text = (custom(primitive).replace('F.Cu', f'{layer}.Cu')
                            .replace('F.Paste', f'{layer}.Paste').replace('F.Mask', f'{layer}.Mask'))
                    result = indexed(text)
                    self.assertEqual(result['coverage'], 'complete', result['findings'])
                    pad = next(p for p in result['footprints'][0]['pads'] if p['number'] == '1')
                    self.assertEqual(pad['geometry'], 'native_custom')
                    expected = ([-700001, -2200001, 3700001, 2200001] if kind == 'circle'
                                else [-1200000, -700000, 2200000, 1200000])
                    self.assertEqual(pad['native_geometry']['bbox_nm'], expected)

    def test_detached_primitive_refuses_even_beside_connected_copper(self):
        island = '(gr_rect (start 10 10) (end 11 11) (width 0) (fill yes))'
        for primitive in (island, PRIMITIVES['rect'] + island):
            for layer in ('F', 'B'):
                with self.subTest(primitive=primitive, layer=layer):
                    text = (custom(primitive, at='3 4 90').replace('F.Cu', f'{layer}.Cu')
                            .replace('F.Paste', f'{layer}.Paste').replace('F.Mask', f'{layer}.Mask'))
                    self.assert_multi_outline_refusal(indexed(text))

    def test_missing_or_invalid_native_outline_evidence_refuses(self):
        for count in (None, 0, True, '1', 1.0, -1):
            with self.subTest(count=count):
                def altered(call, result):
                    self.assertEqual(result['execution'], 'ok')
                    path = call.work / 'footprint.json'
                    report = json.loads(path.read_text())
                    for pad in report['pads']:
                        if pad['shape'] == 'custom':
                            if count is None:
                                pad.pop('outline_count', None)
                            else:
                                pad['outline_count'] = count
                    path.write_text(json.dumps(report))
                    return result
                with native_run.using(after(altered)):
                    result = indexed(custom())
                self.assertEqual((result['execution'], result['outcome'], result['coverage']),
                                 ('failed', 'unknown', 'missing'))
                self.assertEqual(result['footprints'], [])
                self.assertEqual(result['findings'][0]['code'], 'library_coverage')

    def test_translated_rotated_native_extent_and_determinism(self):
        text = custom(at='3 4 90')
        first = indexed(text)
        self.assertEqual(first['execution'], 'ok', first['findings'])
        self.assertEqual(first, indexed(text))
        pad = next(p for p in first['footprints'][0]['pads'] if p['number'] == '1')
        self.assertEqual(pad['native_geometry']['bbox_nm'], [2000000, 2000000, 4000000, 5000000])

    def test_unsupported_options_and_primitives_refuse(self):
        source = custom()
        cases = [
            source.replace('(zone_connect 2)', '(zone_connect 1)'),
            source.replace('(layers "F.Cu" "F.Paste" "F.Mask")', '(layers "F.Paste")'),
            source.replace('(layers "F.Cu" "F.Paste" "F.Mask")', '(layers "*.Cu" "*.Mask")'),
            source.replace('(zone_connect 2)', ''),
            source.replace('(anchor rect)', '(anchor oval)'),
            source.replace('(clearance outline)', '(clearance convexhull)'),
            source.replace('(zone_connect 2)', '(zone_connect 2) (thermal_gap 0.2)'),
            source.replace('(zone_connect 2)', '(zone_connect 2) (padstack (mode front_inner_back))'),
            source.replace('(width 0)', '(width 0) (future_option yes)'),
            source.replace('(fill yes)', '(fill maybe)'),
            source.replace('(width 0)', '(width -0.1)'),
            source.replace('(fill yes)', '(fill no)'),
            source.replace('(zone_connect 2)', '(zone_connect 2) (zone_connect 2)'),
            custom('(gr_arc (start 0 0) (mid 1 1) (end 2 0) (width 0.2))'),
            custom('(gr_line (start 0 0) (end 0 0) (width 0.2))'),
            custom('(gr_poly (pts (xy 0 0) (xy 2 2) (xy 0 2) (xy 2 0)) (width 0) (fill yes))'),
            custom(PRIMITIVES['rect'] * 129),
        ]
        for n, text in enumerate(cases):
            with self.subTest(case=n):
                result = indexed(text)
                self.assertEqual((result['execution'], result['outcome'], result['coverage']),
                                 ('failed', 'unknown', 'missing'))
                self.assertEqual(result['footprints'], [])

    def test_primitive_magnitude_bounds_refuse_without_native_loading(self):
        cases = [
            PRIMITIVES['rect'].replace('(end 2 1)', '(end 3000 1)'),
            PRIMITIVES['rect'].replace('(start -1 -1)', '(start -3000 -1)'),
            PRIMITIVES['poly'].replace('(xy 2 -1)', '(xy 3000 -1)'),
            PRIMITIVES['line'].replace('(end 2 0)', '(end 0 3000)'),
            PRIMITIVES['circle'].replace('(center 0 0)', '(center 0 -3000)'),
            PRIMITIVES['circle'].replace('(end 2 0)', '(end 3000 0)'),
            PRIMITIVES['line'].replace('(width 0.4)', '(width 3000)'),
            # Individually bounded circle coordinates can still overflow its radius.
            '(gr_circle (center -1000 -1000) (end 1000 1000) (width 0) (fill yes))',
            PRIMITIVES['rect'].replace('(end 2 1)', '(end 1000.000001 1)'),
            PRIMITIVES['line'].replace('(width 0.4)', '(width 1000.000001)'),
            PRIMITIVES['rect'].replace('(end 2 1)', '(end 1000 1)').replace('(width 0)', '(width 0.000001)'),
            '(gr_circle (center 500 0) (end -1 0) (width 0) (fill yes))',
            '(gr_circle (center 0 0) (end 1000 0) (width 0.000001) (fill yes))',
        ]
        for primitive in cases:
            with self.subTest(primitive=primitive), native_run.using(native_run.Recorded()) as execute:
                result = indexed(custom(primitive))
                self.assertEqual((result['execution'], result['outcome'], result['coverage']),
                                 ('failed', 'unknown', 'missing'))
                self.assertEqual(result['footprints'], [])
                self.assertEqual(result['findings'][0]['code'], 'library_coverage')
                self.assertIn('1000 mm', result['findings'][0]['message'])
                self.assertEqual(execute.calls, [])

    def test_rotation_invariant_reach_refuses_before_native_loading(self):
        square = '(gr_rect (start -1000 -1000) (end 1000 1000) (width 0) (fill yes))'
        rect = '(gr_rect (start -1 -1) (end 500 1) (width 0) (fill yes))'
        cases = [
            (square, '0 0 10'),
            (square, '0 0 45'),
            (square.replace('1000', '760'), '0 0 45'),
            (rect, '1500 0'),
            (rect, '-1500 0'),
            # The farthest rectangle corner is neither start nor end.
            ('(gr_rect (start 600 0) (end 0 900) (width 0) (fill yes))', '0 0'),
            ('(gr_poly (pts (xy 0 0) (xy 800 800) (xy 0 1)) (width 0) (fill yes))', '0 0'),
            ('(gr_line (start 0 0) (end 800 800) (width 0.2))', '0 0'),
            ('(gr_circle (center 700 700) (end 720 700) (width 0) (fill yes))', '0 0'),
            # Integer ceiling must not round a diagonal or half-nm stroke down.
            ('(gr_line (start 0 0) (end 999.999999 0.000001) (width 0.000001))', '0 0'),
            ('(gr_line (start 0 0) (end 999 0) (width 0.000001))', '1 0'),
            ('(gr_circle (center 0 0) (end 999 0) (width 0) (fill yes))', '1 0.000001'),
        ]
        for primitive, at in cases:
            with self.subTest(primitive=primitive, at=at), native_run.using(native_run.Recorded()) as execute:
                result = indexed(custom(primitive, at=at))
                self.assertEqual((result['execution'], result['outcome'], result['coverage']),
                                 ('failed', 'unknown', 'missing'))
                self.assertEqual(result['footprints'], [])
                self.assertEqual(result['findings'][0]['code'], 'library_coverage')
                self.assertIn('custom-pad copper reach exceeds 1000 mm from footprint origin',
                              result['findings'][0]['message'])
                self.assertEqual(execute.calls, [])

    def test_bounded_rotated_translated_reach_keeps_exact_native_extents(self):
        cases = [
            ('(gr_rect (start -700 -700) (end 700 700) (width 0) (fill yes))', '0 0 45',
             [-989949494, -989949494, 989949494, 989949494]),
            ('(gr_rect (start -500 -500) (end 500 500) (width 0) (fill yes))', '0 0 45',
             [-707106781, -707106781, 707106781, 707106781]),
            ('(gr_rect (start -999 -1) (end 999 1) (width 0) (fill yes))', '0 0 33.3',
             [-835520577, -549309603, 835520577, 549309603]),
            ('(gr_rect (start -1 -1) (end 600 1) (width 0) (fill yes))', '300 0 45',
             [298585786, -424971175, 724971175, 1414214]),
        ]
        for primitive, at, bbox in cases:
            with self.subTest(primitive=primitive, at=at):
                result = indexed(custom(primitive, at=at))
                self.assertEqual(result['coverage'], 'complete', result['findings'])
                pad = next(p for p in result['footprints'][0]['pads'] if p['number'] == '1')
                self.assertEqual(pad['native_geometry']['bbox_nm'], bbox)

    def test_primitive_magnitude_boundary_is_inclusive(self):
        cases = [
            ('(gr_line (start -500 0) (end 500 0) (width 1000))',
             [-1000000000, -500000000, 1000000000, 500000000]),
            ('(gr_line (start -1 0) (end 1 0) (width 1000))',
             [-501000000, -500000000, 501000000, 500000000]),
            ('(gr_circle (center 0 0) (end 1000 0) (width 0) (fill yes))',
             [-1000000000, -1000000000, 1000000000, 1000000000]),
        ]
        for primitive, bbox in cases:
            with self.subTest(primitive=primitive):
                result = indexed(custom(primitive))
                self.assertEqual(result['coverage'], 'complete', result['findings'])
                pad = next(p for p in result['footprints'][0]['pads'] if p['number'] == '1')
                self.assertEqual(pad['native_geometry']['bbox_nm'], bbox)

    def test_native_crash_or_missing_output_never_qualifies(self):
        # Transport fault injection tests refusal only; successful cases above use real pcbnew.
        for status in ('failed', 'ok'):
            fault = native_run.Recorded(lambda call: native_run.Outcome(
                exit_code=1 if status == 'failed' else 0, stdout=b'injected child failure'))
            with self.subTest(status=status), native_run.using(fault):
                result = indexed(custom())
                self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'))
                self.assertEqual(result['footprints'], [])

    def test_ambiguous_custom_identity_refuses(self):
        source = custom()
        # Identical custom anchors cannot be bound uniquely to native pads.
        pad = source[source.index('(pad "1"'):source.index('(pad "2"')]
        result = indexed(source.replace('(pad "2"', pad + '(pad "2"'))
        self.assertEqual(result['outcome'], 'unknown')
        self.assertIn('ambiguous', result['findings'][0]['message'])

    def test_declared_custom_primitive_bounds_refuse_before_native_load(self):
        declared = owner_declarations([('Owned:OwnedCustom', dict(custom_pads=['1']))])
        cases = [
            ('(gr_rect (start 1500 0) (end 1501 1) (width 0) (fill yes))', '0 0'),
            ('(gr_rect (start -760 -760) (end 760 760) (width 0) (fill yes))', '0 0 45'),
            ('(gr_line (start 0 0) (end 999 0) (width 0.000001))', '1 0'),
            ('(gr_circle (center 500 0) (end -1 0) (width 0) (fill yes))', '0 0'),
            (PRIMITIVES['line'].replace('0.4', '1000.000001'), '0 0'),
            ('(gr_poly (pts (xy 0 0) (xy 800 800) (xy 0 1)) (width 0) (fill yes))', '0 0'),
        ]
        for primitive, at in cases:
            with self.subTest(primitive=primitive), native_run.using(native_run.Recorded()) as execute:
                result = indexed(custom(primitive, at=at), declarations=declared)
                self.assertEqual((result['execution'], result['outcome'], result['coverage']),
                                 ('failed', 'unknown', 'missing'))
                self.assertEqual(result['footprints'], [])
                self.assertEqual(result['findings'][0]['code'], 'library_coverage')
                self.assertIn('1000 mm', result['findings'][0]['message'])
                self.assertEqual(execute.calls, [])

    def test_declared_custom_one_outline_on_each_copper_layer(self):
        declared = owner_declarations([('Owned:OwnedCustom', dict(custom_pads=['1']))])
        for layers in ('"F.Cu" "F.Mask"', '"B.Cu" "B.Mask"', '"*.Cu" "*.Mask"'):
            for primitive in (PRIMITIVES['rect'],
                              '(gr_rect (start 10 10) (end 11 11) (width 0) (fill yes))'):
                with self.subTest(layers=layers, primitive=primitive):
                    text = custom(primitive).replace('"F.Cu" "F.Paste" "F.Mask"', layers)
                    result = indexed(text, declarations=declared)
                    if '10 10' in primitive:
                        self.assert_multi_outline_refusal(result)
                    else:
                        self.assertEqual(result['coverage'], 'complete', result['findings'])
                        pad = next(p for p in result['footprints'][0]['pads'] if p['number'] == '1')
                        self.assertEqual(pad['geometry'], 'declared_custom')
                        self.assertNotIn('native_geometry', pad)

    def test_declared_custom_missing_outline_evidence_refuses(self):
        declared = owner_declarations([('Owned:OwnedCustom', dict(custom_pads=['1']))])
        cases = [(False, counts) for counts in (None, [], [True], ['1'], [1.0], [0], [1, 2])]
        cases.extend([(True, [1]), (True, [1] * 31)])
        for multilayer, counts in cases:
            with self.subTest(multilayer=multilayer, counts=counts):
                def altered(call, execution):
                    self.assertEqual(execution['execution'], 'ok')
                    path = call.work / 'footprint.json'
                    report = json.loads(path.read_text())
                    for pad in report['pads']:
                        if pad['shape'] == 'custom':
                            if counts is None:
                                pad.pop('layer_outline_counts')
                            else:
                                pad['layer_outline_counts'] = counts
                    path.write_text(json.dumps(report))
                    return execution
                with native_run.using(after(altered)):
                    text = custom().replace('F.Cu', '*.Cu') if multilayer else custom()
                    result = indexed(text, declarations=declared)
                self.assertEqual((result['execution'], result['outcome'], result['coverage']),
                                 ('failed', 'unknown', 'missing'))
                self.assertEqual(result['footprints'], [])
                self.assertEqual(result['findings'][0]['code'], 'library_coverage')

    def test_declared_custom_normalized_angles_and_duplicates_preserve_numbering(self):
        declared = owner_declarations([('Owned:OwnedCustom', dict(custom_pads=['1']))])
        for angle in (360, -360, 450, 720, -450):
            with self.subTest(angle=angle):
                result = indexed(custom(at=f'0 0 {angle}'), declarations=declared)
                self.assertEqual(result['coverage'], 'complete', result['findings'])
                pad = next(p for p in result['footprints'][0]['pads'] if p['number'] == '1')
                self.assertEqual(pad['orientation_microdegrees'], angle * 1000000)
                self.assertEqual(pad['geometry'], 'declared_custom')
                self.assertNotIn('native_geometry', pad)
                self.assertEqual(indexed(custom(at=f'0 0 {angle}'))['coverage'], 'missing')
        source = custom()
        pad = source[source.index('(pad "1"'):source.index('(pad "2"')]
        for duplicate in (pad, pad.replace('"F.Cu" "F.Paste" "F.Mask"', '"*.Cu" "*.Mask"')):
            with self.subTest(duplicate=duplicate):
                result = indexed(source.replace('(pad "2"', duplicate + '(pad "2"'), declarations=declared)
                self.assertEqual(result['coverage'], 'complete', result['findings'])
                self.assertEqual(len(result['footprints'][0]['pads']), 3)

    def test_numbering_declaration_does_not_claim_native_geometry(self):
        declared = owner_declarations([('Owned:OwnedCustom', dict(custom_pads=['1']))])
        result = indexed(custom(), declarations=declared)
        self.assertEqual(result['execution'], 'ok', result['findings'])
        pad = next(p for p in result['footprints'][0]['pads'] if p['number'] == '1')
        self.assertEqual(pad['geometry'], 'declared_custom')
        self.assertNotIn('native_geometry', pad)

    def test_numbering_only_multilayer_custom_beside_qualified_pad_is_preserved(self):
        declared = owner_declarations([('Owned:OwnedCustom', dict(custom_pads=['2']))])
        other = smd('2', 4, shape='custom', layers='"*.Cu" "*.Mask"',
                    extra='(options (clearance outline) (anchor rect)) '
                          f'(primitives {PRIMITIVES["rect"]})')
        result = indexed(custom().replace(smd('2', 4), other), declarations=declared)
        self.assertEqual(result['coverage'], 'complete', result['findings'])
        pads = {p['number']: p for p in result['footprints'][0]['pads']}
        self.assertEqual(pads['1']['geometry'], 'native_custom')
        self.assertEqual(pads['2']['geometry'], 'declared_custom')
        self.assertNotIn('native_geometry', pads['2'])

    def test_native_number_disagreement_refuses(self):
        def altered(call, result):
            self.assertEqual(result['execution'], 'ok')
            path = call.work / 'footprint.json'
            report = json.loads(path.read_text())
            report['pads'][0]['number'] = 'wrong'
            path.write_text(json.dumps(report))
            return result
        with native_run.using(after(altered)):
            result = indexed(custom())
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'))
        self.assertIn('native pad identity differs', result['findings'][0]['message'])

    def test_assignment_cli_uses_native_custom_pad_numbers(self):
        from agent_ee.executor import execute
        manifest, files = capture(custom(), 'OwnedCustom')
        lib, data = symbol_capture(('1', '2'))
        files[lib['path']] = data
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for path, data in files.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            (root / 'manifest.json').write_text(json.dumps(manifest))
            selected = declarations(lib, footprint='Owned:OwnedCustom')
            (root / 'assignments.json').write_text(json.dumps(selected))
            work = root / 'work'
            execution = execute(root, work, ['/usr/bin/python3', '-B', '-m', 'agent_ee.footprint_library',
                '/candidate/manifest.json', '--captures', '/candidate',
                '--assignments', '/candidate/assignments.json', '--out', '/work/result.json'])
            self.assertEqual(execution['execution'], 'ok', (work / 'console.log').read_text())
            report = json.loads((work / 'result.json').read_text())
            self.assertEqual(report['outcome'], 'pass', report['findings'])
            self.assertEqual(report['index']['footprints'][0]['electrical_numbers'], ['1', '2'])

    def test_captured_pod_jumper_and_39_unchanged_records(self):
        commit = 'e01e97d3888167053cd06016752bffc471add4a6'
        repo = fome_mc_store(os.environ.get('AGENT_EE_FOME_MC_CHECKOUT', '/home/halbritt/git/fome-mc'), commit)
        prefix = 'telemetry-pod/expansion/'
        def blob(path):
            return subprocess.check_output(['git', '-C', str(repo), 'show', commit + ':' + prefix + path])
        manifest = json.loads(blob('board-preflight/footprints.json'))
        previous = json.loads(blob('board-preflight/footprint-report.json'))
        captures = {m['path']: blob('series-divider/native-candidate/inputs/' + m['path'])
                    for lib in manifest['libraries'] for m in lib['members']}
        after = index_footprints(manifest, captures)
        self.assertEqual(previous['index']['coverage'], 'missing')
        self.assertEqual(len(previous['index']['footprints']), 39)
        self.assertEqual(after['coverage'], 'complete', after['findings'])
        self.assertEqual(len(after['footprints']), 40)
        original = {f['lib_id']: canonical(f) for f in previous['index']['footprints']}
        for fp in after['footprints']:
            if fp['lib_id'] in original:
                self.assertEqual(canonical(fp), original[fp['lib_id']])
        # Independent test oracle over the captured XML (the full inventory exceeds
        # current production design-size ceilings; AGENTEE-298 owns that change).
        xml = ET.fromstring(blob('series-divider/native-candidate/native.xml'))
        parts = {(p.get('lib'), p.get('part')): {n.get('num') for n in p.findall('./pins/pin')}
                 for p in xml.findall('./libparts/libpart')}
        footprints = {f['lib_id']: f for f in after['footprints']}
        matched = []
        for comp in xml.findall('./components/comp'):
            source = comp.find('libsource')
            self.assertEqual(set(footprints[comp.findtext('footprint')]['electrical_numbers']),
                             parts[source.get('lib'), source.get('part')], comp.get('ref'))
            matched.append(comp.get('ref'))
        self.assertEqual(len(matched), 327)
        self.assertTrue({'JP1', 'JP2', 'JP3', 'JP4', 'JP5'} <= set(matched))


if __name__ == '__main__':
    unittest.main()
