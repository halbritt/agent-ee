"""Native netclasses/custom rules through the ordinary placement command."""
import copy
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from agent_ee import net_constraints, placement_job as job, routing_rules
from agent_ee.storage import digest, read_json, write_json
from tests.toolchain import require_kicad_route, require_toolchain

ROOT = Path(__file__).resolve().parents[1]
PAIR = ROOT / 'fixtures/telemetry-pod/can-pair'


def prepare(root, checkout):
    """Four-layer software article with two fixed, paired interface terminals."""
    import pcbnew as k
    root.mkdir(parents=True)
    board_path = root / 'interconnect.kicad_pcb'
    subprocess.run([sys.executable, str(PAIR / 'board.py'), str(board_path)], check=True,
                   capture_output=True, timeout=60)
    board = k.LoadBoard(str(board_path))
    board.SetCopperLayerCount(4)
    for fp in board.GetFootprints():
        fp.SetLocked(True)
        fp.Reference().SetVisible(False)
        fp.Value().SetVisible(False)
    k.SaveBoard(str(board_path), board)
    produced = net_constraints.emit((PAIR / 'net-constraints.json').read_bytes(),
        (ROOT / 'fixtures/netclass-completeness/native.kicad_pro').read_bytes(),
        rules_raw=(PAIR / 'gap.kicad_dru').read_bytes())
    board_path.with_suffix('.kicad_pro').write_bytes(produced['project'])
    rules = produced['rules']
    for constraint in routing_rules.compile_routing(read_json(PAIR / 'routing-rules.json'))['constraints']:
        rules += constraint['rules'].split('\n', 1)[1].encode()
    board_path.with_suffix('.kicad_dru').write_bytes(rules)
    write_json(root / 'intent.json', dict(fixed_poses=[], edge_connectors=[], proximity=[]))
    write_json(root / 'groups.json', dict(groups=[dict(name='interface', anchor='J1', refs=['J1', 'J2'])]))
    files = dict(board=board_path, project=board_path.with_suffix('.kicad_pro'),
                 rules=board_path.with_suffix('.kicad_dru'), intent=root / 'intent.json', groups=root / 'groups.json')
    config = dict(schema=job.CONFIG_SCHEMA, budget_s=240,
        inputs={name: dict(path=str(path), sha256=digest(path)) for name, path in files.items()},
        search=dict(seeds=[dict(seed=0, start='scattered')], steps_per_seed=10, shortlist=1),
        router=dict(checkout=str(checkout), grid_step_mm=0.025))
    write_json(root / 'job.json', config)
    return config


def public_job(config_path, out, *extra):
    done = subprocess.run([sys.executable, '-m', 'agent_ee.cli', 'place', 'run', str(config_path),
                           '--out', str(out), *extra], capture_output=True, text=True, timeout=270)
    (out.parent / (out.name + '.stdout')).write_text(done.stdout)
    (out.parent / (out.name + '.stderr')).write_text(done.stderr)
    return done


class NativeRuleJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_toolchain(native=True)
        checkout = require_kicad_route()
        cls.temp = tempfile.TemporaryDirectory(prefix='ee-native-rules-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(os.environ.get('AGENT_EE_RULE_JOB_EVIDENCE', cls.temp.name))
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.config = prepare(cls.root / 'inputs', checkout)
        cls.done = public_job(cls.root / 'inputs/job.json', cls.root / 'job')
        cls.report = read_json(cls.root / 'job/job-report.json')

    def test_public_four_layer_pair_keeps_authored_rules_and_geometry(self):
        import pcbnew as k
        self.assertEqual(self.done.returncode, 0, self.done.stdout + self.done.stderr)
        report = self.report
        self.assertEqual(report['status'], 'completed', report)
        self.assertEqual(report['final']['opens'], 0)
        self.assertEqual(report['final']['identity_problems'], [])
        final = Path(report['final']['board'])
        board = k.LoadBoard(str(final))
        self.assertEqual(board.GetCopperLayerCount(), 4)
        self.assertEqual({t.GetWidth() for t in board.GetTracks()}, {250000})
        self.assertEqual({t.GetNetClassName() for t in board.GetTracks()}, {'CAN'})
        self.assertEqual({fp.GetReference(): (fp.GetPosition().x, fp.GetPosition().y)
                          for fp in board.GetFootprints()}, {'J1': (45000000, 50000000), 'J2': (55000000, 50000000)})
        for key, suffix in [('project', '.kicad_pro'), ('rules', '.kicad_dru')]:
            expected = self.config['inputs'][key]['sha256']
            for path in [final.with_suffix(suffix), Path(report['route']['board']).with_suffix(suffix),
                         Path(report['route_selection']['directory']) / ('board' + suffix)]:
                self.assertEqual(digest(path), expected, path)
        self.assertTrue(all(digest(entry['path']) == entry['sha256'] for entry in self.config['inputs'].values()))

    def test_missing_or_wrong_native_companion_is_refused_before_routing(self):
        for name, change in [('missing', 'omit'), ('wrong-rules', 'rules'), ('wrong-project', 'project')]:
            with self.subTest(name=name):
                config = copy.deepcopy(self.config)
                if change == 'omit':
                    del config['inputs']['rules']
                else:
                    original = Path(config['inputs'][change]['path'])
                    other = self.root / ('other' + original.suffix)
                    shutil.copyfile(original, other)
                    config['inputs'][change]['path'] = str(other)
                path = self.root / (name + '.json')
                write_json(path, config)
                out = self.root / name
                done = public_job(path, out)
                self.assertNotEqual(done.returncode, 0)
                report = read_json(out / 'job-report.json')
                self.assertEqual(report['status'], 'refused')
                self.assertIn('rules' if change == 'omit' else 'native board sibling', report['reason'])
                self.assertNotIn('route', report)

    def final_check(self, board, label):
        import pcbnew as k
        report = self.report
        self.assertEqual(report['status'], 'completed', report)
        placed = read_json(self.root / 'job/routed-incumbent.json')['placement']
        owners = job._owners(k.LoadBoard(placed['board']))
        routed = dict(status='routed', router_accepted=True, board=str(board))
        result = job.final_checks(routed, placed, owners, set(), self.root / label,
            job.normalize_intent(read_json(self.config['inputs']['intent']['path']),
                                 read_json(self.config['inputs']['groups']['path'])))
        return result, job._completion_status(routed, placed, result)

    def test_missing_or_changed_routed_rules_cannot_be_hidden_by_final_copy(self):
        self.assertEqual(self.report['status'], 'completed', self.report)
        source = Path(self.report['final']['board'])
        for name, suffix, content in [('missing', '.kicad_dru', None),
                                      ('changed', '.kicad_dru', b'(version 1)\n'),
                                      ('project', '.kicad_pro', b'{}\n')]:
            with self.subTest(name=name):
                directory = self.root / ('mutated-' + name)
                directory.mkdir()
                board = directory / source.name
                for ext in ('.kicad_pcb', '.kicad_pro', '.kicad_dru'):
                    shutil.copyfile(source.with_suffix(ext), board.with_suffix(ext))
                if content is None:
                    board.with_suffix(suffix).unlink()
                else:
                    board.with_suffix(suffix).write_bytes(content)
                final, status = self.final_check(board, 'verify-' + name)
                self.assertEqual(status, 'integrity_failed')
                self.assertIn('router changed or lost native ' + suffix + ' file', final['identity_problems'])
                self.assertEqual(digest(Path(final['board']).with_suffix('.kicad_dru')),
                                 self.config['inputs']['rules']['sha256'])

    def test_native_skew_violation_rejects_fully_connected_copper(self):
        self.assertEqual(self.report['status'], 'completed', self.report)
        import importlib.util
        spec = importlib.util.spec_from_file_location('can_probe', PAIR / 'probe.py')
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        source = Path(self.report['final']['board'])
        directory = self.root / 'longer-leg'
        directory.mkdir()
        board = directory / 'board.kicad_pcb'
        subprocess.run([sys.executable, '-c', probe.LENGTHEN, str(source), str(board)],
                       check=True, capture_output=True, timeout=60)
        for suffix in ('.kicad_pro', '.kicad_dru'):
            shutil.copyfile(source.with_suffix(suffix), board.with_suffix(suffix))
        final, status = self.final_check(board, 'verify-skew')
        self.assertEqual(final['opens'], 0)
        self.assertEqual(final['integrity'], 'preserved')
        self.assertEqual(status, 'verification_failed')
        native = read_json(Path(final['board']).parent / 'native-drc.json')
        self.assertTrue(any(v['type'] == 'skew_out_of_range' and '5.0000 mm' in v['description']
                            for v in native['violations']), native)

    def test_changed_rules_refuse_resume_of_cancelled_job(self):
        inputs = self.root / 'resume-inputs'
        config = prepare(inputs, self.config['router']['checkout'])
        out = self.root / 'resume-job'
        out.mkdir()
        (out / 'CANCEL').touch()
        done = public_job(inputs / 'job.json', out)
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(read_json(out / 'job-report.json')['status'], 'cancelled')
        checkpoint = (out / 'checkpoint.json').read_bytes()
        (out / 'CANCEL').unlink()
        rules = Path(config['inputs']['rules']['path'])
        rules.write_bytes(rules.read_bytes().replace(b'15.1mm', b'14mm'))
        config['inputs']['rules']['sha256'] = digest(rules)
        write_json(inputs / 'changed.json', config)
        resumed = public_job(inputs / 'changed.json', out, '--resume')
        self.assertNotEqual(resumed.returncode, 0)
        self.assertIn('different job identity', resumed.stdout + resumed.stderr)
        self.assertEqual((out / 'checkpoint.json').read_bytes(), checkpoint)

    def test_public_job_rejects_unmet_native_length_and_malformed_rule(self):
        for name, old, new in [('short-limit', b'15.1mm', b'9mm'),
                               ('malformed', b'constraint length', b'constraint not_a_native_constraint')]:
            with self.subTest(name=name):
                inputs = self.root / (name + '-inputs')
                config = prepare(inputs, self.config['router']['checkout'])
                rules = Path(config['inputs']['rules']['path'])
                raw = rules.read_bytes()
                self.assertIn(old, raw)
                rules.write_bytes(raw.replace(old, new))
                config['inputs']['rules']['sha256'] = digest(rules)
                write_json(inputs / 'job.json', config)
                out = self.root / (name + '-job')
                done = public_job(inputs / 'job.json', out)
                self.assertNotEqual(done.returncode, 0)
                report = read_json(out / 'job-report.json')
                self.assertNotEqual(report['status'], 'completed')
                self.assertFalse((out / 'routed-incumbent.json').exists())
                if name == 'short-limit':
                    self.assertEqual(report['final']['opens'], 0, report)
                    self.assertEqual(report['final']['integrity'], 'preserved', report)
                    native = read_json(Path(report['final']['board']).parent / 'native-drc.json')
                    self.assertTrue(any(v['type'] == 'length_out_of_range' for v in native['violations']))
                else:
                    self.assertIn('native custom rules failed loading witness', report['reason'])
                    self.assertNotIn('route', report)

    def test_build_placement_stage_captures_native_rules_automatically(self):
        from agent_ee import build_workflow
        out = self.root / 'build-stage'
        inputs = out / 'inputs'
        config = prepare(inputs, self.config['router']['checkout'])
        requirements = out / 'requirements'
        requirements.mkdir()
        write_json(requirements / 'scope.json', dict(board='interconnect.kicad_pcb', checks=[]))
        write_json(requirements / 'accounting.json', dict(requirements=[]))
        stage = out / 'placement-routing'
        stage.mkdir()
        plan = dict(placement=dict(budget_s=config['budget_s'], search=config['search'],
            router=config['router'], intent='intent.json', groups='groups.json'))
        # A stage-boundary test, explicitly diagnostic: this software article
        # has no schematic or manufacturing requirement map.
        candidate = build_workflow._execute('placement-routing', plan, inputs, stage,
            [dict(stage='requirements', directory='requirements')], out, diagnostic=True)
        captured = read_json(stage / 'placement-config.json')['inputs']['rules']
        self.assertEqual(captured['sha256'], config['inputs']['rules']['sha256'])
        self.assertEqual(digest(candidate / 'interconnect.kicad_dru'), captured['sha256'])
        report = read_json(stage / 'placement/job-report.json')
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(digest(candidate / 'interconnect.kicad_pcb'), report['final']['board_sha256'])


if __name__ == '__main__':
    unittest.main()
