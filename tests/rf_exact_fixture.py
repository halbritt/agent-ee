"""Owned straight CPW witness with explicit grounded flanks and a backing F.Cu pour."""
from pathlib import Path
import json


def emit(path, width=293000, gap=203200, missing_flank=False, floating=False, diagonal=False, arc=False):
    import pcbnew as k
    import math
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    board = k.BOARD()
    board.SetCopperLayerCount(4)
    board.GetDesignSettings().m_NetSettings.GetDefaultNetclass().SetClearance(203200)
    nets = {}
    for name in ('/RF', 'GND'):
        nets[name] = k.NETINFO_ITEM(board, name)
        board.Add(nets[name])
    def point(x,y):
        if diagonal:
            x,y = 20000000+(x-y)/math.sqrt(2), 20000000+(x+y-40000000)/math.sqrt(2)
        return k.VECTOR2I(round(x),round(y))
    def track(a,b,net,w,curve=False):
        t = k.PCB_ARC(board) if curve else k.PCB_TRACK(board)
        t.SetStart(point(*a)); t.SetEnd(point(*b))
        if curve:
            t.SetMid(point(20000000,21000000))
        t.SetWidth(w); t.SetLayer(k.F_Cu); t.SetNet(nets[net]); board.Add(t)
        return t.m_Uuid.AsString()
    def pad(ref,x,y,net,w):
        f = k.FOOTPRINT(board); f.SetReference(ref); f.SetAttributes(k.FP_SMD)
        f.Reference().SetLayer(k.F_Fab); f.Value().SetLayer(k.F_Fab); board.Add(f)
        p = k.PAD(f); p.SetNumber('1'); p.SetShape(k.PAD_SHAPE_CIRCLE)
        p.SetAttribute(k.PAD_ATTRIB_SMD); p.SetSize(k.VECTOR2I(w,w))
        layers = k.LSET(); layers.AddLayer(k.F_Cu); p.SetLayerSet(layers)
        p.SetPosition(point(x,y)); p.SetNet(nets[net]); f.Add(p)
        return p.m_Uuid.AsString()
    offset = width//2+gap+500000
    rf = track((10000000,20000000),(30000000,20000000),'/RF',width,arc)
    flanks = [track((8000000,20000000+s*offset),(32000000,20000000+s*offset),'GND',1000000)
              for s in (-1,1) if not (missing_flank and s==1)]
    track((8000000,20000000-offset),(8000000,20000000+offset),'GND',1000000)
    anchor = pad('G1',8000000,20000000-offset,'GND',1000000)
    pad('J1',10000000,20000000,'/RF',width); pad('J2',30000000,20000000,'/RF',width)
    vias = []
    if not floating:
        for x in (8000000,32000000):
            for s in (-1,1):
                v = k.PCB_VIA(board); v.SetPosition(point(x,20000000+s*offset))
                v.SetWidth(600000); v.SetDrill(300000); v.SetViaType(k.VIATYPE_THROUGH)
                v.SetLayerPair(k.F_Cu,k.B_Cu); v.SetNet(nets['GND']); board.Add(v)
                vias.append(v.m_Uuid.AsString())
    zones = []
    for layer in (k.F_Cu,k.In1_Cu):
        z = k.ZONE(board); z.SetLayer(layer); z.SetNet(nets['GND']); z.SetLocalClearance(203200)
        z.SetMinThickness(100000); z.SetPadConnection(k.ZONE_CONNECTION_FULL)
        z.SetIslandRemovalMode(k.ISLAND_REMOVAL_MODE_NEVER if floating else k.ISLAND_REMOVAL_MODE_ALWAYS)
        poly = z.Outline(); poly.NewOutline()
        for x,y in ((2000000,2000000),(38000000,2000000),(38000000,38000000),(2000000,38000000)):
            poly.Append(x,y)
        board.Add(z); zones.append(z.m_Uuid.AsString())
    for a,b in zip(((0,0),(40000000,0),(40000000,40000000),(0,40000000)),
                   ((40000000,0),(40000000,40000000),(0,40000000),(0,0))):
        e = k.PCB_SHAPE(board); e.SetShape(k.SHAPE_T_SEGMENT)
        e.SetStart(k.VECTOR2I(*a)); e.SetEnd(k.VECTOR2I(*b)); e.SetLayer(k.Edge_Cuts)
        e.SetWidth(50000); board.Add(e)
    path.with_suffix('.kicad_dru').write_text('''(version 1)
(rule "RF exact ordinary width" (condition "A.NetName == '/RF'")
 (constraint track_width (min 0.293mm) (max 0.293mm)))
(rule "RF physical clearance" (condition "A.NetName == '/RF' || B.NetName == '/RF'")
 (constraint clearance (min 0.2032mm)))
''')
    path.with_suffix('.kicad_pro').write_text(json.dumps({'board':{'design_settings':{'rules':{
        'min_clearance':.2032,'min_track_width':.1}}},'meta':{'version':1}}))
    k.ZONE_FILLER(board).Fill(board.Zones()); k.SaveBoard(str(path),board)
    return {'rf': rf, 'flanks': flanks, 'anchor': anchor, 'vias': vias, 'zones': zones}
