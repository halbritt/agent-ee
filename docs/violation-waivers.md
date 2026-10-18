# Exact native violation waivers

ERC and DRC accept an optional `params.waivers` path to a captured
`agent-ee.violation-waivers/1` file. The [owner decision of 2026-10-18](decisions.md#telemetry-pod-questions-third-batch-2026-10-18)
admits owner-recorded dispositions bound to the exact violation. Agents may
never grant waivers. Every list needs its own recorded owner approval before
it can affect acceptance; permission to implement this mechanism approves no
actual design violation.

A record has exactly `schema`, `waivers` and `approval`. Each waiver has:

| Field | Meaning |
| --- | --- |
| `check` | `erc` or `drc` |
| `type` | Exact native rule type |
| `items` | Complete, nonempty, sorted list of unique canonical item UUIDs |
| `location_hash` | SHA-256 from `violation_key`, described below |
| `reason` | Owner's nonempty reason for this disposition |
| `owner` | Owner identity, identical to the list approver |

`approval` has exactly `owner`, `disposition`, `waivers_sha256` and `reference`.
`disposition` must be `approved`. `reference` records where the owner approved
the list. `waivers_sha256` must equal `agent_ee.storage.identity(waivers)`:
SHA-256 of UTF-8 JSON with sorted object keys, no spaces and no nonfinite
numbers. Array order is retained. Changing any entry, including its reason,
invalidates the approval. Duplicate entries, unknown fields, duplicate JSON
keys and unapproved lists are refused.

This is an owner record supplied through the trusted coordinator input boundary.
Validation checks its structure, named approver and exact list digest. It does
not authenticate a human identity or verify an external signature. The coordinator
must supply the actual owner-authored record and approval reference; agents must
not manufacture either. The test suite's synthetic approvals exercise the format
and confer no design acceptance.

## Capture and writer boundary

The file requires a scope/2 or scope/3 immutable baseline. Declare it as an
input, for example at `owner/waivers.json`, and set the ERC and/or DRC check's
`params.waivers` to that path. Both checks can share one approved list.
Capture validates the file and adds it to baseline files; scope/3 also includes
it in the native check's immutable coverage inputs. It is hashed into the
candidate identity. Snapshot verification and campaign proposal validation
refuse changed bytes.

Give the file its own top-level immutable namespace, such as `owner/`. Capture
refuses a path in the same top-level namespace as any mutable writer input,
including the default board and schematic paths. A mutable alias carrying the
same waiver bytes is also refused. For implementation calls, both subscription
adapters overlay that entire namespace in `/work/design` with a read-only mount
from the captured inputs. `/candidate` is already read-only. The writer cannot
edit or rename this namespace inside its sandbox. Other immutable inputs keep
the existing campaign protection. Waivers cannot be added by editing a proposal:
the baseline, inventory and check criteria must remain unchanged.

## Exact matching and retained evidence

`agent_ee.violation_waivers.violation_key(kind, subject, category, report,
violation, sheet=None)` returns the four matching fields (`check`, `type`,
`items`, `location_hash`) or `None` for an ineligible native item. `subject` is
the captured schematic or board path. `category` is `violations` for ERC and
one of `violations`, `unconnected_items`, `schematic_parity` for DRC. ERC also
requires the native sheet record, including its `uuid_path`.

The location hash binds the check, subject, category, sheet instance/path,
native version and coordinate units, severity, violation description, and all
item UUIDs, descriptions and raw native x/y positions sorted by UUID. It uses
the same canonical JSON hash as the approval list. It has no distance tolerance
or UUID subset match. Even a changed position, rule type, severity, description,
UUID or sheet instance prevents a match. Native numeric representations are
retained exactly; a serialization change can therefore require a fresh approval.
ERC coordinates withheld by the existing sealed-version guard cannot be waived.
An item without UUIDs or finite millimetre positions remains unwaived. This
includes missing-footprint parity findings with empty item lists. Identical
duplicate native findings retain their raw counts and share the same exact key.

The native `erc.json` or `drc.json` is retained unchanged. Results keep
`details.by_severity`, `by_type` and `ignored_checks` for every native item, and add:

| Result field | Meaning |
| --- | --- |
| `waived` | Native items with `status: waived`, owner reason/identity, exact `waiver_key` and raw JSON locator |
| `waived_by_severity` | Counts of matched items at their original severities |
| `unwaived` | Number of unmatched native items |
| `unwaived_by_severity` | Counts of unmatched items at their original severities |
| `waivers_source` | Captured path/hash and verified approval, when a file is declared |

A check-owned `waivers.json` copy joins the native report in sealed evidence.
Every unwaived error or warning still fails. Without a file, `waived` is empty
and all reported violations retain their previous grading. ERC repair findings
include only unwaived violations. DRC placement projections keep their existing
ungraded connectivity behavior and still require a later full DRC. A tool crash,
missing report or invalid waiver input remains unknown coverage.

## Executable coverage

`tests.test_checks_erc` contains `test_waiver_exact_match` and
`test_writer_cannot_edit_waivers`. Real sandboxed native runs establish that one
UUID's disposition leaves another same-type violation failing, and that only
complete exact dispositions can pass. The DRC counterpart in
`tests.test_checks_drc` exercises the same contract. Tests also cover approval
and snapshot tampering, changed UUID/location/type/description/severity,
withheld coordinates, capture rejection of mutable paths and aliases, scope/3
coverage binding, and the writer's actual read-only mount enforcement. These
are original fixtures and synthetic approval records.
