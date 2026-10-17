"""Conditional copper resistance calculation, not ampacity/thermal qualification."""
import json,pathlib,math
b=pathlib.Path('/work/experiments')
g=json.load(open('/evidence/work/geometry/geometry.json'))
# The inspected native routing forms an unbranched series chain to pad 12,
# plus a single pad-12-to-pad-11 stub in each module. Assume module pads 11/12
# are equipotential. Ignore pad spreading, barrel/contact resistance and heating.
rho=1.724e-8; thickness_m=0.035e-3
out={'assumptions':{'resistivity_ohm_m':rho,'temperature_C':20,'saved_B_Cu_thickness_mm':0.035,'model':'Uniform copper. Ux.11 and Ux.12 equipotential; pad11-pad12 stub carries no net current. Excludes contacts, barrels, spreading and full supply/ground loop. No assigned current, rating, temperature rise or acceptance threshold.'},'results':{}}
for net,ref in [('/HeaterNeg','U1'),('/HeaterNeg2','U2')]:
 ts=[t for t in g['tracks'] if t['net']==net and t['type']=='track']
 pads={p['pad']:p for p in g['pads'] if p['ref']==ref and p['pad'] in ['11','12']}
 keys={tuple(p['position_mm']) for p in pads.values()}
 stub=[t for t in ts if {tuple(t['start_mm']),tuple(t['end_mm'])}==keys]
 assert len(stub)==1
 chain=[t for t in ts if t not in stub];res=sum(t['length_mm']*1e-3*rho/(t['width_mm']*1e-3*thickness_m) for t in chain)
 out['results'][net]={'series_chain_length_mm':sum(t['length_mm'] for t in chain),'chain_widths_mm':sorted({t['width_mm'] for t in chain}),'excluded_stub':stub[0],'estimated_trace_resistance_ohm_20C':res,'voltage_drop_V_per_A':res,'copper_power_W_per_A_squared':res,'segments':chain}
(b/'current_path_calculation.json').write_text(json.dumps(out,indent=2))
print(json.dumps({n:{k:v for k,v in r.items() if k not in ['segments','excluded_stub']} for n,r in out['results'].items()},indent=2))
