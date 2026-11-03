"""kicad-route at its current version, fixed per attempt and recorded (owner direction 2026-11-03).

The adapter no longer compares the checkout with a committed table of one commit's
file digests. Each attempt records the checkout it runs (tool.commit, tool.clean,
tool.code_sha256, tool.code_files) and verify reads that record back. These cases
use the coordinator-owned fixture router (tests/kicad_route_fixture.py), a real git
commit, so any change to the router is a new commit or a dirty tree:

- the record is written and read back, and a forged record is refused;
- a dirty tree, an unreadable or missing commit and an unknown tree state are refused
  before the router runs;
- result-shape drift is still refused by the result contract (fail closed);
- a report retained before 2026-11-03 verifies against the commit it recorded;
- a new commit is accepted and recorded, and an earlier report still verifies;
- a campaign continues only on the commit its binding recorded;
- the image installer refuses a missing or unparseable version instead of comparing
  it with a fixed one.
"""
import contextlib
import importlib._bootstrap_external
import importlib.util
import io
import json
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee import campaign
from agent_ee import kicad_route_attempt as attempt
from agent_ee import routing_adapter
from agent_ee import stage_receipts
from agent_ee.stage_receipts import observe_tool
from agent_ee.storage import digest
from tests.kicad_route_fixture import commit_all, git, make_checkout
from tests.test_kicad_route_adapter import REQUEST, Case, forge
from tests.test_stage_receipts import TOOL, step
from tests.toolchain import require_toolchain

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / 'docker/kicad-route/install.sh'
# A not_run report retained before 2026-11-03 (AGENTEE-442 control a): the commit table
# named bd1e633f while the checkout held b5774496, so the router never ran.
RETAINED = ROOT / ('evidence/k91-native-positive-c/bd1/out/control-a-runtime/low-level/receipts/'
                   '1-kicad-route-builtin/run')


def legacy(report, pin):
    """The report as the adapter wrote it before 2026-11-03: tool.pin from the commit table,
    the table's compatibility claim and tool-version reason, and no checkout record."""
    for key in ('clean', 'code_sha256', 'code_files'):
        report['tool'].pop(key)
    report['tool']['pin'] = pin
    report['adapter'] = {'compatibility': f'kicad-route@{pin}'}
    report['tool_version'] = dict(attempt.LEGACY_TOOL_VERSION)


class VersionRecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-route-record-')
        self.addCleanup(self.tmp.cleanup)
        self.count = 0

    def case(self, scenario='complete', **kwargs):
        self.count += 1
        return Case(Path(self.tmp.name) / str(self.count), scenario, **kwargs)

    def run_case(self, case, argv=REQUEST):
        with contextlib.redirect_stdout(io.StringIO()):
            return case.run(argv)

    def test_the_record_is_written_and_read_back(self):
        case = self.run_case(self.case())
        self.assertEqual(case.exit, 0, case.report['semantics'])
        files, problems = attempt.code_files(case.checkout)
        self.assertEqual(problems, [])
        tool = case.report['tool']
        self.assertEqual({k: tool[k] for k in attempt.CHECKOUT_KEYS},
                         {'commit': case.commit, 'clean': True, 'code_sha256': attempt.canonical_identity(files),
                          'code_files': 2})
        self.assertEqual(case.commit, git(case.checkout, 'rev-parse', 'HEAD'))
        self.assertEqual(case.report['adapter'], {'compatibility': f'kicad-route@{case.commit}'})
        self.assertNotIn('pin', tool)
        self.assertEqual(case.report['tool_version'], attempt.TOOL_VERSION)
        summary = case.verify()
        self.assertEqual((summary['classification'], summary['proposal']), ('complete', 'eligible'))
        # The host preflight reports the same record, and nothing it reports is a fixed value.
        check = routing_adapter.preflight(case.checkout)
        self.assertEqual({k: check[k] for k in attempt.CHECKOUT_KEYS}, {k: tool[k] for k in attempt.CHECKOUT_KEYS})
        self.assertEqual(check['problems'], [])
        self.assertNotIn('pin', check)
        # A record the attempt did not write is refused, never read as a claim.
        for label, change, message in (
                ('other commit', lambda r: r['tool'].update(commit='0' * 40), 'different kicad-route commit'),
                ('record dropped', lambda r: r['tool'].pop('code_sha256'), 'different kicad-route commit'),
                ('dirty claimed', lambda r: r['tool'].update(clean=None), 'recorded clean kicad-route commit'),
                ('no files', lambda r: r['tool'].update(code_files=0), 'recorded clean kicad-route commit')):
            with self.subTest(label):
                forged = self.run_case(self.case())
                forge(forged, report=change)
                with self.assertRaisesRegex(ValueError, message):
                    forged.verify()

    def test_a_dirty_tree_is_refused_before_the_router_runs(self):
        def staged(case):
            (case.checkout / 'src/kicad_route/__init__.py').write_text('# staged\n')
            git(case.checkout, 'add', 'src/kicad_route/__init__.py')
        for label, mutate, entry in (
                ('modified', lambda c: (c.checkout / 'bin/kicad-route').write_text('raise SystemExit(0)\n'),
                 ' M bin/kicad-route'),
                ('untracked', lambda c: (c.checkout / 'src/kicad_route/sitecustomize.py').write_text(''),
                 '?? src/kicad_route/sitecustomize.py'),
                ('staged', staged, 'M  src/kicad_route/__init__.py'),
                ('untracked outside the code', lambda c: (c.checkout / 'notes.txt').write_text('x\n'),
                 '?? notes.txt')):
            with self.subTest(label):
                case = self.case()
                mutate(case)
                self.run_case(case)
                self.assertEqual(case.exit, attempt.EXIT_REQUEST)
                reasons = case.report['semantics']['reasons']
                self.assertTrue(any('uncommitted or untracked changes' in r and entry in r for r in reasons), reasons)
                self.assertIsNone(case.report['process'])
                self.assertFalse((case.work / 'routing/out').exists())
                self.assertIs(case.report['tool']['clean'], False)
                self.assertEqual(case.verify()['classification'], 'not_run')
        # Ignored installed state and bytecode caches are not changes to the router.
        case = self.case()
        (case.checkout / 'src/kicad_route/__pycache__').mkdir()
        (case.checkout / 'src/kicad_route/__pycache__/x.cpython-312.pyc').write_bytes(b'cache')
        (case.checkout / '.venv-krt/extra').write_text('installed\n')
        self.run_case(case)
        self.assertEqual(case.exit, 0, case.report['semantics'])
        self.assertIs(case.report['tool']['clean'], True)

    def test_a_missing_or_unparseable_commit_is_refused(self):
        for label, mutate, reason in (
                ('unparseable HEAD', lambda c: (c.checkout / '.git/refs/heads/main').write_text('main\n'),
                 "checkout HEAD 'main' is not a commit"),
                ('unborn branch', lambda c: (c.checkout / '.git/HEAD').write_text('ref: refs/heads/none\n'),
                 'checkout HEAD None is not a commit'),
                ('no repository', lambda c: shutil.rmtree(c.checkout / '.git'), 'cannot read the checkout HEAD')):
            with self.subTest(label):
                case = self.case()
                mutate(case)
                self.run_case(case)
                self.assertEqual(case.exit, attempt.EXIT_REQUEST)
                reasons = case.report['semantics']['reasons']
                self.assertTrue(any(r.startswith(reason) for r in reasons), reasons)
                self.assertIsNone(case.report['process'])
                self.assertIsNone(case.report['tool']['commit'])
                self.assertEqual(case.report['adapter'], {'compatibility': 'kicad-route@unknown'})
                self.assertEqual(case.verify()['classification'], 'not_run')
        # A tree state git cannot report is not taken as clean.
        case = self.case()
        with patch.object(attempt, 'GIT_STATUS', ('/nonexistent/git', *attempt.GIT_STATUS[1:])):
            self.run_case(case)
        self.assertEqual(case.exit, attempt.EXIT_REQUEST)
        self.assertTrue(any(r.startswith('cannot tell whether the router checkout is clean')
                            for r in case.report['semantics']['reasons']), case.report['semantics']['reasons'])
        self.assertIsNone(case.report['tool']['clean'])

    def test_result_shape_drift_is_still_refused(self):
        for scenario, reason in (('unknown_key', 'result.json shape differs from the result contract'),
                                 ('custody_missing', 'result.json shape differs from the result contract'),
                                 ('unknown_result_version', 'unknown result schema version'),
                                 ('runtime_extra_key', 'backend.runtime differs from the result contract')):
            with self.subTest(scenario):
                case = self.run_case(self.case(scenario))
                self.assertEqual(case.exit, attempt.EXIT_CONTRADICTION, case.report['semantics'])
                self.assertTrue(any(r.startswith(reason) for r in case.report['semantics']['reasons']),
                                case.report['semantics']['reasons'])
                self.assertIsNone(case.report['candidate'])
                self.assertFalse((case.work / 'routing/candidate').exists())
                self.assertEqual(case.verify()['classification'], 'contradiction')
        # The one classification of result.json bytes refuses a key the contract does not name.
        good = self.run_case(self.case())
        data = (good.work / 'routing/attempt/result.json').read_bytes()
        self.assertEqual(attempt.classify(data, 0)[0], 'accepted')
        drifted = json.dumps({**json.loads(data), 'schema_version': 2}).encode()
        self.assertEqual(attempt.classify(drifted, 0), ('refused', ['result.json shape differs from the result contract']))

    def test_a_new_commit_is_accepted_and_recorded(self):
        first = self.run_case(self.case())
        self.assertEqual(first.exit, 0, first.report['semantics'])
        # The router moves on: a new commit in the same checkout.
        with (first.checkout / 'bin/kicad-route').open('a') as handle:
            handle.write('\n# a later kicad-route commit\n')
        commit = commit_all(first.checkout, 'later router')
        self.assertNotEqual(commit, first.commit)
        second = Case.__new__(Case)
        second.__dict__.update(first.__dict__, work=first.root / 'work-2')
        second.work.mkdir()
        second.layout = {**first.layout, 'work': second.work}
        self.run_case(second)
        self.assertEqual(second.exit, 0, second.report['semantics'])
        self.assertEqual((second.report['tool']['commit'], second.report['tool']['clean']), (commit, True))
        self.assertNotEqual(second.report['tool']['code_sha256'], first.report['tool']['code_sha256'])
        self.assertEqual(second.verify()['proposal'], 'eligible')
        # The earlier attempt still verifies against the commit it recorded.
        self.assertEqual(first.verify()['proposal'], 'eligible')
        self.assertEqual(json.loads((first.work / 'routing/adapter-report.json').read_text())['tool']['commit'],
                         first.commit)

    def test_an_old_receipt_verifies_against_its_own_record(self):
        report_path = RETAINED / 'routing/adapter-report.json'
        before = report_path.read_bytes()
        report = json.loads(before)
        # The retained record: the commit table named one commit, the checkout held another.
        self.assertNotEqual(report['tool']['pin'], report['tool']['commit'])
        summary = routing_adapter.verify(RETAINED)
        self.assertEqual((summary['classification'], summary['proposal']), ('not_run', 'none'))
        self.assertEqual(summary['tool_version'], attempt.LEGACY_TOOL_VERSION)
        self.assertEqual(report_path.read_bytes(), before)
        # An eligible attempt in the retained form verifies against the commit it recorded.
        case = self.run_case(self.case())
        forge(case, report=lambda r: legacy(r, case.commit))
        self.assertEqual(case.verify()['proposal'], 'eligible')
        # Its record must hold: a retained report whose recorded commit is not the one that ran is refused.
        case = self.run_case(self.case())
        forge(case, report=lambda r: legacy(r, '0' * 40))
        with self.assertRaisesRegex(ValueError, 'commit other than the one it recorded'):
            case.verify()
        # A retained step mounted the retired commit table; it reads as that record.
        spec = routing_adapter.step(project='project', board='rc.kicad_pcb', schematic='rc.kicad_sch',
                                    name='rc', label='step-1', checkout=case.checkout)
        table = Path(self.tmp.name) / 'kicad-route-pin.json'
        table.write_text('{}\n')
        retained = {**spec, 'mounts': [*spec['mounts'], {'source': str(table), 'at': attempt.LEGACY_TABLE_AT}]}
        self.assertEqual(routing_adapter.validate_campaign_step(retained, host=False)['mounts'], retained['mounts'])
        with self.assertRaisesRegex(ValueError, 'differs from the adapter command or mounts'):
            routing_adapter.validate_campaign_step(retained)

    def test_a_campaign_continues_only_on_the_commit_its_binding_recorded(self):
        case = self.case()
        declaration = {'schema': 'agent-ee.routing-adapter/1', 'project': 'project', 'board': 'rc.kicad_pcb',
                       'schematic': 'rc.kicad_sch', 'name': 'rc', 'label': 'campaign',
                       'checkout': str(case.checkout.resolve()), 'budget': 5, 'timeout': 30}
        spec = routing_adapter.validate_campaign_step(
            routing_adapter.declaration_spec(routing_adapter.validate_declaration(declaration)))
        binding = {'spec': spec, 'tool': observe_tool(spec), 'commit': case.commit,
                   'adapter_sha256': attempt.digest(routing_adapter.ADAPTER_SOURCE)}
        routing_adapter.validate_live_binding(binding)
        with (case.checkout / 'bin/kicad-route').open('a') as handle:
            handle.write('\n# a later kicad-route commit\n')
        commit = commit_all(case.checkout, 'later router')
        with self.assertRaises(routing_adapter.StaleRoutingEvidence) as caught:
            routing_adapter.validate_live_binding(binding)
        self.assertEqual((caught.exception.recorded, caught.exception.current), (case.commit, commit))
        # A binding written with the retired commit table is never continued.
        with self.assertRaises(routing_adapter.StaleRoutingEvidence):
            routing_adapter.validate_live_binding({**{k: v for k, v in binding.items() if k != 'commit'},
                                                   'pin': commit})


