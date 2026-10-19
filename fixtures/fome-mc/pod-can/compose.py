"""Prepare the selected four-part CAN child using ordinary block instantiation.

The source capture stays unverified. This is a producer/check fixture for AGENTEE-176
composition; its provenance evidence cannot replace independent source review.
"""
import argparse
from pathlib import Path
import shutil
from uuid import NAMESPACE_URL, uuid5

from agent_ee.block_adaptation import instantiate
from agent_ee.storage import read_json, write_json

ROOT = Path(__file__).resolve().parent
CHILD_UUID = '10adcb05-d59b-4149-a90d-a3957511e398'
ROOT_UUID = '3b9c5ffd-e59b-402d-8c5e-052f7ca643a4'
BINDINGS = {'CAN+': '/CAN+', 'CAN-': '/CAN-', 'TXD': '/TXD', 'RXD': '/RXD',
            'S': 'GND', '+5V': '+5V', '+3V3': '+3V3', 'GND': 'GND'}


def prepare(destination):
    destination = Path(destination)
    destination.mkdir()
    shutil.copytree(ROOT / 'block', destination / 'block')
    shutil.copyfile(ROOT / 'composition.json', destination / 'composition.json')
    project = destination / 'project'
    project.mkdir()
    plan = read_json(destination / 'composition.json')
    body = [f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "{ROOT_UUID}") '
            '(paper "A3") (lib_symbols)',
            f'(sheet (at 40 30) (size 60 100) (uuid "{CHILD_UUID}") '
            '(property "Sheetname" "can" (at 40 28 0) (effects (font (size 1 1)))) '
            '(property "Sheetfile" "can.kicad_sch" (at 40 132 0) (effects (font (size 1 1))))']
    labels = []
    for i, port in enumerate(plan['ports']):
        name, direction, y = port['name'], port['direction'], 35 + 10 * i
        uid = uuid5(NAMESPACE_URL, 'agent-ee/pod-can/composition/' + name)
        body.append(f'(pin "{name}" {direction} (at 40 {y} 180) '
                    f'(effects (font (size 1 1))) (uuid "{uid}"))')
        net = BINDINGS[name]
        kind = 'label' if net.startswith('/') else 'global_label'
        shape = '' if kind == 'label' else '(shape input)'
        labels.append(f'({kind} "{net.removeprefix("/")}" {shape} (at 40 {y} 0) '
                      f'(effects (font (size 1 1))) (uuid "{uuid5(uid, "net")}"))')
    body.extend([')', *labels, ')'])
    (project / 'pod-can.kicad_sch').write_text('\n'.join(body) + '\n')
    (project / 'can.kicad_sch').write_text(
        f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "{CHILD_UUID}") (lib_symbols))')
    (project / 'pod-can.kicad_pcb').write_text(
        '(kicad_pcb (version 20250114) (generator "pcbnew") (general (thickness 1.6)) '
        '(paper "A4") (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user)) '
        '(setup (pad_to_mask_clearance 0)))\n')
    record = read_json(destination / 'block/block.json')
    contract = read_json(destination / 'block/interface.json')
    for component in contract['components'].values():
        for location in component['locations']:
            location['sheet'] = '/' + CHILD_UUID + '/'
    aliases = {'/wbo1/CAN_TX': '/TXD', '/wbo1/CAN_RX': '/RXD', '+3V3_WBO1': '+3V3'}
    source = {'path': 'block/block.json', 'locator': 'requirements/pod-can-interface'}
    for invariant in contract['invariants']:
        invariant['source'] = source
        invariant['requirement'] = 'can-pod-can-interface'
        if 'names' in invariant:
            invariant['names'] = [aliases.get(n, n) for n in invariant['names']]
    write_json(destination / 'interface.json', contract)
    scope = {'schema': 'agent-ee.scope/3', 'name': 'Selected pod CAN composition',
        'scope': 'Four selected source references, native interface and both-source provenance; no source qualification.',
        'schematic': 'project/pod-can.kicad_sch', 'board': 'project/pod-can.kicad_pcb',
        'inputs': [{'path': p, 'at': p} for p in ('project', 'block', 'interface.json', 'composition.json', 'instance.json')],
        'mutable_inputs': ['project/pod-can.kicad_sch', 'project/can.kicad_sch'],
        'requirements': ['block/requirements.md'], 'checks': record['check_set'],
        'baseline': {'requirements': [], 'targets': ['block/requirements.md'],
                     'reference_architectures': ['block/block.json']},
        'limitations': ['Source capture is unverified; draft parts and missing independent source reviews remain open.',
                        'S is grounded, exposed only as an explicit GND alias. Current limits and electrical suitability are not established.',
                        'No actual pod, ERC/DRC, physical bench or fabrication acceptance.']}
    request = {'id': 'can', 'block': 'block', 'sheet': 'project/can.kicad_sch',
        'instance_path': '/' + CHILD_UUID + '/', 'bindings': BINDINGS,
        'requirements': {r['id']: r['checks'] for r in record['requirements']},
        'record': 'instance.json', 'provenance_check': 'provenance', 'netlist_check': 'netlist',
        'composition': 'composition.json'}
    scope = instantiate(destination, scope, request)
    write_json(destination / 'scope.json', scope)
    return destination / 'scope.json'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    print(prepare(parser.parse_args().out))
