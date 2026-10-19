#!/usr/bin/env python3
"""Emit the captured pod circuit with existing APIs and run native graph checks.

This is a reproducible input recipe, not a sealed review or fabrication release.
The explicitly requested unbound draft cannot establish purchasing properties.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from agent_ee.connectivity_ir import resolve_intent
from agent_ee.native_netlist import read_native_inventory
from agent_ee.netlist_parity import resolved_inventory, compare
from agent_ee.schematic_emitter import emit_schematic


def emit(bundle, output, *, unbound_draft=False):
    bundle, output = Path(bundle), Path(output)
    intent_path = bundle / ('intent.unbound.json' if unbound_draft else 'intent.json')
    intent = json.loads(intent_path.read_text())
    captures = {}
    for lib in intent['libraries']:
        raw = (bundle / lib['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != lib['sha256']:
            raise ValueError('symbol capture differs: ' + lib['path'])
        captures[lib['path']] = raw
    cad = json.loads((bundle / 'cad/manifest.json').read_text())
    footprints, tables = {}, {}
    for lib in cad['libraries']:
        directory = Path('native-footprints') / (lib['name'] + '.pretty')
        tables[lib['name']] = '${KIPRJMOD}/' + str(directory)
        for member in lib['members']:
            raw = (bundle / member['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != member['sha256']:
                raise ValueError('footprint capture differs: ' + member['path'])
            footprints[str(directory / Path(member['path']).name)] = raw
    # Captured manufactured-feature proposal, never a supplier identity.
    path = 'footprints/PodFeatures.pretty/PogoPad_D1.5mm.kicad_mod'
    raw = (bundle / path).read_bytes()
    lock = json.loads((bundle / 'power-source-lock.json').read_text())
    expected = next(p['sha256'] for p in lock['files'] if p['path'] == path)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('manufactured-feature footprint differs')
    footprints['native-footprints/PodFeatures.pretty/PogoPad_D1.5mm.kicad_mod'] = raw
    tables['PodFeatures'] = '${KIPRJMOD}/native-footprints/PodFeatures.pretty'
    layout = dict(schema='agent-ee.schematic-layout/1',
        namespace='62320717-99c6-4dd3-9575-7ba8c6cb3ad4', project='telemetry-pod', transforms={})
    result, emitted = emit_schematic(intent, captures, layout,
        symbol_table={lib['name']: '${KIPRJMOD}/' + lib['path'] for lib in intent['libraries']},
        footprint_table=tables)
    if emitted is None:
        raise ValueError('native emission refused: ' + repr(result['findings']))
    output.mkdir(parents=True, exist_ok=False)

    def write_json(name, value):
        (output / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')

    for path, raw in {**captures, **footprints, **emitted}.items():
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    write_json('intent.json', intent)
    write_json('emission.json', result)
    for name, args in [('netlist', ['export', 'netlist', '--format', 'kicadxml', '--output', str(output / 'native.xml')]),
                       ('erc', ['erc', '--format', 'json', '--severity-all', '--output', str(output / 'erc.json')])]:
        command = ['kicad-cli', 'sch', *args, str(output / 'telemetry-pod.kicad_sch')]
        run = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
        (output / (name + '.log')).write_bytes(run.stdout)
        write_json(name + '-execution.json', dict(argv=command, exit_code=run.returncode))
        if run.returncode:
            raise ValueError('native ' + name + ' process failed')
    resolved = resolve_intent(intent, captures)
    expected = resolved_inventory(resolved)
    actual = read_native_inventory(output / 'native.xml')
    policy = dict(predicate='topology', multiset='exactly',
        anchors={cid: dict(reference=c['reference']) for cid, c in expected['components'].items()})
    parity = compare(expected, actual, policy)
    write_json('resolved-intent.json', resolved)
    write_json('native-parity.json', parity)
    erc = json.loads((output / 'erc.json').read_text())
    violations = [v for sheet in erc['sheets'] for v in sheet['violations']]
    report = dict(done=False, unbound_draft=unbound_draft,
        intent_sha256=hashlib.sha256(intent_path.read_bytes()).hexdigest(),
        native_xml_sha256=hashlib.sha256((output / 'native.xml').read_bytes()).hexdigest(),
        physical_components=len(actual['components']),
        parity=dict(execution=parity['execution'], outcome=parity['outcome']),
        erc_violations=len(violations), erc_errors=sum(v['severity'] == 'error' for v in violations),
        limitations=['Native graph and ERC evidence only; no sealed campaign, placement/routing, physical qualification or fabrication release.',
                     'Purchasing bindings absent in unbound draft.' if unbound_draft else 'Purchasing identity is not engineering suitability.'])
    write_json('native-checks.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--unbound-draft', action='store_true')
    args = parser.parse_args()
    result = emit(args.bundle, args.out, unbound_draft=args.unbound_draft)
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result['parity'] == dict(execution='ok', outcome='pass') and not result['erc_violations'] else 1)
