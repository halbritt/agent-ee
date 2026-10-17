from pathlib import Path
import subprocess,os,json
w=Path('/work/experiments'); env=os.environ.copy();env['KICAD_CONFIG_HOME']=str(w/'kicad-config')
commands=[]
for name,layers in [('front','F.Cu,F.SilkS,F.CrtYd,Edge.Cuts'),('back','B.Cu,Edge.Cuts')]:
    argv=['kicad-cli','pcb','export','pdf','--layers',layers,'--mode-single','--scale','8','--output',str(w/(name+'.pdf')),'/candidate/project/rc_filter.kicad_pcb']
    p=subprocess.run(argv,capture_output=True,text=True,env=env);commands.append({'argv':argv,'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
    argv=['pdftoppm','-scale-to','1600','-png','-singlefile',str(w/(name+'.pdf')),str(w/name)]
    p=subprocess.run(argv,capture_output=True,text=True);commands.append({'argv':argv,'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
(w/'render-commands.json').write_text(json.dumps(commands,indent=2))
