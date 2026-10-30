"""Finite multi-leg producer tests; native clearance remains a separate proof."""
from pathlib import Path
from unittest.mock import patch
import unittest

from agent_ee import acquisition_local_return as local
from agent_ee.placement_job import Clock, JobRefusal, JobStopped
from tests import test_acquisition_local_return as fixture
from tests.test_acquisition_local_return import NativePad, NativeTrack, NativeFootprint, native_report


class LocalCopperPathTest(unittest.TestCase):
    def test_rectangle_population_is_bounded_before_vertex_construction(self):
        with self.assertRaisesRegex(JobRefusal,'population'):
            local._obstacle_path((0,0),(6000000,0),[(2000000,-1000000,4000000,1000000)]*1025,
                                 clock=Clock(10),cancel_dir=None)

    def test_local_connection_uses_the_short_corridor_around_foreign_copper(self):
        # A six-millimetre connection crossing a two-millimetre rectangle has
        # an independently calculated shortest legal length: 2 + 2*sqrt(5).
        import math
        a, b = (0, 0), (6_000_000, 0)
        box = (2_000_000, -1_000_000, 4_000_000, 1_000_000)
        path = local._obstacle_path(a, b, [box], clock=Clock(10), cancel_dir=None)
        self.assertEqual((path[0], path[-1]), (a, b))
        self.assertAlmostEqual(sum(math.dist(p, q) for p, q in zip(path, path[1:])) / 1e6,
                               2 + 2*math.sqrt(5))
        self.assertTrue(all(abs(p[1]) >= 1_000_000 for p in path[1:-1]))


