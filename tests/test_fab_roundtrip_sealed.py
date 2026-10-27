"""Sealed-package fab_roundtrip (AGENTEE-524 part A): the released bytes, bound after the done gate."""
import copy
import hashlib
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agent_ee import checks, fab_consistency, fab_export
from agent_ee.baseline import compile_baseline, freeze_evidence, verify_evidence
from agent_ee.candidate import capture
from agent_ee.fab_export import write_zip
from agent_ee.fab_package import artifact_record, require_last
from agent_ee.fab_roundtrip import sealed_members
from agent_ee.storage import digest, read_json, write_json
from tests.receipt_diagnostics import ReceiptTemporaryDirectory
from tests.test_fab_package import captured_files, generation_scope
from tests.toolchain import require_toolchain

# The F.Cu pad flash and NPTH tool of the owned fab-export control (as in test_fab_roundtrip).
PAD_FLASH = 'X115200000Y-106000000D03*\n'
NPTH_TOOL = ('T1C1.500', 'T1C1.600')


def sealed_scope():
    scope = generation_scope('agent-ee.scope/3')
    scope['checks'].append({'id': 'roundtrip', 'kind': 'fab_roundtrip', 'params': {'package_check': 'fab'}})
    scope['baseline']['requirements'][0]['checks'].append('roundtrip')
    return scope


def rezip_and_rehash(stage):
    """What an exporter writes: a zip of its staged plots and an honest manifest of every file."""
    manifest = read_json(stage / 'manifest.json')
    write_zip(stage / 'gerbers.zip', [(p.name, p) for directory in ('gerbers', 'drill')
                                      for p in sorted((stage / directory).iterdir())])
    manifest['files'] = {rel: digest(stage / rel) for rel in manifest['files'] if (stage / rel).is_file()}
    write_json(stage / 'manifest.json', manifest)


def edit(stage, pattern, before, after):
    path = next(stage.glob(pattern))
    text = path.read_text()
    if before not in text:
        raise AssertionError(f'{before!r} absent from {path}')
    path.write_text(text.replace(before, after, 1))


class CapabilityTests(unittest.TestCase):
    """Static scope contract: binding, ordering and exact params (T8, T9, P1)."""

    def setUp(self):
        self.scope = sealed_scope()
        self.files = captured_files(self.scope)

    def test_bound_sealed_roundtrip_follows_the_done_gate(self):
        descriptor = compile_baseline(self.scope, self.files)['coverage']['checks']['roundtrip']
        self.assertEqual(descriptor['predicates'], ['fab-package:roundtrip/1', 'fab-package:sealed-roundtrip/1'])
        self.assertEqual([p['check_id'] for p in descriptor['prerequisites']], ['fab', 'geometry'])

    def test_unbound_wrong_kind_forward_and_mixed_params_refuse(self):
        def unbound(scope):
            scope['checks'][-1]['params']['package_check'] = 'other'

        def wrong_kind(scope):
            scope['checks'][-1]['params']['package_check'] = 'netlist'

        def export_mode(scope):
            scope['checks'][-1]['params'] = {'export_check': 'fab'}

        def both(scope):
            scope['checks'][-1]['params']['export_check'] = 'fab'

        def extra(scope):
            scope['checks'][-1]['params']['model_libraries'] = {}

        def forward(scope):
            scope['checks'].insert(2, scope['checks'].pop())

        for mutate in (unbound, wrong_kind, export_mode, both, extra, forward):
            scope = copy.deepcopy(self.scope)
            mutate(scope)
            with self.subTest(mutate.__name__), self.assertRaises(ValueError):
                compile_baseline(scope, self.files)

    def test_require_last_admits_only_bound_followers(self):
        gate = {'id': 'fab', 'kind': 'fab_package'}
        bound = [gate, {'id': 'rt', 'kind': 'fab_roundtrip', 'params': {'package_check': 'fab'}},
                 {'id': 'dfm', 'kind': 'jlc_dfm', 'params': {'fab_check': 'fab', 'report': 'r.json'}}]
        require_last(bound, gate)
        for follower in ({'id': 'rt', 'kind': 'fab_roundtrip', 'params': {'export_check': 'fab'}},
                         {'id': 'rt', 'kind': 'fab_roundtrip', 'params': {'package_check': 'other'}},
                         {'id': 'rt', 'kind': 'fab_roundtrip'},
                         {'id': 'dfm', 'kind': 'jlc_dfm', 'params': {'package_check': 'fab'}}):
            with self.subTest(follower=follower), self.assertRaisesRegex(ValueError, 'done check last'):
                require_last([gate, follower], gate)