class BoardRouteCacheTest(unittest.TestCase):
    """The board route never reads bytecode cached in the checkout (review of fcfb4385, finding 3)."""

    def test_an_ignored_cache_cannot_run_code_the_recorded_digest_excludes(self):
        with tempfile.TemporaryDirectory(prefix='agent-ee-route-cache-') as temp:
            root = Path(temp)
            checkout, _ = make_checkout(root / 'router', 'complete')
            script = checkout / 'bin/kicad-route'
            script.write_text(script.read_text().replace(
                "ROOT = Path(__file__).resolve().parents[1]",
                "ROOT = Path(__file__).resolve().parents[1]\nsys.path.insert(0, str(ROOT / 'src'))\nimport kicad_route", 1))
            commit_all(checkout, 'import router package')
            before = routing_adapter.preflight(checkout)
            # An unchecked hash-based cache of the router's own package, which git ignores.
            source = checkout / 'src/kicad_route/__init__.py'
            cache = Path(importlib.util.cache_from_source(str(source)))
            cache.parent.mkdir(exist_ok=True)
            code = compile("raise SystemExit('UNRECORDED_CACHE_EXECUTED')", str(source), 'exec')
            cache.write_bytes(importlib._bootstrap_external._code_to_hash_pyc(code, b'\0' * 8, checked=False))
            after = routing_adapter.preflight(checkout)
            self.assertEqual(after['problems'], [])
            self.assertEqual({k: after[k] for k in attempt.CHECKOUT_KEYS}, {k: before[k] for k in attempt.CHECKOUT_KEYS})
            argv = routing_adapter.board_route_argv(checkout, root / 'input', root / 'out', budget_s=5)
            env = routing_adapter.board_route_env(checkout, root / 'home')
            done = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=120)
            self.assertNotIn('UNRECORDED_CACHE_EXECUTED', done.stderr)
            self.assertTrue(cache.is_file())
            # The same command without the private prefix runs the cached code: the hazard guarded.
            exposed = {k: v for k, v in env.items() if k != 'PYTHONPYCACHEPREFIX'}
            done = subprocess.run(argv, env=exposed, capture_output=True, text=True, timeout=120)
            self.assertIn('UNRECORDED_CACHE_EXECUTED', done.stderr)


