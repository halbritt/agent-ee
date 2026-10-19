# Datasheet-derived footprints by dual extraction agreement

`agent_ee.datasheet_footprint` builds a land pattern and symbol for a part
with no library entry, from one captured datasheet PDF. Two isolated agent
extractions must agree and cite the datasheet before anything is published.
Neither extraction is ever accepted alone.

Status: implemented and tested with an owned, fictitious PDF and recorded
(fake) agent transports. No live extraction has been run. This is an
authoring tool with its own CLI, not a registered check kind. Its output feeds
the existing [footprint assignment](footprint-libraries.md) and
[datasheet facts](datasheet-facts.md) checks.

## Flow

```sh
python3 -m agent_ee.datasheet_footprint extract request.json --pdf part.pdf --run build/extract-part
python3 -m agent_ee.datasheet_footprint accept build/extract-part --out build/accept-part
```

`extract` makes two separate agent invocations through the reviewer seam
(`reviewers.dispatch`) and keeps both records, whether they succeed or fail.
`accept` makes no model call. It replays the retained extractions into a new
directory and can be repeated with identical results. Exit status 0 means a
library was published; 1 means it was not.

## Request

```json
{
  "schema": "agent-ee.footprint-extraction-request/1",
  "part_number": "AEX-T3",
  "library": "AgentEE_Datasheet",
  "footprint": "AEX-T3",
  "pdf": {"path": "datasheets/aex-t3.pdf", "sha256": "<sha256 of the PDF bytes>"},
  "extractor": {"path": "/usr/bin/pdftotext", "sha256": "<binary sha256>", "version": "pdftotext version 24.02.0"},
  "kicad_version": "10.0.7",
  "extractors": {
    "extraction-a": {"harness": "claude", "model": "claude-opus-5-5", "effort": "high", "timeout_s": 600},
    "extraction-b": {"harness": "claude", "model": "claude-opus-5-5", "effort": "high", "timeout_s": 600}
  }
}
```

The shared profile validator checks each extractor definition. The
`tool_mode` must be `investigate`, because the extractor has to read the PDF.
The pdftotext declaration follows the [datasheet facts](datasheet-facts.md)
rules.

## Isolation

The extraction run directory holds `request.json` and `inputs/<pdf path>`.
Only `inputs/` is mounted, read-only, as `/candidate`, so each extractor sees
the PDF and nothing else. Each one works in its own
`review-work/<label>` directory. Retained results go to
`extractions/<label>.json`, which is outside every mount. The second extractor
cannot see the first one's output, and the run creates no `checks/`, `work/`
or baseline mount. A retained record holds:

- the label;
- the request identity;
- the extractor definition;
- the raw call record;
- the structured value, or `null` when the call failed.

A failed, timed-out, unavailable or raising agent call is retained with a
`null` value. There is no fallback and no retry.

## Extraction contract

Each extraction (`agent-ee.footprint-extraction/1`) holds:

- `pads`: a list of pads, described below;
- an optional `circuit`;
- `facts`: records in the shared datasheet-facts format, with `kind:
  "dimension"` and `unit: "mm"`;
- `limitations`.

Each pad has these fields:

| Field | Meaning |
| --- | --- |
| `number`, `name` | Pad number and datasheet pin name. Only an `np_thru_hole` pad is unnumbered and unnamed. Pads that share a number must share a name. |
| `kind` | `smd`, `thru_hole` or `np_thru_hole` |
| `shape` | `rect`, `circle` or `oval`. Pads are axis-aligned: `w` is the x extent and `h` the y extent. |
| `x`, `y`, `w`, `h` | Millimetres as decimal text. Top view, KiCad axes (+y down), origin at the package centre. |
| `hole` | `null` for SMD pads, otherwise `{shape, w, h}`. A plated hole needs a copper annulus. An NPTH pad's copper equals its hole. |
| `cite` | `{fact, scale}` or `null` for each of `x`, `y`, `w`, `h`, `hole_w` and `hole_h` |

A circuit lists components as `{ref, type, value}`, with exactly one `device`.
Nets are `{name, pins}`, with each pin written `REF.PIN`. Device pins must be
electrical pad numbers, and no pin may appear in two nets. Binary floats,
unknown fields and contradictory pads are `malformed`.

## Comparison

The comparator is deterministic. It groups pads by number and pairs them by
unique centre agreement. If that fails, it falls back to sorted position. It
then compares:

- `kind`, `shape`, `name` and hole shape exactly;
- `x`, `y`, `w`, `h` and the hole size within 0.01 mm. The limit is
  inclusive: 0.01 agrees, 0.0101 does not.

Circuits agree only if they have the same `(type, value)` component multiset
and the same net partition. The comparator searches for a reference mapping
that preserves type and value, so `C1`/`C2` in one extraction may be
`C9`/`C5` in the other. Net names are ignored. The search is bounded; if it
exceeds the bound, the extractions disagree.

