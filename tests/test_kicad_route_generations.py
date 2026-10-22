"""Router generations and the pin's nested result keys (AGENTEE-450, agent-508 findings).

kicad-route b577449 installs the owned engine in one of two layouts: the legacy
one (no generation selected: engine in the checkout, venv at .venv-krt; the
image's docker/kicad-route/install.sh builds this) and a router generation
(`kicad-route update`: STATE/.router-generations/<hex>/ holds a copy of the
engine source and its own venv, selected by the manifest's krt.generation).
Both qualify, and only when built from the pinned source: a generation is
compared with the verified checkout's SOURCE_ROOT, never with itself, so a
stale generation that `kicad-route doctor` still passes is refused.

Policy cases use the fixture router. State cases derive both layouts from the
configured qualified checkout (whichever layout it has) without rebuilding the
engine. The native case runs the real pin through the sandbox on a generation
layout and checks the result keys it adds: copper_ownership,
process.measurement_qualification and backend.runtime.
"""
import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_ee import kicad_route_attempt as attempt
from agent_ee import routing_adapter
from agent_ee.executor import execute
from tests.kicad_route_fixture import FAKE_PIN, make_candidate, make_checkout
from tests.test_kicad_route_adapter import REQUEST, Case
from tests.toolchain import require_kicad_route, require_toolchain

ROOT = Path(__file__).resolve().parents[1]
GENERATION = '.router-generations/' + 'a' * 32


def select_generation(state, generation=GENERATION, files=attempt.GENERATION_REQUIRED):
    """A fixture state whose manifest selects a generation holding the files the pin requires."""
    for rel in files:
        path = Path(state) / generation / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel == 'venv/bin/python':
            path.symlink_to('/usr/bin/python3')
        else:
            path.write_text('{}\n')
    (Path(state) / 'manifest.json').write_text(json.dumps({'krt': {'generation': generation}}))


def engine_of(checkout):
    """(source dir, venv dir, installation record or None) of the configured checkout's engine."""
    generation, problems = attempt.state_generation(checkout)
    assert not problems, problems
    if generation is None:
        return checkout / 'src/kicad_route/router', checkout / '.venv-krt', None
    record = json.loads((checkout / generation / 'installation.json').read_text())
    return checkout / generation / 'router', checkout / generation / 'venv', record


