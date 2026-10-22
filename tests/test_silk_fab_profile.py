"""Dated JLCPCB silkscreen (legend) limits through the existing source-bound native check.

Pad-to-silk and silk-to-mask stay distinct; recommendations are advisories only.
Boards are owned synthetic fixtures; only the cited numbers come from the capture.
"""
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import tests.test_fab_profile as base
from agent_ee.fab_profile import capability, compile_profile, qualify_silk
from agent_ee.storage import digest

PRESET = Path(__file__).resolve().parents[1] / 'fixtures/fab-profiles/jlcpcb-silkscreen-2026-10-22'
MEASURED = ('pad-to-silk', 'silk-line-width', 'silk-text-height')


def silk_profile(inputs, limits=None):
    document = json.loads((PRESET / 'profile.json').read_text())
    shutil.copyfile(PRESET / 'jlcpcb-silkscreen.txt', inputs / 'jlcpcb-silkscreen.txt')
    if limits is not None:
        document['limits'] = [limit for limit in document['limits'] if limit['id'] in limits]
    return document


class SilkProfileContractTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.inputs = Path(tmp.name)
        self.document = silk_profile(self.inputs)

    def compile(self, document=None):
        files = {p.name: digest(p) for p in self.inputs.iterdir()}
        return compile_profile(document or self.document, files, self.inputs)

    def by_id(self, compiled):
        return {c['id']: c for c in compiled['constraints']}

    def test_dated_preset_binds_distinct_pad_and_mask_sources(self):
        records = self.by_id(self.compile())
        self.assertEqual(sorted(records), ['pad-to-silk', 'silk-line-width', 'silk-text-height', 'silk-to-mask'])
        self.assertEqual({records[i]['execution'] for i in MEASURED}, {'ready'})
        pad, mask = records['pad-to-silk'], records['silk-to-mask']
        self.assertEqual((pad['expected']['source']['page'], mask['expected']['source']['page']), (1, 2))
        self.assertIn('pad to silkscreen', pad['expected']['source']['quote'])
        self.assertIn('silkscreen to solder mask clearance', mask['expected']['source']['quote'])
        self.assertIn("(condition \"B.Type == 'Pad'\")", pad['rules'])
        self.assertIn('(constraint silk_clearance (min 0.15mm))', pad['rules'])
        self.assertEqual(pad['native_types'], ['silk_over_copper', 'silk_overlap'])
        # Silk-to-mask is sourced but has no qualified measurement: unknown, never a rule.
        self.assertEqual((mask['execution'], mask['code']), ('unsupported', 'silk_to_mask_unmeasured'))
        self.assertNotIn('rules', mask)
        height = records['silk-text-height']
        self.assertEqual((height['expected']['value'], height['expected']['source']['page']), (1.0, 1))
        self.assertIn('(constraint text_height (min 1mm))', height['rules'])
        self.assertIn('(constraint text_thickness (min 0.15mm))', records['silk-line-width']['rules'])
        for record in (pad, height, records['silk-line-width']):
            self.assertEqual(record['project_rules'], {})
            self.assertIn(f'(rule "agent_ee_{record["id"]}"', record['rules'])

    def test_guide_text_height_and_recommendations_are_sourced_advisories_only(self):
        compiled = self.compile()
        advisories = {a['id']: a for a in compiled['advisories']}
        self.assertEqual({a['execution'] for a in advisories.values()}, {'ready'})
        self.assertEqual({(a['expected']['key'], a['expected']['value'], a['expected']['role'])
                          for a in advisories.values()},
                         {('min_silk_text_height_mm', .8, 'guide_minimum'),
                          ('min_silk_line_width_mm', .2, 'recommended'),
                          ('min_silk_to_mask_mm', .25, 'recommended')})
        # Both text-height sources keep their provenance; only the capability minimum grades.
        self.assertEqual(advisories['guide-text-height']['expected']['source']['page'], 2)
        values = [c['expected']['value'] for c in compiled['constraints']]
        self.assertNotIn(.8, values); self.assertNotIn(.2, values); self.assertNotIn(.25, values)
        self.assertFalse(any('rules' in a or 'native_types' in a for a in advisories.values()))
        # An unverified advisory refuses the descriptor before any native run.
        broken = copy.deepcopy(self.document)
        broken['advisories'][0]['source']['quote'] = 'legend / character height / absolute minimum: 0.5 mm'
        (self.inputs / 'profile.json').write_text(json.dumps(broken))
        files = {p.name: digest(p) for p in self.inputs.iterdir()}
        with self.assertRaisesRegex(ValueError, 'advisory source not verified'):
            capability({'fab_profile': 'profile.json'}, {}, files, self.inputs, 'fab')
        self.assertEqual(sorted('fab:' + c['id'] for c in compiled['constraints']
                                if c['execution'] == 'ready' and c['expected']['tier'] == 'minimum'),
                         ['fab:' + i for i in MEASURED])

    def test_recommendations_cannot_become_limits_or_charges(self):
        original = copy.deepcopy(self.document)
        for index in range(len(original['limits'])):
            self.document = copy.deepcopy(original)
            self.document['limits'][index]['tier'] = 'preferred'
            with self.subTest(limit=index), self.assertRaisesRegex(ValueError, 'minimum-tier only'):
                self.compile()
        for mutate, message in ((lambda a: a.update(role='minimum'), 'role'),
                                (lambda a: a.update(role='preferred'), 'role'),
                                (lambda a: a.update(tier='minimum'), 'exactly'),
                                (lambda a: a.update(unit='mil'), 'unit'),
                                (lambda a: a.update(value=0), 'positive'),
                                (lambda a: a.update(key='min_silk_to_silk_mm'), 'key')):
            self.document = copy.deepcopy(original)
            mutate(self.document['advisories'][0])
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.compile()
        self.document = copy.deepcopy(original)
        self.document['advisories'].append(copy.deepcopy(self.document['advisories'][0]))
        self.document['advisories'][-1]['id'] = 'copy'
        with self.assertRaisesRegex(ValueError, 'duplicate advisory'):
            self.compile()

    def test_no_silk_to_silk_rule_and_v1_refuses_silk_keys(self):
        bad = copy.deepcopy(self.document)
        bad['limits'][0]['key'] = 'min_silk_to_silk_mm'
        with self.assertRaisesRegex(ValueError, 'unsupported fabricator limit key'):
            self.compile(bad)
        v1 = copy.deepcopy(self.document)
        v1['schema'] = 'agent-ee.fab-profile/1'
        with self.assertRaisesRegex(ValueError, 'no defaults'):
            self.compile(v1)
        del v1['advisories']
        with self.assertRaisesRegex(ValueError, 'unsupported fabricator limit key'):
            self.compile(v1)

    def test_missing_changed_or_misquoted_capture_is_unsupported(self):
        original = copy.deepcopy(self.document)
        for mutation in ('missing', 'changed', 'misquoted', 'wrong-page'):
            with self.subTest(mutation=mutation):
                self.document = copy.deepcopy(original)
                silk_profile(self.inputs)
                source = self.inputs / 'jlcpcb-silkscreen.txt'
                if mutation == 'missing':
                    source.unlink()
                elif mutation == 'changed':
                    source.write_bytes(source.read_bytes() + b'\n')
                elif mutation == 'misquoted':
                    self.document['limits'][0]['source']['quote'] = 'legend / pad to silkscreen / minimum: 0.1 mm'
                else:
                    self.document['limits'][0]['source']['page'] = 2
                compiled = self.compile()
                pad = self.by_id(compiled)['pad-to-silk']
                self.assertEqual(pad['execution'], 'unsupported', pad)
                self.assertNotIn('rules', pad)
                if mutation in ('missing', 'changed'):
                    self.assertEqual({a['execution'] for a in compiled['advisories']}, {'unsupported'})

    def test_capture_manifest_matches_committed_record(self):
        capture = json.loads((PRESET / 'source-capture.json').read_text())
        self.assertFalse(capture['raw_bytes_committed'])
        self.assertEqual([r['url'] for r in capture['raw_captures']],
                         ['https://jlcpcb.com/capabilities/pcb-capabilities/', 'https://jlcpcb.com/blog/pcb-silkscreen'])
        for raw in capture['raw_captures']:
            self.assertRegex(raw['sha256'], r'\A[0-9a-f]{64}\Z')
            self.assertTrue(raw['captured_at'].startswith(capture['as_of']))
            self.assertFalse((PRESET / raw['file']).exists())
        for recorded in capture['recorded_sources'].values():
            self.assertEqual(digest(PRESET / recorded['path']), recorded['sha256'])
            self.assertEqual((PRESET / recorded['path']).stat().st_size, recorded['bytes'])
        document = json.loads((PRESET / 'profile.json').read_text())
        self.assertEqual(document['as_of'], capture['as_of'])
        self.assertEqual({s['source']['sha256'] for s in document['limits'] + document['advisories']},
                         {capture['recorded_sources']['jlcpcb-silkscreen.txt']['sha256']})


