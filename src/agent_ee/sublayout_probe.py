"""pcbnew child process for sub-layout reuse (runs inside the sandbox).

``facts`` reports a saved board's footprints, pads, fields and every
board-level item with its geometry in integer nanometres. ``apply`` executes a
plan already computed by ``agent_ee.sublayout``: it moves the planned
footprints, copies the planned sub-board items with the planned UUIDs and nets,
refills only the copied zones, groups each instance and saves a new file.
``fills`` reports the saved fill of named zones and then their refill on the
private copy, which it never saves. It makes no binding decisions of its own;
pcbnew is imported only in this process.
"""
import argparse
import hashlib
import json
import sys

# Board-level classes the reuse can describe and copy. Anything else in a
# sub-board is reported by ``facts`` and refused by the planner.
TRACKS = ('PCB_TRACK', 'PCB_ARC', 'PCB_VIA')
DRAWINGS = ('PCB_SHAPE', 'PCB_TEXT')


def _sha256(path):
    with open(path, 'rb') as stream:
        return hashlib.sha256(stream.read()).hexdigest()


def _xy(v):
    return [int(v.x), int(v.y)]


def _name(layer):
    # Canonical names: a board's user layer names cannot change the binding.
    import pcbnew
    return pcbnew.BOARD.GetStandardLayerName(layer)


def _layers(board, layer_set):
    return [_name(layer) for layer in layer_set.Seq()]


def _track(board, item):
    record = dict(width_nm=int(item.GetWidth()), layers=[_name(item.GetLayer())],
                  points_nm=[_xy(item.GetStart()), _xy(item.GetEnd())])
    if item.GetClass() == 'PCB_ARC':
        record['points_nm'].insert(1, _xy(item.GetMid()))
    elif item.GetClass() == 'PCB_VIA':
        import pcbnew
        record.update(points_nm=[_xy(item.GetPosition())], width_nm=int(item.GetWidth(pcbnew.F_Cu)),
                      drill_nm=int(item.GetDrillValue()), via_type=int(item.GetViaType()),
                      layers=[_name(item.TopLayer()), _name(item.BottomLayer())])
    return record


def _zone(board, zone):
    return dict(layers=_layers(board, zone.GetLayerSet()), rule_area=bool(zone.GetIsRuleArea()),
                points_nm=[_xy(zone.GetCornerPosition(i)) for i in range(zone.GetNumCorners())],
                filled=bool(zone.IsFilled()))


def _drawing(board, item):
    record = dict(layers=[_name(item.GetLayer())])
    if item.GetClass() == 'PCB_TEXT':
        record.update(points_nm=[_xy(item.GetPosition())], text=item.GetText(),
                      angle_deg=round(item.GetTextAngleDegrees(), 6) % 360)
        return record
    points = [_xy(item.GetStart()), _xy(item.GetEnd())]
    if item.GetShapeStr() == 'Arc':
        points.insert(1, _xy(item.GetArcMid()))
    elif item.GetShapeStr() == 'Polygon':
        outline = item.GetPolyShape().COutline(0)
        points = [_xy(outline.CPoint(i)) for i in range(outline.PointCount())]
    record.update(shape=item.GetShapeStr(), points_nm=points, width_nm=int(item.GetWidth()))
    return record