class SealedMembersTests(unittest.TestCase):
    """The archive reader returns only exact released bytes, with retained digests (T6)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='agent-ee-sealed-members-')
        self.addCleanup(tmp.cleanup)
        self.stage = Path(tmp.name) / 'work/fab/fab'
        for rel, body in (('gerbers/b-F_Cu.gbr', b'G04 top*\nM02*\n'), ('drill/b-PTH.drl', b'M48\nM30\n')):
            (self.stage / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.stage / rel).write_bytes(body)
        self.seal()

    def seal(self, zipped=None):
        write_zip(self.stage / 'gerbers.zip', zipped or [(p.name, p) for d in ('gerbers', 'drill')
                                                          for p in sorted((self.stage / d).iterdir())])
        rels = ('gerbers/b-F_Cu.gbr', 'drill/b-PTH.drl', 'gerbers.zip')
        self.manifest = {'files': {rel: digest(self.stage / rel) for rel in rels}}
        self.found = {rel: (self.stage / rel).stat().st_size for rel in rels}
        self.artifacts = {'files': {f'work/fab/fab/{rel}': self.manifest['files'][rel]
                                    for rel in ('drill/b-PTH.drl', 'gerbers.zip')}}

    def members(self):
        return sealed_members(self.stage, self.found, self.manifest, self.artifacts, 'fab')

    def test_members_are_the_sealed_bytes_with_retained_equality_proof(self):
        members, compared = self.members()
        self.assertEqual(members['drill/b-PTH.drl'], b'M48\nM30\n')
        self.assertEqual(compared['gerbers_zip_sha256'], self.artifacts['files']['work/fab/fab/gerbers.zip'])
        records = {r['staged_path']: r for r in compared['members']}
        for rel, body in members.items():
            self.assertEqual(records[rel]['member_sha256'], hashlib.sha256(body).hexdigest())
            self.assertEqual(records[rel]['member_sha256'], self.manifest['files'][rel])
        self.assertEqual(records['drill/b-PTH.drl']['sealed_sha256'], records['drill/b-PTH.drl']['member_sha256'])
        self.assertNotIn('sealed_path', records['gerbers/b-F_Cu.gbr'])  # plots are released only inside the zip

    def test_zip_digest_member_and_inventory_disagreements_refuse(self):
        cases = {
            'zip digest': lambda: self.artifacts['files'].update({'work/fab/fab/gerbers.zip': '0' * 64}),
            'member differs from staged drill': lambda: (self.stage / 'drill/b-PTH.drl').write_bytes(b'M48\nX1Y1\nM30\n'),
            'member omitted': lambda: self.seal([('b-F_Cu.gbr', self.stage / 'gerbers/b-F_Cu.gbr')]),
            'extra member': lambda: self.seal([(p.name, p) for p in sorted(self.stage.rglob('*.gbr'))]
                                              + [('b-PTH.drl', self.stage / 'drill/b-PTH.drl'),
                                                 ('extra.gbr', self.stage / 'gerbers/b-F_Cu.gbr')]),
            'unsafe member': lambda: self.seal([('../b-F_Cu.gbr', self.stage / 'gerbers/b-F_Cu.gbr')]),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                self.setUp()
                mutate()
                with self.assertRaises(fab_consistency.Malformed):
                    self.members()


class NativeSealedRoundtripTests(unittest.TestCase):
    """The real export, done gate and seal, then the bound roundtrip on the released bytes."""

    def setUp(self):
        require_toolchain(native=True)
        tmp = ReceiptTemporaryDirectory(prefix='agent-ee-sealed-roundtrip-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.runs = 0

    def run_case(self, *, export_fault=None, after_seal=None):
        """export_fault edits the exporter's staging before the gate (then rezips and rehashes
        honestly); after_seal edits the sealed package between the done gate and the roundtrip."""
        self.runs += 1
        case = self.root / str(self.runs)
        case.mkdir()
        write_json(case / 'scope.json', sealed_scope())
        self.run_dir = case / 'run'
        self.run_dir.mkdir()
        capture(case / 'scope.json', self.run_dir)
        producer = fab_export.check_fab_export
        roundtrip = checks.CHECK_FUNCS['fab_roundtrip']

        def faulty_export(ctx):
            producer(ctx)
            export_fault(ctx.work / 'fab')
            rezip_and_rehash(ctx.work / 'fab')

        def tampered_roundtrip(ctx):
            after_seal(ctx.run_dir / 'work/fab/fab')
            roundtrip(ctx)
        with mock.patch.dict(checks.CHECK_FUNCS, {
                'fab_export': faulty_export if export_fault else producer,
                'fab_roundtrip': tampered_roundtrip if after_seal else roundtrip}):
            results = checks.run_checks(self.run_dir, time.monotonic() + 300)
        freeze_evidence(self.run_dir, results)
        verify_evidence(self.run_dir, results, required=True)
        self.results = {r['id']: r for r in results}
        return self.results['fab'], self.results['roundtrip']

    def assert_binding_passed(self, package, roundtrip):
        """The package sealed honestly and the roundtrip compared exactly its released bytes."""
        self.assertEqual((package['execution'], package['outcome']), ('ok', 'pass'), package)
        if (self.run_dir / 'work/fab/fab/gerbers.zip').read_bytes() == b'':
            self.fail('empty sealed zip')
        recorded = package['details']['artifacts']
        self.assertEqual(artifact_record(self.run_dir, package), recorded)
        details = roundtrip['details']
        self.assertEqual((details['package_check'], details['artifacts_id']), ('fab', recorded['artifacts_id']))
        compared = details['compared']
        self.assertEqual(compared['gerbers_zip_sha256'], recorded['files']['work/fab/fab/gerbers.zip'])
        manifest = read_json(self.run_dir / 'work/fab/fab/manifest.json')
        for record in compared['members']:
            self.assertEqual(record['member_sha256'], manifest['files'][record['staged_path']])
            if 'sealed_path' in record:
                self.assertEqual(record['sealed_sha256'], recorded['files'][record['sealed_path']])
        drills = {p for p in recorded['files'] if '/drill/' in p}
        self.assertEqual({r.get('sealed_path') for r in compared['members']} - {None}, drills)
        plots = {r['staged_path'] for r in compared['members'] if r['staged_path'].startswith('gerbers/')}
        self.assertEqual(plots, {p['path'] for p in details['plots']})

    def test_honest_sealed_package_conserves_geometry_of_released_bytes(self):
        package, roundtrip = self.run_case()
        self.assert_binding_passed(package, roundtrip)
        self.assertEqual((roundtrip['execution'], roundtrip['outcome']), ('ok', 'pass'), roundtrip)
        self.assertIn('fab roundtrip conserved', roundtrip['summary'])
        self.assertTrue(any('artwork rendering, hole positions' in text for text in roundtrip['limitations']))

    def test_honestly_sealed_exporter_faults_fail_only_native_geometry(self):
        """T4/T5: every digest and the sealed record agree; only the independent probe disagrees."""
        for name, fault, code in (
                ('dropped F.Cu pad flash', lambda stage: edit(stage, 'gerbers/*F_Cu*', PAD_FLASH, ''), 'flash_count'),
                ('NPTH diameter', lambda stage: edit(stage, 'drill/*-NPTH.drl', *NPTH_TOOL), 'drill_histogram')):
            with self.subTest(name):
                package, roundtrip = self.run_case(export_fault=fault)
                self.assert_binding_passed(package, roundtrip)
                self.assertEqual((roundtrip['execution'], roundtrip['outcome']), ('ok', 'defect'), roundtrip)
                self.assertIn(code, {d['code'] for d in roundtrip['details']['defects']})

    def test_tampering_after_seal_refuses_before_any_geometry_verdict(self):
        """T2/T3: a digest refusal is never reported as geometric coverage."""
        def flip_zip(stage):
            data = bytearray((stage / 'gerbers.zip').read_bytes())
            data[-1] ^= 1
            (stage / 'gerbers.zip').write_bytes(bytes(data))

        def rehashed_edit(stage):
            edit(stage, 'gerbers/*F_Cu*', PAD_FLASH, '')
            rezip_and_rehash(stage)
        for name, tamper, message in (('zip byte', flip_zip, 'does not verify'),
                                      ('re-hashed manifest', rehashed_edit, 'differs from the record its result sealed')):
            with self.subTest(name):
                package, roundtrip = self.run_case(after_seal=tamper)
                self.assertEqual((package['execution'], package['outcome']), ('ok', 'pass'), package)
                self.assertEqual((roundtrip['execution'], roundtrip['outcome']), ('malformed', 'unknown'), roundtrip)
                self.assertIn(message, roundtrip['summary'])
                for key in ('compared', 'plots', 'drill', 'defects'):
                    self.assertNotIn(key, roundtrip['details'])
                self.assertIsNone(re.search(r'conserv', roundtrip['summary']))


if __name__ == '__main__':
    unittest.main()
