import pcbnew,pathlib,json,hashlib,collections,datetime,xml.etree.ElementTree as ET
root=pathlib.Path('/work/experiments'); p='/candidate/project/Polygonus-Universal-Base.kicad_pcb'
b=pcbnew.LoadBoard(p); conn=b.GetConnectivity(); conn.Build(b)
fps={f.GetReference():f for f in b.GetFootprints()}
items=[]
for r,n in [('C15','2'),('U7','4'),('C14','2')]:
 pad=next(x for x in fps[r].Pads() if x.GetNumber()==n)
 tracks=conn.GetConnectedTracks(pad)
 items.append({'ref':r,'pad':n,'pad_uuid':pad.m_Uuid.AsString(),'net':pad.GetNetname(),'connected_tracks':[{'uuid':t.m_Uuid.AsString(),'net':t.GetNetname(),'layer':b.GetLayerName(t.GetLayer()),'start_mm':[t.GetStart().x/1e6,t.GetStart().y/1e6],'end_mm':[t.GetEnd().x/1e6,t.GetEnd().y/1e6],'width_mm':t.GetWidth()/1e6} for t in tracks]})
(root/'thermal-track-connections.json').write_text(json.dumps(items,indent=2))
files=['/evidence/work/drc/drc.json','/evidence/work/erc/erc.json','/evidence/work/netlist/design.xml','/evidence/work/netlist/connectivity.json',p,'/candidate/project/VR Conditioner.kicad_sch','/candidate/project/VR conditioner.kicad_sch']
(root/'input-hashes.json').write_text(json.dumps({s:hashlib.sha256(pathlib.Path(s).read_bytes()).hexdigest() for s in files},indent=2))
summary={}
for s in ['/evidence/work/drc/drc.json',str(root/'independent-drc.json')]:
 d=json.load(open(s));summary[s]={'violations':len(d['violations']),'unconnected_items':len(d['unconnected_items']),'schematic_parity':len(d['schematic_parity']),'by_type_and_exclusion':dict(collections.Counter(f"{v['type']}; excluded={v.get('excluded',False)}" for v in d['violations'])),'error_entries':[{'index':i,**v} for i,v in enumerate(d['violations']) if v['severity']=='error']}
(root/'drc-comparison.json').write_text(json.dumps(summary,indent=2))
print('Saved audit records. Thermal direct track counts:',[(i['ref'],len(i['connected_tracks'])) for i in items])
