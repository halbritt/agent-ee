from pathlib import Path
import subprocess, os, json, hashlib, concurrent.futures
out=Path('/work/experiments')
env=os.environ.copy()
env.update(KICAD_CONFIG_HOME=str(out/'kicad-config'),KICAD10_SYMBOL_DIR='/candidate/stock/symbols',KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints',KICAD10_TEMPLATE_DIR='/candidate/stock/template')
commands={
'drc':['kicad-cli','pcb','drc','--format','json','--severity-all','--schematic-parity','--units','mm','-o',str(out/'independent_drc.json'),'/candidate/project/rc_filter.kicad_pcb'],
'netlist':['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(out/'independent_netlist.xml'),'/candidate/project/rc_filter.kicad_sch']}
paths=[Path('/candidate/project/rc_filter.kicad_pcb'),Path('/candidate/project/rc_filter.kicad_sch')]
before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
def run(item):
 name,cmd=item
 p=subprocess.run(cmd,env=env,capture_output=True,text=True)
 return name,{'argv':cmd,'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
with concurrent.futures.ThreadPoolExecutor() as pool: results=dict(pool.map(run,commands.items()))
results['hashes_before']=before
results['hashes_after']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
(out/'native_commands.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results,indent=2))
