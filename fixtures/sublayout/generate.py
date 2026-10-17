"""Owned top board for sub-layout reuse (AGENTEE-56); needs native pcbnew 10.

Three unplaced instances of the owned regulator module wait in a staging row
right of a 140 x 40 mm outline; one locked header is already placed. The
module's proven layout is fixtures/generation/regulator.kicad_pcb.

    python3 fixtures/sublayout/generate.py fixtures/sublayout/top.kicad_pcb
"""
import argparse
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIBRARY = HERE.parent / 'generation' / 'project' / 'lib' / 'OwnedFP.pretty'
TEMPLATE = HERE.parent / 'generation' / 'regulator.kicad_pcb'
MM = 1000000
# Instance C leaves the input pins unconnected: its VIN copper has no top net.
INSTANCES = {'A': {'1': '/A/VIN', '3': '/A/VOUT'}, 'B': {'1': '/B/VIN', '3': '/B/VOUT'},
             'C': {'1': None, '3': '/C/VOUT'}}


def make_board(out):
    import pcbnew as p
    p.KIID.SeedGenerator(56)
    board = p.LoadBoard(str(TEMPLATE))
    board.RemoveAll()
    for (x0, y0), (x1, y1) in (((100, 100), (240, 100)), ((240, 100), (240, 140)),
                               ((240, 140), (100, 140)), ((100, 140), (100, 100))):
        edge = p.PCB_SHAPE(board)
        edge.SetShape(p.SHAPE_T_SEGMENT)
        edge.SetLayer(p.Edge_Cuts)
        edge.SetWidth(50000)
        edge.SetStart(p.VECTOR2I(x0 * MM, y0 * MM))
        edge.SetEnd(p.VECTOR2I(x1 * MM, y1 * MM))
        board.Add(edge)
    nets = {}
    def net(name):
        if name not in nets:
            nets[name] = p.NETINFO_ITEM(board, name)
            board.Add(nets[name])
        return nets[name]
    def place(name, ref, xy, pads, locked=False):
        fp = p.FootprintLoad(str(LIBRARY), name)
        fp.SetFPID(p.LIB_ID('OwnedFP', name))
        fp.SetReference(ref)
        fp.SetPosition(p.VECTOR2I(int(xy[0] * MM), int(xy[1] * MM)))
        for pad in fp.Pads():
            if pads.get(pad.GetNumber()):
                pad.SetNet(net(pads[pad.GetNumber()]))
        fp.SetLocked(locked)
        board.Add(fp)
    x = 250
    for index, (instance, pins) in enumerate(INSTANCES.items(), start=1):
        place('CONN_1x3', f'J{index}', (x, 105), {'1': pins['1'], '2': 'GND', '3': pins['3']})
        place('REG_4PAD', f'U{index}', (x + 8, 107), {'1': pins['1'], '2': 'GND', '3': pins['3']})
        place('CAP_2PAD', f'C{index}', (x + 16, 107), {'1': pins['3'], '2': 'GND'})
        x += 25
    place('CONN_1x3', 'J9', (232, 105), {'1': 'GND', '2': '/A/VOUT', '3': '/B/VOUT'}, locked=True)
    if not p.SaveBoard(str(out), board):
        raise SystemExit(f'pcbnew failed to save {out}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out')
    make_board(Path(parser.parse_args().out))
