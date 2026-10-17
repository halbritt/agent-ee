"""Read-only native reruns and command/result record for physical review."""
import os, sys, subprocess, json, platform, hashlib
from pathlib import Path

out = Path('/work/experiments')
env = dict(os.environ)
env.update(KICAD_CONFIG_HOME=str(out/'kicad-config'),
           KICAD10_SYMBOL_DIR='/candidate/stock/symbols',
           KICAD10_FOOTPRINT_DIR='/candidate/stock/footprints',
           KICAD10_TEMPLATE_DIR='/candidate/stock/template')
commands = [
    ['kicad-cli', 'version'],
    ['kicad-cli', 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', str(out/'design.xml'), '/candidate/project/rc_filter.kicad_sch'],
    ['kicad-cli', 'pcb', 'drc', '--format', 'json', '--severity-all', '--schematic-parity', '--units', 'mm', '-o', str(out/'drc.json'), '/candidate/project/rc_filter.kicad_pcb'],
    ['kicad-cli', 'sch', 'erc', '--format', 'json', '--severity-all', '--units', 'mm', '-o', str(out/'erc.json'), '/candidate/project/rc_filter.kicad_sch'],
]
hashes = lambda: {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/candidate/project').iterdir() if p.is_file()}
before = hashes()
records = []
for argv in commands:
    result = subprocess.run(argv, env=env, capture_output=True, text=True)
    records.append(dict(argv=argv, exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr))
report = dict(python=sys.version, platform=platform.platform(), environment_overrides={k:env[k] for k in ['KICAD_CONFIG_HOME','KICAD10_SYMBOL_DIR','KICAD10_FOOTPRINT_DIR','KICAD10_TEMPLATE_DIR']}, commands=records, candidate_hashes_before=before, candidate_hashes_after=hashes(), limitations=['No refill requested; saved zone fill only.', 'No candidate file written. Project hashes checked before/after.', 'ERC/DRC are native checks, not electrical behavior measurements.'])
(out/'native_commands.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps(records, indent=2))
