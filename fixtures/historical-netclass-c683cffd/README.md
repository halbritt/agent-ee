# Historical netclass run (agent-ee c683cffd, qualification /1)

A finished checks-only review run, produced by agent-ee commit c683cffd with the
cross-class review's `historical-netclass.py` (2026-11-03) and copied here byte
for byte. Its captured netclass qualification is `agent-ee.netclass-qualification/1`,
the format before `native_release` was recorded separately. Its inputs are copies
of owned fixtures: `fixtures/netclass-completeness/native.kicad_pro` and
`qualification.json` as of cac03d1f, `fixtures/dc-path/straight.kicad_pcb` and
`dc_path.kicad_sch`, and a one-line requirement. Visual capture was skipped.

`tests/test_netclass_completeness.py` verifies a copy with the current code: the
run verifies against its own records, with its older implementation reported as
an observation, while a new run with a /1 qualification is refused at capture.
The absolute paths in `candidate.json` name the review's scratch directory; a
snapshot verification does not read them. Do not edit these files: the test
depends on their exact bytes.
