"""Original owned boards; requires the qualified native pcbnew installation."""
import argparse
from pathlib import Path


def make_board(path, kind):
    import pcbnew as p
    b = p.BOARD()
    net = p.NETINFO_ITEM(b, 'PATH')
    b.Add(net)
    def vec(xy):
        return p.VECTOR2I(*(round((v + 30) * 1000000) for v in xy))
    start, end = (0, 0), (10, 0)
    segments = [(start, end)]
    if kind == 'series':
        segments = [(start, (5, 0)), ((5, 0), end)]
    elif kind == 'parallel':
        end = (7, 1)
        segments = [(start, (3, 4)), ((3, 4), end), (start, (4, -3)), ((4, -3), end)]
    elif kind == 'long':
        end = (20, 0)
        segments = [(start, end)]
    for name, xy in [('SOURCE', start), ('SINK', end)]:
        fp = p.FOOTPRINT(b)
        fp.SetReference(name)
        fp.SetPosition(vec(xy))
        pad = p.PAD(fp)
        pad.SetNumber('1')
        pad.SetAttribute(p.PAD_ATTRIB_SMD)
        pad.SetShape(p.PAD_SHAPE_CIRCLE)
        pad.SetSize(p.VECTOR2I(100000, 100000))
        layers = p.LSET()
        layers.AddLayer(p.F_Cu)
        pad.SetLayerSet(layers)
        pad.SetPosition(vec(xy))
        pad.SetNet(net)
        fp.Add(pad)
        b.Add(fp)
    for a, z in segments:
        track = p.PCB_ARC(b) if kind == 'arc' else p.PCB_TRACK(b)
        track.SetStart(vec(a))
        track.SetEnd(vec(z))
        if kind == 'arc':
            track.SetMid(vec((5, 5)))
        track.SetWidth(125000 if kind == 'narrow' else 250000)
        track.SetLayer(p.F_Cu)
        track.SetNet(net)
        b.Add(track)
    p.SaveBoard(str(path), b)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    for kind in ('straight', 'series', 'parallel', 'arc', 'narrow', 'long'):
        make_board(args.directory / (kind + '.kicad_pcb'), kind)
