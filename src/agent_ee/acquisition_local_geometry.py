"""Bounded local copper proposals; native DRC remains the clearance authority."""
import heapq
import math

from .placement_job import JobRefusal, _check_stop


def via_points(a, b, cap_other, ic_centre, next_pad, cap_box, ic_box):
    """One two-layer filter leg in the actual orthogonal pad frames.

    Leave the capacitor perpendicular to its pad pair, toward the IC. Leave
    the IC along its long pad axis, away from the package, then shift one
    signed pin pitch toward the next signal pad to retain ground-pad access.
    """
    dx,dy=cap_other[0]-a[0],cap_other[1]-a[1]
    if bool(dx)==bool(dy):
        raise JobRefusal('local loops: capacitor pad frame is not orthogonal')
    outward=(-dy//abs(dy),0) if dy else (0,dx//abs(dx))
    toward=sum(d*(q-p) for d,p,q in zip(outward,a,b))
    if not toward:
        raise JobRefusal('local loops: capacitor exit direction is ambiguous')
    if toward<0:outward=tuple(-d for d in outward)
    def exit_point(point,box,direction):
        axis=0 if direction[0] else 1
        sign=direction[axis]
        result=list(point)
        result[axis]=box[axis+(2 if sign>0 else 0)]+sign*525_000
        return tuple(result)
    va=exit_point(a,cap_box,outward)
    width,height=ic_box[2]-ic_box[0],ic_box[3]-ic_box[1]
    if width<=0 or height<=0 or width==height:
        raise JobRefusal('local loops: IC pad long axis is ambiguous')
    axis=0 if width>height else 1
    delta=b[axis]-ic_centre[axis]
    pitch=tuple(q-p for p,q in zip(b,next_pad))
    if not delta or pitch[axis] or not 0<abs(pitch[1-axis])<=2_000_000:
        raise JobRefusal('local loops: IC outward direction or signed pin pitch is unsupported')
    direction=[0,0];direction[axis]=1 if delta>0 else -1
    base=exit_point(b,ic_box,direction)
    vb=tuple(p+d for p,d in zip(base,pitch))
    return [tuple(a),va,vb,base,tuple(b)]


def via_candidates(a, b, cap_other, ic_centre, next_pad, cap_box, ic_box):
    """At most two pad-frame proposals; native DRC must check each one.

    Preserve the perpendicular escape first. Alternatively, leave the
    capacitor away from its other pad until reaching the existing IC escape
    rail. The rail depends only on unchanged pads, so final replay does not
    depend on copper added by later routing.
    """
    original=via_points(a,b,cap_other,ic_centre,next_pad,cap_box,ic_box)
    candidates=[('perpendicular',original)]
    axis=0 if ic_box[2]-ic_box[0]>ic_box[3]-ic_box[1] else 1
    delta=a[axis]-cap_other[axis]
    if not delta:return candidates
    direction=1 if delta>0 else -1
    distance=(original[-2][axis]-a[axis])*direction
    if distance<=0:return candidates
    via=list(a);via[axis]+=distance*direction
    path=[tuple(a),tuple(via),*original[2:]]
    if len(set(path))==len(path) and path!=original:
        candidates.append(('ic-rail',path))
    return candidates


def two_layer_plans(source,endpoints,*,clock,cancel_dir):
    import pcbnew as k
    _check_stop(clock,cancel_dir)
    board=k.LoadBoard(str(source))
    if board is None or list(board.GetEnabledLayers().CuStack())!=[k.F_Cu,k.B_Cu]:
        raise JobRefusal('local loops: two native copper layers are required')
    def pad(ref,number,net):
        fps=[fp for fp in board.GetFootprints() if fp.GetReference()==ref]
        if len(fps)!=1:raise JobRefusal('local loops: ambiguous via footprint '+ref)
        pads=[p for p in fps[0].Pads() if p.GetNumber()==number]
        if (len(pads)!=1 or pads[0].GetNetname()!=net or pads[0].GetAttribute()!=k.PAD_ATTRIB_SMD
                or fps[0].GetLayer()!=k.F_Cu
                or [l for l in board.GetEnabledLayers().CuStack() if pads[0].FlashLayer(l)]!=[k.F_Cu]):
            raise JobRefusal('local loops: unsupported via pad '+ref+'.'+number)
        return fps[0],pads[0]
    cap,p1=pad('C4','1','/ANALOG0');_,p2=pad('C4','2','GND')
    ic,p4=pad('U2','4','/ANALOG0');_,p5=pad('U2','5','/ANALOG1')
    def position(item):
        p=item.GetPosition();return int(p.x),int(p.y)
    def box(item):
        b=item.GetBoundingBox()
        return int(b.GetLeft()),int(b.GetTop()),int(b.GetRight()),int(b.GetBottom())
    a,b=[tuple(e['position_nm']) for e in endpoints]
    if a!=position(p1) or b!=position(p4):
        raise JobRefusal('local loops: via endpoints differ from captured native pads')
    paths=via_candidates(a,b,position(p2),position(ic),position(p5),box(p1),box(p4))
    thickness=int(board.GetDesignSettings().GetBoardThickness())
    if thickness<=0:raise JobRefusal('local loops: board thickness is missing')
    return [dict(proposal=name,path_nm=path,layers=['F.Cu','B.Cu','F.Cu','F.Cu'],
                via_centres_nm=path[1:3],board_thickness_nm=thickness,
                vertical_length_mm=2*thickness/1e6) for name,path in paths]


def _cross(a,b):return a[0]*b[1]-a[1]*b[0]
def _minus(a,b):return a[0]-b[0],a[1]-b[1]
def _bbox(points):
    return min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)
