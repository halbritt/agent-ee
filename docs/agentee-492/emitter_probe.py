"""Native straight CPW emission feasibility; diagnostic only, not a router."""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
from fractions import Fraction

import pcbnew as k

ROOT = Path(sys.argv[1])
ROOT.mkdir(parents=True, exist_ok=True)
NM = 1000000


def emit(name, width=293000, gap=203200, diagonal=False):
    directory = ROOT / name
    directory.mkdir()
    path = directory / 'board.kicad_pcb'
    board = k.BOARD()
    board.SetCopperLayerCount(4)
    board.GetDesignSettings().m_NetSettings.GetDefaultNetclass().SetClearance(203200)
    nets = {}
    for net in ('/RF', 'GND'):
        nets[net] = k.NETINFO_ITEM(board, net)
        board.Add(nets[net])

    def point(x, y):
        if diagonal:
            # Rotate about (20,20) by45 degrees, quantizing once to native nm.
            x, y = (20*NM + (x-20*NM-(y-20*NM))/math.sqrt(2),
                    20*NM + (x-20*NM+y-20*NM)/math.sqrt(2))
        return k.VECTOR2I(round(x), round(y))

    def track(a, b, net, w):
        item = k.PCB_TRACK(board)
        item.SetStart(point(*a)); item.SetEnd(point(*b))
        item.SetWidth(w); item.SetLayer(k.F_Cu); item.SetNet(nets[net])
        board.Add(item)
        return item.m_Uuid.AsString()

    def pad(ref, x, y, net, diameter):
        fp = k.FOOTPRINT(board); fp.SetReference(ref)
        fp.SetAttributes(k.FP_SMD)
        fp.Reference().SetLayer(k.F_Fab); fp.Value().SetLayer(k.F_Fab)
        board.Add(fp)
        item = k.PAD(fp); item.SetNumber('1'); item.SetShape(k.PAD_SHAPE_CIRCLE)
        item.SetAttribute(k.PAD_ATTRIB_SMD); item.SetSize(k.VECTOR2I(diameter, diameter))
        layers = k.LSET(); layers.AddLayer(k.F_Cu); item.SetLayerSet(layers)
        item.SetPosition(point(x,y)); item.SetNet(nets[net]); fp.Add(item)
        return item.m_Uuid.AsString()

    center = 20*NM
    offset = Fraction(width,2) + gap + 500000
    assert offset.denominator == 1
    offset = int(offset)
    rf_id = track((10*NM,center),(30*NM,center),'/RF',width)
    flank_ids = {}
    for side, sign in [('lower',-1),('upper',1)]:
        flank_ids[side] = track((8*NM,center+sign*offset),(32*NM,center+sign*offset),'GND',1000000)
    track((8*NM,center-offset),(8*NM,center+offset),'GND',1000000)
    pad('J1',10*NM,center,'/RF',width); pad('J2',30*NM,center,'/RF',width)
    anchor = pad('G1',8*NM,center-offset,'GND',1000000)
    via_ids = []
    for x in (8*NM,32*NM):
        for sign in (-1,1):
            via = k.PCB_VIA(board); via.SetPosition(point(x,center+sign*offset))
            via.SetWidth(600000); via.SetDrill(300000); via.SetViaType(k.VIATYPE_THROUGH)
            via.SetLayerPair(k.F_Cu,k.B_Cu); via.SetNet(nets['GND']); board.Add(via)
            via_ids.append(via.m_Uuid.AsString())
    zone = k.ZONE(board); zone.SetLayer(k.In1_Cu); zone.SetNet(nets['GND'])
    zone.SetLocalClearance(203200); zone.SetMinThickness(100000)
    zone.SetPadConnection(k.ZONE_CONNECTION_FULL)
    zone.SetIslandRemovalMode(k.ISLAND_REMOVAL_MODE_ALWAYS)
    outline=zone.Outline(); outline.NewOutline()
    for x,y in [(2,2),(38,2),(38,38),(2,38)]:outline.Append(x*NM,y*NM)
    board.Add(zone)
    for a,b in zip([(0,0),(40,0),(40,40),(0,40)],[(40,0),(40,40),(0,40),(0,0)]):
        e=k.PCB_SHAPE(board);e.SetShape(k.SHAPE_T_SEGMENT)
        e.SetStart(k.VECTOR2I(a[0]*NM,a[1]*NM));e.SetEnd(k.VECTOR2I(b[0]*NM,b[1]*NM))
        e.SetLayer(k.Edge_Cuts);e.SetWidth(50000);board.Add(e)
    k.ZONE_FILLER(board).Fill(board.Zones());k.SaveBoard(str(path),board)
    rules='''(version 1)
(rule "RF exact ordinary width"
 (condition "A.NetName == '/RF'")
 (constraint track_width (min 0.293mm) (max 0.293mm)))
(rule "RF physical clearance"
 (condition "A.NetName == '/RF' || B.NetName == '/RF'")
 (constraint clearance (min 0.2032mm)))
'''
    path.with_suffix('.kicad_dru').write_text(rules)
    path.with_suffix('.kicad_pro').write_text(json.dumps({'board':{'design_settings':{'rules':{
        'min_clearance':.2032,'min_track_width':.1}}},'meta':{'version':1}}))
    board=k.LoadBoard(str(path));board.BuildConnectivity()
    tracks={t.m_Uuid.AsString():t for t in board.GetTracks()}
    signal=tracks[rf_id];a=signal.GetStart();b=signal.GetEnd();dx=b.x-a.x;dy=b.y-a.y
    length2=dx*dx+dy*dy
    measurements={}
    for side, uid in flank_ids.items():
        flank=tracks[uid];p=flank.GetStart();q=flank.GetEnd()
        parallel=dx*(q.y-p.y)-dy*(q.x-p.x)==0
        cross=dx*(p.y-a.y)-dy*(p.x-a.x)
        center_squared=Fraction(cross*cross,length2)
        desired=Fraction(signal.GetWidth()+flank.GetWidth(),2)+203200
        actual_center=math.sqrt(center_squared)
        measurements[side]={'parallel':parallel,'center_distance_squared_nm2':str(center_squared),
            'exact_nominal_gap':parallel and center_squared==desired*desired,
            'gap_nm':actual_center-(signal.GetWidth()+flank.GetWidth())/2,
            'signal_width_nm':signal.GetWidth(),'ground_width_nm':flank.GetWidth(),
            'signal_endpoints_nm':[[a.x,a.y],[b.x,b.y]],'ground_endpoints_nm':[[p.x,p.y],[q.x,q.y]],
            'cross_product_nm2':cross,'signal_length_squared_nm2':length2}
    pads={p.m_Uuid.AsString():p for f in board.GetFootprints() for p in f.Pads()}
    con=board.GetConnectivity();related={i.m_Uuid.AsString() for i in con.GetConnectedItems(pads[anchor])}
    zones=list(board.Zones());z=zones[0]
    connections={'ground_anchor_pad':anchor,'connected_item_uuids':sorted(related),
        'reference_zone_uuid':z.m_Uuid.AsString(),'reference_zone_connected':z.m_Uuid.AsString() in related,
        'flanks_connected':all(uid in related for uid in flank_ids.values()),
        'all_ground_vias_connected':all(uid in related for uid in via_ids),
        'reference_fill_present':z.HasFilledPolysForLayer(k.In1_Cu)}
    # A second native refill must retain the intended structure, rather than a stale polygon witness.
    k.ZONE_FILLER(board).Fill(board.Zones());refilled=directory/'refilled.kicad_pcb'
    k.SaveBoard(str(refilled),board)
    reloaded=k.LoadBoard(str(refilled));reloaded.BuildConnectivity()
    p2=next(p for f in reloaded.GetFootprints() for p in f.Pads() if p.m_Uuid.AsString()==anchor)
    refilled_related={i.m_Uuid.AsString() for i in reloaded.GetConnectivity().GetConnectedItems(p2)}
    after_tracks={t.m_Uuid.AsString():t for t in reloaded.GetTracks()}
    ids=[rf_id,*flank_ids.values()]
    connections['ordinary_primitives_unchanged_after_refill']=all(
        after_tracks[uid].GetWidth()==tracks[uid].GetWidth() and
        after_tracks[uid].GetStart()==tracks[uid].GetStart() and
        after_tracks[uid].GetEnd()==tracks[uid].GetEnd() for uid in ids)
    connections['reference_connected_after_refill']=z.m_Uuid.AsString() in refilled_related
    connections['flanks_connected_after_refill']=all(uid in refilled_related for uid in flank_ids.values())
    for suffix in ('.kicad_dru','.kicad_pro'):refilled.with_suffix(suffix).write_bytes(path.with_suffix(suffix).read_bytes())
    command=['kicad-cli','pcb','drc','--format','json','--output',str(directory/'drc.json'),str(refilled)]
    proc=subprocess.run(command,capture_output=True,text=True,timeout=60)
    (directory/'drc.stdout').write_text(proc.stdout);(directory/'drc.stderr').write_text(proc.stderr)
    if proc.returncode:raise RuntimeError(f'DRC process failed {name}: {proc.returncode}: {proc.stderr}')
    drc=json.loads((directory/'drc.json').read_text())
    result={'name':name,'declared_clearance_nm':203200,'requested_width_nm':width,'requested_gap_nm':gap,
        'diagonal':diagonal,'measurements':measurements,'connectivity':connections,
        'drc_command':command,'drc_exit':proc.returncode,
        'drc_counts':{key:len(drc.get(key,[])) for key in ('violations','unconnected_items','schematic_parity')},
        'drc_categories':{t:sum(v['type']==t for v in drc['violations']) for t in sorted({v['type'] for v in drc['violations']})},
        'board_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'refilled_board_sha256':hashlib.sha256(refilled.read_bytes()).hexdigest()}
    (directory/'measurement.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

results=[emit('nominal'),emit('gap_below',gap=202200),emit('gap_above',gap=204200),
         emit('width_narrow',width=283000),emit('width_wide',width=303000),emit('diagonal',diagonal=True)]
(ROOT/'result.json').write_text(json.dumps({'pcbnew_version':k.Version(),'results':results},indent=2)+'\n')
for r in results:print(r['name'],{s:(m['gap_nm'],m['exact_nominal_gap']) for s,m in r['measurements'].items()},r['drc_counts'],r['drc_categories'],r['connectivity']['reference_zone_connected'])
