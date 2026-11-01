"""Native return-path witnesses and conditional DC/RF arithmetic.

This is not a field solver or a calibrated ADC accuracy model. The narrow copper
paths are constructive DC resistance estimates. RF values are sensitivities.
"""
import heapq
import math

import pcbnew as k
from agent_ee.geometry_probe import vec_mm


def circle(center, radius):
    """Circumscribe a circular drill so the remaining copper is conservative."""
    polygon = k.SHAPE_POLY_SET()
    polygon.NewOutline()
    r = radius/math.cos(math.pi/64)+2
    for i in range(64):
        angle = 2*math.pi*i/64
        polygon.Append(round(center.x+r*math.cos(angle)), round(center.y+r*math.sin(angle)))
    return polygon


def bottom_copper(board):
    copper, holes = k.SHAPE_POLY_SET(), k.SHAPE_POLY_SET()
    for zone in board.Zones():
        if not zone.GetIsRuleArea() and zone.GetNetname() == 'GND' and zone.HasFilledPolysForLayer(k.B_Cu):
            copper.Append(zone.GetFilledPolysList(k.B_Cu))
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetname() == 'GND' and pad.IsOnLayer(k.B_Cu) and pad.FlashLayer(k.B_Cu):
                pad.TransformShapeToPolygon(copper,k.B_Cu,0,1,k.ERROR_INSIDE)
            if pad.HasHole():
                pad.TransformHoleToPolygon(holes,0,1,k.ERROR_OUTSIDE)
    for item in board.GetTracks():
        if item.GetClass() == 'PCB_VIA':
            if item.IsOnLayer(k.B_Cu):
                if item.GetNetname() == 'GND' and item.FlashLayer(k.B_Cu):
                    item.TransformShapeToPolygon(copper,k.B_Cu,0,1,k.ERROR_INSIDE)
                holes.Append(circle(item.GetPosition(),item.GetDrillValue()/2))
        elif item.GetLayer() == k.B_Cu and item.GetNetname() == 'GND':
            item.TransformShapeToPolygon(copper,k.B_Cu,0,1,k.ERROR_INSIDE)
    copper.Simplify()
    holes.Simplify()
    copper.BooleanSubtract(holes)
    copper.Simplify()
    return copper


def corridor(board, copper, start, goals, width_nm=100_000):
    """Bounded 0.25 mm grid search; every edge proves its full copper width."""
    cache = {}
    def clear(a,b):
        pair=tuple(sorted((a,b)))
        if pair in cache:
            return cache[pair]
        if a==b:
            return True
        track=k.PCB_TRACK(board)
        track.SetStart(k.VECTOR2I(*a));track.SetEnd(k.VECTOR2I(*b))
        track.SetWidth(width_nm);track.SetLayer(k.B_Cu)
        shape=k.SHAPE_POLY_SET()
        track.TransformShapeToPolygon(shape,k.B_Cu,0,1,k.ERROR_OUTSIDE)
        shape.BooleanSubtract(copper);shape.Simplify()
        result=shape.IsEmpty() or shape.Area()==0
        cache[pair]=result
        return result
    def heuristic(p):
        return min(math.dist(p,g) for g in goals)
    queue=[(heuristic(start),0,start)]
    best={start:0};previous={}
    visited=0
    while queue and visited<5000:
        _,distance,point=heapq.heappop(queue)
        if distance!=best[point]:
            continue
        visited+=1
        for goal in sorted(goals,key=lambda g:math.dist(point,g)):
            if math.dist(point,goal)<=500_000 and clear(point,goal):
                path=[point]
                while path[-1]!=start:
                    path.append(previous[path[-1]])
                path.reverse();path.append(goal)
                return dict(points_nm=[list(p) for p in path],
                    length_mm=(distance+math.dist(point,goal))/1e6,
                    width_mm=width_nm/1e6, visited=visited)
        for dx,dy in ((250_000,0),(-250_000,0),(0,250_000),(0,-250_000)):
            target=(point[0]+dx,point[1]+dy)
            cost=distance+250_000
            if cost<best.get(target,math.inf) and clear(point,target):
                best[target]=cost;previous[target]=point
                heapq.heappush(queue,(cost+heuristic(target),cost,target))
    return None


def distances(graph,start):
    best={start:0};queue=[(0,repr(start),start)]
    while queue:
        distance,_,point=heapq.heappop(queue)
        if distance!=best[point]:continue
        for target,length in graph.get(point,[]):
            # Native same-layer pad/track contacts only; no ideal plane edge.
            layer=target[-1] if target[0]=='pad' else target[1]
            if layer!='F.Cu':continue
            cost=distance+length
            if cost<best.get(target,math.inf):
                best[target]=cost;heapq.heappush(queue,(cost,repr(target),target))
    return best


def annulus_ports(pad):
    """Ports on this pad's own copper, inside the circle inscribed in a round or oval land.

    Copper is the union of plane and pads, so a port off the pad could borrow plane
    copper. The pad must be a plated GND land on B.Cu: otherwise a corridor could end
    on plane copper under a pad it does not connect to.
    """
    drill=pad.GetDrillSize();size=pad.GetSize(k.B_Cu);offset=pad.GetOffset(k.B_Cu)
    land=min(size.x,size.y)
    if (pad.GetNetname()!='GND' or pad.GetAttribute()!=k.PAD_ATTRIB_PTH or not pad.HasHole()
            or not pad.FlashLayer(k.B_Cu) or drill.x!=drill.y or offset.x or offset.y or land<=drill.x
            or pad.GetShape(k.B_Cu) not in (k.PAD_SHAPE_CIRCLE,k.PAD_SHAPE_OVAL)):
        raise ValueError('ground-reference model needs a plated GND land, round or oval, with a centred round drill')
    center=pad.GetPosition();radius=round((drill.x+land)/4)
    return [(center.x+dx*radius,center.y+dy*radius) for dx,dy in ((1,0),(-1,0),(0,1),(0,-1))]


