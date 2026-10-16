"""Extract saved-board geometry facts through pcbnew (runs inside the sandbox).

Judgment-free: emits what the saved .kicad_pcb actually contains (footprints
with DNP state, pads with size/drill/position/net, track segments, vias,
zones, nets, unconnected count). agent_ee.checks applies declared criteria.
UUIDs use KIID.AsString() (str(KIID) is only a SWIG proxy repr).
"""

import argparse
import json
import sys


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


def extract(board_path):
    import pcbnew
    board = pcbnew.LoadBoard(board_path)
    report = {"pcbnew_version": pcbnew.Version(), "board": board_path}

    nets = {}
    footprints, pads = [], []
    for fp in board.GetFootprints():
        entry = {
            "ref": fp.GetReference(), "value": fp.GetValue(),
            "layer": board.GetLayerName(fp.GetLayer()),
            "dnp": bool(fp.IsDNP()),
            "uuid": fp.m_Uuid.AsString(),
            "position_mm": vec_mm(fp.GetPosition()),
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

    tracks = []
    for t in board.GetTracks():
        cls = t.GetClass()
        entry = {"net": t.GetNetname(), "width_mm": mm(t.GetWidth()),
                 "start_mm": vec_mm(t.GetStart()), "end_mm": vec_mm(t.GetEnd()),
                 "uuid": t.m_Uuid.AsString()}
        if cls == "PCB_VIA":
            entry["type"] = "via"
            entry["drill_mm"] = mm(t.GetDrillValue())
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
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("board")
    parser.add_argument("-o", "--out", required=True)
    args = parser.parse_args(argv)
    try:
        import pcbnew  # noqa: F401
    except ImportError as exc:
        print(f"pcbnew bindings unavailable: {exc}", file=sys.stderr)
        return 3
    report = extract(args.board)
    with open(args.out, "w") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(f"geometry: {len(report['footprints'])} footprint(s), "
          f"{len(report['tracks'])} track item(s), {len(report['zones'])} zone(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