def text(uid, visible=True, stroke=True, knockout=False, **extra):
    return {'uuid': uid, 'class': 'PCB_TEXT', 'layers': ['F.SilkS'], 'visible': visible,
            'stroke_font': stroke, 'font_name': '' if stroke else 'Owned Outline', 'knockout': knockout,
            'height_nm': 1000000, 'thickness_nm': 150000, **extra}


def graphic(uid, width, filled=False):
    return {'uuid': uid, 'class': 'PCB_SHAPE', 'layers': ['F.SilkS'], 'shape': 'Line',
            'width_nm': width, 'filled': filled}


class SilkQualificationTest(unittest.TestCase):
    """Applicability gates on native silk records, before any grade is reported."""

    def record(self, key, status='satisfied', findings=(), value=.15):
        return {'id': 'r', 'requirement': 'fab', 'execution': 'ok', 'status': status,
                'expected': {'key': key, 'value': value, 'tier': 'minimum'},
                'native_findings': list(findings)}

    def qualify(self, record, **silk):
        inventory = {'silk': {'texts': [], 'graphics': [], 'other': [], **silk}}
        return qualify_silk([record], inventory)[0]

    def test_missing_inventory_and_unqualified_items_are_unknown(self):
        record = qualify_silk([self.record('min_pad_to_silk_mm')], {})[0]
        self.assertEqual((record['execution'], record['status'], record['code']),
                         ('unsupported', 'not_evaluated', 'silk_inventory_missing'))
        for key in ('min_pad_to_silk_mm', 'min_silk_text_height_mm', 'min_silk_line_width_mm'):
            other = [{'uuid': 'd', 'class': 'PCB_DIM_ALIGNED', 'layers': ['F.SilkS']}]
            result = self.qualify(self.record(key, 'violation', [{'type': 'x', 'items': [{'uuid': 'a'}]}]),
                                  other=other)
            with self.subTest(key=key):
                self.assertEqual((result['status'], result['code'], result['native_status']),
                                 ('not_evaluated', 'silk_item_unqualified', 'violation'))
                self.assertEqual(result['unqualified_silk'], other)

    def test_hidden_text_outline_fonts_and_knockout_are_unknown(self):
        finding = {'type': 'text_height', 'items': [{'uuid': 'h'}]}
        hidden = self.qualify(self.record('min_silk_text_height_mm', 'violation', [finding]),
                              texts=[text('h', visible=False)])
        self.assertEqual(hidden['code'], 'hidden_silk_text')
        uncited = self.qualify(self.record('min_silk_text_height_mm'), texts=[text('h', visible=False)])
        self.assertEqual((uncited['execution'], uncited['status']), ('ok', 'satisfied'))
        for name, item in (('outline', text('t', stroke=False)), ('knockout', text('t', knockout=True))):
            for key in ('min_silk_text_height_mm', 'min_silk_line_width_mm'):
                with self.subTest(case=name, key=key):
                    result = self.qualify(self.record(key), texts=[item])
                    self.assertEqual((result['status'], result['code']), ('not_evaluated', 'silk_text_unqualified'))
            # Copper clearance measures the real glyph outline; the font does not gate it.
            result = self.qualify(self.record('min_pad_to_silk_mm'), texts=[item])
            self.assertEqual((result['execution'], result['status']), ('ok', 'satisfied'))

    def test_tented_pad_finding_is_unknown_not_a_pad_to_silk_defect(self):
        tented = {'type': 'silk_overlap', 'items': [{'uuid': 's'}, {'uuid': 'p'}]}
        exposed = {'type': 'silk_over_copper', 'items': [{'uuid': 's2'}, {'uuid': 'p2'}]}
        result = self.qualify(self.record('min_pad_to_silk_mm', 'violation', [exposed, tented]))
        self.assertEqual((result['status'], result['code'], result['native_status']),
                         ('not_evaluated', 'pad_to_silk_tented_pad', 'violation'))
        result = self.qualify(self.record('min_pad_to_silk_mm', 'violation', [exposed]))
        self.assertEqual((result['execution'], result['status']), ('ok', 'violation'))

    def test_graphic_line_width_is_exact_saved_nanometres(self):
        for width, status in ((149999, 'violation'), (150000, 'satisfied'), (200000, 'satisfied')):
            with self.subTest(width=width):
                result = self.qualify(self.record('min_silk_line_width_mm'), graphics=[graphic('g', width)])
                self.assertEqual((result['execution'], result['status']), ('ok', status))
                if status == 'violation':
                    self.assertEqual(result['offender_ids'], ['g'])
                    self.assertEqual(result['silk_graphic_violations'], [graphic('g', width)])
        border = text('b', border_width_nm=100000, **{'class': 'PCB_TEXTBOX'})
        result = self.qualify(self.record('min_silk_line_width_mm'), texts=[border])
        self.assertEqual((result['status'], result['offender_ids']), ('violation', ['b']))
        for item in (graphic('f', 0, filled=True), graphic('z', 0), graphic('f2', 200000, filled=True)):
            with self.subTest(item=item['uuid']):
                result = self.qualify(self.record('min_silk_line_width_mm'), graphics=[item, graphic('g', 100000)])
                self.assertEqual((result['status'], result['code'], result['native_status']),
                                 ('not_evaluated', 'silk_graphic_unmeasured', 'violation'))
        # Graphic widths never grade text height or pad clearance.
        for key in ('min_silk_text_height_mm', 'min_pad_to_silk_mm'):
            result = self.qualify(self.record(key), graphics=[graphic('g', 1), graphic('f', 0, filled=True)])
            self.assertEqual((result['execution'], result['status']), ('ok', 'satisfied'))

    def test_non_silk_and_unevaluated_records_are_untouched(self):
        other = {'id': 'v', 'execution': 'ok', 'status': 'satisfied',
                 'expected': {'key': 'min_via_drill_mm', 'value': .15, 'tier': 'minimum'}}
        failed = {**self.record('min_pad_to_silk_mm'), 'execution': 'failed', 'status': 'not_evaluated'}
        records = [copy.deepcopy(other), copy.deepcopy(failed)]
        self.assertEqual(qualify_silk(records, {}), [other, failed])


