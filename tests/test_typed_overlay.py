"""Typed overlay preparation: immutable base, derived map/2 + obligations, no judgments invented."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_ee import typed_overlay
from agent_ee.requirement_map import Refused, SCHEMA_2, coverage_view, load_obligations
from agent_ee.spec_record import sha256, verify
from agent_ee.source_authority import verified_reference
from agent_ee.storage import json_bytes, read_json

ROOT = Path(__file__).resolve().parents[1]
POD = ROOT / 'fixtures/telemetry-pod'
ACQUISITION_OVERLAY = ROOT / 'fixtures/acquisition-typed/overlay.json'
RETAINED = Path(os.environ.get('AGENT_EE_ACQUISITION_INPUTS', ROOT / 'build/acquisition-release/inputs'))
CPL_SOURCE = Path(os.environ.get('AGENT_EE_ACQUISITION_CPL_INPUTS', ROOT / 'build/acquisition-release/cpl-inputs'))
TEXT_SOURCE = Path(os.environ.get('AGENT_EE_ACQUISITION_TEXT_INPUTS', ROOT / 'build/acquisition-release/manufacturer-text'))
# The committed overlay pins agent-566's cpl-inputs-rev3 (24 rev2 files + polarity/C83152.polarity.json).


class PodOverlayTests(unittest.TestCase):
    """Owned minimal example on the committed pod fixture (map/1, spec, record)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name) / 'base'
        self.base.mkdir()
        for name in typed_overlay.BASE_FILES:
            (self.base / name).write_bytes((POD / name).read_bytes())
        self.spec = (POD / 'spec.md').read_bytes()
        self.record = verify((POD / 'spec-record.json').read_bytes(), self.spec)
        self.map = read_json(POD / 'requirement-map.json')
        self.overlay = self.minimal_overlay()

    def tearDown(self):
        self.tmp.cleanup()

    def clause(self, rid):
        return next(r for r in self.record['requirements'] + self.record['prose'] if r['id'] == rid)

    def minimal_overlay(self):
        """Every clause keeps its map/1 disposition; POD-AC-3 becomes a two-span mixed clause."""
        obligations, mappings = {}, {}
        for source in self.record['requirements'] + self.record['prose']:
            entry = next(e for e in self.map['requirements'] + self.map['prose'] if e['id'] == source['id'])
            kind = ('physical' if 'bench' in entry else 'context' if 'context' in entry else 'executable')
            start, end = source['locator']['byte_start'], source['locator']['byte_end']
            obligations[source['id']] = [dict(byte_start=start, byte_end=end, obligation=kind)]
            mappings[source['id']] = {'keep': True}
        ac3 = self.clause('POD-AC-3')
        start, end = ac3['locator']['byte_start'], ac3['locator']['byte_end']
        split = self.spec.index(b'. ', start, end) + 1
        checks = [c['id'] for c in next(e for e in self.map['requirements'] if e['id'] == 'POD-AC-3')['checks']]
        obligations['POD-AC-3'] = [dict(byte_start=start, byte_end=split, obligation='executable'),
                                   dict(byte_start=split + 1, byte_end=end, obligation='judgment')]
        mappings['POD-AC-3'] = {'spans': [
            dict(byte_start=start, byte_end=split, id='POD-AC-3-NATIVE', mode='checks', checks=checks),
            dict(byte_start=split + 1, byte_end=end, id='POD-AC-3-WAIVERS', mode='judgment',
                 record='judgments/pod-ac-3-waivers.json')]}
        return dict(schema=typed_overlay.SCHEMA, name='pod-minimal',
                    base=dict(spec_sha256=sha256(self.spec),
                              spec_record_sha256=sha256((POD / 'spec-record.json').read_bytes()),
                              requirement_map_sha256=sha256((POD / 'requirement-map.json').read_bytes())),
                    obligations=obligations, mappings=mappings,
                    judgments={'judgments/pod-ac-3-waivers.json': dict(
                        requirement='POD-AC-3', span=[split + 1, end],
                        questions=['Which waivers are dispositioned and by what source?'],
                        sources=['fixtures/telemetry-pod/spec.md'])},
                    derived_files={}, added_inputs={}, check_additions={'declare': [], 'params': {}})

    def prepare(self, overlay=None, name='out', **kwargs):
        path = Path(self.tmp.name) / 'overlay.json'
        path.write_bytes(json_bytes(self.overlay if overlay is None else overlay))
        return typed_overlay.prepare(self.base, path, Path(self.tmp.name) / name, **kwargs)

    def test_added_inputs_and_check_additions_extend_the_derived_map_only(self):
        source = Path(self.tmp.name) / 'cpl-source'
        (source / 'supplier').mkdir(parents=True)
        (source / 'policy.json').write_bytes(b'{"policy": 1}\n')
        (source / 'supplier/C1.json').write_bytes(b'{"lcsc": "C1"}\n')
        overlay = copy.deepcopy(self.overlay)
        overlay['added_inputs'] = {'cpl': {'files': {'policy.json': sha256(b'{"policy": 1}\n'),
                                                     'supplier/C1.json': sha256(b'{"lcsc": "C1"}\n')}}}
        existing = overlay['mappings']['POD-AC-3']['spans'][0]['checks'][0]
        overlay['check_additions'] = {
            'declare': [{'id': 'extra-erc', 'kind': 'erc', 'params': {}, 'stage': 'verification'}],
            'params': {existing: {'added_param': 'cpl/policy.json'}}}
        overlay['mappings']['POD-AC-3']['spans'][0]['checks'].append('extra-erc')
        result = self.prepare(overlay, name='added', added_sources={'cpl': source})
        out = Path(result['out'])
        self.assertEqual((out / 'cpl/policy.json').read_bytes(), b'{"policy": 1}\n')
        self.assertEqual(result['manifest']['derived']['added_files'],
                         ['obligations.json', 'cpl/policy.json', 'cpl/supplier/C1.json'])
        derived = read_json(out / 'requirement-map.json')
        self.assertIn({'path': 'cpl', 'at': 'cpl'}, derived['scope']['inputs'])
        ac3 = next(e for e in derived['requirements'] if e['id'] == 'POD-AC-3')
        checks = {c['id']: c for c in ac3['spans'][0]['mapping']['checks']}
        self.assertEqual(checks['extra-erc']['kind'], 'erc')
        self.assertEqual(checks[existing]['params']['added_param'], 'cpl/policy.json')
        self.assertNotIn('added_param', self.map['requirements'][0].get('checks', [{}])[0].get('params', {}))
        self.assertEqual((self.base / 'requirement-map.json').read_bytes(), (POD / 'requirement-map.json').read_bytes())
        # Hash drift, a missing source, an undeclared source and a redeclared id all refuse without output.
        bad = copy.deepcopy(overlay)
        bad['added_inputs']['cpl']['files']['policy.json'] = sha256(b'other')
        with self.assertRaises(ValueError):
            self.prepare(bad, name='drift', added_sources={'cpl': source})
        with self.assertRaises(ValueError):
            self.prepare(overlay, name='nosource')
        with self.assertRaises(ValueError):
            self.prepare(overlay, name='extrasource', added_sources={'cpl': source, 'other': source})
        bad = copy.deepcopy(overlay)
        bad['check_additions']['declare'][0]['id'] = existing
        with self.assertRaises(ValueError):
            self.prepare(bad, name='redeclare', added_sources={'cpl': source})
        bad = copy.deepcopy(overlay)
        bad['check_additions']['declare'][0]['params'] = {'declared_param': 1}
        bad['check_additions']['params']['extra-erc'] = {'declared_param': 2}
        with self.assertRaises(ValueError):
            self.prepare(bad, name='overwrite', added_sources={'cpl': source})
        for name in ('drift', 'nosource', 'extrasource', 'redeclare', 'overwrite'):
            self.assertFalse((Path(self.tmp.name) / name).exists())

    def test_prepare_derives_map2_and_obligations_and_leaves_the_base_untouched(self):
        before = {n: sha256((self.base / n).read_bytes()) for n in typed_overlay.BASE_FILES}
        result = self.prepare()
        out = Path(result['out'])
        self.assertEqual({n: sha256((self.base / n).read_bytes()) for n in typed_overlay.BASE_FILES}, before)
        derived = read_json(out / 'requirement-map.json')
        self.assertEqual(derived['schema'], SCHEMA_2)
        self.assertEqual((out / 'spec.md').read_bytes(), self.spec)
        obligations = load_obligations((out / 'obligations.json').read_bytes(), self.record, self.spec)
        self.assertEqual([s['obligation'] for s in obligations['POD-AC-3']], ['executable', 'judgment'])
        for span in obligations['POD-AC-3']:
            self.assertEqual(span['text_sha256'], sha256(self.spec[span['byte_start']:span['byte_end']]))
        manifest = result['manifest']
        self.assertEqual(manifest['derived']['added_files'], ['obligations.json'])
        self.assertEqual(list(manifest['derived']['changed_files']), ['requirement-map.json'])
        self.assertEqual(manifest['accounting']['judgment_records_declared'], ['judgments/pod-ac-3-waivers.json'])
        self.assertEqual(manifest['accounting']['judgment_records_present'], [])
        self.assertFalse((out / 'judgments').exists())
        self.assertTrue(any(s.startswith('POD-AC-3-WAIVERS: JUDGMENT PENDING') for s in manifest['accounting']['limitations']))
        ac3 = next(e for e in derived['requirements'] if e['id'] == 'POD-AC-3')
        self.assertEqual([s['mapping']['id'] for s in ac3['spans']], ['POD-AC-3-NATIVE', 'POD-AC-3-WAIVERS'])
        self.assertIn('checks', ac3['spans'][0]['mapping'])
        with self.assertRaises(ValueError):
            self.prepare()  # existing output directory is never overwritten

    def test_overlay_negatives(self):
        def base_hash(o):
            o['base']['spec_sha256'] = sha256(b'other')

        def unknown_check(o):
            o['mappings']['POD-AC-3']['spans'][0]['checks'] = ['no-such-check']

        def missing_obligation(o):
            del o['obligations']['POD-AC-3']

        def unknown_clause(o):
            o['obligations']['POD-NOPE'] = o['obligations']['POD-AC-3']

        def brief_without_record(o):
            o['judgments']['judgments/extra.json'] = dict(requirement='POD-AC-3', span=[0, 1],
                                                          questions=['q'], sources=['s'])

        def record_without_brief(o):
            o['judgments'] = {}

        def wrong_kind_for_mode(o):
            o['obligations']['POD-AC-3'][1]['obligation'] = 'executable'

        def unsupported_criterion(o):
            span = o['mappings']['POD-AC-3']['spans'][1]
            span.update(mode='operational', criterion='the build resumed once'); span.pop('record')
            o['obligations']['POD-AC-3'][1]['obligation'] = 'operational'
            o['judgments'] = {}

        for mutate, exc in ((base_hash, ValueError), (unknown_check, ValueError), (missing_obligation, ValueError),
                            (unknown_clause, ValueError), (brief_without_record, ValueError),
                            (record_without_brief, ValueError), (wrong_kind_for_mode, Refused),
                            (unsupported_criterion, Refused)):
            with self.subTest(case=mutate.__name__):
                overlay = copy.deepcopy(self.overlay)
                mutate(overlay)
                with self.assertRaises(exc):
                    self.prepare(overlay, name='out-' + mutate.__name__)
                self.assertFalse((Path(self.tmp.name) / ('out-' + mutate.__name__)).exists())


