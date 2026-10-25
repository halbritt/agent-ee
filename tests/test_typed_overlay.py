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
from agent_ee.storage import json_bytes, read_json

ROOT = Path(__file__).resolve().parents[1]
POD = ROOT / 'fixtures/telemetry-pod'
ACQUISITION_OVERLAY = ROOT / 'fixtures/acquisition-typed/overlay.json'
RETAINED = Path(os.environ.get('AGENT_EE_ACQUISITION_INPUTS', ROOT / 'build/acquisition-release/inputs'))


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
                    derived_files={})

    def prepare(self, overlay=None, name='out'):
        path = Path(self.tmp.name) / 'overlay.json'
        path.write_bytes(json_bytes(self.overlay if overlay is None else overlay))
        return typed_overlay.prepare(self.base, path, Path(self.tmp.name) / name)

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


@unittest.skipUnless((RETAINED / 'spec.md').exists(), 'retained acquisition inputs are not present on this host')
class AcquisitionOverlayTests(unittest.TestCase):
    """The committed acquisition overlay against the retained, git-ignored captured inputs."""

    def test_committed_overlay_prepares_and_accounts_every_clause(self):
        overlay = read_json(ACQUISITION_OVERLAY)
        for name, key in (('spec.md', 'spec_sha256'), ('spec-record.json', 'spec_record_sha256'),
                          ('requirement-map.json', 'requirement_map_sha256')):
            self.assertEqual(sha256((RETAINED / name).read_bytes()), overlay['base'][key], name)
        with tempfile.TemporaryDirectory() as tmp:
            result = typed_overlay.prepare(RETAINED, ACQUISITION_OVERLAY, Path(tmp) / 'derived')
            accounting = result['manifest']['accounting']
            self.assertEqual(accounting['obligations']['operational'], 1)
            self.assertEqual(accounting['obligations']['physical'], 2)
            self.assertEqual(accounting['obligations']['context'], 1)
            self.assertEqual(accounting['obligations']['judgment'], 13)
            self.assertEqual(len(accounting['judgment_records_declared']), 13)
            self.assertEqual(accounting['judgment_records_present'], [])
            derived = Path(tmp) / 'derived'
            self.assertFalse((derived / 'judgments').exists())
            self.assertEqual(set(result['manifest']['derived']['changed_files']),
                             {'requirement-map.json', 'analysis/electrical-intervals.json'})
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


if __name__ == '__main__':
    unittest.main()
