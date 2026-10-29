"""Pure custody/geometry tests; pcbnew and native DRC are external test doubles.

These tests do not establish native clearance, source qualification, or G4.
"""
import copy
from decimal import Decimal
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import patch

from agent_ee import acquisition_local_return as local
from agent_ee.placement_job import Clock, JobRefusal, JobStopped
from agent_ee.symbol_library import children, parse_sexpr


# Selected actual KiCad 10.0.7 metadata from retained ESP raw DRC SHA
# 65640609a4ca36db1c203620f4e44116c0788e481b9ee4ce6417a01a6f41ce8d.
# Its findings are not transplanted and its ignore policy is not acceptance.
DRC_METADATA = {
    '$schema': 'https://schemas.kicad.org/drc.v1.json',
    'kicad_version': '10.0.7', 'coordinate_units': 'mm',
    'included_severities': ['error', 'warning', 'exclusion'],
    'ignored_checks': [
        {'description': 'Footprint has no courtyard defined', 'key': 'missing_courtyard'},
        {'description': 'Track endpoint not centered on via', 'key': 'track_not_centered_on_via'},
        {'description': 'Tuning profile track geometries', 'key': 'tuning_profile_track_geometries'},
        {'description': "Footprint doesn't match symbol's footprint filters", 'key': 'footprint_filters_mismatch'},
        {'description': "Footprint component type doesn't match footprint pads", 'key': 'footprint_type_mismatch'},
    ],
}


