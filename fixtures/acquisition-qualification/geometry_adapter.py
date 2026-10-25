"""Parse retained numerical geometry output; acceptance limits remain in scope."""
import json,sys
from pathlib import Path
request=json.loads(Path(sys.argv[1]).read_text());raw=json.loads(Path('/work/tool/geometry-review.json').read_text())
units={k:('V' if k=='rail_drop' else 'mm2' if k in ['body_overlap','body_outside','access_obstruction','ground_strip_outside'] else 'mm') for k in raw['metrics']}
report={'schema':'agent-ee.measurements/1','candidate_id':request['candidate_id'],'files':request['files'],'domain':'manufacturing','artifacts':['tool/geometry-review.json'],'metrics':{k:{'value':v,'unit':units[k],'condition':'nominal25C-reviewed-acquisition'} for k,v in raw['metrics'].items()}}
Path(sys.argv[2]).write_text(json.dumps(report,indent=2)+'\n')
