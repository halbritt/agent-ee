# Hierarchical stage integration, 2026-10-24

This is a bounded integration result, not an accepted electrical design or
manufacturing package. The complete delivery goal remains open in
[the system acceptance map](../system-acceptance.md).

The existing emitter could generate a native hierarchy, but `schematic_stage`
refused more than one schematic output. Stage presence also refused hierarchy,
so scaffold omitted stage gates whenever a required part was on a child sheet.

The repair declares every native sheet as an output of the emission step and
adopts the complete set through the existing receipt mechanism. Forward
annotation re-emits the intent and compares every saved sheet before exporting
the root hierarchy. Presence reuses the existing occurrence-reference parser
and requires agreement with fresh native XML. Scaffold now derives gates for
required references on every sheet; the existing parts check retains its
separate sheet/reference membership obligation. No project rule is replaced by
the emitter's generated project file.

## Native exercise and negative cases

The repository's original `two_level.intent.json` is a targeted feature case.
Its five components and four physical nets have hand-specified expectations
that predate hierarchical emission. Changing the child-sheet capacitor from
100 nF to 220 nF passes through the stage command, adopted child file, native
XML, forward annotation and freshly loaded PCB. The two identically named
local nets remain electrically separate; declared ports connect across sheets.

The public `agent-ee scaffold` and `agent-ee review --checks-only` commands also
run on a two-sheet empty scaffold. They produce replayable `stage_not_reached`
findings for the absent required parts, including the child-sheet part. This
is the expected negative outcome, not a successful design.

Retained rejection cases include a stale child sheet, an omitted child output,
a configured root swapped with its child, a missing file, a hierarchy cycle,
a path outside the project and an unsupported child format version. No board
is adopted from stale or incomplete schematic emission.

Run the relevant integration suite in the supported KiCad 10.0.7 environment:

```sh
ae-ci-exec . -- env PYTHONPATH=src:tests python3 -m unittest -v \
  tests.test_schematic_stage tests.test_stages tests.test_scaffold_producer \
  tests.test_campaign_intent_retry tests.test_stage_done
```

To retain the native feature-case inputs, outputs, receipts and observations,
set `AGENT_EE_SCHEMATIC_EVIDENCE` to a fresh directory inside the checkout when
running `tests.test_schematic_stage` and
`tests.test_scaffold_producer.NativeProducerTests.test_cli_hierarchy_absence_has_replayable_native_stage_evidence`.

Evidence for this iteration is retained outside the checkout at
`~/.cache/agent-ee-qualification/agent545/hierarchical-stages-20261024/`.
Its integration record identifies the revision and final test results;
`SHA256SUMS` binds the source patch, logs and native artifacts. Earlier failed
test runs are retained: the first requested a missing API, the presence test
then reproduced the hierarchy refusal, and the first retained CLI test used
the electrical-inventory reader on an intentionally empty scaffold. That last
test was corrected to use the existing identity-only native reader. An initial
suite invocation also named a nonexistent test module; it did not execute that
module. The final invocation uses the actual campaign retry tests.

## Remaining limits

The emitter's output files are siblings; changing the sheet set requires a new
declared capture. Presence reads descendants within the root project directory.
Native-edit-preserving regeneration remains flat-only and refuses a hierarchical
configuration explicitly. The campaign's automatic pin-diagnostic retry matcher
also remains flat-only. These limits remain work for the complete workflow;
they are not silently converted to accepted omissions.

This iteration does not qualify placement, routing, manufacturing, the fresh
multiblock design, clean installation or full-workflow resumption. `esp_prog`
still has two rejected USB-pair connections. No passing board or 3D render of
an accepted board is claimed.
