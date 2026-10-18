"""Coordinator-observed stage receipts: real sandbox steps, exact applicability, tamper refusal.

The step tool here is an original test program, not a scaffold, placer or router. These
tests qualify the receipt and campaign seam; they do not establish any rung-1 tool.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee import stage_receipts
from agent_ee.campaign import implement, run_campaign, verify_campaign
from agent_ee.executor import Cancellation, sandbox_argv
from agent_ee.stage_receipts import (adopt, applicability, bind_implementation, build_index, run_step,
                                     sandbox_profile, validate_receipt, validate_step, verify_implementation)
from agent_ee.storage import digest, identity, read_json, write_json

from toolchain import toolchain_test

TOOL = '''import json, os, sys, time
from pathlib import Path
mode = sys.argv[1]
board = Path("/work/design/project/board.txt")
if mode == "plant":
    # Plan: remove, files, copy (from the read-only tool mount), links, fifos, sleep, exit.
    plan = json.loads(sys.argv[2])
    work = Path("/work")
    for rel in plan.get("remove", []):
        (work / rel).unlink()
    for rel, text in plan.get("files", {}).items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(text)
    for rel, source in plan.get("copy", {}).items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_bytes(Path(source).read_bytes())
    for rel, target in plan.get("links", {}).items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, work / rel)
    for rel in plan.get("fifos", []):
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        os.mkfifo(work / rel)
    for rel, mode in plan.get("chmod", {}).items():
        os.chmod(work / rel, mode)
    time.sleep(plan.get("sleep", 0))
    raise SystemExit(plan.get("exit", 0))
if mode == "version":
    print("step-tool 1.2.3"); raise SystemExit(0)
if mode == "silent-version":
    raise SystemExit(0)
if mode == "sleep":
    time.sleep(30)
if mode == "crash":
    raise RuntimeError("tool crashed")
if mode == "outside":
    Path("/work/design/project/rules.txt").write_text("relaxed")
if mode == "add":
    Path("/work/design/project/extra.txt").write_text("new")
if mode == "out":
    Path("/work/out").mkdir()
    Path("/work/out/board.txt").write_text("routed board")
    Path("/work/out/result.json").write_text(json.dumps({"backend": {"name": "test", "version": None, "build": "b1"}}))
    raise SystemExit(int(sys.argv[2]) if len(sys.argv) > 2 else 0)
board.write_text("placed board " + Path("/candidate/project/board.txt").read_text())
raise SystemExit(int(sys.argv[2]) if len(sys.argv) > 2 else 0)
'''


def step(mode='edit', *extra, **overrides):
    spec = {'schema': 'agent-ee.stage-step/1', 'stage': 'placement', 'step': 'test-helper',
            'argv': ['/usr/bin/python3', '/tool/step.py', mode, *extra],
            'mounts': [{'source': '<tool>', 'at': '/tool'}], 'source': '/tool',
            'version_argv': ['/usr/bin/python3', '/tool/step.py', 'version'],
            'writes': {'project/board.txt': 'design/project/board.txt'}, 'timeout_s': 20}
    spec.update(overrides)
    return spec


class StepDeclarationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        (Path(self.tmp.name) / 'tool').mkdir()
        self.tool = str(Path(self.tmp.name) / 'tool')

    def spec(self, **overrides):
        value = step(**overrides)
        value['mounts'] = [{'source': self.tool, 'at': '/tool'}] if 'mounts' not in overrides else overrides['mounts']
        return value

    def test_normalizes_defaults(self):
        spec = validate_step({k: v for k, v in self.spec().items() if k != 'timeout_s'})
        self.assertEqual(spec['adoptable_exits'], [0])
        self.assertEqual(spec['timeout_s'], 120)
        self.assertEqual(spec['artifacts'], [])
        self.assertIsNone(spec['backend_report'])

    def test_refuses_tools_and_writes_outside_declared_authority(self):
        cases = {
            'stage': dict(stage='optimizer'),
            'executable from design': dict(argv=['/work/design/tool.py']),
            'executable from candidate': dict(argv=['/candidate/tool'], version_argv=None),
            'reserved mount': dict(mounts=[{'source': '/usr', 'at': '/work/tool'}]),
            'relative mount': dict(mounts=[{'source': 'tool', 'at': '/tool'}]),
            'overlap': dict(mounts=[{'source': '/usr', 'at': '/tool'}, {'source': '/usr', 'at': '/tool/x'}]),
            'in-place rename': dict(writes={'project/board.txt': 'design/project/other.txt'}),
            'executor collision': dict(writes={'project/board.txt': 'stdout.log'}),
            'escape': dict(writes={'../board.txt': 'design/../board.txt'}),
            'empty writes': dict(writes={}),
            'undeclared backend artifact': dict(backend_report={'artifact': 'out/result.json', 'key': 'backend'}),
            'exit without zero': dict(adoptable_exits=[2]),
            'boolean exit': dict(adoptable_exits=[0, True]),
            'timeout': dict(timeout_s=0),
            'unknown field': dict(summary='writer says it worked'),
            'source outside mounts': dict(source='/usr/lib/python3'),
        }
        for label, overrides in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                validate_step(self.spec(**overrides))

    def test_missing_mount_source_refused_at_launch_but_not_for_retained_verification(self):
        spec = self.spec(mounts=[{'source': str(Path(self.tmp.name) / 'gone'), 'at': '/tool'}])
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            validate_step(spec)
        self.assertEqual(validate_step(spec, host=False)['mounts'][0]['at'], '/tool')

    def test_sandbox_profile_ignores_run_paths_but_not_isolation(self):
        a = sandbox_argv('/tmp/a/inputs', '/tmp/a/run', ['/usr/bin/true'], extra_ro=[(self.tool, '/tool')])
        b = sandbox_argv('/tmp/b/inputs', '/tmp/b/run', ['/usr/bin/false'], extra_ro=[(self.tmp.name, '/tool')])
        networked = sandbox_argv('/tmp/a/inputs', '/tmp/a/run', ['/usr/bin/true'], network=True,
                                 extra_ro=[(self.tool, '/tool')])
        self.assertEqual(sandbox_profile(a), sandbox_profile(b))
        self.assertNotEqual(sandbox_profile(a), sandbox_profile(networked))


def fake(inputs, outputs, status='completed'):
    return {'inputs': inputs, 'outputs': outputs, 'status': status}


class ApplicabilityTests(unittest.TestCase):
    def test_exact_whole_tree_relation_and_unbroken_chain(self):
        start = {'board': 'a', 'sch': 's'}
        placed = fake(start, {'board': 'b'})
        routed = fake({'board': 'b', 'sch': 's'}, {'board': 'c'})
        relations = applicability([placed, routed], {'board': 'c', 'sch': 's'})
        self.assertEqual([r['applicability'] for r in relations], ['superseded', 'applicable'])

    def test_writer_edit_after_step_makes_receipt_inapplicable(self):
        routed = fake({'board': 'b', 'sch': 's'}, {'board': 'c'})
        relation, = applicability([routed], {'board': 'c2', 'sch': 's'})
        self.assertEqual(relation, {'applicability': 'not_applicable', 'differences': ['board']})
        # Matching outputs are not enough: an unrelated later edit also breaks exactness.
        relation, = applicability([routed], {'board': 'c', 'sch': 's2'})
        self.assertEqual(relation, {'applicability': 'not_applicable', 'differences': ['sch']})

    def test_writer_edit_between_steps_breaks_chain(self):
        placed = fake({'board': 'a'}, {'board': 'b'})
        routed = fake({'board': 'b-edited'}, {'board': 'c'})
        relations = applicability([placed, routed], {'board': 'c'})
        self.assertEqual([r['applicability'] for r in relations], ['not_applicable', 'applicable'])

    def test_unadoptable_attempts_never_apply(self):
        for status in ('failed', 'timeout', 'cancelled', 'unavailable', 'refused'):
            relation, = applicability([fake({'board': 'a'}, {'board': 'b'}, status)], {'board': 'b'})
            self.assertEqual(relation, {'applicability': 'not_applicable', 'differences': ['status:' + status]})


class StepFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tool = self.root / 'tool'
        self.tool.mkdir()
        (self.tool / 'step.py').write_text(TOOL)
        self.design = self.root / 'design'
        (self.design / 'project').mkdir(parents=True)
        (self.design / 'project/board.txt').write_text('empty board')
        (self.design / 'project/rules.txt').write_text('rules')
        self.binding = {'implementation': 1, 'source_candidate_id': 'c' * 64, 'receipt_index': 1}
        self.count = 0

    def spec(self, mode='edit', *extra, **overrides):
        value = step(mode, *extra, **overrides)
        value['mounts'] = [{'source': str(self.tool), 'at': '/tool'}]
        return value

    def run_step(self, spec, **kwargs):
        self.count += 1
        path = self.root / 'work/receipts' / f'{self.count}-{spec["step"]}'
        return path, run_step(path, self.design, spec, {**self.binding, 'receipt_index': self.count}, **kwargs)


class StepExecutionTests(StepFixture):
    @toolchain_test
    def test_completed_step_records_coordinator_observed_identity_and_adopts_exactly(self):
        before = (self.design / 'project/board.txt').read_bytes()
        path, receipt = self.run_step(self.spec())
        self.assertEqual((self.design / 'project/board.txt').read_bytes(), before)
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(receipt['exit_code'], 0)
        self.assertEqual(receipt['tool']['executable']['sha256'], digest('/usr/bin/python3'))
        self.assertEqual(receipt['tool']['reported_version'],
                         {'status': 'reported', 'value': 'step-tool 1.2.3', 'reason': None})
        self.assertEqual(receipt['tool']['source_tree']['files'], 1)
        self.assertEqual(receipt['tool']['source_tree']['sha256'],
                         identity({'step.py': digest(self.tool / 'step.py')}))
        self.assertEqual(receipt['inputs'], {'project/board.txt': digest(self.design / 'project/board.txt'),
                                             'project/rules.txt': digest(self.design / 'project/rules.txt')})
        produced = path / 'run/design/project/board.txt'
        self.assertEqual(produced.read_text(), 'placed board empty board')
        self.assertEqual(receipt['outputs'], {'project/board.txt': digest(produced)})
        self.assertEqual(receipt['changed'], ['project/board.txt'])
        self.assertEqual(receipt['violations'], [])
        self.assertIn('--unshare-net', receipt['execution']['sandbox_argv'])
        self.assertEqual(receipt['sandbox_profile_sha256'],
                         identity(sandbox_profile(receipt['execution']['sandbox_argv'])))
        self.assertEqual(validate_receipt(path, binding=self.binding), receipt)
        adopt(path, receipt, self.design)
        self.assertEqual((self.design / 'project/board.txt').read_text(), 'placed board empty board')

    @toolchain_test
    def test_tool_without_version_string_keeps_executable_hash_and_unknown_identity(self):
        for label, overrides in (('undeclared', {'version_argv': None}),
                                 ('silent', {'version_argv': ['/usr/bin/python3', '/tool/step.py', 'silent-version']})):
            with self.subTest(label):
                path, receipt = self.run_step(self.spec(**overrides))
                self.assertEqual(receipt['tool']['reported_version']['status'], 'unknown')
                self.assertIsNone(receipt['tool']['reported_version']['value'])
                self.assertEqual(receipt['tool']['executable']['sha256'], digest('/usr/bin/python3'))
                self.assertEqual(receipt['status'], 'completed')
                validate_receipt(path)

    @toolchain_test
    def test_exit_semantics_partial_failed_crash_and_scope_refusals(self):
        cases = [
            (self.spec('edit', '2', adoptable_exits=[0, 2]), 'partial', []),
            (self.spec('edit', '3', adoptable_exits=[0, 2]), 'failed', []),
            (self.spec('crash'), 'failed', []),
            (self.spec('outside'), 'refused', ['write_outside_scope:project/rules.txt']),
            (self.spec('add'), 'refused', ['inventory_changed:project/extra.txt']),
            (self.spec('edit', argv=['/tool/missing']), 'failed', ['tool_changed']),
        ]
        for spec, status, violations in cases:
            with self.subTest(spec['argv']):
                original = stage_receipts.file_tree(self.design)
                path, receipt = self.run_step(spec)
                self.assertEqual(receipt['status'], status)
                for violation in violations:
                    self.assertIn(violation, receipt['violations'])
                self.assertEqual(validate_receipt(path)['status'], status)
                if status == 'partial':
                    continue
                with self.assertRaisesRegex(ValueError, 'no output adopted or substituted'):
                    adopt(path, receipt, self.design)
                self.assertEqual(stage_receipts.file_tree(self.design), original)

    @toolchain_test
    def test_out_of_place_output_and_verbatim_backend_identity(self):
        spec = self.spec('out', '2', writes={'project/board.txt': 'out/board.txt'}, adoptable_exits=[0, 2],
                         artifacts=['out/result.json'],
                         backend_report={'artifact': 'out/result.json', 'key': 'backend'})
        path, receipt = self.run_step(spec)
        self.assertEqual(receipt['status'], 'partial')
        self.assertEqual(receipt['tool']['backend'],
                         {'status': 'reported', 'value': {'name': 'test', 'version': None, 'build': 'b1'},
                          'reason': None})
        self.assertEqual(set(receipt['artifacts']), {'out/result.json'})
        validate_receipt(path)
        missing = self.spec('edit', writes={'project/board.txt': 'out/board.txt'}, artifacts=['out/result.json'],
                            backend_report={'artifact': 'out/result.json', 'key': 'backend'})
        path, receipt = self.run_step(missing)
        self.assertEqual(receipt['tool']['backend']['status'], 'unknown')
        self.assertEqual(receipt['artifacts'], {'out/result.json': None})
        self.assertEqual(receipt['status'], 'refused')
        self.assertIn('missing_output:project/board.txt', receipt['violations'])

    @toolchain_test
    def test_timeout_and_cancellation_bind_partial_receipts(self):
        path, receipt = self.run_step(self.spec('sleep', timeout_s=1, version_argv=None))
        self.assertEqual(receipt['status'], 'timeout')
        validate_receipt(path)
        cancellation = Cancellation()
        timer = threading.Timer(1, cancellation.cancel)
        timer.start()
        self.addCleanup(timer.cancel)
        with self.assertRaises(KeyboardInterrupt):
            self.run_step(self.spec('sleep', version_argv=None), cancellation=cancellation)
        path = self.root / 'work/receipts' / f'{self.count}-test-helper'
        receipt = validate_receipt(path)
        self.assertEqual(receipt['status'], 'cancelled')
        self.assertEqual(receipt['execution']['execution'], 'interrupted')
        with self.assertRaisesRegex(ValueError, 'no output adopted'):
            adopt(path, receipt, self.design)

    @toolchain_test
    def test_tampered_forged_and_foreign_receipts_refused(self):
        path, receipt = self.run_step(self.spec())

        def forged(change):
            value = json.loads(json.dumps(receipt))
            change(value)
            value['identity'] = identity({k: v for k, v in value.items() if k != 'identity'})
            write_json(path / 'receipt.json', value)

        def restore():
            write_json(path / 'receipt.json', receipt)

        attacks = {
            'claimed output': (lambda r: r['outputs'].update({'project/board.txt': '0' * 64}), 'outputs changed'),
            'filled version': (lambda r: r['tool']['reported_version'].update(value='step-tool 9'), 'reported version'),
            'status': (lambda r: r.update(status='partial'), 'status mismatch'),
            'exit code': (lambda r: r.update(exit_code=2), 'exit code'),
            'inputs': (lambda r: r['inputs'].update({'project/rules.txt': '0' * 64}), 'inputs changed'),
            'network': (lambda r: r['execution']['sandbox_argv'].remove('--unshare-net'), 'execution record'),
            'foreign tool path': (lambda r: r['tool']['executable'].update(path='/tool/other'), 'tool identity'),
            'violations': (lambda r: r.update(violations=[]) or r.update(violations=['tool_changed']), 'violations'),
        }
        for label, (change, message) in attacks.items():
            with self.subTest(label):
                forged(change)
                with self.assertRaisesRegex(ValueError, message):
                    validate_receipt(path)
                restore()
        # Rewriting both retained execution copies still cannot remove network isolation.
        record = read_json(path / 'run/execution.json')
        write_json(path / 'run/execution.json', {**record, 'sandbox_argv': [
            a for a in record['sandbox_argv'] if a != '--unshare-net']})
        forged(lambda r: r['execution'].update(sandbox_argv=[a for a in r['execution']['sandbox_argv']
                                                             if a != '--unshare-net']))
        with self.assertRaisesRegex(ValueError, 'network isolation'):
            validate_receipt(path)
        write_json(path / 'run/execution.json', record)
        restore()
        value = dict(receipt, summary='writer: routed and passed')
        write_json(path / 'receipt.json', value)
        with self.assertRaisesRegex(ValueError, 'malformed'):
            validate_receipt(path)
        restore()
        unsigned = dict(receipt, status='failed')
        write_json(path / 'receipt.json', unsigned)
        with self.assertRaisesRegex(ValueError, 'identity'):
            validate_receipt(path)
        restore()
        with self.assertRaisesRegex(ValueError, 'foreign'):
            validate_receipt(path, binding={**self.binding, 'implementation': 2})
        produced = path / 'run/design/project/board.txt'
        produced.write_text('edited after the step')
        with self.assertRaisesRegex(ValueError, 'outputs changed'):
            validate_receipt(path)
        produced.write_text('placed board empty board')
        (path / 'run/stdout.log').write_text('injected')
        with self.assertRaisesRegex(ValueError, 'execution artifacts changed'):
            validate_receipt(path)

    @toolchain_test
    def test_index_applicability_and_captured_proposal(self):
        work = self.root / 'work'
        path, receipt = self.run_step(self.spec())
        adopt(path, receipt, self.design)
        anchor = bind_implementation(work, self.design, 1, 'c' * 64)
        index = read_json(work / 'stage-receipts.json')
        self.assertEqual(index['receipts'][0]['applicability'], 'applicable')
        delivered = stage_receipts.file_tree(self.design)[0]
        verify_implementation(work, anchor, 1, 'c' * 64, captured=delivered, steps=[self.spec()])
        changed = {**delivered, 'project/board.txt': '0' * 64}
        with self.assertRaisesRegex(ValueError, 'claimed applicable stage receipt output differs from captured'):
            verify_implementation(work, anchor, 1, 'c' * 64, captured=changed)
        with self.assertRaisesRegex(ValueError, 'differs from the campaign declaration'):
            verify_implementation(work, anchor, 1, 'c' * 64, steps=[self.spec('crash')])
        with self.assertRaisesRegex(ValueError, 'foreign'):
            verify_implementation(work, anchor, 2, 'c' * 64)
        with self.assertRaisesRegex(ValueError, 'changed or missing'):
            verify_implementation(work, '0' * 64, 1, 'c' * 64)
        # A writer edit after the step leaves the receipt retained but inapplicable.
        (self.design / 'project/board.txt').write_text('writer edit')
        rebound = build_index(work, self.design, 1, 'c' * 64)
        self.assertEqual(rebound['receipts'][0]['applicability'], 'not_applicable')
        self.assertEqual(rebound['receipts'][0]['differences'], ['project/board.txt'])
        stray = work / 'receipts/2-forged'
        stray.mkdir()
        write_json(stray / 'receipt.json', receipt)
        with self.assertRaisesRegex(ValueError, 'foreign|malformed|changed'):
            build_index(work, self.design, 1, 'c' * 64)


SENTINEL = 'OWNED-HOST-SENTINEL-NOT-A-SECRET'


def everything(path):
    """Every regular file's bytes under path, never following a link."""
    found = {}
    for current, dirs, names in os.walk(path, followlinks=False):
        for name in names:
            item = Path(current) / name
            if not item.is_symlink() and item.is_file():
                found[item.relative_to(path).as_posix()] = item.read_bytes()
    return found


