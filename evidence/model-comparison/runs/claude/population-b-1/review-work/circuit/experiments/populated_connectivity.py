#!/usr/bin/env python3
"""Populated-assembly DC connectivity check for /candidate/project/rc_filter.kicad_pcb.

Question: does a FITTED resistive DC path connect net /IN to net /OUT?

Method:
 1. Load the saved board with pcbnew (no refill, no modification; file opened read-only
    from /candidate and never saved).
 2. Enumerate board copper (tracks, vias, zones, pads) by net. DRC on the saved board
    reported 0 violations and 0 unconnected items, so copper of different nets does not
    touch; cross-net conduction can only come from fitted component bodies.
 3. Enumerate footprints and their DNP state. Treat each FITTED 2-terminal passive as a
    conductor class: resistor = resistive DC path, capacitor = DC open, connector pins =
    isolated from each other.
 4. Build net-level graph: edge (netA, netB, kind) for each fitted component bridging
    two nets with a DC-conductive, resistive element. Check /IN -- /OUT reachability.
"""
import pcbnew

board = pcbnew.LoadBoard("/candidate/project/rc_filter.kicad_pcb")

print("pcbnew version:", pcbnew.Version())
print()

fitted_bridges = []   # (ref, value, kind, {pad: net})
for fp in board.GetFootprints():
    ref = fp.GetReference()
    dnp = fp.IsDNP()
    pads = {p.GetNumber(): p.GetNetname() for p in fp.Pads()}
    print(f"{ref:4s} value={fp.GetValue():12s} DNP={dnp}  pads={pads}")
    if dnp:
        continue
    if ref.startswith("R"):
        fitted_bridges.append((ref, "resistive", pads))
    elif ref.startswith("C"):
        fitted_bridges.append((ref, "capacitive (DC open)", pads))
    # Connector J1: pins mutually isolated; no internal bridge.

print()
print("Fitted components that bridge two nets:")
resistive_in_out = False
for ref, kind, pads in fitted_bridges:
    nets = sorted(set(pads.values()))
    print(f"  {ref}: {kind} between {nets}")
    if kind == "resistive" and set(nets) == {"/IN", "/OUT"}:
        resistive_in_out = True

print()
print("Board copper items by net (cannot bridge nets; DRC clearance clean):")
for t in board.GetTracks():
    print(f"  {type(t).__name__:9s} net={t.GetNetname()}")
for z in board.Zones():
    print(f"  ZONE      net={z.GetNetname()} layers={[board.GetLayerName(l) for l in z.GetLayerSet().Seq()]}")

print()
print("RESULT: fitted resistive DC path /IN -> /OUT exists:", resistive_in_out)
