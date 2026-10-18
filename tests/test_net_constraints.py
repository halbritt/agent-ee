"""AGENTEE-47: intent net constraints emitted as native KiCad netclasses."""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from toolchain import require_toolchain

from agent_ee import candidate, checks
from agent_ee.baseline import compile_baseline, freeze_evidence, verify_evidence
from agent_ee.capabilities import describe_check
from agent_ee.net_constraints import emit, exact_pattern, net_minimums
from agent_ee.netclass_completeness import DEFAULT_KEYS, evaluate
from agent_ee.storage import read_json

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = (REPO / 'fixtures/netclass-completeness/native.kicad_pro').read_bytes()
ENVELOPE = (REPO / 'fixtures/board-envelope/rc.envelope.json').read_bytes()
CONSTRAINTS = REPO / 'fixtures/net-constraints/rc.constraints.json'


def document(**overrides):
    doc = json.loads(CONSTRAINTS.read_bytes())
    doc.update(overrides)
    return doc


def run(doc, *, envelope=ENVELOPE, template=TEMPLATE, rules=None):
    raw = doc if isinstance(doc, bytes) else json.dumps(doc).encode()
    artifacts = emit(raw, template, envelope_raw=envelope, rules_raw=rules)
    return artifacts, json.loads(artifacts['record'])


def independent_ipc2152_mm(current_a, rise_c, oz):
    # Restated from the published generic-chart fit, in mils, as an oracle.
    area = (117.555 * rise_c ** -0.913 + 1.15) * current_a ** (0.84 * rise_c ** -0.108 + 1.159)
    return area / (1.37 * oz) * 0.0254