def measure(board,graph,model):
    policy=model['local_plane_returns']
    copper=bottom_copper(board)
    vias=[v for v in board.GetTracks() if v.GetClass()=='PCB_VIA' and v.GetNetname()=='GND'
          and v.TopLayer()==k.F_Cu and v.BottomLayer()==k.B_Cu and v.GetViaType()==k.VIATYPE_THROUGH]
    thickness=k.ToMM(board.GetDesignSettings().GetBoardThickness())
    pads={(fp.GetReference(),p.GetNumber()):p for fp in board.GetFootprints() for p in fp.Pads()}
    rows=[];missing=0;corridor_missing=0
    for ref,pin in policy['endpoints']:
        reachable=distances(graph,('pad',ref,pin,'F.Cu'))
        choices=[]
        for via in vias:
            pos=via.GetPosition();xy=tuple(vec_mm(pos))
            length=reachable.get(('GND','F.Cu',xy))
            if length is not None:choices.append((length,via.m_Uuid.AsString(),via))
        if not choices:
            missing+=1;rows.append(dict(endpoint=[ref,pin],status='missing_transition'));continue
        length,uid,via=min(choices,key=lambda row:(row[0],row[1]))
        point=via.GetPosition()
        # Mid-annulus port. Every subsequent strip is proved inside saved copper.
        port=round((via.GetWidth(k.B_Cu)+via.GetDrillValue())/4)
        starts=[(point.x+dx*port,point.y+dy*port) for dx,dy in ((1,0),(-1,0),(0,1),(0,-1))]
        witnesses=[]
        for ground_pin in ('2','4'):
            goals=annulus_ports(pads[('J3',ground_pin)])
            found=None
            for start in starts:
                found=corridor(board,copper,start,goals)
                if found is not None:break
            if found is None:
                corridor_missing+=1
                witnesses.append(dict(connector=['J3',ground_pin],status='no_witness'))
            else:
                witnesses.append(dict(connector=['J3',ground_pin],status='witnessed',**found))
        dc=model['dc_estimate']
        rho=dc['rho_ohm_m'];cu=dc['copper_um']*1e-6
        front_r=rho*length*1e-3/(dc['track_width_mm']*1e-3*cu)
        # Via wall thickness is an explicit conditional sensitivity, not a fab guarantee.
        barrel_r=rho*thickness*1e-3/(math.pi*k.ToMM(via.GetDrillValue())*1e-3*20e-6)
        for witness in witnesses:
            if witness['status']=='witnessed':
                plane_r=rho*witness['length_mm']*1e-3/(witness['width_mm']*1e-3*cu)
                # The inherited complete contact/joint allowance is deliberately charged
                # again to this path; it is an assumption, not measured joint resistance.
                witness['conditional_path_ohm']=front_r+barrel_r+plane_r+dc['contact_and_joint_ohm']
                witness['drop_v_at_1mA']=witness['conditional_path_ohm']*.001
                witness['drop_v_at_25mA']=witness['conditional_path_ohm']*.025
        rf=[]
        for inductance_per_mm,barrel_nh in (((.25,.5),(1,1),(5,2)) if ref in ('C4','C5') else ()):
            inductance=(length*inductance_per_mm+barrel_nh)*1e-9
            for cap_nf in (9.5,10.5):
                for hz in (10,16000,100000,1000000,10000000,100000000):
                    z=complex(front_r+barrel_r,2*math.pi*hz*inductance-1/(2*math.pi*hz*cap_nf*1e-9))
                    rf.append(dict(trace_nh_per_mm=inductance_per_mm,barrel_nh=barrel_nh,
                        capacitance_nf=cap_nf,hz=hz,voltage_ratio=abs(z/(990+z))))
        rows.append(dict(endpoint=[ref,pin],status='measured',via_uuid=uid,
            front_length_mm=length,barrel_length_mm=thickness,total_length_mm=length+thickness,
            conditional_front_ohm=front_r,conditional_barrel_ohm=barrel_r,
            plane_corridors=witnesses,filter_sensitivity=rf))
    return dict(connections=rows,copper_components=copper.OutlineCount(),graph_resolution_mm=.0001,
        metrics=dict(ground_transition_missing=missing,ground_corridor_missing=corridor_missing,
            ground_plane_components=copper.OutlineCount(),
            ground_return_path=max((r['total_length_mm'] for r in rows if r['status']=='measured'),default=0)),
        limitations=['Constructive DC strip estimates use 17.5 um copper, 0.2 mm tracks, 20 um via plating and the retained 0.13 ohm contact allowance; not supplier guarantees.',
            '25 mA is a network stress sensitivity; no declared total-accuracy limit or calibration is inferred.',
            'ADC can sink SDA only. Host drives SCL and returns its current via J2.1.',
            'RF table is a lumped local-ground inductance sensitivity with 990 ohm series resistance; it omits plane impedance, package ESL and mutual coupling, so is not an RF attenuation guarantee.',
            'Ground error adds to input loading and ADC error; typical noise is not a bound on these terms.'])
