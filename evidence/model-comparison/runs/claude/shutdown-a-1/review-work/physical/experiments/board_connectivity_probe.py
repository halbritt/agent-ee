# Probe saved-board connectivity of /candidate/project/rc_filter.kicad_pcb
# using pcbnew WITHOUT refilling zones. Checks:
# 1) per-net pad membership
# 2) geometric continuity of the saved B.Cu GND zone fill between
#    J1 pad 3 and the GND via, via point-in-polygon sampling
import pcbnew, json

board = pcbnew.LoadBoard("/candidate/project/rc_filter.kicad_pcb")
out = {"pcbnew_version": pcbnew.GetBuildVersion()}

pads = []
for fp in board.GetFootprints():
    for pad in fp.Pads():
        pads.append({"ref": fp.GetReference(), "pad": pad.GetNumber(),
                     "net": pad.GetNetname(),
                     "pos_mm": [pad.GetPosition().x/1e6, pad.GetPosition().y/1e6]})
out["pads"] = pads

zones = board.Zones()
zinfo = []
for z in zones:
    fill = z.GetFilledPolysList(pcbnew.B_Cu)
    zinfo.append({"net": z.GetNetname(), "is_filled": z.IsFilled(),
                  "outline_count": fill.OutlineCount(),
                  "area_mm2": fill.Area()/1e12})
out["zones"] = zinfo

# Sample points: via barrel edge on B.Cu, J1 pad3 annulus, straight-line path between
z = list(zones)[0]
fill = z.GetFilledPolysList(pcbnew.B_Cu)
def inside(x_mm, y_mm):
    return fill.Contains(pcbnew.VECTOR2I(int(x_mm*1e6), int(y_mm*1e6)))
samples = {
  "via_center_113_100": inside(113.0, 100.0),
  "near_via_112.8_100": inside(112.8, 100.0),
  "J1pad3_center_100_105.08": inside(100.0, 105.08),
  "J1pad3_annulus_100_105.7": inside(100.0, 105.7),
  "J1pad3_spoke_up_100_104.4": inside(100.0, 104.4),
  "J1pad3_spoke_down_100_105.9": inside(100.0, 105.9),
  "mid_board_105_106": inside(105.0, 106.0),
  "mid_board_110_104": inside(110.0, 104.0),
  "left_of_J1pads_97.5_102": inside(97.5, 102.0),
}
out["fill_contains"] = samples

# Native connectivity on the saved board (no refill): unrouted/unconnected count
conn = board.GetConnectivity()
out["unconnected_count"] = conn.GetUnconnectedCount(True)

print(json.dumps(out, indent=1))
