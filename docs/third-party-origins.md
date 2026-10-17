# Third-party origins and publication status

This page summarizes the [file-origin inventory](file-origins.json) for owner
review (AGENTEE-14). The inventory classifies every tracked file by kind,
origin, known upstream license and publication status. It was prepared on
2026-10-17 against baseline `69d7bbf`, with 5,166 tracked files at that
baseline. Main integration extended the inventory to all 5,407 tracked files,
including the new native fixtures, generation tooling and vendored viewer runtime.

## Status

- MIT is **proposed** in [LICENSE](../LICENSE) for agent-ee material: files
  whose only origins are `AE` (written for agent-ee) or `AE-RUN` (outputs of
  agent-ee runs on such material). The proposal is now included on main; the
  owner has not approved it. MIT was the owner's stated default; another license needs explicit
  owner approval.
- Every other origin is **unresolved**. Those files are not offered under MIT.
  Each origin needs an owner decision, recorded in the inventory, before its
  status changes. Ownership of this repository, or of the checkout a file was
  copied from, is not treated as a right to redistribute it.
- Observation, not a rights decision: on 2026-10-17 the GitHub API reported
  `halbritt/agent-ee` and `halbritt/fome-mc` as public, with agent-ee `main` at
  `69d7bbf`. That branch already contains the unresolved files, for example
  `references/TJA1051.pdf` and the evidence archives. Removing them from
  published history would be a history rewrite, which is a separate owner gate.

At the baseline, 367 files were proposed for MIT and 4,799 were unresolved.
Most unresolved files are retained evidence, which is classified by directory.

## Origins

| ID | Origin | Upstream license, as checked | Tracked material |
| --- | --- | --- | --- |
| AE | agent-ee original material | MIT proposed | Code, tests, scripts, docs, planning records, original fixtures |
| AE-RUN | agent-ee run outputs | MIT proposed | Records, native tool output and reviewer output from runs on agent-ee material |
| T1 | FOME-MC RC filter fixture (`halbritt/fome-mc`, `5b7b175`) | No license found | `fixtures/rc/control/` and `seeded/`, 90 copies and variants in evidence, RC evidence directories and archives |
| T2 | FOME-MC `toolchain/eda.py` | No license found | Adapted logic in `src/agent_ee/checks.py` |
| T3 | Polygonus-Universal-112 donor (`FOME-Tech`, `ddcf0f7`) | No license found | Donor and model-baseline evidence, its Gerber/drill/render outputs, three reports, eight archives |
| T4 | FOME-Tech kicad-libraries (`ab9c5f8`) | No license found; SamacSys terms not reviewed | Donor captures in archives; donor evidence |
| T5 | rusEFI libraries vendored in the donor | Mixed; includes a GPLv3 credit | Donor captures in archives; donor evidence |
| T6 | KiCad 10.0.7 stock libraries | CC-BY-SA-4.0 with a design exception | Library collections in 15 archives; data embedded in T1 and donor designs |
| T7 | KiCad CLI documentation page | GPL-3.0+ or CC-BY-3.0+ | One saved page, an excerpt and probe records |
| T8 | FOME-Tech Polygonus brain module (`b3176cb` or unpinned) | No license found | 27 schematic copies, READMEs, a PDF export, derived netlists |
| T9 | mck1117/wideband (`0a26d3b` or unpinned) | MIT; notice not included with copies | Schematics, firmware sources, READMEs, derived netlists |
| T10 | NXP, Infineon, ST, Maxim, TI datasheets | Proprietary | 21 PDFs and text extractions; two one-line TJA1051 quotations in a fixture and a test |
| T11 | LCSC pages and web-search captures | Proprietary; terms not reviewed | 11 pages and captures |
| T12 | KiCad 10.0 PPA signing public key | No license stated | `docker/kicad-10.0-releases.asc` |
| T13 | Seeded-benchmark source boards | CC-BY-SA-4.0, CERN-OHL-P/S-2.0 | Metadata and extracted identifiers in `benchmarks/seeded/`; no design files |
| T14 | three.js r180 (0.180.0) | MIT; upstream notice retained | Viewer runtime, loader and controls in `src/agent_ee/viewer_assets/vendor/`; exact sources and transforms in its manifest |

The inventory gives each origin's repository, revisions, local source path,
license evidence and owner decision. The exact files are listed by rule, and
`python3 scripts/check_origins.py --list` expands the rules to one record per
file.

