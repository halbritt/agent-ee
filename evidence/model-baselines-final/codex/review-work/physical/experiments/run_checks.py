import subprocess,json,datetime,hashlib,pathlib
root=pathlib.Path('/work/experiments')
commands=[
 ['kicad-cli','--version'],
 ['/usr/bin/python3','--version'],
 ['kicad-cli','pcb','drc','--format','json','--severity-all','--schematic-parity','--units','mm','-o',str(root/'independent-drc.json'),'/candidate/project/Polygonus-Universal-Base.kicad_pcb'],
 ['kicad-cli','pcb','export','drill','--output',str(root/'drill')+'/', '--excellon-separate-th','--generate-report','--report-path',str(root/'drill-report.txt'),'/candidate/project/Polygonus-Universal-Base.kicad_pcb'],
 ['kicad-cli','pcb','export','gerbers','--output',str(root/'gerbers')+'/', '--layers','F.Cu,In1.Cu,In2.Cu,B.Cu,F.Mask,B.Mask,Edge.Cuts','/candidate/project/Polygonus-Universal-Base.kicad_pcb']
]
records=[]
for i,c in enumerate(commands):
 t=datetime.datetime.now(datetime.timezone.utc).isoformat(); p=subprocess.run(c,capture_output=True,text=True)
 log=f'command-{i}.log'; (root/log).write_text(p.stdout+p.stderr)
 records.append({'argv':c,'started_utc':t,'exit_code':p.returncode,'log':log})
 print(i,p.returncode,(p.stdout+p.stderr)[:800])
(root/'commands.json').write_text(json.dumps(records,indent=2))