def derive_state(checkout, state, layout, *, copy_venv=False):
    """The qualified engine re-laid out under `state` as 'legacy' or 'generation'.

    The source is the pinned SOURCE_ROOT and the venv the qualified one (linked, or
    copied when the state must be mounted elsewhere); the manifest is the checkout's
    with its krt paths moved. Nothing is rebuilt: hashes stay the verified ones."""
    source, venv, record = engine_of(checkout)
    manifest = json.loads((checkout / 'manifest.json').read_text())
    k = manifest['krt']
    module = Path(k['extension_module']['path'])
    inside = module.relative_to(Path(k['venv']['path']))
    state.mkdir(parents=True, exist_ok=True)
    if layout == 'legacy':
        prefix, venv_rel, source_rel = None, '.venv-krt', 'src/kicad_route/router'
    else:
        prefix = GENERATION
        venv_rel, source_rel = f'{prefix}/venv', f'{prefix}/router'
        shutil.copytree(checkout / 'src/kicad_route/router', state / source_rel,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (state / venv_rel).parent.mkdir(parents=True, exist_ok=True)
    if copy_venv:
        shutil.copytree(venv, state / venv_rel, symlinks=True)
    else:
        (state / venv_rel).symlink_to(venv)
    if prefix:
        record = record or {'source': {'source_sha256': k['source_sha256']},
                            'extension_sha256': k['extension_module']['sha256']}
        (state / prefix / 'installation.json').write_text(json.dumps(record))
    k.update(generation=prefix, source_path=source_rel,
             extension_module={**k['extension_module'], 'path': f'{venv_rel}/{inside.as_posix()}'},
             requirements={**k['requirements'], 'path': f'{source_rel}/requirements.txt'},
             venv={**k['venv'], 'path': venv_rel})
    (state / 'manifest.json').write_text(json.dumps(manifest))
    return manifest


def copy_tool(checkout, root):
    """The checkout's tracked tool files with HEAD at the pin, without its installed state."""
    state = {'.git', '.venv-krt', '.router-generations', 'manifest.json', 'build', 'vendor'}
    caches = shutil.ignore_patterns('__pycache__', '*.pyc')
    shutil.copytree(checkout, root, ignore=lambda d, names: (
        caches(d, names) | (state & set(names) if Path(d) == Path(checkout) else set())))
    (root / '.git').mkdir()
    (root / '.git/HEAD').write_text(attempt.PIN + '\n')
    return root


def pin_drift(checkout, state):
    """The pin's own manifest-vs-disk check (what `kicad-route doctor` relies on)."""
    script = ('import json, sys; from pathlib import Path; sys.path.insert(0, sys.argv[1] + "/src"); '
              'from kicad_route import statemanifest; s = Path(sys.argv[2]); '
              'print(json.dumps(statemanifest.verify(json.loads((s / "manifest.json").read_text()), s)))')
    out = subprocess.run(['/usr/bin/python3', '-I', '-B', '-c', script, str(checkout), str(state)],
                         capture_output=True, text=True, timeout=120, check=True,
                         env={'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'})
    return json.loads(out.stdout)


class LayoutPolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-generations-')
        self.addCleanup(self.tmp.cleanup)
        self.count = 0

    def case(self, scenario, **kwargs):
        self.count += 1
        return Case(Path(self.tmp.name) / str(self.count), scenario, **kwargs)

    def run_case(self, case, argv=REQUEST):
        with contextlib.redirect_stdout(io.StringIO()):
            return case.run(argv)

    def test_components_and_required_files_follow_the_layout(self):
        for name in ('builtin', 'krt'):
            self.assertEqual(attempt.state_components(name), ('.venv-krt',))
            self.assertEqual(attempt.state_required(name), ('.venv-krt/bin/python',))
            self.assertEqual(attempt.state_components(name, GENERATION), (GENERATION,))
            self.assertEqual(attempt.state_required(name, GENERATION),
                             tuple(f'{GENERATION}/{rel}' for rel in
                                   ('installation.json', 'router/VERSION', 'router/UPSTREAM.json', 'venv/bin/python')))
        # Freerouting reads no router generation.
        self.assertEqual(attempt.state_components('freerouting', GENERATION), ('vendor/freerouting',))
        self.assertEqual(attempt.state_required('freerouting', GENERATION), ('vendor/freerouting',))
        self.assertIsNone(attempt.generation_of_components('builtin', ['.venv-krt']))
        self.assertEqual(attempt.generation_of_components('builtin', [GENERATION]), GENERATION)
        for components in ([], ['.venv-krt', GENERATION], ['.router-generations/g1'], ['../.venv-krt']):
            with self.subTest(components=components), self.assertRaises(ValueError):
                attempt.generation_of_components('builtin', components)

    def test_state_generation_reads_only_a_contained_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            self.assertEqual(attempt.state_generation(state), (None, []))  # no manifest: legacy
            for value, expected in ((None, (None, [])), (GENERATION, (GENERATION, []))):
                (state / 'manifest.json').write_text(json.dumps({'krt': {'generation': value}}))
                self.assertEqual(attempt.state_generation(state), expected)
            for value in ('.router-generations/g1', '../outside', '.router-generations/' + 'a' * 32 + '/..',
                          '/abs/.router-generations/' + 'a' * 32, '', 5):
                with self.subTest(value=value):
                    (state / 'manifest.json').write_text(json.dumps({'krt': {'generation': value}}))
                    generation, problems = attempt.state_generation(state)
                    self.assertIsNone(generation)
                    self.assertEqual(problems, [f'state manifest selects an invalid router generation {value!r}'])

    def test_a_generation_layout_attempt_is_accepted_and_bound_to_its_runtime(self):
        case = self.case('complete')
        select_generation(case.checkout)
        self.run_case(case)
        self.assertEqual(case.exit, 0, case.report['semantics'])
        self.assertEqual(list(case.report['identity_before']['state']), [GENERATION])
        summary = case.verify()
        self.assertEqual((summary['classification'], summary['proposal']), ('complete', 'eligible'))
        runtime = summary['backend']['value']['runtime']
        self.assertEqual(runtime, {'source': str(case.checkout / GENERATION / 'router'),
                                   'venv': str(case.checkout / GENERATION / 'venv'),
                                   'generation': str(case.checkout / GENERATION)})

    def test_the_legacy_layout_still_binds_the_checkout_engine(self):
        case = self.run_case(self.case('complete'))
        self.assertEqual(case.exit, 0, case.report['semantics'])
        self.assertEqual(list(case.report['identity_before']['state']), ['.venv-krt'])
        self.assertEqual(case.verify()['backend']['value']['runtime'],
                         {'source': str(case.checkout / 'src/kicad_route/router'),
                          'venv': str(case.checkout / '.venv-krt'), 'generation': None})

    def test_an_incomplete_generation_never_runs(self):
        for missing in attempt.GENERATION_REQUIRED:
            with self.subTest(missing=missing):
                case = self.case('complete')
                select_generation(case.checkout, files=[r for r in attempt.GENERATION_REQUIRED if r != missing])
                self.run_case(case)
                self.assertEqual(case.report['semantics']['classification'], 'not_run')
                self.assertIn(f'missing installed backend dependency: KICAD_ROUTE_STATE/{GENERATION}/{missing}',
                              case.report['semantics']['reasons'])
                self.assertIsNone(case.report['process'])
        case = self.case('complete')
        (case.checkout / 'manifest.json').write_text(json.dumps({'krt': {'generation': '../elsewhere'}}))
        self.run_case(case)
        self.assertEqual(case.report['semantics']['classification'], 'not_run')
        self.assertIn("state manifest selects an invalid router generation '../elsewhere'",
                      case.report['semantics']['reasons'])

    def test_a_runtime_other_than_the_verified_engine_is_a_contradiction(self):
        for scenario, generation in (('runtime_elsewhere', None), ('runtime_elsewhere', GENERATION)):
            with self.subTest(generation=generation):
                case = self.case(scenario)
                if generation:
                    select_generation(case.checkout)
                self.run_case(case)
                sem = case.report['semantics']
                self.assertEqual((sem['classification'], sem['proposal']), ('contradiction', 'none'))
                self.assertIn('the router ran an engine other than the verified '
                              + (f'router generation {generation}' if generation else 'checkout engine'),
                              sem['reasons'])
                self.assertIsNone(case.report['candidate'])
                case.verify()  # the contradiction itself verifies; it delivers nothing

    def test_a_forged_runtime_on_an_eligible_report_does_not_verify(self):
        case = self.run_case(self.case('complete'))
        path = case.work / 'routing/attempt/result.json'
        result = json.loads(path.read_text())
        result['backend']['runtime'] = {'source': str(case.checkout / GENERATION / 'router'),
                                        'venv': str(case.checkout / GENERATION / 'venv'),
                                        'generation': str(case.checkout / GENERATION)}
        path.write_text(json.dumps(result))
        report = json.loads((case.work / 'routing/adapter-report.json').read_text())
        report['retained']['attempt/result.json'] = attempt.digest(path)
        report['backend']['value'] = result['backend']
        attempt.write_report(case.work / 'routing/adapter-report.json', report)
        with self.assertRaisesRegex(ValueError, 'ran an engine other than the verified one'):
            case.verify()

    def test_nested_result_keys_are_exact(self):
        for scenario, reason in (('runtime_extra_key', 'backend.runtime differs from the pinned tool'),
                                 ('measurement_missing', 'process.measurement_qualification differs'),
                                 ('measurement_disagrees', 'process.measurement_qualification differs')):
            with self.subTest(scenario=scenario):
                case = self.run_case(self.case(scenario))
                sem = case.report['semantics']
                self.assertEqual(sem['classification'], 'contradiction')
                self.assertTrue(any(r.startswith(reason) for r in sem['reasons']), sem['reasons'])
        # measurement_qualification is on every result; runtime only on the owned engine's.
        freerouting = self.case('complete')
        (freerouting.checkout / 'vendor/freerouting').mkdir()
        self.run_case(freerouting, REQUEST + ['--backend', 'freerouting'])
        self.assertEqual(freerouting.report['semantics']['classification'], 'complete',
                         freerouting.report['semantics'])
        result = json.loads((freerouting.work / 'routing/attempt/result.json').read_text())
        self.assertNotIn('runtime', result['backend'])
        self.assertNotIn('copper_ownership', result)
        self.assertIn('measurement_qualification', result['process'])
        failed = self.run_case(self.case('backend_failure'))
        self.assertEqual(failed.report['semantics']['classification'], 'backend_failure')
        result = json.loads((failed.work / 'routing/attempt/result.json').read_text())
        self.assertEqual(set(result['backend']['runtime']), attempt.RUNTIME_KEYS)
        self.assertNotIn('copper_ownership', result)

    def test_shape_helpers(self):
        good = {'measurement': False, 'process': {'measurement_qualification': {
            'measurement': False, 'build_reproducible': None, 'reasons': ['basis']}}}
        self.assertTrue(attempt.measurement_shape(good))
        for change in (lambda r: r.update(measurement=True),
                       lambda r: r['process']['measurement_qualification'].update(extra=1),
                       lambda r: r['process']['measurement_qualification'].update(reasons=[''])):
            bad = json.loads(json.dumps(good))
            change(bad)
            self.assertFalse(attempt.measurement_shape(bad))
        runtime = {'source': '/opt/kicad-route/src/kicad_route/router', 'venv': '/opt/kicad-route/.venv-krt',
                   'generation': None}
        self.assertTrue(attempt.runtime_shape({'runtime': runtime}))
        for bad in ({}, {'runtime': {**runtime, 'generation': 'relative'}}, {'runtime': {**runtime, 'source': None}},
                    {'runtime': {k: v for k, v in runtime.items() if k != 'venv'}}):
            self.assertFalse(attempt.runtime_shape(bad))


class SandboxedGenerationTest(unittest.TestCase):
    """Under the production executor the selected generation is probed read-only too."""

    def setUp(self):
        require_toolchain()
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-generation-sandbox-')
        self.addCleanup(self.tmp.cleanup)

    def test_the_generation_is_mounted_read_only_and_probed(self):
        root = Path(self.tmp.name)
        checkout, code = make_checkout(root / 'checkout', 'complete')
        select_generation(checkout)
        candidate = make_candidate(root / 'candidate')
        driver = root / 'driver'
        driver.mkdir()
        (driver / 'layout.json').write_text(json.dumps({'pin': FAKE_PIN, 'code': code}))
        (driver / 'run.py').write_text(
            'import json, sys\nsys.path.insert(0, "/opt/agent-ee-routing")\n'
            'import kicad_route_attempt as a\n'
            'cfg = json.load(open("/opt/driver/layout.json"))\n'
            'layout = a.default_layout(a.parse_request(sys.argv[1:]))\n'
            'layout.update(pin=cfg["pin"], code=cfg["code"])\n'
            'sys.exit(a.main(sys.argv[1:], layout))\n')
        work = root / 'work'
        record = execute(candidate, work, ['/usr/bin/python3', '-I', '-B', '/opt/driver/run.py', *REQUEST],
                         timeout=60, extra_ro=[(routing_adapter.ADAPTER_SOURCE, routing_adapter.ADAPTER_AT),
                                               (checkout, '/opt/kicad-route'), (driver, '/opt/driver')])
        report = json.loads((work / 'routing/adapter-report.json').read_text())
        self.assertEqual(record['exit_code'], 0, report['semantics'])
        probes = report['state_write_probe']
        self.assertEqual([p['directory'] for p in probes], ['/opt/kicad-route', f'/opt/kicad-route/{GENERATION}'])
        self.assertTrue(all(p['refused'] and p['errno'] == 'EROFS' for p in probes), probes)
        self.assertEqual(routing_adapter.verify(work, attempt.files_of(attempt.tree(candidate)),
                                                layout={'pin': FAKE_PIN, 'code': code})['proposal'], 'eligible')


class GenerationStateTest(unittest.TestCase):
    """verify_state on both layouts, derived from the real qualified checkout."""

    @classmethod
    def setUpClass(cls):
        cls.checkout = require_kicad_route()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-generation-state-')
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / 'state'

    def verify(self):
        return attempt.verify_state(self.checkout, self.state, mounted_at=attempt.SANDBOX['state'])

    def write(self, manifest):
        manifest['state_dir'] = attempt.SANDBOX['state']
        (self.state / 'manifest.json').write_text(json.dumps(manifest))

    def test_both_layouts_built_from_the_pin_qualify(self):
        for layout in ('legacy', 'generation'):
            with self.subTest(layout=layout):
                shutil.rmtree(self.state, ignore_errors=True)
                self.write(derive_state(self.checkout, self.state, layout))
                self.assertEqual(self.verify(), [])
                self.assertEqual(attempt.state_generation(self.state),
                                 (GENERATION if layout == 'generation' else None, []))

    def test_a_stale_generation_is_refused_although_the_pin_sees_no_drift(self):
        """A generation built from another commit is self-consistent, so doctor passes it."""
        manifest = derive_state(self.checkout, self.state, 'generation')
        router = self.state / GENERATION / 'router'
        (router / 'VERSION').write_text((router / 'VERSION').read_text() + 'stale\n')
        script = ('import sys; sys.path.insert(0, sys.argv[1] + "/src"); '
                  'from kicad_route import engine; print(engine.source_sha256(sys.argv[2]))')
        stale = subprocess.run(['/usr/bin/python3', '-I', '-B', '-c', script, str(self.checkout), str(router)],
                               capture_output=True, text=True, check=True, timeout=120).stdout.strip()
        manifest['krt'].update(source_sha256=stale, commit='f42b879' + '0' * 33)
        record = json.loads((self.state / GENERATION / 'installation.json').read_text())
        record['source']['source_sha256'] = stale
        (self.state / GENERATION / 'installation.json').write_text(json.dumps(record))
        self.write(manifest)
        self.assertEqual(pin_drift(self.checkout, self.state), [])
        problems = self.verify()
        self.assertEqual(len([p for p in problems if p.startswith('owned engine source digest differs; router '
                                                                  f'generation {GENERATION} was built from commit f42b879')]),
                         1, problems)
        self.assertIn('router generation installation record differs from the pinned source or module', problems)

    def test_a_generation_must_hold_its_own_source_venv_and_record(self):
        cases = {
            'checkout source': lambda m: m['krt'].update(source_path='src/kicad_route/router'),
            'legacy venv': lambda m: m['krt']['venv'].update(path='.venv-krt'),
            'module elsewhere': lambda m: m['krt']['extension_module'].update(path='/usr/bin/python3'),
            'record': lambda m: (self.state / GENERATION / 'installation.json').write_text('{}'),
            'invalid selection': lambda m: m['krt'].update(generation='.router-generations/g1'),
            'edited copy': lambda m: (self.state / GENERATION / 'router/VERSION').write_text('edited\n'),
        }
        expected = {
            'checkout source': 'router generation source or requirements path is not the generation copy',
            'legacy venv': 'unexpected routing environment path',
            'module elsewhere': f'manifest must identify the installed {GENERATION}/venv grid_router extension',
            'record': 'router generation installation record differs from the pinned source or module',
            'invalid selection': "state manifest selects an invalid router generation '.router-generations/g1'",
            'edited copy': 'state manifest drift: krt.source_sha256',
        }
        for label, change in cases.items():
            with self.subTest(label=label):
                shutil.rmtree(self.state, ignore_errors=True)
                manifest = derive_state(self.checkout, self.state, 'generation')
                change(manifest)
                self.write(manifest)
                self.assertIn(expected[label], self.verify())

    def test_preflight_requires_the_generation_files(self):
        root = copy_tool(self.checkout, Path(self.tmp.name) / 'tool')
        manifest = derive_state(self.checkout, root, 'generation')
        manifest['state_dir'] = attempt.SANDBOX['checkout']
        (root / 'manifest.json').write_text(json.dumps(manifest))
        self.assertEqual(routing_adapter.preflight(root)['problems'], [])
        (root / GENERATION / 'router/UPSTREAM.json').rename(root / 'UPSTREAM.json.aside')
        problems = routing_adapter.preflight(root)['problems']
        self.assertIn(f'missing installed backend dependency: KICAD_ROUTE_STATE/{GENERATION}/router/UPSTREAM.json',
                      problems)


class GenerationNativeTest(unittest.TestCase):
    """The real pin routes from a generation layout; its result carries exactly the nested keys."""

    @classmethod
    def setUpClass(cls):
        cls.checkout = require_kicad_route()

    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-generation-native-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def generation_checkout(self):
        """The configured checkout's code with its engine as a self-contained generation."""
        if attempt.state_generation(self.checkout)[0] is not None:
            return self.checkout, attempt.state_generation(self.checkout)[0]
        tool = copy_tool(self.checkout, self.root / 'tool')
        manifest = derive_state(self.checkout, tool, 'generation', copy_venv=True)
        manifest['state_dir'] = attempt.SANDBOX['checkout']
        (tool / 'manifest.json').write_text(json.dumps(manifest))
        return tool, GENERATION

    def test_builtin_run_on_a_generation(self):
        tool, generation = self.generation_checkout()
        check = routing_adapter.preflight(tool)
        self.assertEqual(check['problems'], [])
        placed = self.root / 'placed'
        shutil.copytree(ROOT / 'fixtures/rc/control', placed / 'project')
        shutil.copytree(ROOT / 'fixtures/rc/requirements', placed / 'requirements')
        spec = routing_adapter.step(project='project', board='rc_filter.kicad_pcb', schematic='rc_filter.kicad_sch',
                                    name='rc_filter', label='generation-native', checkout=tool, budget=120)
        work = self.root / 'work'
        record = execute(placed, work, spec['argv'], timeout=spec['timeout_s'],
                         extra_ro=[(m['source'], m['at']) for m in spec['mounts']])
        report = json.loads((work / 'routing/adapter-report.json').read_text())
        self.assertIn(record['exit_code'], (0, 2), report['semantics'])
        summary = routing_adapter.verify(work, attempt.files_of(attempt.tree(placed)))
        self.assertIn(summary['proposal'], ('eligible', 'eligible_partial'), summary)
        result = json.loads((work / 'routing/attempt/result.json').read_text())
        mounted = Path(attempt.SANDBOX['checkout'])
        self.assertEqual(set(result), attempt.RESULT_KEYS | {'copper_ownership'})
        self.assertEqual(result['backend']['name'], 'builtin')
        self.assertEqual(result['backend']['runtime'], {
            'source': str(mounted / generation / 'router'), 'venv': str(mounted / generation / 'venv'),
            'generation': str(mounted / generation)})
        self.assertEqual(result['backend']['source_sha256'],
                         json.loads((tool / 'manifest.json').read_text())['krt']['source_sha256'])
        self.assertEqual(set(result['process']['measurement_qualification']), attempt.MEASUREMENT_QUALIFICATION)
        self.assertIs(result['process']['measurement_qualification']['measurement'], result['measurement'])
        self.assertEqual(result['copper_ownership']['status'], 'bound')
        self.assertEqual(list(report['identity_before']['state']), [generation])
        self.assertEqual([p['directory'] for p in report['state_write_probe']],
                         [str(mounted), str(mounted / generation)])
        self.assertTrue(all(p['refused'] for p in report['state_write_probe']))


if __name__ == '__main__':
    unittest.main()
