from pathlib import Path
import subprocess, os, shutil, json, time, collections
import xml.etree.ElementTree as ET
O=Path('/work/experiments'); C=Path('/candidate/project')
cfg=O/'kicad-config'/'10.0';cfg.mkdir(parents=True,exist_ok=True)
for f in ['fp-lib-table','sym-lib-table']:
    shutil.copyfile(Path('/evidence/work/drc/kicad-config/10.0')/f,cfg/f)
env=os.environ.copy()
env.update(KICAD_CONFIG_HOME=str(cfg.parent),KICAD10_SYMBOL_DIR='/candidate/stock/symbols',KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints',KICAD10_TEMPLATE_DIR='/candidate/stock/template')
commands=[['kicad-cli','sch','erc','--format','json','--severity-all','--units','mm','-o',str(O/'erc.json'),str(C/'Polygonus-Universal-Base.kicad_sch')],
 ['kicad-cli','pcb','drc','--format','json','--severity-all','--schematic-parity','--units','mm','-o',str(O/'drc.json'),str(C/'Polygonus-Universal-Base.kicad_pcb')],
 ['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(O/'design.xml'),str(C/'Polygonus-Universal-Base.kicad_sch')],
 ['kicad-cli','pcb','export','drill','--format','excellon','--excellon-separate-th','--generate-report','-o',str(O/'drills')+'/',str(C/'Polygonus-Universal-Base.kicad_pcb')]]
records=[]
for i,argv in enumerate(commands):
    t=time.monotonic(); r=subprocess.run(argv,capture_output=True,text=True,env=env,cwd=O,timeout=120)
    log=f'command-{i}.log';(O/log).write_text(r.stdout+r.stderr)
    records.append({'argv':argv,'exit':r.returncode,'seconds':time.monotonic()-t,'log':log})
(O/'commands.json').write_text(json.dumps({'environment_overrides':{k:env[k] for k in ['KICAD_CONFIG_HOME','KICAD10_SYMBOL_DIR','KICAD10_FOOTPRINT_DIR','KICAD10_TEMPLATE_DIR']},'commands':records},indent=2))
def normal(v): return json.dumps(v,sort_keys=True)
drc=json.loads((O/'drc.json').read_text()); dref=json.loads(Path('/evidence/work/drc/drc.json').read_text())
erc=json.loads((O/'erc.json').read_text()); eref=json.loads(Path('/evidence/work/erc/erc.json').read_text())
def nets(p):
    r=ET.parse(p).getroot();return {n.attrib['name']:sorted((x.attrib['ref'],x.attrib['pin']) for x in n) for n in r.find('nets')}
summary={'drc':{k:collections.Counter(map(normal,drc[k]))==collections.Counter(map(normal,dref[k])) for k in ['violations','schematic_parity','unconnected_items']},
 'erc_sheets_equal':erc['sheets']==eref['sheets'],'netlist_memberships_equal':nets(O/'design.xml')==nets('/evidence/work/netlist/design.xml')}
(O/'verification.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
