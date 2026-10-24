# Whole-board placement job (AGENTEE-515)

Status: first vertical slice of the architecture adopted by chief 531
(decision `b4a5857a`, see [placement-architecture.md](placement-architecture.md),
"Chief decision, 2026-10-24"). It is a working workflow, not an accepted
production default: the existing `placer_runner` planning boundary, the `/1` and
`/2` candidate-set schemas and every live caller (`placer.pack`,
`blocks.pack_block`, `block_variants`, `block_variant_stage`, `reserved_area`,
`pod_driver`, `placement_adapter`) are unchanged. There is no automatic Python
fallback and no superiority or manufacturing claim.

## One entry point

```sh
python3 -m agent_ee.placement_job run job-config.json --out JOB_DIR [--resume]
python3 -m agent_ee.placement_job metrics BOARD.kicad_pcb --groups groups.json --intent intent.json
```

The config (`agent-ee.placement-job-config/1`) names every external input by
path **and SHA-256**; a mismatch refuses the job before any work. Fixture bytes
stay outside the public repository.

```json
{"schema": "agent-ee.placement-job-config/1", "budget_s": 600,
 "inputs": {"board": {"path": "prepared/board.kicad_pcb", "sha256": "<64 hex>"},
            "project": {"path": "prepared/board.kicad_pro", "sha256": "<64 hex>"},
            "intent": {"path": "prepared/intent.json", "sha256": "<64 hex>"},
            "groups": {"path": "prepared/groups.json", "sha256": "<64 hex>"}},
 "search": {"seeds": [{"seed": 0, "start": "scattered"}, {"seed": 6, "start": "clustered"}],
            "steps_per_seed": 1000000, "shortlist": 3},
 "router": {"checkout": "/opt/kicad-route", "budget_s": 240}}
```

`budget_s` is ONE overall budget from job start: preparation, search, judging,
export (routing) and final validation all draw on it; there is no phase cap.
`job-report.json` records `setup`, `import`, `prepare`, `search`, `judge`,
`export` and `final_validation` separately. Queue time (admission) is outside
the process and must be recorded by the caller.

## Order of work

1. **Runtime preflight**: pcbnew import, `kicad-cli version`, the Rust engine
   (`placement_rust_runtime.resolve_executable`, same discovery as the planner)
   and `routing_adapter.preflight` on the router checkout (`problems == []`).
2. **Native capture, once** (`placement_probe.extract` plus pcbnew bounding
   boxes). Identity is the full native footprint UUID; `native_ref` is carried
   unchanged. Each footprint gets a role:
   - `movable`: packed and searched.
   - `fixed_body`: locked footprints and fixed poses that draw a body.
   - `fixed_copper_mark`: a fixed pose whose pads are unnetted and which draws no
     fab, silk or courtyard (a copper fiducial). It is a copper obstacle with its
     native pad clearance, never a body.
   - `silk_only`: padless, zone-free, every graphic on a silkscreen layer. Only
     these are excluded from packing.
   Footprint rule areas are refused (not modelled).
3. **Occupancy.** A proven courtyard is used as such. A present but unproven
   (malformed) courtyard is a hard refusal. A missing courtyard is a metadata gap:
   the footprint gets `derived_occupancy` with its provenance rung, in order
   `fab+pads`, `silk+pads`, `pads`, and the source drawing UUIDs. `User.Drawings`
   is classified as documentation, never as a package outline. Separation of
   derived envelopes is **not** proof of package body separation.
