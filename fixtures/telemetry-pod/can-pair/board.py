"""Original native CAN two-terminal fixture; run only in an isolated child."""

import sys, pcbnew as k
b = k.BOARD()
point = lambda x, y: k.VECTOR2I(k.FromMM(x), k.FromMM(y))
for start, end in [((40, 40), (60, 40)), ((60, 40), (60, 60)),
                   ((60, 60), (40, 60)), ((40, 60), (40, 40))]:
    edge = k.PCB_SHAPE(b)
    edge.SetShape(k.SHAPE_T_SEGMENT)
    edge.SetLayer(k.Edge_Cuts)
    edge.SetStart(point(*start))
    edge.SetEnd(point(*end))
    edge.SetWidth(k.FromMM(.05))
    b.Add(edge)
nets = []
for name in ('CAN+', 'CAN-'):
    net = k.NETINFO_ITEM(b, name)
    b.Add(net)
    nets.append(net)
for reference, x in (('J1', 45), ('J2', 55)):
    fp = k.FOOTPRINT(b)
    b.Add(fp)
    fp.SetReference(reference)
    fp.SetPosition(point(x, 50))
    for number, net in enumerate(nets, 1):
        pad = k.PAD(fp)
        fp.Add(pad)
        pad.SetNumber(str(number))
        pad.SetNet(net)
        pad.SetAttribute(k.PAD_ATTRIB_SMD)
        pad.SetShape(k.PAD_SHAPE_RECT)
        layers = k.LSET()
        layers.AddLayer(k.F_Cu)
        pad.SetLayerSet(layers)
        pad.SetSize(point(.25, .25))
        pad.SetPosition(point(x, 50 + (number - 1) * .6))
k.SaveBoard(sys.argv[1], b)
