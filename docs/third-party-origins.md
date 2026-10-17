# Third-party origins and publication status

This inventory lists every tracked file, or group of tracked files, that
originates in another project. For each origin it records where the files came
from, the upstream license as far as it could be checked, and the publication
status. It was prepared for the license work item (AGENTEE-14) on 2026-10-17
from `git ls-files` at baseline `83e4bc7` (4,988 tracked files), the file
listings of all 17 evidence archives, and the provenance records in the
repository.

## Owner gate

- The [MIT License](../LICENSE) is proposed for material written for agent-ee.
  It does not cover the files listed below. No file listed here is offered
  under MIT by this repository.
- The publication rights for every origin below are **unresolved**. Each one
  requires owner confirmation before publication. Owning this repository, or
  the source checkout a file was copied from, does not establish a right to
  redistribute it.
- This inventory does not claim that any listed file may be published, and it
  does not claim that any upstream license condition has been satisfied.
- Observation, not a rights decision: on 2026-10-17 the GitHub API reported
  `halbritt/agent-ee` as **public**, with `main` at `83e4bc7`. That branch
  already contains the files listed here, for example
  `references/TJA1051.pdf` and the evidence archives. The owner decides
  whether to keep, remove, replace with links, add notices to, or obtain
  permission for each group. Removing files from published history would be a
  history rewrite, which is a separate owner gate.

## How to read and maintain this inventory

- Each bullet that starts with a path in backticks is an inventory entry. A
  path that ends in `/` means every tracked file below that directory.
- "No license found" means: the upstream repository has no license file at
  the cited revision, and the GitHub API reports no detected license. No grant
  of rights is known in that case. Default copyright rules apply.
- "Verified" means the license text was read from the upstream file or the
  installed package at the cited revision. Other license statements are
  marked as unverified.
