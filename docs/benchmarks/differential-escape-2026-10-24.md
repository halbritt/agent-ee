# Differential-terminal placement repair

The placement preference is exercised; the complete board remains rejected.
No manufacturing acceptance or KRT superiority claim follows from these runs.
The live telemetry-pod hold remains
in force. The owner requested actual top and bottom 3D views of esp_prog after
it passes its required checks; no accepted board exists yet.

## Matched failure

The input is the feasible seed-10 placement from the declared-edge regression,
native PCB SHA-256 `602cddffbea934ea20707353dc356426b270a73705cb41354920f8c0d888f8f5`.
Both engines received the same project bytes, SHA-256
`ee37b4fd59153a4b878c13d77ff0cb6c71e9e3baa96d0015bb25df01ae7e09ed`, width 0.2 mm,
gap 0.25 mm, original copper protection, disabled rule rewriting and a 300-second
budget each. They ran sequentially in the same native executor with the same
Python dependency environment. This exercises their differential subcommand,
not the entire public agent-ee workflow.

Stock upstream `d24a7602012b96c5cfa610ea05a171646ad601bd` was archived from a clean
checkout and built offline with its locked Rust dependencies. The owned engine
is the installed kicad-route `b5774496d8466b67ffa0362b6665fee2108155a3` extraction
and patches. Executor image `agent-ee:exec-6c7cb78b8f650c16` has image ID
`sha256:6c2641a2172afe67719dd86568ea2faf9988382db0c632b68d094bf0a6a774c7`.
KiCad is 10.0.7, Python 3.12.3, Rust 1.99.1. Routing uses the installed
`/opt/kicad-route/.venv-krt/bin/python`, not system Python.

| Observation | Owned engine | Stock latest-main |
| --- | --- | --- |
| Differential subprocess exit | 0 | 0 |
| Pair result | Coupled trunk, hybrid escape, terminals not coupled | Same |
| Native open connections on the two pair nets | 0 | 0 |
| Native violation errors / remaining other-net opens | 0 / 44 | 0 / 44 |
| Protected native geometry/identity | Preserved | Preserved |
| Normal coupled-pair guard | Rejects | Would reject |
| One observed subprocess duration | 3.033153 s | 2.965467 s |

These single durations do not establish performance or reliability superiority.
Stock additionally reports 12.8995 mm of coupled copper, 4.4684 mm of uncoupled
copper and 0.2071 mm skew. Electrical connection alone is insufficient. The
normal guard correctly discards these outputs and leaves the two pair nets
open in the complete attempted board. Neither input nor failed output is an
accepted manufacturing design.

The [latest source delta](../research/krt-2026-10-24/d24a760/delta.md) is one
documentation commit after the original feature map. All 1,791 other tracked
files are unchanged. New descriptions of experimental AWX terminal routing do
not change the ordinary differential router used here.

## Root-cause probes

Changing only the launch setback to 1.5 mm still produces hybrid escapes;
3.0 mm defers to single-ended routing. Both strategies were rejected. Removing
all footprints except the two pair terminals produces a coupled, zero-via
route. These reduced boards are diagnostic counterexamples, never candidate
substitutions.

Removing C1, C2, C3, C4, Y1 or U2 individually does not cure the complete board.
Adding U2 alone to the reduced terminal case reproduces the failure; adding R4
alone also reproduces it. C1, C2, C3, C4, Y1, CON1 and CON2 individually do not.
Thus there are independent escape obstructions at the connector and chip.
Ordinary component/pad clearance allows this placement but does not ensure
space for a coupled terminal launch.

The experiments retain initial setup failures: missing dependencies under
system Python, a wrong virtualenv path, and a second native load returning an
unusable SWIG object. Corrected experiments use the actual installed virtualenv
and one native process per diagnostic case. These are not router failures.

## Reusable correction and fresh verification

Native capture now proposes soft local space preferences for aligned,
unambiguous differential terminals using pad UUIDs, local geometry and native
pair width/gap/clearance. The preference extends two pair pitches beyond the
pad boundary and includes one trace half-width plus clearance at either side.
Both the geometry and its search weight are labelled agent-selected heuristics.
They do not create owner requirements, prove impedance, or change feasibility.
Ambiguous, diagonal and multi-pad terminals receive no inferred direction.

The prepared Rust model carries these rectangles through rotations, its spatial
index and both incremental/full score calculations. Copper intersecting the
preferred space adds a separately reported soft cost. Existing fixed anchors,
native rules, placement constraints and the router's coupled-pair guard remain
in force.

The strict Rust suite passes 99 tests, including the retained native problem.
The relevant native suite passes 82 tests with no skips. The first invocation
omitted `AGENT_EE_515_E2E_PROBLEM` and correctly failed that required case; the
corrected complete invocation passed. Native capture checks preserve source
bytes and bind the preference to pad UUIDs through all four quarter turns.

The unchanged six-seed, 30,000-step public invocation produced a feasible seed-0
placement with zero preferred-space overlap and zero proximity excess. It
finished in 236.107 seconds and correctly returned `verification_failed`:
zero native violation errors, two open pair connections, all three declared
edges passing, native geometry preserved. Final PCB SHA-256:
`d8de33e8f7036c0a15b1f5320e739c36b0959925ffe919a38fbc8418abac6134`.
It has 350.3558 mm of copper, 30 vias and six geometric via-in-pad sites; none
of these metrics establishes acceptance.

The closer terminals expose a separate backend policy mismatch: its 4.5 mm
short-leg heuristic defers this explicitly coupled pair to single-ended routing.
A prototype that forces a coupled attempt then rejects terminal polarity/clearance
geometry and returns hybrid escapes. Launch setbacks of 0.3 and 0.5 mm also fail
that guard. These failed strategies are retained; no pad-net swap or relaxed
clearance is authorized as a remedy. The prototype is separate kicad-route work,
not yet part of this integrated agent-ee pin.

Reproduction:

```sh
ae-ci-exec . -- python3 -m agent_ee.cli place run \
  build/agent545/board-config-deeper.json --out build/agent545/board-run-escape
ae-ci-exec . -- env PYTHONPATH=src:tests AGENT_EE_REQUIRE_TOOLCHAIN=1 \
  AGENT_EE_ESP_PROG_PREPARED=/opt/agent-ee/build/agent545/baseline \
  AGENT_EE_515_E2E_PROBLEM=/opt/agent-ee/build/agent545/retained-problem.json \
  python3 -m unittest -v tests.test_placement_escape tests.test_placement_job \
  tests.test_placement_job_capture tests.test_placement_job_clearance \
  tests.test_placement_job_complexity tests.test_placement_job_guards \
  tests.test_placement_job_refusal tests.test_placement_job_reservations
```

Use a fresh output directory when replaying; restore captured inputs/configuration
from the private evidence bundle. The executor is now
`agent-ee:exec-9a21ba287c7cda1d`, image ID
`sha256:3c7094ce1153db32fd4de56d221544b3aa765a5c82a24686ab2156271184b87e`.

Working evidence: `build/agent545/krt-pair-comparison/`, including exact scripts,
arguments, immutable inputs, per-backend records, native checks and all reduced
probes. Durable evidence is under
`~/.cache/agent-ee-qualification/agent545/differential-escape-20261024/` with
`SHA256SUMS`, source patch, exact stock archive/binary and integration identity.
The whole-system acceptance map remains open. The next routing action is to
carry explicit coupling policy into the backend, qualify it on an original
short-pair design, and repair the remaining terminal geometry/placement feedback.