class EmitterTests(unittest.TestCase):
    def test_two_amps_on_one_ounce_outer_copper_meets_ipc2152_width(self):
        artifacts, record = run(document())
        self.assertEqual(record['outcome'], 'pass')
        power = next(c for c in record['classes'] if c['name'] == 'POWER_IN')
        floor = independent_ipc2152_mm(2.0, 10, 1)
        self.assertGreaterEqual(power['min_track_width_mm'], floor)
        self.assertLess(power['min_track_width_mm'] - floor, 0.002)
        self.assertEqual((power['min_track_width_mm'], power['ipc2152']['governing_layer']), (1.012, 'F.Cu'))
        project = json.loads(artifacts['project'])
        cls = next(c for c in project['net_settings']['classes'] if c['name'] == 'POWER_IN')
        self.assertEqual(cls['track_width'], 1.012)
        self.assertIn("(rule \"agent_ee_netclass_POWER_IN\"\n  (condition \"A.NetClass == 'POWER_IN'\")\n"
                      "  (constraint track_width (min 1.012mm)))", artifacts['rules'].decode())

    def test_fit_reproduces_published_chart_point(self):
        # 10 A at 20 C: chart about 500 mil^2, fit 513.1 mil^2 (within its stated 3 %).
        self.assertAlmostEqual(independent_ipc2152_mm(10, 20, 1) * 1.37 / 0.0254, 513.1, delta=0.1)

    def test_thinnest_declared_copper_governs(self):
        envelope = json.loads(ENVELOPE)
        envelope['stackup']['layers'][2]['copper_weight_oz'] = 0.5
        _, record = run(document(), envelope=json.dumps(envelope).encode())
        power = next(c for c in record['classes'] if c['name'] == 'POWER_IN')
        self.assertEqual(power['ipc2152']['governing_layer'], 'B.Cu')
        self.assertGreaterEqual(power['min_track_width_mm'], independent_ipc2152_mm(2.0, 10, 0.5))

    def test_default_keeps_every_key_and_unconstrained_nets(self):
        artifacts, record = run(document())
        project = json.loads(artifacts['project'])
        self.assertEqual(evaluate(project), [])
        settings = project['net_settings']
        default = json.loads(TEMPLATE)['net_settings']['classes'][0]
        self.assertEqual(settings['classes'][0], default)
        for cls in settings['classes']:
            self.assertEqual(set(cls), DEFAULT_KEYS, cls['name'])
        self.assertEqual(settings['netclass_assignments'], {})
        self.assertEqual(sorted(p['pattern'] for p in settings['netclass_patterns']), ['\\/IN', '\\/OUT'])
        self.assertNotIn('GND', record['nets'])

    def test_literal_patterns_escape_regex_and_wildcards(self):
        self.assertEqual(exact_pattern('USB_D+'), 'USB_D\\+')
        self.assertEqual(exact_pattern('N*1?'), 'N\\*1\\?')
        self.assertEqual(exact_pattern('/A.B'), '\\/A\\.B')

    def test_diff_pair_and_impedance_with_stackup_are_recorded_not_enforced(self):
        doc = document(classes=[{'name': 'USB', 'requirement': 'R-usb', 'nets': ['USB_D-', 'USB_D+'],
                                 'track_width_mm': 0.2, 'diff_pair': {'gap_mm': 0.15}, 'impedance_ohm': 90}])
        del doc['temperature_rise_c']
        artifacts, record = run(doc)
        self.assertEqual(record['outcome'], 'pass')
        usb = record['classes'][0]
        self.assertEqual(usb['impedance']['status'], 'recorded_not_enforced')
        self.assertEqual((usb['diff_pair']['positive'], usb['diff_pair']['negative']), ('USB_D+', 'USB_D-'))
        cls = json.loads(artifacts['project'])['net_settings']['classes'][1]
        self.assertEqual((cls['diff_pair_width'], cls['diff_pair_gap'], cls['track_width']), (0.2, 0.15, 0.2))

    def test_impedance_without_stackup_is_unknown_without_project_bytes(self):
        doc = document(classes=[{'name': 'USB', 'requirement': 'R-usb', 'nets': ['USB_D+', 'USB_D-'],
                                 'impedance_ohm': 90}])
        del doc['temperature_rise_c']
        artifacts, record = run(doc, envelope=None)
        self.assertEqual(record['outcome'], 'unknown')
        self.assertEqual(record['findings'], [{'code': 'impedance_without_stackup', 'class': 'USB'}])
        self.assertEqual(set(artifacts), {'record'})
        self.assertIsNone(record['project_sha256'])

    def test_current_without_stackup_or_outside_fit_is_unknown(self):
        _, record = run(document(), envelope=None)
        self.assertEqual(record['findings'], [{'code': 'current_without_stackup', 'class': 'POWER_IN'}])
        for rise, current in ((1, 2000), (10, 30001)):
            doc = document(temperature_rise_c=rise)
            doc['classes'][0]['current_ma'] = current
            artifacts, record = run(doc)
            self.assertEqual((record['outcome'], record['findings'][0]['code']), ('unknown', 'outside_fit_range'))
            self.assertNotIn('project', artifacts)

    def test_explicit_width_wins_and_rounds_up_exactly(self):
        doc = document()
        doc['classes'][0]['track_width_mm'] = 1.1
        _, record = run(doc)
        self.assertEqual(record['classes'][0]['min_track_width_mm'], 1.1)
        doc['classes'][0]['track_width_mm'] = 0.5
        _, record = run(doc)
        self.assertEqual(record['classes'][0]['min_track_width_mm'], 1.012)

    def test_template_rules_are_extended(self):
        base = b'(version 1)\n(rule "owner" (constraint clearance (min 0.3mm)))\n'
        artifacts, record = run(document(), rules=base)
        self.assertTrue(artifacts['rules'].startswith(base))
        self.assertIsNotNone(record['template_rules_sha256'])
        with self.assertRaisesRegex(ValueError, 'reserved rule'):
            run(document(), rules=base + b'(rule agent_ee_netclass_X (constraint track_width (min 1mm)))\n')
        for bad in (b'(rule "a")', b'(version 1)\n(rule "a"', b'(version 1))'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                run(document(), rules=bad)

    def test_deterministic(self):
        self.assertEqual(run(document())[0], run(document())[0])

    def test_refusals(self):
        base = document()
        cases = {
            'duplicate net': [dict(base['classes'][0]), {**base['classes'][1], 'nets': ['/IN']}],
            'reserved Default': [{**base['classes'][0], 'name': 'default'}],
            'no constraint': [{'name': 'X', 'requirement': 'R', 'nets': ['A']}],
            'unknown field': [{**base['classes'][0], 'width': 1}],
            'bad pair': [{'name': 'P', 'requirement': 'R', 'nets': ['A', 'B'], 'diff_pair': {'gap_mm': 0.1}}],
            'quoted net': [{**base['classes'][0], 'nets': ["A'B"]}],
            'zero current': [{**base['classes'][0], 'current_ma': 0}],
            'huge width': [{**base['classes'][0], 'track_width_mm': 1e308}],
            'boolean width': [{**base['classes'][0], 'track_width_mm': True}],
            'no requirement': [{k: v for k, v in base['classes'][0].items() if k != 'requirement'}],
        }
        for label, classes in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                run(document(classes=classes))
        no_rise = document()
        del no_rise['temperature_rise_c']
        for bad in (no_rise, document(schema='agent-ee.net-constraints/2'), document(extra=1)):
            with self.assertRaises(ValueError):
                run(bad)
        with self.assertRaises(ValueError):
            run(b'{"schema": "agent-ee.net-constraints/1", "temperature_rise_c": NaN, "classes": []}')

    def test_template_must_be_qualified_default_only(self):
        project = json.loads(TEMPLATE)
        variants = []
        for mutate in (lambda s: s['classes'][0].pop('via_drill'),
                       lambda s: s['classes'].append({**s['classes'][0], 'name': 'Other'}),
                       lambda s: s.update(netclass_patterns=[{'netclass': 'Default', 'pattern': 'X'}]),
                       lambda s: s['meta'].update(version=4)):
            variant = copy.deepcopy(project)
            mutate(variant['net_settings'])
            variants.append(json.dumps(variant).encode())
        for raw in variants:
            with self.assertRaises(ValueError):
                run(document(), template=raw)

    def test_net_minimums_bind_record_to_project(self):
        artifacts, _ = run(document())
        with tempfile.TemporaryDirectory() as tmp:
            record, project = Path(tmp, 'record.json'), Path(tmp, 'board.kicad_pro')
            record.write_bytes(artifacts['record'])
            project.write_bytes(artifacts['project'])
            self.assertEqual(net_minimums(record, project), {'/IN': 1.012, '/OUT': 0.25})
            data = json.loads(artifacts['project'])
            data['net_settings']['classes'][1]['track_width'] = 0.25
            project.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'below the recorded minimum'):
                net_minimums(record, project)
            data = json.loads(artifacts['project'])
            data['net_settings']['netclass_patterns'][0]['pattern'] = '/IN'
            project.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'does not carry'):
                net_minimums(record, project)
            unknown = json.loads(artifacts['record'])
            unknown['outcome'] = 'unknown'
            record.write_text(json.dumps(unknown))
            with self.assertRaisesRegex(ValueError, 'passing'):
                net_minimums(record, project)

    def test_capability_requires_copper_width_and_captured_inputs(self):
        files = {'b.kicad_pcb': 'x', 'b.kicad_pro': 'x', 'r.json': 'x'}
        scope = {'board': 'b.kicad_pcb'}
        width = {'criterion': 'copper-width/1', 'covered_primitives': ['PCB_TRACK', 'PCB_ARC'],
                 'min_track_width_mm': 0.2, 'exclude_nets': [], 'net_constraints': 'r.json'}
        for params, files_, ok in (({'net_constraints': 'r.json'}, files, False),
                                   (width, {k: v for k, v in files.items() if k != 'b.kicad_pro'}, False),
                                   ({**width, 'net_constraints': 'missing.json'}, files, False),
                                   (width, files, True)):
            spec = {'id': 'w', 'kind': 'geometry', 'params': params}
            descriptor = describe_check(spec, check_specs=[spec], files=files_, scope=scope)
            self.assertEqual(descriptor['status'] == 'supported', ok, descriptor)
            if ok:
                self.assertEqual(sorted(descriptor['immutable_inputs']), ['b.kicad_pro', 'r.json'])

    def test_coverage_applies_net_minimum_and_tolerates_malformed_nets(self):
        params = {'criterion': 'copper-width/1', 'covered_primitives': ['PCB_TRACK', 'PCB_ARC'],
                  'min_track_width_mm': 0.2, 'exclude_nets': []}
        item = {'uuid': 'u', 'native_class': 'PCB_TRACK', 'layer': 'F.Cu', 'width_nm': 1011999}
        result = checks.copper_width_coverage(
            {'tracks': [{**item, 'net': '/IN'}, {**item, 'net': ['/IN']}, {**item, 'net': '/OUT'}],
             'zones': []}, params, {'/IN': 1.012})
        self.assertEqual([p['limit_mm'] for p in result['primitives']], [1.012, 0.2, 0.2])
        self.assertEqual([p['net'] for p in result['offending_primitives']], ['/IN'])
        self.assertEqual(result['counts']['unsupported'], 1)

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'board.kicad_pro').write_bytes(TEMPLATE)
            argv = [sys.executable, '-m', 'agent_ee.net_constraints', str(CONSTRAINTS),
                    '--project', str(root / 'board.kicad_pro')]
            env = {'PYTHONPATH': str(REPO / 'src')}
            ok = subprocess.run(argv + ['--envelope', str(REPO / 'fixtures/board-envelope/rc.envelope.json'),
                                        '--out', str(root / 'ok')], env=env, capture_output=True)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual(sorted(p.name for p in (root / 'ok').iterdir()),
                             ['board.kicad_dru', 'board.kicad_pro', 'net-constraints.record.json'])
            unknown = subprocess.run(argv + ['--out', str(root / 'unknown')], env=env, capture_output=True)
            self.assertEqual(unknown.returncode, 1, unknown.stderr)
            self.assertEqual([p.name for p in (root / 'unknown').iterdir()], ['net-constraints.record.json'])
            refused = subprocess.run(argv + ['--out', str(root / 'ok')], env=env, capture_output=True)
            self.assertEqual(refused.returncode, 2)


