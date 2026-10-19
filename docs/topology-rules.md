# Requirement topology rules

`topology_rules` (AGENTEE-60) checks a design's native connectivity against a
captured, owner-authored rule file. Each rule is tagged with the baseline
requirement it covers. The rules state structure: which pin sits on which net,
how many parts of a class join two nets, what lies in series with a pin, what
must not join two nets, which values must be equal, and which set of parts,
wired how, must be present. The check does not parse prose; the design dialog
or the owner writes the rules. It does not judge electrical adequacy,
tolerances, proximity, routing or geometry.

The matcher idea (role assignment by bounded backtracking with bijective net
roles) comes from the unlicensed tscircuit `pcb-generation-benchmark01`
evaluator, and the worked rule shapes from PCBSchemaGen examples. This is a
clean-room implementation: no code, rule files or benchmark netlists were
copied. The fixtures in [`fixtures/topology-rules/generate.py`](../fixtures/topology-rules/generate.py)
are original.

## Check parameters

```json
{"id": "rules", "kind": "topology_rules",
 "params": {"rules": "topology-rules.json", "netlist_check": "netlist"}}
```

`rules` is a captured path. It becomes an immutable baseline dependency.
`netlist_check` names an earlier `netlist` check. The check reads that check's
native XML through the strict shared inventory reader and refuses it unless the
retained `connectivity.json` equals the projection of the same XML. A missing,
failed or mismatched prerequisite makes execution `failed`.

The set of requirement IDs tagged in the rule file must equal the set of
baseline requirements that map this check, and each such requirement's source
must be captured. Otherwise the check is `unsupported`, and scope/3 capture
refuses it. Each rule publishes one predicate, `topology-rule:<rule id>`.

## Rule file

`agent-ee.topology-rules/1` has exactly `schema`, `classes` and `rules`.
Duplicate JSON keys and non-finite numbers are refused.

`classes` maps exact `library:symbol` IDs to `{class, terminals}`.
`terminals` maps terminal names to distinct physical pin numbers. The class
set is closed:

| Classes | Terminal swap when matching | Terminals |
| --- | --- | --- |
| `resistor`, `capacitor`, `inductor`, `ferrite`, `fuse` | Allowed | Exactly two |
| `diode`, `led`, `polarized_capacitor` | Refused | Exactly two |
| `connector`, `ic`, `transistor`, `switch`, `crystal` | Refused | One or more |

A populated part is *classified* when its symbol has a class, the declared pins
exist on its native pin inventory (exactly, for two-terminal classes) and every
declared pin has a native net. Any other populated part is *unclassified*. DNP
parts are excluded from every population rule and listed in `excluded_dnp`.

Every rule has `id` (`[A-Za-z0-9][A-Za-z0-9_.-]{0,63}`, unique), `requirement`
and `type`, plus the fields of its type and no others:

| `type` | Fields | Satisfied when |
| --- | --- | --- |
| `pin_net` | `terminal {reference, pin}`, `net` | The pin is on the net |
| `pin_no_connect` | `terminal` | The pin is alone on its net; the native no-connect marker is recorded |
| `count_between` | `class` (two-terminal), `nets` [A, B], `min` ≥ 1, optional `max` | `min` ≤ count of classified parts of the class with one terminal on each net ≤ `max` |
| `series_element` | `terminal`, `net`, `class` (two-terminal) | The pin's net differs from `net` and a part of the class joins them. A pin tied directly to `net` is a violation |
| `forbidden_between` | `class` (any), `nets` | No classified part of the class has terminals on both nets |
| `element_between` | `class` (two-terminal), `nets`, optional `value` | A part of the class joins the nets, with an equal value if one is given |
| `value_equal` | `references` (two or more) | All named parts have equal values |
| `expected_components` | `match`, `components`, optional `fixed_nets`, `max_visits` | See below |

A net is named by its exact native name, such as `/VBUS+`. A name without `/`
also resolves to the one native net with that basename. An absent or ambiguous
name is unknown.

Values are compared only for equality. A value is a plain written quantity:
`4.7k`, `4k7`, `120R`, `10 kΩ`, `100nF`, `4n7`. Case matters: `m` is milli
and `M` is mega. Units must agree when both values carry one. Any other text,
including an empty value, is unreadable.

## Expected components

`components` lists roles. Each role has `role`, `class`, `terminals` (terminal
name to net role) and optionally `value` and an exact `symbol`. Net roles are
abstract names. The matcher assigns each role a distinct classified part and
each net role a distinct native net, keeping the net-role map a bijection.
`fixed_nets` pins net roles to named native nets before the search. Two-terminal
passives may match with their terminals swapped; the result lists them in
`mapping.swapped`. Polarized parts and connectors match only as declared, so a
reversed LED is a defect. Terminals that a role does not list are unconstrained.

`match: "exact"` requires the classified kind counts to equal the expected
counts and every populated part to be classified. `match: "embedded"` looks for
the roles inside a larger design and only requires enough parts of each class.

Roles are tried most-constrained first (fewest candidates, then most terminals,
then role name), and candidates in reference order, so the mapping is
deterministic. Every candidate attempt counts as one visit. The default bound
is 20,000 visits (`max_visits` 1 to 1,000,000).

| Situation | Status |
| --- | --- |
| A consistent assignment uses only readable, equal values | `satisfied`, with `mapping` (`roles`, `nets`, `swapped`) |
| Exact match: more parts of a class than expected, or fewer with no unclassified part | `violation`, naming the kind counts |
| No consistent assignment exists and no unclassified part could supply a role | `violation`, with the deepest partial assignment |
| The only assignment depends on a missing or unreadable value | `unknown`, naming the parts |
| Unclassified parts block exactness, or could supply a missing role | `unknown` |
| The visit bound is reached | `unknown`, recording `max_visits` and `visits` |

## Unknown, never pass

An absent reference, pin or net, an ambiguous net, an unreadable value or an
exhausted bound makes a rule `unknown`. An unclassified populated part that
touches both nets of a count, series, element or forbidden rule, or has a pin
without a net, can turn a would-be defect or absence into `unknown`. It never
turns one into a pass.

## Result

`work/<id>/topology-rules.json` (`agent-ee.topology-rules-result/1`) holds one
record per rule with its status, reasons, resolved nets and the matched or
offending references. It also lists unclassified parts with reasons and DNP
exclusions, and the SHA-256 of the native XML, `connectivity.json` and the rule
file. Any violation makes the check `ok/defect`. Otherwise any unknown rule
makes it `unsupported/unknown`, and all rules satisfied make it `ok/pass`.
A rule file that does not parse or validate makes execution `malformed`.

## Limits

These are exported-net structure rules against owner-declared classes. The
oracle does not know whether a requirement's prose is fully covered by its
rules; the owner reviews that mapping. It does not check proximity, routing,
current capacity, tolerance or ratings.
