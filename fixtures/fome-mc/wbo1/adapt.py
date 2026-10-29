"""Fixture writer: instantiate wbo1, then apply the declared wbo2 parameters.

This is a bounded regression fixture. Legacy block admission refuses unresolved
pending requirements. The separate native probe characterizes topology without
claiming that the captured block can be instantiated or qualified.
"""
import argparse
import copy
from pathlib import Path
import re
import shutil
import uuid

from agent_ee.block_adaptation import instantiate
from agent_ee.hardware_interface import placed_pins
from agent_ee.schematic_emit import Quoted, format_sexpr, parse_quoted
from agent_ee.storage import read_json, write_json
from agent_ee.symbol_library import children

HERE = Path(__file__).resolve().parent
ROOT_UUID = '3b9c5ffd-e59b-402d-8c5e-052f7ca643a4'
INSTANCE_UUID = 'a1000000-0000-4000-8000-000000000005'


def dump(tree):
    return ('\n'.join(format_sexpr(tree)) + '\n').encode()


def wrapper(blocks):
    """Original parent geometry; bindings come from each captured block."""
    text = f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "{ROOT_UUID}") (paper "A3") (lib_symbols)'
    for column, (name, record) in enumerate(blocks):
        instance = record['instances'][0]['uuid_path'].strip('/')
        x = 40 + column * 80
        text += f'''(sheet (at {x} 30) (size 45 75) (uuid "{instance}")
          (property "Sheetname" "{name}" (at {x} 28 0) (effects (font (size 1.27 1.27))))
          (property "Sheetfile" "{name}.kicad_sch" (at {x} 107 0) (effects (font (size 1.27 1.27))))'''
        labels = ''
        for i, port in enumerate(record['boundary_ports']):
            y = 35 + 5 * i
            text += f'''(pin "{port['name']}" {port['direction']} (at {x} {y} 180)
              (effects (font (size 1 1))) (uuid "{uuid.uuid5(uuid.UUID(instance), port['name'])}"))'''
            labels += f'''(global_label "{port['net_name']}" (shape passive) (at {x} {y} 0)
              (effects (font (size 1 1))) (uuid "{uuid.uuid5(uuid.UUID(instance), 'net:' + port['name'])}"))'''
        text += ')' + labels
    return (text + ')\n').encode()


def adapt_sheet(raw, parameters):
    """Rename physical references/nets and change only the two MCU straps."""
    tree = parse_quoted(raw)

    def offset(reference):
        if reference.startswith('#'):
            return reference
        match = re.fullmatch(r'([A-Za-z_]+)([0-9]+)', reference)
        if match is None:
            raise ValueError('fixture reference has no designator number')
        return Quoted(match[1] + str(int(match[2]) + parameters['reference_offset']))

    for symbol in children(tree, 'symbol'):
        for prop in children(symbol, 'property'):
            if prop[1] == 'Reference':
                prop[2] = offset(prop[2])
        for instances in children(symbol, 'instances'):
            for project in children(instances, 'project'):
                project[1] = Quoted('reference')
                for path in children(project, 'path'):
                    path[1] = Quoted('/' + ROOT_UUID + '/' + INSTANCE_UUID)
                    for reference in children(path, 'reference'):
                        reference[1] = offset(reference[1])

    def rename(node):
        if isinstance(node, list):
            for i, atom in enumerate(node):
                if isinstance(atom, list):
                    rename(atom)
                elif isinstance(atom, Quoted):
                    for old, new in parameters['renames']:
                        atom = Quoted(atom.replace(old, new))
                    node[i] = atom
    rename(tree)
    if not parameters['straps']:
        return dump(tree)
    if parameters['straps'] != [dict(reference='U402', pin='15', net=None),
                                 dict(reference='U402', pin='18', net='+3V3_WBO2')]:
        raise ValueError('fixture writer supports only the declared wbo2 strap pair')
    mcu = next(s for s in children(tree, 'symbol')
               if any(p[1:3] == ['Reference', 'U402'] for p in children(s, 'property')))
    library = {s[1]: s for s in children(children(tree, 'lib_symbols')[0], 'symbol')}
    pins = placed_pins(mcu, library)
    points = {pin: tuple(str(v / 1000000) for v in pins[pin][0]['point']) for pin in ('15', '18')}

    def at(node, point):
        return tuple(map(float, node[1:3])) == tuple(map(float, point))
    wires = [w for w in children(tree, 'wire')
             if any(at(p, points['15']) for p in children(children(w, 'pts')[0], 'xy'))]
    markers = [n for n in children(tree, 'no_connect') if at(children(n, 'at')[0], points['18'])]
    if len(wires) != 1 or len(markers) != 1:
        raise ValueError('pinned source strap geometry differs')
    tree.remove(wires[0])
    tree.remove(markers[0])
    x, y = points['15']
    tree.append(parse_quoted(f'(no_connect (at {x} {y}) (uuid "826129dd-eb5b-4408-9c94-433228235790"))'.encode()))
    x, y = points['18']
    tree.append(parse_quoted(f'''(global_label "+3V3_WBO2" (shape passive) (at {x} {y} 0)
      (effects (font (size 1 1))) (uuid "cf7534b6-1537-4784-8bb8-baf195603ec1"))'''.encode()))
    return dump(tree)


