"""Extract saved-board geometry facts through pcbnew (runs inside the sandbox).

Judgment-free: emits what the saved .kicad_pcb actually contains (footprints
with DNP/BOM/position/board-only attributes, lib id, orientation and exact nm
anchor; pads with size/drill/position/net and their native board-space bounding
box in integer nm, overall and per flashed copper layer; track segments, vias,
zones, per-layer zone fill outline and netted copper shape counts, nets,
unconnected count).
agent_ee.checks applies declared criteria.
UUIDs use KIID.AsString() (str(KIID) is only a SWIG proxy repr).
`--dfm` adds a `dfm` section of exact native facts for agent_ee.dfm_geometry:
pad shapes and copper polygons, via types, arc midpoints, courtyards, the saved
board thickness and per-layer united copper area. The base report is unchanged.
"""

import argparse
import json
from pathlib import Path
import sys


def load_readonly_board(board_path):
    """Read board bytes and load a pcbnew board once for read-only inspection."""
    import pcbnew
    path = Path(board_path)
    before = path.read_bytes()
    board = pcbnew.LoadBoard(str(path))
    if board is None:
        raise ValueError('native board load failed')
    return board, before


def verify_unchanged(board_path, before, label='geometry probe'):
    if Path(board_path).read_bytes() != before:
        raise ValueError(f'saved board changed during {label}')


def mm(value):
    import pcbnew
    return round(pcbnew.ToMM(int(value)), 4)


def vec_mm(vec):
    return [mm(vec.x), mm(vec.y)]


def pad_vec(pad, getter):
    """Pad size/drill accessors grew a padstack layer argument in KiCad 9+."""
    import pcbnew
    try:
        return getter()
    except TypeError:
        return getter(pcbnew.PADSTACK.ALL_LAYERS)


def pad_attribute(pad):
    import pcbnew
    value = pad.GetAttribute()
    for name in ("PAD_ATTRIB_PTH", "PAD_ATTRIB_SMD", "PAD_ATTRIB_CONN", "PAD_ATTRIB_NPTH"):
        if hasattr(pcbnew, name) and getattr(pcbnew, name) == value:
            return name.replace("PAD_ATTRIB_", "")
    return int(value)


def box_nm(box):
    """Native BOX2I as integer nm [left, top, right, bottom] (no rounding)."""
    return [int(box.GetX()), int(box.GetY()), int(box.GetRight()), int(box.GetBottom())]