def _inside(point,poly,*,tick):
    for a,b in zip(poly,poly[1:]+poly[:1]):
        tick()
        if _cross(_minus(b,a),_minus(point,a))<=0:return False
    return True
def _crosses(p,q,poly,*,tick):
    low,high=0.,1.;direction=_minus(q,p)
    for a,b in zip(poly,poly[1:]+poly[:1]):
        tick()
        edge=_minus(b,a);base=_cross(edge,_minus(p,a));delta=_cross(edge,direction)
        if delta==0:
            if base<=0:return False
        elif delta>0:low=max(low,-base/delta)
        else:high=min(high,-base/delta)
        if low>=high:return False
    return low<high


def polygon_path(source,endpoints,limit_mm,*,clock,cancel_dir):
    """Visibility proposal around native expanded convex copper outlines.

    Ignore polygon holes conservatively. Refuse nonconvex contours and foreign
    front zones; use at most 2048 search vertices and 32768 obstacle vertices.
    The caller must subsequently check every emitted segment with native DRC.
    """
    import pcbnew as k
    from .rf_geometry import _polyset
    _check_stop(clock,cancel_dir)
    board=k.LoadBoard(str(source))
    if board is None:raise JobRefusal('local loops: native obstacle load failed')
    a,b=[tuple(e['position_nm']) for e in endpoints];net=endpoints[0]['net']
    if any(z.IsOnLayer(k.F_Cu) and z.GetNetname()!=net for z in board.Zones()):
        raise JobRefusal('local loops: foreign front zones unsupported')
    items=[pad for fp in board.GetFootprints() for pad in fp.Pads()]+list(board.GetTracks())+list(board.GetDrawings())
    if len(items)>4096:raise JobRefusal('local loops: native obstacle population exceeds 4096')
    polygons=[];vertices=0
    for item in items:
        _check_stop(clock,cancel_dir)
        if not item.IsOnLayer(k.F_Cu) or (hasattr(item,'GetNetname') and item.GetNetname()==net):continue
        shape=k.SHAPE_POLY_SET()
        item.TransformShapeToPolygon(shape,k.F_Cu,350_000,1000,k.ERROR_OUTSIDE)
        shape.Simplify()
        for contour in _polyset(shape):
            poly=[tuple(v) for v in contour['outline']]
            vertices+=len(poly)
            if vertices>32768:raise JobRefusal('local loops: native obstacle vertex limit exceeded')
            if len(poly)<3:raise JobRefusal('local loops: malformed native obstacle outline')
            area=sum(_cross(u,v) for u,v in zip(poly,poly[1:]+poly[:1]))
            if area<0:poly.reverse()
            if not area or not all(_cross(_minus(v,u),_minus(w,v))>=0
                                   for u,v,w in zip(poly,poly[1:]+poly[:1],poly[2:]+poly[:2])):
                raise JobRefusal('local loops: nonconvex native obstacle unsupported')
            polygons.append((_bbox(poly),poly))
    return _visibility_path(a,b,polygons,limit_mm,clock=clock,cancel_dir=cancel_dir)


def _visibility_path(a,b,polygons,limit_mm,*,clock,cancel_dir,max_edge_work=25_000_000):
    _check_stop(clock,cancel_dir)
    edge_work=0
    def tick():
        nonlocal edge_work
        edge_work+=1
        if edge_work%256==0:_check_stop(clock,cancel_dir)
        if edge_work>max_edge_work:
            raise JobRefusal('local loops: visibility edge-work budget exceeded')
    if any(_inside(point,poly,tick=tick) for point in (a,b) for _,poly in polygons):return None
    limit=limit_mm*1e6;nodes={a,b}
    for _,poly in polygons:
        _check_stop(clock,cancel_dir)
        for point in poly:
            _check_stop(clock,cancel_dir)
            if math.dist(a,point)+math.dist(point,b)<=limit and not any(_inside(point,other,tick=tick) for _,other in polygons):
                nodes.add(point)
            if len(nodes)>2048:raise JobRefusal('local loops: visibility search exceeds 2048 vertices')
    nodes=sorted(nodes);best={a:0.};previous={};queue=[(math.dist(a,b),0.,a)]
    while queue:
        _check_stop(clock,cancel_dir)
        _,length,point=heapq.heappop(queue)
        if length!=best[point]:continue
        if point==b:
            path=[b]
            while path[-1]!=a:path.append(previous[path[-1]])
            return list(reversed(path))
        for other in nodes:
            _check_stop(clock,cancel_dir)
            distance=length+math.dist(point,other)
            if other==point or distance>=best.get(other,math.inf) or distance+math.dist(other,b)>limit:continue
            bounds=_bbox([point,other]);blocked=False
            for bound,poly in polygons:
                if bound[2]<bounds[0] or bounds[2]<bound[0] or bound[3]<bounds[1] or bounds[3]<bound[1]:continue
                if _crosses(point,other,poly,tick=tick):blocked=True;break
            if not blocked:
                best[other]=distance;previous[other]=point
                heapq.heappush(queue,(distance+math.dist(other,b),distance,other))
    return None
