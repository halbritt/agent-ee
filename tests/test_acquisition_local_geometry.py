"""Generated via exits follow native pad frames, not absolute board axes."""
import unittest
import math
from pathlib import Path
import tempfile
from unittest.mock import patch
from agent_ee import acquisition_local_geometry as geometry
from agent_ee.placement_job import Clock, JobRefusal, JobStopped


class LocalViaGeometryTests(unittest.TestCase):
    def test_skipped_obstacles_do_not_exhaust_the_edge_work_budget(self):
        polygons=[]
        for i in range(100):
            x=(10+2*i)*1000000
            poly=[(x,10000000),(x+1000000,10000000),(x+1000000,11000000),(x,11000000)]
            polygons.append((geometry._bbox(poly),poly))
        self.assertEqual(geometry._visibility_path((0,0),(6000000,0),polygons,6.,
            clock=Clock(10),cancel_dir=None,max_edge_work=600),[(0,0),(6000000,0)])

    def test_visibility_work_budget_refuses_before_search(self):
        poly=[(0,0),(1000000,0),(1000000,1000000),(0,1000000)]
        with self.assertRaisesRegex(JobRefusal,'edge-work budget'):
            geometry._visibility_path((-1000000,0),(2000000,0),[(geometry._bbox(poly),poly)],10.,
                clock=Clock(10),cancel_dir=None,max_edge_work=1)

    def test_covered_dense_contour_obeys_cancellation_during_vertex_filtering(self):
        circle=[(round(1e6*math.cos(i*math.tau/1024)),round(1e6*math.sin(i*math.tau/1024)))
                for i in range(1024)]
        cover=[(-2000000,-2000000),(2000000,-2000000),(2000000,2000000),(-2000000,2000000)]
        polygons=[(geometry._bbox(p),p) for p in (circle,cover)]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);calls=0;original=geometry._inside
            def inside(*args,**kwargs):
                nonlocal calls
                calls+=1
                if calls==5:(root/'CANCEL').touch()
                return original(*args,**kwargs)
            with patch.object(geometry,'_inside',side_effect=inside):
                with self.assertRaises(JobStopped):
                    geometry._visibility_path((-3000000,0),(3000000,0),polygons,10.,clock=Clock(30),cancel_dir=root)
            self.assertLess(calls,12,'covered vertices must not postpone cancellation to the next contour')

    def test_exit_and_signed_pin_pitch_rotate_with_the_component_frames(self):
        a,b,other,centre,next_pad=(4000000,3000000),(0,1000000),(5500000,3000000),(-2150000,1500000),(0,500000)
        boxes=[(3550000,2525000,4450000,3475000),(-725000,850000,725000,1150000)]
        expected=[a,(4000000,2000000),(1250000,500000),(1250000,1000000),b]
        def rotate(point,turns):
            x,y=point
            for _ in range(turns):x,y=-y,x
            return x+17000000,y+23000000
        def box_rotate(box,turns):
            x,y,u,v=box;corners=[rotate(p,turns) for p in ((x,y),(x,v),(u,y),(u,v))]
            return min(p[0] for p in corners),min(p[1] for p in corners),max(p[0] for p in corners),max(p[1] for p in corners)
        for turns in range(4):
            with self.subTest(turns=turns):
                path=geometry.via_points(*(rotate(p,turns) for p in (a,b,other,centre,next_pad)),
                    *(box_rotate(box,turns) for box in boxes))
                self.assertEqual(path,[rotate(p,turns) for p in expected])

    def test_ambiguous_or_nonorthogonal_pad_frames_refuse(self):
        base=[(4000000,3000000),(0,1000000),(5500000,3000000),(-2150000,1500000),(0,500000),
              (3550000,2525000,4450000,3475000),(-725000,850000,725000,1150000)]
        for index,value in ((2,(5500000,3100000)),(2,base[0]),(3,(0,1500000)),
                            (4,(100000,500000)),(4,base[1])):
            with self.subTest(index=index,value=value):
                args=base.copy();args[index]=value
                with self.assertRaises(JobRefusal):geometry.via_points(*args)
