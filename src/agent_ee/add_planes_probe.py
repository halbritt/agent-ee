"""Native child for planned zones; all pcbnew imports stay in this process."""
import argparse
from pathlib import Path
import shutil

from .add_planes import SIBLINGS, validate_plan, zone_ids
from .catalog import strict_json
from .storage import write_json
from .plane_polygon_identity import polygon_digest

_REMOVED = []


def _outline(board, spec):
    import pcbnew as k
    poly = k.SHAPE_POLY_SET()
    if spec == 'board':
        if not board.GetBoardPolygonOutlines(poly, False) or poly.OutlineCount() != 1:
            raise ValueError('one explicit closed board outline required; inferred outlines are forbidden')
    else:
        poly.NewOutline()
        for x, y in spec['polygon_mm']:
            poly.Append(k.FromMM(x), k.FromMM(y))
        if poly.IsSelfIntersecting():
            raise ValueError('plane polygon self-intersects')
        if poly.Area() <= 0:
            raise ValueError('plane polygon has zero area')
    return poly


def _zone(board, spec, uid):
    import pcbnew as k
    net = board.FindNet(spec['net'])
    if net is None:
        raise ValueError('planned plane net is missing: ' + spec['net'])
    layer = board.GetLayerID(spec['layer'])
    if layer < 0 or not board.IsLayerEnabled(layer):
        raise ValueError('planned plane layer is disabled: ' + spec['layer'])
    zone = k.ZONE(board)
    zone.SetUuidDirect(k.KIID(uid))
    zone.SetZoneName('agent-ee-plane/' + uid)
    zone.SetNet(net)
    zone.SetLayer(layer)
    zone.SetLocalClearance(k.FromMM(spec['clearance_mm']))
    zone.SetMinThickness(k.FromMM(spec['min_width_mm']))
    zone.SetPadConnection({'solid':k.ZONE_CONNECTION_FULL,'thermal':k.ZONE_CONNECTION_THERMAL,
                           'tht_thermal':k.ZONE_CONNECTION_THT_THERMAL}[spec['thermal']['connection']])
    zone.SetThermalReliefGap(k.FromMM(spec['thermal']['gap_mm']))
    zone.SetThermalReliefSpokeWidth(k.FromMM(spec['thermal']['spoke_width_mm']))
    zone.SetAssignedPriority(spec['priority'])
    zone.SetIslandRemovalMode(k.ISLAND_REMOVAL_MODE_ALWAYS if spec['island_removal']=='always' else k.ISLAND_REMOVAL_MODE_NEVER)
    zone.Outline().Append(_outline(board, spec['outline']))
    return zone


def _inventory(board, plan):
    expected = dict(zip(zone_ids(plan), plan['zones']))
    owned = list(board.Zones())
    for fp in board.GetFootprints():
        if any(not z.GetIsRuleArea() for z in fp.Zones()):
            raise ValueError('unplanned footprint-owned copper zone')
    seen = set()
    for zone in owned:
        if zone.GetIsRuleArea():
            continue
        uid = zone.m_Uuid.AsString()
        if (uid in seen or uid not in expected or zone.GetZoneName() != 'agent-ee-plane/' + uid or
                zone.GetNetname() != expected[uid]['net'] or
                list(zone.GetLayerSet().Seq()) != [board.GetLayerID(expected[uid]['layer'])]):
            raise ValueError('unplanned copper zone')
        seen.add(uid)
    return [z for z in owned if not z.GetIsRuleArea()]


def _poly(poly):
    return [[[(chain.CPoint(i).x, chain.CPoint(i).y) for i in range(chain.PointCount())]
             for chain in [poly.Outline(n), *(poly.Hole(n,h) for h in range(poly.HoleCount(n)))]]
            for n in range(poly.OutlineCount())]


