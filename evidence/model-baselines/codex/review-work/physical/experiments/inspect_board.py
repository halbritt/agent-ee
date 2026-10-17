import pcbnew as p, json, hashlib, pathlib, collections, math
src='/candidate/project/Polygonus-Universal-Base.kicad_pcb'
b=p.LoadBoard(src)
mm=lambda v:p.ToMM(v)
xy=lambda v:[mm(v.x),mm(v.y)]
fps={f.GetReference():f for f in b.GetFootprints()}
out={'pcbnew_version':p.Version(),'board_sha256':hashlib.sha256(open(src,'rb').read()).hexdigest(),'footprints':{}}
for ref in ['J1','J2','U1','U2','U3','U15','C14','C15','U7','R36','R37']:
 f=fps[ref]
 out['footprints'][ref]={'lib':str(f.GetFPID().GetLibItemName()),'position':xy(f.GetPosition()),'orientation_deg':f.GetOrientationDegrees(),'dnp':f.IsDNP(),'pads':[]}
 for pad in f.Pads():
  if ref=='J1' and pad.GetNumber():continue
  out['footprints'][ref]['pads'].append({'number':pad.GetNumber(),'uuid':pad.m_Uuid.AsString(),'net':pad.GetNetname(),'size_mm':xy(pad.GetSize()),'drill_mm':xy(pad.GetDrillSize()),'position_mm':xy(pad.GetPosition()),'attribute':pad.GetAttribute(),'layers':pad.GetLayerSet().FmtBin(),'annular_simple_mm':(min(xy(pad.GetSize()))-max(xy(pad.GetDrillSize())))/2 if pad.GetDrillSize().x else None})
out['zones']=[{'uuid':z.m_Uuid.AsString(),'net':z.GetNetname(),'layers':[b.GetLayerName(l) for l in z.GetLayerSet().Seq()],'thermal_gap_mm':mm(z.GetThermalReliefGap()),'thermal_spoke_mm':mm(z.GetThermalReliefSpokeWidth()),'filled':z.IsFilled()} for z in b.Zones()]
# Compare normalized pad locations against bundled symbol-selected J2 footprint.
l=p.FootprintLoad('/candidate/project/fome-kicad-libs/fome_modules.pretty','polygonus')
lf={pad.GetNumber():pad for pad in l.Pads()}
f=fps['J2']; f.SetOrientationDegrees(0);f.SetPosition(p.VECTOR2I(0,0)) # RAM only; never saved.
out['j2_pad_comparison']={'assumption':'Saved footprint normalized to zero position/orientation in memory only; library has default origin/orientation. No file modification.', 'saved_pad_count':len(list(f.Pads())),'lib_pad_count':len(lf),'delta_mm':[]}
for pad in f.Pads():
 if pad.GetNumber() in lf:
  libpad=lf[pad.GetNumber()]; a=xy(pad.GetPosition());c=xy(libpad.GetPosition())
  out['j2_pad_comparison']['delta_mm'].append({'pad':pad.GetNumber(),'saved':a,'library':c,'delta':[a[0]-c[0],a[1]-c[1]]})
pathlib.Path('/work/experiments/board_inspection.json').write_text(json.dumps(out,indent=2))
print(json.dumps({k:v for k,v in out.items() if k not in ['footprints','j2_pad_comparison']},indent=2))
for ref in ['J1','J2','R36','R37']:
 print(ref,json.dumps(out['footprints'][ref],indent=2)[:2000])
print('J2_DELTA_FIRST',out['j2_pad_comparison']['delta_mm'][:8])
