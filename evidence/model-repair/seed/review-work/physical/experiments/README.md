# Physical review experiment record

Scope: saved passive RC fixture against its captured ideal unloaded 1 V step and
0.20 mm minimum track width. No ECU assumptions or unprovided part ratings.
All candidate reads were from /candidate; no candidate was saved or refilled.
Citations for this directory use review-work/physical/experiments/<filename>.

## Commands and versions

Main command:
`/usr/bin/python3 /work/experiments/physical_review.py > /work/experiments/physical_review.stdout.txt 2> /work/experiments/physical_review.stderr.txt`

Supplemental command:
`/usr/bin/python3 /work/experiments/saved_fill_probe.py > /work/experiments/saved-fill-probe.stdout.txt 2> /work/experiments/saved-fill-probe.stderr.txt`

Both final invocations exited 0. Main script's subprocess arguments, working
directory, exit codes, stdout/stderr files are in commands.json. Environment
overrides for stock KiCad libraries and config under experiments are explicit in
the script. Versions: KiCad CLI/pcbnew 10.0.7; ngspice 42; Python version retained
in python-version.stdout.txt. No fetched references or network needed.

Exploratory read-only commands used pwd, rg --files for AGENTS.md (none found),
ls on /work, /candidate/project, /evidence/work and /evidence/checks, cat on the
requirements, project configuration/library tables, supplied check artifacts,
and rg -n on original schematic/PCB component values, tracks, via and zone.
Initial version/module probe `/usr/bin/python3 -c 'import pcbnew;
print(pcbnew.Version()); import shapely; print(shapely.__version__)'` showed
Shapely unavailable in system Python; no Shapely dependency was used.
pcbnew API introspection used dir and method __doc__ for connectivity/fill.

The first main invocation stopped on GetUnconnectedCount requiring its boolean
argument. That attempt's stderr is preserved in first-attempt.stderr.txt.
The final script supplies False and a layer argument for via width. The final
pcbnew invocations still emit three PROPERTY_ENUM assertions at import, also
seen in the supplied geometry console. Parsed values and geometry were checked
against original text and native DRC; these diagnostics are not design defects.

## Outcomes

- Fresh ERC, DRC with schematic parity and XML export reproduce the supplied
  records: zero reported ERC/DRC violations, zero unconnected items/parity
  issues, 3 components and 3 nets. See erc.json, drc.json and design.xml.
- native-inspection.json records the original PCB coordinates, values, pads,
  all five 0.25 mm tracks, 0.6/0.3 mm ground via and saved B.Cu zone. The 20 x
  12 mm outline is closed, and the footprints and copper lie inside it.
- The physical path is J1.1 -> R1.1 on F.Cu, R1.2 -> C1.1 and J1.2 on F.Cu,
  C1.2 -> via (113,100) -> saved B.Cu GND fill -> J1.3. The OUT branch terminates
  on the horizontal OUT segment at (107.5,100). Connectivity APIs list direct
  neighbors, not all transitive pads; empty direct-pad lists on a track/via do
  not mean an open circuit. DRC reports no open connections.
- Saved-fill-probe.json samples all four diagonal J1.3 thermal spokes beginning
  at radius 0.6364 mm (inside its copper annulus: drill radius 0.5 mm, pad radius
  0.85 mm) and extending into plane. Every sample is in saved fill. The ground
  via center is also in the same single saved fill outline. The J1.3 pad center
  being outside fill is expected for a drilled pad/thermal, not an open return.
- Source-expression parsing independently confirms R1=100k and C1=100n in both
  original native files. rc-calculation.json derives tau=10 ms and
  Vout(1 ms)=0.095162581964 V, Vout(5 ms)=0.393469340287 V. The required 1 ms
  window corresponds to tau=0.95254..1.03350 ms. Failure follows directly from
  the saved values, independent of simulation status.
- independent-step.cir uses those PCB-derived values and zero initial bias,
  delays the input ramp to 100 us, measures relative to its end, and limits
  timestep to 0.1 us (50 times smaller than the supplied 5 us step). The input
  measurement is 1 V; output measurements are 0.09516263 V and 0.3934694 V.
  Agreement with exact ideal-step calculation and supplied ngspice log excludes
  timestep, initial bias or measurement-origin mistakes as the cause here.
  The 1 ns ramp introduces only about 5e-8 V error at the 1 ms measurement.
- Initial ideal current and resistor power at 1 V are 10 uA and 10 uW. These
  are stimulus calculations only, not thermal/ampacity qualifications.
- native-hashes.json confirms PCB/schematic match packet hashes and all three
  checked native project files remained unchanged.

## Coverage omissions and limitations

No refill, fabrication, assembled-board measurement, Gerber/drill CAM inspection,
3D/mechanical fit, solder-process or fab-specific capability qualification.
No part numbers, voltage/power ratings, copper weight or thermal environment
were supplied; ampacity and temperature rise are not established by track width.
No tolerance, temperature, dielectric bias, parasitic extraction, external load,
source impedance, ADC sampling, EMI or hardware verification. Saved polygon
sampling is supplemental to native DRC, not an exhaustive geometric proof.
Existing ignored ERC/DRC checks remain as listed in their native reports.
The supplied binary result.raw was not parsed; supplied deck/log and independent
analytic/simulation measurements were checked instead. The 5 ms measurement has
no acceptance bounds, so it supplies information only. No prior reviewer
experiments or judgments were consulted.