def facts(board):
    import pcbnew
    bbox = board.GetBoardEdgesBoundingBox()
    edges = [d for d in board.GetDrawings() if d.GetLayer() == pcbnew.Edge_Cuts]
    report = dict(pcbnew_version=pcbnew.Version(), copper_layers=board.GetCopperLayerCount(),
                  edge_items=len(edges), enabled_layers=_layers(board, board.GetEnabledLayers()),
                  footprints=[], items=[], groups=[],
                  edge_bbox_nm=[bbox.GetLeft(), bbox.GetTop(), bbox.GetRight(), bbox.GetBottom()])
    for fp in board.GetFootprints():
        box = fp.GetBoundingBox(False)
        report['footprints'].append(dict(
            uuid=fp.m_Uuid.AsString(), reference=fp.GetReference(), fpid=fp.GetFPIDAsString(),
            position_nm=_xy(fp.GetPosition()), orientation_deg=round(fp.GetOrientationDegrees(), 6) % 360,
            layer=_name(fp.GetLayer()), locked=bool(fp.IsLocked()),
            bbox_nm=[box.GetLeft(), box.GetTop(), box.GetRight(), box.GetBottom()],
            pads=[dict(uuid=p.m_Uuid.AsString(), number=p.GetNumber(), net=p.GetNetname(),
                       position_nm=_xy(p.GetPosition()), local_nm=_xy(p.GetFPRelativePosition()))
                  for p in fp.Pads()],
            fields=[dict(name=f.GetName(), position_nm=_xy(f.GetPosition()), visible=bool(f.IsVisible()),
                         angle_deg=round(f.GetTextAngleDegrees(), 6) % 360,
                         layer=_name(f.GetLayer())) for f in fp.GetFields()]))
    for item in [*board.GetTracks(), *board.Zones(), *board.GetDrawings()]:
        cls = item.GetClass()
        if cls in TRACKS:
            record = _track(board, item)
        elif cls == 'ZONE':
            record = _zone(board, item)
        elif cls in DRAWINGS:
            record = _drawing(board, item)
        else:
            record = {}
        net = item.GetNetname() if isinstance(item, pcbnew.BOARD_CONNECTED_ITEM) else ''
        box = item.GetBoundingBox()
        report['items'].append(dict(uuid=item.m_Uuid.AsString(), kind=cls, net=net, supported=bool(record),
                                    copper=bool(item.IsOnCopperLayer()),
                                    bbox_nm=[box.GetLeft(), box.GetTop(), box.GetRight(), box.GetBottom()],
                                    **record))
    for group in board.Groups():
        report['groups'].append(dict(uuid=group.m_Uuid.AsString(), name=group.GetName(),
                                     members=sorted(i.m_Uuid.AsString() for i in group.GetItems())))
    for key in ('footprints', 'items', 'groups'):
        report[key].sort(key=lambda x: x['uuid'])
    return report


def _fill(zone):
    """Every saved fill polygon of a zone, outline then holes, per canonical layer name."""
    import pcbnew
    fill = {}
    for layer in range(pcbnew.PCB_LAYER_ID_COUNT):
        # Any layer, so a fill saved on a layer outside the zone's own is seen too.
        if not zone.HasFilledPolysForLayer(layer):
            continue
        polys = zone.GetFilledPolysList(layer)
        if polys.OutlineCount():
            fill[_name(layer)] = [[[_xy(chain.CPoint(i)) for i in range(chain.PointCount())]
                                   for chain in [polys.Outline(o), *(polys.Hole(o, h) for h in range(polys.HoleCount(o)))]]
                                  for o in range(polys.OutlineCount())]
    return fill


def fills(board, uuids):
    """Saved fills of the named zones, then the same zones refilled as ``apply`` refills them."""
    import pcbnew
    zones = [z for z in board.Zones() if z.m_Uuid.AsString() in set(uuids)]
    report = [dict(uuid=z.m_Uuid.AsString(), saved=_fill(z)) for z in zones]
    for zone in zones:
        zone.UnFill()
    filled = [z for z in zones if not z.GetIsRuleArea()]
    if filled:
        board.BuildConnectivity()
        pcbnew.ZONE_FILLER(board).Fill(filled)
    for entry, zone in zip(report, zones):
        entry['refilled'] = _fill(zone)
    return sorted(report, key=lambda e: e['uuid'])


def _layer(board, name):
    layer = board.GetLayerID(name)
    if layer < 0:
        raise SystemExit('planned layer is absent from the top board: ' + name)
    return layer


def _place(board, fp, spec):
    import pcbnew
    layer = _layer(board, spec['layer'])
    if fp.GetLayer() != layer:
        fp.SetLayerAndFlip(layer)
    fp.SetOrientationDegrees(spec['orientation_deg'])
    fp.SetPosition(pcbnew.VECTOR2I(*spec['position_nm']))
    fields = {f.GetName(): f for f in fp.GetFields()}
    for want in spec['fields']:
        field = fields[want['name']]
        field.SetLayer(_layer(board, want['layer']))
        field.SetTextAngleDegrees(want['angle_deg'])
        field.SetPosition(pcbnew.VECTOR2I(*want['position_nm']))
        field.SetVisible(want['visible'])


