import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from agent_ee import board_envelope, requirement_map, spec_record
from agent_ee.baseline import compile_baseline
from agent_ee.storage import digest, json_bytes, write_json

ROOT = Path(__file__).resolve().parents[1]


def add_rc_envelope(template, entries, directory):
    template['envelope'] = dict(path='envelope.json', profile='fab-profile.json', dialog=None)
    record = board_envelope.validate((directory / 'envelope.json').read_bytes(),
        profile=(directory / 'fab-profile.json').read_bytes(), captured=board_envelope.source_inventory(directory))
    derived = board_envelope.derive_requirements(record, 'envelope.json')['requirements']
    selectors = {'outline': ('board_envelope', dict(criterion='outline/1')),
                 'layer_count': ('board_envelope', dict(criterion='layers/1')),
                 'stackup': ('board_envelope', dict(criterion='stackup/1')),
                 'fab_profile': ('fab_profile', dict(fab_profile='fab-profile.json')),
                 'fixed_footprints': ('placement', dict(criterion='connector-edge/1', reference='J1', drc_check='drc'))}
    for row in derived:
        field = row['source']['locator'].split('/')[1]
        kind, params = selectors[field]
        entries.append(dict(id=row['id'], checks=[dict(id='envelope-' + field, kind=kind,
            params=params if field == 'fab_profile' else dict(params, envelope='envelope.json'), stage='verification')]))
    return derived


class EnvelopeMapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / 'fixtures/rc/control', self.root / 'project')
        shutil.copyfile(ROOT / 'fixtures/board-envelope/rc-jlc-2layer.envelope.json', self.root / 'envelope.json')
        shutil.copytree(ROOT / 'fixtures/board-envelope/jlc-2layer', self.root, dirs_exist_ok=True)
        self.spec = b'# Checks\n\n- Native ERC must pass.\n- Native DRC must pass.\n'
        self.record = json_bytes(spec_record.capture(self.spec, 'QUAL', {'Checks': 'CHECKS'}))
        template = dict(schema='agent-ee.scope/3', name='Envelope map', scope='Original compiler qualification.',
            limitations=[], inputs=[dict(path='project', at='project'),dict(path='envelope.json', at='envelope.json'),
                dict(path='fab-profile.json', at='fab-profile.json'), dict(path='sources', at='sources')],
            schematic='project/rc_filter.kicad_sch', board='project/rc_filter.kicad_pcb',
            stages=dict(schema='agent-ee.stages/1', order=['verification'], finding_retry_limit=3,
                subjects=dict(verification=dict(path='project/rc_filter.kicad_sch', kind='symbols', references=['R1','C1','J1']))))
        entries = [dict(id=row['id'], checks=[dict(id=kind, kind=kind, params={}, stage='verification')])
                   for row, kind in zip(json.loads(self.record)['requirements'], ['erc','drc'])]
        self.derived = add_rc_envelope(template, entries, self.root)
        self.declaration = dict(schema='agent-ee.requirement-map/1', spec_record_sha256=spec_record.sha256(self.record),
            scope=template, requirements=entries, prose=[])
        self.files = {p.relative_to(self.root).as_posix(): digest(p) for p in self.root.rglob('*') if p.is_file()}

    def compile(self, declaration=None):
        return requirement_map.compile_map(json_bytes(declaration or self.declaration), self.record,
            self.spec, files=self.files, inputs=self.root)

    def test_declared_envelope_sources_keep_their_exact_identity_and_coverage(self):
        scope, receipt = self.compile()
        actual = {r['id']: r for r in scope['baseline']['requirements']}
        self.assertEqual(len(actual), 7)
        for expected in self.derived:
            for key in ['statement','source','intent']:
                self.assertEqual(actual[expected['id']][key], expected[key])
        self.assertEqual([c['id'] for c in scope['checks']], ['erc','drc','envelope-outline',
            'envelope-layer_count','envelope-stackup','envelope-fab_profile','envelope-fixed_footprints'])
        provenance = self.root / requirement_map.PROVENANCE
        provenance.mkdir()
        for name, raw in [('spec.md',self.spec),('spec-record.json',self.record),
                          ('requirement-map.json',json_bytes(self.declaration))]:
            (provenance / name).write_bytes(raw)
        files = {p.relative_to(self.root).as_posix(): digest(p) for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(compile_baseline(scope,files,self.root)['baseline_id'],receipt['baseline_id'])

    def test_omitted_or_invented_envelope_requirements_are_refused(self):
        for row in self.derived:
            broken = copy.deepcopy(self.declaration)
            broken['requirements'] = [e for e in broken['requirements'] if e['id'] != row['id']]
            with self.subTest(id=row['id']), self.assertRaisesRegex(requirement_map.Refused, 'no mapping'):
                self.compile(broken)
        broken = copy.deepcopy(self.declaration)
        broken['requirements'][-1]['id'] = 'R-envelope-rc-filter-invented'
        with self.assertRaisesRegex(requirement_map.Refused, 'not present'):
            self.compile(broken)
        del broken['scope']['envelope']
        with self.assertRaisesRegex(requirement_map.Refused, 'not present'):
            self.compile(broken)

    def test_wrong_predicate_or_dropped_physical_obligation_cannot_compile(self):
        for mode in ['wrong_field', 'limitation', 'bench']:
            broken = copy.deepcopy(self.declaration)
            row = next(r for r in broken['requirements'] if r['id'].endswith('-outline'))
            if mode == 'wrong_field':
                row['checks'][0]['params']['criterion'] = 'layers/1'
            else:
                del row['checks']
                row[mode] = (dict(reason='Deferred') if mode == 'limitation' else
                             dict(criterion='Outline matches', procedure='Inspect later'))
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'exact criterion|lack executable coverage'):
                self.compile(broken)

    def test_captured_envelope_and_profile_bytes_are_required_and_bound(self):
        for name in ['envelope.json', 'fab-profile.json', 'sources/jlcpcb-2layer.txt']:
            path = self.root / name
            original = path.read_bytes()
            path.write_bytes(original + b'\n')
            with self.subTest(path=name), self.assertRaises(ValueError):
                self.compile()
            path.write_bytes(original)
        (self.root / 'envelope.json').unlink()
        with self.assertRaises(ValueError):
            self.compile()

    def test_changed_envelope_cannot_reuse_old_ids(self):
        path = self.root / 'envelope.json'
        value = json.loads(path.read_bytes())
        value['id'] = 'different-board'
        write_json(path, value)
        self.files['envelope.json'] = digest(path)
        with self.assertRaisesRegex(requirement_map.Refused, 'not present'):
            self.compile()


if __name__ == '__main__':
    unittest.main()
