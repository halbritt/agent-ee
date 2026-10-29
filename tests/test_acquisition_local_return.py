"""Pure custody/geometry tests; pcbnew and native DRC are external test doubles.

These tests do not establish native clearance, source qualification, or G4.
"""
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import acquisition_local_return as local
from agent_ee.placement_job import Clock, JobRefusal, JobStopped
from agent_ee.symbol_library import children, parse_sexpr


def field(node, key):
    return children(node, key)[0][1]


class NativePad:
    def __init__(self, node):
        self.node = node
        self.m_Uuid = NS(AsString=lambda: field(node, 'uuid'))
    def GetNumber(self): return self.node[1]
    def GetAttribute(self): return 1
    def GetNetname(self): return field(self.node, 'net')
    def GetNetCode(self): return {'GND': 7, 'SCL': 8}.get(self.GetNetname(), 0)
    def GetPosition(self):
        row = children(self.node, 'at')[0]
        return NS(x=int(row[1]), y=int(row[2]))
    def IsOnLayer(self, layer): return layer == int(field(self.node, 'layer'))
    def FlashLayer(self, layer): return self.IsOnLayer(layer)


class NativeFootprint:
    def __init__(self, node):
        self.node = node
        self.m_Uuid = NS(AsString=lambda: field(node, 'uuid'))
    def GetReference(self): return field(self.node, 'reference')
    def GetLayer(self): return int(field(self.node, 'layer'))
    def Pads(self): return [NativePad(p) for p in children(self.node, 'pad')]


class NativeTrack:
    def __init__(self, node):
        self.node = node
        self.m_Uuid = NS(AsString=lambda: field(node, 'uuid'))
    def GetClass(self): return 'PCB_TRACK'
    def GetLayer(self): return {'F.Cu': 0, 'B.Cu': 2}[field(self.node, 'layer')]
    def GetNetname(self): return field(self.node, 'net')
    def GetNetCode(self): return {'GND': 7, 'SCL': 8}.get(self.GetNetname(), 0)
    def GetWidth(self): return int(Decimal(field(self.node, 'width')) * 1_000_000)
    def point(self, key):
        row = children(self.node, key)[0]
        return NS(x=int(Decimal(row[1])*1_000_000), y=int(Decimal(row[2])*1_000_000))
    def GetStart(self): return self.point('start')
    def GetEnd(self): return self.point('end')


def load_board(path):
    tree = parse_sexpr(Path(path).read_bytes())
    return NS(GetFootprints=lambda: [NativeFootprint(fp) for fp in children(tree, 'footprint')],
              GetEnabledLayers=lambda: NS(CuStack=lambda: [0, 2]),
              GetTracks=lambda: [NativeTrack(t) for t in children(tree, 'segment')])


