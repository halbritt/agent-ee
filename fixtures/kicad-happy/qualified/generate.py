"""Generate original, deliberately simplified adapter qualification designs.

These symbols/pads are authored here; they are neither manufacturer designs nor
physical component models. Run with system Python (pcbnew).
"""
import json
from pathlib import Path
from uuid import UUID, uuid5
import pcbnew

ROOT = Path(__file__).resolve().parent
NS = UUID('66073869-59ad-41e4-9428-c24401f642c4')
def uid(s):
    return str(uuid5(NS, s))
def q(s):
    return json.dumps(s)
def generate(variant):
    target = ROOT / variant
    target.mkdir(exist_ok=True)
    root_id = uid('sheet')
    # ref, library part, value, footprint name, [(pin name, electrical type, net)]
    parts = [
        ('U1', 'Regulator_Linear:LM317', 'LM317', 'Fixture:SOT-223', [('VIN','power_in','+12V'), ('VOUT','power_out','+5V'), ('ADJ','input','FB')]),
        ('U2', 'Fixture:Load', 'Load', 'Fixture:Load', [('VCC','power_in','+5V'), ('GND','passive','GND')]),
        ('R1', 'Device:R', '1000' if variant == 'feedback' else '720', 'Fixture:R_0805', [('~','passive','+5V'), ('~','passive','FB')]),
        ('R2', 'Device:R', '240', 'Fixture:R_0805', [('~','passive','FB'), ('~','passive','GND')]),
        ('C1', 'Device:C', '10u/6V X7R' if variant == 'derating' else '10u/16V X7R', 'Fixture:C_0805', [('~','passive','+5V'), ('~','passive','GND')]),
        ('D1', 'Device:D_TVS', 'D_TVS', 'Fixture:D', [('K','passive','+5V'), ('A','passive','GND')]),
        ('RV1', 'Device:Varistor', 'Varistor', 'Fixture:RV', [('1','passive','+5V'), ('2','passive','GND')]),
        ('J1', 'Connector:USB_B', 'USB_B', 'Fixture:USB', [('VBUS','passive','+5V'), ('GND','passive','GND')]),
        ('J2', 'Connector:Power', 'Power', 'Fixture:Conn', [('VIN','power_out','+12V'), ('GND','power_out','GND')]),
    ]
    if variant == 'protection':
        parts = [p for p in parts if p[0] != 'D1']
    if variant == 'power':
        parts = [(a,b,c,d,[(n,t,'+12V' if a=='U2' and n=='VCC' else net) for n,t,net in pins]) for a,b,c,d,pins in parts]
    defs = {}
    instances, labels = [], []
    board = pcbnew.BOARD()
    netmap = {}
    for net in ('+12V','+5V','FB','GND'):
        item = pcbnew.NETINFO_ITEM(board,net); board.Add(item); netmap[net]=item
    for index,(ref,lib,value,footprint,pins) in enumerate(parts):
        sx,sy=30+index*20,40
        name=lib.split(':')[1]
        # Pin numbers have one fixed, original geometry per symbol class.
        pintext=''.join(f'(pin {typ} line (at 0 {-i*2.54} 0) (length 2.54) (name {q(n)} (effects (font (size 1 1)))) (number {q(str(i+1))} (effects (font (size 1 1)))))' for i,(n,typ,net) in enumerate(pins))
        defs[lib]=f'(symbol {q(lib)} (property "Reference" {q(ref[0])} (at 0 0 0) (effects (font (size 1 1)))) (property "Value" {q(name)} (at 0 0 0) (effects (font (size 1 1)))) (symbol {q(name+"_1_1")} {pintext}))'
        symbol_id=uid(ref)
        instances.append(f'(symbol (lib_id {q(lib)}) (at {sx} {sy} 0) (unit 1) (in_bom yes) (on_board yes) (dnp no) (uuid {q(symbol_id)}) (property "Reference" {q(ref)} (at {sx} {sy-4} 0) (effects (font (size 1 1)))) (property "Value" {q(value)} (at {sx} {sy-2} 0) (effects (font (size 1 1)))) (property "Footprint" {q(footprint)} (at {sx} {sy} 0) (effects (font (size 1 1)) (hide yes))) (instances (project "qualification" (path {q("/"+root_id)} (reference {q(ref)}) (unit 1)))))')
        for i,(_,_,net) in enumerate(pins):
            labels.append(f'(label {q(net)} (at {sx} {sy+i*2.54} 0) (effects (font (size 1 1))) (uuid {q(uid(ref+str(i)))}) )')
        fp=pcbnew.FOOTPRINT(board); fp.SetReference(ref); fp.SetValue(value); fp.SetFPID(pcbnew.LIB_ID('Fixture',footprint.split(':')[1])); fp.SetPath(pcbnew.KIID_PATH('/'+root_id+'/'+symbol_id))
        x,y=30+index*10,30
        if ref=='RV1': x,y=(70 if variant=='emc' else 52),30
        if ref=='J1': x,y=50,30
        fp.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x),pcbnew.FromMM(y)))
        for i,(_,_,net) in enumerate(pins):
            pad=pcbnew.PAD(fp); pad.SetNumber(str(i+1)); pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD); pad.SetShape(pcbnew.PAD_SHAPE_RECT); pad.SetSize(pcbnew.VECTOR2I(1000000,1000000)); layers=pcbnew.LSET(); layers.AddLayer(pcbnew.F_Cu); layers.AddLayer(pcbnew.F_Paste); layers.AddLayer(pcbnew.F_Mask); pad.SetLayerSet(layers); pad.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x),pcbnew.FromMM(y+i*2))); pad.SetNet(netmap[net]); fp.Add(pad)
        board.Add(fp)
    sch=f'(kicad_sch (version 20250114) (generator "agent_ee_owned_fixture") (uuid {q(root_id)}) (paper "A4") (lib_symbols {"".join(defs.values())}) {"".join(labels)} {"".join(instances)})'
    (target/'qualification.kicad_sch').write_text(sch+'\n')
    pcbnew.SaveBoard(str(target/'qualification.kicad_pcb'),board)
    (target/'qualification.kicad_pro').write_text('{}\n')
if __name__=='__main__':
    for variant in ('control','feedback','protection','derating','power','emc'):
        generate(variant)
