import os,subprocess,json,pathlib,datetime,hashlib
base=pathlib.Path('/work/experiments'); env=os.environ.copy();env.update(KICAD10_SYMBOL_DIR='/candidate/stock/symbols',KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints',KICAD10_TEMPLATE_DIR='/candidate/stock/template')
commands=[['kicad-cli','version'],['python3','--version'],['kicad-cli','pcb','drc','--format','json','--severity-all','--schematic-parity','--units','mm','-o',str(base/'independent_drc.json'),'/candidate/project/Polygonus-Universal-Base.kicad_pcb'],['kicad-cli','pcb','export','drill','--excellon-separate-th','--generate-report','--output',str(base/'drill')+'/', '/candidate/project/Polygonus-Universal-Base.kicad_pcb'],['kicad-cli','pcb','export','svg','--layers','F.Cu,Edge.Cuts','--page-size-mode','2','--exclude-drawing-sheet','--mode-single','--output',str(base/'front_saved.svg'),'/candidate/project/Polygonus-Universal-Base.kicad_pcb']]
records=[]
for i,argv in enumerate(commands):
 start=datetime.datetime.now(datetime.timezone.utc).isoformat(); cp=subprocess.run(argv,cwd=base,env=env,text=True,capture_output=True)
 (base/f'command_{i:02d}.stdout').write_text(cp.stdout);(base/f'command_{i:02d}.stderr').write_text(cp.stderr)
 records.append(dict(argv=argv,cwd=str(base),started_utc=start,returncode=cp.returncode,stdout=f'command_{i:02d}.stdout',stderr=f'command_{i:02d}.stderr'));print(i,cp.returncode,cp.stdout.strip(),cp.stderr[:150])
(base/'commands.json').write_text(json.dumps(records,indent=2))