class AcquisitionLocalReturnTest(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        self.source = self.root / 'source'
        self.source.mkdir()
        # Shifted synthetic endpoints; these are not rejected/actual board poses.
        self.board = b'''(kicad_pcb (version 20260206)
 (setup (unchanged yes)) (zone (net "GND") (layer "B.Cu"))
 (segment (start 80 90) (end 81 90) (width 0.3) (layer "B.Cu") (net "SCL") (uuid "old-track"))
 (footprint (reference "C3") (uuid "fresh-C") (layer 0)
   (pad "2" (uuid "fresh-C-pad") (net "GND") (layer 0) (at 34000000 41500000)))
 (footprint (reference "U2") (uuid "fresh-U") (layer 0)
   (pad "3" (uuid "fresh-U-pad") (net "GND") (layer 0) (at 35500000 39000000))))\n'''
        (self.source / local.FILES[0]).write_bytes(self.board)
        project = {'board': {'design_settings': {'rules': {'min_clearance': 0.2, 'min_track_width': 0.2}}}}
        (self.source / local.FILES[1]).write_text(json.dumps(project))
        # Mock only pcbnew load and CLI execution, never the route/custody algorithm.
        self.enterContext(patch.dict('sys.modules', pcbnew=NS(LoadBoard=load_board, F_Cu=0, PAD_ATTRIB_SMD=1)))
        self.drc = self.enterContext(patch.object(local, 'drc', side_effect=self.clear_drc))
        self.output = self.root / 'output'

    def clear_drc(self, board, directory, **kwargs):
        local._check_stop(kwargs['clock'], kwargs['cancel_dir'])
        data = dict(violations=[], unconnected_items=[{'expected_pre_route_open': True}])
        (directory / 'drc.json').write_text(json.dumps(data))
        return data, dict(exit_code=0, report_sha256=local._sha((directory / 'drc.json').read_bytes()))

    def run_seed(self, **kwargs):
        args = dict(expected_inputs=local._identity(local._capture(self.source)), clock=Clock(30))
        args.update(kwargs)
        return local.produce(self.source, self.output, **args)

    def test_fresh_endpoints_add_only_trace_preserve_source_and_poses(self):
        record = self.run_seed()
        result = Path(record['output']) / local.FILES[0]
        self.assertEqual(record['status'], 'seeded')
        self.assertFalse(record['acceptance_assessed'])
        self.assertEqual((self.source / local.FILES[0]).read_bytes(), self.board)
        before, after = parse_sexpr(self.board), parse_sexpr(result.read_bytes())
        count = len(record['trials'][0]['segment_uuids'])
        self.assertEqual(after[:-count], before)
        added = after[-count:]
        self.assertEqual(children(added[0], 'start'), [['start', '34.000000', '41.500000']])
        self.assertEqual(children(added[-1], 'end'), [['end', '35.500000', '39.000000']])
        self.assertEqual(record['endpoints'][0]['pad_uuid'], 'fresh-C-pad')
        self.assertEqual(record['endpoints'][1]['net_code'], 7)
        self.assertEqual(record['trials'][0]['unconnected_count'], 1)
        self.assertEqual((result.with_suffix('.kicad_pro')).read_bytes(), (self.source / local.FILES[1]).read_bytes())
        for segment in added:
            self.assertEqual(children(segment, 'width'), [['width', '0.25']])
            self.assertEqual(children(segment, 'net'), [['net', 'GND']])
            self.assertEqual(children(segment, 'layer'), [['layer', 'F.Cu']])

    def test_missing_ambiguous_wrong_net_or_back_endpoints_refuse_without_bundle(self):
        variants = [self.board.replace(b'(reference "C3")', b'(reference "C4")'),
                    self.board.replace(b'(pad "2"', b'(pad "9"'),
                    self.board.replace(b'(net "GND") (layer 0)', b'(net "SCL") (layer 0)', 1),
                    self.board.replace(b'(layer 0)', b'(layer 2)', 1),
                    self.board.replace(b'fresh-U-pad', b'fresh-C-pad'),
                    self.board.replace(b'(reference "U2")', b'(reference "C3")'),
                    self.board.replace(b'(at 34000000 41500000)))',
                        b'(at 34000000 41500000)) (pad "2" (uuid "duplicate")))')]
        for index, board in enumerate(variants):
            with self.subTest(index=index):
                (self.source / local.FILES[0]).write_bytes(board)
                self.output = self.root / f'refused-{index}'
                with self.assertRaises(JobRefusal): self.run_seed()
                self.assertFalse((self.output / 'accepted').exists())
                self.assertEqual(json.loads((self.output / 'record.json').read_text())['status'], 'refused')

    def test_clearance_collision_retains_each_rejection_then_adopts_legal_path(self):
        calls = []
        def collision_then_clear(board, directory, **kwargs):
            data, receipt = self.clear_drc(board, directory, **kwargs)
            if not calls:
                data['violations'] = [dict(type='clearance', severity='error', description='synthetic obstacle')]
                (directory / 'drc.json').write_text(json.dumps(data))
            calls.append(directory)
            return data, receipt
        self.drc.side_effect = collision_then_clear
        record = self.run_seed()
        self.assertEqual([t['status'] for t in record['trials']], ['rejected', 'clearance_pass'])
        self.assertTrue((self.output / 'trial-1' / 'drc.json').is_file())
        self.assertNotEqual(record['trials'][0]['path_nm'], record['trials'][1]['path_nm'])

    def test_all_collisions_or_unknown_drc_never_publish(self):
        for index, report in enumerate((dict(violations=[dict(type='clearance', severity='error')], unconnected_items=[]),
                                        dict(violations=[dict(type='edge_clearance', severity='warning')], unconnected_items=[]),
                                        {}, dict(violations=[dict(type='new', severity='unknown')], unconnected_items=[]))):
            with self.subTest(index=index):
                self.output = self.root / f'bad-drc-{index}'
                self.drc.side_effect = lambda *a, **k: (report, {'exit_code': 0})
                with self.assertRaises(JobRefusal): self.run_seed()
                self.assertFalse((self.output / 'accepted').exists())
                record = json.loads((self.output / 'record.json').read_text())
                self.assertEqual(record['status'], 'refused')
                self.assertLessEqual(len(record['trials']), 5)

    def test_deadline_and_cancel_propagate_without_partial_adoption(self):
        cancel = self.root / 'cancel'
        cancel.mkdir()
        for status in ('deadline', 'cancelled'):
            with self.subTest(status=status):
                self.output = self.root / status
                def stopped(board, directory, **kwargs):
                    if status == 'cancelled':
                        (cancel / 'CANCEL').write_text('stop')
                        local._check_stop(kwargs['clock'], kwargs['cancel_dir'])
                    kwargs['clock'].budget = -1
                    local._check_stop(kwargs['clock'], kwargs['cancel_dir'])
                self.drc.side_effect = stopped
                with self.assertRaises(JobStopped) as raised: self.run_seed(cancel_dir=cancel)
                self.assertEqual(raised.exception.status, status)
                self.assertFalse((self.output / 'accepted').exists())
                self.assertEqual(json.loads((self.output / 'record.json').read_text())['status'], 'stopped')

    def test_native_reload_lost_or_changed_seed_refuses(self):
        for mode in ('missing', 'wrong-width', 'wrong-net'):
            with self.subTest(mode=mode):
                self.output = self.root / mode
                def changed_load(path):
                    board = load_board(path)
                    original = list(board.GetTracks())
                    if Path(path).parent != self.source:
                        seed = original[-1]
                        if mode == 'missing': original.pop()
                        elif mode == 'wrong-width': seed.GetWidth = lambda: 200000
                        else: seed.GetNetCode = lambda: 8
                    board.GetTracks = lambda: original
                    return board
                with patch.dict('sys.modules', pcbnew=NS(LoadBoard=changed_load, F_Cu=0, PAD_ATTRIB_SMD=1)):
                    with self.assertRaisesRegex(JobRefusal, 'native seed'): self.run_seed()
                self.assertFalse((self.output / 'accepted').exists())

    def test_input_or_helper_drift_refuses_before_adoption(self):
        for which in ('board', 'project', 'helper', 'trial'):
            with self.subTest(which=which), tempfile.TemporaryDirectory(dir=self.root) as tmp:
                self.output = Path(tmp) / 'output'
                originals = local._capture(self.source)
                helper = Path(tmp) / 'helper.py'
                helper.write_text('initial')
                def drift(board, directory, **kwargs):
                    report = self.clear_drc(board, directory, **kwargs)
                    if which == 'helper': helper.write_text('changed')
                    elif which == 'trial': Path(board).write_bytes(Path(board).read_bytes() + b'\n')
                    else:
                        p = self.source / local.FILES[0 if which == 'board' else 1]
                        p.write_bytes(p.read_bytes() + b'\n')
                    return report
                self.drc.side_effect = drift
                with patch.object(local, '__file__', str(helper)):
                    with self.assertRaisesRegex(JobRefusal, 'drift'): self.run_seed()
                self.assertFalse((self.output / 'accepted').exists())
                for name, data in originals.items():
                    if data is not None: (self.source / name).write_bytes(data)

    def test_native_drc_execution_failure_is_retained_without_adoption(self):
        def failed(board, directory, **kwargs):
            (directory / 'drc.log').write_text('synthetic CLI failure exit 1')
            raise JobRefusal('native DRC did not complete (exit 1)')
        self.drc.side_effect = failed
        with self.assertRaisesRegex(JobRefusal, 'exit 1'): self.run_seed()
        self.assertTrue((self.output / 'trial-1' / 'drc.log').is_file())
        self.assertFalse((self.output / 'accepted').exists())

    def test_custom_rule_staging_owns_new_directory_and_preserves_captured_rules(self):
        from agent_ee.routing_native import stage_board
        rules = b'(version 1)\n(rule "captured" (constraint clearance (min 0.2)))\n'
        (self.source / local.FILES[2]).write_bytes(rules)
        def witness(source, directory, **kwargs):
            # Exercise the real staging seam; native witness execution is omitted.
            target = stage_board(Path(source), directory, dict(marker='_test_local_return',
                                 rules='(version 1)\n', native_types=[]))
            return dict(status='pass', synthetic_test_double=True,
                        rules_sha256=local._sha(rules), probe=str(target))
        with patch.object(local, 'verify_rule_loading', side_effect=witness):
            record = self.run_seed()
        self.assertEqual((Path(record['output']) / local.FILES[2]).read_bytes(), rules)
        self.assertEqual((self.source / local.FILES[2]).read_bytes(), rules)
        self.assertTrue((self.output / 'rule-loading' / local.FILES[0]).is_file())
        self.assertEqual(record['inputs'][local.FILES[2]], local._sha(rules))

    def test_wrong_clearance_or_rule_loading_failure_never_publish(self):
        project_path = self.source / local.FILES[1]
        project = json.loads(project_path.read_text())
        project['board']['design_settings']['rules']['min_clearance'] = 0.1
        project_path.write_text(json.dumps(project))
        with self.assertRaisesRegex(JobRefusal, '0.2 mm'): self.run_seed()
        self.assertFalse((self.output / 'accepted').exists())
        project['board']['design_settings']['rules']['min_clearance'] = 0.2
        project_path.write_text(json.dumps(project))
        (self.source / local.FILES[2]).write_text('(version 1)')
        self.output = self.root / 'bad-rules'
        # External native rule-loading witness is intentionally unavailable.
        with patch.object(local, 'verify_rule_loading', side_effect=JobRefusal('rule witness unavailable')):
            with self.assertRaisesRegex(JobRefusal, 'rule witness'): self.run_seed()
        self.assertFalse((self.output / 'accepted').exists())

    def test_declared_identity_alias_and_existing_output_refuse(self):
        wrong = local._identity(local._capture(self.source))
        wrong[local.FILES[0]] = '0' * 64
        with self.assertRaisesRegex(JobRefusal, 'identity'): self.run_seed(expected_inputs=wrong)
        self.assertFalse(self.output.exists())
        self.output.mkdir()
        with self.assertRaisesRegex(JobRefusal, 'output'): self.run_seed()
        self.output = self.root / 'alias-output'
        (self.source / local.FILES[1]).rename(self.source / 'original-project')
        (self.source / local.FILES[1]).symlink_to('original-project')
        with self.assertRaisesRegex(JobRefusal, 'alias'): self.run_seed()
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
