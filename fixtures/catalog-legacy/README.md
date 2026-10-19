# Legacy catalog normalization fixture

Original synthetic regulator claims captured by the unmodified catalog module at
commit `c3ec24c936296e2c55cc45924bc67a46b14ec8aa`. This extends the independent
AGENTEE-165 reviewer’s original failing input
with all four newly recognized keys and a preexisting attribute outside the
regulator category. These bytes were captured by the exact old module, then
loaded successfully by that module. No table was dropped from a current capture.

`provenance.json` pins the producer module and all input/output bytes. The
`owned.csv` source is retained outside the two-file snapshot. `output_current`,
`input_voltage_max`, `switching_frequency`,
and `topology` are raw claims unknown to that producer. `input_voltage` and
`resistance` are normalized, preserving the old global vocabulary even for
attributes outside the regulator category. No supplier, live stock, or actual
pod suitability is asserted.

To reproduce, load that commit's `src/agent_ee/catalog.py` as an `agent_ee`
package module. Take the source fields from `snapshot/manifest.json`, set its
schema to `agent-ee.catalog-source/1`, and call `capture_catalog` with this
folder as source root and a fresh output directory. The SQL byte pin assumes
the recorded SQLite version; tests use these retained bytes without Git or
network access.
