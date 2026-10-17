#!/usr/bin/env python3
"""Stale-fill detection: refill a SCRATCH COPY of the board and compare the
fresh B.Cu GND fill against the saved fill. The candidate is never modified.

Comparison: polygon areas, vertex counts, and XOR (symmetric difference) area
via SHAPE_POLY_SET booleans.
"""
import shutil
import pcbnew

SRC = "/candidate/project/rc_filter.kicad_pcb"
SCRATCH = "/work/experiments/scratch_refill/rc_filter.kicad_pcb"
import os
os.makedirs(os.path.dirname(SCRATCH), exist_ok=True)
shutil.copy(SRC, SCRATCH)

b_saved = pcbnew.LoadBoard(SRC)
z_saved = list(b_saved.Zones())[0]
fill_saved = pcbnew.SHAPE_POLY_SET(z_saved.GetFilledPolysList(pcbnew.B_Cu))

b_new = pcbnew.LoadBoard(SCRATCH)
filler = pcbnew.ZONE_FILLER(b_new)
ok = filler.Fill(b_new.Zones())
print("refill ok:", ok)
z_new = list(b_new.Zones())[0]
fill_new = pcbnew.SHAPE_POLY_SET(z_new.GetFilledPolysList(pcbnew.B_Cu))

a_saved = fill_saved.Area() / 1e12  # nm^2 -> mm^2
a_new = fill_new.Area() / 1e12
print(f"saved fill: outlines={fill_saved.OutlineCount()} verts={fill_saved.TotalVertices()} area={a_saved:.4f} mm^2")
print(f"fresh fill: outlines={fill_new.OutlineCount()} verts={fill_new.TotalVertices()} area={a_new:.4f} mm^2")

xor = pcbnew.SHAPE_POLY_SET(fill_saved)
xor.BooleanXor(fill_new)
print(f"XOR area: {xor.Area()/1e12:.6f} mm^2  (0 => fills identical)")

# connectivity on freshly filled scratch board
conn = b_new.GetConnectivity()
print("fresh-fill unconnected count:", conn.GetUnconnectedCount(False))
