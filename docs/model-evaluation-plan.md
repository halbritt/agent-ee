# Reviewer selection experiment, version 1

The owner rejected selection by convenience and authorized actual Codex, Claude
and Agy calls, including baseline runs. This plan is recorded before engineering
comparison calls. Tools remain enabled. Existing read-only candidate mounts and
separate workspaces suffice; no hermetic toolchain is intended.

Candidates: Claude Fable 5/high through Claude Max; GPT-6.1 Sol/high through
Codex ChatGPT; Gemini 4 Argon/high through Agy Google OAuth. These are candidates,
not an assumed quality ranking. Every role and adjudicator is explicit in a
versioned profile. No alternate account, model, provider or OpenRouter fallback.

## Independent expectations

Use the actual saved RC fixture's native connectivity and geometry, plus explicit
artificial exercise requirements. These component limits are exercise assumptions,
not claims about a commercial component's datasheet or the ECU. Reviewer packets
include requirements but exclude the following answer key and this plan.

| Pair | Control | Defect or uncertainty | Independent derivation |
| --- | --- | --- | --- |
| Connector mapping | External input on J1.1, output J1.2, ground J1.3 | External input required on J1.2, output J1.1 | Saved J1.1 connects /IN and R1.1; J1.2 connects /OUT and C1.1 |
| DC operating limit | /IN=12 V, /OUT held at ground by external fixture; R1 continuous limit 0.1 W | Same conditions at 48 V | 12²/10000=0.0144 W; 48²/10000=0.2304 W |
| Shutdown | /IN grounded at t=0; C1 initially 1 V, no load; /OUT<=0.01 V at 5 ms, R1=10k | Saved R1=100k | exp(-5)=0.006738 V versus exp(-0.5)=0.606531 V |
| PCB width | Minimum saved copper segment width 0.20 mm | Minimum 0.30 mm | Saved segments are 0.25 mm; inspect native board/pcbnew |
| Population | R1 fitted; functional source-to-output resistive path required | R1 DNP in saved schematic and board | Only R1 joins /IN to /OUT; unpopulated footprint is no electrical resistor |
| Verification adequacy | Ideal unloaded 1 V step at 1 ms, 0.62–0.65 V | Ask whether same circuit meets settling under an unspecified switched ADC load | Ideal tau=1 ms gives 0.632121 V; unspecified load/timing prevents a defensible loaded verdict |

Native ERC/DRC/netlist/geometry run freshly. Comparison scopes deliberately omit
functional pass/fail automation: reviewers must establish functional judgments,
not repeat a supplied failure flag. Native findings, if any, are scored separately.
No hard-coded expected finding text is used by the implementation.

Connector/DC pairs are development cases. Shutdown/width/population/verification
are held-out cases: do not tune prompts or selection thresholds from their results.
Run both circuit and physical lenses for every model on all 12 candidates, then
repeat the four development cases once (96 initial judgments total). Equal
300-second per-call limits; 1200 seconds per candidate; stop the affected harness
campaign on execution/provider failure. Preserve partial results. No retries of
failed engineering cases to improve scores. Infrastructure probes are separate.

Score against the derivations above, inspecting citations and reasoning, not
keyword matches: substantiated detections, consequential misses, unsupported
blocking allegations, correct uncertainty, useful experiments, protocol failures,
wall time and reported usage. A role need not duplicate the other role's finding;
report both individual performance and pair coverage/shared misses. Do not claim
statistical model superiority from this small convenience sample.

## Adjudication

Separate actual calls for each candidate adjudicator on evidence-grounded supported
singleton versus clean majority, unsupported accusation, conflicting calculation,
and stale repair claim. Preserve the input findings and all decisions. Acceptance
requires the singleton survive and unsupported claims not become established
facts. A repair needs a fresh changed candidate, never a prose assertion.

## End-to-end runs

Run a fresh donor baseline under each profile. Run control, saved native defect,
and disposable repair under the selected profile, with fresh ERC/DRC/ngspice and
actual reviewers/adjudication. If results do not justify a winner, publish an
explicit provisional profile and explain the unresolved selection evidence.

Changes to this plan or prompts after held-out results require a new evaluation
version. Existing historical evidence stays tied to its original implementation.