MM = 1_000_000


def silk_board(saved, *, gap=0.2, margin=0.05, tented=False, back_gap=None, text_size=1.0, thickness=0.15,
               line=0.15, hidden_small=False, outline_font=False):
    """Owned board: one pad with a board silk line `gap` mm from its copper, plus text.

    `saved` receives role -> saved UUID, read back from the saved board.
    """
    def build(path, **_):
        import pcbnew as k
        def v(x, y):
            return k.VECTOR2I(round(x*MM), round(y*MM))
        board = k.BOARD(); board.SetCopperLayerCount(2)
        corners = ((0, 0), (30, 0), (30, 20), (0, 20))
        for start, end in zip(corners, corners[1:] + corners[:1]):
            edge = k.PCB_SHAPE(board, k.SHAPE_T_SEGMENT); edge.SetLayer(k.Edge_Cuts)
            edge.SetStart(v(*start)); edge.SetEnd(v(*end)); edge.SetWidth(50000); board.Add(edge)

        def pad_at(ref, x, layers):
            fp = k.FOOTPRINT(board); fp.SetReference(ref); fp.SetPosition(v(x, 10)); board.Add(fp)
            fp.Reference().SetLayer(k.F_Fab)
            if hidden_small and ref == 'U1':
                # Saved hidden field on silk: neither plotted nor graded by native DRC.
                value = fp.Value(); value.SetText('HID'); value.SetLayer(k.F_SilkS); value.SetVisible(False)
                value.SetPosition(v(20, 3)); value.SetTextSize(v(.5, .5)); value.SetTextThickness(80000)
            pad = k.PAD(fp); pad.SetNumber('1'); pad.SetAttribute(k.PAD_ATTRIB_SMD); pad.SetSize(v(1, 1))
            layer_set = k.LSET()
            for layer in layers:
                layer_set.AddLayer(layer)
            pad.SetLayerSet(layer_set); pad.SetPosition(v(x, 10)); pad.SetLocalSolderMaskMargin(round(margin*MM))
            fp.Add(pad)

        def silk(x, offset, layer, width=line, side=-1):
            shape = k.PCB_SHAPE(board, k.SHAPE_T_SEGMENT); shape.SetLayer(layer); shape.SetWidth(round(width*MM))
            edge = x + side * (0.5 + offset + width/2)
            shape.SetStart(v(edge, 9)); shape.SetEnd(v(edge, 11)); board.Add(shape)

        # The runner's rule-loading witness disallows tracks, so the board needs one.
        net = k.NETINFO_ITEM(board, 'W'); board.Add(net)
        track = k.PCB_TRACK(board); track.SetStart(v(25, 18)); track.SetEnd(v(28, 18))
        track.SetLayer(k.F_Cu); track.SetWidth(200000); track.SetNet(net); board.Add(track)
        pad_at('U1', 10, [k.F_Cu] if tented else [k.F_Cu, k.F_Mask])
        silk(10, gap, k.F_SilkS)
        if back_gap is not None:
            pad_at('U2', 20, [k.B_Cu, k.B_Mask])
            silk(20, back_gap, k.B_SilkS)
            silk(20, back_gap, k.F_SilkS, 0.15, side=1)  # front silk beside a back-only pad
        label = k.PCB_TEXT(board); label.SetText('AE'); label.SetLayer(k.F_SilkS); label.SetPosition(v(5, 3))
        label.SetTextSize(v(text_size, text_size)); label.SetTextThickness(round(thickness*MM)); board.Add(label)
        k.SaveBoard(str(path), board)
        if outline_font:
            raw = path.read_text(encoding='utf-8')
            at = raw.index('(font', raw.index('"AE"'))
            path.write_text(raw[:at] + '(font (face "DejaVu Sans") ' + raw[at+len('(font'):], encoding='utf-8')
        loaded = k.LoadBoard(str(path))
        for item in loaded.GetDrawings():
            if item.GetClass() == 'PCB_TEXT':
                saved[item.GetText()] = item.m_Uuid.AsString()
            elif item.GetLayer() in (k.F_SilkS, k.B_SilkS):
                saved[f'silk@{item.GetStart().x / MM:.3f}'] = item.m_Uuid.AsString()
        for fp in loaded.GetFootprints():
            saved[fp.GetReference()] = next(iter(fp.Pads())).m_Uuid.AsString()
            if hidden_small and fp.GetReference() == 'U1':
                assert not fp.Value().IsVisible() and fp.Value().GetLayer() == k.F_SilkS
                saved['HID'] = fp.Value().m_Uuid.AsString()
        if outline_font:
            assert next(i for i in loaded.GetDrawings() if i.GetClass() == 'PCB_TEXT').GetFont() is not None
    return build


