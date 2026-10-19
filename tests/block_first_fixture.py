"""Owned two-pin IC blocks and independently authored topology expectations."""
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from xml.etree import ElementTree as ET

from agent_ee.storage import write_json


def uid(name):
    return str(uuid5(NAMESPACE_URL, 'agent-ee-block-first-owned/' + name))


def prop(key, value):
    return f'(property "{key}" "{value}" (at 0 0 0) (effects (font (size 1 1)) (hide yes)))'


def definition(name, reference):
    return (f'(symbol "{name}" (pin_names (offset 0)) (in_bom yes) (on_board yes) '
            + prop('Reference', reference) + prop('Value', name)
            + f'(symbol "{name}_0_1" (rectangle (start 2.54 1.27) (end 7.62 -3.81) '
              '(stroke (width 0.254) (type default)) (fill (type background))))'
            + f'(symbol "{name}_1_1" '
            + ''.join(f'(pin passive line (at 0 {-2.54*i} 0) (length 2.54) '
                      f'(name "P{i+1}" (effects (font (size 1.27 1.27)))) '
                      f'(number "{i+1}" (effects (font (size 1.27 1.27)))))' for i in range(2)) + '))')


def schematic(index):
    stem = f'block{index}'
    symbols = []
    for ref, name, x, value in ((f'U{index}', 'IC', 40.64, 'owned-ic'), (f'R{index}', 'R', 71.12, '1k')):
        symbols.append(f'(symbol (lib_id "Owned:{name}") (at {x} 50.8 0) (unit 1) '
            f'(in_bom yes) (on_board yes) (dnp no) (uuid "{uid(ref)}")'
            + prop('Reference', ref) + prop('Value', value)
            + ''.join(f'(pin "{pin}" (uuid "{uid(ref+str(pin))}"))' for pin in (1, 2))
            + f'(instances (project "verify{index}" (path "/{uid("verify"+str(index))}/{uid("sheet"+str(index))}" (reference "{ref}") (unit 1))) '
              f'(project "top" (path "/{uid("top")}/{uid("sheet"+str(index))}" (reference "{ref}") (unit 1)))))')
        for i, net in enumerate(('A', 'B')):
            symbols.append(f'(label "{net}" (at {x} {50.8+2.54*i} 0) '
                f'(effects (font (size 1.27 1.27)) (justify left bottom)) (uuid "{uid(ref+net)}"))')
    for i, net in enumerate(('A', 'B')):
        symbols.append(f'(hierarchical_label "{net}" (shape passive) (at 20.32 {50.8+2.54*i} 180) '
            f'(effects (font (size 1.27 1.27)) (justify right)) (uuid "{uid(stem+net)}"))')
        symbols.append(f'(wire (pts (xy 20.32 {50.8+2.54*i}) (xy 40.64 {50.8+2.54*i})) '
            f'(stroke (width 0) (type default)) (uuid "{uid(stem+"wire"+net)}"))')
    libs = definition('IC', 'U').replace('(symbol "IC"', '(symbol "Owned:IC"', 1)
    libs += definition('R', 'R').replace('(symbol "R"', '(symbol "Owned:R"', 1)
    return (f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "{uid(stem)}") '
            f'(paper "A4") (lib_symbols {libs}) {"".join(symbols)})\n')


def golden(indices):
    root = ET.fromstring('<export version="E"><design><sheet tstamps="/"/></design>'
                         '<components/><libparts/><nets/></export>')
    for name in ('IC', 'R'):
        part = ET.SubElement(root.find('libparts'), 'libpart', lib='Owned', part=name)
        pins = ET.SubElement(part, 'pins')
        for pin in ('1', '2'):
            ET.SubElement(pins, 'pin', num=pin, name='P'+pin, type='passive')
    for index in indices:
        for ref, name, value in ((f'U{index}', 'IC', 'owned-ic'), (f'R{index}', 'R', '1k')):
            comp = ET.SubElement(root.find('components'), 'comp', ref=ref)
            ET.SubElement(comp, 'value').text = value
            ET.SubElement(comp, 'libsource', lib='Owned', part=name)
            ET.SubElement(comp, 'sheetpath', tstamps='/')
            ET.SubElement(comp, 'tstamps').text = uid(ref)
            pins = ET.SubElement(ET.SubElement(ET.SubElement(comp, 'units'), 'unit', name='A'), 'pins')
            for pin in ('1', '2'):
                ET.SubElement(pins, 'pin', num=pin)
        for pin in ('1', '2'):
            net = ET.SubElement(root.find('nets'), 'net', code=str(index*10+int(pin)), name=f'{index}-{pin}')
            for ref in (f'U{index}', f'R{index}'):
                ET.SubElement(net, 'node', ref=ref, pin=pin)
    return ET.tostring(root)