def extract(board_path, *, board=None):
    import pcbnew
    from .placement_probe import primitive, prove_contours
    before = None
    if board is None:
        board, before = load_readonly_board(board_path)
    report = {"pcbnew_version": pcbnew.Version(), "board": board_path}
    copper = list(board.GetEnabledLayers().CuStack())
    report['copper_layers'] = [board.GetLayerName(layer) for layer in copper]
    outline_shapes = [s for s in board.GetDrawings() if s.GetLayer() == pcbnew.Edge_Cuts]

    nets = {}
    footprints, pads = [], []
    for fp in board.GetFootprints():
        outline_shapes.extend(s for s in list(fp.GraphicalItems()) + list(fp.GetFields())
                              if s.GetLayer() == pcbnew.Edge_Cuts)
        entry = {
            "ref": fp.GetReference(), "value": fp.GetValue(),
            "layer": board.GetLayerName(fp.GetLayer()),
            "dnp": bool(fp.IsDNP()),
            "exclude_from_bom": bool(fp.IsExcludedFromBOM()),
            "exclude_from_pos_files": bool(fp.IsExcludedFromPosFiles()),
            "board_only": bool(fp.IsBoardOnly()),
            "uuid": fp.m_Uuid.AsString(),
            "position_mm": vec_mm(fp.GetPosition()),
            "position_nm": [int(fp.GetPosition().x), int(fp.GetPosition().y)],
            "orientation_deg": float(fp.GetOrientationDegrees()),
            "fpid": fp.GetFPIDAsString(),
            "pads": [],
        }
        for pad in fp.Pads():
            size = pad_vec(pad, pad.GetSize)
            drill = pad_vec(pad, pad.GetDrillSize)
            net = pad.GetNetname()
            record = {
                "ref": fp.GetReference(),
                "pad": pad.GetNumber(),
                "net": net,
                "attribute": pad_attribute(pad),
                "position_mm": vec_mm(pad.GetPosition()),
                "size_mm": vec_mm(size),
                "drill_mm": vec_mm(drill),
                "drill_nm": [int(drill.x), int(drill.y)],
                "flash_layers": [board.GetLayerName(layer) for layer in copper if pad.FlashLayer(layer)],
                "bbox_nm": box_nm(pad.GetBoundingBox()),
                "flash_bbox_nm": {board.GetLayerName(layer): box_nm(pad.GetBoundingBox(layer))
                                  for layer in copper if pad.FlashLayer(layer)},
                "uuid": pad.m_Uuid.AsString(),
            }
            if net:
                nets.setdefault(net, 0)
                nets[net] += 1
            entry["pads"].append(record)
            pads.append(record)
        footprints.append(entry)
    report["footprints"] = footprints
    report["pads"] = pads
    report['outline'] = prove_contours([primitive(s) for s in outline_shapes],
                                       'outline_missing', 'outline_unproven')

    tracks = []
    for t in board.GetTracks():
        cls = t.GetClass()
        width = (t.GetWidth(pcbnew.PADSTACK.ALL_LAYERS) if cls == "PCB_VIA"
                 else t.GetWidth())
        entry = {"net": t.GetNetname(), "width_mm": mm(width),
                 "start_mm": vec_mm(t.GetStart()), "end_mm": vec_mm(t.GetEnd()),
                 "uuid": t.m_Uuid.AsString(), "native_class": cls,
                 "width_nm": int(width)}
        if cls == "PCB_VIA":
            entry["type"] = "via"
            entry["drill_mm"] = mm(t.GetDrillValue())
            entry['drill_nm'] = int(t.GetDrillValue())
            entry['flash_layers'] = [board.GetLayerName(layer) for layer in copper if t.FlashLayer(layer)]
            entry["layers"] = [board.GetLayerName(t.TopLayer()),
                               board.GetLayerName(t.BottomLayer())]
        else:
            entry["type"] = "track" if cls == "PCB_TRACK" else cls.lower()
            entry["layer"] = board.GetLayerName(t.GetLayer())
            entry["length_mm"] = mm(t.GetLength())
        if entry["net"]:
            nets.setdefault(entry["net"], 0)
        tracks.append(entry)
    report["tracks"] = tracks

    zones = []
    for z in board.Zones():
        # No silent fallback: a layer-set extraction failure must fail the probe.
        layers = [board.GetLayerName(l) for l in z.GetLayerSet().Seq()]
        zones.append({"net": z.GetNetname(), "layers": layers,
                      "filled": bool(z.IsFilled()),
                      "min_thickness_mm": mm(z.GetMinThickness()),
                      "uuid": z.m_Uuid.AsString()})
        if z.GetNetname():
            nets.setdefault(z.GetNetname(), 0)
    report["zones"] = zones
    # Plotted copper beyond tracks and pads, for fab_roundtrip: every saved
    # filled-polygon outline of board and footprint zones (one Gerber region
    # each) and netted board/footprint shapes (plotted as Conductor artwork).
    report["zone_fill_outlines"] = {board.GetLayerName(layer): sum(
        z.GetFilledPolysList(layer).OutlineCount()
        for z in (*board.Zones(), *(z for fp in board.GetFootprints() for z in fp.Zones()))
        if z.IsOnLayer(layer)) for layer in copper}
    shapes = [*board.GetDrawings(), *(g for fp in board.GetFootprints() for g in fp.GraphicalItems())]
    report["copper_conductor_shapes"] = {board.GetLayerName(layer): sum(
        1 for s in shapes if s.GetClass() == "PCB_SHAPE" and s.GetLayer() == layer and s.GetNetCode() > 0)
        for layer in copper}
    report["nets"] = dict(sorted(nets.items()))

    try:
        report["unconnected_items"] = board.GetConnectivity().GetUnconnectedCount(True)
    except TypeError:
        try:
            report["unconnected_items"] = board.GetConnectivity().GetUnconnectedCount()
        except Exception as exc:  # recorded, not fatal: facts stay facts
            report["unconnected_items"] = None
            report["unconnected_items_error"] = str(exc)
    except Exception as exc:
        report["unconnected_items"] = None
        report["unconnected_items_error"] = str(exc)
    if before is not None:
        verify_unchanged(board_path, before)
    return report


