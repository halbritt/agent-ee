# Inverse switch correction — 2026-10-24

The acquisition power simulation exposed a general simulator defect. Stock
ngspice 42 and maintained release 47 calculate the wrong resistance for a
logarithmic voltage-controlled switch when its on resistance exceeds its off
resistance. This can report a false circuit failure without a simulator warning.
The correction changes the minimum resistance to the smaller of both authored
resistances, in both control-threshold branches. No vendor model, board,
component value, source limit or electrical requirement is changed.

## Source and reproduction

[Upstream release history](https://ngspice.sourceforge.io/news.html) identifies
47, released 2026-08-11. Source observed at
`a80f6e3e95d51534905b1f23410a951802666656`, tag `ngspice-47`, from
`https://git.code.sf.net/p/ngspice/ngspice`.
`src/xspice/icm/xtradev/pswitch/cfunc.mod` SHA-256 is
`83c2f7c80125348f59b8bd37c51561bb37fcd6713630c466e47fc95196077d08`.
The existing limiter assumes the on resistance is the smaller resistance.
At an exact off threshold it therefore substitutes 1 Mohm for an authored
1 uohm. Just below that threshold, a separate branch uses the correct value.
The error also affects the interpolated transition, not only the endpoint.

The original independent circuit is a 1 V supply, switch and 1 ohm load. For
`Ron=1e6 Roff=1e-6 Von=1m Voff=0`, Ohm's law gives
`Vout=1/(1+1e-6)=0.999999000001 V` at zero control voltage. Both stock builds
instead produce `9.99999000001647e-7 V`. The exact native decks, libraries,
logs and raw traces are retained in `build/agent545/`:

- `ngspice-inverse-switch-42c`: stock 42 supported container.
- `ngspice-inverse-switch-47-stock`: isolated stock 47 source build.
- `ngspice47-stock-switch-suite`: 20-point independent test; four failures.
- `ngspice47-patched-switch-suite`: same 20 points after correction; all pass.

[verify.py](../../docker/ngspice/verify.py) checks both resistance orderings and
both threshold polarities, with outside-threshold values, both exact endpoints
and the midpoint. Expected voltages come from Ohm's law and the geometric mean
resistance at the logarithmic midpoint. No vendor circuit or generator result
supplies the expected answers. The image build runs this same qualification.

The ordinary native `active_spice` regression uses an independently specified
unity buffer, switch and load after the existing RC circuit. Stock 42 produces
`6.32101520504544e-8 V` instead of approximately `0.06321 V` at 1 ms. The regression
fails honestly as `ok/defect`; its log is `spice-inverse-switch-red.log`.

## Unchanged actual design

The complete actual native acquisition netlist is used with the captured TI
TPS7A20 model, Diodes BAT46W model, explicit 20 mA non-sinking input source,
1 mA external load and declared host transitions. The ADC power-only equivalent
assumes 300 uA; it is not an ADC functional model or a guaranteed load bound.
TI's model omits quiescent current and temperature effects. Nominal capacitors
omit bias, aging and ESR effects. Those limitations remain acceptance gaps.

| Simulator | Native output at 9 ms | Native output at 24 ms | Model-internal output |
| --- | ---: | ---: | ---: |
| Stock 42 |~1.650e-7V|~1.650e-7V|~3.30018V|
| Stock 47 |1.650051090e-7V|1.649976410e-7V|~3.30018V|
| Corrected 47, isolated build |3.299955614 V|3.299734744 V|Matches connected output|
| Corrected 47, supported ordinary review |3.299955614 V|3.299734744 V|No additional internal probes|

Evidence directories are `acquisition-power-internal-probe`,
`acquisition-power-internal-47-stock` and
`acquisition-power-internal-47-patched`. Differences in copied control/deck
files are absolute include/output path rebasing and the explicitly recorded
internal probe vectors. Device models and physical circuit are unchanged.
The earlier ideal-source, 100 mA and 2.2 uF experiments are retained counterfactual
diagnostics; none replaces the 20 mA specification or actual 10 uF native capacitor.

## Supported installation and remaining qualification

[build.sh](../../docker/ngspice/build.sh) fetches the exact upstream commit,
checks the reviewed source hash, applies the generic correction and builds 47
from the pinned container package snapshot. It preserves upstream license
notices and an installed-file SHA-256 manifest. Compilers and source remain in
the builder stage. The binary is at the normal `/usr/bin/ngspice` location,
so existing public checks use it without a special board adapter.

The supported image `agent-ee:exec-b5ff7ad833e5ea01` built successfully:
image SHA-256 `157e2c6fd1777245a570b7251dd6bbc6504cf12dc9091a5f4c127ef0c8742325`.
`/usr/bin/ngspice` SHA-256 is
`a238c8f410e1f59656a62b3cb12d4955cfbaca2d70db92b661b2bf98a9fc586b`.
All 28 installed file hashes match the build manifest. The image's 20 switch
checks and 102 Rust planner tests pass. The passive-corner qualification is
explicitly rebound to this binary and current parser/model sources, then
requalified against its analytic control, defect, zero-tolerance and identity
rejection cases. Its numerical limits and identity guards are unchanged.

The supported native suite passes 139 tests across active SPICE, templates,
vendor-model handling, passive corners, native checks, apt retries and doctor;
no skips. The exact commands and results are retained in
`build/agent545/ngspice47-native-regressions.{log,md}`. The same acquisition scope
now passes through the ordinary command:

```sh
ae-ci-exec . -- bin/agent-ee review \
  build/agent545/acquisition-power-startup.scope.json --checks-only \
  --out build/agent545/acquisition-power-startup-ngspice47-review --max-seconds 180
```

The native netlist check accounts for all 17 components and 12 nets. The power check
reports `ok/pass`, with 3.299955614 V before host drive and 3.299734744 V at declared
load. Captured PCB hash remains
`e802bb4a77374a33acf1d40e4b0551befa3dc92c64dd1340103da44182a26087`.
Checks-only review correctly reports overall incomplete; this bounded case does
not close the complete electrical requirements or independent review. Full
manufacturing acceptance and the whole-system goal remain unfinished.


Both ordinary RC integration gates pass in the new image: the control executes
and passes every declared check; the seeded wrong-value circuit fails its
functional transient check while ERC, DRC and geometry pass. Both retained runs
pass `agent-ee verify`. Logs, gate reports and exit records are
`build/agent545/ngspice47-rc-*`. The actual acquisition power run also passes
fresh verification and its bounded checks-only gate. No check is skipped.
