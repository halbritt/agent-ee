import re,json,pathlib,xml.etree.ElementTree as ET

def parse(path):
 t=re.findall(r'"(?:\\.|[^"\\])*"|[^\s()]+|[()]',pathlib.Path(path).read_text()); root=[]; st=[root]
 for x in t:
  if x=='(': a=[]; st[-1].append(a); st.append(a)
  elif x==')':st.pop()
  else: st[-1].append(json.loads(x) if x.startswith('"') else x)
 return root[0]
def children(a,key): return [x for x in a if isinstance(x,list) and x and x[0]==key]
def one(a,key):return next(iter(children(a,key)),[])
if __name__=='__main__':
 for p in pathlib.Path('/candidate/project').glob('*.kicad_sch'):
  if p.name not in ['Polygonus-Universal-Base.kicad_sch','VR Conditioner.kicad_sch','vr_module_poly.kicad_sch','etb-9201.kicad_sch','IGBT outputs.kicad_sch','Lowside Adder.kicad_sch','wbo-can-trans.kicad_sch']:continue
  a=parse(p); print('\nFILE',p.name)
  for x in children(a,'text'):print('TEXT',x[1])
  for x in children(a,'symbol'):
   ps={y[1]:y[2] for y in children(x,'property')}
   if not ps.get('Reference','').startswith('#'): print(ps,one(x,'at'))
