import os,pathlib,subprocess,json,collections,hashlib
base=pathlib.Path('/work/experiments');cfg=base/'kicad-config';cfg.mkdir(exist_ok=True)
fpdirs=sorted(pathlib.Path('/candidate/stock/footprints').glob('*.pretty'))
fp='(fp_lib_table\n (version 7)\n'+''.join(f' (lib (name "{p.stem}")(type "KiCad")(uri "{p}")(options "")(descr ""))\n' for p in fpdirs)+')\n'
syms=sorted(pathlib.Path('/candidate/stock/symbols').glob('*.kicad_sym'))
sym='(sym_lib_table\n (version 7)\n'+''.join(f' (lib (name "{p.stem}")(type "KiCad")(uri "{p}")(options "")(descr ""))\n' for p in syms)+')\n'
for d in [cfg,cfg/'10.0']:
 d.mkdir(exist_ok=True);(d/'fp-lib-table').write_text(fp);(d/'sym-lib-table').write_text(sym)
env=os.environ.copy();env.update(KICAD_CONFIG_HOME=str(cfg),KICAD10_SYMBOL_DIR='/candidate/stock/symbols',KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints',KICAD10_TEMPLATE_DIR='/candidate/stock/template')
cmds=[['kicad-cli','pcb','drc','--format','json','--severity-all','--schematic-parity','--units','mm','-o',str(base/'stock_drc.json'),'/candidate/project/Polygonus-Universal-Base.kicad_pcb'],['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(base/'independent_design.xml'),'/candidate/project/Polygonus-Universal-Base.kicad_sch']]
records=[]
for i,args in enumerate(cmds):
 cp=subprocess.run(args,cwd=base,env=env,text=True,capture_output=True); (base/f'stock_command_{i}.stdout').write_text(cp.stdout);(base/f'stock_command_{i}.stderr').write_text(cp.stderr)
 records.append(dict(argv=args,cwd=str(base),env_overrides={k:env[k] for k in ['KICAD_CONFIG_HOME','KICAD10_SYMBOL_DIR','KICAD10_FOOTPRINT_DIR','KICAD10_TEMPLATE_DIR']},returncode=cp.returncode,stdout=f'stock_command_{i}.stdout',stderr=f'stock_command_{i}.stderr'));print(cp.returncode,cp.stdout,cp.stderr[:300])
(base/'stock_commands.json').write_text(json.dumps(records,indent=2))
