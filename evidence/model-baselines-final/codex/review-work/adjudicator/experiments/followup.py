import subprocess,json,pathlib,os,xml.etree.ElementTree as ET
base=pathlib.Path('/work/experiments');env=os.environ.copy();env['KICAD_CONFIG_HOME']=str(base/'kicad-config')
commands=[['pdftotext','-layout',str(base/'references/VNL5050.pdf'),str(base/'references/VNL5050.txt')],['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(base/'module_mcu.xml'),str(base/'references/Polygonus_mcu.kicad_sch')]]
log=[]
for i,argv in enumerate(commands):
 p=subprocess.run(argv,env=env,capture_output=True,text=True,timeout=60)
 (base/f'followup-{i}.log').write_text(p.stdout+p.stderr)
 log.append({'argv':argv,'exit_code':p.returncode,'log':f'followup-{i}.log'})
(base/'followup_commands.json').write_text(json.dumps(log,indent=2))
if (base/'module_mcu.xml').exists():
 root=ET.parse(base/'module_mcu.xml').getroot()
 nets={n.attrib['name']:[dict(p.attrib) for p in n.findall('node')] for n in root.findall('./nets/net')}
 wanted=['/'+n for n in ['LS9','LS10','LS11','LS12','IGN9','IGN10','IGN11','IGN12']]+['+3V3']
 (base/'module_interface.json').write_text(json.dumps({n:nets.get(n) for n in wanted},indent=2))
print(json.dumps(log))
