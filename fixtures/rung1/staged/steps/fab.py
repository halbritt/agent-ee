"""Rung-1 fab step: sealed native export, then captured CPL rotation correction."""
import sys

sys.path.insert(0, '/tool/agent-ee')
from agent_ee.fab_stage import main

name = sys.argv[1]
raise SystemExit(main(['--board', f'project/{name}.kicad_pcb',
                      '--schematic', f'project/{name}.kicad_sch']))
