# Proposed fix: PCB_VIA width query in geometry_probe (Fable, agent-413)

Status: applied and regression-tested by the coordinator in commit `2a0e7f0`,
after preserving the interrupted initial attempts at source commit `6efecc7`.
Fresh full review demonstrations use the corrected implementation.

## Reviewer concern (confirmed real)

`work/geometry/console.log` contains, once per via:

    ./kicad/pcbnew/pcb_track.cpp(387): assert ""false"" failed in GetWidth():
    Warning: PCB_VIA::GetWidth called without a layer argument

Cause: `agent_ee/geometry_probe.py` calls `t.GetWidth()` uniformly for all
track items. KiCad 10 vias are padstack-aware; the no-argument overload is a
deprecated compatibility path that asserts before falling back. The returned
value is still the padstack diameter, which is why native track widths and
via `.size` agreed — tooling noise, not wrong data, but it pollutes captured
evidence and would mask a real assert.

## API probe (installed pcbnew 10.0.7, /usr/bin/python3)

    PCB_VIA.GetWidth doc:
        GetWidth(PCB_VIA self) -> int
        GetWidth(PCB_VIA self, PCB_LAYER_ID aLayer) -> int
    PCB_TRACK.GetWidth doc: GetWidth(PCB_TRACK self) -> int   (no layer overload)
    pcbnew.PADSTACK.ALL_LAYERS exists; via.GetFrontWidth() exists;
    via.GetPadstack() does NOT exist in the Python bindings (only SetPadstack).

On the donor board (Polygonus-Universal-Base.kicad_pcb, 1224 vias, 2207 tracks):

| call                                   | asserts (pcb_track.cpp:387) | value |
|----------------------------------------|------------------------------|-------|
| `via.GetWidth()` (current)             | 1224 (one per via)           | correct |
| `via.GetWidth(pcbnew.PADSTACK.ALL_LAYERS)` (proposed) | **0**        | identical |

- Value identity: 1224 vias compared, **0 differences** old vs new; distinct
  diameters 0.5 mm and 0.8 mm, matching the native file's `(via (size …))`.
- `GetWidth(ALL_LAYERS)` also equals `GetFrontWidth()` on all 1224 vias.
- Plain `PCB_TRACK.GetWidth()` has no layer overload and no assert; it must
  keep the no-argument call.

The three `property.h(607) PROPERTY_ENUM` lines are pcbnew *import-time*
noise, independent of this call (present even in the fixed run). fome-mc's
eda.py filters exactly this assert as known startup noise (its `NOISE`
regex); if wanted, filtering belongs at evidence-rendering, not in the probe.

## Exact patch (src/agent_ee/geometry_probe.py)

Replace:

```python
    tracks = []
    for t in board.GetTracks():
        cls = t.GetClass()
        entry = {"net": t.GetNetname(), "width_mm": mm(t.GetWidth()),
                 "start_mm": vec_mm(t.GetStart()), "end_mm": vec_mm(t.GetEnd()),
                 "uuid": t.m_Uuid.AsString()}
```

with:

```python
    tracks = []
    for t in board.GetTracks():
        cls = t.GetClass()
        # KiCad 10 vias are padstack-aware: the no-argument GetWidth() asserts
        # (pcb_track.cpp:387). Query the via diameter with an explicit layer;
        # plain PCB_TRACK has no layer overload and keeps the bare call.
        width = (t.GetWidth(pcbnew.PADSTACK.ALL_LAYERS) if cls == "PCB_VIA"
                 else t.GetWidth())
        entry = {"net": t.GetNetname(), "width_mm": mm(width),
                 "start_mm": vec_mm(t.GetStart()), "end_mm": vec_mm(t.GetEnd()),
                 "uuid": t.m_Uuid.AsString()}
```

One expression changes; everything else (vias' `drill_mm`/`layers`, track
`layer`/`length_mm`) is untouched.

## Proposed regression test (tests/test_checks.py, apply with the patch)

Add to `ControlFixtureTest` (the control board contains 1 via, so the old
code reproduces the assert there too):

```python
    def test_geometry_console_has_no_via_width_assert(self):
        console = (self.run_dir / "work" / "geometry" / "console.log").read_text(errors="replace")
        self.assertNotIn("PCB_VIA::GetWidth called without a layer argument", console)
```

Expected: fails against current code (assert present once), passes with the
patch; all existing 35 tests unaffected (values proven identical, so
`width_mm`, `narrowest_mm` and the min-width verdicts cannot move).

## Evidence trail

Probe scripts: scratchpad `via_probe.py` (+ inline comparison), run against
`build/donor-discovery/inputs/project/Polygonus-Universal-Base.kicad_pcb`
(read-only). Raw counts above are from those runs on 2026-10-16.