class CampaignContinuationTest(unittest.TestCase):
    """Every turn of a campaign with a routing binding stops on another commit (review of fcfb4385,
    finding 4): a non-routing step never runs or adopts on M under L's binding."""

    def setUp(self):
        require_toolchain()
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-route-campaign-')
        self.addCleanup(self.tmp.cleanup)

    def test_a_non_routing_turn_on_another_commit_is_refused_before_it_runs(self):
        root = Path(self.tmp.name)
        checkout, recorded = make_checkout(root / 'checkout', 'complete')
        declaration = routing_adapter.validate_declaration(dict(
            schema='agent-ee.routing-adapter/1', project='project', board='rc.kicad_pcb', schematic='rc.kicad_sch',
            name='rc', label='campaign', checkout=str(checkout), budget=5, timeout=30))
        spec = routing_adapter.validate_campaign_step(routing_adapter.declaration_spec(declaration))
        binding = dict(spec=spec, tool=stage_receipts.observe_tool(spec), commit=recorded,
                       adapter_sha256=digest(routing_adapter.ADAPTER_SOURCE))
        (checkout / 'src/kicad_route/__init__.py').write_text('# later commit\n')
        current = commit_all(checkout, 'later runtime')
        # The reviewer's campaign-path case: a placement-stage step that would edit the design.
        work = root / '1'
        work.mkdir()
        design = root / 'design'
        (design / 'project').mkdir(parents=True)
        (design / 'project/board.txt').write_text('original')
        tool = root / 'tool'
        tool.mkdir()
        (tool / 'step.py').write_text(TOOL)
        custom = step(mounts=[dict(source=str(tool), at='/tool')])
        manifest = dict(candidate_id='fixture', files=stage_receipts.file_tree(design)[0])
        state = dict(routing_binding=binding, stage_receipts={}, mutable_inputs=['project/board.txt'])
        with self.assertRaises(routing_adapter.StaleRoutingEvidence) as caught:
            campaign.run_implementation_steps(work, design, manifest, state, time.monotonic() + 30,
                                              [dict(kind='step', spec=custom, receipt_index=1)])
        self.assertEqual((caught.exception.recorded, caught.exception.current), (recorded, current))
        # The step never ran: the design is unchanged and no step receipt exists.
        self.assertEqual((design / 'project/board.txt').read_text(), 'original')
        self.assertEqual(sorted(p.name for p in (work / 'receipts').glob('*')) if (work / 'receipts').exists() else [],
                         [])
        # The guard is the binding's, not the plan's: a turn without steps is refused too.
        (root / '2').mkdir()
        with self.assertRaises(routing_adapter.StaleRoutingEvidence):
            campaign.run_implementation_steps(root / '2', design, manifest, state, time.monotonic() + 30, [])


