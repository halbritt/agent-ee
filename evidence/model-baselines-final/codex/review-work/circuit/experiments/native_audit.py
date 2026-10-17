import re,json,pathlib,xml.etree.ElementTree as ET,hashlib

def parse(path):
 text=pathlib.Path(path).read_text(); tokens=re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+',text); stack=[];root=None
 for t in tokens:
  if t=='(':
   n=[]
   if stack:stack[-1].append(n)
   else:root=n
   stack.append(n)
  elif t==')':stack.pop()
  else:stack[-1].append(json.loads(t) if t.startswith('"') else t)
 return root

def children(n,key):return [x for x in n if isinstance(x,list) and x and x[0]==key]
def one(n,key,default=None):
 x=children(n,key);return x[0][1] if x else default
if __name__=='__main__':
 root=ET.parse('/evidence/work/netlist/design.xml').getroot()
 comps={c.attrib['ref']:c for c in root.findall('./components/comp')}
 pinmap={}
 for net in root.findall('./nets/net'):
  for n in net.findall('node'):pinmap[n.attrib['ref']+'.'+n.attrib['pin']]=dict(net=net.attrib['name'],**n.attrib)
 out=[]
 for ref in ['A1']+['U'+str(i) for i in range(1,22)]+['JP1','R1','R2','R30','R31','R32','R33','R34','R36','R37']:
  c=comps[ref];out.append({'ref':ref,'value':c.findtext('value'),'properties':{p.attrib['name']:p.get('value') for p in c.findall('property')},'pins':[v for k,v in pinmap.items() if v['ref']==ref]})
 pathlib.Path('/work/experiments/component_pin_audit.json').write_text(json.dumps(out,indent=2))
 board=parse('/candidate/project/Polygonus-Universal-Base.kicad_pcb'); fps={one(f,'property'):f for f in []}
 summary=[]
 for f in children(board,'footprint'):
  props={p[1]:p[2] for p in children(f,'property')};ref=props['Reference']
  if ref not in comps:continue
  pads=[]
  for p in children(f,'pad'):
   net=children(p,'net');pads.append({'pad':p[1],'net':net[0][-1] if net else None,'at':children(p,'at'),'layers':children(p,'layers')})
  summary.append({'ref':ref,'library':f[1],'value':props['Value'],'attributes':children(f,'attr'),'pads':pads})
 pathlib.Path('/work/experiments/board_pin_audit.json').write_text(json.dumps(summary,indent=2))
 print('Saved component_pin_audit.json and board_pin_audit.json')
