"""Replay retained model data against captured circuit/part identities.

This produces fresh arithmetic from an old waveform, not a new LTspice run or
device qualification. The ordinary checker evaluates externally authored limits.
"""
from hashlib import sha256
import json
from pathlib import Path
import sys

import power_analysis

root, output = Path(sys.argv[1]), Path(sys.argv[2])
case = root / 'analysis/adp7104'
declaration = json.loads((case / 'power-application.json').read_bytes())
custody = json.loads((case / 'retained-case.json').read_bytes())
for name, pin in custody['files'].items():
    if sha256((case / name).read_bytes()).hexdigest() != pin:
        raise ValueError('retained model evidence changed: ' + name)
if sha256((root / 'circuit.json').read_bytes()).hexdigest() != declaration['circuit_sha256']:
    raise ValueError('operative circuit differs from declared model application')
if declaration['circuit_sha256'] != custody['circuit_sha256']:
    raise ValueError('retained native model used another circuit')
if sha256((root / 'parts.json').read_bytes()).hexdigest() != declaration['parts_sha256']:
    raise ValueError('operative parts differ from declared application')
parts = {p['id']: p for p in json.loads((root / 'parts.json').read_bytes())['parts']}
if declaration['selections'] != custody['selections']:
    raise ValueError('retained model uses different electrical parts')
for ref, selection in declaration['selections'].items():
    if any(parts[ref][key] != value for key, value in selection.items()):
        raise ValueError('operative selected electrical part differs: ' + ref)
for name, pin in declaration['files'].items():
    if sha256((case / name).read_bytes()).hexdigest() != pin or custody['files'].get(name) != pin:
        raise ValueError('operative model/deck differs from retained native execution')
execution = json.loads((case / 'execution.json').read_bytes())
pins_raw = (case / 'input-pins.json').read_bytes()
if sha256(pins_raw).hexdigest() != execution['input_pins_sha256']:
    raise ValueError('retained execution input identity differs')
pins = json.loads(pins_raw)
historical = json.loads((case / 'executed-application.json').read_bytes())
if any(historical[key] != custody[key] for key in ('circuit_sha256', 'parts_sha256', 'selections')):
    raise ValueError('retained executed application differs from declared custody')
for name, pin in pins['generated_files'].items():
    member = 'executed-application.json' if Path(name).name == 'power-application.json' else Path(name).name
    if custody['files'].get(member) != pin:
        raise ValueError('retained executed model/deck identity differs')
for member, key in [('circuit.json', 'circuit_sha256'), ('parts.json', 'parts_sha256')]:
    found = [pin for name, pin in pins['files'].items() if Path(name).name == member]
    if found != [custody[key]]:
        raise ValueError('retained execution does not bind the original ' + member)
if execution['exit_code'] != 0 or execution['timed_out'] or execution['input_changes']:
    raise ValueError('retained native model run did not complete on unchanged inputs')
raw, log = [(case / ('high-z-removal.' + suffix)).read_bytes() for suffix in ('raw', 'log')]
for suffix, data in [('raw', raw), ('log', log)]:
    if sha256(data).hexdigest() != execution['outputs']['high-z-removal.' + suffix]['sha256']:
        raise ValueError('retained native output custody differs')
result = power_analysis.measurements(raw, log)
result['evidence_operation'] = 'replay of retained native model output; no fresh simulation'
result['historical_parts_sha256'] = custody['parts_sha256']
result['operative_parts_sha256'] = declaration['parts_sha256']
result['part_equivalence'] = 'Same exact circuit and selected model-relevant MPN/LCSC identities; source/CAD metadata is separately verified.'
output.write_text(json.dumps(result, indent=2) + '\n')
