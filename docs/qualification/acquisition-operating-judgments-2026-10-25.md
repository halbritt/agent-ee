# Acquisition operating judgments — 2026-10-25

Chief agent-564 authored four candidate-independent records from exact captured
source spans, operating instructions, circuit connections and primary ADS1115
text. Agent-566 independently concurred with three; configuration concurrence
is pending. The chief rechecked admission against the newly prepared source
with the [timing supplement](acquisition-operating-supplement-2026-10-25.md).
No package has frozen these records and no file-release, formal-review, native
board, supplier or physical acceptance is asserted.

| Record | Exact judgment SHA-256 | Current independent status |
| --- | --- | --- |
| `acq-elec-1-unqualified-conditions` | `962f9ed9ff6c74a6ad8d0a6b2842676bc3883b93e370f4d08431731924751489` | concur |
| `acq-elec-5-configuration` | `b60deeb411c205fee53cd42c6143a3e2b7beb0d1b1965a4772e10bd59d914efb` | missing |
| `acq-elec-5-host-bus-contract` | `ff49a8eff2ae8d4ec5f432289185f21c1bd1d9a58b36b86e8d6499fc5830e679` | concur |
| `acq-elec-6-high-impedance-contract` | `7709c13bb1bae4fd30cf720d154bfe8788d29cdfc2e076fd947e8f3aba4e23eb` | concur |

The three concurrence witnesses are hash-bound to those exact authored bytes:

- `acq-elec-1-unqualified-conditions`: `a823c215ccde2fd123c3c1cb21807cbaaa3be07f7edeebaf6ab3f2036ff80fb1`.
- `acq-elec-5-host-bus-contract`: `3cb930b68860f8561a953886797767de7fc197799d36d6e0f2f22fe15408edf5`.
- `acq-elec-6-high-impedance-contract`: `7d4da363b33cd03af606e912e8933a1aaa27fc9cf517b1b45fd12ae144498dea`.

The records establish the stated non-claims, host contract/address, external
high-impedance obligation and register instructions under their assumptions.
They distinguish source-defined connections from final native topology and
external host behavior. The 100 pF limit remains an agent-selected fixture
contract. A passive series resistor is not treated as an isolation switch.
The fourth record derives `0xC583` / `0xD583` from the primary register table
and incorporates the newly captured reset/startup timing correction.

All source/input/span/envelope pins resolved through the actual qualification
consumer. Three records returned `recorded` / `concur`; configuration returned
`pending`, `record_status: recorded`, `witness: missing`. The other ten required
judgments remain absent. The prepared overlay itself still truthfully reports
14 absent records: preparation does not import this separate authoring bundle.

## Evidence and package boundary

Authored records and chief admission results are retained at
`~/.cache/agent-evidence/agent-ee-564-integration/acquisition-operating-judgments-v1/`.
Independent witnesses and reasoning are at
`~/.cache/agent-audits/agent-ee-566-20261025/operating-judgment-witnesses/`.
The prepared source is
`~/.cache/agent-evidence/agent-ee-564-integration/acquisition-operating-prepared/`.

The current prospective package bundle is
`~/.cache/agent-evidence/agent-ee-564-integration/acquisition-authored-evidence/`.
It contains only `judgments/<record>.json` and `verification/<witness>.json`.
For a future build, copy that bundle under a separately declared source
directory such as `authored-evidence/` and use `judgments: "authored-evidence"`
in the typed build plan. Mapping record paths are relative to this declared
bundle: retain their existing `judgments/` prefix. Witnesses are under the
bundle's `verification/` directory. This records the current consumer's path
contract and avoids accidentally looking up a different record location.

Authoring remains mutable before package freeze. Once a package seals its
judgment inventory, adding or changing a record requires a new run. Existing
historical packages and the original 198-file capture remain unchanged.

## Reasoning limits

The initial bounded doctrine packet was `pkt-e62cb33d66e1246d`, content hash
`e62cb33d66e1246df96b0a36f41ec489ea1a1a0438c47e5ce0f7513a5a0d5383`,
from validated release `d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`. Its
receipt records the narrower three-record result and the then-withheld fourth
record; that historical abstention is preserved. The later supplement closes
the instruction omission, while independent configuration verification remains
pending. Thirty-seven generic unmet doctrine obligations are individually
classified for the narrowed authoring step; none establishes broader release
acceptance. Repository requirements and primary source evidence govern.
