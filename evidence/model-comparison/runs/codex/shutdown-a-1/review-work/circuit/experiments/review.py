import hashlib,json,math,pathlib,re,subprocess,sys,xml.etree.ElementTree as ET
D=pathlib.Path('/work/experiments')
commands=[]
def run(argv):
    p=subprocess.run(argv,cwd=D,text=True,capture_output=True)
    commands.append({'argv':argv,'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
    (D/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
    return p
for argv in [['python3','--version'],['kicad-cli','version'],['ngspice','--version']]:
    assert run(argv).returncode==0
# Parse native s-expressions without evaluating design text.
def parse(path):
    tokens=re.findall(r'"(?:\\.|[^"\\])*"|[^\s()]+|[()]',path.read_text())
    stack=[];root=None
    for t in tokens:
        if t=='(':
            x=[]
            if stack: stack[-1].append(x)
            else: root=x
            stack.append(x)
        elif t==')': stack.pop()
        else: stack[-1].append(json.loads(t) if t.startswith('"') else t)
    assert not stack
    return root
def children(node,key): return [x for x in node if isinstance(x,list) and x and x[0]==key]
def child(node,key): return children(node,key)[0]
def props(node): return {x[1]:x[2] for x in children(node,'property')}
schpath=pathlib.Path('/candidate/project/rc_filter.kicad_sch')
pcbpath=pathlib.Path('/candidate/project/rc_filter.kicad_pcb')
sch=parse(schpath);pcb=parse(pcbpath)
symbols={props(x)['Reference']:x for x in children(sch,'symbol')}
assert props(symbols['R1'])['Value']=='10k'
assert props(symbols['C1'])['Value']=='100n'
p=run(['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(D/'independent.xml'),str(schpath)])
assert p.returncode==0
xml=ET.parse(D/'independent.xml').getroot()
comps={x.attrib['ref']:x.findtext('value') for x in xml.find('components')}
nets={x.attrib['name']:sorted(n.attrib['ref']+'.'+n.attrib['pin'] for n in x.findall('node')) for x in xml.find('nets')}
expected={'/IN':['J1.1','R1.1'],'/OUT':['C1.1','J1.2','R1.2'],'GND':['C1.2','J1.3']}
assert nets==expected
assert nets=={k:sorted(v) for k,v in json.loads(pathlib.Path('/evidence/work/netlist/connectivity.json').read_text())['nets'].items()}
boardpads={}
for fp in children(pcb,'footprint'):
    ref=props(fp)['Reference']
    assert props(fp)['Value']==comps[ref]
    for pad in children(fp,'pad'):
        boardpads[ref+'.'+pad[1]]=child(pad,'net')[-1]
assert boardpads=={pin:net for net,pins in nets.items() for pin in pins}
# Save native source locators and topology; the native netlist establishes terminal identities.
summary={'hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [schpath,pcbpath,pathlib.Path('/candidate/requirements.txt')]},'component_values':comps,'nets':nets,'board_pad_nets':boardpads,'native_symbols':{k:{'uuid':child(v,'uuid')[1],'at':child(v,'at')[1:],'properties':props(v)} for k,v in symbols.items()},'native_wires':[child(x,'pts')[1:] for x in children(sch,'wire')],'native_labels':[{'name':x[1],'at':child(x,'at')[1:]} for x in children(sch,'label')]}
(D/'native_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
# Audit supplied checks; these have no electrical stimulus or transient measurement.
checks={}
for check in ['erc','drc','netlist','geometry']:
    base=pathlib.Path('/evidence/work')/check
    execution=json.loads((base/'execution.json').read_text())
    record=json.loads((pathlib.Path('/evidence/checks')/(check+'.json')).read_text())
    checks[check]={'execution':execution,'check_record':record,'console':(base/'console.log').read_text()}
    if check in ['erc','drc']:
        checks[check]['native_report']=json.loads((base/(check+'.json')).read_text())
    if check=='geometry': checks[check]['geometry']=json.loads((base/'geometry.json').read_text())
(D/'check_audit.json').write_text(json.dumps(checks,indent=2)+'\n')
R=10000.;C=100e-9;t=.005;v0=1.;limit=.01
a={'R_ohm':R,'C_F':C,'initial_V':v0,'IN_V_for_t_ge_0':0,'external_load':'none','tau_s':R*C,'equation':'Vout(t) = 1 V * exp(-t/(R1*C1))','Vout_at_5ms_V':v0*math.exp(-t/(R*C)),'limit_V':limit,'time_to_10mV_s':R*C*math.log(v0/limit),'pass':v0*math.exp(-t/(R*C))<=limit,'assumptions':['Ideal resistor and capacitor; ideal wires; /IN clamped to GND at t=0; C1 initially +1 V at OUT relative to GND.','No external load, leakage, tolerances, or parasitics.']}
(D/'calculation.json').write_text(json.dumps(a,indent=2)+'\n')
# The 0 V source represents exactly the required grounding, not an added discharge path at OUT.
deck='''Ideal shutdown of native R1=10k and C1=100n
Vground in 0 DC 0
R1 in out 10k
C1 out 0 100n IC=1
.options reltol=1e-8 abstol=1e-14 vntol=1e-10
.control
tran 1u 6m 0 1u uic
meas tran vout_5ms FIND v(out) AT=5m
meas tran t_10mV WHEN v(out)=0.01 FALL=1
wrdata shutdown_samples.dat v(out) v(in)
quit
.endc
.end
'''
(D/'shutdown.cir').write_text(deck)
p=run(['ngspice','-b','shutdown.cir'])
(D/'shutdown.log').write_text(p.stdout+p.stderr)
assert p.returncode==0
v=float(re.search(r'vout_5ms\s*=\s*([\d.eE+-]+)',p.stdout).group(1))
cross=float(re.search(r't_10mv\s*=\s*([\d.eE+-]+)',p.stdout,re.I).group(1))
rows=[[float(x) for x in line.split()] for line in (D/'shutdown_samples.dat').read_text().splitlines() if line.strip()]
measurement={'ngspice_Vout_at_5ms_V':v,'ngspice_time_to_10mV_s':cross,'analytic_Vout_at_5ms_V':a['Vout_at_5ms_V'],'absolute_difference_V':abs(v-a['Vout_at_5ms_V']),'first_sample':rows[0],'last_sample':rows[-1],'max_sample_abs_error_from_analytic_V':max(abs(row[1]-math.exp(-row[0]/(R*C))) for row in rows),'max_abs_IN_V':max(abs(row[3]) for row in rows),'pass':v<=limit}
assert measurement['max_abs_IN_V']==0
assert abs(v-a['Vout_at_5ms_V'])<1e-7
assert measurement['pass']
(D/'measurement.json').write_text(json.dumps(measurement,indent=2)+'\n')
print(json.dumps({'calculation':a,'measurement':measurement},indent=2))
