"""KICADROUTE-82 backend rename: 'builtin' is canonical, 'krt' its alias (AGENTEE-343).

Requests and parsed results accept both names and a request keeps the name it was written
with, so retained 'krt' records still read. Since the b577449 pin (AGENTEE-450) agent-ee
emits only 'builtin': it is the default and the only name the pinned tool is given.
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from agent_ee import kicad_route_attempt as attempt
from agent_ee import routing_adapter
from tests.test_kicad_route_adapter import REQUEST, Case


class BackendAliasTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-route-alias-')
        self.addCleanup(self.tmp.cleanup)
        self.count = 0

    def run_case(self, backend, scenario='complete'):
        self.count += 1
        with contextlib.redirect_stdout(io.StringIO()):
            return Case(Path(self.tmp.name) / str(self.count), scenario).run(REQUEST + ['--backend', backend])

    def test_canonical_names(self):
        self.assertEqual(attempt.canonical_backend('krt'), 'builtin')
        self.assertEqual(attempt.canonical_backend('builtin'), 'builtin')
        self.assertEqual(attempt.canonical_backend('freerouting'), 'freerouting')
        self.assertEqual(attempt.canonical_backend('nope'), 'nope')

    def test_requests_accept_both_names_and_keep_the_written_one(self):
        for name in ('krt', 'builtin'):
            with self.subTest(name):
                self.assertEqual(attempt.parse_request(REQUEST + ['--backend', name])['backend'], name)
                request = attempt.parse_request(REQUEST + ['--backend', name, '--diff-pairs', 'auto'])
                self.assertEqual(request['diff_pairs'], 'auto')
        self.assertEqual(attempt.parse_request(REQUEST)['backend'], 'builtin')

    def test_unknown_backend_is_refused(self):
        for name in ('', 'KRT', 'Builtin', 'krt2', 'builtin '):
            with self.subTest(name), self.assertRaisesRegex(attempt.RequestError, 'backend must be one of'):
                attempt.parse_request(REQUEST + ['--backend', name])
        with self.assertRaisesRegex(attempt.RequestError, 'diff-pairs'):
            attempt.parse_request(REQUEST + ['--backend', 'freerouting', '--diff-pairs', 'auto'])

    def test_pinned_tool_receives_only_builtin(self):
        layout = {'work': '/w', 'candidate': '/c', 'checkout': '/k'}
        for name in ('krt', 'builtin'):
            argv = attempt.tool_argv(attempt.parse_request(REQUEST + ['--backend', name]), layout)
            self.assertEqual(argv[argv.index('--backend') + 1], 'builtin')
            self.assertNotIn('krt', argv)
        argv = attempt.tool_argv(attempt.parse_request(REQUEST + ['--backend', 'freerouting']), layout)
        self.assertEqual(argv[argv.index('--backend') + 1], 'freerouting')

    def test_lookups_agree_across_aliases(self):
        for table in (attempt.REPRODUCIBILITY, attempt.STATE_REQUIRED, attempt.STATE_COMPONENTS):
            self.assertEqual(table['krt'], table['builtin'])

    def test_declaration_accepts_both_names(self):
        base = {'schema': 'agent-ee.routing-adapter/1', 'project': 'project', 'board': 'rc.kicad_pcb',
                'schematic': 'rc.kicad_sch', 'name': 'rc', 'label': 'step-1', 'checkout': '/opt/x'}
        for name in ('krt', 'builtin'):
            self.assertEqual(routing_adapter.validate_declaration({**base, 'backend': name})['backend'], name)
        with self.assertRaises(attempt.RequestError):
            routing_adapter.validate_declaration({**base, 'backend': 'other'})

    def test_runs_of_either_name_verify_and_krt_bytes_are_unchanged(self):
        reports = {}
        for name in ('krt', 'builtin'):
            case = self.run_case(name)
            self.assertEqual(case.exit, 0, case.report['semantics'])
            self.assertEqual(case.report['request']['backend'], name)
            summary = case.verify()
            self.assertEqual(summary['proposal'], 'eligible')
            self.assertEqual(summary['reproducibility'], attempt.REPRODUCIBILITY[name])
            result = json.loads((case.work / 'routing/attempt/result.json').read_text())
            self.assertEqual(result['backend']['name'], 'builtin')  # the tool echoes the emitted name
            reports[name] = case.report
        self.assertEqual(reports['krt']['reproducibility'], reports['builtin']['reproducibility'])

    def test_expected_settings_follow_a_reported_alias_only_of_the_same_backend(self):
        case = self.run_case('krt')
        path = case.work / 'routing/attempt/result.json'
        original = path.read_text()
        result = json.loads(original)
        self.assertEqual(attempt.canonical_backend(result['backend']['name']), 'builtin')
        self.assertEqual(attempt.expected_settings(attempt.parse_request(REQUEST + ['--backend', 'krt']), None,
                                                   reported='builtin')['backend'], 'builtin')
        self.assertEqual(attempt.expected_settings(attempt.parse_request(REQUEST + ['--backend', 'krt']), None,
                                                   reported='freerouting')['backend'], 'krt')
        self.assertEqual(attempt.expected_settings(attempt.parse_request(REQUEST + ['--backend', 'freerouting']), None,
                                                   reported='builtin')['backend'], 'freerouting')


if __name__ == '__main__':
    unittest.main()
