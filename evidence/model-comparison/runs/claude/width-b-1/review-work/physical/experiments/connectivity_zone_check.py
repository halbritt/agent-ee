import pcbnew, shutil, hashlib
shutil.copy('/candidate/project/rc_filter.kicad_pcb', '/work/experiments/scratch_board.kicad_pcb')
b = pcbnew.LoadBoard('/work/experiments/scratch_board.kicad_pcb')

# saved fill polygon vertex counts per zone
for z in b.Zones():
    layer = z.GetLayerName()
    poly = z.GetFilledPolysList(z.GetFirstLayer())
    print("zone net", z.GetNetname(), "layer", layer, "saved fill outlines:", poly.OutlineCount(),
          "vertices:", poly.TotalVertices(), "IsFilled:", z.IsFilled())
    saved_hash = hashlib.sha256(poly.Format(True).encode()).hexdigest()

# refill on the scratch copy and compare
filler = pcbnew.ZONE_FILLER(b)
filler.Fill(b.Zones())
for z in b.Zones():
    poly = z.GetFilledPolysList(z.GetFirstLayer())
    new_hash = hashlib.sha256(poly.Format(True).encode()).hexdigest()
    print("after refill vertices:", poly.TotalVertices())
    print("fill identical to saved:", new_hash == saved_hash)

# connectivity: ratsnest after refill
conn = b.GetConnectivity()
conn.RecalculateRatsnest()
print("unconnected count after refill:", conn.GetUnconnectedCount(True))