def native_report(violations=None):
    return dict(copy.deepcopy(DRC_METADATA), violations=violations or [], schematic_parity=[],
                unconnected_items=[dict(type='unconnected_items', description='synthetic pre-route open',
                                        severity='error', items=[{'uuid': 'open-pad'}])])


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
    def GetPosition(self):
        return self.Pads()[0].GetPosition() if self.Pads() else NS(x=11000000, y=12000000)
    def GetFPID(self): return NS(GetLibNickname=lambda: 'Synthetic_Lib')


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

    def drc_result(self, data, directory):
        (directory / 'drc.json').write_text(json.dumps(data))
        return data, dict(exit_code=0, report_sha256=local._sha((directory / 'drc.json').read_bytes()))

    def clear_drc(self, board, directory, **kwargs):
        local._check_stop(kwargs['clock'], kwargs['cancel_dir'])
        data = native_report()
        return self.drc_result(data, directory)

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
        self.assertEqual(record['trials'][0]['coverage']['ignored_checks'], DRC_METADATA['ignored_checks'])
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
            if directory.name == 'trial-1':
                data['violations'] = [dict(type='clearance', severity='error', description='synthetic obstacle', items=[{'uuid': 'obstacle'}])]
                (directory / 'drc.json').write_text(json.dumps(data))
            calls.append(directory)
            return self.drc_result(data, directory)
        self.drc.side_effect = collision_then_clear
        record = self.run_seed()
        self.assertEqual([t['status'] for t in record['trials']], ['rejected', 'clearance_pass'])
        self.assertTrue((self.output / 'trial-1' / 'drc.json').is_file())
        self.assertNotEqual(record['trials'][0]['path_nm'], record['trials'][1]['path_nm'])

    def test_all_collisions_or_unknown_drc_never_publish(self):
        reports = [native_report([dict(type='clearance', severity='error', description='collision', items=[])]),
                   native_report([dict(type='edge_clearance', severity='warning', description='edge', items=[])]),
                   native_report([dict(type='unfamiliar_warning', severity='warning', description='unknown', items=[])])]
        for key in ('$schema', 'included_severities', 'ignored_checks'):
            report = native_report()
            del report[key]
            reports.append(report)
        for value in (None, {}, ['clearance'], [{'key': 'clearance'}],
                      [{'key': 'clearance', 'description': 3}],
                      [{'key': 'clearance', 'description': 'Electrical clearance disabled'}],
                      [{'key': 'edge_clearance', 'description': 'Board edge disabled'}],
                      [{'key': 'track_width', 'description': 'Width disabled'}],
                      [{'key': 'unexpected_check', 'description': 'Unqualified ignore'}]):
            report = native_report()
            report['ignored_checks'] = value
            reports.append(report)
        for key, value in (('$schema', 'unknown-schema'), ('kicad_version', '10.0.6'),
                           ('coordinate_units', 'in'), ('unconnected_items', ['malformed']),
                           ('unconnected_items', [dict(type='clearance', severity='error', description='misclassified', items=[])]),
                           ('ignored_checks', [DRC_METADATA['ignored_checks'][0]] * 2)):
            report = native_report()
            report[key] = value
            reports.append(report)
        for severities in (['warning', 'exclusion'], ['error', 'warning'], ['error', 'warning', {}]):
            report = native_report()
            report['included_severities'] = severities
            reports.append(report)
        for category in ('violations', 'unconnected_items', 'schematic_parity'):
            report = native_report()
            report[category] = [dict(type='clearance', severity='exclusion', description='excluded', items=[])]
            reports.append(report)
        for index, report in enumerate(reports):
            with self.subTest(index=index):
                self.output = self.root / f'bad-drc-{index}'
                self.drc.side_effect = lambda board, directory, **k: self.drc_result(report, directory)
                with self.assertRaises(JobRefusal): self.run_seed()
                self.assertFalse((self.output / 'accepted').exists())
                record = json.loads((self.output / 'record.json').read_text())
                self.assertEqual(record['status'], 'refused')
                self.assertLessEqual(len(record['trials']), 5)

    def test_pre_artwork_warnings_are_retained_without_conductive_waiver(self):
        artwork = [dict(type=kind, severity='warning', description='synthetic silk conflict',
                        items=[{'uuid': 'synthetic-field'}]) for kind in sorted(local.DEFERRED_ARTWORK)]
        def reports(board, directory, **kwargs):
            warnings = self.library_warnings() + artwork
            if directory.name != 'baseline':
                warnings += [dict(artwork[1], description='new silk over added copper')]
            return self.drc_result(native_report(warnings), directory)
        self.drc.side_effect = reports
        record = self.run_seed()
        self.assertEqual(record['baseline']['deferred_artwork'], artwork)
        self.assertEqual(len(record['trials'][0]['deferred_artwork']), 3)
        self.assertFalse(record['acceptance_assessed'])
        for severity in ('error', 'exclusion'):
            with self.subTest(severity=severity):
                self.output = self.root / ('fatal-silk-' + severity)
                self.drc.side_effect = lambda b, d, **k: self.drc_result(
                    native_report([dict(artwork[0], severity=severity)]), d)
                with self.assertRaises(JobRefusal): self.run_seed()

    def test_raw_reports_cannot_be_symlinks_or_hardlinks(self):
        for phase in ('baseline', 'trial-1'):
            for kind in ('symlink', 'hardlink'):
                with self.subTest(phase=phase, kind=kind):
                    self.output = self.root / (phase + '-' + kind)
                    def reports(board, directory, **kwargs):
                        data, execution = self.clear_drc(board, directory, **kwargs)
                        if directory.name == phase:
                            path = directory / 'drc.json'
                            other = directory / 'aliased-report.json'
                            path.rename(other)
                            if kind == 'symlink': path.symlink_to(other)
                            else: os.link(other, path)
                        return data, execution
                    self.drc.side_effect = reports
                    with self.assertRaisesRegex(JobRefusal, 'aliased|unaliased'): self.run_seed()
                    self.assertFalse((self.output / 'accepted').exists())

    def test_selected_report_drift_around_copy_prevents_adoption(self):
        original_copy = local.shutil.copytree
        for which in ('trial', 'pending'):
            with self.subTest(which=which):
                self.output = self.root / ('copy-drift-' + which)
                def copy_then_change(source, destination, **kwargs):
                    result = original_copy(source, destination, **kwargs)
                    path = Path(source if which == 'trial' else destination) / 'drc.json'
                    path.write_bytes(path.read_bytes() + b'\n')
                    return result
                with patch.object(local.shutil, 'copytree', side_effect=copy_then_change):
                    with self.assertRaisesRegex(JobRefusal, 'adoption/input drift'): self.run_seed()
                self.assertFalse((self.output / 'accepted').exists())

    def test_post_route_seed_must_survive_with_same_endpoints_and_retained_report(self):
        record = self.run_seed()
        seeded = Path(record['output']) / local.FILES[0]
        routed = self.root / 'routed.kicad_pcb'
        original = seeded.read_bytes()
        routed.write_bytes(original)
        result = local.verify_seed(routed, record, clock=Clock(10))
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['board_sha256'], local._sha(original))
        for data in (original.replace(b'(width 0.25)', b'(width 0.20)'),
                     original.replace(record['trials'][0]['segment_uuids'][0].encode(), b'changed-id'),
                     original.replace(b'(at 34000000 41500000)', b'(at 34000001 41500000)')):
            with self.subTest(data=data):
                routed.write_bytes(data)
                with self.assertRaises(JobRefusal): local.verify_seed(routed, record, clock=Clock(10))
        routed.write_bytes(original)
        raw = seeded.parent / 'drc.json'
        raw.write_bytes(raw.read_bytes() + b'\n')
        with self.assertRaisesRegex(JobRefusal, 'seed report changed'):
            local.verify_seed(routed, record, clock=Clock(10))

    def library_warnings(self):
        return [dict(type='lib_footprint_issues', severity='warning',
            description="The current configuration does not include the footprint library 'Synthetic_Lib'",
            items=[dict(description='Footprint ' + fp.GetReference(), uuid=fp.m_Uuid.AsString(),
                        pos=dict(x=fp.GetPosition().x / 1e6, y=fp.GetPosition().y / 1e6))])
            for fp in load_board(self.source / local.FILES[0]).GetFootprints()]

    def test_fresh_baseline_carries_identical_actual_format_library_multiset(self):
        # 17 synthetic current footprints; actual missing-library diagnostic/item
        # shapes, no old candidate UUIDs/positions or witness errors transplanted.
        additions = b''.join(f'(footprint (reference "L{i}") (uuid "new-L{i}") (layer 0))'.encode()
                             for i in range(15))
        board = self.board.rstrip()[:-1] + additions + b')'
        (self.source / local.FILES[0]).write_bytes(board)
        warnings = self.library_warnings()
        seen = []
        def reports(board_path, directory, **kwargs):
            seen.append(directory.name)
            data = native_report(list(reversed(warnings)) if directory.name != 'baseline' else warnings)
            return self.drc_result(data, directory)
        self.drc.side_effect = reports
        record = self.run_seed()
        self.assertEqual(seen, ['baseline', 'trial-1'])
        self.assertEqual(record['baseline']['library_warning_count'], 17)
        self.assertEqual(record['baseline']['board_sha256'], local._sha(board))
        for directory, receipt in ((self.output / 'baseline', record['baseline']),
                                   (self.output / 'trial-1', record['trials'][0])):
            self.assertEqual(receipt['report_sha256'], local._sha((directory / 'drc.json').read_bytes()))
        self.assertEqual((self.output / 'baseline' / local.FILES[0]).read_bytes(), board)
        self.assertEqual((self.source / local.FILES[0]).read_bytes(), board)

    def test_library_multiset_add_remove_duplicate_or_change_refuses(self):
        warnings = self.library_warnings()
        variants = [warnings[:-1], warnings + [warnings[0]], []]
        for key, value in (('description', 'changed diagnostic'), ('severity', 'error'),
                           ('type', 'clearance')):
            changed = copy.deepcopy(warnings)
            changed[0][key] = value
            variants.append(changed)
        for key, value in (('uuid', 'other-native-uuid'), ('description', 'Footprint other'),
                           ('pos', {'x': 999, 'y': 999})):
            changed = copy.deepcopy(warnings)
            changed[0]['items'][0][key] = value
            variants.append(changed)
        for index, modified in enumerate(variants):
            with self.subTest(index=index):
                self.output = self.root / f'multiset-{index}'
                def reports(board, directory, **kwargs):
                    data = native_report(warnings if directory.name == 'baseline' else modified)
                    return self.drc_result(data, directory)
                self.drc.side_effect = reports
                with self.assertRaises(JobRefusal): self.run_seed()
                self.assertFalse((self.output / 'accepted').exists())
                record = json.loads((self.output / 'record.json').read_text())
                self.assertEqual(len(record['trials']), 5)

    def test_missing_or_duplicate_baseline_records_do_not_match_full_trial(self):
        warnings = self.library_warnings()
        for index, baseline in enumerate(([], warnings[:-1], warnings + [warnings[0]])):
            with self.subTest(index=index):
                self.output = self.root / f'baseline-count-{index}'
                def reports(board, directory, **kwargs):
                    return self.drc_result(native_report(baseline if directory.name == 'baseline' else warnings), directory)
                self.drc.side_effect = reports
                with self.assertRaises(JobRefusal): self.run_seed()
                self.assertFalse((self.output / 'accepted').exists())
                record = json.loads((self.output / 'record.json').read_text())
                self.assertEqual(len(record['trials']), 5)
                self.assertTrue(any(record['trials'][0]['warning_difference'].values()))

    def test_baseline_board_or_report_drift_during_trial_refuses(self):
        for name in (local.FILES[0], 'drc.json'):
            with self.subTest(name=name):
                self.output = self.root / ('baseline-drift-' + name)
                def reports(board, directory, **kwargs):
                    result = self.clear_drc(board, directory, **kwargs)
                    if directory.name == 'trial-1':
                        p = directory.parent / 'baseline' / name
                        p.write_bytes(p.read_bytes() + b'\n')
                    return result
                self.drc.side_effect = reports
                with self.assertRaisesRegex(JobRefusal, 'baseline/input or report drift'): self.run_seed()
                self.assertFalse((self.output / 'accepted').exists())

    def test_unbound_or_forbidden_fresh_baseline_refuses_before_proposals(self):
        base = self.library_warnings()
        cases = []
        for field, value in (('description', 'different library issue'), ('severity', 'error'),
                             ('severity', 'exclusion'), ('type', 'clearance'), ('type', 'unknown_warning')):
            changed = copy.deepcopy(base)
            changed[0][field] = value
            cases.append(changed)
        changed = copy.deepcopy(base)
        changed[0]['items'][0]['uuid'] = 'old-board-uuid'
        cases.append(changed)
        for index, warnings in enumerate(cases):
            with self.subTest(index=index):
                self.output = self.root / f'baseline-bad-{index}'
                self.drc.side_effect = lambda board, directory, **k: self.drc_result(native_report(warnings), directory)
                with self.assertRaises(JobRefusal): self.run_seed()
                record = json.loads((self.output / 'record.json').read_text())
                self.assertEqual(record['trials'], [])
                self.assertFalse((self.output / 'accepted').exists())

    def test_explicit_empty_authored_policy_preserves_bytes_with_real_default_metadata(self):
        path = self.source / local.FILES[1]
        project = json.loads(path.read_bytes())
        project['board']['design_settings'].update(rule_severities={}, drc_exclusions=[])
        data = json.dumps(project, indent=3).encode()
        path.write_bytes(data)
        record = self.run_seed()
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual((Path(record['output']) / local.FILES[1]).read_bytes(), data)
        self.assertEqual(record['trials'][0]['coverage']['ignored_checks'], DRC_METADATA['ignored_checks'])
        self.assertEqual(record['trials'][0]['unconnected_count'], 1)

    def test_authored_suppressions_refuse_without_rewriting_project_or_rules(self):
        original = (self.source / local.FILES[1]).read_bytes()
        for index, (key, value) in enumerate((('rule_severities', {'clearance': 'ignore'}),
                                             ('rule_severities', {'clearance': 'warning'}),
                                             ('rule_severities', []), ('rule_severities', None),
                                             ('drc_exclusions', ['native-exclusion']),
                                             ('drc_exclusions', {}), ('drc_exclusions', None))):
            with self.subTest(key=key, value=value):
                project = json.loads(original)
                project['board']['design_settings'][key] = value
                data = json.dumps(project).encode()
                (self.source / local.FILES[1]).write_bytes(data)
                self.output = self.root / f'suppression-{index}'
                with self.assertRaisesRegex(JobRefusal, 'severity overrides/exclusions'): self.run_seed()
                self.assertEqual((self.source / local.FILES[1]).read_bytes(), data)
                self.assertFalse((self.output / 'accepted').exists())
        (self.source / local.FILES[1]).write_bytes(original)
        for severity in ('ignore', 'warning'):
            self.output = self.root / ('custom-' + severity)
            rules = f'(version 1)\n(rule "suppressed" (severity {severity}) (constraint clearance (min 0.2)))\n'.encode()
            (self.source / local.FILES[2]).write_bytes(rules)
            with self.assertRaisesRegex(JobRefusal, 'custom-rule severity'): self.run_seed()
            self.assertEqual((self.source / local.FILES[2]).read_bytes(), rules)
            self.assertFalse((self.output / 'accepted').exists())

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
        self.assertTrue((self.output / 'baseline' / 'drc.log').is_file())
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
