"""Read-only native PCB audit; run with /usr/bin/python3 (KiCad 10)."""
import collections
import hashlib
import json
from pathlib import Path
import pcbnew as p

ROOT = Path('/work/experiments')
SOURCE = Path('/candidate/project/Polygonus-Universal-Base.kicad_pcb')
b = p.LoadBoard(str(SOURCE))
conn = b.GetConnectivity()
mm = lambda v: round(p.ToMM(v), 6)
xy = lambda v: [mm(v.x), mm(v.y)]
uid = lambda v: v.m_Uuid.AsString()

def pad_info(pad):
    return dict(ref=pad.GetParentFootprint().GetReference(), pad=pad.GetNumber(),
                uuid=uid(pad), net=pad.GetNetname(), position_mm=xy(pad.GetPosition()),
                size_mm=xy(pad.GetSize()), drill_mm=xy(pad.GetDrillSize()),
                attribute=pad.GetAttribute(), layer_set=pad.GetLayerSet().FmtBin())

out = dict(tool=p.Version(), source=str(SOURCE), sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest())
out['holes_no_annulus'] = [pad_info(x) for x in b.GetPads()
    if x.GetAttribute() == p.PAD_ATTRIB_PTH and
       min(x.GetSize().x-x.GetDrillSize().x, x.GetSize().y-x.GetDrillSize().y) <= 0]
out['target_grounds'] = []
for fp in b.GetFootprints():
    if fp.GetReference() in ['C15', 'C14', 'U7', 'U3', 'U15', 'U20', 'U21', 'U1', 'U2']:
        for pad in fp.Pads():
            if pad.GetNetname() != 'GND': continue
            info=pad_info(pad)
            info['connected_tracks'] = [dict(uuid=uid(t), layer=b.GetLayerName(t.GetLayer()),
                start_mm=xy(t.GetStart()), end_mm=xy(t.GetEnd()), width_mm=mm(t.GetWidth()),
                type=t.GetClass()) for t in conn.GetConnectedTracks(pad)]
            info['transitively_connected_item_type_counts'] = dict(collections.Counter(t.GetClass() for t in conn.GetConnectedItems(pad)))
            out['target_grounds'].append(info)
out['zone_summary'] = []
for z in b.Zones():
    out['zone_summary'].append(dict(uuid=uid(z), net=z.GetNetname(),
        layers=[b.GetLayerName(l) for l in z.GetLayerSet().Seq()],
        thermal_gap_mm=mm(z.GetThermalReliefGap()), spoke_width_mm=mm(z.GetThermalReliefSpokeWidth()),
        filled=z.IsFilled(), filled_area_mm2=z.GetFilledArea()/1e12))

# Compare each schematic pin's assignment with the board's saved assignments.
expected=json.loads(Path('/evidence/work/netlist/connectivity.json').read_text())
ep={key:net for net,pins in expected['nets'].items() for key in pins}
bp=collections.defaultdict(set)
for pad in b.GetPads():
    if pad.GetNumber(): bp[f'{pad.GetParentFootprint().GetReference()}.{pad.GetNumber()}'].add(pad.GetNetname())
out['pin_net_discrepancies'] = [dict(pin=k, schematic_net=v, board_nets=sorted(bp.get(k, [])))
    for k,v in ep.items() if bp.get(k) != {v}]
out['extra_board_pins'] = [dict(pin=k, nets=sorted(v)) for k,v in bp.items() if k not in ep]

# Geometric inventory, no interpretation as current capacity or effective parallel resistance.
out['selected_net_routes']={}
for name in ['GND','/12V_MR','/12V_MR_FUSE','/HeaterNeg','/HeaterNeg2','/ETB1_+','/ETB1_-','/CAN2+','/CAN2-']:
    tracks=[t for t in b.GetTracks() if t.GetNetname()==name and t.GetClass()!='PCB_VIA']
    vias=[t for t in b.GetTracks() if t.GetNetname()==name and t.GetClass()=='PCB_VIA']
    out['selected_net_routes'][name]=dict(segment_count=len(tracks),
        total_segment_length_mm=round(sum(mm(t.GetLength()) for t in tracks),6),
        widths_mm=dict(collections.Counter(str(mm(t.GetWidth())) for t in tracks)),
        layers=dict(collections.Counter(b.GetLayerName(t.GetLayer()) for t in tracks)),
        via_count=len(vias),
        pads=[pad_info(x) for x in b.GetPads() if x.GetNetname()==name])
out['can_termination_footprints']=[dict(ref=f.GetReference(), value=f.GetValue(), dnp=f.IsDNP(),
        pos_mm=xy(f.GetPosition()), pads=[pad_info(x) for x in f.Pads()])
    for f in b.GetFootprints() if f.GetReference() in ['R36','R37']]
ROOT.joinpath('native_audit.json').write_text(json.dumps(out, indent=2)+'\n')
print(json.dumps({k:out[k] for k in ['tool','sha256','holes_no_annulus','pin_net_discrepancies','extra_board_pins','zone_summary']}, indent=2))
