import urllib.request,json,pathlib,concurrent.futures,hashlib
base=pathlib.Path('/work/experiments/references')
items=[]
for name,files in [('Polygonus',['README.md','polygonus-Shortage-Version.kicad_sch','triggers.kicad_sch','mcu.kicad_sch','export/V1.6/polygonus-Shortage-Version.pdf']),('wideband',['README.md','hardware/README.md','firmware/boards/f0_module/readme.md','hardware/board_module/wideband_controller.kicad_sch'])]:
 tree=json.loads((base/(name+'_tree.json')).read_text());sha=tree['sha'];repo='FOME-Tech/Polygonus' if name=='Polygonus' else 'mck1117/wideband'
 for f in files:items.append((name,f,'https://raw.githubusercontent.com/'+repo+'/'+sha+'/'+f))
def fetch(item):
 name,f,url=item;p=base/(name+'_'+f.replace('/','_'))
 try:
  b=urllib.request.urlopen(url,timeout=25).read();p.write_bytes(b);return dict(url=url,file=str(p),bytes=len(b),sha256=hashlib.sha256(b).hexdigest())
 except Exception as e:return dict(url=url,error=str(e))
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:out=list(pool.map(fetch,items))
(base/'modules_fetch_log.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
