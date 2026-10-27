# Acquisition interface variant 525, revision 1

Author: agent-673, 2026-10-27, under agent-669's bounded preparation assignment.
This is an agent-selected tooling experiment, not an owner product requirement.

Change ACQ-ELEC-5 from at most 100 pF to at most **150 pF total capacitance
per I2C line**, including board, host and cable. Retain 3.3 V open-drain operation,
the existing 10–400 kHz timing contract, address 0x48, ADC configuration, host
leakage/sink assumptions and sequencing. Retain the captured 17-part circuit,
R3/R4 = 2.2 kohm ±1%, component sources, layout, groups and locality limits.

Use the captured interval equation `0.8473 * pullup_r * bus_c` at the declared
maximum load. Keep its existing 300 ns rise-time limit. Compare the original
100 pF case, the requested 150 pF case and an intentionally contradictory
200 pF model control with the same equation and limit. A failing control is
evidence about that authored model, not a native waveform measurement.

The 150 pF value is a selected test input. It is not a manufacturer guarantee
or a measured cable/host property. The 300 ns limit is retained from the captured
ADS1115 fast-mode timing table, SBAS444E §5.6. Model applicability and actual
total line capacitance still require engineering/physical evidence.

The revised spec and OPERATING.md supersede only the 100 pF bus-capacitance
instruction for this private revision. The original operating supplement's
100 pF sentence describes its original capture; all its power-up and conversion
timing clarifications continue to apply. Preserve that supplement byte-for-byte.
Preserve historical review and diagnostic captures as history; they do not
become judgments or acceptance of this variant.

Prepare inputs and retain the edit ledger. Do not generate a new board, run a
full build, carry forward old judgments, order, upload, or claim M1/M2 acceptance.
Final build/finalization qualification waits for AGENTEE-521/515 and coordinator
allocation after independent review.
