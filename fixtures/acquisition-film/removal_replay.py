"""Recompute every declared removal case from retained native traces."""
from hashlib import sha256
import json
from pathlib import Path
import sys

import power_analysis


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


root, output = Path(sys.argv[1]), Path(sys.argv[2])
base = root / 'analysis/film-removal'
manifest = json.loads((base / 'evidence-manifest.json').read_bytes())
for name, expected in manifest['files'].items():
    if digest(root / name) != expected:
        raise ValueError('retained removal evidence changed: ' + name)
for name in ('circuit', 'parts'):
    if digest(root / (name + '.json')) != manifest[name + '_sha256']:
        raise ValueError('operative ' + name + ' differs from the executed removal application')
if digest(base / 'power_analysis.py') != manifest['power_analysis_sha256']:
    raise ValueError('retained measurement reader changed')
matrix = json.loads((base / 'matrix-completion.json').read_bytes())
if (matrix['execution'] != 'ok' or len(manifest['cases']) != 8
        or sorted(r['case'] for r in matrix['completed']) != sorted(manifest['cases'])):
    raise ValueError('retained removal matrix is incomplete')
cases = {}
for name, expected in sorted(manifest['cases'].items()):
    case = base / expected['directory']
    parameters = json.loads((case / 'case.json').read_bytes())
    if parameters != expected['parameters']:
        raise ValueError('independent capacitor case changed: ' + name)
    pins_raw = (case / 'input-pins.json').read_bytes()
    pins = json.loads(pins_raw)
    execution = json.loads((case / 'execution.json').read_bytes())
    native_deck = 'Z:' + (expected['source_root'] + '/probe/high-z-removal.cir').replace('/', chr(92))
    if (sha256(pins_raw).hexdigest() != execution['input_pins_sha256']
            or execution['exit_code'] != 0 or execution['timed_out'] or execution['input_changes']
            or any(row.get('exit_code') != 0 for row in execution['cleanup'])
            or digest(case / 'run.py') != execution['runner_sha256']
            or execution['command'][-3:] != ['-b', '-ascii', native_deck]):
        raise ValueError('retained native execution did not complete unchanged: ' + name)
    for member in ('run.py', 'case.json'):
        if pins['files'].get(expected['source_root'] + '/' + member) != digest(case / member):
            raise ValueError('native case source was not bound before execution: ' + name)
    for member in ('circuit', 'parts'):
        found = [value for path, value in pins['files'].items() if Path(path).name == member + '.json']
        if found != [manifest[member + '_sha256']]:
            raise ValueError('native case used another ' + member)
    for path, value in pins['generated_files'].items():
        if not path.startswith(expected['source_root'] + '/probe/') or digest(case / 'probe' / Path(path).name) != value:
            raise ValueError('executed model or stimulus differs: ' + name)
    for suffix in ('raw', 'log'):
        member = 'high-z-removal.' + suffix
        if digest(case / 'probe' / member) != execution['outputs'][member]['sha256']:
            raise ValueError('native output changed: ' + name)
    result = power_analysis.film_measurements((case / 'probe/high-z-removal.raw').read_bytes(),
                                            (case / 'probe/high-z-removal.log').read_bytes())
    cases[name] = dict(parameters=parameters, observations=result['observations'],
                       model_predicates=result['model_predicates'],
                       operating_point_recovery=result['operating_point_recovery'],
                       trace_sha256=result['trace_sha256'], log_sha256=result['log_sha256'])

observations = {}
worst_cases = {}
for key in ('steady_vout_range_v', 'vin_range_v', 'vout_range_v', 'source_range_a',
            'post_removal_source_range_a', 'post_removal_source_power_range_w',
            'post_removal_bleed_range_a'):
    low = min(cases, key=lambda name: cases[name]['observations'][key][0])
    high = max(cases, key=lambda name: cases[name]['observations'][key][1])
    observations[key] = [cases[low]['observations'][key][0], cases[high]['observations'][key][1]]
    worst_cases[key] = [low, high]
for key in ('pre_removal_host_max_abs_a', 'disconnected_source_max_abs_a',
            'post_removal_bus_max_abs_a', 'post_removal_reserve_max_abs_a',
            'removal_max_out_minus_in_v', 'steady_source_max_a'):
    name = max(cases, key=lambda name: cases[name]['observations'][key])
    observations[key] = cases[name]['observations'][key]
    worst_cases[key] = name
predicates = {key: all(case['model_predicates'][key] for case in cases.values())
              for key in next(iter(cases.values()))['model_predicates']}
report = dict(schema='agent-ee.film-removal-measurements/1', case_count=len(cases), cases=cases,
    observations=observations, worst_cases=worst_cases, model_predicates=predicates,
    all_named_model_predicates_pass=all(predicates.values()), accepted=False, file_release='not_assessed',
    evidence_operation='Fresh arithmetic over retained native outputs; no fresh simulation.',
    limitations=['Independent capacitance sensitivities at nominal25C, not guaranteed joint RLC/lot/temperature/aging bounds.',
                 'External host release and source opening retain the declared operating sequence.',
                 'Native model results do not establish physical validation or independent engineering acceptance.'])
output.write_text(json.dumps(report, indent=2) + '\n')
