# Capturing a requirements spec

`agent_ee.spec_record` (AGENTEE-158) turns a markdown requirements spec into an
`agent-ee.spec-record/1`. Every table row, bullet and numbered item becomes a
requirement with an ID and a byte-exact locator. Every paragraph is kept as a
prose entry with the same kind of locator. The capture uses only the Python
standard library. It makes no model calls, needs no network and infers nothing:
cell text is kept verbatim (markdown escapes included), IDs come from the codes
you declare and the spec's own first cells and item numbers, and typed values
are left to the requirement map (AGENTEE-159).

It is the stage-1 input for boards that are not the rung-1 RC low-pass. The
rung-1 RC spec keeps the existing regex path in `generate.parse_spec`; this
module does not change it.

```sh
PYTHONPATH=src python3 -m agent_ee.spec_record capture fixtures/telemetry-pod/spec.md \
  --out spec-record.json --prefix POD \
  --code 'Telemetry pod requirements=DOC' --code Purpose=PU --code Function=FN \
  --code Interfaces=IF --code 'Component decisions=CD' \
  --code 'Mechanical and environment=ME' --code 'Acceptance criteria=AC'
PYTHONPATH=src python3 -m agent_ee.spec_record verify spec-record.json fixtures/telemetry-pod/spec.md
```

`capture` exit 0 means the record and a receipt (`spec-record.receipt.json`, or
`--receipt PATH`) were written. Exit 1 means the spec was refused: the findings
are printed as JSON, each with a `code`, the `byte` offset in the spec and a
message, and nothing is written. Exit 2 means bad arguments, an output path that
already exists, or an I/O failure; existing files are never overwritten.

`verify RECORD SPEC` exits 0 only when the spec bytes match the recorded sha256
and size and capturing them again with the recorded prefix and codes gives the
record byte for byte. Consumers load a record through `spec_record.verify`.

## IDs

An ID is `PREFIX-CODE-KEY`:

| Source | Key | Example |
| --- | --- | --- |
| Table row | The first cell, upper-cased, with each run of characters other than ASCII letters and digits replaced by one hyphen | `POD-IF-CAN1`, `POD-CD-IMU` |
| Bullet | The bullet's ordinal among all bullets in its section | `POD-ME-1` |
| Numbered item | The number written in the spec, not its position | `POD-AC-4` |
| Paragraph | `P` and the paragraph's ordinal in its section | `POD-FN-P2` |

`--prefix` and each `--code` are upper-case letters and digits starting with a
letter (prefix up to 16, code up to 8 characters). `--code HEADING=CODE` names a
heading by its exact text. Every heading with content needs a code, no two
headings share a code, and a code naming no heading with content is refused.
Two sections with the same heading text and content are refused, because codes
are keyed by heading text.

An ID that collides with an earlier one is refused with both byte offsets. IDs
are never suffixed or renumbered: a numbered list written `1. 1. 1.` is
refused, and a bullet and a numbered item with the same number in one section
collide. Table-row IDs follow the first cell, so editing that cell changes the
ID; bullet IDs follow position, so inserting a bullet shifts later IDs. The
record hash changes in both cases.

## Record

The record (`agent-ee.spec-record/1`) is written as sorted, indented JSON.

| Field | Content |
| --- | --- |
| `spec` | `{name, sha256, bytes}`: the file name only, so the record does not depend on the working directory |
| `ids` | `{prefix, codes}` as given; `verify` recaptures with them |
| `headings` | `{level, text, heading_path, code, byte_start, byte_end}` per ATX heading; `code` is null for a heading without content |
| `tables` | `{section, heading_path, table, headers, rows, byte_start, byte_end}`; `table` is the 1-based index in the section |
| `lists` | `{section, heading_path, list, ordered, items, byte_start, byte_end}` |
| `requirements` | See below |
| `prose` | `{id, section, text, text_sha256, locator{heading_path, paragraph, byte_start, byte_end}}` |
| `counts` | Requirements per kind, all requirements, and prose entries |

