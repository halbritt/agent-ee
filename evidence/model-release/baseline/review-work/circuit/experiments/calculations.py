"""Static bounds only. No vendor device model or dynamic simulation is implied."""
import json
from pathlib import Path

rup = 1000.0   # Native R1 and R2
rdown = 1000.0 # Native RN1 branches
rseries = 1000.0 # Native RN2/RN4 branches
v5 = 5.0
v3 = 3.3
out = {
    'inputs': {'R_pullup_ohm':rup, 'R_pulldown_ohm':rdown,
               'R_series_ohm':rseries, 'named_5V_rail_V':v5,
               'conditional_GPIO_high_V':v3},
    'assumptions': [
        'Native nominal values; no unstated tolerance or temperature corners imposed.',
        'The 3.3 V case is conditional on a Polygonus GPIO powered at 3.3 V; the exact mated module is not in the candidate.',
        'GPIO high-Z case excludes current into U11/U12 and A1; it is the unloaded Thevenin voltage, not a prediction of switch state.',
        'Series resistor cannot raise the downstream input above an ideal voltage-driven source when that input consumes current.',
        '65 uA at 5 V is a published supply-current maximum, not a valid model at 3.3 V; no current extrapolation is used.'],
    'LS9_LS10_GPIO_highZ': {
        'equation': 'Vth = 5 * 1000 / (1000 + 1000)',
        'Vth_V':v5*rdown/(rup+rdown),
        'Rth_ohm':rup*rdown/(rup+rdown),
        'DRV8825_VIH_min_V':2.2,
        'unloaded_margin_to_DRV8825_VIH_V':v5*rdown/(rup+rdown)-2.2,
        'interpretation':'Does not prove a failed stepper high. Does leave LS9/10 at a nonzero level with GPIO high-Z; VNL behavior unqualified here.'},
    'lowside_GPIO_3V3_case':{
        'U_input_upper_bound_V':v3,
        'N3_operating_supply_min_V':3.5,
        'upper_bound_minus_operating_min_V':v3-3.5,
        'N3_input_threshold_max_V':3.5,
        'interpretation':'For either named VNL5090N3-E or SKU-selected VNL5050N3-E, N3 input is also its control supply. 3.3 V is below the published 3.5 V minimum. It is not evidence that every sample remains off.'},
    'CAN2_carrier':{
        'native_direct_resistors':[{'ref':'R36','ohm':120,'dnp':True},{'ref':'R37','ohm':120,'dnp':True}],
        'populated_local_termination_conductance_S':0,
        'TJA1051_differential_input_ohm':{'min':19000,'typ':30000,'max':52000},
        'two_TJA1051_parallel_typ_ohm':15000,
        'interpretation':'Semiconductor input resistance is not a 120-ohm termination. Host module and harness termination are unknown. No frequency-domain or transmission-line conclusion is made.'}}
Path('/work/experiments/calculations.json').write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