class PreservationTests(PodOverlayTests):
    """Scratch and output handling never deletes or reads through anything this invocation did not create."""

    def added(self):
        source = Path(self.tmp.name) / 'cpl-source'
        (source / 'supplier').mkdir(parents=True, exist_ok=True)
        (source / 'policy.json').write_bytes(b'{"policy": 1}\n')
        overlay = copy.deepcopy(self.overlay)
        overlay['added_inputs'] = {'cpl': {'files': {'policy.json': sha256(b'{"policy": 1}\n')}}}
        return source, overlay

    def test_preexisting_scratch_paths_survive_a_refused_and_a_successful_run(self):
        out = Path(self.tmp.name) / 'out'
        for sentinel in (out.with_name('out.staging'), out.with_name('out.compile')):
            sentinel.mkdir()
            (sentinel / 'valuable.txt').write_bytes(b'keep me')
        source, overlay = self.added()
        with self.assertRaises(ValueError):  # added source missing: refused after staging began
            self.prepare(overlay, name='out')
        self.assertFalse(out.exists())
        for sentinel in (out.with_name('out.staging'), out.with_name('out.compile')):
            self.assertEqual((sentinel / 'valuable.txt').read_bytes(), b'keep me')
        leftovers = [p.name for p in Path(self.tmp.name).iterdir() if p.name.startswith('out.staging-')]
        self.assertEqual(leftovers, [], 'the invocation must remove only its own scratch directory')
        result = self.prepare(overlay, name='out', added_sources={'cpl': source})
        self.assertTrue(out.is_dir())
        for sentinel in (out.with_name('out.staging'), out.with_name('out.compile')):
            self.assertEqual((sentinel / 'valuable.txt').read_bytes(), b'keep me')
        self.assertEqual(result['manifest']['derived']['added_files'], ['obligations.json', 'cpl/policy.json'])

    def test_failing_added_source_validation_leaves_no_output_and_touches_no_input(self):
        source, overlay = self.added()
        before = {n: sha256((self.base / n).read_bytes()) for n in typed_overlay.BASE_FILES}
        overlay['added_inputs']['cpl']['files']['policy.json'] = sha256(b'other')
        with self.assertRaises(ValueError):
            self.prepare(overlay, name='drift', added_sources={'cpl': source})
        self.assertFalse((Path(self.tmp.name) / 'drift').exists())
        self.assertEqual({n: sha256((self.base / n).read_bytes()) for n in typed_overlay.BASE_FILES}, before)
        self.assertEqual((source / 'policy.json').read_bytes(), b'{"policy": 1}\n')
        self.assertEqual([p.name for p in Path(self.tmp.name).iterdir() if 'staging-' in p.name], [])

    def test_nested_or_aliased_paths_refuse_before_any_copy(self):
        source, overlay = self.added()
        path = Path(self.tmp.name) / 'overlay.json'
        path.write_bytes(json_bytes(overlay))
        cases = {'out inside base': (self.base / 'derived', {'cpl': source}),
                 'out equals base': (self.base, {'cpl': source}),
                 'base inside out': (self.base.parent, {'cpl': source}),
                 'out inside added source': (source / 'derived', {'cpl': source}),
                 'added source inside out': (Path(self.tmp.name) / 'out2', {'cpl': Path(self.tmp.name) / 'out2' / 'cpl'})}
        for label, (out, sources) in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    typed_overlay.prepare(self.base, path, out, added_sources=sources)
        self.assertEqual({n: sha256((self.base / n).read_bytes()) for n in typed_overlay.BASE_FILES},
                         {n: sha256((POD / n).read_bytes()) for n in typed_overlay.BASE_FILES})
        self.assertFalse((self.base / 'derived').exists())
        self.assertFalse((Path(self.tmp.name) / 'out2').exists())
        self.assertEqual([p.name for p in self.base.iterdir() if 'staging-' in p.name], [])

    def test_directory_and_file_links_are_refused_before_recursive_copy(self):
        source, overlay = self.added()
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        (outside / 'secret.txt').write_bytes(b'not an input')
        link = self.base / 'linked'
        link.symlink_to(outside, target_is_directory=True)
        try:
            with self.assertRaises(ValueError):
                self.prepare(overlay, name='linkbase', added_sources={'cpl': source})
            self.assertFalse((Path(self.tmp.name) / 'linkbase').exists())
        finally:
            link.unlink()
        (source / 'linkdir').symlink_to(outside, target_is_directory=True)
        overlay['added_inputs']['cpl']['files']['linkdir/secret.txt'] = sha256(b'not an input')
        with self.assertRaises(ValueError):
            self.prepare(overlay, name='linksource', added_sources={'cpl': source})
        self.assertFalse((Path(self.tmp.name) / 'linksource').exists())
        (source / 'file-link.json').symlink_to(outside / 'secret.txt')
        overlay['added_inputs']['cpl']['files'] = {'file-link.json': sha256(b'not an input')}
        with self.assertRaises(ValueError):
            self.prepare(overlay, name='linkfile', added_sources={'cpl': source})
        self.assertEqual((outside / 'secret.txt').read_bytes(), b'not an input')

    def test_failed_compile_removes_only_its_own_copy_and_keeps_the_derived_output(self):
        source, overlay = self.added()
        out = Path(self.tmp.name) / 'out'
        sentinel = out.with_name('out.compile')
        sentinel.mkdir()
        (sentinel / 'valuable.txt').write_bytes(b'keep me')
        # A candidate without the native design files cannot compile a scope; the failure
        # must leave the derived inputs, the sentinel and the candidate untouched and no
        # owned compile copy behind.
        candidate = Path(self.tmp.name) / 'candidate'
        candidate.mkdir()
        (candidate / 'unrelated.txt').write_bytes(b'candidate file')
        with self.assertRaises(ValueError):
            self.prepare(overlay, name='out', added_sources={'cpl': source}, compile_against=candidate)
        self.assertTrue((out / 'overlay-manifest.json').is_file())
        self.assertEqual((sentinel / 'valuable.txt').read_bytes(), b'keep me')
        self.assertEqual(sorted(p.name for p in candidate.iterdir()), ['unrelated.txt'])
        self.assertEqual([p.name for p in Path(self.tmp.name).iterdir() if p.name.startswith('out.compile-')], [])
        with self.assertRaises(ValueError):  # a candidate nested in the derived output is refused up front
            typed_overlay.compile_typed(out, out / 'inner')

    def test_successful_runs_are_deterministic(self):
        source, overlay = self.added()
        first = self.prepare(overlay, name='a', added_sources={'cpl': source})['manifest']
        second = self.prepare(overlay, name='b', added_sources={'cpl': source})['manifest']
        for key in ('overlay_sha256', 'base', 'accounting'):
            self.assertEqual(first[key], second[key])
        self.assertEqual({k: v for k, v in first['derived'].items() if k != 'added_inputs'},
                         {k: v for k, v in second['derived'].items() if k != 'added_inputs'})


