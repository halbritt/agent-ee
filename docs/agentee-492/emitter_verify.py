"""Check the retained emitter controls against reloaded native boards and DRC."""
import json
from pathlib import Path
import sys
from fractions import Fraction

import pcbnew as k

root=Path(sys.argv[1])
record=json.loads((root/'result.json').read_text())
expected={
    'nominal': (293000,203200,{}),
    'gap_below': (293000,202200,{'clearance':3}),
    'gap_above': (293000,204200,{}),
    'width_narrow': (283000,203200,{'track_width':1}),
    'width_wide': (303000,203200,{'track_width':1}),
    'diagonal': (293000,None,{})}
assert {r['name'] for r in record['results']}==set(expected)
for result in record['results']:
    name=result['name']; width,gap,categories=expected[name]; directory=root/name
    board=k.LoadBoard(str(directory/'refilled.kicad_pcb')); board.BuildConnectivity()
    signal=[t for t in board.GetTracks() if t.GetClass()=='PCB_TRACK' and t.GetNetname()=='/RF']
    assert len(signal)==1 and signal[0].GetWidth()==width
    a=signal[0].GetStart(); b=signal[0].GetEnd(); dx=b.x-a.x;dy=b.y-a.y
    flanks=[]
    for t in board.GetTracks():
        if t.GetClass()!='PCB_TRACK' or t.GetNetname()!='GND':continue
        p=t.GetStart();q=t.GetEnd()
        if dx*(q.y-p.y)-dy*(q.x-p.x):continue
        cross=dx*(p.y-a.y)-dy*(p.x-a.x)
        distance_squared=Fraction(cross*cross,dx*dx+dy*dy)
        if gap is not None:
            assert distance_squared==(Fraction(width+t.GetWidth(),2)+gap)**2
        else:
            assert distance_squared!=(Fraction(width+t.GetWidth(),2)+203200)**2
        flanks.append((t,cross))
    assert len(flanks)==2 and flanks[0][1]*flanks[1][1]<0
    pad=next(p for f in board.GetFootprints() if f.GetReference()=='G1' for p in f.Pads())
    connected={i.m_Uuid.AsString() for i in board.GetConnectivity().GetConnectedItems(pad)}
    assert all(t.m_Uuid.AsString() in connected for t,_ in flanks)
    assert all(z.m_Uuid.AsString() in connected and z.HasFilledPolysForLayer(k.In1_Cu) for z in board.Zones())
    drc=json.loads((directory/'drc.json').read_text())
    actual={v['type']:sum(w['type']==v['type'] for w in drc['violations']) for v in drc['violations']}
    assert actual==categories and not drc['unconnected_items']
    assert result['connectivity']['ordinary_primitives_unchanged_after_refill']
    rules=(directory/'refilled.kicad_dru').read_text()
    assert '(min 0.2032mm)' in rules and '(min 0.293mm) (max 0.293mm)' in rules
    project=json.loads((directory/'refilled.kicad_pro').read_text())
    assert project['board']['design_settings']['rules']['min_clearance']==.2032
    print(name, 'verified: native geometry, both flanks, connected reference, DRC controls, unchanged rules')
print('PASS: six native saved/refilled controls; diagonal exact equality intentionally NOT accepted')
