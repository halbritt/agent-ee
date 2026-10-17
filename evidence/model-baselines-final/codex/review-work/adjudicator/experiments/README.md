# Adjudication evidence and limits

Candidate: 69119fab9d25319b3bf376eca634b13104ba76b7417416b85a2a64a4e78a6ab4
Run: f3c237c2-3a08-4617-aaba-f04c47bbe98f

## Method and commands

All candidate files were read only. No native circuit/board edits, fill operations, or repair claims. Initial read commands were `ls`, `find`, `rg`, `cat`, and `sed` on /candidate/project, /evidence/work, /evidence/checks, and relevant /experiments/circuit and /experiments/physical evidence; no credentials were read. The filesystem search found no AGENTS.md. Reviewer conclusions were treated as hypotheses; no vote was taken.

`python3 /work/experiments/audit.py > /work/experiments/audit.log` independently tokenizes native KiCad S-expressions, reads complete native ERC/DRC JSON and XML, compares numbered PCB pad nets, checks the alias, extracts component identities and exclusions, computes passive values, and hashes native files. Python 3.12.3. Results: audit.json and audit.log. The 694 numbered saved-board pad instances match the packet XML nets. Fresh native export matches packet connectivity. Alias files are byte-identical. See integrity.json for post-experiment native hashes.

`python3 /work/experiments/run_native.py`: commands.json records exact argv, exit status, and time for versions, fresh native XML export and initial drill export. KiCad CLI 10.0.7; ngspice 42; pdftotext 24.02.0. command-0.log through command-4.log retain output. The first drill export used KiCad's combined-file default, producing Polygonus-Universal-Base.drl; an inspection attempt at an assumed *-PTH.drl failed (missing file). This was an output-name assumption, not a board check failure. `kicad-cli pcb export drill --help` exposed --excellon-separate-th; final_checks.py uses it explicitly. No candidate modification was required.

`python3 /work/experiments/retrieve.py`: freshly retrieves the pinned Polygonus README/MCU schematic, supplier product HTML and manufacturer-authored VNL5050 PDF mirrored by that supplier. retrieval.json records source URLs, hashes, sizes and timestamps. The ST VNL5090 direct PDF request timed out; web retrieval of ST's published DS8791 Rev10 succeeded and is retained in web_sources.json. Web tools also independently checked the supplier C2680415 mapping and pinned module pinout. These retrieved module sources describe an interface; they are not evidence of the actual assembled ECU revision.

`python3 /work/experiments/followup.py`: followup_commands.json records pdftotext conversion and standalone export of the fetched MCU sheet; module_interface.json independently derives the eight GPIO nodes and U26's +3V3 supply pins. This confirms the previous reviewer's extraction against original reference source. Manufacturer VNL5050 PDF bytes also match the prior source hash. Native symbols and board SOT-223 pads, not prose, select the N3 package under either part identity.

`python3 /work/experiments/final_checks.py`: final_commands.json records separate PTH/NPTH drill export with report and resistor-only ngspice DC analysis. reset_bias.cir/log retain stimulus and result. integrity.json records native hash verification. The drill files use absolute millimetres; T10 is 2.2 mm, T12 is 4 mm. No active driver or load model was used.

## Findings

### check/erc

Confirmed 353 entries, 348 warnings and 5 unexcluded errors across ten sheet instances. Root /sheets/0/violations/0..4 are missing power-output drivers on GND, VMOT and 12V_IGN; unused A1 FLT; and two power-output typed J2 sensor-rail pins tied together. Accept the failed declared ERC criterion. Exit zero means execution succeeded; the command does not request a violation exit code. This is an established native-rule/check defect, not proof that external power is absent or two independent regulators fight. Native XML confirms connector-fed power and a single module interface; no such electrical failure is inferred. No native ERC pass or repair is asserted.

### check/drc

Confirmed 142 board violations plus 114 schematic-parity entries and zero unconnected items. There are 48 excluded board entries, including ALL 13 severity-error entries (three starved thermals, five padstack and five annular-width); 94 unexcluded board warnings remain, plus 114 parity warnings. Accept the failed declared DRC criterion, retaining exclusions exactly as saved. Do not infer 13 unwaived manufacturing defects from the raw error count. The parity record includes J2 footprint identity and BOM-attribute differences, as well as metadata differences. A clean numbered-pad net comparison is narrower than rule/parity compliance and does not make DRC pass. Existing fills only; no refill or independent DRC rerun here, because complete fresh packet check evidence already establishes its reported outcome.

### circuit/circuit-001

Accept for the stated condition: Polygonus direct GPIO commanded high. Native root J2.12..15 and J2.24..27 connect the eight added channels through populated 1k RN2/RN4 branches to SOT-223 input pad1; RN1/RN3 pulldowns are on the GPIO side. Native board implements the same nets. The pinned upstream module directly connects these channels to U26 GPIO, powered at +3V3; README distinguishes them from buffered IGN1..8.

