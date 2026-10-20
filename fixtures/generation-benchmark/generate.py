"""Rebuild the original series-resistor task; imports no third-party material."""
import importlib.util
import json
from pathlib import Path

from agent_ee.benchmark import children, parse
from agent_ee.storage import digest, write_json

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('owned_topology', ROOT.parent / 'topology-rules/generate.py')
owned = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owned)
root = ROOT / 'original-series'
parts = [('R1', 'Owned:R', '1k', ['IN', 'MID']), ('R2', 'Owned:R', '1k', ['MID', 'OUT']),
         ('J1', 'Owned:TP', 'IN', ['IN']), ('J2', 'Owned:TP', 'OUT', ['OUT'])]
source = owned.schematic(parts, project='circuit')
for old, new in [('30 40', '30.48 40.64'), ('30 42.54', '30.48 43.18'),
                 ('50 40', '50.8 40.64'), ('50 42.54', '50.8 43.18'),
                 ('70 40', '71.12 40.64'), ('90 40', '91.44 40.64')]:
    source = source.replace('(at ' + old + ' ', '(at ' + new + ' ')
    source = source.replace('(at ' + old + '.0 ', '(at ' + new + ' ')
(root / 'reference.kicad_sch').write_text(source)
cache = children(parse(source), 'lib_symbols')[0]
symbols = [source[n.start:n.end].replace('(symbol "Owned:', '(symbol "', 1) for n in children(cache, 'symbol')]
(root / 'evaluator/Owned.kicad_sym').write_text(
    '(kicad_symbol_lib (version 20241209) (generator "kicad_symbol_editor") ' + ''.join(symbols) + ')\n')
(root / 'evaluator/stock/template').mkdir(parents=True, exist_ok=True)
(root / 'evaluator/stock/template/sym-lib-table').write_text(
    '(sym_lib_table (version 7) (lib (name "Owned") (type "KiCad") '
    '(uri "/candidate/Owned.kicad_sym") (options "") (descr "Original agent-ee symbols")))\n')
(root / 'evaluator/stock/template/fp-lib-table').write_text('(fp_lib_table (version 7))\n')
# Keep requirements and scoring visibly authored in the frozen JSON, rather than inferred.
task = json.loads((root / 'task.json').read_text())
task['scope']['stages']['subjects']['verification']['references'] = ['J1', 'J2', 'R1', 'R2']
write_json(root / 'task.json', task)
rules = json.loads((root / 'evaluator/rules.json').read_text())
rules['classes']['Owned:TP'] = {'class': 'connector', 'terminals': {'1': '1'}}
components = [r for r in rules['rules'][0]['components'] if r['class'] != 'connector']
components.extend({'role': role, 'class': 'connector', 'terminals': {'1': net}}
                  for role, net in [('input', 'IN'), ('output', 'OUT')])
rules['rules'][0]['components'] = components
write_json(root / 'evaluator/rules.json', rules)
(root / 'prompt.md').write_text('Create a KiCad schematic with two 1k resistors in series from IN through MID to OUT, '
                              'with a single-pin connector at each endpoint. Use R1/R2 (Owned:R) and J1/J2 (Owned:TP). '
                              'Deliver circuit.kicad_sch. The following original symbol library defines these symbols:\n\n'
                              '```\n' + (root / 'evaluator/Owned.kicad_sym').read_text() + '```\n')
write_json(root / 'pins.json', {'schema': 'agent-ee.generation-pins/1', 'files': {
    p.relative_to(root).as_posix(): digest(p) for p in sorted(root.rglob('*'))
    if p.is_file() and p.name != 'pins.json'}})