@unittest.skipUnless((RETAINED / 'spec.md').exists() and (CPL_SOURCE / 'assembly-profile.json').exists()
                     and (TEXT_SOURCE / 'manifest.json').exists(),
                     'retained acquisition inputs or additive CPL/text captures are not present on this host')
class AcquisitionOverlayTests(unittest.TestCase):
    """The committed acquisition overlay against the retained, git-ignored captured inputs."""

    def test_committed_overlay_prepares_and_accounts_every_clause(self):
        overlay = read_json(ACQUISITION_OVERLAY)
        for name, key in (('spec.md', 'spec_sha256'), ('spec-record.json', 'spec_record_sha256'),
                          ('requirement-map.json', 'requirement_map_sha256')):
            self.assertEqual(sha256((RETAINED / name).read_bytes()), overlay['base'][key], name)
        with tempfile.TemporaryDirectory() as tmp:
            result = typed_overlay.prepare(RETAINED, ACQUISITION_OVERLAY, Path(tmp) / 'derived',
                                           added_sources={'cpl': CPL_SOURCE, 'manufacturer-text': TEXT_SOURCE,
                                               'operating-supplement': ROOT / 'fixtures/acquisition-typed/operating-supplement'})
            accounting = result['manifest']['accounting']
            self.assertEqual(accounting['obligations']['operational'], 1)
            self.assertEqual(accounting['obligations']['physical'], 2)
            self.assertEqual(accounting['obligations']['context'], 1)
            self.assertEqual(accounting['obligations']['judgment'], 14)
            self.assertEqual(len(accounting['judgment_records_declared']), 14)
            self.assertIn('judgments/acq-mech-3-return-paths.json', accounting['judgment_records_declared'])
            self.assertEqual(accounting['judgment_records_present'], [])
            derived = Path(tmp) / 'derived'
            self.assertFalse((derived / 'judgments').exists())
            self.assertEqual(set(result['manifest']['derived']['changed_files']),
                             {'requirement-map.json', 'analysis/electrical-intervals.json'})
            self.assertEqual(result['manifest']['derived']['added_files'],
                             ['obligations.json'] + sorted(at + '/' + n for at, declaration in
                                 overlay['added_inputs'].items() for n in declaration['files']))
            derived_map = read_json(derived / 'requirement-map.json')
            deliv = next(e for e in derived_map['requirements'] if e['id'] == 'ACQ-DELIV-1')
            ids = [c['id'] for c in deliv['spans'][0]['mapping']['checks']]
            self.assertEqual(ids[-3:], ['fab-export', 'cpl', 'fab'])
            fab = next(c for c in deliv['spans'][0]['mapping']['checks'] if c['id'] == 'fab')
            self.assertEqual(fab['params']['assembly_profile'], 'cpl/assembly-profile.json')
            cpl = next(c for c in deliv['spans'][0]['mapping']['checks'] if c['id'] == 'cpl')
            self.assertEqual(cpl['params']['polarity_evidence'], ['cpl/polarity/C83152.polarity.json'])
            self.assertEqual((derived / 'cpl/polarity/C83152.polarity.json').read_bytes(),
                             (CPL_SOURCE / 'polarity/C83152.polarity.json').read_bytes())
            # The polarity record is supplemental interpreted data, never a judgment record.
            self.assertFalse((derived / 'judgments').exists())
            intervals = json.loads((derived / 'analysis/electrical-intervals.json').read_bytes())
            base = json.loads((RETAINED / 'analysis/electrical-intervals.json').read_bytes())
            self.assertEqual(intervals['parameters'].keys(), base['parameters'].keys())
            self.assertEqual([a['id'] for a in intervals['assertions']], [a['id'] for a in base['assertions']])
            self.assertEqual({a['requirement'] for a in intervals['assertions']},
                             {'ACQ-ELEC-1-SUPPLY', 'ACQ-ELEC-2-RAIL', 'ACQ-ELEC-3-REVERSE',
                              'ACQ-ELEC-4-MEASUREMENT', 'ACQ-ELEC-5-PULLUPS'})
            for a, b in zip(intervals['assertions'], base['assertions']):
                self.assertEqual({k: v for k, v in a.items() if k != 'requirement'},
                                 {k: v for k, v in b.items() if k != 'requirement'})
            # Actual captured PDF text must survive the same read_text/quote path as judgments.
            manifest = read_json(derived / 'manufacturer-text/manifest.json')
            self.assertEqual(manifest['status'], 'complete')
            self.assertEqual(len(manifest['documents']), 13)
            self.assertEqual(sum(len(d['pages']) for d in manifest['documents']), 574)
            for document in manifest['documents']:
                name = 'manufacturer-text/' + document['text']['path']
                raw = (derived / name).read_bytes()
                text = (derived / name).read_text(encoding='utf-8')
                self.assertEqual(sha256(raw), document['text']['sha256'])
                self.assertEqual(text.encode('utf-8'), raw)
                offset = 0
                for number, page in enumerate(document['pages'], 1):
                    self.assertEqual(page['physical_page'], number)
                    self.assertEqual(page['byte_start'], offset)
                    end = page['byte_end']
                    self.assertEqual(raw[page['content_byte_end']:end], b'\f')
                    self.assertEqual(sha256(raw[offset:end]), page['sha256'])
                    offset = end
                self.assertEqual(offset, len(raw))
                page = next(p for p in document['pages'] if p['text_nonempty'])
                paragraph = raw[page['byte_start']:page['content_byte_end']].decode('utf-8')
                quote = next(line.strip() for line in paragraph.splitlines() if line.strip())
                citation = {'path': name, 'sha256': document['text']['sha256'],
                            'locator': f"PDF {document['source']['sha256']}, physical page {page['physical_page']}",
                            'quote': quote}
                self.assertEqual(verified_reference(citation, {name: text})['enforcement'], 'verified')


if __name__ == '__main__':
    unittest.main()
