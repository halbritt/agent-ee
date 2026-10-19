"""Capture the two CAN reference sets or build their native composition audit.

The audit loads whole pinned sheets. It is not a standalone pod schematic:
unselected components remain present, and S remains grounded as in the source.
"""
import argparse
import copy
from pathlib import Path
import shutil
from uuid import NAMESPACE_URL, uuid5

from agent_ee.reference_blocks import capture_block, hierarchy_ports, validate_block
from agent_ee.storage import read_json, write_json

ROOT = Path(__file__).resolve().parent
BLOCK = ROOT / 'block'
COMMIT = 'df304f3321296f7e517e0d2e2aa52e1380d52993'
ROOT_UUID = '3b9c5ffd-e59b-402d-8c5e-052f7ca643a4'


def recapture(repository, destination):
    request = read_json(BLOCK / 'block.json')
    supplements = {p: (BLOCK / p).read_bytes() for p in request['files']
                   if p not in request['source']['paths'] and p != 'block-contract.json'}
    return capture_block(request, repository, COMMIT, destination, supplements=supplements,
                         repository_label='fome-mc')


def uid(name):
    return str(uuid5(NAMESPACE_URL, 'agent-ee/pod-can/audit/' + name))


def prepare(destination):
    """Write a scope/3 audit whose evidence comes from fresh native exports."""
    validate_block(BLOCK)
    destination = Path(destination)
    destination.mkdir()
    shutil.copytree(BLOCK, destination / 'block')
    project = destination / 'project'
    project.mkdir()
    record = read_json(BLOCK / 'block.json')
    body = [f'(kicad_sch (version 20250114) (generator "eeschema") '
            f'(uuid "{ROOT_UUID}") (paper "A3") (lib_symbols)']
    for index, instance in enumerate(record['instances']):
        name = instance['path'].strip('/')
        path = Path(instance['sheet'])
        shutil.copyfile(BLOCK / path, project / path.name)
        x = 40 + index * 100
        ports = hierarchy_ports((BLOCK / path).read_bytes())
        sheet_uuid = instance['uuid_path'].strip('/')
        body.append(f'(sheet (at {x} 30) (size 60 110) (uuid "{sheet_uuid}") '
                    f'(property "Sheetname" "{name}" (at {x} 28 0) (effects (font (size 1 1)))) '
                    f'(property "Sheetfile" "{path.name}" (at {x} 142 0) (effects (font (size 1 1))))')
        for i, (port, direction) in enumerate(sorted(ports.items())):
            y = 35 + 5 * i
            body.append(f'(pin "{port}" {direction} (at {x} {y} 180) '
                        f'(effects (font (size 1 1))) (uuid "{uid(name + port)}"))')
        body.append(')')
        for i, port in enumerate(sorted(ports)):
            y = 35 + 5 * i
            # Join only the selected PSU D4/R9 bus to wbo1's U307 bus.
            net = {'wbo1': {'CANH2': 'CAN+', 'CANL2': 'CAN-'},
                   'psu': {'CANH': 'CAN+', 'CANL': 'CAN-'}}[name].get(port, name + '_' + port)
            body.append(f'(label "{net}" (at {x} {y} 0) (effects (font (size 1 1))) '
                        f'(uuid "{uid(name + port + "/label")}"))')
    body.append(')')
    (project / 'audit.kicad_sch').write_text('\n'.join(body) + '\n')
    (project / 'audit.kicad_pcb').write_text(
        '(kicad_pcb (version 20250114) (generator "pcbnew") (general (thickness 1.6)) '
        '(paper "A4") (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user)) '
        '(setup (pad_to_mask_clearance 0)))\n')
    contract = read_json(BLOCK / 'interface.json')
    source = {'path': 'block/requirements.md', 'locator': 'CAN composition'}
    for invariant in contract['invariants']:
        invariant['source'] = source
    write_json(destination / 'interface.json', contract)
    requirements = copy.deepcopy(record['requirements'])
    for requirement in requirements:
        requirement.update(intent='acceptance' if requirement['checks'] == ['interface']
                           else 'informational', source=source)
    scope = {'schema': 'agent-ee.scope/3', 'name': 'Pod CAN reference-set audit',
             'scope': 'Fresh native composition evidence; no standalone block or board qualification.',
             'schematic': 'project/audit.kicad_sch', 'board': 'project/audit.kicad_pcb',
             'inputs': [{'path': p, 'at': p} for p in ('project', 'block', 'interface.json')],
             'mutable_inputs': [], 'requirements': ['block/requirements.md'],
             'checks': record['check_set'],
             'baseline': {'requirements': requirements, 'targets': ['block/requirements.md'],
                          'reference_architectures': ['block/block.json']},
             'limitations': ['Whole source sheets retain unselected parts. S is grounded; VIO is +3V3_WBO1.',
                             'No ERC/DRC, parts qualification, independent review, or sealed source qualification.']}
    write_json(destination / 'scope.json', scope)
    return destination / 'scope.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('capture', 'prepare'))
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--repo', type=Path)
    args = parser.parse_args()
    if args.command == 'capture':
        if args.repo is None:
            parser.error('capture requires --repo (local Git object store)')
        print(recapture(args.repo, args.out)['status'])
    else:
        print(prepare(args.out))


if __name__ == '__main__':
    main()
