"""Exact owner dispositions and real native ERC; fixture approvals are synthetic."""
import copy
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from uuid import UUID

from agent_ee import candidate, checks, executor
from agent_ee import design_rule_provenance as provenance
from agent_ee.baseline import freeze_evidence, load_baseline, verify_evidence
from agent_ee.capabilities import convert_scope
from agent_ee.campaign import check_proposal, mutable_inputs
from agent_ee.harnesses import evidence_mounts
from agent_ee.storage import digest, identity, read_json, write_json
from agent_ee import violation_waivers as waivers
from tests.toolchain import require_toolchain
from owner_fixture import cite

ROOT = Path(__file__).resolve().parents[1]
POLICY = 'owner/waivers.json'


def approved(entries):
    return dict(schema=waivers.SCHEMA, waivers=entries,
                approval=dict(owner='synthetic-owner', disposition='approved',
                              waivers_sha256=identity(entries), reference='Owned test approval, not a real waiver'))


def fixture(root, kind='erc'):
    template = ROOT / ('fixtures/kicad-quirks/erc-scale.scope.json' if kind == 'erc'
                       else 'fixtures/closed-loop/control.scope.json')
    scope = read_json(template)
    for spec in scope['inputs']:
        spec['path'] = str((template.parent / spec['path']).resolve())
    scope.update(schema='agent-ee.scope/2', checks=[dict(id=kind, kind=kind)],
                 mutable_inputs=[scope['schematic']] + ([scope['board']] if kind == 'drc' else []))
    req = root / 'requirements.md'
    req.write_text('Original synthetic native check requirement.\n')
    scope['inputs'].append(dict(path=str(req), at='requirements.md'))
    scope['baseline'] = dict(requirements=[dict(id='native', statement='Zero unwaived native violations.',
        source=dict(path='requirements.md', locator='native'), checks=[kind])],
        targets=['requirements.md'], reference_architectures=['requirements.md'])
    scope['requirements'] = ['requirements.md']
    if kind == 'erc':
        # A campaign has a board subject even when this fixture checks only the schematic.
        board = root / 'unused.kicad_pcb'
        shutil.copyfile(ROOT / 'fixtures/rc/control/rc_filter.kicad_pcb', board)
        scope['board'] = 'project/unused.kicad_pcb'
        scope['inputs'].append(dict(path=str(board), at=scope['board']))
    if kind == 'drc':
        project = root / 'project'
        shutil.copytree(scope['inputs'][0]['path'], project)
        scope['inputs'][0]['path'] = str(project)
        # A default limit is advisory (AGENTEE-331), so the fixture declares its own
        # sourced track-width minimum; its native violations then stay hard defects.
        limits = project / 'drc-waiver-limits.json'
        declared = {'schema': provenance.SCHEMA, 'board_setup_rules': {'min_track_width': {
            'value_mm': 0.9, 'class': 'owner',
            'basis': 'Synthetic test fixture: authored track-width minimum for the native DRC waiver '
                     'contract; not a real design rule'}}}
        cite(declared, declared['board_setup_rules']['min_track_width'])  # AGENTEE-348 cited form keeps it hard
        write_json(limits, declared)
        pro = project / 'rc_filter.kicad_pro'
        data, record = provenance.import_constraints(read_json(pro), limits.read_bytes(),
                                                     'project/' + limits.name, digest(limits))
        provenance.bind_project(data, record)
        write_json(pro, data)
    return scope


def capture(root, scope, entries=None, name='run'):
    scope = copy.deepcopy(scope)
    if entries is not None:
        source = root / f'{name}-waivers.json'
        write_json(source, approved(entries))
        scope['inputs'].append(dict(path=str(source), at=POLICY))
        scope['checks'][0]['params'] = {'waivers': POLICY}
    scope_path = root / f'{name}.scope.json'
    write_json(scope_path, scope)
    run = root / name
    run.mkdir()
    manifest = candidate.capture(scope_path, run)
    return run, manifest


class CaptureWaiverTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.scope = fixture(self.root)

    def test_capture_binds_approval_in_scope2_and_scope3_baselines(self):
        for version in (2, 3):
            with self.subTest(version=version):
                scope = self.scope if version == 2 else convert_scope(self.scope)
                run, manifest = capture(self.root, scope, [], f'capture-{version}')
                baseline = load_baseline(run)
                self.assertEqual(baseline['files'][POLICY], manifest['files'][POLICY])
                if version == 3:
                    self.assertIn(POLICY, baseline['coverage']['checks']['erc']['immutable_inputs'])
                candidate.verify_snapshot(run)
                (run / 'inputs' / POLICY).write_text('{}')
                with self.assertRaisesRegex(ValueError, 'snapshot changed'):
                    candidate.verify_snapshot(run)

    def test_capture_refuses_writer_reachable_waiver_paths_and_aliases(self):
        for mode in ('exact', 'sibling', 'alias', 'legacy'):
            with self.subTest(mode=mode):
                scope = copy.deepcopy(self.scope)
                source = self.root / f'{mode}.json'
                write_json(source, approved([]))
                scope['inputs'].append(dict(path=str(source), at=POLICY))
                scope['checks'][0]['params'] = {'waivers': POLICY}
                if mode == 'exact':
                    scope['mutable_inputs'].append(POLICY)
                elif mode == 'sibling':
                    scope['mutable_inputs'].append('owner/another.json')
                elif mode == 'alias':
                    scope['inputs'].append(dict(path=str(source), at='design/alias.json'))
                    scope['mutable_inputs'].append('design/alias.json')
                else:
                    scope['schema'] = 'agent-ee.scope/1'
                with self.assertRaisesRegex(ValueError, 'writer|mutable|immutable'):
                    capture(self.root, scope, name=mode)

    def test_capture_refuses_unapproved_or_changed_list(self):
        for index, change in enumerate(('unapproved', 'digest', 'owner', 'duplicate-json')):
            with self.subTest(change=change):
                source = self.root / f'bad-{index}.json'
                record = approved([])
                if change == 'unapproved':
                    record['approval']['disposition'] = 'pending'
                elif change == 'digest':
                    record['approval']['waivers_sha256'] = '0' * 64
                elif change == 'owner':
                    record['approval']['owner'] = ''
                write_json(source, record)
                if change == 'duplicate-json':
                    source.write_text('{"schema":"a","schema":"b"}')
                scope = copy.deepcopy(self.scope)
                scope['inputs'].append(dict(path=str(source), at=POLICY))
                scope['checks'][0]['params'] = {'waivers': POLICY}
                with self.assertRaises(ValueError):
                    capture(self.root, scope, name=f'bad-{index}')

    def test_invalid_entries_and_stale_list_approval_are_refused(self):
        entry = dict(check='erc', type='unconnected_wire_endpoint', items=[str(UUID(int=1))],
                     location_hash='a' * 64, reason='Original test reason', owner='synthetic-owner')
        for field, value in [('check', 'netlist'), ('type', ''), ('reason', ''), ('owner', 'agent'),
                             ('items', []), ('items', ['not-a-uuid']),
                             ('items', [str(UUID(int=2)), str(UUID(int=1))]),
                             ('items', [str(UUID(int=1))] * 2), ('location_hash', '*')]:
            with self.subTest(field=field, value=value):
                bad = dict(entry, **{field: value})
                with self.assertRaises(ValueError):
                    waivers.validate(approved([bad]))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            waivers.validate(approved([entry, entry]))
        record = approved([entry])
        record['waivers'][0]['reason'] = 'Changed after approval'
        with self.assertRaisesRegex(ValueError, 'exact list'):
            waivers.validate(record)

    def test_writer_cannot_edit_waivers(self):
        require_toolchain()
        run, manifest = capture(self.root, self.scope, [])
        work = run / 'review-work/implementation'
        shutil.copytree(run / 'inputs', work / 'design')
        original = digest(run / 'inputs' / POLICY)
        # Both native subscription adapters use these exact mounts. No model is launched.
        rec = executor.execute(run / 'inputs', work, ['/usr/bin/python3', '-c', '''
import errno, pathlib
for path in ('/candidate/owner/waivers.json', '/work/design/owner/waivers.json'):
    try:
        pathlib.Path(path).write_text('forged approval')
    except OSError as exc:
        assert exc.errno in (errno.EROFS, errno.EACCES), exc
    else:
        raise AssertionError('writer could edit an owner disposition')
try:
    pathlib.Path('/work/design/owner').rename('/work/design/renamed-owner')
except OSError:
    pass
else:
    raise AssertionError('writer could move the protected namespace')
'''], extra_ro=evidence_mounts(run, 'implementation'))
        self.assertEqual((rec['execution'], rec['exit_code']), ('ok', 0), (work / 'console.log').read_text())
        self.assertEqual(digest(run / 'inputs' / POLICY), original)
        self.assertEqual(digest(work / 'design' / POLICY), original)
        baseline = load_baseline(run)
        authority = mutable_inputs(manifest, baseline)
        check_proposal(work / 'design', manifest, dict(mutable_inputs=authority, baseline=baseline))
        (work / 'design' / POLICY).write_text('host tamper after sandbox exit')
        with self.assertRaisesRegex(ValueError, 'immutable'):
            check_proposal(work / 'design', manifest, dict(mutable_inputs=authority, baseline=baseline))


