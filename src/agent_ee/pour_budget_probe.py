"""Native child for the pour budget (AGENTEE-257); all pcbnew imports stay in this process.

Measures, on a refilled copy of a board, what kicad-route's pour_integrity uses as
its area-loss denominator, and the fill one routed through-via of each net class
removes from each pour. The refill is kicad-route's own: kicad-cli pcb drc
--refill-zones --save-board."""
import argparse
from pathlib import Path
import re
import shutil
import subprocess

from .add_planes import SIBLINGS
from .storage import write_json

NM = 1_000_000
# Interior margin beyond the via's own clearance: the probe via's antipad must lie
# wholly inside the pour so the measured loss is one whole antipad.
SLACK_NM = 500_000
# kicad-route (KRT) sizes a via from a custom rule when one exists; pcbnew's Python
# cannot evaluate those rules, so a board with them is refused.
VIA_RULE = re.compile(r'\(\s*constraint\s+(via_diameter|hole_size)\b')


def _copy(source, dest):
    dest.mkdir(parents=True)
    shutil.copyfile(source, dest/source.name)
    for suffix in SIBLINGS:
        sibling = source.with_suffix(suffix)
        if sibling.is_file():
            shutil.copyfile(sibling, dest/sibling.name)
    return dest/source.name


def _refill(board, log):
    """The refill kicad-route grades with (cli.kicad_drc refill_and_save)."""
    rec = subprocess.run(['/usr/bin/kicad-cli', 'pcb', 'drc', '--refill-zones', '--save-board', '--format', 'json',
                          '-o', str(log.with_suffix('.json')), str(board)],
                         capture_output=True, text=True, timeout=1200)
    log.write_text(rec.stdout + rec.stderr)
    if rec.returncode != 0:
        raise ValueError(f'zone refill failed (exit {rec.returncode})')


def _zones(board):
    return sorted((z for z in board.Zones() if not z.GetIsRuleArea() and z.GetNetCode() > 0),
                  key=lambda z: (z.GetNetname(), z.GetZoneName(), -z.GetAssignedPriority()))


def areas(board):
    """Filled area per net@layer, summed exactly as kicad-route's kicad/pour.py sums it."""
    import pcbnew as k
    out = {}
    for zone in _zones(board):
        for layer in zone.GetLayerSet().CuStack():
            if not zone.HasFilledPolysForLayer(layer):
                continue
            fill = zone.GetFilledPolysList(layer)
            pieces = []
            for i in range(fill.OutlineCount()):
                piece = k.SHAPE_POLY_SET()
                piece.AddOutline(fill.COutline(i))
                pieces.append(round(piece.Area() / NM / NM, 4))
            key = f'{zone.GetNetname()}@{board.GetLayerName(layer)}'
            for area in sorted(pieces, reverse=True):
                out[key] = round(out.get(key, 0.0) + area, 4)
    return dict(sorted(out.items()))


def _fill(board, key):
    import pcbnew as k
    merged = k.SHAPE_POLY_SET()
    for zone in _zones(board):
        for layer in zone.GetLayerSet().CuStack():
            if f'{zone.GetNetname()}@{board.GetLayerName(layer)}' == key and zone.HasFilledPolysForLayer(layer):
                merged.BooleanAdd(zone.GetFilledPolysList(layer))
    return merged


def _outside(board):
    """Footprints whose anchor is not inside the board outline: the board is not placed."""
    import pcbnew as k
    outline = k.SHAPE_POLY_SET()
    if not board.GetBoardPolygonOutlines(outline, False) or outline.OutlineCount() == 0:
        raise ValueError('one explicit closed board outline required')
    return sorted(fp.GetReference() for fp in board.GetFootprints()
                  if not outline.Contains(fp.GetPosition()))


def _via_class(board, name):
    nc = board.GetDesignSettings().m_NetSettings.GetEffectiveNetClass(name)
    return dict(net=name, netclass=nc.GetName(), via_diameter_mm=nc.GetViaDiameter() / NM,
                via_drill_mm=nc.GetViaDrill() / NM, clearance_mm=nc.GetClearance() / NM)


def _classes(board, zone_nets):
    """One representative routed net per net class: nets of two or more pads outside the pours' nets."""
    pads = {}
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetCode() > 0:
                pads[pad.GetNetname()] = pads.get(pad.GetNetname(), 0) + 1
    classes = {}
    for name in sorted(n for n, count in pads.items() if count >= 2 and n not in zone_nets):
        cls = _via_class(board, name)
        classes.setdefault(cls['netclass'], cls)
    return [classes[n] for n in sorted(classes)]


