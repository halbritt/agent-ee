"""Read-only native review and nominal unloaded RC cross-check; no ADC model."""
import hashlib, json, math, pathlib, platform, re, subprocess, xml.etree.ElementTree as ET
D = pathlib.Path('/work/experiments')
commands = []
def run(argv):
    r = subprocess.run(argv, text=True, capture_output=True, cwd=D)
    commands.append(dict(argv=argv, returncode=r.returncode, stdout=r.stdout, stderr=r.stderr))
    return r

def sexpr(path):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', path.read_text())
    stack=[]; root=None
    for token in tokens:
        if token == '(':
            node=[]
            if stack: stack[-1].append(node)
            stack.append(node)
        elif token == ')': root=stack.pop()
        else: stack[-1].append(json.loads(token) if token.startswith('"') else token)
    return root

def children(node,key): return [x for x in node if isinstance(x,list) and x and x[0]==key]
def child(node,key): return children(node,key)[0]
def props(node): return {p[1]:p[2] for p in children(node,'property')}

for argv in [['python3','--version'],['kicad-cli','--version'],['ngspice','--version']]: run(argv)
expected={
 'requirements.txt':'f34808d7f1924e9af83993800cc62f94f650540e7948beda7de5635f5727b998',
 'project/rc_filter.kicad_sch':'ecc56a68a96b9297d8751ae7686a9c8818e9cac84faefafa364b06a0af8a342b',
 'project/rc_filter.kicad_pcb':'f8264d95e52dc3c3d48358696c87294c6bbe7ff83102ecc9e0c4c3c27c7e72de'}
hashes={p:hashlib.sha256((pathlib.Path('/candidate')/p).read_bytes()).hexdigest() for p in expected}
assert hashes==expected
sch=sexpr(pathlib.Path('/candidate/project/rc_filter.kicad_sch'))
pcb=sexpr(pathlib.Path('/candidate/project/rc_filter.kicad_pcb'))
instances={props(n)['Reference']:props(n) for n in children(sch,'symbol')}
components={r:instances[r]['Value'] for r in ['R1','C1','J1']}
xml=ET.parse('/evidence/work/netlist/design.xml').getroot()
xml_values={n.attrib['ref']:n.findtext('value') for n in xml.findall('./components/comp')}
assert components==xml_values
xml_nets={n.attrib['name']:sorted(f"{p.attrib['ref']}.{p.attrib['pin']}" for p in n.findall('node')) for n in xml.findall('./nets/net')}
pcb_nets={}; pcb_values={}
for fp in children(pcb,'footprint'):
    p=props(fp); pcb_values[p['Reference']]=p['Value']
    for pad in children(fp,'pad'):
        pcb_nets.setdefault(child(pad,'net')[1],[]).append(f"{p['Reference']}.{pad[1]}")
pcb_nets={k:sorted(v) for k,v in pcb_nets.items()}
assert pcb_nets==xml_nets
assert pcb_values==xml_values
assert xml_nets=={'/IN':['J1.1','R1.1'], '/OUT':['C1.1','J1.2','R1.2'], 'GND':['C1.2','J1.3']}
checks={p.stem:json.loads(p.read_text()) for p in pathlib.Path('/evidence/checks').glob('*.json')}
assert checks['erc']['details']['violations']==0
assert checks['drc']['details']['violations']==0
assert checks['drc']['details']['schematic_parity']==0
assert checks['drc']['details']['unconnected_items']==0

def number(s):
    m=re.fullmatch(r'([\d.]+)([knum]?)',s)
    return float(m[1])*{'':1,'k':1e3,'n':1e-9,'u':1e-6,'m':1e-3}[m[2]]
R=number(components['R1']); C=number(components['C1']); t=1e-3
tau=R*C; v=-math.expm1(-t/tau)
calc={
 'native_hashes':hashes, 'native_schematic_values':components,
 'exported_xml_values':xml_values, 'native_pcb_values':pcb_values,
 'xml_and_pcb_nets':xml_nets,
 'assumptions':['Nominal ideal linear R and C; no tolerance or parasitic models supplied.',
 'Unloaded /OUT only: this calculation does not model the required switched ADC.',
 'Input is 0 V before rising to 1 V; C1 starts discharged in DC equilibrium.',
 'Measure /OUT 1 ms after edge midpoint; SPICE approximates ideal step using 1 ns rise.'],
 'R_ohm':R,'C_farad':C,'tau_s':tau, 'ideal_unloaded_out_at_1ms_V':v,
 'lower_margin_V':v-.62,'upper_margin_V':.65-v,
 'acceptable_tau_s_for_unloaded_zero_state':{'min':-t/math.log(1-.65), 'max':-t/math.log(1-.62)},
 'supplied_check_kinds':list(checks),
 'transient_check_supplied':False,
 'adc_parameters_supplied':False,
 'general_closed_switch_equations':[
 'C1*dVout/dt=(Vin-Vout)/R1-(Vout-Vsample)/Rswitch',
 'Csample*dVsample/dt=(Vout-Vsample)/Rswitch',
 'For a short charge-sharing event with negligible source charge: Vafter=(C1*Vbefore+Csample*Vsample_before)/(C1+Csample).',
 'These equations explain parameter dependence only. No numerical ADC capacitance, switch resistance, initial voltage, or sampling schedule was selected.']}
(D/'nominal.cir').write_text(f'''Nominal native RC; deliberately unloaded, no ADC emulation
* Values and topology verified against XML and native PCB/schematic.
* Step midpoint = 1.0005 us. Measure exactly 1 ms later.
VSTEP in 0 PULSE(0 1 1u 1n 1n 10m 20m)
R1 in out {components['R1']}
C1 out 0 {components['C1']}
.options reltol=1e-8 abstol=1e-13 vntol=1e-10
.tran 0.1u 2m 0 0.1u
.meas tran vout_initial FIND v(out) AT=0.5u
.meas tran vin_at_1ms FIND v(in) AT=1.0010005m
.meas tran vout_at_1ms FIND v(out) AT=1.0010005m
.end
''')
r=run(['ngspice','-b','-o',str(D/'nominal.log'),str(D/'nominal.cir')])
log=(D/'nominal.log').read_text()
calc['spice_measurements']={k:float(val) for k,val in re.findall(r'^(vout_initial|vin_at_1ms|vout_at_1ms)\s*=\s*([\d.eE+\-]+)',log,re.M)}
assert r.returncode==0
assert len(calc['spice_measurements'])==3, log
assert abs(calc['spice_measurements']['vout_at_1ms']-v)<1e-7
assert calc['spice_measurements']['vin_at_1ms']==1
assert calc['spice_measurements']['vout_initial']==0
(D/'calculation.json').write_text(json.dumps(calc,indent=2)+'\n')
(D/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
print(json.dumps(calc,indent=2))
