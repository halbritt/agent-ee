# Scope configuration reference

A scope declares what to capture, what to check and what the reviewers can see.
It is not a discovery engine for every dependency in a design. Include the
native project, rules, library tables, actual libraries, applicable requirements
and models needed to support the declared engineering claims.

Start from [the control scope](../fixtures/rc/control.scope.json) for a complete
passive example. [The donor scope](../scopes/donor.json) demonstrates an external
project with captured stock and project libraries. The latter contains paths
specific to the original workstation; adapt them before using it elsewhere.

## Top-level fields

| Field | Type / default | Meaning |
| --- | --- | --- |
| `schema` | Required string | Exactly `agent-ee.scope/1` |
| `name` | Required nonempty string | Name displayed in reports |
| `scope` | Required nonempty string | Review purpose, boundary and relevant operating conditions |
| `inputs` | Required list of input mappings | Source files/trees to copy into the candidate |
| `schematic` | Required relative path | Captured root `.kicad_sch` |
| `board` | Required relative path | Captured saved `.kicad_pcb` |
| `checks` | Required nonempty list | Ordered check specifications |
| `requirements` | List, default `[]` | Captured input-relative text files placed first in the packet |
| `limitations` | List of strings, default `[]` | Explicit omitted conditions or analyses retained in the report |
| `packet_inputs` | List; default root schematic and board | Captured input-relative source text selected for reviewers |
| `packet_artifacts` | List, default `[]` | Required generated evidence paths, relative to the run |
| `packet_max_bytes` | Integer, default 240000 | Total artifact-text budget, allowed range 1024–1000000 |
| `review_profile` | Explicit profile object; alternatively pass `--profile FILE` | Harness/model/effort/timeout for every reviewer and adjudicator |

Both native paths and all requirement paths must actually be captured. Every
review currently declares a board and a schematic, including a narrow circuit
review. Schematic-only operation is not implemented. Native parity expects the
schematic beside the board under the matching basename used by KiCad; the
scope's separate `schematic` setting does not override KiCad's parity file
selection. Preserve the actual project naming relationship.

The scope is validated by Python code, not a published standalone JSON Schema.
Some shape/type checks occur only when the relevant field is used; unknown
top-level keys are not comprehensively rejected. An accepted JSON file is not
proof that every intended setting was understood. Use exactly the documented
fields and inspect the captured scope and resulting checks.

## Input mappings

Each entry in `inputs` contains:

| Field | Required | Meaning |
| --- | --- | --- |
| `path` | Yes | Source file or directory; relative to the scope file, absolute, or `~`-expanded |
| `at` | Yes | Destination relative to the candidate's `inputs/` |
| `include` | No | Directory-selection patterns, default `["**"]` |
| `exclude` | No | Additional directory-selection exclusion patterns |

Directory selection uses Python `fnmatch` on each complete relative path. On
the demonstrated Linux environment it is case-sensitive; `*` can match a slash
in that string, so these are not shell traversal rules. Exclusion takes
precedence. Git metadata is excluded automatically. An empty directory selection
is refused. `include` and `exclude` do not filter a single-file input mapping.

An explicitly named source root is resolved to its real path; this permits a
deliberately selected checkout alias. Symlinks encountered inside a selected
tree are refused. Declare their intended targets as separate inputs at the
effective candidate destinations. Targets containing `..` or absolute candidate
paths are refused. Two mappings cannot supply the same destination, even with
identical bytes. A library mapping can populate an otherwise empty subdirectory
inside a project mapping, provided no selected files overlap.

Capture records bytes, not just Git HEAD. Uncommitted files, external references
and requirement edits matter. The Git revision is provenance; it does not
replace selected-file hashes. Broad selections can capture unrelated local
files and consume time/storage. Select explicitly and keep secrets outside the
input set.

## Native library layout

Inside every tool sandbox these candidate destinations have configured meanings:

| Candidate-relative destination | Sandbox value |
| --- | --- |
| `stock/symbols` | `KICAD10_SYMBOL_DIR=/candidate/stock/symbols` |
| `stock/footprints` | `KICAD10_FOOTPRINT_DIR=/candidate/stock/footprints` |
| `stock/template` | `KICAD10_TEMPLATE_DIR=/candidate/stock/template` |

ERC, DRC and netlist checks copy captured `stock/template/sym-lib-table` and
`fp-lib-table` into a fresh `/work/kicad-config/10.0`. Missing template tables are
reported as a limitation; actual native library errors remain in the reports.
Project tables should resolve their custom symbols/footprints inside the
candidate, for example through `${KIPRJMOD}`. Host home configuration and
undeclared live libraries are not exposed. A custom environment variable in a
library table does not automatically become available inside the cleared child
environment.

For the donor, external project libraries are captured under
`project/fome-kicad-libs` to match its native tables. The disposable case-alias
scope also maps one existing source file to a second, differently capitalized
candidate name. This explicit input difference changes candidate identity and
does not repair the original checkout.

## Check specification

Each check has `id`, `kind`, and optional `params` (object, default `{}`). CLI
check IDs are unique and match `[a-z][a-z0-9_-]{0,63}`. Checks execute in the
listed order; dependencies are not topologically sorted.

Supported kinds are `erc`, `drc`, `netlist`, `geometry`, and `rc_transient`.
Unknown kinds are retained as unsupported unknown coverage. The transient check
must name a preceding check in `params.netlist_check`; use an actual `netlist`
check whose generated connectivity exists. Parameters, units and check-specific
failure behavior are in the [check reference](check-reference.md).

The current ERC/DRC policy treats any returned all-severity native issue as a
failed declared criterion, including reported excluded issues. There is no scope
parameter for "warnings only" acceptance, arbitrary DRC waiver, or silently
ignoring a failing native check. Report exclusions and resolve their engineering
meaning separately; changing that policy is an explicit implementation change.

## Reviewer specification

Use an explicit [reviewer profile](reviewer-profiles.md), either embedded as
`review_profile` or supplied with `--profile FILE`. The external file takes
precedence and is independently hashed. No model default or fixed adjudicator
remains. Initial contexts and experiment directories are separate. Tools are
enabled by default so reviewers can inspect complete captured files and investigate.
Role lenses request coverage; actual coverage still needs recorded evidence.

## Packet ordering and omissions

The packet includes scope criteria, check records and selected text in this order:

1. Requirement paths, prefixed with `inputs/`.
2. `packet_inputs`, prefixed with `inputs/`.
3. Required `packet_artifacts`.
4. Remaining evidence paths produced by the declared checks.

Duplicate paths are included once. Each artifact text is capped at 100000 bytes,
within the shared artifact budget. The budget does not cap all JSON overhead,
check-record text or adjudicator context. Text is a prefix when clipped. Binary
artifacts are retained on disk but omitted from text context. Unavailable paths,
clipped extents and binary omissions are recorded in `packet_limits`.

`packet_artifacts` must name paths actually listed in this run's check evidence;
a missing or mismatched required artifact fails packet construction and leaves
the review incomplete. A path in `packet_inputs` is not automatically required
by the same validation, so inspect unavailable-source notices. Choosing a path
or capturing a document does not ensure its full contents reached the reviewers.
If a relevant source is clipped, narrow the scope or add a suitable extraction
with preserved source/page/native provenance and rerun.
