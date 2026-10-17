from pathlib import Path
import subprocess, json, os, hashlib, sys
out=Path('/work/experiments')
env=os.environ.copy()
env.update(KICAD_CONFIG_HOME=str(out/'kicad-config'),KICAD10_SYMBOL_DIR='/candidate/stock/symbols',KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints',KICAD10_TEMPLATE_DIR='/candidate/stock/template')
commands=[
 ['kicad-cli','version'], ['ngspice','--version'],
 ['kicad-cli','pcb','drc','--format','json','--severity-all','--schematic-parity','--all-track-errors','--exit-code-violations','--units','mm','-o',str(out/'drc.json'),'/candidate/project/rc_filter.kicad_pcb'],
 ['kicad-cli','sch','erc','--format','json','--severity-all','--exit-code-violations','-o',str(out/'erc.json'),'/candidate/project/rc_filter.kicad_sch'],
 ['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(out/'design.xml'),'/candidate/project/rc_filter.kicad_sch']]
records=[]
for i,argv in enumerate(commands):
 p=subprocess.run(argv,capture_output=True,text=True,env=env)
 (out/f'command-{i}.stdout.log').write_text(p.stdout)
 (out/f'command-{i}.stderr.log').write_text(p.stderr)
 records.append(dict(argv=argv,returncode=p.returncode,stdout=f'command-{i}.stdout.log',stderr=f'command-{i}.stderr.log'))
files=['project/rc_filter.kicad_pcb','project/rc_filter.kicad_sch','project/rc_filter.kicad_pro','requirements/fixture-requirements.md']
(out/'commands.json').write_text(json.dumps(dict(python=sys.version,environment_overrides={k:env[k] for k in ['KICAD_CONFIG_HOME','KICAD10_SYMBOL_DIR','KICAD10_FOOTPRINT_DIR','KICAD10_TEMPLATE_DIR']},commands=records,input_hashes={f:hashlib.sha256((Path('/candidate')/f).read_bytes()).hexdigest() for f in files},assumptions=['Candidate read only; no --refill-zones or --save-board.', 'Native check reports are inspected separately; exit status alone is not used as electrical evidence.']),indent=2))
print(json.dumps(records,indent=2))