class AcquisitionLocalLoopsTest(unittest.TestCase):
    setUp = fixture.AcquisitionLocalReturnTest.setUp
    drc_result = fixture.AcquisitionLocalReturnTest.drc_result
    clear_drc = fixture.AcquisitionLocalReturnTest.clear_drc

    def prepare(self):
        from types import SimpleNamespace as NS
        import sys
        from agent_ee.symbol_library import children, parse_sexpr
        from agent_ee.schematic_emit import format_sexpr
        tree = parse_sexpr(self.board)
        def pad(number, net, x, y, identity):
            return ['pad',number,['uuid',identity],['net',net],['layer','0'],['at',str(x),str(y)]]
        fps = children(tree, 'footprint')
        fps[0].append(pad('1','/V3V3',32000000,41500000,'C3-supply'))
        fps[1].append(pad('8','/V3V3',33000000,39000000,'U2-supply'))
        fps[1].append(pad('4','/ANALOG0',35500000,38500000,'U2-analog0'))
        fps[1].append(pad('5','/ANALOG1',35500000,38000000,'U2-analog1'))
        for ref, number, x, y in [('C2','2',20000000,30000000),('U1','2',23000000,30000000),
                                  ('C4','2',37000000,42000000),('C5','2',39000000,38000000)]:
            tree.append(['footprint',['reference',ref],['uuid',ref+'-fp'],['layer','0'],
                         pad(number,'GND',x,y,ref+'-pad')])
        extra = {'C2':('1','/V3V3',20000000,28000000),'U1':('5','/V3V3',23000000,28000000),
                 'C4':('1','/ANALOG0',35500000,42000000),'C5':('1','/ANALOG1',37500000,38000000)}
        for fp in children(tree,'footprint'):
            ref=children(fp,'reference')[0][1]
            if ref in extra:
                number,net,x,y=extra[ref];fp.append(pad(number,net,x,y,ref+'-signal'))
        self.board = ('\n'.join(format_sexpr(tree))+'\n').encode()
        (self.source/local.FILES[0]).write_bytes(self.board)
        for cls in (NativePad, NativeTrack):
            self.enterContext(patch.object(cls,'GetNetCode',
                lambda p:{'GND':7,'SCL':8,'/V3V3':9,'/ANALOG0':10,'/ANALOG1':11}.get(p.GetNetname(),0)))
        def box(x,y,u,v):
            return NS(GetLeft=lambda:x,GetTop=lambda:y,GetRight=lambda:u,GetBottom=lambda:v)
        self.enterContext(patch.object(NativePad,'GetBoundingBox',
            lambda p:box(p.GetPosition().x-725000,p.GetPosition().y-150000,
                         p.GetPosition().x+725000,p.GetPosition().y+150000)
                if p.m_Uuid.AsString().startswith('U2-') else
                box(p.GetPosition().x-450000,p.GetPosition().y-475000,
                    p.GetPosition().x+450000,p.GetPosition().y+475000),create=True))
        self.enterContext(patch.object(NativeFootprint,'GetPosition',
            lambda fp:NS(x=round(sum(p.GetPosition().x for p in fp.Pads())/len(fp.Pads())),
                         y=round(sum(p.GetPosition().y for p in fp.Pads())/len(fp.Pads())))))
        self.enterContext(patch.object(NativeTrack,'GetBoundingBox',
            lambda t:box(min(t.GetStart().x,t.GetEnd().x)-t.GetWidth()//2,
                         min(t.GetStart().y,t.GetEnd().y)-t.GetWidth()//2,
                         max(t.GetStart().x,t.GetEnd().x)+t.GetWidth()//2,
                         max(t.GetStart().y,t.GetEnd().y)+t.GetWidth()//2),create=True))
        self.enterContext(patch.object(NativeTrack,'IsOnLayer',lambda t,l:t.GetLayer()==l,create=True))
        class NativeVia(NativeTrack):
            def GetClass(self):return 'PCB_VIA'
            def GetViaType(self):return 1
            def GetWidth(self,*args):return local.nanometres(fixture.field(self.node,'size'))
            def GetDrillValue(self):return local.nanometres(fixture.field(self.node,'drill'))
            def TopLayer(self):return 0
            def BottomLayer(self):return 2
            def GetPosition(self):return self.point('at')
            def IsOnLayer(self,layer):return layer in (0,2)
            def GetBoundingBox(self):
                p=self.GetPosition();r=self.GetWidth()//2
                return box(p.x-r,p.y-r,p.x+r,p.y+r)
        def load(path):
            board=fixture.load_board(path)
            board.GetDrawings=lambda:[];board.Zones=lambda:[]
            tracks=board.GetTracks()
            board.GetTracks=lambda:tracks+[NativeVia(v) for v in children(parse_sexpr(Path(path).read_bytes()),'via')]
            board.GetDesignSettings=lambda:NS(GetBoardThickness=lambda:1200000)
            return board
        self.enterContext(patch.object(sys.modules['pcbnew'],'B_Cu',2,create=True))
        self.enterContext(patch.object(sys.modules['pcbnew'],'VIATYPE_THROUGH',1,create=True))
        self.enterContext(patch.object(sys.modules['pcbnew'],'PADSTACK',NS(ALL_LAYERS=-1),create=True))
        self.enterContext(patch.object(sys.modules['pcbnew'],'LoadBoard',side_effect=load))
        # These are custody tests. The native polygon adapter and its real
        # retained-board replay have separate geometry/native coverage.
        self.enterContext(patch.object(local.geometry,'polygon_path',return_value=None))
        project=local.json.loads((self.source/local.FILES[1]).read_bytes())
        project['board']['design_settings']['rules'].update(min_via_diameter=.6,min_through_hole_diameter=.3)
        (self.source/local.FILES[1]).write_text(local.json.dumps(project))

    def run_loops(self):
        self.prepare()
        self.clock = Clock(30)
        return local.produce_loops(self.source,self.output,
            expected_inputs=local._identity(local._capture(self.source)),clock=self.clock,cancel_dir=self.root)

    def test_clear_front_connection_does_not_require_via_rules(self):
        self.prepare()
        project=local.json.loads((self.source/local.FILES[1]).read_bytes())
        project['board']['design_settings']['rules']['min_via_diameter']=.8
        (self.source/local.FILES[1]).write_text(local.json.dumps(project))
        record=local.produce_loops(self.source,self.output,
            expected_inputs=local._identity(local._capture(self.source)),clock=Clock(30),cancel_dir=self.root)
        signal=record['connections'][0]
        trial=signal['trials'][signal['selected_trial']-1]
        self.assertEqual(trial['vias'],[])
        self.assertEqual(trial['vertical_length_mm'],0)
        self.assertEqual(len(record['connections']),8)
        self.assertEqual(local.verify_seed(Path(record['output'])/local.FILES[0],record,
                                          clock=Clock(10))['status'],'pass')

    def run_loops_with_vias(self):
        def drc(board,directory,**kwargs):
            if directory.parent.name=='filter0-signal' and directory.name.startswith('trial-'):
                bad=dict(type='clearance',description='front path blocked',severity='error',items=[])
                return self.drc_result(native_report([bad]),directory)
            return self.clear_drc(board,directory,**kwargs)
        self.drc.side_effect=drc
        return self.run_loops()

    def test_front_exhaustion_retained_and_bound_before_via_fallback(self):
        record=self.run_loops_with_vias()
        retained=record['rejected_families'][0]
        receipt=Path(retained['record'])
        failed=local.json.loads(receipt.read_bytes())
        self.assertEqual(failed['failure_kind'],'search_exhausted')
        self.assertTrue(failed['trials'])
        self.assertTrue(all(t['status']=='rejected' for t in failed['trials']))
        self.assertFalse((receipt.parent/'accepted').exists())
        signal=record['connections'][0]
        self.assertEqual(signal['inputs'],failed['inputs'])
        self.assertEqual(len(signal['trials'][signal['selected_trial']-1]['vias']),2)
        self.assertTrue(all(c.kwargs['clock'] is self.clock for c in self.drc.call_args_list))
        final=Path(record['output'])/local.FILES[0]
        self.assertEqual(local.verify_seed(final,record,clock=Clock(10))['status'],'pass')
        receipt.write_text('{}')
        with self.assertRaisesRegex(JobRefusal,'rejected family record changed'):
            local.verify_seed(final,record,clock=Clock(10))

    def test_front_native_execution_failure_does_not_try_vias(self):
        def drc(board,directory,**kwargs):
            if directory.parent.name=='filter0-signal' and directory.name.startswith('trial-'):
                raise JobRefusal('native DRC did not complete')
            return self.clear_drc(board,directory,**kwargs)
        self.drc.side_effect=drc
        with self.assertRaisesRegex(JobRefusal,'native DRC did not complete'):
            self.run_loops()
        self.assertFalse((self.output/'filter0-signal-two-layer').exists())

    def test_front_library_warning_drift_does_not_try_vias(self):
        def drc(board,directory,**kwargs):
            warnings=fixture.AcquisitionLocalReturnTest.library_warnings(self)
            if directory.parent.name=='filter0-signal' and directory.name.startswith('trial-'):
                warnings=warnings[:-1]
            return self.drc_result(native_report(warnings),directory)
        self.drc.side_effect=drc
        with self.assertRaisesRegex(JobRefusal,'library warning'):
            self.run_loops()
        self.assertFalse((self.output/'filter0-signal-two-layer').exists())
        failed=local.json.loads((self.output/'filter0-signal/record.json').read_bytes())
        self.assertNotIn('failure_kind',failed)
        self.assertTrue(failed['trials'][0]['warning_difference']['missing'])

    def test_front_parity_failure_does_not_try_vias(self):
        def drc(board,directory,**kwargs):
            report=native_report()
            if directory.parent.name=='filter0-signal' and directory.name.startswith('trial-'):
                report['schematic_parity']=[dict(type='net_conflict',description='changed net',
                    severity='error',items=[dict(uuid='changed-pad')])]
            return self.drc_result(report,directory)
        self.drc.side_effect=drc
        with self.assertRaisesRegex(JobRefusal,'parity'):
            self.run_loops()
        self.assertFalse((self.output/'filter0-signal-two-layer').exists())

    def test_front_cancellation_does_not_try_vias(self):
        def drc(board,directory,**kwargs):
            if directory.parent.name=='filter0-signal' and directory.name.startswith('trial-'):
                (self.root/'CANCEL').touch()
            return self.clear_drc(board,directory,**kwargs)
        self.drc.side_effect=drc
        with self.assertRaises(JobStopped):self.run_loops()
        self.assertFalse((self.output/'filter0-signal-two-layer').exists())

    def rail_seed(self):
        from agent_ee.symbol_library import children,parse_sexpr
        from agent_ee.schematic_emit import format_sexpr
        self.prepare()
        tree=parse_sexpr(self.board)
        cap=next(fp for fp in children(tree,'footprint') if children(fp,'reference')[0][1]=='C4')
        for pad in children(cap,'pad'):
            at=children(pad,'at')[0];at[1]=str(int(at[1])+3000000)
        self.board=('\n'.join(format_sexpr(tree))+'\n').encode()
        (self.source/local.FILES[0]).write_bytes(self.board)
        self.clock=Clock(30)
        return local._produce(self.source,self.output,
            expected_inputs=local._identity(local._capture(self.source)),clock=self.clock,cancel_dir=self.root,
            connection='filter0-signal',obstacle_search=True,max_length_mm=12.,two_layer=True)

    def reject_first_escape(self,board,directory,**kwargs):
        if directory.name=='trial-1':
            violation=dict(type='shorting_items',description='neighboring pad',severity='error',items=[])
            return self.drc_result(native_report([violation]),directory)
        return self.clear_drc(board,directory,**kwargs)

    def test_rejected_escape_is_retained_before_second_plan_passes_and_replays(self):
        self.drc.side_effect=self.reject_first_escape
        seed=self.rail_seed()
        self.assertEqual(seed['selected_trial'],2)
        self.assertEqual([(t['proposal'],t['status']) for t in seed['trials']],
                         [('perpendicular','rejected'),('ic-rail','clearance_pass')])
        self.assertTrue((self.output/'trial-1/drc.json').exists())
        self.assertEqual((self.source/local.FILES[0]).read_bytes(),self.board)
        self.assertTrue(all(c.kwargs['clock'] is self.clock for c in self.drc.call_args_list))
        self.assertEqual(local.verify_seed(Path(seed['output'])/local.FILES[0],seed,
                                          clock=Clock(10))['status'],'pass')
        self.assertEqual(local._leg_length(seed),9.9)
        from agent_ee.symbol_library import children,parse_sexpr
        vias=children(parse_sexpr((Path(seed['output'])/local.FILES[0]).read_bytes()),'via')
        self.assertEqual([(children(v,'size')[0][1],children(v,'drill')[0][1]) for v in vias],
                         [('0.6','0.3')]*2)

    def test_both_escape_rejections_retain_reports_without_adoption(self):
        def drc(board,directory,**kwargs):
            if directory.name.startswith('trial-'):
                bad=dict(type='clearance',description='blocked',severity='error',items=[])
                return self.drc_result(native_report([bad]),directory)
            return self.clear_drc(board,directory,**kwargs)
        self.drc.side_effect=drc
        with self.assertRaisesRegex(JobRefusal,'all bounded proposals'):
            self.rail_seed()
        record=local.json.loads((self.output/'record.json').read_bytes())
        self.assertEqual([t['status'] for t in record['trials']],['rejected','rejected'])
        self.assertFalse((self.output/'accepted').exists())
        self.assertEqual((self.source/local.FILES[0]).read_bytes(),self.board)

    def test_cancellation_between_escape_proposals_stops_before_second_trial(self):
        def drc(board,directory,**kwargs):
            result=self.reject_first_escape(board,directory,**kwargs)
            if directory.name=='trial-1':(self.root/'CANCEL').touch()
            return result
        self.drc.side_effect=drc
        with self.assertRaises(JobStopped):self.rail_seed()
        self.assertFalse((self.output/'trial-2').exists())
        self.assertFalse((self.output/'accepted').exists())

    def test_selected_escape_name_and_geometry_cannot_be_relabelled(self):
        import copy
        from agent_ee.storage import write_json
        self.drc.side_effect=self.reject_first_escape
        seed=self.rail_seed();board=Path(seed['output'])/local.FILES[0]
        for key,value in [('proposal','unknown'),('proposal','perpendicular'),
                          ('via_centres_nm',[[0,0],[1,1]]),('vertical_length_mm',3.2)]:
            with self.subTest(key=key,value=value):
                changed=copy.deepcopy(seed);changed['trials'][1][key]=value
                write_json(self.output/'record.json',changed)
                with self.assertRaisesRegex(JobRefusal,'pad-frame plan'):
                    local.verify_seed(board,changed,clock=Clock(10))
        write_json(self.output/'record.json',seed)

    def test_legacy_unnamed_receipt_replays_only_the_original_escape(self):
        from agent_ee.storage import write_json
        record=self.run_loops_with_vias();seed=record['connections'][0]
        self.assertEqual(seed['selected_trial'],1)
        del seed['trials'][0]['proposal']
        write_json(Path(seed['output']).parent/'record.json',seed)
        self.assertEqual(local.verify_seed(Path(seed['output'])/local.FILES[0],seed,
                                          clock=Clock(10))['status'],'pass')

    def test_both_legs_of_each_required_loop_survive_routing(self):
        record = self.run_loops()
        self.assertEqual(record['status'],'seeded')
        self.assertFalse(record['acceptance_assessed'])
        self.assertEqual([r['connection'] for r in record['connections']],
                         ['filter0-signal','adc-supply','output-supply','filter1-signal',
                          'adc-return','output-return','filter0-return','filter1-return'])
        self.assertEqual((self.source/local.FILES[0]).read_bytes(),self.board)
        self.assertEqual(len({id(c.kwargs['clock']) for c in self.drc.call_args_list}),1)
        self.assertIs(self.drc.call_args_list[0].kwargs['clock'],self.clock)
        for previous, current in zip(record['connections'],record['connections'][1:]):
            self.assertEqual(previous['output_inputs'],current['inputs'])
        final = Path(record['output'])/local.FILES[0]
        result = local.verify_seed(final,record,clock=Clock(10))
        self.assertEqual(result['status'],'pass')
        self.assertEqual(len(result['connections']),8)
        # Corrupt a separate routed copy: the immutable retained output and its
        # hash must stay intact, so this exercises the earlier seed reload.
        routed = self.root/'routed.kicad_pcb'
        original = final.read_bytes()
        identity = record['connections'][0]['trials'][0]['segment_uuids'][0]
        routed.write_bytes(original.replace(identity.encode(),b'lost-first-seed'))
        with self.assertRaisesRegex(JobRefusal,'seed'):
            local.verify_seed(routed,record,clock=Clock(10))
        self.assertEqual(final.read_bytes(),original)

    def test_failed_later_leg_never_adopts_partial_bundle(self):
        def drc(board,directory,**kwargs):
            if directory.parent.name=='output-supply' and directory.name.startswith('trial-'):
                bad=dict(type='clearance',description='owned collision',severity='error',items=[dict(uuid='blocked')])
                return self.drc_result(native_report([bad]),directory)
            return self.clear_drc(board,directory,**kwargs)
        self.drc.side_effect=drc
        with self.assertRaises(JobRefusal):
            self.run_loops()
        from agent_ee.storage import read_json
        record=read_json(self.output/'record.json')
        self.assertEqual(record['status'],'refused')
        self.assertNotIn('output',record)
        self.assertEqual((self.source/local.FILES[0]).read_bytes(),self.board)
        self.assertEqual(len(record['connections']),2)
        failed=read_json(self.output/'output-supply/record.json')
        self.assertLessEqual(len(failed['trials']),7)
        self.assertFalse((self.output/'output-supply/accepted').exists())

    def test_via_identity_and_drill_survive_in_a_separate_routed_copy(self):
        from agent_ee.symbol_library import children,parse_sexpr
        from agent_ee.schematic_emit import format_sexpr
        record=self.run_loops_with_vias();source=Path(record['output'])/local.FILES[0]
        original=source.read_bytes();routed=self.root/'routed-vias.kicad_pcb'
        for mode in ('missing','drill','position'):
            with self.subTest(mode=mode):
                tree=parse_sexpr(original);via=children(tree,'via')[0]
                if mode=='missing':tree.remove(via)
                elif mode=='drill':children(via,'drill')[0][1]='0.31'
                else:children(via,'at')[0][1]='99'
                routed.write_text('\n'.join(format_sexpr(tree))+'\n')
                with self.assertRaisesRegex(JobRefusal,'via'):
                    local.verify_seed(routed,record,clock=Clock(10))
                self.assertEqual(source.read_bytes(),original)

    def test_via_depth_consumes_the_return_legs_remaining_loop_budget(self):
        record=self.run_loops_with_vias()
        first=record['connections'][0];trial=first['trials'][first['selected_trial']-1]
        self.assertEqual(trial['vertical_length_mm'],2.4)
        self.assertAlmostEqual(trial['total_length_mm'],trial['trace_length_mm']+2.4)
        returned=next(r for r in record['connections'] if r['connection']=='filter0-return')
        self.assertAlmostEqual(returned['max_length_mm'],12.-trial['total_length_mm'])

    def test_mid_chain_cancellation_never_publishes_a_partial_bundle(self):
        def drc(board,directory,**kwargs):
            if 'output-supply' in directory.parts:
                (self.root/'CANCEL').touch()
            return self.clear_drc(board,directory,**kwargs)
        self.drc.side_effect=drc
        with self.assertRaises(JobStopped):self.run_loops()
        from agent_ee.storage import read_json
        record=read_json(self.output/'record.json')
        self.assertEqual(record['status'],'stopped')
        self.assertNotIn('output',record)

    def test_old_receipt_drift_during_later_leg_refuses_the_bundle(self):
        def drc(board,directory,**kwargs):
            if 'filter1-return' in directory.parts:
                (self.output/'adc-return/record.json').write_text('{}')
            return self.clear_drc(board,directory,**kwargs)
        self.drc.side_effect=drc
        with self.assertRaisesRegex(JobRefusal,'record changed'):self.run_loops()
        from agent_ee.storage import read_json
        record=read_json(self.output/'record.json')
        self.assertEqual(record['status'],'refused')
        self.assertNotIn('output',record)