## Kinds of file

The inventory separates:

- material written here: `owned-source`, `owned-documentation`,
  `owned-fixture` and `owned-metadata`. Metadata names or describes
  third-party material without copying it, for example the donor scopes.
- `adapted-source`: only `src/agent_ee/checks.py`. It shares 29 distinct
  non-trivial lines with FOME-MC `eda.py` at `5b7b175`. `executor.py` shares
  3 generic lines with that file and is treated as agent-ee material.
- `copied-design`: copies and variants of design files from another project.
- `third-party-document`: verbatim documents, pages, firmware sources and
  text extractions.
- `retained-output` and `generated-design-output`: tool, coordinator and
  reviewer outputs, plus plots, renders, Gerber and drill files.
- `evidence-archive`: the 17 archives. Each holds a readable copy of its
  evidence directory, the captured inputs, and generated configuration.

Original fixtures (active SPICE, datasheet pins, DC path, hardware interface,
kicad-happy qualification, netclass, passive corners and closed loop) were
generated or hand-authored here. Their symbols and footprints were checked to
contain no stock library graphics. Each KiCad file in them is listed with a
recorded review.

## What the check enforces

`python3 scripts/check_origins.py` and `tests/test_file_origins.py` fail when:

- a file has no rule, two equally specific rules, or a rule path selects no
  file;
- a file that looks like third-party material is classified as agent-ee
  material without an exact-path rule that records a review. "Looks like"
  means a file type such as PDF, HTML, image, archive, KiCad design or library,
  Gerber or drill, C/C++ or PGP key. For agent-ee files, it also means
  content markers such as embedded KiCad text, PDF or HTML headers, rights
  notices and third-party project names;
- a retained `candidate.json` captured inputs from an origin that its rule
  omits, or a captured source matches no known origin;
- an archive omits an origin of the evidence directory it holds;
- a third-party origin is marked as proposed for MIT, or is marked
  `owner-confirmed` without an `owner_record`.

Without `.git`, as in the container image, the check walks the checkout with
the `.gitignore` exclusions.

## Limits

- Evidence directories are classified as a whole. Their files were not
  reviewed one by one. The directory's origins are assigned because its
  packets, native reports and reviewer outputs embed or quote those designs.
- Origin detection relies on provenance records, captured-source paths, file
  types and content markers. Copying that leaves none of those traces would
  not be detected.
- License statements come from upstream files and installed packages at the
  cited revisions. "No license found" means no grant is known. This page does
  not give legal advice and does not settle anyone's rights.
- Agent-ee material was written by the owner and by AI agents working under
  the owner's direction. How AI authorship and provider terms affect copyright
  was not analysed.
- KiCad is installed, not vendored. `geometry_probe` and `dc_geometry_probe`
  import `pcbnew` in sandboxed child processes. `benchmark.verify_pcb_protection`
  imports it inside the `seeded_benchmark.py` preparation process. The fixture
  generators also import it. Whether these imports satisfy decision 6 ("GPL
  tools are called as processes, never linked") for distribution is open.
- The container image clones kicad-happy (MIT; its `LICENSE` stays in the
  image) and installs KiCad, ngspice and Ubuntu packages under their own
  licenses. Publishing an image would be a separate distribution, which this
  inventory does not cover.

## Owner decision needed

One question for Heath, answered per part:

> Do you approve (1) MIT for agent-ee material (`AE`, `AE-RUN`) with the
> copyright line `Copyright (c) 2026 Heath (GitHub: halbritt)`, and (2) MIT for
> the FOME-MC RC fixture and `eda.py`-derived logic (T1, T2), as their sole
> author? (3) Do you confirm that T3–T13 stay excluded from MIT and unresolved
> until you choose, per origin, to obtain permission, add the upstream notices,
> or keep only links, revisions and hashes in future trees?

Parts 1 and 2 are permissions only the owner can give. Part 3 covers other
parties' material, which the owner cannot relicense. Removing those files from
published history, or deciding the decision 6 question above, needs separate
owner decisions.

## Recording a decision

Set the origin's `permission_status` to `owner-confirmed` and add an
`owner_record` with the date, the decision and where the owner gave it. The
check refuses a confirmed status without that record. A decision to remove or
replace files is a separate change. Do not delete or rewrite retained evidence
to make the inventory pass.
