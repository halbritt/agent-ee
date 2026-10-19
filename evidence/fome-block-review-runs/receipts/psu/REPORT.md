# fome-mc PSU block, rung-2 power reference (AGENTEE-106) review

Outcome: **incomplete**; process state: `incomplete`.

Candidate: `1d1a619470a38dc6646e7af098b799792d7236f34ba2aca4f40d899504c331b2`
Run: `877a075f-51d6-4cf2-a627-8ee5fb441305`

Captured fome-mc psu.kicad_sch at df304f3 without the CAN transceivers (31 parts), on an original standalone board: J1 supplies /12V_RAW and GND, J2 exposes the protected and regulated rails, J3 the CAN buses, and an mcu stub the power-good and CAN logic nets. Judge the block's input protection, reverse-battery block, LMR16020 buck, TLS115D0E sensor supplies, TLV757P 3.3 V LDO and capacitor selection against requirements.md, which restates fome-mc docs/DESIGN.md section 6.3 and the docs/BLOCK_LIBRARY.md power supply entry. block-contract.json cites those documents by pinned commit, path, Git blob and SHA-256 and carries none of their bytes. No layout is captured.

## Checks

| Check | Execution | Design result | Summary |
| --- | --- | --- | --- |
| erc | ok | pass | ERC ran on 3 sheet instance(s) with zero unwaived violations; 0 waived |
| netlist | ok | info | native XML netlist exported from a complete 3-instance hierarchy: 43 component(s), 34 net(s), 0 DNP |
| interface | ok | pass | 0 interface violation(s); complete=True |
| input-filter | unsupported | unknown | net names collide after SPICE node sanitisation |
| tvs-clamp | unsupported | unknown | unknown check kind 'datasheet_limits' |
| reverse-block | unsupported | unknown | unknown check kind 'active_model' |
| c12-part | unsupported | unknown | unknown check kind 'catalog_part_identity' |
| c4-rating | unsupported | unknown | unknown check kind 'catalog_rating' |
| brownout-floor | unsupported | unknown | unknown check kind 'active_model' |
| rail-regulation | unsupported | unknown | unknown check kind 'active_model' |
| capacitor-ratings | unsupported | unknown | unknown check kind 'catalog_rating' |

## Initial reviews

- circuit: malformed; requested model gpt-6.1-sol.
- physical: malformed; requested model gpt-6.1-sol.

## Findings and dispositions

No findings recorded. Acceptance still requires complete applicable execution and review.

## Limitations

- Checks of kinds datasheet_limits, active_model, catalog_part_identity and catalog_rating are not registered; they are declared so the requirements they serve are recorded as unknown coverage (AGENTEE-18, 19, 27, 29, 61, 137).
- rc_transient cannot model the L1/C6 filter: two PSU net names, /CAN+ and /CAN-, collide after SPICE node sanitisation, and the sheet has active parts that rc_transient does not model, so the check is expected to be unsupported.
- The fome-mc planning documents are cited, not captured: requirement sources resolve only against a local fome-mc object store holding the pinned commit, and stay unverified without one.
- No datasheet, catalog snapshot or layout is captured. The saved board is empty.
- The block entry is a draft: boundary voltage domains and currents are undeclared owner facts, and parts.json is a draft listing.
- text artifact partially supplied: work/interface/interface.json (12369/141965 bytes)

This result applies only to the captured candidate and declared criteria. Prototype and bench
validation, including operating conditions omitted from this review, remain necessary.
