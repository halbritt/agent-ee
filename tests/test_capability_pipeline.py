"""Native scope/3 corpus with independent oracles and scripted agent transport.

This establishes coordinator acceptance behavior, not live-model reliability.
The expectation file is committed separately before corpus execution. Agent
responses never supply capability, tool results, or the expected violations.
"""
import contextlib
import io
import math
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_ee.baseline import load_baseline, validate_witnesses
from agent_ee.capabilities import convert_scope
from agent_ee.cli import run_review, verify
from agent_ee.storage import digest, read_json, write_json
from toolchain import require_toolchain

REPO = Path(__file__).resolve().parents[1]
ORACLE = REPO / 'tests/fixtures/capability-mutations.json'
# Independently specified engineering identity, not generated from the coverage report.
CHECK_REQUIREMENTS = {'erc': 'R-native', 'drc': 'R-native',
                      'geometry': 'R-width', 'rc-step': 'R-response'}
PROFILE_CALL = {'harness': 'codex', 'model': 'scripted-transport', 'effort': 'high', 'timeout_s': 30}


class NativeCapabilityPipelineTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(native=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-ee-capability-corpus-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.oracle_hash = digest(ORACLE)
        self.oracle = read_json(ORACLE)
        self.calls = []

    def prepare(self, name):
        root = self.root / name
        root.mkdir()
        project = root / 'project'
        fixture = 'seeded' if name == 'wrong_rc' else 'control'
        shutil.copytree(REPO / f'fixtures/rc/{fixture}', project)
        pro = project / 'rc_filter.kicad_pro'
        config = read_json(pro)
        config['board']['design_settings']['rules']['min_track_width'] = .2
        write_json(pro, config)
        sch = project / 'rc_filter.kicad_sch'
        board = project / 'rc_filter.kicad_pcb'
        if name == 'unsupported_value':
            sch.write_text(sch.read_text().replace('"10k"', '"4k7"'))
            board.write_text(board.read_text().replace('"10k"', '"4k7"'))
        elif name == 'rule_defect':
            # Remove the electrical driving authority of the captured power flags.
            sch.write_text(sch.read_text().replace('(pin power_out line', '(pin power_in line'))
        elif name == 'malformed_schematic':
            sch.write_text('(kicad_sch malformed')
        elif name in ('thin_track', 'thin_arc'):
            mutation = '''import pcbnew, sys
p, kind = sys.argv[1:]
b = pcbnew.LoadBoard(p)
if kind == 'thin_track':
    next(iter(b.GetTracks())).SetWidth(pcbnew.FromMM(.05))
else:
    arc = pcbnew.PCB_ARC(b)
    arc.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(10), pcbnew.FromMM(10)))
    arc.SetMid(pcbnew.VECTOR2I(pcbnew.FromMM(11), pcbnew.FromMM(11)))
    arc.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(12), pcbnew.FromMM(10)))
    arc.SetWidth(pcbnew.FromMM(.05))
    arc.SetLayer(pcbnew.F_Cu)
    arc.SetNetCode(next(p.GetNetCode() for f in b.GetFootprints() for p in f.Pads() if p.GetNetname() == '/OUT'))
    b.Add(arc)
pcbnew.SaveBoard(p, b)
'''
            completed = subprocess.run(['/usr/bin/python3', '-c', mutation, str(board), name],
                                       capture_output=True, text=True, timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stderr)
        template = REPO / 'fixtures/closed-loop/control.scope.json'
        scope = convert_scope(read_json(template))
        for item in scope['inputs']:
            item['path'] = str((template.parent / item['path']).resolve())
        scope['inputs'][0]['path'] = str(project)
        scope['review_profile'] = {'schema': 'agent-ee.profile/1', 'name': 'scripted-native-corpus',
            'reviewers': [{**PROFILE_CALL, 'role': 'circuit'}, {**PROFILE_CALL, 'role': 'physical'}],
            'adjudicator': PROFILE_CALL}
        geometry = next(c for c in scope['checks'] if c['id'] == 'geometry')
        geometry['params'].update(criterion='copper-width/1',
                                 covered_primitives=['PCB_TRACK', 'PCB_ARC'], exclude_nets=[])
        # Netlist export remains visible under an explicitly informational obligation.
        scope['baseline']['requirements'][0]['checks'].remove('netlist')
        scope['baseline']['requirements'].append({'id': 'I-netlist', 'intent': 'informational',
            'statement': 'Retain native connectivity', 'checks': ['netlist'],
            'source': {'path': 'requirements/system.md', 'locator': 'R-native'}})
        write_json(root / 'scope.json', scope)
        return root

    def agent(self, run, label, definition, prompt, schema, timeout):
        run = Path(run)
        self.calls.append((run.name, label))
        packet = read_json(run / 'packet.json')
        identity = {key: packet[key] for key in ('candidate_id', 'run_id')}
        if label == 'adjudicator':
            # Scripted adjudication of the coordinator findings, never the oracle.
            checks = [read_json(p) for p in sorted((run / 'checks').glob('*.json'))]
            decisions = [{'finding_id': 'check/' + c['id'], 'disposition': 'accepted',
                'reason': 'Sealed coordinator criterion violation',
                'evidence': [{'path': 'checks/' + c['id'] + '.json', 'locator': 'outcome'}]}
                for c in checks if c['execution'] == 'ok' and c['outcome'] == 'defect']
            return {'execution': 'ok'}, {'schema': 'agent-ee.decisions/1', **identity, 'decisions': decisions}
        self.assertIn(label, ('circuit', 'physical'))
        self.assertNotIn('"initial_reviews"', prompt)
        self.assertNotIn('"history"', prompt)
        self.assertEqual(packet['baseline']['schema'], 'agent-ee.baseline/2')
        self.assertEqual(packet['baseline'], load_baseline(run))
        return {'execution': 'ok'}, {'schema': 'agent-ee.review/1', **identity,
            'packet_sha256': packet['packet_sha256'], 'role': label,
            'coverage': ['Scripted independent initial review fixture'],
            'limitations': ['No model inference; native coordinator contract test only'], 'findings': []}

    def test_independent_native_control_mutation_acceptance_corpus(self):
        for name, expected in self.oracle['cases'].items():
            with self.subTest(case=name):
                root = self.prepare(name)
                with patch('agent_ee.reviewers.reviewer_call', side_effect=self.agent), \
                     contextlib.redirect_stdout(io.StringIO()):
                    status = run_review(root / 'scope.json', root / 'run', 180)
                result = verify(root / 'run')
                self.assertEqual(status, 0 if expected['outcome'] == 'criteria_satisfied' else 1, result)
                self.assertEqual(result['outcome'], expected['outcome'], result)
                self.assertTrue(result['adjudicated'], result)
                self.assertEqual([label for run, label in self.calls[-3:]],
                                 ['circuit', 'physical', 'adjudicator'])
                self.assertEqual([c['id'] for c in result['checks'] if c['execution'] != 'ok'],
                                 expected['unknown_checks'], result['checks'])
                accepted = [f for f in result['findings'] if f.get('status') == 'accepted']
                actual = sorted({CHECK_REQUIREMENTS[f['local_id']] for f in accepted})
                self.assertEqual(actual, sorted(expected['violated_requirements']), result['findings'])
                self.assertTrue(all(f['source'] == 'check' for f in accepted))
                for cid in expected['unknown_checks']:
                    check = next(c for c in result['checks'] if c['id'] == cid)
                    self.assertEqual(check['outcome'], 'unknown', check)
                    self.assertNotIn('check/' + cid, {f['id'] for f in accepted})
                if name in ('control', 'wrong_rc'):
                    response = next(c for c in result['checks'] if c['id'] == 'rc-step')
                    resistance = 100_000 if name == 'wrong_rc' else 10_000
                    oracle_voltage = 1 - math.exp(-.001 / (resistance * 100e-9))
                    self.assertIn('margins', response['details'])
                    self.assertAlmostEqual(response['details']['metrics']['vout_1ms']['value'],
                                           oracle_voltage, delta=.0001)
                if name == 'unsupported_value':
                    with self.assertRaisesRegex(ValueError, 'failed criterion'):
                        validate_witnesses(root / 'run', [{'requirement_id': 'R-response', 'check_id': 'rc-step'}],
                            [{'path': 'checks/rc-step.json', 'locator': 'outcome'}])
                self.assertEqual(digest(ORACLE), self.oracle_hash)
