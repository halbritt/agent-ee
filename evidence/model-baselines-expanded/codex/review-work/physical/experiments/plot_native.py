"""Saved copper geometry plots, no fill/save operations. Dimensions in mm."""
import pcbnew as p
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Circle, Rectangle
from matplotlib.transforms import Affine2D
from pathlib import Path
b=p.LoadBoard('/candidate/project/Polygonus-Universal-Base.kicad_pcb')
root=Path('/work/experiments')
xy=lambda v:(p.ToMM(v.x),p.ToMM(v.y))

fig,ax=plt.subplots(figsize=(15,10))
for f in b.GetFootprints():
 for pad in f.Pads():
  if not pad.GetNumber(): continue
  ax.plot(*xy(pad.GetPosition()),'.',color='gray',ms=1.5)
 if f.GetReference() in ['J1','J2','U1','U2','U20','U21','R36','R37','F1','U3','U15']:
  ax.text(*xy(f.GetPosition()),f.GetReference(),fontsize=8)
for name,color in [('/CAN2+','red'),('/CAN2-','blue')]:
 for t in b.GetTracks():
  if t.GetNetname()!=name: continue
  a=xy(t.GetStart());c=xy(t.GetEnd())
  ax.plot([a[0],c[0]],[a[1],c[1]],color=color,lw=1,label=name if name not in ax.get_legend_handles_labels()[1] else None)
ax.set_title('Saved CAN2 routes (all layers; projected into XY)')
ax.set_aspect('equal');ax.invert_yaxis();ax.set_xlabel('X, mm');ax.set_ylabel('Y, mm');ax.legend();fig.tight_layout()
fig.savefig(root/'can2-layout.png',dpi=180)
plt.close(fig)

fig,axs=plt.subplots(1,3,figsize=(15,5))
for ax,ref,num in zip(axs,['C15','U7','C14'],['2','4','2']):
 target=next(x for f in b.GetFootprints() if f.GetReference()==ref for x in f.Pads() if x.GetNumber()==num)
 x,y=xy(target.GetPosition())
 for z in b.Zones():
  if z.GetNetname()!='GND' or not z.IsOnLayer(p.F_Cu):continue
  poly=z.GetFilledPolysList(p.F_Cu)
  for oi in range(poly.OutlineCount()):
   ol=poly.COutline(oi)
   verts=[xy(ol.CPoint(i)) for i in range(ol.PointCount())]
   ax.add_patch(Polygon(verts,color='#d8ead2'))
   for hi in range(poly.HoleCount(oi)):
    hole=poly.CHole(oi,hi)
    vs=[xy(hole.CPoint(i)) for i in range(hole.PointCount())]
    ax.add_patch(Polygon(vs,color='white'))
 for t in b.GetTracks():
  if t.GetLayer()!=p.F_Cu or t.GetClass()=='PCB_VIA':continue
  a,c=xy(t.GetStart()),xy(t.GetEnd())
  if max(a[0],c[0])<x-2 or min(a[0],c[0])>x+2 or max(a[1],c[1])<y-2 or min(a[1],c[1])>y+2: continue
  ax.plot([a[0],c[0]],[a[1],c[1]],color='darkgreen' if t.GetNetname()=='GND' else '#cc7c62',lw=p.ToMM(t.GetWidth())*35,solid_capstyle='round')
 for f in b.GetFootprints():
  for pad in f.Pads():
   px,py=xy(pad.GetPosition())
   if abs(px-x)>2 or abs(py-y)>2:continue
   sx,sy=xy(pad.GetSize())
   patch=Rectangle((-sx/2,-sy/2),sx,sy,color='darkgreen' if pad.GetNetname()=='GND' else '#cc7c62')
   patch.set_transform(Affine2D().rotate_deg(pad.GetOrientationDegrees()).translate(px,py)+ax.transData)
   ax.add_patch(patch)
 for t in b.GetTracks():
  if t.GetClass()=='PCB_VIA':
   vx,vy=xy(t.GetPosition())
   if abs(vx-x)<2 and abs(vy-y)<2:
    ax.add_patch(Circle((vx,vy),p.ToMM(t.GetWidth())/2,color='darkgreen' if t.GetNetname()=='GND' else '#cc7c62'))
    ax.add_patch(Circle((vx,vy),p.ToMM(t.GetDrillValue())/2,color='white'))
 ax.set_xlim(x-1.8,x+1.8);ax.set_ylim(y+1.8,y-1.8);ax.set_aspect('equal')
 ax.set_title(f'{ref}.{num} saved F.Cu ground\nDirect ground tracks dark green');ax.set_xlabel('X, mm');ax.set_ylabel('Y, mm')
fig.tight_layout();fig.savefig(root/'thermal-details.png',dpi=200)
