#!/bin/bash
# Rung-1 campaign step `route-krt`: kicad-route's deterministic KRT backend on a copy of the
# placed project. The kicad-route checkout (with vendor/krt and .venv-krt) is a read-only
# mount at /tool/kicad-route and is also its state directory. The routed board and
# result.json are copied out of the stamped run directory (its `latest` entry is a link).
set -uo pipefail
name="$1"
export KICAD_ROUTE_STATE=/tool/kicad-route
mkdir -p /work/out/candidate
cp -r /candidate/project /work/out/project || exit 70
cd /work/out/project || exit 70
/tool/kicad-route/bin/kicad-route --out /work/out/route run --backend krt --budget 300
code=$?
/usr/bin/python3 -B -P - "$name" <<'PY' || { [ "$code" -eq 0 ] && exit 70; }
import json, shutil, sys
from pathlib import Path
out = Path('/work/out')
results = sorted(p for p in out.glob('route/runs/*/*/*/result.json') if not p.parent.is_symlink())
if len(results) != 1:
    sys.exit(f'expected one kicad-route result.json, found {len(results)}')
shutil.copyfile(results[0], out / 'result.json')
result = json.loads(results[0].read_text())
candidate = out / 'route' / result['candidate']['dir'] / (sys.argv[1] + '.kicad_pcb')
if candidate.is_file():
    shutil.copyfile(candidate, out / 'candidate' / candidate.name)
PY
exit "$code"