class InstallRecordRulesTest(unittest.TestCase):
    """The image installer records versions and refuses only a missing or unparseable one. The refusals
    run in install.sh itself: it is sourced, and its functions read fake toolchains and engines."""

    def setUp(self):
        self.script = INSTALL.read_text()
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-install-rules-')
        self.addCleanup(self.tmp.cleanup)
        self.count = 0

    def tool(self, directory, name, output, status=0):
        """An executable that prints output and exits with status, whatever its arguments."""
        path = Path(directory) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'#!/bin/bash\nprintf "%s\\n" {json.dumps(output)}\nexit {status}\n')
        path.chmod(0o755)
        return path

    def run_installer(self, commands, env=None):
        """`source install.sh; COMMANDS` in bash; (exit status, stdout, stderr)."""
        done = subprocess.run(['bash', '-c', f'source "$1"; {commands}', 'install-rules', str(INSTALL)],
                              capture_output=True, text=True, timeout=60,
                              env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', **(env or {})})
        return done.returncode, done.stdout, done.stderr

    def fresh(self):
        self.count += 1
        return Path(self.tmp.name) / str(self.count)

    def test_versions_are_resolved_and_recorded_not_compared(self):
        # No commit, engine version, toolchain banner or wheel version is written into the installer.
        self.assertIsNone(re.search(r'\b[0-9a-f]{40}\b', self.script))
        self.assertNotIn('0.23.1', self.script)
        self.assertNotIn('1.99.1', self.script)
        self.assertIn('KICAD_ROUTE_COMMIT=${KICAD_ROUTE_COMMIT:-}', self.script)
        self.assertIn('git ls-remote "$KICAD_ROUTE_URL" "refs/heads/$KICAD_ROUTE_BRANCH"', self.script)
        self.assertFalse((ROOT / 'docker/kicad-route/krt-requirements.txt').exists())
        dockerfile = (ROOT / 'Dockerfile').read_text()
        self.assertIn('ARG KICAD_ROUTE_COMMIT=\n', dockerfile)

    def test_a_missing_or_unparseable_toolchain_banner_is_refused(self):
        good = {'rustc': 'rustc 1.99.1 (58cf8f9c3 2026-10-13)', 'cargo': 'cargo 1.101.0 (5f94df478 2027-01-02)'}
        cases = [('recorded', good, 0, None)]
        for tool in ('rustc', 'cargo'):
            for bad in ('', tool, f'{tool} 1.99 (58cf8f9c3 2026-10-13)', f'{tool} 1.99.1',
                        f'{tool} 1.99.1-nightly (58cf8f9c3 2026-10-13)', 'error: no toolchain'):
                cases.append((f'{tool} {bad!r}', {**good, tool: bad}, 0, tool))
            cases.append((f'{tool} fails', good, 127 if tool == 'rustc' else 1, tool))
        for label, banners, status, refused in cases:
            with self.subTest(label):
                bin_dir = self.fresh()
                for tool, banner in banners.items():
                    self.tool(bin_dir, tool, banner, status if tool == refused else 0)
                code, out, err = self.run_installer('read_toolchain; printf "%s|%s" "$rustc" "$cargo"',
                                                    {'PATH': f'{bin_dir}:/usr/bin:/bin'})
                if refused is None:
                    self.assertEqual((code, out), (0, f"{good['rustc']}|{good['cargo']}"), err)
                else:
                    self.assertEqual(code, 1, (out, err))
                    self.assertIn(f'kicad-route install: unreadable {refused} version', err)

    def test_a_missing_or_unparseable_engine_version_is_refused(self):
        for version, status in (('0.23.1', 0), ('0.24.0', 0), ('1.0.0-rc.1', 0),
                                ('', 0), ('0.23', 0), ('unknown', 0), ('v0.23.1', 0), ('0.23.1', 1)):
            with self.subTest(version=version, status=status):
                root = self.fresh()
                (root / 'krt').mkdir(parents=True)
                self.tool(root / 'venv/bin', 'python', version, status)
                code, out, err = self.run_installer(f'KRT={root / "krt"}; VENV={root / "venv"}; read_engine_version; '
                                                    'printf "%s" "$engine"')
                if status == 0 and re.fullmatch(r'\d+\.\d+\.\d+(-rc\.1)?', version):
                    self.assertEqual((code, out), (0, version), err)
                else:
                    self.assertEqual(code, 1, (out, err))
                    self.assertIn('kicad-route install: unreadable engine version', err)

    def test_a_commit_argument_that_is_not_a_commit_is_refused(self):
        for value, accepted in (('a' * 40, True), ('main', False), ('a' * 39, False), ('A' * 40, False)):
            with self.subTest(value=value):
                code, out, err = self.run_installer('resolve_commit; printf "%s %s" "$KICAD_ROUTE_COMMIT" "$REQUESTED"',
                                                    {'KICAD_ROUTE_COMMIT': value})
                if accepted:
                    self.assertEqual((code, out), (0, f'{value} {value}'), err)
                else:
                    self.assertEqual(code, 1, (out, err))
                    self.assertIn('is not a commit', err)


if __name__ == '__main__':
    unittest.main()