def _scaffold(out):
    """Owned fixture scaffolding; never qualifies the captured pending block."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(HERE / 'blocks/wbo1', out / 'blocks/wbo1')
    shutil.copytree(HERE / 'blocks/wbo2', out / 'blocks/wbo2')
    project = out / 'project'
    project.mkdir()
    target = read_json(out / 'blocks/wbo2/block.json')
    (project / 'reference.kicad_sch').write_bytes(wrapper([('wbo2', target)]))
    (project / 'wbo2.kicad_sch').write_text(f'''(kicad_sch (version 20250114) (generator "eeschema")
      (uuid "{ROOT_UUID}") (lib_symbols))''')
    shutil.copyfile(HERE / 'reference.kicad_pcb', project / 'reference.kicad_pcb')
    shutil.copyfile(HERE / 'COVERAGE.md', out / 'COVERAGE.md')
    scope = read_json(HERE / 'adaptation.scope.json')
    interface = read_json(out / 'blocks/wbo2/interface.json')
    for invariant in interface['invariants']:
        invariant['requirement'] = 'wbo2-wbo1-interface'
        invariant['source'] = dict(path='blocks/wbo1/block.json', locator='requirements/wbo1-interface')
    write_json(out / 'adapted-interface.json', interface)
    request = dict(id='wbo2', block='blocks/wbo1', sheet='project/wbo2.kicad_sch',
        instance_path='/' + INSTANCE_UUID + '/',
        bindings={p['name']: p['net_name'] for p in target['boundary_ports']},
        requirements={'wbo1-native': ['erc', 'netlist'], 'wbo1-interface': ['adapted-interface']},
        record='instance.json', provenance_check='provenance', netlist_check='netlist')
    return out, project, scope, request


def prepare(out, *, straps=True):
    """Legacy public admission: pending electrical requirements must refuse."""
    out, project, scope, request = _scaffold(out)
    scope = instantiate(out, scope, request)
    return _finish(out, project, scope, straps)


def prepare_native_probe(out, *, straps=True):
    """Characterize native topology only, without claiming block adaptation."""
    out, project, scope, request = _scaffold(out)
    source = read_json(out / 'blocks/wbo1/block.json')
    (project / 'wbo2.kicad_sch').write_bytes((out / 'blocks/wbo1' / source['sheet']).read_bytes())
    scope['inputs'] = [i for i in scope['inputs'] if i['at'] != 'instance.json']
    scope['baseline']['requirements'].append(dict(id='probe-erc',statement='Fresh native ERC of the regenerated topology only.',
        source=dict(path='COVERAGE.md',locator='Unknown coverage'),checks=['erc'],intent='acceptance'))
    scope['baseline']['requirements'].append(dict(id='wbo2-wbo1-interface',
        statement='Characterize regenerated channel topology; pending function and CAN-ID obligations remain unassessed.',
        source=dict(path='blocks/wbo1/block.json', locator='requirements/wbo1-interface'), checks=['adapted-interface'], intent='acceptance'))
    scope['limitations'].append('Native topology characterization only; block adaptation is refused by its unchanged pending requirements. No source/adaptation qualification.')
    historical=(out/'COVERAGE.md').read_bytes()
    (out/'coverage-legacy.md').write_bytes(historical)
    (out/'COVERAGE.md').write_bytes(b'Current native probe: no block-provenance admission or qualification. All pending requirements remain unassessed. The legacy fixture description below is retained history.\n\n'+historical)
    scope['inputs'].append(dict(path='coverage-legacy.md',at='coverage-legacy.md'))
    write_json(out / 'probe-coverage.json', dict(kind='unqualified native topology characterization',
        pending_requirements=source['pending_requirements'], evaluation='not_evaluated'))
    scope['inputs'].append(dict(path='probe-coverage.json', at='probe-coverage.json'))
    return _finish(out, project, scope, straps)


def _finish(out, project, scope, straps):
    parameters = copy.deepcopy(read_json(HERE / 'wbo1-to-wbo2.json')['parameters'])
    if not straps:
        parameters['straps'] = []
    child = project / 'wbo2.kicad_sch'
    child.write_bytes(adapt_sheet(child.read_bytes(), parameters))
    write_json(out / 'scope.json', scope)
    return scope


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    prepare(args.out)


if __name__ == '__main__':
    main()
