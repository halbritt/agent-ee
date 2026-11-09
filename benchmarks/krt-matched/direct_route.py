#!/usr/bin/env python3
"""Run `kicad-route run` on a placed board with the base flags agent-ee's adapter passes.

Used only as the labelled secondary measurement when `agent-ee place run`
refuses a board. Runs inside the CI image (PYTHONPATH=src):

    direct_route.py BOARD.kicad_pcb OUT_DIR BUDGET_S
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from agent_ee import routing_adapter

CHECKOUT = '/opt/kicad-route'


def main():
    board, out, budget = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
    staged = out / 'candidate' / board.stem
    staged.mkdir(parents=True)
    for suffix in ('.kicad_pcb', '.kicad_pro', '.kicad_dru'):
        if board.with_suffix(suffix).is_file():
            shutil.copyfile(board.with_suffix(suffix), staged / f'board{suffix}')
    argv = routing_adapter.board_route_argv(CHECKOUT, staged, out / 'out', design_name=board.stem, budget_s=budget)
    env = routing_adapter.board_route_env(CHECKOUT, out / 'home')
    start = time.monotonic()
    proc = subprocess.run(argv, env=env, capture_output=True, text=True)
    (out / 'kicad-route.stdout').write_text(proc.stdout)
    (out / 'kicad-route.stderr').write_text(proc.stderr)
    record = {'argv': argv, 'exit_code': proc.returncode, 'elapsed_s': round(time.monotonic() - start, 1), 'board': None}
    results = sorted(out.glob('out/runs/*/*/*/result.json'))
    if results:
        result = json.loads(results[-1].read_text())
        record['routing'] = result.get('routing')
        candidate = (result.get('candidate') or {}).get('board')
        if candidate and (Path.cwd() / candidate).is_file():   # the router reports paths from its cwd
            record['board'] = str((Path.cwd() / candidate).resolve())
        record['validation'] = {key: (result.get('validation') or {}).get(key) for key in ('status', 'reasons')}
    (out / 'direct-route.json').write_text(json.dumps(record, indent=1) + '\n')
    print(json.dumps(record, indent=1))


if __name__ == '__main__':
    main()
