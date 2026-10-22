"""Dated JLCPCB silkscreen (legend) limits through the existing source-bound native check.

Pad-to-silk and silk-to-mask stay distinct; recommendations are advisories only.
Silk minima grade only where the cited page's supplier process, base-material and
copper-layer scope covers a captured order selection, and the order matches a saved
board whose complete stackup classifies. Boards and order selections are owned
synthetic fixtures; only the cited supplier numbers and names come from the capture.
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


ORDER_SOURCE = 'Owned test order selection record'


def ordered(inputs, document, process='rigid PCB', material='FR-4', layers=2):
    """Owned captured order selection, one whole recorded line per selection, cited by the profile."""
    lines = {'process': process, 'base_material': material, 'copper_layers': layers}
    path = inputs / 'order-selection.txt'
    quotes = {k: f'order / owned test order / {k.replace("_", " ")}: {v}' for k, v in lines.items()}
    path.write_text('Owned test order / 2026-10-22\n' + '\n'.join(quotes.values()) + '\n', encoding='utf-8')
    document['order'] = [{'id': f'order-{k.replace("_", "-")}', 'selection': k, 'value': v,
                          'source': {'path': path.name, 'sha256': digest(path), 'source': ORDER_SOURCE,
                                     'as_of': document['as_of'], 'page': 1, 'quote': quotes[k]}}
                         for k, v in lines.items()]
    return document


def graded(limits=MEASURED, **order):
    """Profile factory: the shipped preset (subset) with an owned captured order selection."""
    return lambda inputs: ordered(inputs, silk_profile(inputs, limits), **order)


def promote(document, limit_id, advisory_id):
    """Cite an advisory's unchanged source and value as a hard minimum."""
    advice = next(a for a in document['advisories'] if a['id'] == advisory_id)
    limit = next(l for l in document['limits'] if l['id'] == limit_id)
    limit.update(value=advice['value'], source=copy.deepcopy(advice['source']))
    return document


