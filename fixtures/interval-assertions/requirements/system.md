# Owned divider interval requirements

Synthetic requirements authored for agent-ee AGENTEE-61. No third-party design.

- R-VIN: the input rail is 10 V within +/-1 %.
- R-VOUT: the divider output `vin * r2 / (r1 + r2)` stays within 3.3 V +/-10 %
  at every combination of rail and resistor tolerance.
- R-C1-RATING: the C1 rated voltage is at least the input rail at its maximum.

The machine-readable relations are in `divider.assertions.json`.
