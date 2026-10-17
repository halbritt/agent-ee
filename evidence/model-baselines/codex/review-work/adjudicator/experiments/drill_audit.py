"""Run python3 /work/experiments/drill_audit.py. Parse coordinates numerically."""
from pathlib import Path
import json
import re

base=Path('/work/experiments')
s=(base/'drill/Polygonus-Universal-Base-PTH.drl').read_text()
targets={(75.55,-39.34),(199.65,-39.34),(71.95,-35.49),(120.,-28.49),(203.25,-35.49)}
hits=[]
tool=None
for line in s.splitlines():
    if re.fullmatch(r'T\d+',line): tool=line
    match=re.fullmatch(r'X(-?[\d.]+)Y(-?[\d.]+)',line)
    if match and tuple(map(float,match.groups())) in targets:
        hits.append(dict(tool=tool,hit=line))
out=dict(header=s.splitlines()[:8],tool_definitions=[l for l in s.splitlines() if l.startswith(('T10C','T12C'))],selected_hits=hits)
(base/'drill-audit.json').write_text(json.dumps(out,indent=2)+'\n')
assert len(hits)==5,hits
print('Confirmed all five plated hits')
