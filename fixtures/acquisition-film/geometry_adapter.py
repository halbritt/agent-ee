"""Parse retained geometry measurements with explicit dimensional units."""
import json
from pathlib import Path
import sys

request=json.loads(Path(sys.argv[1]).read_text())
raw=json.loads(Path('/work/tool/geometry-review.json').read_text())
counts={'ground_transition_missing','ground_corridor_missing','ground_plane_components','ground_saved_net_changes'}
areas={'body_overlap','body_outside','access_obstruction','ground_strip_outside'}
units={name:('V' if name=='rail_drop' else 'count' if name in counts
             else 'mm2' if name in areas else 'mm') for name in raw['metrics']}
report={'schema':'agent-ee.measurements/1','candidate_id':request['candidate_id'],
    'files':request['files'],'domain':'manufacturing','artifacts':['tool/geometry-review.json'],
    'metrics':{name:{'value':value,'unit':units[name],'condition':'nominal25C-reviewed-acquisition'}
               for name,value in raw['metrics'].items()}}
Path(sys.argv[2]).write_text(json.dumps(report,indent=2)+'\n')
