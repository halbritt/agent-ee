from pathlib import Path
import subprocess, json, hashlib, math, re, sys
import xml.etree.ElementTree as ET

p = Path('/work/experiments')
log = []
def run(argv, name):
    r = subprocess.run(argv, cwd=p, capture_output=True, text=True)
    (p / (name + '.log')).write_text(r.stdout + r.stderr)
    log.append({'argv':argv, 'cwd':str(p), 'exit_code':r.returncode, 'output':name+'.log'})
    if r.returncode:
        (p/'commands.json').write_text(json.dumps(log, indent=2)+'\n')
        raise RuntimeError(name)
    return r.stdout + r.stderr

for argv, name in [(['python3','--version'],'python-version'), (['kicad-cli','version'],'kicad-version'), (['ngspice','--version'],'ngspice-version')]:
    run(argv,name)
# Read only native candidate and fresh check records, never prior reviewer experiments.
files = [Path('/candidate/requirements.txt'), Path('/candidate/project/rc_filter.kicad_sch'), Path('/candidate/project/rc_filter.kicad_pcb')]
record = {'sha256':{str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}}
for f in files:
    lines=f.read_text().splitlines()
    if f.suffix in ('.kicad_sch','.kicad_pcb'):
        hits=[i for i,l in enumerate(lines) if re.search(r'\(property "Reference" "(?:R1|C1|J1)"|\(property "Value" "(?:100k|100n)"|\(label "(?:IN|OUT)"|\(net "(?:/IN|/OUT|GND)"',l)]
        (p/(f.name+'.locators.txt')).write_text('\n'.join(f'{i+1}: {lines[i]}' for i in hits)+'\n')

run(['kicad-cli','sch','export','netlist','--format','kicadxml','-o',str(p/'independent.xml'),'/candidate/project/rc_filter.kicad_sch'],'export')
root=ET.parse(p/'independent.xml').getroot()
values={c.attrib['ref']: c.findtext('value') for c in root.findall('./components/comp')}
nets={n.attrib['name']: sorted(f"{x.attrib['ref']}.{x.attrib['pin']}" for x in n.findall('node')) for n in root.findall('./nets/net')}
expected={'/IN':['J1.1','R1.1'],'/OUT':['C1.1','J1.2','R1.2'],'GND':['C1.2','J1.3']}
assert nets==expected, nets
fresh=json.loads(Path('/evidence/work/netlist/connectivity.json').read_text())
assert nets=={k:sorted(v) for k,v in fresh['nets'].items()}
assert values=={k:v['value'] for k,v in fresh['components'].items()}
assert values['R1']=='100k' and values['C1']=='100n'
record.update({'native_export_values':values,'native_export_nets':nets,'matches_fresh_connectivity':True})
for name in ['erc','drc','netlist','geometry']:
    record[name+'_check']=json.loads(Path('/evidence/checks/'+name+'.json').read_text())
for name in ['erc','drc']:
    record[name+'_native_report']=json.loads(Path('/evidence/work/'+name+'/'+name+'.json').read_text())
    record[name+'_execution']=json.loads(Path('/evidence/work/'+name+'/execution.json').read_text())
record['geometry_console']=Path('/evidence/work/geometry/console.log').read_text()
(p/'source-check-audit.json').write_text(json.dumps(record,indent=2)+'\n')

# Only ideal linear R and C. Native net names are mapped to simple SPICE names.
# V=0 source clamps IN to ground for t>=0. UIC with capacitor IC=1 retains charge.
# No external load or implicit operating-point solution that would erase charge.
(p/'shutdown.cir').write_text('''Ideal shutdown of saved native RC circuit
Vground in 0 DC 0
R1 in out 100k
C1 out 0 100n IC=1
.options reltol=1e-9 abstol=1e-15 vntol=1e-12
.control
tran 10u 50m 0 10u uic
meas tran vout_5ms FIND v(out) AT=5m
meas tran time_to_10mv WHEN v(out)=0.01 FALL=1
set wr_vecnames
set wr_singlescale
wrdata shutdown.tsv v(in) v(out)
quit
.endc
.end
''')
simlog=run(['ngspice','-b','shutdown.cir'],'shutdown')
R=100e3; C=100e-9; V0=1.; T=5e-3; limit=.01
calc={'R_ohm':R,'C_F':C,'V0_V':V0,'evaluation_time_s':T,'limit_V':limit,'tau_s':R*C,
      'formula':'Vout(t)=V0*exp(-t/(R*C)); t_limit=R*C*ln(V0/limit)',
      'analytic_Vout_at_5ms_V':V0*math.exp(-T/(R*C)),
      'analytic_time_to_10mV_s':R*C*math.log(V0/limit),
      'maximum_tau_for_criterion_s':T/math.log(V0/limit)}
for key in ['vout_5ms','time_to_10mv']:
    calc['ngspice_'+key]=float(re.search(r'(?m)^'+key+r'\s*=\s*([\d.eE+-]+)',simlog)[1])
calc['absolute_analytic_simulation_error_V']=abs(calc['ngspice_vout_5ms']-calc['analytic_Vout_at_5ms_V'])
assert calc['absolute_analytic_simulation_error_V']<1e-6
rows=(p/'shutdown.tsv').read_text().splitlines()
first=[float(x) for x in rows[1].split()]
calc['first_saved_sample']={'time_s':first[0],'Vin_V':first[1],'Vout_V':first[2]}
assert abs(first[1])<1e-12 and first[2]>.999
calc['criterion_pass']=calc['analytic_Vout_at_5ms_V']<=limit
(p/'shutdown-calculation.json').write_text(json.dumps(calc,indent=2)+'\n')
(p/'commands.json').write_text(json.dumps(log,indent=2)+'\n')
print(json.dumps(calc,indent=2))