class NativeWaiverContract:
    kind = 'erc'

    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        cls.scope = fixture(cls.root, cls.kind)
        cls.initial_run, _ = capture(cls.root, cls.scope, name='unwaived')
        cls.initial = checks.run_checks(cls.initial_run, time.monotonic() + 900)[0]
        if (cls.initial['execution'], cls.initial['outcome']) != ('ok', 'defect'):
            raise AssertionError(cls.initial)
        cls.report = read_json(cls.initial_run / f'work/{cls.kind}/{cls.kind}.json')
        cls.entries = []
        groups = ([(s, 'violations', s['violations']) for s in cls.report['sheets']] if cls.kind == 'erc'
                  else [(None, c, cls.report[c]) for c in ('violations', 'unconnected_items', 'schematic_parity')])
        for sheet, category, violations in groups:
            for v in violations:
                key = waivers.violation_key(cls.kind, cls.scope['schematic' if cls.kind == 'erc' else 'board'],
                                            category, cls.report, v, sheet)
                if key:
                    entry = dict(key, reason='Synthetic exact test disposition', owner='synthetic-owner')
                    # KiCad can emit identical duplicates (both wire ends at the same point).
                    if entry not in cls.entries:
                        cls.entries.append(entry)
        if not cls.entries:
            raise AssertionError('native fixture produced no waiver-eligible violations')

    def native(self, entries, name):
        run, manifest = capture(self.root, self.scope, entries, self.kind + '-' + name)
        result = checks.run_checks(run, time.monotonic() + 900)[0]
        self.assertEqual(result['execution'], 'ok', result)
        return run, manifest, result

    def test_waiver_exact_match(self):
        first = next(e for e in self.entries if any(o['type'] == e['type'] and o['items'] != e['items']
                                                  for o in self.entries))
        _, _, result = self.native([first], 'exact')
        self.assertEqual(result['outcome'], 'defect')
        self.assertEqual(len(result['details']['waived']), 1)
        waived = result['details']['waived'][0]
        self.assertEqual(waived['waiver_key'], {k: first[k] for k in ('check', 'type', 'items', 'location_hash')})
        self.assertEqual((waived['status'], waived['reason']), ('waived', first['reason']))
        self.assertEqual(result['details']['by_severity'], self.initial['details']['by_severity'])
        self.assertEqual(sum(result['details']['waived_by_severity'].values()), 1)
        self.assertGreater(result['details']['unwaived'], 0)

    def test_all_exact_dispositions_pass_without_hiding_native_report(self):
        run, _, result = self.native(self.entries, 'all')
        self.assertEqual(result['outcome'], 'pass', result)
        self.assertEqual(result['details']['unwaived'], 0)
        self.assertEqual(len(result['details']['waived']), sum(self.initial['details']['by_severity'].values()))
        self.assertNotIn('gate_findings', result['details'])
        self.assertIn(f'work/{self.kind}/waivers.json', result['evidence'])
        self.assertEqual(waivers.load(run / f'work/{self.kind}/waivers.json'), approved(self.entries))
        raw = read_json(run / f'work/{self.kind}/{self.kind}.json')
        self.assertNotIn('waived', raw)
        freeze_evidence(run, [result])
        verify_evidence(run, [result], required=True)
        (run / f'work/{self.kind}/waivers.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'seal contradicts'):
            verify_evidence(run, [result], required=True)

    def test_tampered_waiver_uuid_and_location_hash_do_not_match(self):
        for field, value in (('items', [str(UUID(int=1))]), ('location_hash', '0' * 64)):
            with self.subTest(field=field):
                entry = dict(self.entries[0], **{field: value})
                run, manifest = capture(self.root, self.scope, [entry], self.kind + '-key-' + field)
                ctx = checks.Ctx(run, manifest, manifest['scope']['checks'][0], time.monotonic() + 60)
                self.assertTrue(waivers.classify(ctx, self.report))
                self.assertEqual(ctx.result['details']['waived'], [])

    def test_tampered_uuid_location_type_and_approval_fail_closed(self):
        run, manifest = capture(self.root, self.scope, [self.entries[0]], self.kind + '-tamper')
        for change in ('uuid', 'location', 'type', 'sheet', 'units', 'version', 'missing-position',
                       'description', 'severity'):
            with self.subTest(change=change):
                report = copy.deepcopy(self.report)
                if self.kind == 'erc':
                    sheet = report['sheets'][0]
                    v = next(v for v in sheet['violations'] if v['type'] == self.entries[0]['type'] and
                             sorted(i['uuid'] for i in v['items']) == self.entries[0]['items'])
                else:
                    v = next(v for c in ('violations', 'unconnected_items', 'schematic_parity') for v in report[c]
                             if v['type'] == self.entries[0]['type'] and
                             sorted(i['uuid'] for i in v['items']) == self.entries[0]['items'])
                if change == 'uuid':
                    v['items'][0]['uuid'] = str(UUID(int=1))
                elif change == 'location':
                    v['items'][0]['pos']['x'] += .001
                elif change == 'type':
                    v['type'] = 'different_rule'
                elif change == 'sheet' and self.kind == 'erc':
                    sheet['uuid_path'] += '/other-instance'
                elif change == 'sheet':
                    v['items'].append(copy.deepcopy(v['items'][0]))
                elif change == 'missing-position':
                    v['items'][0].pop('pos')
                elif change == 'units':
                    report['coordinate_units'] = 'mils'
                elif change == 'description':
                    v['description'] += ' changed'
                elif change == 'severity':
                    v['severity'] = 'exclusion'
                else:
                    report['kicad_version'] += '-unknown'
                ctx = checks.Ctx(run, manifest, manifest['scope']['checks'][0], time.monotonic() + 60)
                remaining = waivers.classify(ctx, report)
                self.assertEqual(ctx.result['details']['waived'], [])
                self.assertTrue(remaining)
        (run / 'inputs' / POLICY).write_text('{}')
        ctx = checks.Ctx(run, manifest, manifest['scope']['checks'][0], time.monotonic() + 60)
        with self.assertRaises(checks.CheckError) as caught:
            waivers.classify(ctx, self.report)
        self.assertEqual((caught.exception.execution, ctx.result['outcome']), ('malformed', 'unknown'))

    def test_no_waiver_preserves_failures_and_withheld_locations_never_waive(self):
        self.assertEqual(self.initial['details']['waived'], [])
        self.assertEqual(self.initial['details']['unwaived_by_severity'], self.initial['details']['by_severity'])
        run, manifest = capture(self.root, self.scope, self.entries, self.kind + '-withheld')
        ctx = checks.Ctx(run, manifest, manifest['scope']['checks'][0], time.monotonic() + 60)
        remaining = waivers.classify(ctx, self.report, locations_trusted=False)
        self.assertTrue(remaining)
        self.assertEqual(ctx.result['details']['waived'], [])


class NativeErcWaiverTests(NativeWaiverContract, unittest.TestCase):
    kind = 'erc'
