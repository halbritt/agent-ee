# Declared local decoupling copper and saved-board paths

`design compile` emits `local-decoupling.json` (`agent-ee.local-decoupling/1`)
from the design's exact decoupling endpoint pairs and plane declaration. The
build plan passes this immutable document into placement as `local_decoupling`.
The requirement map adds a typed `decoupling_loops` check with
`params: {"contract": "local-decoupling.json"}` to the decoupling requirement.
This check is also usable in an ordinary captured scope/3 requirement map.

The document contains `relations`, `planes` and boolean `tie_other_pads`.
Each relation has a unique `id`, `supply` and `return` pairs in device/capacitor
order. Each endpoint is `{reference, pad, net}`. A plane is `{net, layer}`.
References and net names are data; no component or board recipe is selected.

After an eligible placement and pre-routing plane production, each relation
gets supply and return legs on a copper layer shared by its endpoints. A capacitor pad on
a declared plane net gets a nearby through via when its surface differs from
the plane layer. The device return leg therefore leads to the capacitor's
plane entry. The via search uses the side of the capacitor away from the device
and keeps every generated via clear of all pads and other generated legs.
The final report retains the actual bypass paths and plane-entry UUIDs;
shared return pins and copper added by the router need separate attribution. Optional other-pad ties give remaining
surface pads on plane nets their own short track and via where one fits.
The compiler leaves `tie_other_pads` false: the example controls completed with
and without them, and the additional ties increased routing time. Required
capacitor plane entries remain enabled. Explicit local contracts can select
the optional policy for a separately measured workload.

The local search uses effective native net-class routing sizes and clearance,
bounded visibility paths around copper and every undeclared pad, including
same-net pads, and radial via escapes outside all solder lands. Each generated
via keeps effective net-class clearance from every pad on every copper layer.
Generated tracks may touch only their declared endpoint pads. A separate native
shape check rejects violations even when native DRC permits same-net contact. A wide class may neck into a small land down to the unchanged
board minimum. Paths are bounded to 12 mm; via escapes to 2 mm. These are finite
search budgets, not electrical acceptance limits. Native DRC grades generated
copper before routing. New errors reject that candidate, retaining the reason
and seed evidence; the placement portfolio continues. Exhaustion fails the job
with a relation-named reason. Required local copper is never silently omitted.
Custom rules, board edges and rule areas remain subject to native DRC.

Generated tracks and vias are locked. The router receives `--protect locked`
for a track-free source seeded by this operation. Final validation checks each
generated item's UUID, geometry, net and lock state after native refill/save,
and repeats the via/pad and declared-track contact checks. Identical locked
segments shared by relations are reused, with one UUID in the protected inventory.
It also measures the final loops and rejects a candidate with broken copper,
an unknown measurement or an exceeded declared loop limit. The package check
repeats the measurement on its saved board. Seeding and its DRC share the
candidate's routing time allocation; a local deadline can advance the portfolio.
The acquisition recipe retains its original producer and evidence identity.
`local_return_seed` refuses `connections`, `local_loops` and `decoupling_loops`
in design-declared mechanics models, naming the field. Those fields cannot
select the acquisition board's fixed endpoints. Use the separate
`local_decoupling` contract for declared electrical relations; this keeps
mechanics custody and the historical acquisition recipe distinct.

The check loads the final saved board and uses native KiCad
`GetConnectedTracks` and `GetConnectedPads`, plus collisions with saved filled
zone shapes. A common pad is the junction between tracks meeting inside it.
Neither a matching net name nor pad separation creates a graph edge. A broken
leg or missing required capacitor plane connection is a defect.

Lengths follow track centrelines, with native-connected contact projections to
pad centres. Each through via or plated land pays the full board thickness.
When a return uses a plane, entry and exit must lie on the **same filled
island**. The lateral hop follows a visibility path inside its saved polygon,
including holes; the report retains that path. Plane travel is never free.
`loop_length_mm` sums the two external copper legs; component internals are
excluded. Bypass flags report whether the pin can reach plane copper without
passing through the selected capacitor pad. Bypass itself is reported, not
an implicit additional acceptance rule.

The optional design `max_loop_mm` becomes a hard relation limit with the basis
**Agent-selected measurement requirement; not an owner or datasheet ruling**.
It is copied into the immutable contract and stated in the generated spec.
Without it, numeric measurements are `info`; the typed check can still pass
the hard continuity requirement. A supplied limit cannot change clearance,
placement limits, or any existing gate. Examples intentionally retain their
existing limits without inventing new loop acceptance numbers.

This implementation supports two copper layers, SMD pads on one face, and
plated through-hole pads on both copper layers. Plated pads on a declared plane
layer serve as their own plane entries. Legs use a common copper layer;
incompatible layers, missing/ambiguous pads, unsupported pad attributes or
malformed plated holes are refused during compilation with relation and pad
names, before spending a placement budget. Missing or ambiguous endpoints, unsupported arcs, unfilled planes and
plane searches exceeding 512 polygon vertices refuse measurement instead of
approximating a pass. These are geometric measurements, not an inductance,
impedance, EMC, physical-operation or fabrication-readiness claim.
Plane visibility uses lazy A* with at most 50000 tested edges; exhaustion is
an unknown measurement and cannot pass a requirement.

The routing receipt calls its input `locked_local_copper_from_track_free_input`
when the plane producer started from a track-free board and local copper was
seeded before routing. The job identity includes the contract, generator,
loop checker, path geometry and borrowed acquisition visibility primitive.
A build plan carrying `local_decoupling` must carry a typed loop check bound to
that same contract; empty designs omit the placement binding.

The pinned kicad-route implementation differs from its `docs/decoupling.md`:
it suppresses a track/via link whenever both items touch a common pad, including
junctions outside that pad. This checker suppresses the link only on layers
where the items meet inside the pad. The kicad-route audit remains read-only
and unchanged; compare its selected pins and definition with the declared typed
relations before interpreting differences. A ground-only plane cannot make
both sides of a kicad-route cell `respects`.