Every difference goes into one list, `differences`, in `agreement.json`. Any
difference gives `execution: ok` and `decision: rejected`, and nothing is
published.

## Fact coverage

Every pad value must equal a cited fact. A dimension (`w`, `h`, hole size)
must equal its fact exactly, with scale `1`. A coordinate may be a declared
rational multiple of one cited fact, such as `-1/2` of a row spacing or `-1`
of a pitch. A coordinate of exactly `0` is the declared origin and needs no
fact. A fact must:

- name the request's part and PDF;
- have unit `mm`;
- spell its value as a plain unsigned decimal.

Each cited fact is verified with the shared rules. The installed pdftotext
must match the declaration before and after extraction. It runs in the
networkless sandbox and extracts the document once. The value must be a
complete token of the quote, and the quote must be on its cited physical page.
The shared verifier also requires the quote not to cut a printed page token.
`Pad pitch e 0.9` is not support when the page prints `0.95`.

Each missing, unknown, mismatched or unverified citation is listed in
`coverage_gaps`. Any gap gives `execution: unsupported` and `decision:
unknown`, and nothing is published. Cited pages are rendered at 200 DPI by
`pdftoppm` into `pages/`, with the renderer's binary hash and version.

## Emission and load

The agreed table is written as `.kicad_mod` and `.kicad_sym`. Values come from
`extraction-a`; the pad table keeps both extractions' citations. The
footprint gets:

- pads on their KiCad layer sets;
- `attr smd` or `attr through_hole`;
- a `F.CrtYd` rectangle at the KiCad 0.25 mm clearance, rounded outward to
  the 0.01 mm grid;
- deterministic UUIDs, so KiCad's re-save is reproducible.

The courtyard bounds pad copper only, because no body outline is extracted.
Symbol pins are numbered by unique electrical pad number, carry the agreed
names, and have electrical type `unspecified`.

Before publication:

1. `kicad-cli version` must equal `kicad_version`.
2. `kicad-cli fp upgrade --force` and `kicad-cli sym upgrade --force` must load
   and re-save the generated files.
3. The shared strict land-pattern extractor re-reads both the generated and
   the KiCad re-saved footprint. Pads and holes must equal the agreed table.
4. The shared `check_assignments` (footprint assignment) must report
   `execution: ok`, `outcome: pass` for pad/pin parity of the generated symbol
   against the generated footprint. Mixed SMD/through-hole technology stays
   unknown there, so it is never published.

A kicad-cli or tool failure is `execution: failed`. A parity defect is
`decision: rejected`.

## Published output

On success the complete staged directory is renamed, without replacement, to
`library/`. It contains:

- `<library>.pretty/<footprint>.kicad_mod` and `<library>.kicad_sym`;
- `footprint-libraries.json`: a complete
  [footprint manifest](footprint-libraries.md) for the assignment check;
- `facts.json`: `agent-ee.datasheet-facts/1` with both extractions' cited
  facts, ids prefixed by label, which the `datasheet_facts` check can re-verify;
- `pad-table.json`: the agreed pads with both citation sets;
- `reference-circuit.json`: when a circuit was agreed, with the reference
  mapping.

`agreement.json` records:

- the request identity and the execution of each extraction;
- differences, coverage gaps and fact statuses;
- page hashes and the kicad-cli re-save hashes;
- the sealed assignment result;
- every library file hash.

## Outcomes

| Condition | execution | decision | Published |
| --- | --- | --- | --- |
| Either agent call failed, timed out, was unavailable or is missing | failed | unknown | no |
| A retained record belongs to another role or request, or a value violates the contract | malformed | unknown | no |
| Changed PDF bytes | failed | unknown | no |
| Any difference beyond tolerance | ok | rejected | no |
| Any pad value without a verified fact | unsupported | unknown | no |
| pdftotext, pdftoppm or kicad-cli unavailable or failing | failed | unknown | no |
| Symbol/footprint pad parity defect | ok | rejected | no |
| Agreement, full fact coverage, KiCad load and parity pass | ok | accepted | yes |

## Limits

- No IPC-7351 calculation. Dimensions are taken as extracted and cited.
- No pad rotation, roundrect, custom or trapezoid pads, and no inner layers.
- Citation proves text occurrence. It does not prove row or condition
  selection, unit interpretation or applicability.
- Agreement of two model extractions is not proof that they are correct; both
  can make the same mistake.
- The retained files cannot prove that the two extractions were independent.
  Only `extract` enforces separate invocations and mounts.
- A reference circuit is agreed, not shown to be electrically correct, and is
  kept as JSON. No schematic is emitted.

The agreement protocol (two isolated extractors that read only the PDF, pads
agreeing within 0.01 mm, a circuit as a component multiset and net partition)
follows the architecture notes of tscircuit's datasheet-to-tscircuit. The
pad-table fields resemble those of Konnect's `create_footprint` tool. The
ticket supplied both as mechanism references only. No code, fixtures or text
from either project were retrieved or copied. Per the ticket, the first has no
licence and the second is AGPL.
