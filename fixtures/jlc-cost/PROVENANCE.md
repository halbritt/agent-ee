# fixtures/jlc-cost provenance

Original inputs written for agent-ee (AGENTEE-77). They price the owned
`fixtures/fab-export` board; no third-party design is used.

- `jlc-pricing-owned.json` is an `agent-ee.jlc-pricing/1` table. Its entry
  kinds (layer base price within a size box, stepped area surcharge, per-side
  setup and stencil, per-joint and per-unique-part loading fees, tier side
  limit) follow the mechanism of atopile's MIT-licensed
  `src/atopile/server/domains/cost_estimation.py`. No code, constant or text is
  copied from it. Every value is a round test value, marked
  `"values": "owned-test-values"`; none is a JLCPCB price.
- `catalog.json` is an `agent-ee.catalog-snapshot/1` snapshot for the four
  fixture LCSC numbers (`C9000xx`, invented). Stock, prices and class flags are
  test values.
- `control.scope.json` runs `netlist`, `fab_export` and two estimates
  (standard and economic tier) over the fab-export fixture.
