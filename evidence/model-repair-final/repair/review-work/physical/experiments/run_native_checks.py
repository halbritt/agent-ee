from pathlib import Path
import os, subprocess, json, concurrent.futures, hashlib
out=Path('/work/experiments')
env=os.environ.copy()
env.update(KICAD_CONFIG_HOME=str(out/'kicad-config'),KICAD10_SYMBOL_DIR='/candidate/stock/symbols',KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints',KICAD10_TEMPLATE_DIR='/candidate/stock/template')
commands={
'drc':['kicad-cli','pcb','drc','--format','json','--severity-all','--schematic-parity','--units','mm','-o',str(out/'drc.json'),'/candidate/project/rc_filter.kicad_pcb'],
'erc':['kicad-cli','sch','erc','--format','json','--severity-all','--units','mm','-o',str(out/'erc.json'),'/candidate/project/rc_filter.kicad_sch'],
'netlist':['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(out/'design.xml'),'/candidate/project/rc_filter.kicad_sch']}
paths=list(Path('/candidate/project').glob('*'))
before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}
def run(item):
 name,cmd=item
 p=subprocess.run(cmd,env=env,cwd=out,capture_output=True,text=True)
 (out/(name+'.console.log')).write_text(p.stdout+p.stderr)
 return {'check':name,'argv':cmd,'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
 results=list(pool.map(run,commands.items()))
after={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}
record={'commands':results,'environment_overrides':{k:env[k] for k in ['KICAD_CONFIG_HOME','KICAD10_SYMBOL_DIR','KICAD10_FOOTPRINT_DIR','KICAD10_TEMPLATE_DIR']},'candidate_project_hashes_before':before,'candidate_project_unchanged':before==after,'assumption':'Saved fill only: no refill option, no candidate SaveBoard or other writes.'}
(out/'native_execution.json').write_text(json.dumps(record,indent=2))
print(json.dumps(record,indent=2))