def _probe(base, work, key, cls, max_clearance):
    """Add one through-via of the class at an interior point of the pour, refill, measure."""
    import pcbnew as k
    path = _copy(base, work)
    board = k.LoadBoard(str(path))
    before = areas(board)
    fill = _fill(board, key)
    diameter = int(round(cls['via_diameter_mm'] * NM))
    margin = diameter // 2 + max_clearance + SLACK_NM
    interior = k.SHAPE_POLY_SET(fill)
    interior.Deflate(margin, k.CORNER_STRATEGY_ROUND_ALL_CORNERS, int(board.GetDesignSettings().m_MaxError))
    if interior.OutlineCount() == 0:
        return dict(key=key, **cls, status='unknown', reason='no pour interior wide enough for a whole antipad')
    largest = max(range(interior.OutlineCount()), key=lambda i: interior.COutline(i).Area())
    point = min(_points(interior.COutline(largest)), key=lambda p: (p.y, p.x))
    via = k.PCB_VIA(board)
    via.SetViaType(k.VIATYPE_THROUGH)
    via.SetLayerPair(k.F_Cu, k.B_Cu)
    via.SetPosition(k.VECTOR2I(point.x, point.y))
    via.SetWidth(diameter)
    via.SetDrill(int(round(cls['via_drill_mm'] * NM)))
    via.SetNet(board.FindNet(cls['net']))
    # A via without tracks takes the net of the copper it sits in when KiCad refills;
    # a routed via keeps its net through its tracks. Free keeps the probe's net.
    via.SetIsFree(True)
    board.Add(via)
    layer = board.GetLayerID(key.rsplit('@', 1)[1])
    keepout = k.SHAPE_POLY_SET()
    via.TransformShapeToPolygon(keepout, layer, margin - diameter // 2, int(board.GetDesignSettings().m_MaxError),
                                k.ERROR_OUTSIDE)
    keepout.BooleanSubtract(fill)
    if keepout.Area() > 0:
        return dict(key=key, **cls, status='unknown', reason='the probe antipad is not wholly inside the pour')
    if not k.SaveBoard(str(path), board):
        raise ValueError('probe board save failed')
    _refill(path, work/'refill.log')
    refilled = k.LoadBoard(str(path))
    vias = [v for v in refilled.GetTracks() if isinstance(v, k.PCB_VIA)]
    if len(vias) != 1 or vias[0].GetNetname() != cls['net']:
        return dict(key=key, **cls, status='unknown', reason='the probe via did not keep its net through the refill')
    after = areas(refilled)
    if set(after) != set(before):
        return dict(key=key, **cls, status='unknown', reason='the probe via changed which pours are filled')
    return dict(key=key, **cls, status='measured', position_mm=[point.x / NM, point.y / NM],
                flashed=[board.GetLayerName(l) for l in via.GetLayerSet().CuStack() if via.FlashLayer(l)],
                loss_mm2={n: round(before[n] - after[n], 4) for n in before})


def _points(chain):
    return [chain.CPoint(i) for i in range(chain.PointCount())]


def measure(board_path, out):
    import pcbnew as k
    dru = board_path.with_suffix('.kicad_dru')
    if dru.is_file() and VIA_RULE.search(dru.read_text(errors='replace')):
        raise ValueError('custom via size rules are not evaluated; the per-via loss would be unknown')
    work = out.parent
    base = _copy(board_path, work/'base')
    _refill(base, work/'base-refill.log')
    board = k.LoadBoard(str(base))
    measured = areas(board)
    zones = _zones(board)
    zone_nets = {z.GetNetname() for z in zones}
    clearance = max([z.GetLocalClearance() or 0 for z in zones] +
                    [int(round(c['clearance_mm'] * NM)) for c in _classes(board, zone_nets)] +
                    [board.GetDesignSettings().m_MinClearance])
    classes = _classes(board, zone_nets)
    probes = [_probe(base, work/f'probe-{i}-{j}', key, cls, clearance)
              for i, key in enumerate(measured) for j, cls in enumerate(classes)]
    # A via of the pour's own net (fanout, stitching) joins the pour: it must remove nothing.
    own = [_probe(base, work/f'own-{i}', key, _via_class(board, key.rsplit('@', 1)[0]), clearance)
           for i, key in enumerate(measured)]
    write_json(out, dict(pcbnew_version=k.Version(), outside_outline=_outside(board), pour_area_mm2=measured,
                         net_classes=classes, probes=probes, own_net_probes=own))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args(argv)
    measure(args.board, args.output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