4. **Prepared Rust model and incremental search** (`crates/placement-engine/src/job.rs`,
   schema `agent-ee.placement-job-input/1`, one process for the whole seed
   portfolio). `prepare` validates once: full-UUID identity with deterministic,
   collision-checked aliases (`fp000`... in UUID order, never a UUID prefix),
   roles, occupancy labels, rules (largest net-class clearance, per-pad native
   clearance, copper-to-edge clearance, solder-mask minimum web), groups,
   proximity rules and edge rules. `SearchState::evaluate` computes the delta of a
   move (single part, quarter turn, rigid group translation) from a bucketed
   spatial index queried at the **new** pose plus the old contributors, with
   movers paired among themselves; `commit` is generation-checked and atomic,
   `reject` leaves the committed state unchanged, and a stale trial is refused.
   `full_evaluate` recomputes everything independently; every retained state is
   checked against it, and `verify_every` enables a per-commit differential check.
   All hard verdicts and objective terms are integer nanometres (areas i128).
   Seeds are independent named `splitmix64/agent-ee-515-v1` streams; scattered and
   clustered starts are explicit. The engine returns a shortlist of hard-feasible
   states. When none exists it returns a **diagnostic shortlist**: least-infeasible
   states with their exact named violations, labelled `feasible: false`.
   An `evaluate` request re-evaluates explicit pose vectors (predicted native pad
   positions, terms, violations) without searching.
5. **Shortlist native checks before promotion.** Each candidate is applied to a
   disposable native board by UUID (pcbnew), saved, the frozen project bytes are
   restored next to it, and it is reloaded. Integrity requires the same UUID set,
   refs, sides, locks, FPIDs, pad UUIDs/numbers/nets and graphics; every saved pose
   equal to the engine pose; every native pad exactly where the engine transform
   put it; unchanged fixed parts; and unchanged non-footprint board content (the
   only normalization: SaveBoard's standard layer user names). Native DRC
   (`kicad-cli pcb drc --severity-all`) errors are split into **pre-existing**
   (all items in one footprint and the same finding on the unchanged input) and
   **placement-introduced**.
6. **Incumbent custody.** `incumbent.json` is replaced atomically (`os.replace`)
   only by a strictly better candidate with preserved integrity, engine-feasible
   poses and zero placement-introduced errors. A failed trial, crash or worse
   candidate never touches it, and candidate directories are never overwritten.
   Without a valid incumbent the best diagnostic candidate is kept separately in
   `best-diagnostic.json` and may be routed **only as an exploratory run**; the
   report then says `NO valid incumbent`.
7. **Pinned router process** on a fresh copy (`kicad-route run`, the adapter's
   argv shape, `--protect existing`, `--diff-pairs auto`, the checkout's own
   state). The sandboxed `routing_adapter.step` refuses a candidate without a
   schematic, so a schematic-less board is routed by this direct process call;
   the router's preflight identity is recorded either way.
8. **Final checks, independent of the search**: reload the routed board; footprint
   UUIDs, refs, poses, sides, locks and pads must be unchanged by routing; native
   DRC with `--refill-zones`; opens; errors split into pre-existing,
   placement-introduced and routing-introduced; warnings by type; measured route
   length (segments and arcs, by layer), vias, geometric via-in-pad (via centre
   inside native pad copper), grouping and declared-edge setbacks. Pre-existing
   errors stay FAILED physical checks: the report claims `accepted: false,
   clean: false`.

## Cancellation, deadline and resume

A `CANCEL` file in the job directory stops the engine at its next check (every
256 steps by default) and the controller between candidates. The engine also
stops at the overall deadline; unstarted seeds are reported `not_started`.
Finished seeds are checkpointed atomically (`checkpoint.json`, bound to the job
identity: problem, config and engine hashes). `--resume` refuses a checkpoint of
another identity, skips completed seeds and restarts unfinished ones from their
start: it is restart at a seed boundary, not continuation of an interrupted PRNG
state. Diagnostic candidates of earlier seeds are not carried across a resume.

## Tests

`tests/test_placement_job.py` (original synthetic boards built through pcbnew)
and the `job::tests` Rust module. The esp_prog capture test reads the external
AGENTEE-513 bundle by SHA-256 when `AGENT_EE_ESP_PROG_PREPARED` names it.

## Origins

The transaction, spatial-query and seeded-restart semantics follow KRT v0.23.7
mechanisms selected in AGENTEE-514. They were reimplemented after source
inspection (not clean-room); `job.rs` carries the upstream MIT notice and
`docs/file-origins.json` origin `KRT-515` records the attribution.