- `tests/test_metadata.py` checks that every entry still matches a tracked
  file. It also checks that each tracked file in one of these categories has
  an entry: PDFs, web pages, archives, images, KiCad design and library files,
  library tables, Gerber and drill outputs, C/C++ sources, files in reviewer
  reference folders, and files whose content names the donor design or
  embeds KiCad design text. A file that matches only because it mentions such
  material is listed under
  [Reviewed files with no third-party content](#reviewed-files-with-no-third-party-content).
- When an origin is resolved, replace its status line with
  `Publication status: Owner confirmed YYYY-MM-DD (record: ...)` and cite the
  owner's decision. Do not mark a status resolved without that record.

## Summary

| ID | Origin | Upstream license | Tracked material |
| --- | --- | --- | --- |
| [T1](#t1-fome-mc-rc-filter-fixture) | FOME-MC RC filter fixture (`halbritt/fome-mc`) | No license found | Fixture copies, a seeded variant, disposable copies, packets and tool output in RC evidence |
| [T2](#t2-fome-mc-edapy-validation-logic) | FOME-MC `toolchain/eda.py` | No license found | Adapted validation logic in `src/agent_ee/checks.py` |
| [T3](#t3-polygonus-universal-112-donor-design) | FOME-Tech Polygonus-Universal-112 ECU base board | No license found | Captured design in archives; packets, reports, Gerber/drill outputs and renders in donor evidence; donor scopes |
| [T4](#t4-fome-tech-kicad-libraries) | FOME-Tech kicad-libraries | No license found | Captured library files in donor archives |
| [T5](#t5-rusefi-libraries-vendored-in-the-donor) | rusEFI libraries vendored in the donor checkout | Mixed and unverified; includes a GPLv3 credit | Captured library files in donor archives |
| [T6](#t6-stock-kicad-1007-libraries-and-templates) | KiCad 10.0.7 stock symbols, footprints and templates | CC-BY-SA 4.0 with a design exception (verified) | Library collections in all design archives; symbol and footprint data embedded in fixture designs |
| [T7](#t7-kicad-documentation) | KiCad documentation (docs.kicad.org) | GPLv3+ or CC-BY 3.0+ (stated on the page) | One saved HTML page and an excerpt |
| [T8](#t8-fome-tech-polygonus-brain-module) | FOME-Tech Polygonus brain module | No license found | Schematics, a PDF export and READMEs fetched by reviewers |
| [T9](#t9-mck1117-wideband) | mck1117/wideband (FOME wideband module) | MIT (verified at `0a26d3b`) | Schematics, firmware sources and READMEs fetched by reviewers |
| [T10](#t10-semiconductor-datasheets) | NXP, Infineon, STMicroelectronics, Maxim and Texas Instruments datasheets | Proprietary; "All rights reserved" or copyright notices | PDFs and text extractions |
| [T11](#t11-distributor-pages-and-web-search-captures) | LCSC pages and web-search tool captures | Proprietary site content; terms not reviewed | HTML pages, JSON and text captures |

Every row requires owner confirmation before publication.

## T1 FOME-MC RC filter fixture

- Origin: `/home/halbritt/git/fome-mc/toolchain/fixtures/rc_filter/`
  (GitHub `halbritt/fome-mc`). Copied byte for byte on 2026-10-16 from a
  working tree with HEAD `c43ad7b`; the files were untracked there at the
  time ([provenance note](../fixtures/rc/PROVENANCE.md)). On 2026-10-17 the
  same five files are tracked in `fome-mc` commit `5b7b175`, and their
  SHA-256 hashes match the copies in `fixtures/rc/control/`.
- Authorship: the `fome-mc` history has one author name, `halbritt`. The files
  were produced by its `generate.py`. Authorship by the owner is likely but is
  not treated here as a publication right.
- Upstream license: no license found. `fome-mc` has no license file and the
  GitHub API reports none.
- Embedded third-party data: the schematic and board embed symbols and
  footprints from the KiCad 10 stock libraries ([T6](#t6-stock-kicad-1007-libraries-and-templates)).
  The KiCad library exception covers designs that use library data.
- Publication status: requires owner confirmation before publication.

Copied fixture and the seeded variant (one value changed in two files):

- `fixtures/rc/control/fp-lib-table`
- `fixtures/rc/control/rc_filter.kicad_pcb`
- `fixtures/rc/control/rc_filter.kicad_pro`
- `fixtures/rc/control/rc_filter.kicad_sch`
- `fixtures/rc/control/sym-lib-table`
- `fixtures/rc/seeded/fp-lib-table`
- `fixtures/rc/seeded/rc_filter.kicad_pcb`
- `fixtures/rc/seeded/rc_filter.kicad_pro`
- `fixtures/rc/seeded/rc_filter.kicad_sch`
- `fixtures/rc/seeded/sym-lib-table`

Further copies and variants of the fixture in evidence:

- `evidence/rc-verified/disposable/`
- `evidence/model-repair/disposable/`
- `evidence/model-repair-final/disposable/`
- `evidence/model-release/repair-demo/disposable/`
- `evidence/model-comparison/cases/` (12 variants with their scopes and requirements)
- `evidence/model-comparison/runs/claude/population-a-1/review-work/physical/experiments/scratch_project/`
- `evidence/model-comparison/runs/claude/shutdown-a-1/review-work/physical/experiments/scratch_board.kicad_pcb`
- `evidence/model-comparison/runs/claude/width-a-1/review-work/physical/experiments/scratch_refill.kicad_pcb`
- `evidence/model-comparison/runs/claude/width-b-1/review-work/physical/experiments/scratch_board.kicad_pcb`

Evidence derived from the fixture. Review packets embed the fixture's native
text. Tool reports, netlists, plots and reviewer outputs describe it:

- `evidence/rc-demonstration/`
- `evidence/rc-final/`
- `evidence/rc-verified/`
- `evidence/model-repair/`
- `evidence/model-repair-final/`
- `evidence/model-release/repair-demo/`
- `evidence/model-release/uncertainty/`
- `evidence/model-comparison/`
- `evidence/adjudicator-comparison/`
- `evidence/model-comparison-summary.json`

The RC archives listed in [Evidence archives](#evidence-archives) contain the
same material.

## T2 FOME-MC eda.py validation logic

- Origin: `/home/halbritt/git/fome-mc/toolchain/eda.py`, read in the working
  tree at `fome-mc` commit `c43ad7b`, where it was untracked. It was committed
  later in `fome-mc` `5b7b175`.
- What was taken: the module docstring of `checks.py` says that validation
  logic for report shape, sheet coverage, silent parity skips and ngspice
  output is adapted from `eda.py`. A line comparison on 2026-10-17 against
  `eda.py` at `5b7b175` found 28 identical lines longer than 25 characters
  (regular expressions, report-shape checks and library-table setup). It did
  not find copied code in `geometry_probe.py` or `executor.py`. This is a
  heuristic count, not a full similarity audit. The version read at `c43ad7b`
  may differ from `5b7b175`.
- Upstream license: no license found (see T1).
- Publication status: requires owner confirmation before publication. Until
  the owner confirms, the adapted portions of this file are not covered by the
  MIT grant.

- `src/agent_ee/checks.py`

## T3 Polygonus-Universal-112 donor design

- Origin: `/home/halbritt/git/Polygonus-Universal-112`, GitHub
  `FOME-Tech/Polygonus-Universal-112`, captured at `ddcf0f7` (last commit by
  Matthew Kennedy, 2026-03-22). Each donor `candidate.json` records the exact
  source paths, hashes and revisions.
- Upstream license: no license found. The README calls the board
  "open-source" but names no license.
- What is tracked: the archives contain verbatim copies of the donor
  schematics, board, project file, README and project-local footprints. The
  tracked evidence contains review packets with donor schematic text, native
  ERC/DRC reports, netlists and connectivity, saved-board geometry, Gerber and
  drill outputs, renders, and reviewer outputs that quote the design.
- Publication status: requires owner confirmation before publication.

Scopes that name the donor files (written here; they name file paths and
describe the design):

- `scopes/donor.json`
- `scopes/donor-case-alias.json`

Reports written here that describe the donor and quote identifiers, values and
locations:

- `evidence/BASELINE.md`
- `evidence/MODEL-BASELINE.md`
- `evidence/MODEL-SELECTION.md`

Evidence derived from the donor design:

- `evidence/donor-baseline/`
- `evidence/donor-baseline-v2/`
- `evidence/donor-final/`
- `evidence/donor-verified/`
- `evidence/model-baselines/`
- `evidence/model-baselines-expanded/`
- `evidence/model-baselines-final/`
- `evidence/model-release/baseline/`

Manufacturing outputs and renders generated from the donor board by reviewers
(also inside the directories above):

- `evidence/model-baselines/codex/review-work/adjudicator/experiments/drill/Polygonus-Universal-Base-NPTH.drl`
- `evidence/model-baselines/codex/review-work/adjudicator/experiments/drill/Polygonus-Universal-Base-PTH.drl`
- `evidence/model-baselines/codex/review-work/adjudicator/experiments/drill/Polygonus-Universal-Base.drl`
- `evidence/model-baselines/codex/review-work/physical/experiments/drill/Polygonus-Universal-Base-NPTH.drl`
- `evidence/model-baselines/codex/review-work/physical/experiments/drill/Polygonus-Universal-Base-PTH.drl`
- `evidence/model-baselines/codex/review-work/physical/experiments/drill/Polygonus-Universal-Base-drill.rpt`
- `evidence/model-baselines/codex/review-work/physical/experiments/front_saved.png`
- `evidence/model-baselines/codex/review-work/physical/experiments/front_saved.svg`
- `evidence/model-baselines-expanded/codex/review-work/adjudicator/experiments/drill/Polygonus-Universal-Base-NPTH.drl`
- `evidence/model-baselines-expanded/codex/review-work/adjudicator/experiments/drill/Polygonus-Universal-Base-PTH.drl`
- `evidence/model-baselines-expanded/codex/review-work/physical/experiments/drill/Polygonus-Universal-Base-NPTH.drl`
- `evidence/model-baselines-expanded/codex/review-work/physical/experiments/drill/Polygonus-Universal-Base-PTH.drl`
- `evidence/model-baselines-expanded/codex/review-work/physical/experiments/can2-layout.png`
- `evidence/model-baselines-expanded/codex/review-work/physical/experiments/thermal-details.png`
- `evidence/model-baselines-final/codex/review-work/adjudicator/experiments/drill/Polygonus-Universal-Base-NPTH.drl`
- `evidence/model-baselines-final/codex/review-work/adjudicator/experiments/drill/Polygonus-Universal-Base-PTH.drl`
- `evidence/model-baselines-final/codex/review-work/adjudicator/experiments/drill/Polygonus-Universal-Base.drl`
- `evidence/model-baselines-final/codex/review-work/physical/experiments/drill/Polygonus-Universal-Base-NPTH.drl`
- `evidence/model-baselines-final/codex/review-work/physical/experiments/drill/Polygonus-Universal-Base-PTH.drl`
- `evidence/model-baselines-final/codex/review-work/physical/experiments/gerbers/`
- `evidence/model-baselines-final/codex/review-work/physical/experiments/thermal-reliefs.png`
- `evidence/model-release/baseline/review-work/adjudicator/experiments/drills/Polygonus-Universal-Base-NPTH.drl`
- `evidence/model-release/baseline/review-work/adjudicator/experiments/drills/Polygonus-Universal-Base-PTH.drl`
- `evidence/model-release/baseline/review-work/adjudicator/experiments/drills/Polygonus-Universal-Base-drill.rpt`
- `evidence/model-release/baseline/review-work/physical/experiments/drills/Polygonus-Universal-Base-NPTH.drl`
- `evidence/model-release/baseline/review-work/physical/experiments/drills/Polygonus-Universal-Base-PTH.drl`
- `evidence/model-release/baseline/review-work/physical/experiments/saved-thermal-copper.png`

Archives that contain verbatim donor files are listed in
[Evidence archives](#evidence-archives).

## T4 FOME-Tech kicad-libraries

- Origin: `/home/halbritt/git/kicad-libraries`, GitHub
  `FOME-Tech/kicad-libraries`, captured at `ab9c5f8` as
  `project/fome-kicad-libs/` (the donor's submodule pin).
- Content: 50 files per captured candidate: `fome_modules`, `Simon`,
  `Wiring-Diagrams` and `Molex symbols` libraries, and `SamacSys_Parts`
  symbols and footprints. SamacSys parts are usually generated by the
  SamacSys / Component Search Engine service, which has its own terms. Those
  terms were not reviewed.
- Upstream license: no license found.
- Publication status: requires owner confirmation before publication.
- Tracked only inside the donor archives (see
  [Evidence archives](#evidence-archives)).

## T5 rusEFI libraries vendored in the donor

- Origin: the `kicad-libraries/` directory committed inside the donor
  repository (its files were first added in donor commit `8e54662`). Its readme describes it as
  rusEFI's libraries. 151 `.lib`, `.dcm`, `.kicad_mod` and outline files are
  captured per candidate as `project/kicad-libraries/`.
- Upstream license: mixed and unverified. The directory's `Credit_lic.txt`
  marks `HC-0X_bluetooth.mod` and `itead_hc-0X_bluetooth.lib` as "GPLv3 from
  W. Lain"; the `.lib` file is among the captured files. The same note says
  the STM32 symbols came from the former `KiCad/kicad-library` repository,
  whose historical license was not verified. The rusEFI library repository
  (`rusefi/kicad-legacy-libraries` on GitHub) reports no license. The licenses
  of the other parts are unknown.
- Publication status: requires owner confirmation before publication. The
  GPLv3 credit means these files cannot be offered under MIT.
- Tracked only inside the donor archives (see
  [Evidence archives](#evidence-archives)).

## T6 Stock KiCad 10.0.7 libraries and templates

- Origin: Ubuntu packages `kicad-symbols`, `kicad-footprints` and
  `kicad-templates` 10.0.7, from `/usr/share/kicad` on host `proximal`.
- Upstream license (verified from the installed
  `/usr/share/doc/kicad-symbols/LICENSE.md`, SHA-256
  `45d2bce75e5a4208f5afb01b8fb2c406e700371c4fe2b5f5cd5c443d46db4d8f`, which
  is identical for the three packages): CC-BY-SA 4.0. An exception waives the
  share-alike article for designs that use the library data and for files
  generated from those designs. The exception does not apply when the
  libraries, or parts of them, are redistributed as a collection. Such a
  collection must keep CC-BY-SA 4.0, the attribution and the license
  documents.
- What is tracked: the design archives contain whole stock footprint
  directories (for example `Resistor_SMD.pretty`), stock symbol libraries and
  template library tables, captured as `stock/` inputs. The generated
  `kicad-config/` directories in the archives hold copies of the template
  `sym-lib-table` and `fp-lib-table`. No archive contains the KiCad library
  license file. The fixture designs (T1) embed stock symbol and footprint
  data, and the donor files (T3) may also do so. The design exception applies
  to data embedded in designs.
- Publication status: requires owner confirmation before publication. The
  archived collections cannot be offered under MIT. Publishing them would also
  need the CC-BY-SA 4.0 attribution and license document, which the archives
  do not include.
- Tracked only inside the archives (see [Evidence archives](#evidence-archives))
  and as embedded data in T1 and T3 files.

## T7 KiCad documentation

- Origin: `https://docs.kicad.org/10.0/en/cli/cli.html`, saved by a Codex
  network probe.
- Upstream license: the page states that it may be distributed under the GNU
  GPL version 3 or later, or the Creative Commons Attribution License version
  3.0 or later. Copyright: The KiCad Documentation Contributors.
- Publication status: requires owner confirmation before publication.

- `evidence/model-network-probes/codex-fixed/review-work/probe/experiments/kicad-cli.html`
- `evidence/model-network-probes/codex-fixed/review-work/probe/experiments/kicad-cli-source-excerpt.txt`
- `evidence/model-network-probes/codex-fixed/` (logs that echo the excerpt)

The `model-network-probes` archive contains the same files.

## T8 FOME-Tech Polygonus brain module

- Origin: GitHub `FOME-Tech/Polygonus`. The Codex reviewer fetched pinned
  files at `b3176cb` (its fetch log is
  `references/modules_fetch_log.json`). The Agy and Claude reviewers fetched
  from the unpinned `master` branch, so their exact revisions are not
  recorded.
- Upstream license: no license found at `b3176cb` or in the GitHub API's
  current repository record.
- File kinds: verbatim copies of schematics, README and a KiCad PDF export;
  a text extraction of that PDF; netlists, net lists and interface summaries
  that reviewers derived from the copies; GitHub tree listings.
- Publication status: requires owner confirmation before publication.

- `evidence/model-baselines-expanded/agy/review-work/circuit/experiments/ref_polygonus/`
- `evidence/model-baselines-expanded/claude/review-work/adjudicator/experiments/fetch/psu_upstream.kicad_sch`
- `evidence/model-baselines-expanded/claude/review-work/circuit/experiments/polygonus/`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/polygonus-Shortage-Version.kicad_sch`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/polygonus_Keep_Alive.kicad_sch`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/polygonus_README.md`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/polygonus_full.xml`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/polygonus_full_nets.txt`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/polygonus_ign4.kicad_sch`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/polygonus_ign4.xml`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/polygonus_lowside_quad.kicad_sch`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/polygonus_lowside_quad.xml`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/polygonus_mcu.kicad_sch`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/polygonus_mcu.xml`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/polygonus_psu.kicad_sch`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/polygonus_psu.xml`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/polygonus_sub_nets.txt`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/polygonus_triggers.kicad_sch`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/polygonus_triggers.xml`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/all_ref_symbols.txt`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/parsed_refs.txt`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/repo_files.txt`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/upstream_nets.txt`
- `evidence/model-baselines-final/codex/review-work/adjudicator/experiments/references/Polygonus_README.md`
- `evidence/model-baselines-final/codex/review-work/adjudicator/experiments/references/Polygonus_mcu.kicad_sch`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/Polygonus_mcu.xml`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/Polygonus_mcu_interface.json`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/Polygonus_README.md`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/Polygonus_export_V1.6_polygonus-Shortage-Version.pdf`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/Polygonus_mcu.kicad_sch`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/Polygonus_polygonus-Shortage-Version.kicad_sch`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/Polygonus_tree.json`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/Polygonus_triggers.kicad_sch`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/Polygonus_v1.6.txt`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/modules_fetch_log.json`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/fetch_log.json`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/fetch_log_followup.json`
- `evidence/model-release/baseline/review-work/circuit/experiments/polygonus-readme.md`

## T9 mck1117 wideband

- Origin: GitHub `mck1117/wideband` (titled "FOME Open Source Wideband O2
  Module"). The Codex reviewer fetched pinned files at `0a26d3b`. The Agy
  reviewers fetched from the unpinned `master` branch.
- Upstream license (verified): MIT, "Copyright (c) 2023 Matthew Kennedy", in
  `LICENSE` at `0a26d3b`. The `master` copies were not checked against a
  revision. MIT permits redistribution only if the copyright and permission
  notice are included. No such notice accompanies the copies here.
- Publication status: requires owner confirmation before publication. A
  publication decision also needs the upstream notice to be added.

- `evidence/model-baselines-expanded/agy/review-work/circuit/experiments/ref_wideband/`
- `evidence/model-baselines-expanded/agy/review-work/circuit/experiments/ref_wideband_controller.kicad_sch`
- `evidence/model-baselines-expanded/agy/review-work/circuit/experiments/wideband_controller.kicad_sch`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/wideband_board_carrier.kicad_sch`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/wideband_board_carrier.xml`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/wideband_controller.kicad_sch`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/wideband_controller.xml`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/wideband_f0_board.h`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/wideband_f0_io_pins.h`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/wideband_f0_port.cpp`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/wideband_f0_port_shared.cpp`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/wideband_f0_readme.md`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/wideband_netlist.xml`
- `evidence/model-baselines-final/agy/review-work/circuit/experiments/refs/wideband_nets.txt`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/wideband_README.md`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/wideband_firmware_boards_f0_module_readme.md`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/wideband_hardware_README.md`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/wideband_hardware_board_module_wideband_controller.kicad_sch`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/wideband_tree.json`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/wideband_module.xml`

## T10 Semiconductor datasheets

- Origin and notices, read from the files:
  - NXP TJA1051, revision 9: "© NXP N.V. 2017. All rights reserved."
    Download details for the copy in `references/` are in
    [SOURCES.md](../references/SOURCES.md).
  - Infineon TLE9201SG: "© 2014 Infineon Technologies AG. All Rights Reserved."
  - STMicroelectronics VNL5050 and VNL5090: "© 2025 STMicroelectronics – All
    rights reserved" (read from the VNL5090 copy; the VNL5050 copies carry ST
    authorship metadata).
  - Maxim Integrated MAX9924–MAX9927: "© 2012 Maxim Integrated Products".
  - Texas Instruments DRV8825: "Copyright © 2010–2014, Texas Instruments
    Incorporated".
- Upstream license: proprietary. No redistribution permission was found in
  these files. The `.txt` files are text extractions of the PDFs. The `.tmp`
  file is a saved error page from a datasheet request.
- Publication status: requires owner confirmation before publication. One
  option is to keep only the URLs, revisions and hashes, as
  [SOURCES.md](../references/SOURCES.md) already records them for TJA1051.

- `references/TJA1051.pdf`
- `evidence/model-baselines-final/claude/review-work/circuit/experiments/max9924.pdf`
- `evidence/model-baselines-final/claude/review-work/circuit/experiments/max9924.txt`
- `evidence/model-baselines-final/claude/review-work/circuit/experiments/vnl5090.pdf`
- `evidence/model-baselines-final/claude/review-work/circuit/experiments/vnl5090.txt`
- `evidence/model-baselines-final/claude/review-work/circuit/experiments/tja.tmp`
- `evidence/model-baselines-final/codex/review-work/adjudicator/experiments/references/VNL5050.pdf`
- `evidence/model-baselines-final/codex/review-work/adjudicator/experiments/references/VNL5050.txt`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/TJA1051.pdf`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/TJA1051.txt`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/TLE9201_actual.pdf`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/TLE9201_actual.txt`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/VNL5050_supplier.pdf`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/VNL5050_supplier.txt`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/VNL5050_supplier_fetch.json`
- `evidence/model-release/baseline/review-work/circuit/experiments/drv8825.pdf`
- `evidence/model-release/baseline/review-work/circuit/experiments/drv8825.txt`
- `evidence/model-release/baseline/review-work/circuit/experiments/tja1051.pdf`
- `evidence/model-release/baseline/review-work/circuit/experiments/tja1051.txt`
- `evidence/model-release/baseline/review-work/circuit/experiments/tle9201.pdf`
- `evidence/model-release/baseline/review-work/circuit/experiments/tle9201.txt`

## T11 Distributor pages and web-search captures

- Origin: LCSC product and datasheet pages (`lcsc.com`, `szlcsc.com`), and
  web-search tool captures that quote STMicroelectronics, Analog Devices and
  LCSC pages.
- Upstream license: proprietary site content. The site terms were not
  reviewed.
- Publication status: requires owner confirmation before publication.

- `evidence/model-baselines-final/claude/review-work/circuit/experiments/t.tmp`
- `evidence/model-baselines-final/codex/review-work/adjudicator/experiments/references/LCSC.html`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/LCSC_C2680415_cn.html`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/LCSC_C2680415_product.json`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/web_driver_sources.json`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/web_pin_sources.json`
- `evidence/model-baselines-final/codex/review-work/circuit/experiments/references/web_vnl5050_sources.json`
- `evidence/model-release/baseline/review-work/circuit/experiments/identity-web.txt`
- `evidence/model-release/baseline/review-work/circuit/experiments/vnl-reference-web.txt`
- `evidence/model-release/baseline/review-work/circuit/experiments/vnl5050-primary-web.txt`
- `evidence/model-release/baseline/review-work/circuit/experiments/vnl5050-search-web.txt`

## Evidence archives

Each archive is one tracked file that holds material from several origins.
Counts are files per origin, taken from the archive listings on 2026-10-17.
"Readable copy" means the archive's copy of the tracked evidence directory of
the same name, so it has the same origins as that directory. "Generated" means
fontconfig caches and KiCad configuration created during the run. Fontconfig
caches index installed fonts; they are not copies of fonts. The KiCad
configuration includes copies of the T6 template library tables.

| Archive | T1 fixture | T3 donor | T4 | T5 | T6 stock KiCad | T7 | Readable copy | Generated |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `adjudicator-comparison` | 72 | - | - | - | 5,436 | - | 480 | 1,804 |
| `donor-baseline` | - | 29 | 50 | 151 | 2,045 | - | 18 | 52 |
| `donor-baseline-v2` | - | 29 | 50 | 151 | 2,045 | - | 22 | 52 |
| `donor-final` | - | 29 | 50 | 151 | 2,045 | - | 36 | 52 |
| `donor-verified` | - | 30 | 50 | 151 | 2,045 | - | 47 | 149 |
| `model-baselines` | - | 90 | 150 | 453 | 6,135 | - | 208 | 485 |
| `model-baselines-expanded` | - | 90 | 150 | 453 | 6,135 | - | 236 | 510 |
| `model-baselines-final` | - | 90 | 150 | 453 | 6,135 | - | 285 | 473 |
| `model-comparison` | 182 | - | - | - | 13,590 | - | 1,844 | 4,704 |
| `model-network-probes` | - | - | - | - | - | in readable copy | 20 | 1 |
| `model-release` | 24 | 30 | 50 | 151 | 3,857 | - | 550 | 843 |
| `model-repair` | 18 | - | - | - | 1,359 | - | 330 | 505 |
| `model-repair-final` | 18 | - | - | - | 1,359 | - | 319 | 503 |
| `model-selection` | - | - | - | - | - | - | 31 | - |
| `rc-demonstration` | 12 | - | - | - | 906 | - | 94 | 298 |
| `rc-final` | 12 | - | - | - | 906 | - | 107 | 298 |
| `rc-verified` | 18 | - | - | - | 1,359 | - | 181 | 447 |

The T1 column counts fixture files and requirement files captured with them.
The one generated file in `model-network-probes` is the probe's own
`protected.txt` input. Readable copies of donor evidence also contain T8 to T11 material where the
tracked directory does.

- `evidence/archives/adjudicator-comparison.tar.gz`
- `evidence/archives/donor-baseline.tar.gz`
- `evidence/archives/donor-baseline-v2.tar.gz`
- `evidence/archives/donor-final.tar.gz`
- `evidence/archives/donor-verified.tar.gz`
- `evidence/archives/model-baselines.tar.gz`
- `evidence/archives/model-baselines-expanded.tar.gz`
- `evidence/archives/model-baselines-final.tar.gz`
- `evidence/archives/model-comparison.tar.gz`
- `evidence/archives/model-network-probes.tar.gz`
- `evidence/archives/model-release.tar.gz`
- `evidence/archives/model-repair.tar.gz`
- `evidence/archives/model-repair-final.tar.gz`
- `evidence/archives/model-selection.tar.gz`
- `evidence/archives/rc-demonstration.tar.gz`
- `evidence/archives/rc-final.tar.gz`
- `evidence/archives/rc-verified.tar.gz`

Publication status: requires owner confirmation before publication, because
every archive except `model-selection` contains material from at least one
origin above.

## Reviewed files with no third-party content

These files match the automated categories because they name the donor, cite
KiCad text in tests, render the fixture, or record source state. Review found
them to be written for agent-ee or generated from T1 inputs already covered
above. They contain no copied third-party file content beyond identifiers,
paths and short quoted values.

- `docs/third-party-origins.md` (this inventory)
- `tests/test_metadata.py` (its category patterns name the donor)
- `README.md` (its license section names the donor)
- `docs/README.md`
- `docs/argon-assessment.md`
- `docs/evaluation-plan.md`
- `docs/integration-review-argon.md`
- `docs/reproducing-evidence.md`
- `docs/via-probe-correction.md`
- `tests/test_checks.py` (short KiCad text written for tests)
- `tests/test_execution_failures.py` (short KiCad text written for tests)
- `tests/test_findings.py` (short KiCad text written for tests)
- `evidence/model-validation/source-state-final.json` (donor checkout status)
- `evidence/verification/final-freshness-and-source-status.json` (donor checkout status)
- `evidence/model-repair-final/control/review-work/physical/experiments/back.pdf` (KiCad plot of the T1 fixture, inside a T1 directory)
- `evidence/model-repair-final/control/review-work/physical/experiments/front.pdf` (KiCad plot of the T1 fixture, inside a T1 directory)

Material outside the scope of this inventory:

- Outputs of the Claude, Codex and Agy subscription reviewers are recorded
  throughout `evidence/`. They are model outputs, not files from another
  project. Their provider terms were not analysed here.
- `fixtures/rc/PROVENANCE.md`, `fixtures/rc/requirements/`, `fixtures/closed-loop/`
  and `references/SOURCES.md` were written for agent-ee. They describe T1 and
  T10 material but do not copy it.

## Open questions for the owner

1. T1 and T2: confirm that the FOME-MC fixture files and the adapted `eda.py`
   logic may be published, and under which license.
2. T3, T4, T5 and T8: the FOME-Tech, rusEFI and Polygonus sources have no
   license grant that was found, and T5 includes a GPLv3 credit. Decide whether
   to obtain permission, keep only derived facts, or remove the copies.
3. T6: decide whether the archives should keep the stock KiCad library
   collections. If they stay, they need CC-BY-SA 4.0 attribution and the
   license document.
4. T7 and T9: decide whether to keep these copies with their upstream notices.
5. T10 and T11: decide whether to replace the copies with URLs, revisions and
   hashes.
6. The public GitHub visibility observed on 2026-10-17: decide whether that is
   intended while these questions are open.
