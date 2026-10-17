"""Independent read-only RC fixture audit. Run: python3 /work/experiments/review.py."""
from pathlib import Path
import hashlib, json, math, re, struct, subprocess, xml.etree.ElementTree as ET
B=Path('/work/experiments')
def command(argv):
 r=subprocess.run(argv,cwd=B,capture_output=True,text=True)
 with (B/'commands.log').open('a') as f: f.write('$ '+repr(argv)+'\nexit='+str(r.returncode)+'\n'+r.stdout+r.stderr+'\n')
 if r.returncode: raise RuntimeError(argv)
 return r

def parse(path):
 text=Path(path).read_text(); toks=re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+',text)
 stack=[]; root=None
 for t in toks:
  if t=='(':
   item=[]
   if stack: stack[-1].append(item)
   stack.append(item)
  elif t==')': root=stack.pop()
  else: stack[-1].append(json.loads(t) if t.startswith('"') else t)
 return root

def children(s,key): return [x for x in s if isinstance(x,list) and x and x[0]==key]
def child(s,key): return children(s,key)[0]
def props(s): return {x[1]:x[2] for x in children(s,'property')}

schpath=Path('/candidate/project/rc_filter.kicad_sch'); pcbpath=Path('/candidate/project/rc_filter.kicad_pcb')
sch=parse(schpath); pcb=parse(pcbpath)
# Derive native schematic pin nets directly from embedded library pin positions,
# instance rotations and connected wire endpoints. This fixture has no mid-wire pins.
libs={x[1]:x for x in children(child(sch,'lib_symbols'),'symbol')}
parent={}
def pt(x): return tuple(round(float(t),6) for t in x)
def find(p):
 parent.setdefault(p,p)
 if parent[p]!=p: parent[p]=find(parent[p])
 return parent[p]
def union(a,b): parent[find(a)]=find(b)
for w in children(sch,'wire'):
 ends=child(w,'pts')[1:]; union(pt(ends[0][1:]),pt(ends[1][1:]))
labels={}
for label in children(sch,'label'): labels.setdefault(find(pt(child(label,'at')[1:3])),set()).add('/'+label[1])
pins={}; values={}; identities={}
for inst in children(sch,'symbol'):
 p=props(inst); ref=p['Reference']; lib=child(inst,'lib_id')[1]; at=child(inst,'at'); x,y,deg=map(float,at[1:4]); angle=math.radians(deg)
 localpins=[pin for sub in children(libs[lib],'symbol') for pin in children(sub,'pin')]
 for pin in localpins:
  lx,ly=map(float,child(pin,'at')[1:3]); coord=(round(x+math.cos(angle)*lx-math.sin(angle)*ly,6),round(y-math.sin(angle)*lx-math.cos(angle)*ly,6))
  root=find(coord)
  if lib=='power:GND': labels.setdefault(root,set()).add('GND')
  pins[ref+'.'+child(pin,'number')[1]]=root
 if not ref.startswith('#'): values[ref]=p['Value']; identities[ref]={'lib_id':lib,'uuid':child(inst,'uuid')[1],'dnp':child(inst,'dnp')[1]}
native_nets={}
for pin,root in pins.items():
 if pin.startswith('#'): continue
 net=sorted(labels.get(find(root),[])); assert len(net)==1,(pin,net)
 native_nets.setdefault(net[0],[]).append(pin)
