from pathlib import Path
import math, json, xml.etree.ElementTree as ET
out=Path('/work/experiments')
x=ET.parse(out/'independent_netlist.xml').getroot()
values={c.attrib['ref']:c.findtext('value') for c in x.findall('components/comp')}
assert values['R1']=='10k' and values['C1']=='100n'
R=10000.; C=100e-9; t=.001
v=1-math.exp(-t/(R*C))
result={
'input_values':values,
'assumptions':['Ideal 0-to-1 V step at IN relative to J1.3 ground','C1 initially 0 V','Nominal R1=10 kilohm and C1=100 nF, ideal components and wiring','No ADC or other load in this baseline; not a loaded-ADC acceptance test'],
'formula':'Vout(t)=1-exp(-t/(R1*C1))',
'R1_ohm':R,'C1_F':C,'tau_s':R*C,'at_s':t,'Vout_V':v,'within_0_62_to_0_65_V':.62<=v<=.65,
'allowed_tau_s_under_same_assumptions':{'min':-t/math.log(1-.65),'max':-t/math.log(1-.62)},
'allowed_C_F_with_exact_10k_and_no_other_load':{'min':-t/math.log(1-.65)/R,'max':-t/math.log(1-.62)/R},
'ADC_dependence':{
'finite_switch_model':'C1*dv/dt=(Vin-v)/R1 - s(t)*(v-vs)/Rsw; Cs*dvs/dt=s(t)*(v-vs)/Rsw',
'fast_charge_sharing_limit':'v_after=(C1*v_before+Cs*vs_before)/(C1+Cs)',
'interpretation':'Both models are illustrative equations, not specified ADC models. Unknown Cs, Rsw, vs_before and s(t) prevent unique loaded response. No assertion that an actual ADC violates the band.'},
'limitations':['No component tolerances, capacitor bias/temperature variation, leakage/ESR, parasitic extraction, ADC load or bench measurement','The requirement is evaluated as voltage at 1 ms; no unstated stay-in-band or final-value settling criterion is imposed.']}
(out/'response_calculation.json').write_text(json.dumps(result,indent=2))
(out/'nominal_baseline.cir').write_text('''Nominal RC baseline only: no ADC load, no extracted PCB parasitics
Vstep in 0 PWL(0 0 1p 1 2m 1)
R1 in out 10k
C1 out 0 100n IC=0
.tran 1u 1.1m 0 100n uic
.measure tran v_at_1ms FIND v(out) AT=1m
.end
''')
print(json.dumps(result,indent=2))
