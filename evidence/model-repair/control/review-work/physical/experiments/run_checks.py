import hashlib, json, os, pathlib, subprocess, sys, time
root=pathlib.Path('/work/experiments'); root.mkdir(exist_ok=True)
commands=[
 ['kicad-cli','version'],
 ['ngspice','--version'],
 ['/usr/bin/python3','--version'],
 ['/usr/bin/python3',str(root/'audit.py')],
 ['kicad-cli','pcb','drc','--format','json','--severity-all','--schematic-parity','--units','mm','-o',str(root/'drc.json'),'/candidate/project/rc_filter.kicad_pcb'],
 ['kicad-cli','pcb','export','svg','--layers','F.Cu,B.Cu,Edge.Cuts','--mode-multi','--page-size-mode','2','--exclude-drawing-sheet','--output',str(root/'plots'),'/candidate/project/rc_filter.kicad_pcb'],
]
records=[]
for i,argv in enumerate(commands):
    started=time.time()
    p=subprocess.run(argv,cwd=root,capture_output=True)
    (root/f'command-{i}.stdout.log').write_bytes(p.stdout)
    (root/f'command-{i}.stderr.log').write_bytes(p.stderr)
    records.append(dict(argv=argv,cwd=str(root),exit_code=p.returncode,elapsed_s=time.time()-started,
                        stdout=f'command-{i}.stdout.log',stderr=f'command-{i}.stderr.log'))
    (root/'commands.json').write_text(json.dumps(records,indent=2)+'\n')
    print(i,argv,'exit',p.returncode,flush=True)
    if i==3 and p.returncode: print(p.stderr.decode(),flush=True)
