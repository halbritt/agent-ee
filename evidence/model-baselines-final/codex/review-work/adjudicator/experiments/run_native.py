import subprocess,json,pathlib,datetime,os
base=pathlib.Path('/work/experiments'); (base/'drill').mkdir(exist_ok=True)
env=os.environ.copy(); env['KICAD_CONFIG_HOME']=str(base/'kicad-config')
commands=[['kicad-cli','--version'],['ngspice','--version'],['pdftotext','-v'],['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(base/'fresh.xml'),'/candidate/project/Polygonus-Universal-Base.kicad_sch'],['kicad-cli','pcb','export','drill','--format','excellon','--output',str(base/'drill')+'/', '/candidate/project/Polygonus-Universal-Base.kicad_pcb']]
log=[]
for i,argv in enumerate(commands):
 p=subprocess.run(argv,env=env,capture_output=True,text=True,timeout=120)
 (base/f'command-{i}.log').write_text(p.stdout+p.stderr)
 log.append({'argv':argv,'exit_code':p.returncode,'log':f'command-{i}.log','time_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
 print(json.dumps(log[-1]),flush=True)
(base/'commands.json').write_text(json.dumps(log,indent=2))