Each requirement is
`{id, section, kind, cells, text, text_sha256, locator}`. `kind` is `table_row`,
`bullet` (unordered list item) or `acceptance` (ordered list item). `cells` maps
each column header to the verbatim cell text; it is empty for list items. The
locator is `{heading_path, table, row, list, item, byte_start, byte_end, cells}`.
`table` and `row` are set for table rows, `list` and `item` for list items, and
the others are null. `locator.cells` maps each header to the `[start, end]` byte
span of its cell.

`raw[byte_start:byte_end]` is exactly `text`, and `text_sha256` is its SHA-256.
For a table row the span is the row from its leading `|` to its trailing `|`.
For a list item it starts after the marker and ends at the end of the item's
last line; continuation lines are kept verbatim. Offsets count bytes, not
characters. Headings, tables, lists and paragraphs together cover every
non-whitespace byte of the spec, so no spec text is dropped silently.

The receipt (`agent-ee.spec-capture-receipt/1`) records the module SHA-256,
the Python version and implementation, the argument list, the spec identity,
the record's SHA-256 and size, and the counts. It carries no timestamp.

## Supported markdown

The parser reads a strict subset of GitHub-flavoured markdown and refuses the
rest with the byte offset rather than reading it another way.

- ATX headings (`#` to `######`, optional closing `#`s).
- Pipe tables whose rows all begin and end with `|`, followed by a delimiter row
  (`| --- | :-: |`). A backslash escapes the next character, so `\|` is a pipe
  inside a cell. A table ends at a blank line or a heading.
- Bullet lists (`-`, `*`, `+`) and numbered lists (`1.` or `1)`) with the marker
  in the first column. A line directly after an item continues it.
- Paragraphs. LF or CRLF line ends; UTF-8 without a byte-order mark.

Refused, each with its offset:

| Code | Cause |
| --- | --- |
| `table` | A ragged row or delimiter row, an empty or duplicate header, a row not beginning with `\|`, a row whose last `\|` is escaped or missing, a backslash at the end of a row, or a `\|` line without a delimiter row |
| `duplicate_id` | An ID collision |
| `id` | A first cell with no ASCII letter or digit |
| `section` | A heading with content and no code, an unused code, or a repeated heading text with content |
| `list` | A nested or indented list item, an empty item, a table inside an item, or indented text after a blank line in a list |
| `structure` | Content before the first heading, or a list item or table row directly after a paragraph line |
| `unsupported` | Fenced or indented code, block quotes, HTML or autolinks at a block start, thematic breaks, setext headings, tab indentation |
| `heading` | An empty heading |
| `encoding` | An empty file, a byte-order mark, invalid UTF-8, a NUL byte, or a carriage return without a line feed |

## The telemetry pod fixture

`fixtures/telemetry-pod/spec.md` is the owner's telemetry pod requirements,
revision 2 (2026-10-18, which folds the owner decisions of that day into the
text and supersedes the 2026-10-17 text with SHA-256 `c5128ef9…`), copied byte
for byte (SHA-256
`09e02c8d9471aabfa5d25935c87f436370cf1be367a96425b35305a53ce93493`).
`fixtures/telemetry-pod/spec-record.json` is its capture with the command above:
7 function rows, 8 interface rows, 8 component-decision rows, 6 mechanical
bullets, 9 acceptance items and 10 paragraphs. `tests/test_spec_record.py`
checks that a fresh capture reproduces it byte for byte.

## Limits

- The record says where text is, not what it means. Values such as `500 kbit/s`
  stay in the cell text; the requirement map (AGENTEE-159) maps each ID to a
  check, a limitation or a bench obligation.
- A requirement stated in a paragraph is a prose entry, not a requirement. For
  example, the pod's CAN relay sentence is `POD-FN-P2`. Consumers must account
  for prose as they do for requirements.
- `kind: acceptance` names an ordered list item wherever it occurs; the parser
  does not read headings to decide what a list means.
- Binding the record by SHA-256 into a scope's inputs is AGENTEE-159's
  compiler step; this module produces the record and receipt only.