Applicable limit: manufacturer VNL5090N3 DS8791 Rev10 Table2 ties INPUT to Vsupply; Table6 specifies 3.5 V minimum; Table8 threshold may reach 3.5 V. VNL5050N3 DocID15917 Rev6 Tables2/6/8 has the same limits. The S5 variant's 2.1 V VIH is inapplicable. With nominal high GPIO 3.3 V and nonnegative input current, Vin = Vgpio - 1k*Iin <= 3.3 V, giving -0.2 V margin even ideally. LS9/10 pullups do not raise a driven push-pull GPIO above its imposed high level; their loading does not fix this bound. Guaranteed turn-on/operating-range compatibility therefore fails under either saved part identity. No exact current, damage, universal failure or thermal outcome is claimed. The acceptance concerns documented module compatibility, not proof of fitted hardware behavior or Atlas equivalence.

Sources: [ST VNL5090 datasheet](https://www.st.com/resource/en/datasheet/vnl5090n3-e.pdf), [supplier-hosted ST VNL5050 datasheet](https://atta.szlcsc.com/upload/public/pdf/source/20210702/812CDCDABBE6A13FE581F089AB8A7F27.pdf), [pinned Polygonus source](https://raw.githubusercontent.com/FOME-Tech/Polygonus/b3176cb1de7d0f5b9380ecebc1d007e2f365a6de/README.md).

### circuit/circuit-002

Defer the major concern. Native pullup/pulldown/series branches are verified. With ideal 5 V and undriven, unloaded GPIO, A1 and driver inputs, Vcontrol=Vdriver=5*1k/(1k+1k)=2.5 V; Rsource at driver=1k+(1k||1k)=1.5k. Independent ngspice reproduces it. A1 is connected and input loading is not known; Vdriver=2.5-1500*I applies to driver input current only with other loads omitted. Neither extrapolating the 5 V supply-current specification to 2.5 V nor assuming the MCU reset state/duration is justified. The threshold interval overlaps the bias, so the concern cannot be dismissed. No startup waveform or actuator model/measurement resolves actual conduction; cannot promote to established actuation defect or downgrade the material concern to minor information.

### circuit/circuit-003

Note as a supported minor component-identity concern. All repeated native driver symbols and saved board properties say VNL5090 but LCSC C2680415; freshly retrieved supplier JSON-LD says VNL5050N3TR-E. The devices differ. The packet declares ERC/DRC/netlist/geometry criteria, but no particular on-resistance/current-limit target or purchasing-field consistency requirement, and no substitution failure is established. Thus the observation alone does not establish an additional functional defect. It is not a duplicate of the operating-range defect: either identified N3 device fails that range comparison.

Source: [supplier product record](https://item.szlcsc.com/2772332.html), JSON-LD Product sku/mpn retained in references/LCSC.html and audit.json.

### physical/physical-001

Note, preserving the original minor concern classification. Native J1 five no-net anonymous thru_hole pads have size=drill (three 4 mm, two 2.2 mm), zero nominal annulus. Separate export places them in PTH output. Native minimum annular width remains 0.1 mm; all five annular checks AND all five padstack checks carry explicit saved, empty-rationale exclusions. These are pre-existing per-item exceptions, not changes or new waivers by the adjudicator. The raw flagged geometry remains recorded under accepted check/drc. No separate unwaived geometric criterion, required mounting-hole plating/finished-fit specification, or demonstrated fabrication/electrical failure establishes a further defect. Plating/fit are unverified; this is not manufacturing approval.

## Scope observations and limitations

Both R36 and R37 are 120 ohm and DNP in native schematic and saved board. Their saved assembly contributes zero differential terminating conductance on CAN2. Packet geometry and netlist agree with U20/U21 CANH/CANL and J2.73/.74, J1.15/.11. This does not establish end-to-end termination or communication. Actual module/harness termination, bus lengths/bitrate, and waveform tests are absent. No mandatory 60-ohm short-bus requirement was invented, no second termination was assumed, and no missing behavioral execution was converted to pass.

No hardware tests, validated active-device simulation, automotive transients, ignition-energy, wideband-loop, EMC, thermal, complete mechanical or integrated FOME-MC qualification. These remain the packet's limitations. No claim of repair or candidate approval.

## Final output validation

`decisions.json` preserves all six exact IDs. Citation files were checked for existence. `validation.log` records that the local /work/schema.json requires duplicate_of on every entry, conflicting with the user's explicit instruction to include it only for duplicates. There are no duplicate decisions, so it was omitted. All remaining local schema constraints validate after removing only that conflicting local required-key rule. The final response follows the user's key-omission instruction and the response schema's available properties.
