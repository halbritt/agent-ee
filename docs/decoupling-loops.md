# Declared local decoupling copper and saved-board paths

`design compile` emits `local-decoupling.json` (`agent-ee.local-decoupling/1`)
from the design's exact decoupling endpoint pairs and plane declaration. The
build plan passes this immutable document into placement as `local_decoupling`.
The requirement map adds a typed `decoupling_loops` check with
`params: {"contract": "local-decoupling.json"}` to the decoupling requirement.
This check is also usable in an ordinary captured scope/3 requirement map.

The document contains `relations`, `planes` and boolean `tie_other_pads`.
The compiler also emits derived `banks`: `{id, relations}` records. Relations
sharing a device supply or return pad join the same bank transitively. Statements
need no bank field. Legacy contracts without `banks` derive the same components
on read; a supplied bank list must match those components exactly.
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
copper added by the router and authored plated device pads need separate attribution.
Each bank uses star legs from each declared device pin to its capacitors.
Shared same-net device-leg copper within a bank is allowed; escape tracks still
meet device legs only inside a declared capacitor pad. Every capacitor retains
its own plane entry beside its return pad, away from its device leg. A capacitor
entry shared by multiple relations is generated once. The generator gives no
device pin a plane entry of its own. A device plane pad also declared as a
capacitor entry is refused at compile with the relations and pad named.
Optional other-pad ties give remaining
surface pads on plane nets their own short track and via where one fits.
The compiler leaves `tie_other_pads` false: the example controls completed with
and without them, and the additional ties increased routing time. Required
capacitor plane entries remain enabled. Explicit local contracts can select
the optional policy for a separately measured workload.

The local search uses effective native net-class routing sizes and clearance,
bounded visibility paths around copper and every undeclared pad, including
same-net pads, and radial via escapes outside all solder lands. The search retains 40 radial directions at six fixed radii, followed by a
0.1 mm grid inside the same 2 mm disk (at most 1,496 centres before deduplication
and the far-side filter);
paths still stay within 2 mm and every pad, edge and copper rule remains in force. Each generated
via keeps effective net-class clearance from every pad on every copper layer.
Generated tracks may touch only their declared endpoint pads. Same-net
device legs in one bank may share copper; other distinct legs may meet only
inside a declared shared pad. Vias may never touch another generated leg. A separate native
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
segments shared by relations in one bank are reused, with one UUID in the
protected inventory. Reuse is explicitly restricted to that bank; identical
segments from different banks remain distinct and reach the cross-group guard.
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
excluded. The primary bypass flags are in the result's `banks` array. Each bank lists its
relations and supply/return device pins. `return.pin_bypasses_bank` reports a
path to the plane after all same-net capacitor return pads in that bank are
removed together. `device_return_reaches_plane_only_through_bank_capacitors`
is true when every device return reaches the plane and no such bypass exists.
If a return reaches no plane, the positive statement is null with
`positive_statement_status: not_applicable`; `device_returns_without_plane`
names those endpoints. This differs from a measured bypass (false positive
statement) and an unknown measurement (`positive_statement_status: unknown`).
The report retains each bypass path and entry UUID. Travel through another
capacitor of the same bank is allowed. Per-relation supply, return, capacitor
plane-access and loop lengths remain separate and carry their bank ID.
Single-relation banks also retain the round-2 `pin_bypasses_capacitor` and
`bypass_paths` fields. Bypass remains information, not an implicit acceptance
threshold; broken continuity, unknown measurements and supplied limits still
decide eligibility.

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

The round-3 bank rule supersedes the round-2 shared-return compile refusal.
Capacitors in parallel on a shared device pin are ordinary supported input.
The kicad-route audit still uses its own per-capacitor topology and can label a
legitimate path through another bank capacitor `bypassed`. Its result is not the
typed bank result.

Local search counts and supported design size

The documented design inventory is 1,024 components, 2,048 nets and 8,192
terminals (docs/design-size-limits.md). There is no additional per-net copper
item count ceiling in this check. For each net, native track/pad connectivity
is traversed from every declared relation endpoint before constructing the
measurement graph. This retains a remotely connected bypass via, but excludes
disconnected remote items. It does not clip connectivity to a geometric radius
or merge disconnected plane islands.

The producer counts only obstacles intersecting a search corridor. Corridors
expand from the direct leg to a final region covering the unchanged 12 mm path
bound. A path is accepted only inside the region whose obstacles were counted.
Diagonal tracks use short conservative rectangles, avoiding a large empty
triangle in their whole-track bounding box. Existing same-net tracks are
obstacles too, with openings only at declared shared pads. Full native shape
verification remains the independent guard.

The borrowed visibility primitive retains its local work guards: 1,024 boxes
and 512 vertices. These are local geometry search budgets, not whole-design
pad limits; a dense candidate may still exhaust them and advance the portfolio.
The checker retains its documented filled-plane visibility work guards and
180-second typed execution budget. None of these resource guards supplies a
passing measurement on exhaustion.

Redundant router stubs

The producer does not create dangling stubs in the retained timer case. Native
DRC flags an unlocked router-created 0.0707 mm spur whose endpoint lies in the
interior of a locked generated segment. A generated-only diagnostic has no
such warning. Removing just the router spur preserves zero native errors and
zero opens; splitting the locked receiver also clears the warning, but the
producer keeps its protected UUID/geometry contract.

Final validation can trial removal of warned unlocked router tracks whose two
endpoints are already on one generated straight track of the same net/layer,
with no greater width. The original board and DRC are retained. A fresh native
process prepares a disposable trial. The trial is adopted only with unchanged
protected native content and generated items, zero errors, zero opens and no
dangling-track warning. Every ordinary final plane, locality, loop, length and
impedance check still runs on the resulting board. Kicad-route is unchanged.

Round-5 routing policy and geometry

The compiler emits the same routing net classes as main: only its derived Default
class, without an agent-selected Power class or power-routing-default.json.
Local tracks use that class's preferred width, capped by their endpoint land
widths and bounded below by the unchanged fabrication minimum. On the supplied
examples this is 0.2 mm. This is an inherited routing preference, not a new
current-rating or impedance requirement. It creates no rule for router-added
copper. Local copper has no dependency on a Power class.

Generated copper that must stay unjoined keeps at least the native board's
minimum copper clearance (`BOARD_DESIGN_SETTINGS.m_MinClearance`, serialized as
`board.design_settings.rules.min_clearance` in the authored project). This is
0.1 mm on both supplied examples. The fabrication floor replaces round 5's
0.025 mm topology-only gap; etching can bridge such a small gap even on one net.
The search retains its 0.025 mm margin **on top of** the floor. It uses the floor
rather than the net-class preference for same-net separation. Foreign copper
retains effective class clearance, and vias still keep full effective net-class
clearance from every pad, including same-net pads.

The floor covers escape tracks/vias against device legs or other relations,
different banks, and every distinct leg allowed to meet only inside a declared
shared pad. Same-bank device legs already allowed to share copper remain exempt,
as do items of the same generated leg. Verification clips allowed shared-pad
interiors out of conservative native copper polygons before checking the floor.
Near misses below the floor count as meetings even without geometric contact.
The saved-board separation report lists every non-exempt pair/layer and its
conservative minimum distance, plus exemptions and pairs on separate layers.

Via sites are checked using native pad and copper shapes rather than inflated
rectangular pad corners. A bounded grid resolves windows between the radial
samples. The far-side restriction, 2 mm escape path bound, 12 mm leg bound,
locked-copper identity and bank rule are unchanged; verify_geometry additionally
enforces the fabrication separation floor.
A search refusal is not a proof that no geometrically legal layout exists.