def inspect(board, plan):
    import pcbnew as k
    zones = _inventory(board, plan)
    by_id = {z.m_Uuid.AsString():z for z in zones}
    if set(by_id) != set(zone_ids(plan)):
        raise ValueError('planned zone is missing after save')
    facts = []
    for uid, spec in zip(zone_ids(plan), plan['zones']):
        actual, expected = by_id[uid], _zone(board, spec, uid)
        getters = ('GetLocalClearance','GetMinThickness','GetPadConnection','GetThermalReliefGap',
                   'GetThermalReliefSpokeWidth','GetAssignedPriority','GetIslandRemovalMode')
        if any(getattr(actual,g)() != getattr(expected,g)() for g in getters) or _poly(actual.Outline()) != _poly(expected.Outline()):
            raise ValueError('saved plane settings or outline differ from plan')
        fill = actual.GetFilledPolysList(actual.GetLayer())
        if not actual.IsFilled() or fill.OutlineCount() == 0 or fill.Area() <= 0:
            raise ValueError('planned plane has no native filled copper')
        facts.append(dict(uuid=uid, net=actual.GetNetname(), layer=board.GetLayerName(actual.GetLayer()),
                          filled_area_mm2=fill.Area()/1e12, fill_polygons=fill.OutlineCount(),
                          polygon_evidence=_polygon_evidence(k, board, actual)))
    return dict(pcbnew_version=k.Version(), zones=facts)


def _chain_mm(k, chain):
    return [[k.ToMM(chain.CPoint(n).x), k.ToMM(chain.CPoint(n).y)] for n in range(chain.PointCount())]


def _polygon_evidence(k, board, zone):
    """AGENTEE-487 H2: one record per filled polygon of the SAVED (reloaded) zone fill: index, full identity
    (outline and holes, digest of their exact nm vertices) and isolation from KiCad's native shape collision
    (SHAPE_POLY_SET.Collide(effective shape, 0) against every same-net pad, track, arc and via on the layer)."""
    layer, net = zone.GetLayer(), zone.GetNetCode()
    fill = zone.GetFilledPolysList(layer)
    shapes = [p.GetEffectiveShape(layer) for fp in board.GetFootprints() for p in fp.Pads()
              if p.GetNetCode() == net and p.IsOnLayer(layer)]
    shapes += [t.GetEffectiveShape(layer) for t in board.GetTracks() if t.GetNetCode() == net and t.IsOnLayer(layer)]
    records = []
    for i in range(fill.OutlineCount()):
        unit = fill.UnitSet(i)
        outline = fill.COutline(i)
        holes = [fill.CHole(i, h) for h in range(fill.HoleCount(i))]
        nm = [[[outline.CPoint(n).x, outline.CPoint(n).y] for n in range(outline.PointCount())],
              *[[[c.CPoint(n).x, c.CPoint(n).y] for n in range(c.PointCount())] for c in holes]]
        records.append(dict(index=i, outline_mm=_chain_mm(k, outline), holes_mm=[_chain_mm(k, c) for c in holes],
                            digest=polygon_digest(nm),
                            isolated=not any(bool(unit.Collide(shape, 0)) for shape in shapes)))
    return records


def apply(board_path, plan, output):
    import pcbnew as k
    private = output.parent/'native-project'
    private.mkdir()
    copied = private/board_path.name
    shutil.copyfile(board_path, copied)
    saved = {}
    for suffix in SIBLINGS:
        source = board_path.with_suffix(suffix)
        saved[suffix] = source.read_bytes() if source.is_file() else None
        if saved[suffix] is not None:
            copied.with_suffix(suffix).write_bytes(saved[suffix])
    try:
        board = k.LoadBoard(str(copied))
        existing = _inventory(board, plan)
        planned = [_zone(board, spec, uid) for uid,spec in zip(zone_ids(plan),plan['zones'])]
        for zone in existing:
            _REMOVED.append(zone)
            board.Remove(zone)
        for zone in planned:
            board.Add(zone)
        board.BuildConnectivity()
        if not k.ZONE_FILLER(board).Fill(planned):
            raise ValueError('native zone fill failed')
        if not k.SaveBoard(str(copied), board):
            raise ValueError('native plane save failed')
        shutil.copyfile(copied, output)
    finally:
        for suffix, original in saved.items():
            sibling = copied.with_suffix(suffix)
            if original is None:
                sibling.unlink(missing_ok=True)
            else:
                sibling.write_bytes(original)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('apply','inspect'))
    parser.add_argument('board', type=Path)
    parser.add_argument('plan', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args(argv)
    plan = validate_plan(strict_json(args.plan.read_bytes()))
    if args.command == 'apply':
        apply(args.board, plan, args.output)
    else:
        import pcbnew as k
        import hashlib
        facts = inspect(k.LoadBoard(str(args.board)), plan)
        facts['board_sha256'] = hashlib.sha256(args.board.read_bytes()).hexdigest()  # the saved board inspected
        write_json(args.output, facts)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
