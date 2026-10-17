"""Rung-1 campaign step `export-fab`: kicad-cli fab exports of the delivered design.

Gerbers (enabled copper + F/B Mask, Paste, Silkscreen, Edge.Cuts), Excellon drill with
separate PTH/NPTH, positions and BOM; CPL rewritten to JLCPCB columns and not_assembled
parts dropped with a recorded exclusion list; fab-manifest.json hashes every file. Reuses
agent_ee.generate's stage-7 helpers. This is the driver's export; the qualified check kind
is AGENTEE-23. Exit 0 complete, 3 incomplete package, 5 a native export failed.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, '/tool/agent-ee')
from agent_ee import generate as g  # noqa: E402
from agent_ee.storage import digest, write_json  # noqa: E402

name = sys.argv[1]
candidate = Path('/candidate')
board, sch = candidate / 'project' / f'{name}.kicad_pcb', candidate / 'project' / f'{name}.kicad_sch'
out, raw, package = Path('/work/out'), Path('/work/out/raw'), Path('/work/out/fab')
config = Path('/work/kicad-config/10.0')
config.mkdir(parents=True, exist_ok=True)
for table in ('sym-lib-table', 'fp-lib-table'):
    shutil.copyfile(candidate / 'stock' / 'template' / table, config / table)
layers = g.copper_layers(board.read_text()) + list(g.FAB_LAYERS)
parts = json.loads((candidate / 'generation' / 'parts-manifest.json').read_text())
excluded = {e['ref']: e for e in parts['assembly_exclusions']}
commands = {
    'gerbers': ['kicad-cli', 'pcb', 'export', 'gerbers', '--layers', ','.join(layers), '-o', str(raw / 'gerbers') + '/',
                str(board)],
    'drill': ['kicad-cli', 'pcb', 'export', 'drill', '--format', 'excellon', '--excellon-units', 'mm',
              '--excellon-separate-th', '-o', str(raw / 'drill') + '/', str(board)],
    'pos': ['kicad-cli', 'pcb', 'export', 'pos', '--format', 'csv', '--units', 'mm', '--side', 'both',
            '--exclude-dnp', '-o', str(raw / 'pos' / 'positions.csv'), str(board)],
    'bom': ['kicad-cli', 'sch', 'export', 'bom', '--fields', 'Value,Reference,Footprint,LCSC',
            '--labels', 'Comment,Designator,Footprint,LCSC Part #', '--group-by', 'Value,Footprint,LCSC',
            '--ref-delimiter', ',', '--ref-range-delimiter', '', '--exclude-dnp',
            '-o', str(raw / 'bom' / 'bom.csv'), str(sch)],
}
log = []
for label, argv in commands.items():
    (raw / label).mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    (raw / f'{label}.log').write_text(proc.stdout + proc.stderr)
    log.append({'label': label, 'argv': argv, 'exit_code': proc.returncode})
    if proc.returncode != 0:
        write_json(out / 'fab-commands.json', log)
        print(f'kicad-cli {label} export failed (exit {proc.returncode})', file=sys.stderr)
        raise SystemExit(5)
write_json(out / 'fab-commands.json', log)
gerbers, drills = sorted((raw / 'gerbers').iterdir()), sorted((raw / 'drill').iterdir())
for sub, files in (('gerbers', gerbers), ('drill', drills)):
    (package / sub).mkdir(parents=True)
    for path in files:
        shutil.copyfile(path, package / sub / path.name)
try:
    cpl, cpl_kept, cpl_dropped = g.jlcpcb_cpl((raw / 'pos' / 'positions.csv').read_text(), excluded)
    bom, bom_kept, bom_dropped = g.jlcpcb_bom((raw / 'bom' / 'bom.csv').read_text(), excluded)
except g.StageFailure as exc:
    print(f'fab rewrite refused: {exc}', file=sys.stderr)
    raise SystemExit(3)
(package / 'cpl.csv').write_text(cpl)
(package / 'bom.csv').write_text(bom)
g.deterministic_zip(package / f'{name}-gerbers.zip',
                    {p.name: p for p in sorted((package / 'gerbers').iterdir()) + sorted((package / 'drill').iterdir())})
files = sorted(p for p in package.rglob('*') if p.is_file())
empty = [p.name for p in files if p.stat().st_size == 0]
manifest = {
    'schema': 'agent-ee.rung1-campaign-fab-manifest/0-experimental', 'qualification': 'experimental-unqualified',
    'producer': 'fixtures/rung1/staged/steps/fab.py (driver export run as a campaign stage step)',
    'qualified_check': 'AGENTEE-23 (fab_export check kind; patch attached there, not duplicated here)',
    'board': {'path': f'/candidate/project/{name}.kicad_pcb', 'sha256': digest(board)},
    'schematic': {'path': f'/candidate/project/{name}.kicad_sch', 'sha256': digest(sch)},
    'kicad_cli': g.tool_identity('kicad-cli'), 'gerber_layers': layers, 'commands': log,
    'assembly_exclusions': list(excluded.values()),
    'bom_designators': bom_kept, 'cpl_designators': cpl_kept,
    'excluded_from_bom': bom_dropped, 'excluded_from_cpl': cpl_dropped,
    'files': [{'path': str(p.relative_to(package)), 'bytes': p.stat().st_size, 'sha256': digest(p)} for p in files],
    'limitations': ['Gerber and drill files embed creation timestamps; hashes bind these bytes only',
                    'CPL rotations are KiCad-native; JLCPCB rotation correction is AGENTEE-72',
                    'No DFM, layer reconciliation or Gerber re-parse (AGENTEE-73, AGENTEE-74, AGENTEE-76)']}
write_json(out / 'fab-manifest.json', manifest)
if empty or len(gerbers) < len(layers) or not drills or not cpl_kept or not bom_kept or sorted(cpl_kept) != sorted(bom_kept):
    print(f'fab package incomplete: empty={empty} gerbers={len(gerbers)} drills={len(drills)} '
          f'bom={bom_kept} cpl={cpl_kept}', file=sys.stderr)
    raise SystemExit(3)
print(json.dumps({'status': 'ok', 'files': len(files), 'manifest': str(out / 'fab-manifest.json')}))