def scope(inputs, sheet, golden_path, requirements):
    return dict(schema='agent-ee.scope/3', schematic=sheet,
        inputs=[dict(path=p, at=p) for p in inputs], requirements=[requirements],
        mutable_inputs=[sheet],
        checks=[dict(id='erc', kind='erc'), dict(id='netlist', kind='netlist'),
                dict(id='parity', kind='netlist_parity', params=dict(
                    source=dict(kind='golden_xml', path=golden_path), netlist_check='netlist',
                    predicate='topology', multiset='exactly'))],
        baseline=dict(requirements=[
            dict(id='native', intent='informational', statement='Export native connectivity.',
                 source=dict(path=requirements, locator='line 1'), checks=['netlist']),
            dict(id='safe', intent='acceptance', statement='No ERC defects and exact topology.',
                 source=dict(path=requirements, locator='line 2'), checks=['erc', 'parity'])],
            targets=[requirements], reference_architectures=[golden_path]))


def fixture(root):
    root = Path(root)
    project = root / 'project'
    project.mkdir()
    library = '(kicad_symbol_lib (version 20241209) (generator "eeschema") ' + definition('IC', 'U') + definition('R', 'R') + ')'
    (project / 'Owned.kicad_sym').write_text(library)
    (project / 'sym-lib-table').write_text('(sym_lib_table (version 7) (lib (name "Owned") (type "KiCad") (uri "${KIPRJMOD}/Owned.kicad_sym") (options "") (descr "")))')
    (project / 'fp-lib-table').write_text('(fp_lib_table (version 7))')
    template = root / 'stock/template'
    template.mkdir(parents=True)
    (template / 'sym-lib-table').write_text('(sym_lib_table (version 7))')
    (template / 'fp-lib-table').write_text('(fp_lib_table (version 7))')
    shared = ['project/Owned.kicad_sym', 'project/sym-lib-table', 'project/fp-lib-table',
              'stock/template/sym-lib-table', 'stock/template/fp-lib-table']
    scopes, all_inputs, nodes = {}, list(shared), []
    for index in (1, 2):
        stem = f'block{index}'
        sheet = f'project/{stem}.kicad_sch'
        (root / sheet).write_text(schematic(index))
        expected, req = f'{stem}.xml', f'{stem}.md'
        (root / expected).write_bytes(golden([index]))
        (root / req).write_text('Export connectivity.\nPreserve the owned block topology.\n')
        inputs = [sheet, expected, req]
        scopes[sheet] = root / f'{stem}.scope.json'
        start = len(nodes)
        y = 30.48 + index*30.48
        nodes.append(f'(sheet (at 20.32 {y}) (size 30.48 20.32) (uuid "{uid("sheet"+str(index))}") '
            + prop('Sheetname', stem) + prop('Sheetfile', stem+'.kicad_sch')
            + ''.join(f'(pin "{name}" passive (at 20.32 {y+5.08+5.08*i} 180) '
                f'(effects (font (size 1.27 1.27))) (uuid "{uid(stem+"parent"+name)}"))'
                for i, name in enumerate(('A', 'B'))) + ')')
        for i, name in enumerate(('A', 'B')):
            nodes.append(f'(no_connect (at 20.32 {y+5.08+5.08*i}) '
                f'(uuid "{uid(stem+"binding"+name)}"))')
        harness = f'project/verify{index}.kicad_sch'
        (root / harness).write_text(f'(kicad_sch (version 20250114) (generator "eeschema") '
            f'(uuid "{uid("verify"+str(index))}") (paper "A4") (lib_symbols) {"".join(nodes[start:])})')
        pro = f'project/verify{index}.kicad_pro'
        write_json(root / pro, {'meta': {'version': 1}})
        inputs.extend([harness, pro])
        document = scope(shared + inputs, harness, expected, req)
        document['mutable_inputs'] = [sheet]
        write_json(root / f'{stem}.scope.json', document)
        all_inputs.extend(inputs)
    (project / 'top.kicad_sch').write_text(f'(kicad_sch (version 20250114) (generator "eeschema") '
        f'(uuid "{uid("top")}") (paper "A4") (lib_symbols) {"".join(nodes)})')
    write_json(project / 'top.kicad_pro', {'meta': {'version': 1}})
    (root / 'top.xml').write_bytes(golden([1, 2]))
    (root / 'top.md').write_text('Export connectivity.\nIntegrate only verified blocks.\n')
    top = scope(all_inputs+['project/top.kicad_sch', 'project/top.kicad_pro', 'top.xml', 'top.md'], 'project/top.kicad_sch', 'top.xml', 'top.md')
    top['mutable_inputs'].extend(scopes)
    write_json(root / 'top.scope.json', top)
    return scopes, root / 'top.scope.json'
