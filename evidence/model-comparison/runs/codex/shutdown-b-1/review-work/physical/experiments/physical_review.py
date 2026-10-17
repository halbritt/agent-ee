from pathlib import Path
import json, math, re, subprocess, hashlib, sys
import xml.etree.ElementTree as ET
import pcbnew
P=Path('/work/experiments')
commands=[]
def run(argv):
    r=subprocess.run(argv,capture_output=True,text=True)
    commands.append({'argv':argv,'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
    return r
for argv in [['/usr/bin/python3','--version'],['kicad-cli','version'],['ngspice','--version']]: run(argv)
# Read native schematic with a minimal S-expression reader; no library or candidate writes.
def sexp(s):
    tok=re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+',s)
    stack=[]; root=None
    for t in tok:
        if t=='(':
            a=[]
            if stack: stack[-1].append(a)
            else: root=a
            stack.append(a)
        elif t==')': stack.pop()
        else: stack[-1].append(json.loads(t) if t.startswith('"') else t)
    return root
def children(a,k): return [x for x in a if isinstance(x,list) and x and x[0]==k]
def first(a,k): return children(a,k)[0]
sch=sexp(Path('/candidate/project/rc_filter.kicad_sch').read_text())
values={}
for sym in children(sch,'symbol'):
    props={x[1]:x[2] for x in children(sym,'property')}
    if props.get('Reference') in ['R1','C1','J1']: values[props['Reference']]=props['Value']
b=pcbnew.LoadBoard('/candidate/project/rc_filter.kicad_pcb')
cn=b.GetConnectivity(); cn.Build(b)
def pos(v): return [pcbnew.ToMM(v.x),pcbnew.ToMM(v.y)]
def padname(p): return p.GetParentFootprint().GetReference()+'.'+p.GetNumber()
board={}; netpads={}
for fp in b.GetFootprints():
    pads=[]
    for p in fp.Pads():
        netpads.setdefault(p.GetNetname(),[]).append(padname(p))
        pads.append({'pad':p.GetNumber(),'net':p.GetNetname(),'position_mm':pos(p.GetPosition()),'size_mm':pos(p.GetSize()),'drill_mm':pos(p.GetDrillSize()),'connected_pads':sorted(padname(q) for q in cn.GetConnectedPads(p)),'connected_tracks':[str(t.m_Uuid.AsString()) for t in cn.GetConnectedTracks(p)]})
    board[fp.GetReference()]={'value':fp.GetValue(),'position_mm':pos(fp.GetPosition()),'pads':pads}
for ref in values: assert values[ref]==board[ref]['value']
xml=ET.parse('/evidence/work/netlist/design.xml')
xmlnets={n.attrib['name']:sorted(x.attrib['ref']+'.'+x.attrib['pin'] for x in n.findall('node')) for n in xml.findall('./nets/net')}
assert {k:sorted(v) for k,v in netpads.items()}==xmlnets
tracks=[{'uuid':str(t.m_Uuid.AsString()),'type':type(t).__name__,'net':t.GetNetname(),'start_mm':pos(t.GetStart()),'end_mm':pos(t.GetEnd()),'width_mm':pcbnew.ToMM(t.GetWidth(t.GetLayer()) if isinstance(t,pcbnew.PCB_VIA) else t.GetWidth()),'layer':b.GetLayerName(t.GetLayer())} for t in b.GetTracks()]
zones=[{'uuid':str(z.m_Uuid.AsString()),'net':z.GetNetname(),'filled':z.IsFilled(),'layer':b.GetLayerName(z.GetLayer()),'outline_count':z.GetFilledPolysList(z.GetLayer()).OutlineCount()} for z in b.Zones()]
result={'pcbnew_version':pcbnew.Version(),'schematic_values':values,'footprints':board,'netlist_matches_pad_assignments':True,'net_pad_assignments':xmlnets,'tracks':tracks,'zones':zones,'unconnected_count':cn.GetUnconnectedCount(False),'limitations':['Connectivity rebuilt in memory from saved copper; no zone refill or candidate writes.','Connectivity API is another use of KiCad, not an independent manufactured-board measurement.']}
(P/'saved_board_probe.json').write_text(json.dumps(result,indent=2)+'\n')
# Ideal circuit: IN is grounded throughout t>=0; capacitor IC and UIC avoid zero-volt operating point erasing initial charge.
scale={'k':1e3,'n':1e-9}
R=float(values['R1'][:-1])*scale[values['R1'][-1]]
C=float(values['C1'][:-1])*scale[values['C1'][-1]]
tau=R*C
calc={'R_ohm':R,'C_F':C,'initial_V':1.0,'deadline_s':0.005,'limit_V':0.01,'tau_s':tau,'equation':'Vout(t)=1*exp(-t/(R*C))','vout_at_deadline_V':math.exp(-.005/tau),'time_to_limit_s':tau*math.log(100),'max_tau_s':.005/math.log(100),'initial_current_A':1/R,'initial_resistor_power_W':1/R,'initial_energy_J':.5*C,'assumptions':['Ideal passive R and C; no tolerance, leakage, ESR, trace resistance or inductance.','IN shorted to the same reference as C1.2 at t=0, no external OUT load.','Only saved resistor provides an ideal DC discharge path; PWR_FLAG is not an electrical component.']}
(P/'shutdown_calculation.json').write_text(json.dumps(calc,indent=2)+'\n')
for maxstep, name in [('1u','shutdown'),('0.1u','shutdown_fine')]:
    deck=f'''Saved RC shutdown, native R1/C1 values, ideal grounded IN, unloaded OUT
Vground in 0 DC 0
R1 in out {values['R1']}
C1 out 0 {values['C1']} IC=1
.options reltol=1e-8 abstol=1e-15 vntol=1e-10
.tran {maxstep} 50m 0 {maxstep} UIC
.meas tran vout_5ms FIND v(out) AT=5m
.meas tran t_to_10mV WHEN v(out)=0.01 FALL=1
.end
'''
    (P/(name+'.cir')).write_text(deck)
    run(['ngspice','-b','-o',str(P/(name+'.log')),str(P/(name+'.cir'))])
hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path('/candidate/project/rc_filter.kicad_pcb'),Path('/candidate/project/rc_filter.kicad_sch')]}
(P/'execution.json').write_text(json.dumps({'driver_argv':sys.argv,'python':sys.version,'commands':commands,'input_sha256':hashes},indent=2)+'\n')
print(json.dumps(calc,indent=2))
print('Saved probes, simulations and execution record to',P)