class NativeSilkTest(unittest.TestCase):
    """Real sandboxed KiCad DRC; offenders are saved silk and pad UUIDs."""
    @classmethod
    def setUpClass(cls):
        from tests.toolchain import require_toolchain
        require_toolchain(native=True)

    def run_silk(self, limits=MEASURED, **options):
        saved = {}
        result = base.NativeFabTest.run_profile(self, None, builder=silk_board(saved, **options),
                                                profile=lambda inputs: silk_profile(inputs, limits))
        return result, {c['id']: c for c in result['details']['constraints']}, saved

    def cited(self, record):
        return sorted({i['uuid'] for f in record.get('native_findings', []) for i in f['items']})

    def test_compliant_board_passes_and_full_preset_stays_unknown_for_mask(self):
        result, records, _ = self.run_silk()
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertEqual({r['status'] for r in records.values()}, {'satisfied'})
        self.assertEqual(result['details']['extra_charges'], [])
        full, records, _ = self.run_silk(limits=None)
        self.assertEqual((full['execution'], full['outcome']), ('unsupported', 'unknown'), full)
        self.assertEqual((records['silk-to-mask']['status'], records['silk-to-mask']['code']),
                         ('not_evaluated', 'silk_to_mask_unmeasured'))
        self.assertEqual({records[i]['status'] for i in MEASURED}, {'satisfied'})
        # The pad-to-silk boundary is exclusive: exactly 0.15 mm from copper is legal.
        edge, records, _ = self.run_silk(gap=0.15)
        self.assertEqual((edge['outcome'], records['pad-to-silk']['status']), ('pass', 'satisfied'), edge)

    def test_undersized_silk_is_a_defect_citing_saved_items(self):
        result, records, saved = self.run_silk(gap=0.14, text_size=0.8, thickness=0.1, line=0.1)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'defect'), result)
        self.assertEqual({r['status'] for r in records.values()}, {'violation'})
        line = saved[f'silk@{10 - 0.5 - 0.14 - 0.05:.3f}']
        pad = records['pad-to-silk']
        self.assertEqual(self.cited(pad), sorted([line, saved['U1']]))
        self.assertEqual({f['type'] for f in pad['native_findings']}, {'silk_over_copper'})
        self.assertIn('actual 0.1400 mm', pad['native_findings'][0]['description'])
        self.assertEqual(self.cited(records['silk-text-height']), [saved['AE']])
        width = records['silk-line-width']
        self.assertEqual(self.cited(width), [saved['AE']])
        self.assertEqual(width['offender_ids'], [line])
        self.assertEqual(width['silk_graphic_violations'][0]['width_nm'], 100000)

    def test_pad_to_silk_measures_copper_not_the_mask_aperture(self):
        # The measured distance is the same at either mask margin, so it cannot
        # stand for silkscreen-to-solder-mask clearance.
        for margin in (0.05, 0.3):
            with self.subTest(margin=margin):
                result, records, _ = self.run_silk(limits=('pad-to-silk',), gap=0.14, margin=margin)
                self.assertEqual(result['outcome'], 'defect', result)
                self.assertIn('actual 0.1400 mm', records['pad-to-silk']['native_findings'][0]['description'])
                result, _, _ = self.run_silk(limits=('pad-to-silk',), gap=0.16, margin=margin)
                self.assertEqual(result['outcome'], 'pass', result)

    def test_back_side_is_graded_and_opposite_side_silk_is_not(self):
        result, records, saved = self.run_silk(limits=('pad-to-silk',), back_gap=0.1)
        self.assertEqual(result['outcome'], 'defect', result)
        back = saved[f'silk@{20 - 0.5 - 0.1 - 0.075:.3f}']
        self.assertIn(f'silk@{20 + 0.5 + 0.1 + 0.075:.3f}', saved)
        self.assertEqual(self.cited(records['pad-to-silk']), sorted([back, saved['U2']]))

    def test_tented_pad_hidden_text_and_outline_font_are_unknown(self):
        cases = ((dict(tented=True, gap=0.1), 'pad-to-silk', 'pad_to_silk_tented_pad'),
                 (dict(outline_font=True), 'silk-text-height', 'silk_text_unqualified'),
                 (dict(outline_font=True), 'silk-line-width', 'silk_text_unqualified'))
        for options, limit, code in cases:
            with self.subTest(limit=limit, code=code):
                result, records, _ = self.run_silk(limits=(limit,), **options)
                self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
                self.assertEqual((records[limit]['status'], records[limit]['code']), ('not_evaluated', code))
        # An outline font does not gate copper clearance; the glyph outline is measured.
        result, records, _ = self.run_silk(limits=('pad-to-silk',), outline_font=True)
        self.assertEqual(result['outcome'], 'pass', result)

    def test_inventory_records_saved_silk_classes(self):
        from agent_ee.fab_profile import native_inventory
        with tempfile.TemporaryDirectory() as tmp:
            saved, path = {}, Path(tmp) / 'b.kicad_pcb'
            silk_board(saved, hidden_small=True, outline_font=True, line=0.12, back_gap=0.2)(path)
            silk = native_inventory(path)['silk']
        texts = {t['uuid']: t for t in silk['texts']}
        self.assertEqual((texts[saved['HID']]['visible'], texts[saved['HID']]['class']), (False, 'PCB_FIELD'))
        self.assertEqual((texts[saved['AE']]['stroke_font'], texts[saved['AE']]['font_name']), (False, 'DejaVu Sans'))
        self.assertEqual(sorted((g['layers'], g['width_nm'], g['filled']) for g in silk['graphics']),
                         [(['B.SilkS'], 120000, False), (['F.SilkS'], 120000, False), (['F.SilkS'], 150000, False)])
        self.assertEqual(silk['other'], [])

    def test_hidden_footprint_field_is_inventoried_but_not_graded(self):
        # KiCad neither plots nor grades a hidden field; it never decides a silk limit.
        result, records, saved = self.run_silk(hidden_small=True)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertEqual({r['status'] for r in records.values()}, {'satisfied'})
        self.assertIn('HID', saved)


if __name__ == '__main__':
    unittest.main()
