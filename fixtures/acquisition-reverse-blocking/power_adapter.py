"""Expose sampled model values; acceptance limits remain in the typed scope."""
import json
from pathlib import Path
import sys

request = json.loads(Path(sys.argv[1]).read_bytes())
result = json.loads(Path('/work/tool/model-measurements.json').read_bytes())
row = result['observations']
values = {
    'steady_vout_min': (row['steady_vout_range_v'][0], 'V'),
    'steady_vout_max': (row['steady_vout_range_v'][1], 'V'),
    'vout_max': (row['vout_range_v'][1], 'V'),
    'vout_min': (row['vout_range_v'][0], 'V'),
    'vin_min': (row['vin_range_v'][0], 'V'),
    'vin_max': (row['vin_range_v'][1], 'V'),
    'source_max': (row['source_range_a'][1], 'A'),
    'source_min': (row['source_range_a'][0], 'A'),
    'host_current_on_removal': (row['post_removal_bus_max_abs_a'], 'A'),
    'reserve_current_on_removal': (row['post_removal_reserve_max_abs_a'], 'A'),
    'source_power_on_removal': (row['post_removal_source_power_range_w'][1], 'W'),
}
report = dict(schema='agent-ee.measurements/1', candidate_id=request['candidate_id'], files=request['files'],
    domain='electrical', artifacts=['tool/model-measurements.json'],
    metrics={name: dict(value=value, unit=unit, condition='adp7104-25c-highz-zero-target-model')
             for name, (value, unit) in values.items()})
Path(sys.argv[2]).write_text(json.dumps(report, indent=2) + '\n')
