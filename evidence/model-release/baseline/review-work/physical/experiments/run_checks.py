import subprocess, os, json, hashlib, pathlib, sys
root=pathlib.Path('/work/experiments'); board='/candidate/project/Polygonus-Universal-Base.kicad_pcb'; sch='/candidate/project/Polygonus-Universal-Base.kicad_sch'
env=os.environ.copy();env.update(KICAD10_SYMBOL_DIR='/candidate/stock/symbols',KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints',KICAD10_TEMPLATE_DIR='/candidate/stock/template',KICAD_CONFIG_HOME=str(root/'kicad-config'))
commands=[['kicad-cli','version'],['/usr/bin/python3','--version'],['kicad-cli','pcb','drc','--format','json','--severity-all','--schematic-parity','--units','mm','-o',str(root/'independent-drc.json'),board],['kicad-cli','pcb','export','drill','--excellon-separate-th','--generate-report','--report-path',str(root/'drill-report.txt'),'-o',str(root/'drills'),board],['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(root/'independent-netlist.xml'),sch]]
records=[]
for i,cmd in enumerate(commands):
 r=subprocess.run(cmd,env=env,capture_output=True,text=True);(root/f'command-{i}.log').write_text(r.stdout+r.stderr); records.append(dict(argv=cmd,exit_code=r.returncode,log=f'command-{i}.log'))
print(json.dumps(records,indent=2));(root/'commands.json').write_text(json.dumps(dict(commands=records,environment_overrides={k:env[k] for k in ['KICAD10_SYMBOL_DIR','KICAD10_FOOTPRINT_DIR','KICAD10_TEMPLATE_DIR','KICAD_CONFIG_HOME']},board_sha256=hashlib.sha256(pathlib.Path(board).read_bytes()).hexdigest()),indent=2))
