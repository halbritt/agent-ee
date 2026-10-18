"""Author an original two-pad ground link; no external symbol or footprint bytes."""
import json
from pathlib import Path
from uuid import UUID, uuid5

import pcbnew

ROOT = Path(__file__).resolve().parent
NAMESPACE = UUID('8ef190f6-368b-43e6-b533-59a7c4e04990')


def uid(name):
    return str(uuid5(NAMESPACE, name))


def generate(variant):
    target = ROOT / variant
    target.mkdir(exist_ok=True)
    board = pcbnew.BOARD()
    ground = pcbnew.NETINFO_ITEM(board, 'GND')
    board.Add(ground)
    fp = pcbnew.FOOTPRINT(board)
    fp.SetReference('J1')
    fp.SetValue('Owned ground link')
    fp.SetFPID(pcbnew.LIB_ID('Owned', 'GroundLink'))
    fp.SetPath(pcbnew.KIID_PATH('/' + uid('sheet') + '/' + uid('J1')))
    fp.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(10), pcbnew.FromMM(10)))
    for number, x in enumerate((10, 12), 1):
        pad = pcbnew.PAD(fp)
        pad.SetNumber(str(number))
        pad.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
        pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
        pad.SetSize(pcbnew.VECTOR2I(2000000, 2000000))
        pad.SetDrillSize(pcbnew.VECTOR2I(1000000, 1000000))
        pad.SetLayerSet(pcbnew.LSET.AllCuMask())
        pad.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(10)))
        pad.SetNet(ground)
        fp.Add(pad)
    board.Add(fp)
    trace = pcbnew.PCB_TRACK(board)
    trace.SetStart(pcbnew.VECTOR2I(10000000, 10000000))
    trace.SetEnd(pcbnew.VECTOR2I(12000000, 10000000))
    trace.SetWidth(500000)
    trace.SetLayer(pcbnew.F_Cu)
    trace.SetNet(ground)
    board.Add(trace)
    for start, end in (((0, 0), (20, 0)), ((20, 0), (20, 20)), ((20, 20), (0, 20)), ((0, 20), (0, 0))):
        edge = pcbnew.PCB_SHAPE(board)
        edge.SetShape(pcbnew.SHAPE_T_SEGMENT)
        edge.SetStart(pcbnew.VECTOR2I(*(pcbnew.FromMM(v) for v in start)))
        edge.SetEnd(pcbnew.VECTOR2I(*(pcbnew.FromMM(v) for v in end)))
        edge.SetLayer(pcbnew.Edge_Cuts)
        edge.SetWidth(50000)
        board.Add(edge)
    if variant != 'error':
        zone = pcbnew.ZONE(board)
        zone.SetLayer(pcbnew.B_Cu)
        zone.SetNet(ground)
        zone.Outline().NewOutline()
        for x, y in ((1, 1), (19, 1), (19, 19), (1, 19)):
            zone.Outline().Append(pcbnew.FromMM(x), pcbnew.FromMM(y))
        board.Add(zone)
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(str(target / 'gate.kicad_pcb'), board)
    fields = [('Reference', 'J1'), ('Value', 'Owned ground link'),
              ('Footprint', 'Owned:GroundLink'), ('MPN', 'OwnedGroundLink2'),
              ('Manufacturer', 'agent-ee fixture')]
    properties = ''.join(f'(property {json.dumps(k)} {json.dumps(v)} (at 10 10 0) (effects (font (size 1 1))))' for k, v in fields)
    pins = ''.join(f'(pin passive line (at {i*2.54} 0 90) (length 2.54) (name "GND" (effects (font (size 1 1)))) (number "{i+1}" (effects (font (size 1 1)))))' for i in range(2))
    schematic = f'''(kicad_sch (version 20250114) (generator "agent_ee_owned_fixture")
      (uuid "{uid('sheet')}") (paper "A4")
      (lib_symbols (symbol "Owned:GroundLink"
        (property "Reference" "J" (at 0 0 0) (effects (font (size 1 1))))
        (property "Value" "GroundLink" (at 0 0 0) (effects (font (size 1 1))))
        (symbol "GroundLink_1_1" {pins})))
      (symbol (lib_id "Owned:GroundLink") (at 10 10 0) (unit 1)
        (in_bom yes) (on_board yes) (dnp no) (uuid "{uid('J1')}") {properties}
        (instances (project "gate" (path "/{uid('sheet')}" (reference "J1") (unit 1)))))
      (label "GND" (at 10 10 0) (effects (font (size 1 1))) (uuid "{uid('label1')}"))
      (label "GND" (at 12.54 10 0) (effects (font (size 1 1))) (uuid "{uid('label2')}")))'''
    (target / 'gate.kicad_sch').write_text(schematic + '\n')
    (target / 'gate.kicad_pro').write_text('{}\n')


if __name__ == '__main__':
    for variant in ('warning', 'error'):
        generate(variant)
