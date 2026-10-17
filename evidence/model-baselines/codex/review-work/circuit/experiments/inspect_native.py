import re,json,pathlib,hashlib

def parse(path):
 s=pathlib.Path(path).read_text(); toks=re.findall(r'"(?:\\.|[^"\\])*"|[^\s()]+|[()]',s); stack=[]
 for t in toks:
  if t=='(': stack.append([])
  elif t==')':
   x=stack.pop()
   if stack:stack[-1].append(x)
   else:return x
  else:stack[-1].append(json.loads(t) if t.startswith('"') else t)
def children(s,k):return [x for x in s if isinstance(x,list) and x and x[0]==k]
def child(s,k):return next(iter(children(s,k)),[])
def props(s):return {x[1]:x[2] for x in children(s,'property')}
if __name__=='__main__':
 out=[]
 for file in pathlib.Path('/candidate/project').glob('*.kicad_sch'):
  s=parse(file);out.append('\nFILE '+str(file))
  for lib in children(child(s,'lib_symbols'),'symbol'):
   out.append('LIB '+lib[1]+' '+json.dumps(props(lib)))
   for sub in children(lib,'symbol'):
    for pin in children(sub,'pin'):out.append(' pin '+str((child(pin,'number'),child(pin,'name'),pin[1])))
  for sym in children(s,'symbol'):
   p=props(sym)
   if not p.get('Reference','').startswith('#'):out.append('INSTANCE '+json.dumps({'props':p,'at':child(sym,'at'),'unit':child(sym,'unit'),'lib_id':child(sym,'lib_id'),'dnp':child(sym,'dnp'),'uuid':child(sym,'uuid')}))
 pathlib.Path('/work/experiments/native-symbols.txt').write_text('\n'.join(out))
