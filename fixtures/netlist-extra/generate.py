"""Original native connectivity fixture; symbol geometry is authored here."""
from uuid import NAMESPACE_URL, uuid5


def uid(name):
    return str(uuid5(NAMESPACE_URL, 'agent-ee/netlist-extra/' + name))


def prop(name, value):
    return f'(property "{name}" "{value}" (at 0 0 0) (effects (font (size 1 1)) (hide yes)))'


def schematic(project='topology', variant='control', child=None):
    root_uuid = uid('root' if child is None else child)
    symbols = ['Owned:R', 'Owned:Switch', 'Unknown:R']
    definitions = []
    for symbol in symbols:
        name = symbol.split(':')[1]
        pins = ''.join(f'(pin passive line (at 0 {-2.54*i} 0) (length 2.54) '
            f'(name "{i+1}" (effects (font (size 1 1)))) (number "{i+1}" (effects (font (size 1 1)))))'
            for i in range(2))
        definitions.append(f'(symbol "{symbol}" (pin_names (offset 0)) (in_bom yes) (on_board yes) '
            + prop('Reference', 'X') + prop('Value', name) + f'(symbol "{name}_1_1" {pins}))')
    specs = [('R1','Owned:R',['+3V3','MID']), ('R2','Owned:R',['MID','GND']),
             ('SW1','Owned:Switch',['A','B']), ('RDEBUG','Owned:R',['A','B'])]
    if child:
        specs = [('RA' if child=='a' else 'RB','Owned:R',['SDA','RETURN'])]
    placed = []
    for index, (ref, symbol, labels) in enumerate(specs):
        if variant == 'passive-short' and ref == 'R1':
            labels = ['+3V3','+3V3']
        if variant == 'switch-short' and ref == 'SW1':
            labels = ['GND','GND']
        if variant == 'rail-merge':
            labels = ['GND' if n=='+3V3' else n for n in labels]
        if variant == 'unknown' and ref == 'R2':
            symbol = 'Unknown:R'
        x, y = 40 + 25*index, 50
        path = f'/{uid("root")}' + (f'/{uid("sheet-"+child)}' if child else '')
        pins = ''.join(f'(pin "{i+1}" (uuid "{uid(ref+str(i))}"))' for i in range(2))
        placed.append(f'(symbol (lib_id "{symbol}") (at {x} {y} 0) (unit 1) '
            f'(in_bom yes) (on_board yes) (dnp {"yes" if ref=="RDEBUG" else "no"}) (uuid "{uid(ref)}")'
            + prop('Reference',ref) + prop('Value','10k' if symbol.endswith(':R') else 'switch') + pins
            + f'(instances (project "{project}" (path "{path}" (reference "{ref}") (unit 1)))))')
        for i, label in enumerate(labels):
            placed.append(f'(label "{label}" (at {x} {y+2.54*i} 0) '
                f'(effects (font (size 1 1)) (justify left bottom)) (uuid "{uid(ref+label+str(i))}"))')
    if child is None:
        for i, name in enumerate(('a','b')):
            placed.append(f'(sheet (at {50+60*i} 100) (size 30 20) (stroke (width 0) (type default)) '
                f'(fill (color 0 0 0 0)) (uuid "{uid("sheet-"+name)}")'
                + prop('Sheetname',name) + prop('Sheetfile',name+'.kicad_sch')
                + f'(instances (project "{project}" (path "/{root_uuid}" (page "{i+2}")))))')
    return f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "{root_uuid}") (paper "A4") (lib_symbols {"".join(definitions)}) {"".join(placed)})\n'


def policy():
    return {'schema':'agent-ee.netlist-extra-policy/1',
        'requirement':{'id':'topology','source':{'path':'requirements.md','locator':'Topology'}},
        'classes':{'Owned:R':{'class':'resistor','pins':['1','2']},
                   'Owned:Switch':{'class':'switch','pins':['1','2']}},
        'overrides':[], 'exemptions':[{'component':{'sheet':'/','uuid':uid('RDEBUG')},
            'symbol':'Owned:R','reason':'DNP debug network intentionally outside no-self-short requirement'}],
        'rail_pairs':[{'id':'supply-ground','endpoints':[
            {'sheet':'/','uuid':uid('R1'),'pin':'1'}, {'sheet':'/','uuid':uid('R2'),'pin':'2'}]}]}
