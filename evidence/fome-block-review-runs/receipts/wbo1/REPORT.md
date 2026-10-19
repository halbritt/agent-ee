# Pinned fome-mc wbo1 reference audit review

Outcome: **incomplete**; process state: `incomplete`.

Candidate: `46310254a330cbb063e68fcc16e7d081352143919b963630c5fc51c9aa5b09d0`
Run: `0f41455d-4252-48d9-b43d-e79dfa3df22d`

Native acquisition evidence for the rung-2 wideband reference. Incomplete engineering coverage; see COVERAGE.md.

## Checks

| Check | Execution | Design result | Summary |
| --- | --- | --- | --- |
| erc | ok | defect | ERC ran on 3 sheet instance(s) and reported 836 violation(s): {'warning': 834, 'error': 2}; 836 unwaived, 0 waived |
| netlist | ok | info | native XML netlist exported from a complete 3-instance hierarchy: 116 component(s), 101 net(s), 0 DNP |
| interface | ok | pass | 0 interface violation(s); complete=True |

## Initial reviews

- circuit: malformed; requested model gpt-6.1-sol.
- physical: malformed; requested model gpt-6.1-sol.

## Findings and dispositions

### check/erc: ERC ran on 3 sheet instance(s) and reported 836 violation(s): {'warning': 834, 'error': 2}; 836 unwaived, 0 waived

Disposition: **unresolved**.

Location: erc. Condition: check erc (erc).

ERC ran on 3 sheet instance(s) and reported 836 violation(s): {'warning': 834, 'error': 2}; 836 unwaived, 0 waived

ERC ran on 3 sheet instance(s) and reported 836 violation(s): {'warning': 834, 'error': 2}; 836 unwaived, 0 waived
- [work/erc/kicad-version/execution.json](<work/erc/kicad-version/execution.json>): check:erc
- [work/erc/kicad-version/console.log](<work/erc/kicad-version/console.log>): check:erc
- [work/erc/kicad-version/stdout.log](<work/erc/kicad-version/stdout.log>): check:erc
- [work/erc/kicad-version/stderr.log](<work/erc/kicad-version/stderr.log>): check:erc
- [work/erc/execution.json](<work/erc/execution.json>): check:erc
- [work/erc/console.log](<work/erc/console.log>): check:erc
- [work/erc/erc.json](<work/erc/erc.json>): check:erc

## Limitations

- No fabrication or physical-board qualification. Empty original board supplies the required schematic-review container only.
- Upstream Rev C parity, functional models, parts qualification, heater geometry and independent reviews are pending.
- text artifact partially supplied: work/erc/erc.json (100000/533919 bytes)
- text artifact partially supplied: work/netlist/design.xml (27604/159547 bytes)
- text artifact partially supplied: work/netlist/connectivity.json (0/47804 bytes)
- text artifact partially supplied: work/interface/interface.json (0/225796 bytes)

This result applies only to the captured candidate and declared criteria. Prototype and bench
validation, including operating conditions omitted from this review, remain necessary.