def nm(vec):
    return [int(vec.x), int(vec.y)]


def outlines(poly):
    """Outer contours of a native polygon set as nm point lists (holes listed separately)."""
    result, holes = [], 0
    for index in range(poly.OutlineCount()):
        chain = poly.Outline(index)
        result.append([[int(chain.CPoint(i).x), int(chain.CPoint(i).y)]
                       for i in range(chain.PointCount())])
        holes += poly.HoleCount(index)
    return result, holes


def enum_name(value, prefix, names):
    import pcbnew
    for name in names:
        if hasattr(pcbnew, prefix + name) and getattr(pcbnew, prefix + name) == value:
            return name
    return int(value)


PAD_SHAPES = ("CIRCLE", "RECTANGLE", "OVAL", "TRAPEZOID", "ROUNDRECT", "CHAMFERED_RECT", "CUSTOM")
VIA_TYPES = ("THROUGH", "BLIND", "BURIED", "MICROVIA", "BLIND_BURIED", "NOT_DEFINED")


def padstack_mode(item):
    import pcbnew
    value = item.Padstack().Mode()
    for name in ("NORMAL", "FRONT_INNER_BACK", "CUSTOM"):
        if getattr(pcbnew.PADSTACK, "MODE_" + name) == value:
            return name
    return int(value)


def saved_thickness(board_path):
    """Board thickness only as saved in (general (thickness ...)); pcbnew's load default is not a fact.

    Only the bounded root prefix ending at the root-level general node is read
    with the shared parser, so large boards stay within its node limit.
    """
    from pathlib import Path
    from agent_ee.symbol_library import LibraryError, children, nanometres, parse_sexpr
    try:
        data = Path(board_path).read_bytes()
        start = data.find(b"(general")
        while start >= 0:
            end = start
            for _ in range(256):
                end = data.find(b")", end) + 1
                if not end:
                    break
                try:
                    root = parse_sexpr(data[:end] + b")")
                except LibraryError:
                    continue
                if root[0] != "kicad_pcb" or not isinstance(root[-1], list) or root[-1][:1] != ["general"]:
                    break
                if len(children(root, "general")) != 1:
                    return None, "saved board declares more than one root general section"
                found = children(root[-1], "thickness")
                if len(found) != 1 or len(found[0]) != 2 or not isinstance(found[0][1], str):
                    return None, "saved board declares no single (general (thickness ...))"
                return nanometres(found[0][1]), None
            start = data.find(b"(general", start + 1)
        return None, "saved board declares no root (general ...) section"
    except (OSError, LibraryError) as exc:
        return None, f"saved board thickness unreadable: {exc}"


CUSTOM_COPPER_SCHEMA = 'agent-ee.custom-smd-copper/1'


