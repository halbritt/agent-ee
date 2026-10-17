# R1 adjudication evidence

Scope: only R1 at externally maintained /IN=48 V DC and /OUT=0 V indefinitely, using the explicitly assumed fitted-part continuous limit of 0.1 W. No actual 0805 part rating is inferred from its footprint.

Commands used for inspection (all succeeded, except the file search may omit nonexistent paths without consequence):

```
pwd
rg --files /candidate /evidence /work/experiments -g 'AGENTS.md' -g 'requirements.txt' -g 'rc_filter.kicad_sch' -g 'rc_filter.kicad_pcb' -g 'design.xml' -g 'connectivity.json' -g 'erc.json' -g 'drc.json'
for p in /AGENTS.md /work/AGENTS.md; do if [ -f "$p" ]; then cat "$p"; fi; done
python3 -V
cat /candidate/requirements.txt /evidence/work/netlist/connectivity.json /evidence/work/erc/erc.json /evidence/work/drc/drc.json
rg -n -A 65 -B 14 'property "Reference" "R1"|label "IN"|label "OUT"|xy 86.36 59.69|xy 86.36 67.31' /candidate/project/rc_filter.kicad_sch
python3 /work/experiments/r1_power.py
```

Tool: Python 3.12.3, standard library only; Decimal arithmetic for the calculation. The last command writes r1_power_result.json and asserts agreement of native value/board pad nets and the fresh exported XML netlist. No credential files were read. No native candidate file was written.

Native schematic: R1 instance at (86.36,63.5), angle 0, Device:R, value 10k, DNP no, UUID c5c38e1a-7593-5c99-b971-2f9d2e7bef78. Library pins are at relative (0,3.81) and (0,-3.81); with schematic Y inversion they connect at (86.36,59.69) and (86.36,67.31). The upper wire reaches IN label at (86.36,57.15), lower wire reaches OUT label at (86.36,69.85). Lines 1252-1285 identify R1 and its value; lines 725-734, 775-784, 815-824, 845-854 establish these endpoint label connections. The exported XML nets independently list R1.1 on /IN and R1.2 on /OUT. Native PCB R1 footprint also assigns pad 1 /IN and pad 2 /OUT.

Outcome: voltage across R1 = 48-0 = 48 V; current = 48/10000 = 0.0048 A; continuous power = 48^2/10000 = 0.2304 W, also verified as VI and I^2R. This is 2.304 times the stated 0.1 W limit, exceeding it by 0.1304 W. The external clamp sustains the voltage across R1; capacitor charging cannot eliminate this DC dissipation. Accept challenger/one as an established defect. The alternative reviewer result 0.002304 W is 100 times too small for the specified 48 V and native 10k value and cannot rebut the finding.

Existing native check evidence: KiCad 10.0.7 ERC reports no violations, DRC reports no violations/unconnected items/parity issues. Those rule checks contain no R1 continuous power calculation and therefore do not resolve or contradict the exercise failure. Their execution was not rerun; existing fresh outputs were inspected directly. Geometry has no separately declared limit. No failing check is dismissed or made to pass.

Limitations: nominal DC analytical evaluation only. No temperature, damage time, actual physical failure mode, unprovided tolerance, or qualification claim. No new simulation is needed to determine dissipation for a resistor whose endpoint voltages are fixed by the declared condition. No proposed repairs or scratch design changes are evaluated.