class CoordinatorBoundaryTests(StepFixture):
    """A step tool may plant links and special files in /work; the coordinator never follows them."""

    def setUp(self):
        super().setUp()
        # An owned host-only directory the sandbox cannot see; only the coordinator could reach it.
        self.host = self.root / 'host-only'
        (self.host / 'nested').mkdir(parents=True)
        (self.host / 'sentinel').write_text(SENTINEL)
        (self.host / 'nested/board.txt').write_text('routed board')
        (self.host / 'result.json').write_text(json.dumps({'backend': {'name': 'host'}}))
        self.host_before = everything(self.host)
        self.sentinel_sha = digest(self.host / 'sentinel')

    def plant(self, plan, **overrides):
        return self.spec('plant', json.dumps(plan), **{'version_argv': None, **overrides})

    def assert_host_untouched(self, path, receipt):
        self.assertEqual(everything(self.host), self.host_before)
        self.assertNotIn(self.sentinel_sha, json.dumps(receipt))
        self.assertNotIn(SENTINEL.encode(), b''.join(everything(path).values()))
        self.assertNotIn(SENTINEL, (self.design / 'project/board.txt').read_text())

    def assert_refused_unadoptable(self, path, receipt, violation):
        self.assertEqual(receipt['status'], 'refused')
        self.assertIn(violation, receipt['violations'])
        self.assertEqual(validate_receipt(path, binding={**self.binding, 'receipt_index': self.count}), receipt)
        before = stage_receipts.file_tree(self.design)
        with self.assertRaisesRegex(ValueError, 'no output adopted or substituted'):
            adopt(path, receipt, self.design)
        self.assertEqual(stage_receipts.file_tree(self.design), before)

    @toolchain_test
    def test_ancestor_link_to_owned_host_directory_is_never_followed(self):
        # The reviewer's reproduction: write source, artifacts and backend report all behind /work/link.
        spec = self.plant({'links': {'link': str(self.host)}},
                          writes={'project/board.txt': 'link/sentinel'},
                          artifacts=['link', 'link/sentinel', 'link/result.json'],
                          backend_report={'artifact': 'link/result.json', 'key': 'backend'})
        path, receipt = self.run_step(spec)
        self.assertEqual(receipt['exit_code'], 0)
        self.assertEqual(receipt['outputs'], {'project/board.txt': None})
        self.assertEqual(receipt['artifacts'], {'link': None, 'link/result.json': None, 'link/sentinel': None})
        self.assertEqual(receipt['tool']['backend'], {'status': 'unknown', 'value': None,
                                                      'reason': 'backend report artifact is a link or special file'})
        self.assertNotIn('missing_output:project/board.txt', receipt['violations'])
        self.assert_refused_unadoptable(path, receipt, 'unsafe_output:project/board.txt')
        self.assert_host_untouched(path, receipt)

    @toolchain_test
    def test_leaf_links_to_candidate_retained_or_missing_files_are_not_outputs(self):
        targets = {
            'relative to design copy': '../design/project/board.txt',
            'absolute retained input': None,
            'sandbox candidate path': '/candidate/project/board.txt',
            'host file': str(self.host / 'sentinel'),
            'broken': str(self.root / 'no-such-owned-target'),
        }
        for label, target in targets.items():
            with self.subTest(label):
                if target is None:
                    target = str(self.root / 'work/receipts' / f'{self.count + 1}-test-helper/inputs/project/board.txt')
                spec = self.plant({'links': {'out/board.txt': target}}, writes={'project/board.txt': 'out/board.txt'},
                                  artifacts=['out/board.txt'])
                path, receipt = self.run_step(spec)
                self.assertEqual(receipt['outputs'], {'project/board.txt': None})
                self.assertEqual(receipt['artifacts'], {'out/board.txt': None})
                self.assert_refused_unadoptable(path, receipt, 'unsafe_output:project/board.txt')
                self.assert_host_untouched(path, receipt)

    @toolchain_test
    def test_nested_output_adopts_while_links_and_specials_inside_artifacts_stay_unfollowed(self):
        spec = self.plant({'files': {'out/nested/deep/board.txt': 'routed nested board',
                                     'out/result.json': json.dumps({'backend': {'name': 'kept'}})},
                           'links': {'out/report/host-file': str(self.host / 'sentinel'),
                                     'out/report/host-dir': str(self.host),
                                     'out/report/relative': '../nested/deep/board.txt',
                                     'out/report/broken': 'missing-target'},
                           'fifos': ['out/report/pipe']},
                          writes={'project/board.txt': 'out/nested/deep/board.txt'},
                          artifacts=['out/nested', 'out/report', 'out/result.json'],
                          backend_report={'artifact': 'out/result.json', 'key': 'backend'})
        path, receipt = self.run_step(spec)
        run = path / 'run'
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(receipt['violations'], [])
        self.assertEqual(receipt['outputs'], {'project/board.txt': digest(run / 'out/nested/deep/board.txt')})
        self.assertEqual(receipt['artifacts'], {
            'out/nested/deep/board.txt': digest(run / 'out/nested/deep/board.txt'),
            'out/report/broken': None, 'out/report/host-dir': None, 'out/report/host-file': None,
            'out/report/pipe': None, 'out/report/relative': None,
            'out/result.json': digest(run / 'out/result.json')})
        self.assertEqual(receipt['tool']['backend'], {'status': 'reported', 'value': {'name': 'kept'}, 'reason': None})
        self.assertEqual(validate_receipt(path), receipt)
        adopt(path, receipt, self.design)
        self.assertEqual((self.design / 'project/board.txt').read_text(), 'routed nested board')
        self.assert_host_untouched(path, receipt)

    @toolchain_test
    def test_links_in_the_design_copy_are_inventory_violations_not_followed(self):
        spec = self.plant({'remove': ['design/project/board.txt'],
                           'links': {'design/project/board.txt': str(self.host / 'sentinel'),
                                     'design/project/host': str(self.host)}})
        path, receipt = self.run_step(spec)
        self.assertIn('inventory_changed:project/host', receipt['violations'])
        self.assertEqual(receipt['outputs'], {'project/board.txt': None})
        self.assert_refused_unadoptable(path, receipt, 'unsafe_output:project/board.txt')
        self.assert_host_untouched(path, receipt)

    @toolchain_test
    def test_planted_executor_record_links_refuse_the_receipt_and_are_never_read(self):
        # Every record name, and the old fixed temporary name, links to the host-only sentinel.
        sentinel = str(self.host / 'sentinel')
        names = ('stdout.log', 'stderr.log', 'console.log', 'execution.json', 'execution.json.tmp')
        plan = {'files': {'design/project/board.txt': 'placed'}, 'links': {name: sentinel for name in names}}
        version = {'links': {'stdout.log': sentinel}}
        spec = self.plant(plan, version_argv=['/usr/bin/python3', '/tool/step.py', 'plant', json.dumps(version)])
        path, receipt = self.run_step(spec)
        self.assertEqual(receipt['execution']['exit_code'], 0)
        self.assertEqual([v for v in receipt['violations'] if v.startswith('executor_record_')],
                         ['executor_record_unsafe:version/stdout.log',
                          *(f'executor_record_unsafe:run/{name}' for name in
                            ('execution.json', 'stdout.log', 'stderr.log', 'console.log'))])
        # The executor's own captured output, not anything behind the planted link.
        self.assertEqual(receipt['tool']['reported_version'],
                         {'status': 'unknown', 'value': None, 'reason': 'version probe printed no version'})
        for name, moved in receipt['execution']['displaced_records'].items():
            self.assertEqual(moved['kind'], 'link')
            self.assertEqual(os.readlink(path / 'run' / moved['moved_to']), sentinel)
        self.assertEqual(os.readlink(path / 'run/execution.json.tmp'), sentinel)
        self.assert_refused_unadoptable(path, receipt, 'executor_record_unsafe:run/stdout.log')
        self.assert_host_untouched(path, receipt)

    @toolchain_test
    def test_unwritable_work_keeps_a_refused_receipt_without_trusting_planted_records(self):
        forged = {'execution': 'ok', 'exit_code': 0, 'argv': ['forged']}
        spec = self.plant({'files': {'design/project/board.txt': 'placed', 'execution.json': json.dumps(forged)},
                           'chmod': {'.': 0o500}})
        path = self.root / 'work/receipts' / f'{self.count + 1}-test-helper'
        self.addCleanup(lambda: (path / 'run').exists() and os.chmod(path / 'run', 0o700))
        # The executor itself cannot write its records; that error still propagates.
        with self.assertRaises(PermissionError):
            self.run_step(spec)
        receipt = validate_receipt(path)
        self.assertIsNone(receipt['execution'])
        self.assertIsNone(receipt['exit_code'])
        self.assertEqual(receipt['outputs'], {'project/board.txt': digest(path / 'run/design/project/board.txt')})
        self.assert_refused_unadoptable(path, receipt, 'executor_record_missing:run')
        anchor = bind_implementation(self.root / 'work', self.design, 1, 'c' * 64)
        verify_implementation(self.root / 'work', anchor, 1, 'c' * 64)

    def test_records_on_disk_after_an_interrupt_are_trusted_only_as_cancellation(self):
        real = stage_receipts.execute
        argv = ['/usr/bin/python3', '/tool/step.py', 'edit']

        def interrupted_before_record(inputs, work, run_argv, **kwargs):
            # As if the coordinator were interrupted before the executor wrote its own record,
            # leaving whatever the tool had planted in /work.
            (Path(work) / 'home').mkdir(parents=True)
            write_json(Path(work) / 'execution.json', planted)
            raise KeyboardInterrupt

        cases = {'forged success': ({'execution': 'ok', 'exit_code': 0, 'argv': argv}, 'refused'),
                 'malformed interrupt': ({'execution': 'interrupted', 'argv': argv, 'sandbox_argv': 'x'}, 'refused')}
        for label, (planted, status) in cases.items():
            with self.subTest(label), patch('agent_ee.stage_receipts.execute', side_effect=interrupted_before_record):
                with self.assertRaises(KeyboardInterrupt):
                    self.run_step(self.spec(version_argv=None))
                path = self.root / 'work/receipts' / f'{self.count}-test-helper'
                receipt = validate_receipt(path)
                self.assertIsNone(receipt['execution'])
                self.assertEqual(receipt['status'], status)
                self.assertIn('executor_record_missing:run', receipt['violations'])
        self.assertIs(stage_receipts.execute, real)

    @toolchain_test
    def test_malformed_backend_reports_keep_the_observed_process_receipt(self):
        prefix = 'backend report artifact '
        cases = {
            'nan': (b'{"backend": NaN}', 'has a non-finite number'),
            'infinity': (b'{"backend": {"v": Infinity}}', 'has a non-finite number'),
            'negative infinity': (b'{"backend": -Infinity}', 'has a non-finite number'),
            'float overflow': (b'{"backend": 1e400}', 'has a non-finite number'),
            'duplicate key': (b'{"backend": 1, "backend": 2}', 'has duplicate keys'),
            'recursion depth': (b'{"backend": ' + b'[' * 100000 + b']' * 100000 + b'}', 'nesting exceeds 64'),
            'over the nesting bound': (b'{"backend": ' + b'[' * 64 + b']' * 64 + b'}', 'nesting exceeds 64'),
            'invalid utf-8': (b'{"backend": "\xff"}', 'is not UTF-8'),
            'truncated': (b'{"backend": {"name": "x"', 'is not JSON'),
            'oversized integer': (b'{"backend": ' + b'9' * 5000 + b'}', 'has an out-of-range number'),
            'oversized': (b' ' * stage_receipts.REPORT_LIMIT + b'{}', 'too large'),
        }
        for label, (data, reason) in cases.items():
            with self.subTest(label):
                (self.tool / 'report.bin').write_bytes(data)
                spec = self.plant({'files': {'design/project/board.txt': 'placed'},
                                   'copy': {'out/result.json': '/tool/report.bin'}},
                                  artifacts=['out/result.json'],
                                  backend_report={'artifact': 'out/result.json', 'key': 'backend'})
                path, receipt = self.run_step(spec)
                self.assertEqual(receipt['tool']['backend'], {'status': 'unknown', 'value': None,
                                                              'reason': prefix + reason})
                # The process is still recorded; the backend is honestly unknown, not guessed.
                self.assertEqual((receipt['status'], receipt['exit_code'], receipt['violations']), ('completed', 0, []))
                self.assertEqual(receipt['artifacts'], {'out/result.json': digest(path / 'run/out/result.json')})
                self.assertEqual(stage_receipts._strict_json((path / 'receipt.json').read_bytes()), receipt)
                self.assertEqual(validate_receipt(path), receipt)
        # Exactly at the nesting bound the value is copied verbatim.
        nested = json.loads('[' * 63 + ']' * 63)
        (self.tool / 'report.bin').write_text(json.dumps({'backend': nested}))
        path, receipt = self.run_step(self.plant({'copy': {'out/result.json': '/tool/report.bin'}},
                                                 writes={'project/board.txt': 'out/result.json'},
                                                 artifacts=['out/result.json'],
                                                 backend_report={'artifact': 'out/result.json', 'key': 'backend'}))
        self.assertEqual(receipt['tool']['backend'], {'status': 'reported', 'value': nested, 'reason': None})
        validate_receipt(path)

    @toolchain_test
    def test_partial_malformed_report_on_failure_timeout_and_cancellation(self):
        (self.tool / 'report.bin').write_bytes(b'{"backend": {"name": "x", "version": "1.')
        declared = dict(artifacts=['out/result.json'], backend_report={'artifact': 'out/result.json', 'key': 'backend'})
        unknown = {'status': 'unknown', 'value': None, 'reason': 'backend report artifact is not JSON'}
        path, receipt = self.run_step(self.plant({'copy': {'out/result.json': '/tool/report.bin'}, 'exit': 3},
                                                 **declared))
        self.assertEqual((receipt['status'], receipt['tool']['backend']), ('failed', unknown))
        validate_receipt(path)
        path, receipt = self.run_step(self.plant({'copy': {'out/result.json': '/tool/report.bin'}, 'sleep': 30},
                                                 timeout_s=1, **declared))
        self.assertEqual((receipt['status'], receipt['tool']['backend']), ('timeout', unknown))
        validate_receipt(path)
        cancellation = Cancellation()
        timer = threading.Timer(1, cancellation.cancel)
        timer.start()
        self.addCleanup(timer.cancel)
        with self.assertRaises(KeyboardInterrupt):
            self.run_step(self.plant({'copy': {'out/result.json': '/tool/report.bin'}, 'sleep': 30}, **declared),
                          cancellation=cancellation)
        path = self.root / 'work/receipts' / f'{self.count}-test-helper'
        receipt = validate_receipt(path)
        self.assertEqual((receipt['status'], receipt['tool']['backend']), ('cancelled', unknown))
        with self.assertRaisesRegex(ValueError, 'no output adopted'):
            adopt(path, receipt, self.design)
        # Every executed step left a receipt, so the index over all of them still verifies.
        anchor = bind_implementation(self.root / 'work', self.design, 1, 'c' * 64)
        index = verify_implementation(self.root / 'work', anchor, 1, 'c' * 64)
        self.assertEqual([link['applicability'] for link in index['receipts']], ['not_applicable'] * 3)

    @toolchain_test
    def test_adoption_never_follows_a_replaced_output_or_ancestor(self):
        spec = self.plant({'files': {'out/nested/board.txt': 'routed board'}},
                          writes={'project/board.txt': 'out/nested/board.txt'})
        path, receipt = self.run_step(spec)
        self.assertEqual(receipt['status'], 'completed')
        run = path / 'run'
        original = stage_receipts.file_tree(self.design)
        # Same bytes behind a link: a hash comparison alone would accept either replacement.
        self.assertEqual(digest(self.host / 'nested/board.txt'), receipt['outputs']['project/board.txt'])
        os.rename(run / 'out', run / 'out-real')
        os.symlink(self.host, run / 'out')
        leaf = run / 'out-real/nested/board.txt'
        replacements = {'ancestor': lambda: None,
                        'leaf': lambda: (os.unlink(run / 'out'), os.rename(run / 'out-real', run / 'out'),
                                         os.rename(run / 'out/nested/board.txt', run / 'board.real'),
                                         os.symlink(self.host / 'nested/board.txt', run / 'out/nested/board.txt'))}
        for label, replace in replacements.items():
            with self.subTest(label):
                replace()
                with self.assertRaisesRegex(ValueError, 'outputs changed'):
                    adopt(path, receipt, self.design)
                # Even when replaced after revalidation, the copied bytes are read without following links.
                with patch('agent_ee.stage_receipts.validate_receipt', return_value=receipt), \
                        self.assertRaisesRegex(ValueError, 'output changed before adoption'):
                    adopt(path, receipt, self.design)
                self.assertEqual(stage_receipts.file_tree(self.design), original)
        self.assertFalse(leaf.exists())
        self.assert_host_untouched(path, receipt)

    @toolchain_test
    def test_adoption_requires_the_retained_coordinator_receipt(self):
        path, receipt = self.run_step(self.spec('edit', '3', adoptable_exits=[0, 2]))
        self.assertEqual(receipt['status'], 'failed')
        original = stage_receipts.file_tree(self.design)
        blessed = dict(receipt, status='completed')
        blessed['identity'] = identity({k: v for k, v in blessed.items() if k != 'identity'})
        with self.assertRaisesRegex(ValueError, 'differs from the retained coordinator receipt'):
            adopt(path, blessed, self.design)
        self.assertEqual(stage_receipts.file_tree(self.design), original)

    def test_tree_scan_records_links_specials_and_excess_depth_without_entering_them(self):
        root = self.root / 'tree'
        (root / 'a').mkdir(parents=True)
        (root / 'a/file').write_text('x')
        os.symlink(self.host, root / 'dir-link')
        os.symlink(self.host / 'sentinel', root / 'a/file-link')
        os.mkfifo(root / 'a/pipe')
        deep = root.joinpath(*['d'] * (stage_receipts.TREE_DEPTH + 1))
        deep.mkdir(parents=True)
        (deep / 'f').write_text('deep')
        files, other = stage_receipts.file_tree(root)
        self.assertEqual(files, {'a/file': digest(root / 'a/file')})
        self.assertEqual(other, sorted(['a/file-link', 'a/pipe', 'dir-link',
                                        '/'.join(['d'] * stage_receipts.TREE_DEPTH)]))
        with self.assertRaisesRegex(ValueError, 'not a directory'):
            stage_receipts.file_tree(root / 'dir-link')


class CampaignStageReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = self.root / 'source'
        source.mkdir()
        for name in ('board', 'schematic', 'requirements', 'targets', 'reference', 'model'):
            (source / name).write_text(name)
        write_json(source / 'design.json', {'value': 15})
        (source / 'solver.py').write_text('from pathlib import Path\nimport json\nvalue=json.loads(Path("/candidate/design/design.json").read_text())["value"]\nPath("raw.json").write_text(json.dumps({"value":value}))\n')
        (source / 'adapter.py').write_text('''import json, sys
from pathlib import Path
request = json.loads(Path(sys.argv[1]).read_text())
value = json.loads(Path("/work/tool/raw.json").read_text())["value"]
report = {"schema": "agent-ee.measurements/1", "candidate_id": request["candidate_id"],
 "files": request["files"], "tool": "test-protocol", "version": "1", "domain": "electrical",
 "artifacts": ["tool/raw.json"], "metrics": {"output": {"value": value, "unit": "V", "condition": "load"}}}
Path(sys.argv[2]).write_text(json.dumps(report))
''')
        self.tool = self.root / 'tool'
        self.tool.mkdir()
        (self.tool / 'fix.py').write_text('import json, sys\nfrom pathlib import Path\n'
            'Path("/work/design/design/design.json").write_text(json.dumps({"value": 5}))\n'
            'if len(sys.argv) > 2:\n    Path("/work/result.json").write_text(sys.argv[2])\n'
            'raise SystemExit(int(sys.argv[1]))\n')
        definition = {'harness': 'codex', 'model': 'test', 'effort': 'high', 'timeout_s': 10}
        self.profile = {'schema': 'agent-ee.profile/1', 'name': 'protocol-test',
            'reviewers': [{**definition, 'role': 'circuit'}, {**definition, 'role': 'physical'}],
            'adjudicator': definition}
        scope = {'schema': 'agent-ee.scope/2', 'name': 'protocol', 'scope': 'stage receipt integration test',
            'board': 'design/board', 'schematic': 'design/schematic', 'mutable_inputs': ['design/design.json'],
            'inputs': [{'path': str(source), 'at': 'design'}], 'review_profile': self.profile,
            'checks': [{'id': 'response', 'kind': 'measurements', 'params': {
                'adapter': 'design/adapter.py', 'tool': {'argv': ['/usr/bin/python3', '/candidate/design/solver.py'],
                'sha256': digest('/usr/bin/python3'), 'domain': 'electrical', 'inputs': ['design/solver.py']},
                'calibration_inputs': ['design/model'],
                'limits': {'output': {'unit': 'V', 'condition': 'load', 'min': 0, 'max': 10}}}}],
            'baseline': {'requirements': [{'id': 'R1', 'statement': 'Output in [0,10] V at load',
                'source': {'path': 'design/requirements', 'locator': 'R1'}, 'checks': ['response']}],
                'targets': ['design/targets'], 'reference_architectures': ['design/reference']}}
        write_json(self.root / 'scope.json', scope)
        self.config = {'schema': 'agent-ee.campaign/1', 'scope': 'scope.json', 'review_profile': self.profile,
                       'implementation': definition, 'max_iterations': 3, 'max_seconds': 60}
        self.writer_prompts = []
        self.addCleanup(patch.stopall)
        patch('agent_ee.reviewers.dispatch', side_effect=self.agent).start()
        patch('agent_ee.cli.runtime_files', return_value={name: {'path': '/usr/bin/true', 'sha256': digest('/usr/bin/true')}
            for name in ('codex', 'codex_tool_host', 'bwrap', '/usr/bin/python3')}).start()

    def agent(self, run, label, definition, prompt, schema, timeout, *, cancellation=None, executable=None):
        run = Path(run)
        if label == 'implementation':
            self.writer_prompts.append(prompt)
            # The writer's statement and its scratch files are not evidence; only the coordinator step edits here.
            forged = run / 'review-work/implementation/receipts/1-fix-helper/receipt.json'
            write_json(forged, {'schema': 'agent-ee.stage-receipt/1', 'status': 'completed'})
            return {'execution': 'ok'}, {'summary': 'Ran the helper; it passed'}
        packet = read_json(run / 'packet.json')
        ident = {k: packet[k] for k in ('candidate_id', 'run_id')}
        if label == 'adjudicator':
            checks = [read_json(p) for p in (run / 'checks').glob('*.json')]
            decisions = [{'finding_id': 'check/' + c['id'], 'disposition': 'accepted', 'reason': 'R1 failed',
                'evidence': [{'path': 'checks/' + c['id'] + '.json', 'locator': 'outcome'}]}
                for c in checks if c['outcome'] == 'defect']
            return {'execution': 'ok'}, {'schema': 'agent-ee.decisions/1', **ident, 'decisions': decisions}
        return {'execution': 'ok'}, {'schema': 'agent-ee.review/1', **ident,
            'packet_sha256': packet['packet_sha256'], 'role': label,
            'coverage': ['protocol'], 'limitations': ['scripted test response'], 'findings': []}

    def step(self, code=0, **overrides):
        return {'schema': 'agent-ee.stage-step/1', 'stage': 'placement', 'step': 'fix-helper',
                'argv': ['/usr/bin/python3', '/tool/fix.py', str(code)],
                'mounts': [{'source': str(self.tool), 'at': '/tool'}], 'source': '/tool',
                'writes': {'design/design.json': 'design/design/design.json'}, 'timeout_s': 20, **overrides}

    def campaign(self, steps):
        write_json(self.root / 'campaign.json', {**self.config, 'implementation_steps': steps})
        with contextlib.redirect_stdout(io.StringIO()):
            status = run_campaign(self.root / 'campaign.json', self.root / 'campaign')
        return status, read_json(self.root / 'campaign/state.json')

    def staged_scope(self):
        scope = read_json(self.root / 'scope.json')
        scope['board'] = 'design/board.kicad_pcb'
        scope['checks'][0]['stage'] = 'placement'
        scope['stages'] = {'schema': 'agent-ee.stages/1', 'order': ['placement'],
            'subjects': {'placement': {'path': 'design/board.kicad_pcb', 'kind': 'footprints',
                                       'references': ['R1', 'C1']}}, 'finding_retry_limit': 3}
        fixture = Path(__file__).resolve().parents[1] / 'fixtures/stages/placed.kicad_pcb'
        (self.root / 'source/board').unlink()
        (self.root / 'source/board.kicad_pcb').write_bytes(fixture.read_bytes())
        write_json(self.root / 'scope.json', scope)

    @toolchain_test
    def test_staged_writer_edit_to_step_output_is_named_refusal_before_step(self):
        self.staged_scope()

        def editing_writer(run, label, *args, **kwargs):
            if label == 'implementation':
                write_json(Path(run) / 'review-work/implementation/design/design/design.json', {'value': 7})
            return self.agent(run, label, *args, **kwargs)

        with patch('agent_ee.reviewers.dispatch', side_effect=editing_writer):
            status, state = self.campaign([self.step()])
        self.assertEqual(status, 1, state)
        self.assertEqual(state['state'], 'incomplete')
        self.assertIn('error', state, state)
        self.assertIn('writer_step_output_edit', state['error'])
        self.assertIn('design/design.json', state['error'])
        self.assertIn('fix-helper', state['error'])
        work = self.root / 'campaign/implementations/1'
        self.assertFalse((work / 'receipts').exists())
        self.assertFalse((work / 'proposal.scope.json').exists())
        self.assertFalse((self.root / 'campaign/runs/2').exists())
        self.assertEqual(read_json(self.root / 'source/design.json'), {'value': 15})
        self.assertEqual(verify_campaign(self.root / 'campaign')['state'], 'incomplete')

    @toolchain_test
    def test_staged_retained_prompt_declares_steps_and_noop_keeps_exact_step_outputs(self):
        self.staged_scope()
        first = self.step(argv=['/usr/bin/python3', '-c',
            'from pathlib import Path; Path("/work/design/design/design.json").write_text(\'{"value": 6}\')'])
        status, state = self.campaign([first, self.step(step='finish-helper')])
        self.assertEqual(status, 0, state)
        work = self.root / 'campaign/implementations/1'
        prompt = (work / 'implementation-prompt.txt').read_text()
        self.assertEqual(prompt, self.writer_prompts[0])
        context = json.loads(prompt.split('\nDeclared implementation steps:\n', 1)[1])
        self.assertEqual(context['implementation_steps'], [
            {'stage': 'placement', 'step': name, 'writes': {'design/design.json': 'design/design/design.json'},
             'scheduled': True} for name in ('fix-helper', 'finish-helper')])
        self.assertEqual(context['step_owned_outputs'], ['design/design.json'])
        self.assertIn('Declared steps own their stages', prompt)
        self.assertIn('must not perform a stage a declared step will run', prompt)
        self.assertIn('no-op summary', prompt)
        index = read_json(work / 'stage-receipts.json')
        self.assertEqual([r['applicability'] for r in index['receipts']], ['superseded', 'applicable'])
        receipt = read_json(work / index['receipts'][-1]['path'])
        proposal = read_json(self.root / 'campaign/runs/2/candidate.json')
        self.assertEqual(proposal['files'], index['delivered'])
        self.assertEqual(proposal['files']['design/design.json'], receipt['outputs']['design/design.json'])
        self.assertEqual(verify_campaign(self.root / 'campaign')['state'], 'criteria_satisfied')

    @toolchain_test
    def test_staged_writer_board_edit_refused_for_out_of_place_routing_output(self):
        self.staged_scope()
        scope = read_json(self.root / 'scope.json')
        scope['checks'][0]['stage'] = 'routing'
        scope['stages']['order'] = ['routing']
        scope['stages']['subjects']['routing'] = scope['stages']['subjects'].pop('placement')
        scope['mutable_inputs'].append('design/board.kicad_pcb')
        write_json(self.root / 'scope.json', scope)

        def routing_writer(run, label, *args, **kwargs):
            if label == 'implementation':
                board = Path(run) / 'review-work/implementation/design/design/board.kicad_pcb'
                board.write_bytes(board.read_bytes() + b'\n')
            return self.agent(run, label, *args, **kwargs)

        with patch('agent_ee.reviewers.dispatch', side_effect=routing_writer):
            status, state = self.campaign([self.step(stage='routing', step='route-helper', writes={
                'design/design.json': 'design/design/design.json',
                'design/board.kicad_pcb': 'out/candidate/board.kicad_pcb'})])
        self.assertEqual(status, 1, state)
        self.assertIn("writer_step_output_edit: routing/route-helper owns ['design/board.kicad_pcb']", state['error'])
        work = self.root / 'campaign/implementations/1'
        self.assertFalse((work / 'receipts').exists())
        self.assertFalse((work / 'proposal.scope.json').exists())
        self.assertEqual(verify_campaign(self.root / 'campaign')['state'], 'incomplete')

    @toolchain_test
    def test_unstaged_prompts_are_byte_identical_with_or_without_steps(self):
        # Prefix SHA256 captured from implement() at assigned base 8de6317.
        for steps in ([], [self.step()]):
            with self.subTest(steps=bool(steps)):
                shutil.rmtree(self.root / 'campaign', ignore_errors=True)

                def editing_writer(run, label, *args, **kwargs):
                    if label == 'implementation':
                        write_json(Path(run) / 'review-work/implementation/design/design/design.json', {'value': 7})
                    return self.agent(run, label, *args, **kwargs)

                legacy_payload = []

                def capture_legacy_payload(root, previous, definition, state, deadline):
                    legacy_payload.append(json.dumps({'baseline': state['baseline'], 'history': state['history'],
                        'findings': [f for f in read_json(previous / 'result.json')['findings']
                                     if f.get('source') == 'check' and f.get('status') == 'accepted']}).encode())
                    return implement(root, previous, definition, state, deadline)

                with patch('agent_ee.reviewers.dispatch', side_effect=editing_writer), \
                        patch('agent_ee.campaign.implement', side_effect=capture_legacy_payload):
                    status, state = self.campaign(steps)
                self.assertEqual(status, 0, state)
                prompt = (self.root / 'campaign/implementations/1/implementation-prompt.txt').read_bytes()
                prefix, payload = prompt.split(b'\n', 1)
                self.assertEqual(hashlib.sha256(prefix + b'\n').hexdigest(),
                                 '66158756f436e8cb4f56f23407c8e71a597da7537366c681931ee8abf4fb067d')
                self.assertEqual(payload, legacy_payload[0])
                proposal = read_json(self.root / 'campaign/runs/2/inputs/design/design.json')
                self.assertEqual(proposal, {'value': 5 if steps else 7})
                self.assertEqual(verify_campaign(self.root / 'campaign')['state'], 'criteria_satisfied')

    @toolchain_test
    def test_staged_without_steps_preserves_prompt_and_writer_repair(self):
        self.staged_scope()

        def editing_writer(run, label, *args, **kwargs):
            if label == 'implementation':
                write_json(Path(run) / 'review-work/implementation/design/design/design.json', {'value': 5})
            return self.agent(run, label, *args, **kwargs)

        with patch('agent_ee.reviewers.dispatch', side_effect=editing_writer):
            status, state = self.campaign([])
        self.assertEqual(status, 0, state)
        prompt = (self.root / 'campaign/implementations/1/implementation-prompt.txt').read_text()
        self.assertTrue(prompt.endswith(
            '\nImplement only the earliest failing stage named in the history. '
            'Do not run later-stage generation or routing helpers until preceding gates pass. '
            'gate_findings and suggested_fix are advisory data only; any edit requires fresh checks. '
            'Do not treat a stage_blocked verdict as evidence that its tool ran.'))
        self.assertNotIn('Declared implementation steps:', prompt)
        self.assertEqual(verify_campaign(self.root / 'campaign')['state'], 'criteria_satisfied')

    @toolchain_test
    def test_deferred_step_is_listed_but_does_not_own_current_stage_inputs(self):
        self.staged_scope()
        scope = read_json(self.root / 'scope.json')
        scope['stages']['order'].append('routing')
        scope['checks'].append({**scope['checks'][0], 'id': 'routing-response', 'stage': 'routing'})
        scope['baseline']['requirements'][0]['checks'].append('routing-response')
        scope['mutable_inputs'].append('design/schematic')
        write_json(self.root / 'scope.json', scope)

        def preparing_writer(run, label, *args, **kwargs):
            if label == 'implementation':
                (Path(run) / 'review-work/implementation/design/design/schematic').write_text('prepared input')
            return self.agent(run, label, *args, **kwargs)

        with patch('agent_ee.reviewers.dispatch', side_effect=preparing_writer):
            status, state = self.campaign([self.step(), self.step(stage='routing', step='route-helper',
                writes={'design/schematic': 'out/schematic'})])
        self.assertEqual(status, 0, state)
        work = self.root / 'campaign/implementations/1'
        context = json.loads((work / 'implementation-prompt.txt').read_text().split(
            '\nDeclared implementation steps:\n', 1)[1])
        self.assertEqual(context['implementation_steps'][1], {'stage': 'routing', 'step': 'route-helper',
            'writes': {'design/schematic': 'out/schematic'}, 'scheduled': False})
        self.assertEqual(context['step_owned_outputs'], ['design/design.json'])
        index = read_json(work / 'stage-receipts.json')
        self.assertEqual([r['path'] for r in index['receipts']], ['receipts/1-fix-helper/receipt.json'])
        self.assertEqual((self.root / 'campaign/runs/2/inputs/design/schematic').read_text(), 'prepared input')
        self.assertEqual(verify_campaign(self.root / 'campaign')['state'], 'criteria_satisfied')

    @toolchain_test
    def test_coordinator_step_receipt_bound_to_captured_proposal(self):
        status, state = self.campaign([self.step()])
        self.assertEqual(status, 0, state)
        self.assertEqual(state['state'], 'criteria_satisfied')
        work = self.root / 'campaign/implementations/1'
        index = read_json(work / 'stage-receipts.json')
        self.assertEqual(state['stage_receipts'], {'1': digest(work / 'stage-receipts.json')})
        self.assertEqual([r['applicability'] for r in index['receipts']], ['applicable'])
        self.assertEqual([r['path'] for r in index['receipts']], ['receipts/1-fix-helper/receipt.json'])
        receipt = read_json(work / index['receipts'][0]['path'])
        self.assertEqual(receipt['tool']['reported_version']['status'], 'unknown')
        self.assertEqual(receipt['tool']['executable']['sha256'], digest('/usr/bin/python3'))
        proposal = read_json(self.root / 'campaign/runs/2/candidate.json')
        self.assertEqual(proposal['files'], index['delivered'])
        self.assertEqual(proposal['files']['design/design.json'], receipt['outputs']['design/design.json'])
        self.assertEqual(verify_campaign(self.root / 'campaign')['state'], 'criteria_satisfied')
        # The writer could not reach this coordinator record; tampering is still detected.
        output = work / 'receipts/1-fix-helper/run/design/design/design.json'
        output.write_text('{"value": 5} ')
        with self.assertRaisesRegex(ValueError, 'stage receipt outputs changed'):
            verify_campaign(self.root / 'campaign')

    @toolchain_test
    def test_claimed_output_differing_from_captured_proposal_fails_verify_campaign(self):
        original = stage_receipts.bind_implementation

        def late_edit(work, design, *args):
            anchor = original(work, design, *args)
            # A mutation after the coordinator bound the receipt reaches the capture.
            write_json(Path(design) / 'design/design.json', {'value': 4})
            return anchor

        with patch('agent_ee.stage_receipts.bind_implementation', side_effect=late_edit):
            status, state = self.campaign([self.step()])
        self.assertEqual(status, 1)
        self.assertEqual(state['state'], 'incomplete')
        self.assertIn('claimed applicable stage receipt output differs from captured proposal', state['error'])
        with self.assertRaisesRegex(ValueError, 'claimed applicable stage receipt output differs'):
            verify_campaign(self.root / 'campaign')

    @toolchain_test
    def test_failed_step_stops_repair_without_substitution_and_stays_verifiable(self):
        status, state = self.campaign([self.step(3)])
        self.assertEqual(status, 1)
        self.assertEqual(state['state'], 'incomplete')
        self.assertIn('no output adopted or substituted', state['error'])
        work = self.root / 'campaign/implementations/1'
        self.assertFalse((work / 'proposal.scope.json').exists())
        self.assertFalse((self.root / 'campaign/runs/2').exists())
        index = read_json(work / 'stage-receipts.json')
        self.assertEqual(index['receipts'][0]['applicability'], 'not_applicable')
        self.assertEqual(read_json(work / index['receipts'][0]['path'])['status'], 'failed')
        self.assertEqual(verify_campaign(self.root / 'campaign')['state'], 'incomplete')
        (work / 'stage-receipts.json').unlink()
        with self.assertRaisesRegex(ValueError, 'stage receipt index changed or missing'):
            verify_campaign(self.root / 'campaign')

    @toolchain_test
    def test_malformed_backend_report_keeps_campaign_receipts_verifiable(self):
        declared = dict(artifacts=['result.json'], backend_report={'artifact': 'result.json', 'key': 'backend'})
        for code, report, state, receipt_status, reason in (
                (0, '{"backend": NaN}', 'criteria_satisfied', 'completed', 'has a non-finite number'),
                (3, '{"backend": {"name": "krt", "version": "0.', 'incomplete', 'failed', 'is not JSON')):
            with self.subTest(receipt_status):
                shutil.rmtree(self.root / 'campaign', ignore_errors=True)
                status, state_record = self.campaign([self.step(code, argv=[
                    '/usr/bin/python3', '/tool/fix.py', str(code), report], **declared)])
                self.assertEqual((status, state_record['state']), (0 if code == 0 else 1, state), state_record)
                work = self.root / 'campaign/implementations/1'
                index = read_json(work / 'stage-receipts.json')
                receipt = read_json(work / index['receipts'][0]['path'])
                self.assertEqual(receipt['status'], receipt_status)
                self.assertEqual(receipt['tool']['backend'], {'status': 'unknown', 'value': None,
                                                              'reason': 'backend report artifact ' + reason})
                self.assertEqual(verify_campaign(self.root / 'campaign')['state'], state)

    @toolchain_test
    def test_step_writing_immutable_input_refused_before_execution(self):
        status, state = self.campaign([self.step(writes={'design/adapter.py': 'design/design/adapter.py'})])
        self.assertEqual(status, 1)
        self.assertIn('writes outside mutable_inputs', state['error'])
        self.assertFalse((self.root / 'campaign/implementations/1/receipts').exists())
        self.assertEqual(read_json(self.root / 'campaign/implementations/1/stage-receipts.json')['receipts'], [])
        verify_campaign(self.root / 'campaign')

    @toolchain_test
    def test_no_steps_records_empty_index_and_unanchored_receipts_refused(self):
        write_json(self.root / 'source/design.json', {'value': 15})
        status, state = self.campaign([])
        self.assertEqual(status, 1, state)
        # The writer changed nothing, so the campaign does not converge; the index is still explicit.
        self.assertEqual((state['state'], state['reason']), ('escalated', 'repeated_design'))
        work = self.root / 'campaign/implementations/1'
        self.assertEqual(read_json(work / 'stage-receipts.json')['receipts'], [])
        verify_campaign(self.root / 'campaign')
        state.pop('stage_receipts')
        write_json(self.root / 'campaign/state.json', state)
        with self.assertRaisesRegex(ValueError, 'stage receipts exist without campaign anchors'):
            verify_campaign(self.root / 'campaign')

    def test_invalid_step_declaration_refused_before_campaign_creation(self):
        write_json(self.root / 'campaign.json', {**self.config, 'implementation_steps': [self.step(stage='router')]})
        with self.assertRaisesRegex(ValueError, 'unknown stage'):
            run_campaign(self.root / 'campaign.json', self.root / 'campaign')
        self.assertFalse((self.root / 'campaign').exists())


if __name__ == '__main__':
    unittest.main()