def rescope(inputs, document, line, names):
    """Owned variant record: page 1 states another scope line; every citation is re-hashed."""
    path = inputs / 'jlcpcb-silkscreen.txt'
    old = next(a for a in document['applicability'] if a['id'] == 'capability-scope')
    path.write_text(path.read_text(encoding='utf-8').replace(old['source']['quote'], line), encoding='utf-8')
    sha = digest(path)
    for entry in document['limits'] + document['advisories'] + document['applicability']:
        entry['source']['sha256'] = sha
    old.update(base_materials=names)
    old['source']['quote'] = line
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
        # Each graded minimum carries its own page's verified supplier scope components; the
        # capability page states process and layer range, so the line width cites it too.
        self.assertEqual(records['silk-line-width']['expected']['source']['page'], 1)
        for limit in MEASURED:
            scope = records[limit]['applicability']
            self.assertEqual(scope['scope_ids'], ['capability-copper-layers', 'capability-process',
                                                  'capability-scope'])
            self.assertEqual((scope['base_materials'], scope['processes'], scope['copper_layers']),
                             (['FR-4'], ['rigid PCB'], [1, 32]))
            # No order selection was captured: the shipped preset alone grades no silk limit.
            self.assertIsNone(scope['order'])
        self.assertNotIn('applicability', mask)

    def test_guide_text_height_and_recommendations_are_sourced_advisories_only(self):
        compiled = self.compile()
        advisories = {a['id']: a for a in compiled['advisories']}
        self.assertEqual({a['execution'] for a in advisories.values()}, {'ready'})
        self.assertEqual({(a['expected']['key'], a['expected']['value'], a['expected']['role'])
                          for a in advisories.values()},
                         {('min_silk_text_height_mm', .8, 'guide_minimum'),
                          ('min_silk_line_width_mm', .15, 'guide_minimum'),
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

    def test_recommendation_cited_as_hard_minimum_is_unknown(self):
        # The source fact's role binds its use: an unchanged recommendation citation
        # and value moved into a limit never grades, at any value.
        for limit_id, advisory_id in (('silk-line-width', 'recommended-line-width'),
                                      ('silk-to-mask', 'recommended-silk-to-mask')):
            with self.subTest(limit=limit_id):
                record = self.by_id(self.compile(promote(copy.deepcopy(self.document), limit_id, advisory_id)))[limit_id]
                self.assertEqual((record['execution'], record['code']), ('unsupported', 'source_role_mismatch'))
                self.assertIn("'recommended'", record['reason'])
                self.assertNotIn('rules', record)
        recommended = {'page': 2, 'quote': 'legend / character height / recommended: 1.0 mm'}
        for source, expected in ((recommended, ('unsupported', 'source_role_mismatch')),
                                 ({'page': 2, 'quote': 'legend / character height / absolute minimum: 0.8 mm'},
                                  ('unsupported', 'source_value_mismatch')),
                                 ({'page': 1, 'quote': 'legend / pad to silkscreen / minimum: 0.15 mm'},
                                  ('unsupported', 'source_subject_mismatch')),
                                 ({'page': 1, 'quote': 'text height / minimum: 1.0 mm'},
                                  ('unsupported', 'source_fact_unparsed'))):
            document = copy.deepcopy(self.document)
            limit = next(l for l in document['limits'] if l['id'] == 'silk-text-height')
            limit['source'].update(source)
            with self.subTest(quote=source['quote']):
                record = self.by_id(self.compile(document))['silk-text-height']
                self.assertEqual((record['execution'], record.get('code')), expected)
        # A separately sourced hard minimum is not refused for sharing a recommendation's value
        # or for being a guide minimum: the same 1.0 mm from the capability minimum grades,
        # and the guide's 0.8 mm absolute minimum may be stated as a limit.
        self.assertEqual(self.by_id(self.compile())['silk-text-height']['execution'], 'ready')
        document = copy.deepcopy(self.document)
        limit = next(l for l in document['limits'] if l['id'] == 'silk-text-height')
        guide = next(a for a in document['advisories'] if a['id'] == 'guide-text-height')
        limit.update(value=.8, source=copy.deepcopy(guide['source']))
        record = self.by_id(self.compile(document))['silk-text-height']
        self.assertEqual(record['execution'], 'ready')
        self.assertIn('(constraint text_height (min 0.8mm))', record['rules'])

    def test_advisory_role_binds_its_source_fact(self):
        line = 'legend / line width / text thickness / absolute minimum: 0.15 mm'
        for advisory_id, update in (('recommended-line-width', {'value': .15, 'quote': line}),
                                    ('guide-text-height', {'value': 1.0,
                                                           'quote': 'legend / character height / recommended: 1.0 mm'})):
            document = copy.deepcopy(self.document)
            advisory = next(a for a in document['advisories'] if a['id'] == advisory_id)
            advisory['value'] = update['value']; advisory['source']['quote'] = update['quote']
            with self.subTest(advisory=advisory_id):
                record = next(a for a in self.compile(document)['advisories'] if a['id'] == advisory_id)
                self.assertEqual(record['execution'], 'unsupported')
                self.assertIn('cannot stand as', record['reason'])

    def test_applicability_scope_is_a_verified_whole_scope_line(self):
        applicability = {a['id']: a for a in self.compile()['applicability']}
        self.assertEqual({a['execution'] for a in applicability.values()}, {'ready'})
        self.assertEqual(applicability['capability-scope']['expected']['source']['page'], 1)
        for mutate, reason in ((lambda a: a.update(base_materials=['FR-4', 'Aluminum']), 'exactly base_materials'),
                               (lambda a: a['source'].update(quote='scope / rigid PCB capabilities'), 'whole recorded'),
                               (lambda a: a['source'].update(quote='legend / text height / minimum: 1.0 mm'),
                                'whole recorded'),
                               (lambda a: a['source'].update(sha256='0' * 64), 'hash mismatch')):
            document = copy.deepcopy(self.document)
            mutate(document['applicability'][0])
            with self.subTest(reason=reason):
                record = next(a for a in self.compile(document)['applicability'] if a['id'] == 'capability-scope')
                self.assertEqual(record['execution'], 'unsupported')
                self.assertIn(reason, record['reason'])
                # Unverified scope refuses the descriptor before any native run.
                (self.inputs / 'profile.json').write_text(json.dumps(document))
                files = {p.name: digest(p) for p in self.inputs.iterdir()}
                with self.assertRaisesRegex(ValueError, 'applicability source not verified'):
                    capability({'fab_profile': 'profile.json'}, {}, files, self.inputs, 'fab')
                # No base-material scope binds to the silk minima of that page.
                records = self.by_id(self.compile(document))
                for limit in MEASURED:
                    self.assertNotIn('base_materials', records[limit]['applicability'])
                    self.assertNotIn('capability-scope', records[limit]['applicability']['scope_ids'])
        for mutate, message in ((lambda d: d['applicability'].append(copy.deepcopy(d['applicability'][0])),
                                 'duplicate applicability'),
                                (lambda d: d['applicability'][0].update(base_materials=[]), 'base_materials'),
                                (lambda d: d['applicability'][0].update(process='rigid'), 'exactly'),
                                (lambda d: d['applicability'][0].update(processes=['rigid PCB']), 'exactly'),
                                (lambda d: d['applicability'][1].update(processes='rigid PCB'), 'processes'),
                                (lambda d: d['applicability'][2].update(copper_layers=[32, 1]), 'range'),
                                (lambda d: d['applicability'][2].update(copper_layers=[0, 32]), 'range'),
                                (lambda d: d['applicability'][2].update(copper_layers=[True, 32]), 'range'),
                                (lambda d: d['applicability'].append({**copy.deepcopy(d['applicability'][2]),
                                                                      'id': 'again'}), 'source page component'),
                                (lambda d: d.update(applicability=[]), 'nonempty')):
            document = copy.deepcopy(self.document)
            mutate(document)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.compile(document)
        # Process and copper-layer scope lines verify the same way, per component.
        for index, mutate in ((1, lambda a: a.update(processes=['flex PCB'])),
                              (1, lambda a: a['source'].update(quote='scope / rigid PCB capabilities / '
                                                                     'base material: FR-4')),
                              (2, lambda a: a.update(copper_layers=[1, 16])),
                              (2, lambda a: a['source'].update(quote='scope / rigid PCB capabilities / '
                                                                     'process: rigid PCB'))):
            document = copy.deepcopy(self.document)
            mutate(document['applicability'][index])
            entry = document['applicability'][index]
            with self.subTest(entry=entry['id'], quote=entry['source']['quote']):
                record = next(a for a in self.compile(document)['applicability'] if a['id'] == entry['id'])
                self.assertEqual(record['execution'], 'unsupported')
                self.assertIn('whole recorded scope line', record['reason'])
                kind = 'processes' if index == 1 else 'copper_layers'
                self.assertNotIn(kind, self.by_id(self.compile(document))['pad-to-silk']['applicability'])
        # Without any applicability block the silk minima carry no scope at all.
        document = copy.deepcopy(self.document)
        del document['applicability']
        self.assertFalse(any('applicability' in r for r in self.compile(document)['constraints']))

    def test_order_selection_is_verified_whole_order_lines(self):
        document = ordered(self.inputs, copy.deepcopy(self.document), layers=4)
        compiled = self.compile(document)
        self.assertEqual({o['execution'] for o in compiled['order']}, {'ready'})
        self.assertIn('order-selection.txt', compiled['immutable_inputs'])
        scope = self.by_id(compiled)['pad-to-silk']['applicability']
        self.assertEqual(scope['order'], {'process': 'rigid PCB', 'base_material': 'FR-4', 'copper_layers': 4,
                                          'ids': ['order-base-material', 'order-copper-layers', 'order-process']})
        # Each selection must be its own whole recorded line; any unverified selection drops
        # the order for every silk minimum and refuses the descriptor before a native run.
        for selection, mutate, reason in (
                ('process', lambda o: o.update(value='flex PCB'), 'whole recorded order line'),
                ('copper_layers', lambda o: o.update(value=2), 'whole recorded order line'),
                ('base_material', lambda o: o['source'].update(
                    quote='order / owned test order / process: rigid PCB'), 'whole recorded order line'),
                ('process', lambda o: o['source'].update(sha256='0' * 64), 'hash mismatch'),
                ('process', lambda o: o['source'].update(as_of='2026-10-21'), 'as_of')):
            bad = copy.deepcopy(document)
            mutate(next(o for o in bad['order'] if o['selection'] == selection))
            with self.subTest(selection=selection, reason=reason):
                compiled = self.compile(bad)
                record = next(o for o in compiled['order'] if o['selection'] == selection)
                self.assertEqual(record['execution'], 'unsupported')
                self.assertIn(reason, record['reason'])
                self.assertIsNone(self.by_id(compiled)['pad-to-silk']['applicability']['order'])
                (self.inputs / 'profile.json').write_text(json.dumps(bad))
                files = {p.name: digest(p) for p in self.inputs.iterdir()}
                with self.assertRaisesRegex(ValueError, 'order selection source not verified'):
                    capability({'fab_profile': 'profile.json'}, {}, files, self.inputs, 'fab')
        for mutate, message in ((lambda d: d['order'].pop(), 'exactly one'),
                                (lambda d: d['order'].append(copy.deepcopy(d['order'][0])), 'exactly one'),
                                (lambda d: d['order'][0].update(selection='finish'), 'exactly one'),
                                (lambda d: d['order'][0].update(extra=1), 'exactly unique'),
                                (lambda d: next(o for o in d['order'] if o['selection'] == 'copper_layers')
                                 .update(value='2'), 'layer count'),
                                (lambda d: next(o for o in d['order'] if o['selection'] == 'process')
                                 .update(value='rigid PCB, flex PCB'), 'one name'),
                                (lambda d: d.update(order=[]), 'exactly one')):
            bad = copy.deepcopy(document)
            mutate(bad)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.compile(bad)
        v1 = copy.deepcopy(document); v1['schema'] = 'agent-ee.fab-profile/1'
        with self.assertRaisesRegex(ValueError, 'no defaults'):
            self.compile(v1)

    def test_no_silk_to_silk_rule_and_v1_refuses_silk_keys(self):
        bad = copy.deepcopy(self.document)
        bad['limits'][0]['key'] = 'min_silk_to_silk_mm'
        with self.assertRaisesRegex(ValueError, 'unsupported fabricator limit key'):
            self.compile(bad)
        v1 = copy.deepcopy(self.document)
        v1['schema'] = 'agent-ee.fab-profile/1'
        with self.assertRaisesRegex(ValueError, 'no defaults'):
            self.compile(v1)
        del v1['advisories'], v1['applicability']
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
        self.assertEqual({s['source']['sha256'] for s in
                          document['limits'] + document['advisories'] + document['applicability']},
                         {capture['recorded_sources']['jlcpcb-silkscreen.txt']['sha256']})


def text(uid, visible=True, stroke=True, knockout=False, **extra):
    return {'uuid': uid, 'class': 'PCB_TEXT', 'layers': ['F.SilkS'], 'visible': visible,
            'stroke_font': stroke, 'font_name': '' if stroke else 'Owned Outline', 'knockout': knockout,
            'height_nm': 1000000, 'thickness_nm': 150000, **extra}


FR4 = [{'layer': 'dielectric 1', 'materials': ['FR4']}]
ORDER = {'process': 'rigid PCB', 'base_material': 'FR-4', 'copper_layers': 2,
         'ids': ['order-base-material', 'order-copper-layers', 'order-process']}


def graphic(uid, width, filled=False):
    return {'uuid': uid, 'class': 'PCB_SHAPE', 'layers': ['F.SilkS'], 'shape': 'Line',
            'width_nm': width, 'filled': filled}


class SilkQualificationTest(unittest.TestCase):
    """Applicability gates on native silk records, before any grade is reported."""

    def record(self, key, status='satisfied', findings=(), value=.15, scope=('FR-4',), order=ORDER, **parts):
        applicability = {'scope_ids': ['s'], 'base_materials': list(scope or ()), 'processes': ['rigid PCB'],
                         'copper_layers': [1, 32], 'order': order, **parts}
        return {'id': 'r', 'requirement': 'fab', 'execution': 'ok', 'status': status,
                'expected': {'key': key, 'value': value, 'tier': 'minimum'},
                'native_findings': list(findings),
                **({} if scope is None else {'applicability': applicability})}

    def qualify(self, record, dielectrics=FR4, copper=2, problems=(), **silk):
        inventory = {'silk': {'texts': [], 'graphics': [], 'other': [], **silk}, 'dielectrics': dielectrics,
                     'stackup_problems': list(problems), 'copper_layers': copper}
        return qualify_silk([record], inventory)[0]

    def test_applicability_gates_precede_every_grade(self):
        finding = [{'type': 'silk_over_copper', 'items': [{'uuid': 'a'}]}]
        polyimide = [{'layer': 'dielectric 1', 'materials': ['Polyimide']}]
        mixed = [*FR4, {'layer': 'dielectric 2', 'materials': ['FR4', 'Not specified']}]
        untyped = [{'layer': 'dielectric 2', 'type': None, 'materials': ['Polyimide'],
                    'reason': 'stackup layer type missing or unrecognized'}]
        cases = ((dict(scope=None), {}, 'silk_applicability_unsourced'),
                 (dict(processes=None), {}, 'silk_applicability_unsourced'),
                 (dict(copper_layers=None), {}, 'silk_applicability_unsourced'),
                 (dict(order=None), {}, 'silk_order_unsourced'),
                 ({}, dict(problems=untyped), 'silk_stackup_unverified'),
                 ({}, dict(dielectrics=None), 'silk_material_unverified'),
                 ({}, dict(dielectrics=[]), 'silk_material_unverified'),
                 ({}, dict(dielectrics=[{'layer': 'dielectric 1', 'materials': []}]), 'silk_material_unverified'),
                 ({}, dict(dielectrics=polyimide), 'silk_material_unverified'),
                 ({}, dict(dielectrics=mixed), 'silk_material_unverified'),
                 (dict(scope=('Aluminum',)), {}, 'silk_applicability_mismatch'),
                 (dict(order={**ORDER, 'process': 'flex PCB'}), {}, 'silk_applicability_mismatch'),
                 (dict(order={**ORDER, 'copper_layers': 34}, copper_layers=[1, 32]), dict(copper=34),
                  'silk_applicability_mismatch'),
                 (dict(order={**ORDER, 'base_material': 'Aluminum'}, scope=('FR-4', 'Aluminum')), {},
                  'silk_order_mismatch'),
                 (dict(order={**ORDER, 'copper_layers': 4}), {}, 'silk_order_mismatch'))
        for key in ('min_pad_to_silk_mm', 'min_silk_text_height_mm', 'min_silk_line_width_mm'):
            for options, inventory, code in cases:
                record = self.record(key, **options)
                # A None scope component is one the cited page never stated.
                for part in ('processes', 'copper_layers'):
                    if record.get('applicability', {}).get(part, ()) is None:
                        del record['applicability'][part]
                for status, findings in (('satisfied', []), ('violation', finding)):
                    record.update(status=status, native_findings=list(findings))
                    with self.subTest(key=key, code=code, options=options, inventory=inventory, status=status):
                        result = self.qualify(copy.deepcopy(record), **inventory)
                        # Unverified applicability is unknown, never pass and never a defect;
                        # the native measurement stays as diagnostic evidence.
                        self.assertEqual((result['execution'], result['status'], result['code'], result['native_status']),
                                         ('unsupported', 'not_evaluated', code, status))
                        self.assertEqual(result['native_findings'], findings)
        # Dielectrics without a stackup coverage inventory prove nothing either.
        result = qualify_silk([self.record('min_pad_to_silk_mm')],
                              {'silk': {'texts': [], 'graphics': [], 'other': []}, 'dielectrics': FR4,
                               'copper_layers': 2})[0]
        self.assertEqual((result['status'], result['code']), ('not_evaluated', 'silk_stackup_unverified'))
        # The offending saved stackup layer stays in the diagnostic evidence.
        result = self.qualify(self.record('min_pad_to_silk_mm'), problems=untyped)
        self.assertEqual(result['unqualified_silk'], untyped)
        # Applicability takes precedence over, and keeps, every other silk refusal.
        other = [{'uuid': 'd', 'class': 'PCB_DIM_ALIGNED', 'layers': ['F.SilkS']}]
        result = self.qualify(self.record('min_pad_to_silk_mm', scope=None), other=other)
        self.assertEqual([r['code'] for r in result['silk_refusals']],
                         ['silk_applicability_unsourced', 'silk_order_unsourced', 'silk_item_unqualified'])
        # A scope naming the qualified material and process, a matching order, and the
        # qualified material on every saved dielectric grades as before.
        two = [*FR4, {'layer': 'dielectric 2', 'materials': ['FR4', 'FR4']}]
        result = self.qualify(self.record('min_pad_to_silk_mm', scope=('Aluminum', 'FR-4')), dielectrics=two)
        self.assertEqual((result['execution'], result['status']), ('ok', 'satisfied'))
        result = self.qualify(self.record('min_pad_to_silk_mm', 'violation', finding))
        self.assertEqual((result['execution'], result['status']), ('ok', 'violation'))

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


def stackup(material):
    """Owned two-layer saved KiCad stackup; one core naming `material` (none when None)."""
    named = '' if material is None else f' (material "{material}")'
    return ('(stackup (layer "F.SilkS" (type "Top Silk Screen")) (layer "F.Mask" (type "Top Solder Mask") '
            '(thickness 0.01)) (layer "F.Cu" (type "copper") (thickness 0.035)) (layer "dielectric 1" '
            f'(type "core") (thickness 1.51){named}) (layer "B.Cu" (type "copper") (thickness 0.035)) '
            '(layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01)) (layer "B.SilkS" '
            '(type "Bottom Silk Screen")) (copper_finish "None") (dielectric_constraints no)) ')


def stackup4(*dielectrics):
    """Owned four-copper-layer saved stackup; each dielectric is its saved body, None omits it."""
    copper = ('F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu')
    layers = [f'(layer "{copper[0]}" (type "copper") (thickness 0.035))']
    for index, (body, name) in enumerate(zip(dielectrics, copper[1:]), 1):
        if body is not None:
            layers.append(f'(layer "dielectric {index}" {body})')
        layers.append(f'(layer "{name}" (type "copper") (thickness 0.035))')
    return '(stackup ' + ' '.join(layers) + ' (copper_finish "None") (dielectric_constraints no)) '


FR4_CORE = '(type "core") (thickness 0.5) (material "FR4")'
FR4_PREPREG = '(type "prepreg") (thickness 0.2) (material "FR4")'


def silk_board(saved, *, gap=0.2, margin=0.05, tented=False, back_gap=None, text_size=1.0, thickness=0.15,
               line=0.15, hidden_small=False, outline_font=False, material='FR4', stacked=True, copper=2,
               stack=None):
    """Owned board: one pad with a board silk line `gap` mm from its copper, plus text.

    The saved stackup names one core `material`, or is the saved `stack` text for a
    `copper`-layer board; stacked=False saves no stackup.
    `saved` receives role -> saved UUID, read back from the saved board.
    """
    def build(path, **_):
        import pcbnew as k
        def v(x, y):
            return k.VECTOR2I(round(x*MM), round(y*MM))
        board = k.BOARD(); board.SetCopperLayerCount(copper)
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
        if stacked:
            raw = path.read_text(encoding='utf-8')
            at = raw.index('(setup') + len('(setup')
            path.write_text(raw[:at] + ' ' + (stack or stackup(material)) + raw[at:], encoding='utf-8')
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
        assert loaded.GetDesignSettings().m_HasStackup == stacked
        assert loaded.GetCopperLayerCount() == copper
    return build


class NativeSilkTest(unittest.TestCase):
    """Real sandboxed KiCad DRC; offenders are saved silk and pad UUIDs."""
    @classmethod
    def setUpClass(cls):
        from tests.toolchain import require_toolchain
        require_toolchain(native=True)

    def run_silk(self, limits=MEASURED, profile=None, **options):
        saved = {}
        result = base.NativeFabTest.run_profile(self, None, builder=silk_board(saved, **options),
                                                profile=profile or graded(limits))
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

    def test_recommendation_promoted_to_hard_minimum_never_grades(self):
        # Review H1: the 0.2 mm stroke recommendation, cited unchanged as the line-width
        # minimum, on a compliant 0.15 mm board, is unknown instead of a defect.
        for limit, advisory in (('silk-line-width', 'recommended-line-width'),
                                ('silk-to-mask', 'recommended-silk-to-mask')):
            with self.subTest(limit=limit):
                result, records, _ = self.run_silk(
                    limits=(limit,), profile=lambda inputs: promote(silk_profile(inputs, (limit,)), limit, advisory))
                self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
                self.assertEqual((records[limit]['status'], records[limit]['code']),
                                 ('not_evaluated', 'source_role_mismatch'))
                self.assertNotIn('native_findings', records[limit])

    def refused(self, cases):
        """Each case is unknown at a compliant and a violating gap, keeping native_status."""
        for options, refused, code in cases:
            for gap, native in ((0.2, 'satisfied'), (0.14, 'violation')):
                with self.subTest(code=code, options=sorted(options), gap=gap):
                    result, records, _ = self.run_silk(gap=gap, **options)
                    self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
                    for limit in refused:
                        self.assertEqual((records[limit]['status'], records[limit]['code']),
                                         ('not_evaluated', code))
                    self.assertEqual(records['pad-to-silk']['native_status'], native)
                    self.assertEqual(bool(records['pad-to-silk']['native_findings']), native == 'violation')

    def test_unverified_material_is_unknown_with_native_diagnostics(self):
        # The measured-only profile without a supplier scope, a board without a saved
        # stackup material, an unqualified material and a scope that excludes the board's
        # material are all unknown; the native measurement stays as native_status.
        def unscoped(inputs):
            document = graded()(inputs)
            del document['applicability']
            return document
        def aluminum(inputs):
            return ordered(inputs, rescope(inputs, silk_profile(inputs, MEASURED),
                                           'scope / rigid PCB capabilities / base material: Aluminum', ['Aluminum']),
                           material='Aluminum')
        self.refused(((dict(profile=unscoped), MEASURED, 'silk_applicability_unsourced'),
                      (dict(stacked=False), MEASURED, 'silk_material_unverified'),
                      (dict(material=None), MEASURED, 'silk_material_unverified'),
                      (dict(material='Polyimide'), MEASURED, 'silk_material_unverified'),
                      (dict(profile=aluminum), MEASURED, 'silk_applicability_mismatch')))

    def test_process_and_order_applicability_gate_fr4_boards(self):
        # Review H1 (ee75d): an owned FR4 stackup and the measured-only shipped preset, with
        # no captured order selection, or with one the cited page's process or layer scope
        # does not cover, or that differs from the board, is unknown at either gap.
        def guide_width(inputs):
            document = graded(('silk-line-width',))(inputs)
            document['limits'][0]['source'] = copy.deepcopy(next(a for a in document['advisories']
                                                                 if a['id'] == 'guide-line-width')['source'])
            return document
        self.refused(((dict(profile=lambda inputs: silk_profile(inputs, MEASURED)), MEASURED, 'silk_order_unsourced'),
                      (dict(profile=graded(process='flex PCB')), MEASURED, 'silk_applicability_mismatch'),
                      (dict(profile=graded(material='Aluminum')), MEASURED, 'silk_applicability_mismatch'),
                      (dict(profile=graded(layers=4)), MEASURED, 'silk_order_mismatch')))
        result, records, _ = self.run_silk(profile=lambda inputs: silk_profile(inputs, MEASURED))
        self.assertEqual(records['pad-to-silk']['silk_refusals'][0]['reason'],
                         'no verified captured order selection of process, base material and copper layers')
        # The guide table states neither a process nor a layer range: its line-width
        # minimum is unknown under a matching order, compliant or not.
        for line, native in ((0.15, 'satisfied'), (0.1, 'violation')):
            result, records, _ = self.run_silk(profile=guide_width, line=line, thickness=line)
            self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'), result)
            width = records['silk-line-width']
            self.assertEqual((width['code'], width['native_status'], width['unqualified_silk']),
                             ('silk_applicability_unsourced', native, ['processes', 'copper_layers']))
        # The same boards grade once the captured order selection is in scope and matches.
        for gap, outcome in ((0.2, 'pass'), (0.14, 'defect')):
            result, records, _ = self.run_silk(gap=gap)
            self.assertEqual((result['execution'], result['outcome']), ('ok', outcome), result)
            self.assertEqual(records['pad-to-silk']['applicability']['order']['process'], 'rigid PCB')

    def test_partial_multilayer_stackup_is_unknown(self):
        # Review H2 (ee75d): a four-copper-layer board with one valid FR4 core whose other
        # dielectrics are untyped, of an unrecognized type or omitted is unknown, never pass;
        # the offending saved layer and its material stay in the diagnostic evidence.
        untyped = '(thickness 0.5) (material "Polyimide")'
        unknown = '(type "unspecified") (thickness 0.5) (material "Polyimide")'
        # An unclassified layer also leaves its copper pair without a core/prepreg (None row).
        gap = (None, None, [])
        cases = (('untyped', (FR4_CORE, untyped, FR4_CORE), [('dielectric 2', None, ['Polyimide']), gap]),
                 ('unrecognized', (FR4_CORE, FR4_PREPREG, unknown),
                  [('dielectric 3', 'unspecified', ['Polyimide']), gap]),
                 ('both', (FR4_CORE, untyped, unknown), [('dielectric 2', None, ['Polyimide']),
                                                         ('dielectric 3', 'unspecified', ['Polyimide']), gap, gap]),
                 ('omitted', (FR4_CORE, None, FR4_CORE), [gap]))
        for name, dielectrics, offenders in cases:
            options = dict(copper=4, stack=stackup4(*dielectrics), profile=graded(layers=4))
            self.refused(((options, MEASURED, 'silk_stackup_unverified'),))
            with self.subTest(case=name):
                result, records, _ = self.run_silk(**options)
                refusal = records['pad-to-silk']['silk_refusals'][0]
                self.assertEqual(refusal['code'], 'silk_stackup_unverified')
                self.assertEqual([(i['layer'], i['type'], i['materials']) for i in refusal['items']], offenders)
        # A complete four-layer FR4 stackup grades under a matching four-layer order.
        for gap, outcome in ((0.2, 'pass'), (0.14, 'defect')):
            result, _, _ = self.run_silk(gap=gap, copper=4, stack=stackup4(FR4_CORE, FR4_PREPREG, FR4_CORE),
                                         profile=graded(layers=4))
            self.assertEqual((result['execution'], result['outcome']), ('ok', outcome), result)

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

    def test_inventory_reads_saved_stackup_dielectrics(self):
        from agent_ee.fab_profile import native_inventory
        polyimide = '(type "unspecified") (thickness 0.5) (material "Polyimide")'
        for options, expected, problems in (
                (dict(), [{'layer': 'dielectric 1', 'materials': ['FR4']}], []),
                (dict(material=None), [{'layer': 'dielectric 1', 'materials': []}], []),
                (dict(stacked=False), None, []),
                (dict(copper=4, stack=stackup4(FR4_CORE, '(thickness 0.5) (material "Polyimide")', polyimide)),
                 [{'layer': 'dielectric 1', 'materials': ['FR4']}],
                 [{'layer': 'dielectric 2', 'type': None, 'materials': ['Polyimide'],
                   'reason': 'stackup layer type missing or unrecognized'},
                  {'layer': 'dielectric 3', 'type': 'unspecified', 'materials': ['Polyimide'],
                   'reason': 'stackup layer type missing or unrecognized'},
                  {'layer': None, 'type': None, 'materials': [], 'between': ['In1.Cu', 'In2.Cu'],
                   'reason': 'no saved core/prepreg dielectric between adjacent copper layers'},
                  {'layer': None, 'type': None, 'materials': [], 'between': ['In2.Cu', 'B.Cu'],
                   'reason': 'no saved core/prepreg dielectric between adjacent copper layers'}]),
                # The two-layer stackup on a board saved with four copper layers.
                (dict(copper=4), [{'layer': 'dielectric 1', 'materials': ['FR4']}],
                 [{'layer': None, 'type': 'copper', 'materials': [], 'saved': ['F.Cu', 'B.Cu'],
                   'board': None, 'reason': 'saved stackup copper layers differ from the board copper layers'}])):
            with self.subTest(options=sorted(options)), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'b.kicad_pcb'
                silk_board({}, **options)(path)
                inventory = native_inventory(path)
                self.assertEqual(inventory['dielectrics'], expected)
                if problems and problems[0].get('board', ()) is None:
                    problems[0]['board'] = inventory['copper_layer_names']
                    self.assertEqual(sorted(inventory['copper_layer_names']),
                                     ['B.Cu', 'F.Cu', 'In1.Cu', 'In2.Cu'])
                self.assertEqual(inventory['stackup_problems'], problems)

    def test_hidden_footprint_field_is_inventoried_but_not_graded(self):
        # KiCad neither plots nor grades a hidden field; it never decides a silk limit.
        result, records, saved = self.run_silk(hidden_small=True)
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result)
        self.assertEqual({r['status'] for r in records.values()}, {'satisfied'})
        self.assertIn('HID', saved)


if __name__ == '__main__':
    unittest.main()