native_nets={k:sorted(v) for k,v in native_nets.items()}
command(['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(B/'fresh-netlist.xml'),str(schpath)])
def xml_nets(path):
 tree=ET.parse(path)
 return {n.attrib['name']:sorted(x.attrib['ref']+'.'+x.attrib['pin'] for x in n.findall('node')) for n in tree.findall('./nets/net')}
assert native_nets==xml_nets(B/'fresh-netlist.xml')==xml_nets('/evidence/work/netlist/design.xml')
board_nets={}; board_values={}
for fp in children(pcb,'footprint'):
 ref=props(fp)['Reference']; board_values[ref]=props(fp)['Value']
 for pad in children(fp,'pad'): board_nets.setdefault(child(pad,'net')[1],[]).append(ref+'.'+pad[1])
board_nets={k:sorted(v) for k,v in board_nets.items()}; assert board_nets==native_nets and board_values==values
tracks=[{'uuid':child(s,'uuid')[1],'net':child(s,'net')[1],'start':child(s,'start')[1:],'end':child(s,'end')[1:],'width_mm':float(child(s,'width')[1])} for s in children(pcb,'segment')]
assert len(tracks)==5 and all(s['width_mm']>=0.20 for s in tracks)
# Independent ideal response: zero initial voltage, ideal 1 V source, no output load.
R=10000.0; C=100e-9; tau=R*C
calc={str(t):1-math.exp(-t/tau) for t in [0.001,0.005]}
# Read actual check binary trace rather than trusting log/exit status.
raw=Path('/evidence/work/rc-step/result.raw').read_bytes(); header,data=raw.split(b'Binary:\n',1); header=header.decode()
nvars=int(re.search(r'No. Variables:\s*(\d+)',header)[1]); npts=int(re.search(r'No. Points:\s*(\d+)',header)[1])
names=re.findall(r'^\s*\d+\s+(\S+)\s+\S+',header,re.M); assert len(names)==nvars
assert len(data)==nvars*npts*8
rows=list(struct.iter_unpack('<'+'d'*nvars,data)); ti=names.index('time'); oi=names.index('v(out)'); ii=names.index('v(in)')
def sample(t):
 for a,b in zip(rows,rows[1:]):
  if a[ti]<=t<=b[ti]:
   f=(t-a[ti])/(b[ti]-a[ti]); return a[oi]+f*(b[oi]-a[oi])
 raise ValueError(t)
samples={str(t):sample(t) for t in [0.001,0.005]}; assert .62<=samples['0.001']<=.65
# Independent simulation uses finer steps and a delayed 1 ns rise to make pre-step state explicit.
deck='''* independent native-value RC verification, initial steady 0 V
R1 in out 10k
C1 out 0 100n
V1 in 0 PWL(0 0 100u 0 100.001u 1 6m 1)
.tran 0.5u 5.1m 0 0.5u
.meas tran out_1ms FIND v(out) AT=1.1m
.meas tran out_5ms FIND v(out) AT=5.1m
.end
'''
(B/'independent.cir').write_text(deck)
command(['ngspice','-n','-b','-o',str(B/'independent.log'),str(B/'independent.cir')])
report={'native_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [schpath,pcbpath]},'native_component_values':values,'native_identities':identities,'native_schematic_nets':native_nets,'fresh_and_check_xml_match_native':True,'board_pad_nets_match_native':True,'board_values_match_schematic':True,'tracks':tracks,'minimum_track_width_mm':min(s['width_mm'] for s in tracks),'R_ohm':R,'C_farad':C,'tau_seconds':tau,'analytic_zero_rise_response_v':calc,'check_trace_header':header,'check_trace_first_point':dict(zip(names,rows[0])),'check_trace_last_point':dict(zip(names,rows[-1])),'check_trace_interpolated_measurements_v':samples,'check_trace_vs_analytic_error_v':{t:samples[t]-calc[t] for t in calc},'independent_ngspice_measurements':(B/'independent.log').read_text(),'assumptions':['Zero initial capacitor voltage; ideal source; unloaded output; ideal nominal R and C only.','Check stimulus is a 1 ns linear rise at t=0; its negligible difference from a mathematical step is covered by numeric comparison.','Independent deck delays the same step by 100 us; measurements are shifted by the same 100 us.','Native board text checks pad net identities and track widths, not geometric copper connectivity; saved-board DRC is the connectivity evidence.'],'limitations':['No candidate edits or zone refill.','No tolerance, temperature, parasitic, loading, ADC, EMI or hardware verification.','No specified physical part models/ratings, reset/brownout logic or active devices exist to verify in this fixture.']}
(B/'audit.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
