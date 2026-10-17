import pathlib,subprocess,json,os,hashlib
base=pathlib.Path('/work/experiments'); env=os.environ.copy();env['KICAD_CONFIG_HOME']=str(base/'kicad-config')
(base/'reset_bias.cir').write_text('''Unloaded native LS9 or LS10 passive bias only
Vrail rail 0 5
Rpullup rail control 1k
Rpulldown control 0 1k
Rseries control driver 1k
.control
op
print v(control) v(driver)
quit
.endc
.end
''')
commands=[['kicad-cli','pcb','export','drill','--format','excellon','--excellon-separate-th','--generate-report','--report-path',str(base/'drill-report.txt'),'--output',str(base/'drill')+'/', '/candidate/project/Polygonus-Universal-Base.kicad_pcb'],['ngspice','-b',str(base/'reset_bias.cir')]]
log=[]
for i,argv in enumerate(commands):
 p=subprocess.run(argv,env=env,capture_output=True,text=True,timeout=60)
 name=['drill-export.log','reset_bias.log'][i];(base/name).write_text(p.stdout+p.stderr)
 log.append({'argv':argv,'exit_code':p.returncode,'log':name})
 print(json.dumps(log[-1]))
(base/'final_commands.json').write_text(json.dumps(log,indent=2))
audit=json.load(open(base/'audit.json'))
changed=[]
for path,expected in audit['hashes'].items():
 if path.startswith('/candidate/') and hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()!=expected: changed.append(path)
(base/'integrity.json').write_text(json.dumps({'candidate_native_files_changed':changed,'refill_performed':False,'native_read_only_policy':'No candidate writes; all generated files under /work/experiments'},indent=2))
