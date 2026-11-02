# Requirements to a retained board build

`agent-ee design generate` asks a subscription author to turn requirements into
an `agent-ee.design/1` statement, captures the selected parts, compiles the typed
bundle and builds through package. Run native work inside the production image
under `native-admit`, with the pinned router, planner and subscription login
mounts required by the deployment. The command does not provide admission or
install a native runtime itself. Capture/search download vendor bytes; they
never upload project files or place orders.

```sh
agent-ee design generate REQUIREMENTS.md --out NEW_RUN \
  --author codex:gpt-6.1-sol:high --max-attempts 4 --max-seconds 2400
agent-ee design benchmark fixtures/design-requirements --out NEW_BENCHMARK \
  --max-attempts 3 --max-seconds 1200
```

The default author needs a subscription login, not an API key. Set
`AGENT_EE_CODEX_AUTH_HOME` to the mounted login directory and
`AGENT_EE_CODEX_BINARY` to the native binary. There is no fallback on login,
quota or harness failure. Those failures stop the design and benchmark.
`--context-root` selects the checkout with current compiler docs/examples;
`--kicad-root` selects local CAD libraries (default `/usr/share/kicad`).

The author receives the current compiler docs, example statements, schema
validators and a structured envelope schema. The statement is JSON text inside
that envelope because its parts/nets maps have design-dependent keys. Every
intent and functional text has an exact requirement quotation in `trace`.
`agent_selected` lists discretionary choices separately from requirement facts.
These are retained provenance, not an assertion that the prose or engineering is
correct. Unsupported requirements must be reported explicitly.

The author can run these helpers inside its isolated workspace:

```sh
python3 -m agent_ee.design_tools search 'NE555' --out /work/experiments/search-1
python3 -m agent_ee.design_tools symbol Timer:NE555D
python3 -m agent_ee.design_tools footprint Package_SO:SOIC-8_3.9x4.9mm_P1.27mm
```

Search retains one bounded JLC response page and returns stock, tier, MPN,
package, unit price and candidate datasheet URLs. Requests sharing a parent
output directory are spaced five seconds apart. Stocked Basic/Preferred parts
with URLs sort first. A URL is not proof of a usable PDF: capture verifies that
later. Pin and pad helpers use the existing shared readers; custom pads refuse
before any native helper is invoked. Downloads and library bytes stay in the
retained run, outside the public repository.

Each attempt keeps prompt, response, statement, refusal text, provenance,
execution logs, partial captures, bundle and build when those phases run.
`generate-report.json` has schema `agent-ee.design-generate/1`, one row per
attempt, explicit bounds, durations, failed check records and the final state.
Compiler/capture refusal fields form stable `refusal_classes`; the exact message
is retained. An unlocated refusal is flagged as a tool gap. Short feedback goes
back to the author with the current statement. A search failure can first use
one automatic additional-seed attempt (`--search-retries`), reusing immutable
captures and changing only placement search parameters. This consumes the same
attempt and wall-time bounds. All external phases own bounded process trees.

`accepted` requires the build's typed qualification with `file_release: pass`.
It claims no supplier preview or physical test. Full engineering review is off
by default. Request it with `--review-profile PROFILE.json`; the ordinary
`build review --profile` path runs fresh checks, independent reviewers and
adjudication on the selected package. Its actual engineering qualification is
recorded. Blocking findings go back to the author within the same bounds;
incomplete review stops further seat calls. File release remains a distinct
claim from engineering review. Allow roughly 20 extra minutes and increase
`--max-seconds` accordingly.

`benchmark-report.json` uses schema `agent-ee.design-benchmark/1`. Designs run in
filename order, one at a time. Each row includes requirement hash, report path,
state, attempts to accepted (null on failure), total time, every refusal field,
failing stages and check records. It reports a final-coordinate outline
utilisation proxy: the projected union of linear courtyard bounding boxes,
divided by the stated rectangular outline area. This is not exact polygon
occupancy, routing density or proof that the outline can shrink. Missing native
candidates are explicitly unavailable. A complete benchmark can contain failures;
the CLI still exits nonzero unless every design is accepted.
