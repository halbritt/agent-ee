"""Read-only native audit and unloaded baseline; never assigns ADC parameters."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path('/work/experiments')
COMMANDS = []

def save(name, data):
    (ROOT / name).write_text(json.dumps(data, indent=2) + '\n')

def run(name, argv, env=None):
    result = subprocess.run(argv, cwd=ROOT, env=env, text=True, capture_output=True)
    (ROOT / (name + '.stdout.log')).write_text(result.stdout)
    (ROOT / (name + '.stderr.log')).write_text(result.stderr)
    COMMANDS.append({'argv': argv, 'cwd': str(ROOT), 'exit_code': result.returncode,
                     'stdout': name + '.stdout.log', 'stderr': name + '.stderr.log'})
    save('commands.json', COMMANDS)
    if result.returncode:
        raise RuntimeError(name + ' failed; see logs')
    return result.stdout

paths = [Path('/candidate/requirements.txt'),
         Path('/candidate/project/rc_filter.kicad_sch'),
         Path('/candidate/project/rc_filter.kicad_pcb')]
def hashes():
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}

before = hashes()
save('native_hashes_before.json', before)
run('python_version', [sys.executable, '--version'])
run('kicad_version', ['kicad-cli', '--version'])
run('ngspice_version', ['ngspice', '--version'])
env = os.environ.copy()
env['KICAD_CONFIG_HOME'] = str(ROOT / 'kicad-config')
run('native_export', ['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml',
                     '-o', str(ROOT / 'native.xml'), str(paths[1])], env)
tree = ET.parse(ROOT / 'native.xml')
values = {c.attrib['ref']: c.findtext('value') for c in tree.findall('./components/comp')}
nets = {n.attrib['name']: sorted(x.attrib['ref'] + '.' + x.attrib['pin'] for x in n.findall('node'))
        for n in tree.findall('./nets/net')}
fresh = json.loads(Path('/evidence/work/netlist/connectivity.json').read_text())
assert nets == {k: sorted(v) for k, v in fresh['nets'].items()}
assert values == {k: v['value'] for k, v in fresh['components'].items()}
assert values['R1'] == '10k' and values['C1'] == '100n'
save('connectivity_audit.json', {'native_values': values, 'native_nets': nets,
     'matches_fresh_check': True, 'requirements': paths[0].read_text()})
checks = {}
for name, filename in [('erc', 'erc.json'), ('drc', 'drc.json'), ('geometry', 'geometry.json')]:
    report = json.loads(Path('/evidence/work', name, filename).read_text())
    execution = json.loads(Path('/evidence/work', name, 'execution.json').read_text())
    checks[name] = {'execution': execution['execution'], 'exit_code': execution['exit_code'],
                    'report': report}
save('fresh_check_audit.json', checks)

R, C, t = 10000.0, 100e-9, 1e-3
v = 1 - math.exp(-t / (R*C))
save('calculation.json', {
    'assumptions': ['Ideal 0-to-1 V input step; C1 initially 0 V.',
                    'Nominal ideal R1=10k and C1=100n; no ADC or other load.',
                    'This is only an unloaded reference, not the required ADC-connected test.'],
    'R_ohm': R, 'C_F': C, 'tau_s': R*C, 't_s': t,
    'formula': 'Vout(t)=1-exp(-t/(R*C))', 'Vout_V': v,
    'unloaded_in_band': 0.62 <= v <= 0.65,
    'symbolic_ADC_dependence': {
        'finite_switch_while_closed': 'C1*dv/dt=(Vin-v)/R1-(v-vs)/Rsw; Cs*dvs/dt=(v-vs)/Rsw',
        'while_open': 'C1*dv/dt=(Vin-v)/R1; dvs/dt=0 (ideal isolated sample capacitor)',
        'fast_sharing_limit': 'v_after=(C1*v_before+Cs*vs_before)/(C1+Cs)',
        'limitation': 'Equations explain dependence only. No ADC Cs, Rsw, initial charge or switch sequence assigned; no actual loaded response computed.'}})
(ROOT / 'unloaded.cir').write_text('''Unloaded reference only; ADC-connected criterion untested
Vin in 0 DC 1
R1 in out 10k
C1 out 0 100n IC=0
.options reltol=1e-8 abstol=1e-12 vntol=1e-10
.tran 0.1u 1.1m 0 0.1u uic
.meas tran out_1ms FIND v(out) AT=1m
.end
''')
output = run('unloaded', ['ngspice', '-b', str(ROOT / 'unloaded.cir')])
measured = float(re.search(r'out_1ms\s*=\s*([\d.eE+-]+)', output).group(1))
save('measurement.json', {'unloaded_Vout_1ms_V': measured, 'analytic_Vout_1ms_V': v,
                         'difference_V': measured-v, 'ADC_connected_test_executed': False})
after = hashes()
save('native_hashes_after.json', after)
assert before == after
print(json.dumps({'values': values, 'nets': nets, 'unloaded_analytic_V': v,
                  'unloaded_spice_V': measured, 'candidate_unchanged': before == after}, indent=2))
