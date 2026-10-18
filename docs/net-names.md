# Net-name provenance

`net_names` (AGENTEE-44) checks that owner-declared net names survive schematic
generation, and that names the generator invented are never silently merged
with or split from them. It consumes a preceding successful `netlist` check, its
exact `design.xml` and `connectivity.json`, a captured owner policy and the
generator's name-level annotation. Parameters are exactly `netlist_check`,
`policy` and `levels`. The check reads captured bytes only, launches no tool and
renames nothing.

The idea follows atopile's net-name levels (required versus suggested names and
its duplicate/conflict checks, MIT). No atopile code or design files are used.

## Inputs

Each name is a level: **required** (owner-declared in the requirements) or
**suggested** (derived by the generator). Names use the generator grammar
`[A-Za-z0-9_+.-]{1,64}`, or the `__agent_ee_<sha256>` labels the resolver
assigns to unnamed hierarchical segments.

The policy is an immutable baseline dependency with schema
`agent-ee.net-name-policy/1` and exactly these fields:

- `requirement`: `{id, source: {path, locator}}`, matching the check's sole
  mapped baseline requirement and captured source.
- `required`: `{name, anchors}` entries. Each anchor is an exported
  `{reference, pin}` terminal that the owner states belongs to the named net. Every
  required name needs at least one anchor. Names are unique. A terminal that
  anchors two required names is a contradictory declaration and is refused.

```json
{
  "schema": "agent-ee.net-name-policy/1",
  "requirement": {"id": "names", "source": {"path": "requirements.md", "locator": "Net names"}},
  "required": [
    {"name": "VBUS", "anchors": [{"reference": "J1", "pin": "1"}]},
    {"name": "SDA", "anchors": [{"reference": "U1", "pin": "1"}, {"reference": "U3", "pin": "1"}]}
  ]
}
```

The level annotation is mutable candidate input, written by the generator with
the design. Its schema is `agent-ee.net-name-levels/1`; `levels` maps each name to
`required` or `suggested`. Duplicate keys and other values are malformed.

```json
{
  "schema": "agent-ee.net-name-levels/1",
  "levels": {"VBUS": "required", "SDA": "required", "GND": "suggested", "USB_P": "suggested", "USB_N": "suggested"}
}
```

## Evaluation

Native nets are grouped by basename, the exported name after its last `/`, so
`/a/SDA`, `/b/SDA` and a global `SDA` are one name on three nets. KiCad's own
names for unlabelled nets (`Net-(…)`, `unconnected-(…)`) carry no generator or owner
name. They are listed under `unnamed` and are not judged. Each name gets one
record with its level, native nets, anchors and result.

| Condition | Result |
| --- | --- |
| A required name is on no native net | Defect `required_absent` |
| Two or more distinct native nets carry a required name, for example `/a/SDA` and `/b/SDA` | Defect `required_duplicate` |
| The anchors of one required name resolve to more than one native net | Defect `required_split` |
| One native net holds the anchors of two required names, for example VBUS and 5V | Defect `required_conflict` on both names, with the shared net |
| An anchored net's native name is not the required name | Defect `required_displaced` |
| The annotation marks an owner-required name `suggested` | Defect `level_downgraded` |
| The annotation marks `required` a name the owner did not declare | Defect `required_unsupported` |
| A named native net has no level annotation | Unknown `level_missing` |
| An anchor reference or pin is absent, or has no exported net | Unknown `anchor_unresolved` |
| Two or more native nets carry one suggested name | Information: deterministic disambiguation |

KiCad keeps one label as a net's name. When a port joins a parent VBUS segment
to a child segment labelled 5V, the native net is `/VBUS`, and 5V does not
appear. The VBUS and 5V anchors show that both names were declared for one net.

For a suggested collision, colliding nets are taken in exported-name order and
each is proposed `<name>_<k>`. If a proposal already exists as a name, the next
index is used. A trailing `_P` or `_N` stays last, so `USB_P` becomes `USB_1_P`
and `USB_2_P`. A pair index is used only when neither polarity exists, which
keeps both halves of a colliding pair on the same index. The record has
`status=informational`, `severity=info`. It never fails the check, and the
design is not changed. A proposal over 64 characters cannot be formed and is
unknown.

Any unknown record makes the check `execution=unsupported`, `outcome=unknown`,
even if another name has a defect; the defect records remain in the report.
Otherwise any defect gives `defect`, and none gives `pass`. Missing, failed,
malformed or mismatched native prerequisites give `execution=failed`. A
malformed level annotation gives `execution=malformed`. Both have outcome
`unknown`.

## Evidence

`net_names.json` holds every record, the unnamed nets and the candidate, XML,
connectivity, policy and annotation hashes. Existing sealed coordinator findings
and public verification cover it. Scope/3 declares the predicates
`net-names:levels` and `net-names:required:<name>`. The policy is an immutable
baseline input. The annotation and design remain mutable candidate input.

`tests/test_net_names.py` emits original hierarchical designs with the
[schematic emitter](schematic-emitter.md) and the owned generation library. It
then runs the sandboxed native netlist export and this check through the
coordinator:

- The control joins SDA from two child sheets through a root segment and repeats
  `USB_P`/`USB_N` on both children. It passes, with `USB_1_P`/`USB_2_P` and
  `USB_1_N`/`USB_2_N` reported as information.
- Local SDA labels on both children give `/a/SDA` and `/b/SDA`. At the required
  level this is a defect. At the suggested level it is disambiguated to
  `SDA_1`/`SDA_2`.
- A port named 5V on the parent VBUS net gives one native `/VBUS` net. With VBUS and
  5V both required, both names get `required_conflict`, and the defect witness
  passes witness validation. The control's result is rejected as a witness.
- A missing annotation is unknown. A malformed annotation is malformed, and
  altered XML or a failed producer fails. Policy, annotation and report tampering
  is detected. Pure tests cover each defect code, unresolved anchors, pair
  disambiguation around existing names and the strict input shapes.

## Limits

Names are compared by native basename, after KiCad has chosen one name per net.
The check does not read label geometry and does not run ERC. It cannot see a
label KiCad dropped unless an anchor places that name's net. Anchors are exported
references, so reannotation that moves a reference moves the anchor. Only
`_P`/`_N` pair suffixes are recognized. Physical connectivity against the intent
is [netlist parity](netlist-parity.md). Owner-declared terminals and loads are
[hardware interfaces](hardware-interface.md).
Parity can also capture this same required-name policy through `net_name_policy`
to anchor its bounded exact search. Suggested level annotations do not select
parity anchors.