def _copy(board, source, spec, origin, target, angle):
    import pcbnew
    cls = source.GetClass()
    clone = (source.Duplicate(False) if cls == 'ZONE' else source.Duplicate()).Cast()
    clone.SetUuidDirect(pcbnew.KIID(spec['uuid']))
    board.Add(clone)
    if isinstance(clone, pcbnew.BOARD_CONNECTED_ITEM):
        net = board.FindNet(spec['net']) if spec['net'] else board.FindNet(0)
        if net is None:
            raise SystemExit('planned top-board net is absent: ' + spec['net'])
        clone.SetNet(net)
    clone.Move(pcbnew.VECTOR2I(target[0] - origin[0], target[1] - origin[1]))
    if angle:
        clone.Rotate(pcbnew.VECTOR2I(*target), pcbnew.EDA_ANGLE(float(angle), pcbnew.DEGREES_T))
    return clone


def apply(board_path, plan, sub_root, out):
    import pcbnew
    if _sha256(board_path) != plan['top_board']['sha256']:
        raise SystemExit('top board differs from the planned input')
    board = pcbnew.LoadBoard(board_path)
    by_ref = {fp.GetReference(): fp for fp in board.GetFootprints()}
    zones = []
    for group in plan['groups']:
        path = sub_root + '/' + group['sub_board']['path']
        if _sha256(path) != group['sub_board']['sha256']:
            raise SystemExit('sub-board differs from the planned input: ' + group['sub_board']['path'])
        sub = pcbnew.LoadBoard(path)
        sources = {i.m_Uuid.AsString(): i for i in [*sub.GetTracks(), *sub.Zones(), *sub.GetDrawings()]}
        pcb_group = pcbnew.PCB_GROUP(board)
        pcb_group.SetName(group['name'])
        pcb_group.SetUuidDirect(pcbnew.KIID(group['uuid']))
        board.Add(pcb_group)
        for spec in group['footprints']:
            fp = by_ref[spec['top_ref']]
            _place(board, fp, spec)
            pcb_group.AddItem(fp)
        for spec in group['copy']:
            clone = _copy(board, sources[spec['source_uuid']], spec, group['sub_anchor_nm'],
                          group['anchor_at_nm'], group['rotation_deg'])
            pcb_group.AddItem(clone)
            if clone.GetClass() == 'ZONE' and not clone.GetIsRuleArea():
                # The sub-board's fill is discarded so that a saved fill proves the refill.
                clone.UnFill()
                zones.append(clone)
        # The sub-board stays alive until exit: its items were duplicated from it.
        _KEEP.append(sub)
    if zones:
        board.BuildConnectivity()
        pcbnew.ZONE_FILLER(board).Fill(zones)
    if not pcbnew.SaveBoard(out, board):
        raise SystemExit('pcbnew failed to save ' + out)


_KEEP = []


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    one = sub.add_parser('facts')
    one.add_argument('board')
    one.add_argument('-o', '--out', required=True)
    two = sub.add_parser('apply')
    two.add_argument('board')
    two.add_argument('--plan', required=True)
    two.add_argument('--sub-boards', required=True, help='directory holding the captured sub-boards')
    two.add_argument('-o', '--out', required=True)
    three = sub.add_parser('fills')
    three.add_argument('board')
    three.add_argument('--zone', action='append', default=[], help='UUID of a zone to refill')
    three.add_argument('-o', '--out', required=True)
    args = parser.parse_args(argv)
    try:
        import pcbnew
    except ImportError as exc:
        print(f'pcbnew bindings unavailable: {exc}', file=sys.stderr)
        return 3
    if args.command == 'apply':
        with open(args.plan, 'rb') as stream:
            apply(args.board, json.load(stream), args.sub_boards, args.out)
        return 0
    board = pcbnew.LoadBoard(args.board)
    report = fills(board, args.zone) if args.command == 'fills' else facts(board)
    with open(args.out, 'x') as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write('\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
