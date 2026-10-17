import json,pathlib,hashlib,subprocess,xml.etree.ElementTree as ET,sys
sys.path.insert(0,'/work/experiments')
from inspect_native import parse,children,child,props
p=pathlib.Path('/work/experiments'); log=[]
def record(x): log.append(x)
cmd=['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(p/'independent-netlist.xml'),'/candidate/project/Polygonus-Universal-Base.kicad_sch']
r=subprocess.run(cmd,text=True,capture_output=True);record({'command':cmd,'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
x=ET.parse(p/'independent-netlist.xml'); nets={n.attrib['name']:sorted(i.attrib['ref']+'.'+i.attrib['pin'] for i in n.findall('node')) for n in x.findall('./nets/net')}; orig=json.load(open('/evidence/work/netlist/connectivity.json'));record({'independent_netlist_matches_supplied':nets==orig['nets'],'nets':len(nets),'components':len(x.findall('./components/comp'))})
board=parse('/candidate/project/Polygonus-Universal-Base.kicad_pcb');fps={props(f)['Reference']:f for f in children(board,'footprint')}
boardnodes={}
for ref,f in fps.items():
 for pad in children(f,'pad'):
  n=child(pad,'net')
  if n and pad[1]:boardnodes[ref+'.'+pad[1]]=n[-1]
missing=[];mismatch=[]
for net,nodes in nets.items():
 for node in nodes:
  if node not in boardnodes: missing.append(node)
  elif boardnodes[node]!=net:mismatch.append({'node':node,'schematic':net,'board':boardnodes[node]})
record({'missing_pads':missing,'pad_net_mismatches':mismatch})
for ref in ['U1','U2','U20','U21','R36','R37','J2','U7','U10','U3','U15','A1']:
 c=orig['components'][ref];f=fps[ref]
 record({'ref':ref,'schematic':c,'board_value':props(f).get('Value'),'board_dnp':'dnp' in child(f,'attr'),'board_pad_nets':{pad[1]:child(pad,'net')[-1] if child(pad,'net') else '' for pad in children(f,'pad')}})
for file in ['VR Conditioner.kicad_sch','VR conditioner.kicad_sch','Polygonus-Universal-Base.kicad_pcb']:
 record({'path':'inputs/project/'+file,'sha256':hashlib.sha256(pathlib.Path('/candidate/project',file).read_bytes()).hexdigest()})
record({'CAN_cross_bus_resistors':[{'ref':ref,'value':c['value'],'dnp':c['dnp']} for ref,c in orig['components'].items() if c['libsource']['part'] in ['R','Res'] and all(any(ref+'.'+pin in nets[n] for pin in ['1','2']) for n in ['/CAN2+','/CAN2-'])]})
record({'nominal_bias_calculations':{'MAX9924_BIAS_divider_V':5*1000/(1000+1000),'VR_module_Vref_unloaded_V':5*10000/(10000+10000),'LS9_10_high_Z_V_ignoring_IC_input_currents':5*1000/(1000+1000),'LS9_10_high_Z_Thevenin_ohms':500,'VR_low_threshold_from_LS12_V': 'V(LS12)*68000/(12000+10000+68000) = 0.755556*V(LS12); assumes ideal followers and DC','VR_high_threshold_V':'2*2.5 - V(thresh_low); assumes ideal followers and DC'},'limitations':'Nominal algebra only, not device limits or startup simulation; module output type and tolerance not supplied.'})
(p/'connection-verification.json').write_text(json.dumps(log,indent=2));print(json.dumps(log[:3],indent=2));print(json.dumps(log[-2:],indent=2))