def _polygon_topology(poly):
    """Retain holes on an unfractured COPY, never alter measured copper."""
    import pcbnew as k
    def record(value):
        outer, _ = outlines(value)
        holes = [[[[int(value.Hole(i, h).CPoint(j).x), int(value.Hole(i, h).CPoint(j).y)]
                   for j in range(value.Hole(i, h).PointCount())]
                  for h in range(value.HoleCount(i))] for i in range(value.OutlineCount())]
        return dict(outlines_nm=outer, holes_nm=holes, arcs=int(value.ArcCount()))
    unfractured = k.SHAPE_POLY_SET(poly)
    unfractured.Unfracture()
    return dict(saved=record(poly), unfractured=record(unfractured))


def _custom_copper(pad, node, layers, polygons, topology, max_error):
    import hashlib, json
    import pcbnew as k
    from .symbol_library import children, nanometres
    from .placement_probe import custom_pad_geometry
    result = dict(schema=CUSTOM_COPPER_SCHEMA, status='unsupported',
                  pad_uuid=pad.m_Uuid.AsString(), footprint_uuid=pad.GetParent().m_Uuid.AsString(),
                  recipe='single-filled-gr_poly+rect-anchor/1', api='GetEffectivePolygon/ERROR_OUTSIDE',
                  max_error_nm=max_error)
    try:
        if (node is None or node[2:4] != ['smd', 'custom'] or padstack_mode(pad) != 'NORMAL'
                or len(layers) != 1 or layers[0] not in ('F.Cu', 'B.Cu')):
            raise ValueError('custom SMD requires unique saved source, NORMAL stack and one outer layer')
        allowed = {'at', 'size', 'layers', 'net', 'uuid', 'solder_mask_margin', 'clearance',
                   'options', 'primitives', 'pinfunction', 'pintype', 'zone_connect'}
        if (any(not isinstance(c, list) or not c or c[0] not in allowed for c in node[4:])
                or len({c[0] for c in node[4:]}) != len(node[4:])
                or children(node, 'options') != [['options', ['clearance', 'outline'], ['anchor', 'rect']]]):
            raise ValueError('unsupported custom pad source options/geometry')
        primitives = children(node, 'primitives')
        if len(primitives) != 1 or len(primitives[0]) != 2:
            raise ValueError('custom pad requires one source primitive')
        primitive = primitives[0][1]
        if (not isinstance(primitive, list) or primitive[:1] != ['gr_poly'] or len(primitive) != 4
                or children(primitive, 'width') != [['width', '0']]
                or children(primitive, 'fill') != [['fill', 'yes']] or len(children(primitive, 'pts')) != 1):
            raise ValueError('custom pad requires one filled zero-stroke gr_poly')
        pts = children(primitive, 'pts')[0][1:]
        if not 3 <= len(pts) <= 10000 or any(not isinstance(v, list) or len(v) != 3 or v[0] != 'xy' for v in pts):
            raise ValueError('unsupported custom polygon vertices')
        points = [[nanometres(t) for t in v[1:]] for v in pts]
        if pad.GetAnchorPadShape(k.PADSTACK.ALL_LAYERS) != k.PAD_SHAPE_RECTANGLE:
            raise ValueError('custom anchor is not a native rectangle')
        merged = custom_pad_geometry(pad)  # Existing native merge + copied Unfracture hole check.
        if len(merged['merged_polygons_nm']) != 1:
            raise ValueError('custom primitive merge has multiple contours')
        for layer in layers:
            native_layer = k.F_Cu if layer == 'F.Cu' else k.B_Cu
            whole = k.SHAPE_POLY_SET()
            pad.TransformShapeToPolygon(whole, native_layer, 0, max_error, k.ERROR_OUTSIDE)
            whole.BooleanSubtract(pad.GetEffectivePolygon(native_layer, k.ERROR_OUTSIDE))
            if not whole.IsEmpty():
                raise ValueError('effective custom polygon does not cover whole native pad shape')
            data = topology[layer]
            for stage in ('saved', 'unfractured'):
                shape = data[stage]
                if (len(shape['outlines_nm']) != 1 or shape['arcs'] or any(shape['holes_nm'])
                        or len(shape['outlines_nm'][0]) < 3):
                    raise ValueError('custom copper holes/arcs/multiple or missing contours unsupported')
            contour = polygons[layer][0]
            if not sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(contour,contour[1:]+contour[:1])):
                raise ValueError('custom native copper is degenerate')
        result.update(status='qualified', source_pad_sha256=hashlib.sha256(
            json.dumps(node,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            source_vertex_count=len(points), native_anchor=merged['anchor'], anchor_shape='RECTANGLE',
            merged_pad_local_polygons_nm=merged['merged_polygons_nm'],
            layers={layer:dict(status='complete', whole_shape_covered=True, topology=topology[layer], polygon_sha256=hashlib.sha256(
                json.dumps(polygons[layer],separators=(',',':')).encode()).hexdigest()) for layer in layers})
    except (ValueError, TypeError, AttributeError, RuntimeError) as exc:
        result['reason'] = str(exc)
    return result


def dfm_facts(board, board_path):
    import pcbnew
    settings = board.GetDesignSettings()
    max_error = int(settings.m_MaxError)
    copper = [l for l in board.GetEnabledLayers().CuStack()]
    names = [board.GetLayerName(l) for l in copper]
    thickness_nm, thickness_problem = saved_thickness(board_path)
    facts = {"max_error_nm": max_error, "polygon_error": "outside",
             "copper_layers": names, "board_thickness_nm": thickness_nm,
             "board_thickness_source": None if thickness_nm is None else "saved (general (thickness))"}
    if thickness_problem:
        facts["board_thickness_problem"] = thickness_problem
        facts["native_thickness_default_nm"] = int(settings.GetBoardThickness())

    from .placement_probe import _custom_pad_nodes
    custom_nodes = _custom_pad_nodes(Path(board_path).read_bytes())
    pads, courtyards = [], []
    for fp in board.GetFootprints():
        side = board.GetLayerName(fp.GetLayer())
        for pad in fp.Pads():
            layers = [board.GetLayerName(l) for l in copper if pad.IsOnLayer(l) and pad.FlashLayer(l)]
            polygons, topology = {}, {}
            for layer in copper:
                if pad.IsOnLayer(layer) and pad.FlashLayer(layer):
                    poly = pad.GetEffectivePolygon(layer, pcbnew.ERROR_OUTSIDE)
                    polygons[board.GetLayerName(layer)], _ = outlines(poly)
                    topology[board.GetLayerName(layer)] = _polygon_topology(poly)
            pads.append({
                "uuid": pad.m_Uuid.AsString(), "ref": fp.GetReference(), "pad": pad.GetNumber(),
                "footprint_uuid": fp.m_Uuid.AsString(), "net": pad.GetNetname(),
                "attribute": pad_attribute(pad), "padstack_mode": padstack_mode(pad),
                "shape": enum_name(pad.GetShape(pcbnew.F_Cu), "PAD_SHAPE_", PAD_SHAPES),
                "position_nm": nm(pad.GetPosition()), "orientation_deg": pad.GetOrientationDegrees(),
                "size_nm": nm(pad_vec(pad, pad.GetSize)), "offset_nm": nm(pad_vec(pad, pad.GetOffset)),
                "drill_shape": enum_name(pad.GetDrillShape(), "PAD_DRILL_SHAPE_", ("CIRCLE", "OBLONG")),
                "drill_nm": nm(pad_vec(pad, pad.GetDrillSize)),
                "copper_layers": layers, "polygons_nm": polygons, "polygon_topology": topology})
            if pads[-1]['shape'] == 'CUSTOM':
                pads[-1]['custom_copper'] = _custom_copper(pad, custom_nodes.get(pad.m_Uuid.AsString()),
                    layers, polygons, topology, max_error)
        fp.BuildCourtyardCaches()
        courtyard_layer = pcbnew.B_CrtYd if fp.GetLayer() == pcbnew.B_Cu else pcbnew.F_CrtYd
        contours, holes = outlines(fp.GetCourtyard(courtyard_layer))
        courtyards.append({"ref": fp.GetReference(), "footprint_uuid": fp.m_Uuid.AsString(),
                           "side": side, "pad_count": len(fp.Pads()),
                           "board_only": bool(fp.IsBoardOnly()),
                           "exclude_from_pos_files": bool(fp.IsExcludedFromPosFiles()),
                           "position_nm": nm(fp.GetPosition()),
                           "polygons_nm": contours, "holes": holes})
    facts["pads"], facts["courtyards"] = pads, courtyards

    tracks, vias = [], []
    for t in board.GetTracks():
        cls = t.GetClass()
        if cls == "PCB_VIA":
            vias.append({"uuid": t.m_Uuid.AsString(), "net": t.GetNetname(),
                         "via_type": enum_name(t.GetViaType(), "VIATYPE_", VIA_TYPES),
                         "padstack_mode": padstack_mode(t),
                         "position_nm": nm(t.GetPosition()),
                         "width_nm": int(t.GetWidth(pcbnew.PADSTACK.ALL_LAYERS)),
                         "drill_nm": int(t.GetDrillValue()),
                         "layers": [board.GetLayerName(t.TopLayer()), board.GetLayerName(t.BottomLayer())],
                         "copper_layers": [board.GetLayerName(l) for l in copper if t.IsOnLayer(l)]})
            continue
        entry = {"uuid": t.m_Uuid.AsString(), "net": t.GetNetname(), "native_class": cls,
                 "layer": board.GetLayerName(t.GetLayer()), "width_nm": int(t.GetWidth()),
                 "start_nm": nm(t.GetStart()), "end_nm": nm(t.GetEnd())}
        if cls == "PCB_ARC":
            entry["mid_nm"] = nm(t.GetMid())
        tracks.append(entry)
    facts["tracks"], facts["vias"] = tracks, vias

    # United copper per layer through pcbnew's own polygon engine (overlaps count once).
    areas = {}
    unfilled = sorted(z.m_Uuid.AsString() for z in board.Zones()
                      if not z.GetIsRuleArea() and not z.IsFilled())
    for layer in copper:
        poly = pcbnew.SHAPE_POLY_SET()
        for t in board.GetTracks():
            if t.IsOnLayer(layer):
                t.TransformShapeToPolygon(poly, layer, 0, max_error, pcbnew.ERROR_INSIDE)
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                if pad.IsOnLayer(layer) and pad.FlashLayer(layer):
                    pad.TransformShapeToPolygon(poly, layer, 0, max_error, pcbnew.ERROR_INSIDE)
            for item in (*fp.GraphicalItems(), *fp.Zones()):
                if item.IsOnLayer(layer) and item.GetClass() != "ZONE":
                    item.TransformShapeToPolygon(poly, layer, 0, max_error, pcbnew.ERROR_INSIDE)
        for item in board.GetDrawings():
            if item.IsOnLayer(layer):
                item.TransformShapeToPolygon(poly, layer, 0, max_error, pcbnew.ERROR_INSIDE)
        zones = [z for z in (*board.Zones(), *(z for fp in board.GetFootprints() for z in fp.Zones()))
                 if not z.GetIsRuleArea() and z.IsOnLayer(layer)]
        for zone in zones:
            if zone.IsFilled():
                poly.Append(zone.GetFilledPolysList(layer))
        poly.Simplify()
        box = poly.BBox() if poly.OutlineCount() else None
        areas[board.GetLayerName(layer)] = {
            "area_nm2": int(poly.Area()),
            "bbox_nm": None if box is None else [int(box.GetX()), int(box.GetY()),
                                                 int(box.GetRight()), int(box.GetBottom())],
            "unfilled_zones": sorted(z.m_Uuid.AsString() for z in zones if not z.IsFilled())}
    facts["copper_area"] = areas
    facts["unfilled_zones"] = unfilled
    return facts


def routing_facts(board_path, *, board=None):
    """Exact saved track/via/zone inventory for routing equivalence (no refill)."""
    import pcbnew
    from .routing_equivalence import canonical_ring

    def polygons(poly):
        if poly.ArcCount():
            raise ValueError('curved zone contours are unsupported')
        return sorted((canonical_ring([nm(poly.Outline(i).CPoint(j))
                                       for j in range(poly.Outline(i).PointCount())]),
                       tuple(sorted(canonical_ring([nm(poly.Hole(i, h).CPoint(j))
                                                    for j in range(poly.Hole(i, h).PointCount())])
                                    for h in range(poly.HoleCount(i)))))
                      for i in range(poly.OutlineCount()))

    before = None
    if board is None:
        board, before = load_readonly_board(board_path)
    copper = list(board.GetEnabledLayers().CuStack())
    records = []
    for track in board.GetTracks():
        cls = track.GetClass()
        entry = {'kind': cls, 'net': track.GetNetname()}
        if cls == 'PCB_VIA':
            if padstack_mode(track) != 'NORMAL':
                raise ValueError('nonuniform via padstack is unsupported')
            entry.update(position_nm=nm(track.GetPosition()),
                         layers=sorted(board.GetLayerName(l) for l in copper if track.IsOnLayer(l)),
                         width_nm=int(track.GetWidth(pcbnew.PADSTACK.ALL_LAYERS)),
                         drill_nm=int(track.GetDrillValue()),
                         via_type=enum_name(track.GetViaType(), 'VIATYPE_', VIA_TYPES),
                         flash_layers=sorted(board.GetLayerName(l) for l in copper if track.FlashLayer(l)))
        elif cls in ('PCB_TRACK', 'PCB_ARC'):
            entry.update(layer=board.GetLayerName(track.GetLayer()), width_nm=int(track.GetWidth()),
                         endpoints_nm=sorted([nm(track.GetStart()), nm(track.GetEnd())]))
            if cls == 'PCB_ARC':
                entry['mid_nm'] = nm(track.GetMid())
        else:
            raise ValueError(f'unsupported routed primitive: {cls}')
        records.append(entry)
    zones = [*board.Zones(), *(z for fp in board.GetFootprints() for z in fp.Zones())]
    for zone in zones:
        if zone.GetIsRuleArea():
            continue  # Rule areas contain no routed copper.
        layers = [l for l in copper if zone.IsOnLayer(l)]
        if not layers:
            continue
        records.append({'kind': 'ZONE', 'net': zone.GetNetname(),
                        'layers': sorted(board.GetLayerName(l) for l in layers),
                        'width_nm': int(zone.GetMinThickness()),
                        'outline_nm': polygons(zone.Outline()), 'filled': bool(zone.IsFilled()),
                        'filled_polygons_nm': {board.GetLayerName(l): polygons(zone.GetFilledPolysList(l))
                                               if zone.HasFilledPolysForLayer(l) else [] for l in layers}})
    if before is not None:
        verify_unchanged(board_path, before, 'routing geometry probe')
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("board")
    parser.add_argument("-o", "--out", required=True)
    parser.add_argument("--dfm", action="store_true", help="add the dfm native fact section")
    args = parser.parse_args(argv)
    try:
        import pcbnew  # noqa: F401
    except ImportError as exc:
        print(f"pcbnew bindings unavailable: {exc}", file=sys.stderr)
        return 3
    board, before = load_readonly_board(args.board)
    report = extract(args.board, board=board)
    if args.dfm:
        report["dfm"] = dfm_facts(board, args.board)
    verify_unchanged(args.board, before)
    with open(args.out, "w") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(f"geometry: {len(report['footprints'])} footprint(s), "
          f"{len(report['tracks'])} track item(s), {len(report['zones'])} zone(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