class NativeNetclassTests(unittest.TestCase):
    """The routed RC fixture with emitted project files, judged by native KiCad 10."""

    def setUp(self):
        require_toolchain(native=True)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def project(self, in_width=None):
        source = self.root / 'source'
        shutil.copytree(REPO / 'fixtures/rc/control', source)
        artifacts = emit(CONSTRAINTS.read_bytes(), TEMPLATE, envelope_raw=ENVELOPE)
        (source / 'rc_filter.kicad_pro').write_bytes(artifacts['project'])
        (source / 'rc_filter.kicad_dru').write_bytes(artifacts['rules'])
        (source / 'net-constraints.record.json').write_bytes(artifacts['record'])
        (source / 'requirements.md').write_text('R-width: /IN carries 2 A; /OUT tracks at least 0.25 mm.')
        if in_width is not None:
            script = '''import pcbnew, sys
b = pcbnew.LoadBoard(sys.argv[1])
for t in b.GetTracks():
    if t.GetNetname() == "/IN": t.SetWidth(pcbnew.FromMM(float(sys.argv[2])))
pcbnew.SaveBoard(sys.argv[1], b)
'''
            done = subprocess.run(['/usr/bin/python3', '-c', script, str(source / 'rc_filter.kicad_pcb'),
                                   str(in_width)], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
        return source

    def review(self, source, params=None):
        width = {'criterion': 'copper-width/1', 'covered_primitives': ['PCB_TRACK', 'PCB_ARC'],
                 'min_track_width_mm': 0.2, 'exclude_nets': [],
                 'net_constraints': 'project/net-constraints.record.json', **(params or {})}
        stock = [i for i in read_json(REPO / 'fixtures/rc/control.scope.json')['inputs']
                 if i['at'].startswith('stock/')]
        scope = {'schema': 'agent-ee.scope/2', 'inputs': [{'path': str(source), 'at': 'project'}, *stock],
                 'schematic': 'project/rc_filter.kicad_sch', 'board': 'project/rc_filter.kicad_pcb',
                 'checks': [{'id': 'drc', 'kind': 'drc'}, {'id': 'width', 'kind': 'geometry', 'params': width}],
                 'baseline': {'requirements': [{'id': 'R-width', 'statement': 'Netclass widths from intent',
                     'source': {'path': 'project/requirements.md', 'locator': 'R-width'},
                     'checks': ['drc', 'width']}], 'targets': ['project/requirements.md'],
                     'reference_architectures': ['project/requirements.md']}}
        (self.root / 'scope.json').write_text(json.dumps(scope))
        run_dir = self.root / 'run'
        run_dir.mkdir()
        candidate.capture(self.root / 'scope.json', run_dir)
        return run_dir, checks.run_checks(run_dir, time.monotonic() + 300)

    def test_drc_and_copper_width_flag_track_below_netclass_minimum(self):
        run_dir, (drc, width) = self.review(self.project())
        report = json.loads((run_dir / 'work/drc/drc.json').read_text())
        flagged = [v for v in report['violations'] if v['type'] == 'track_width']
        self.assertEqual(len(report['violations']), 1, report['violations'])
        self.assertIn("rule 'agent_ee_netclass_POWER_IN' min width 1.0120 mm; actual 0.2500 mm",
                      flagged[0]['description'])
        self.assertEqual((drc['execution'], drc['outcome']), ('ok', 'defect'))
        self.assertEqual((width['execution'], width['outcome']), ('ok', 'defect'), width)
        offenders = width['details']['offending_primitives']
        self.assertEqual({(p['net'], p['limit_mm']) for p in offenders}, {('/IN', 1.012)})
        limits = {p['net']: p['limit_mm'] for p in width['details']['primitives']}
        self.assertEqual(limits, {'/IN': 1.012, '/OUT': 0.25, 'GND': 0.2})
        self.assertEqual(width['details']['net_constraints']['record'], 'project/net-constraints.record.json')
        freeze_evidence(run_dir, [drc, width])
        verify_evidence(run_dir, [drc, width], required=True)

    def test_wide_enough_track_passes_and_undeclared_net_stays_default(self):
        source = self.project(in_width=1.012)
        classes = subprocess.run(['/usr/bin/python3', '-c', '''import pcbnew, sys
b = pcbnew.LoadBoard(sys.argv[1])
print(sorted({(t.GetNetname(), t.GetNetClassName()) for t in b.GetTracks()}))''',
                                  str(source / 'rc_filter.kicad_pcb')], capture_output=True, text=True)
        self.assertEqual(classes.returncode, 0, classes.stderr)
        self.assertIn("[('/IN', 'POWER_IN'), ('/OUT', 'SIGNAL'), ('GND', 'Default')]", classes.stdout)
        _, (drc, width) = self.review(source)
        self.assertEqual((drc['execution'], drc['outcome']), ('ok', 'pass'), drc['summary'])
        self.assertEqual((width['execution'], width['outcome']), ('ok', 'pass'), width['summary'])

    def width_descriptor(self, run_dir):
        manifest = json.loads((run_dir / 'candidate.json').read_text())
        spec = manifest['scope']['checks'][1]
        return describe_check(spec, check_specs=manifest['scope']['checks'], files=manifest['files'],
                              scope=manifest['scope'], inputs=run_dir / 'inputs')

    def test_tampered_project_cannot_lower_the_recorded_minimum(self):
        source = self.project()
        project = json.loads((source / 'rc_filter.kicad_pro').read_text())
        project['net_settings']['classes'][1]['track_width'] = 0.25
        (source / 'rc_filter.kicad_pro').write_text(json.dumps(project))
        run_dir, (_, width) = self.review(source)
        self.assertEqual((width['execution'], width['outcome']), ('unsupported', 'unknown'))
        self.assertIn('below the recorded minimum', width['summary'])
        descriptor = self.width_descriptor(run_dir)
        self.assertEqual(descriptor['status'], 'unsupported')
        self.assertIn('below the recorded minimum', descriptor['unsupported_reasons'][0]['message'])

    def test_record_and_project_are_immutable_baseline_inputs(self):
        run_dir, _ = self.review(self.project())
        manifest = json.loads((run_dir / 'candidate.json').read_text())
        baseline = compile_baseline(manifest['scope'], manifest['files'], run_dir / 'inputs')
        self.assertIn('project/net-constraints.record.json', baseline['files'])
        self.assertIn('project/rc_filter.kicad_pro', baseline['files'])
        descriptor = self.width_descriptor(run_dir)
        self.assertEqual(descriptor['status'], 'supported', descriptor)
        self.assertEqual(sorted(descriptor['immutable_inputs']),
                         ['project/net-constraints.record.json', 'project/rc_filter.kicad_pro'])

if __name__ == '__main__':
    unittest.main()
