# Acquisition electrical qualification, 2026-10-24

The fresh sourced 17-component acquisition design is **not electrically or
manufacturing accepted**. At integrated revision
`d10eb6ce866702d8a40a16d04c84767840f199af`, the ordinary build generates editable
native files from the captured requirements, parts and circuit. The independent
checks below establish specific circuit properties and reject a consequential
error. They do not close the complete electrical requirements.

## Native circuit control and negative

Inputs are under `build/agent545/acquisition-qualified-inputs/`; the normal plan
is `build/agent545/acquisition-qualified-build.json`. The negative bundle changes
only D1's two terminal assignments in `circuit.json`, before the ordinary parts,
scaffold and schematic producers execute. No emitted native file is repaired or
substituted. The independent 41-rule contract is the same for both candidates.

```sh
ae-ci-exec . -- bin/agent-ee build run \
  build/agent545/acquisition-reversed-diode-build.json \
  --out build/agent545/acquisition-reversed-diode-build-native --through schematic

ae-ci-exec . -- bin/agent-ee review \
  build/agent545/acquisition-polarity-control.scope.json --checks-only \
  --out build/agent545/acquisition-polarity-control-review-02

ae-ci-exec . -- bin/agent-ee review \
  build/agent545/acquisition-polarity-reversed.scope.json --checks-only \
  --out build/agent545/acquisition-polarity-reversed-review-02
```

Use new output paths to repeat these commands. The retained inputs are required;
these commands are not yet a clean-install qualification recipe.

| Independent result | Unchanged control | Reversed D1 |
| --- | --- | --- |
| Native ERC | Zero unwaived violations | Zero unwaived violations |
| Generator intent versus native netlist | Exact match | Exact match |
| Physical inventory | 17 components, 12 nets, zero DNP | Same inventory |
| Independent electrical topology | 41 satisfied, zero unknown | Two violations: `D1-2-1`, `D1-1-1`; zero unknown |

Both review commands report incomplete because independent review is unfinished;
read the individual check results. The reversed diode would prevent the intended
forward supply path and compromise the polarity-protection intent. ERC and
round-trip parity alone cannot establish that the intended circuit is correct.
The independent check rejects the incorrect connections. Earlier review capture
attempts, refused for missing baseline mappings, remain retained and do not count
as executed tests.

## Declared electrical arithmetic

`build/agent545/acquisition-electrical-screening-review/` runs the ordinary native
review with the same topology rules and 24 exact interval assertions. Its
`electrical-intervals` result is **ok/unknown**, not pass: 17 bounded assertions
satisfy their declared targets and seven retain unbounded operands. The equations
are authored engineering models, not inferred circuit physics. Operand origins,
applicability and unresolved bounds are captured in
`acquisition-electrical-parameter-origins.json`. The original requirements remain
unchanged; this is partial screening and not their complete acceptance coverage.

For nominal 25 C operation under the recorded applicability assumptions:

- Regulator pin voltage is 3.2505–3.3495 V from the TPS7A20 DBV specification.
  The minimum R6 load is 1.46287 mA, above the specification's 1 mA condition.
- Internal plus declared external load is at most 5.91364 mA with both I2C lines
  low. This excludes regulator ground current, whose loaded maximum is unresolved.
- At 100 pF and 2.2 kohm ±1%, the authored I2C rise calculation gives at most
  188.27006 ns, below 300 ns. Sink-current and declared leakage margins also pass.
- The input-filter time constant is at most 11.655 us. Twelve time constants fit
  within one conversion interval at the fastest declared 128 SPS tolerance corner.
  This does not qualify ADC input impedance, total error or antialias rejection.

The mandatory unresolved results are individually visible:

| Assertion | Missing substantiation |
| --- | --- |
| `input-current` | Applicable maximum regulator ground current under load; the no-load maximum and loaded typical value are different conditions |
| `rail-at-u2` | Final copper and return-path voltage drop at U2 |
| `rail-at-j2` | Final copper and return-path voltage drop at J2 |
| `output-effective-capacitance` | Applicable C2 capacitance after voltage bias, temperature, tolerance and aging |
| `output-effective-esr` | Applicable C2 effective ESR bound |
| `startup-evaluated` | Startup with the declared 20 mA source and actual capacitors; the 1 uF datasheet test does not directly establish this case |
| `power-removal-reverse-voltage` | Peak OUT–IN during supply removal, compared with the regulator's 0.3 V absolute-maximum difference |

Temperature extension, ADC error/applicability, host configuration, final physical
constraints and manufacturing checks also remain open. A subset pass cannot close
those obligations.

## Captured primary sources and next work

The current [TI TPS7A20 product page](https://www.ti.com/product/TPS7A20) links
transient model SBVM961. Its captured archive and original model are retained in
`build/agent545/acquisition-sources/tps7a20-spice/`. The model explicitly excludes
temperature and ground/quiescent-current behavior. It therefore cannot supply the
missing loaded-current bound or qualify temperature behavior merely by running.
Its applicability to startup and power removal must be inspected separately.

The [Samsung component page](https://product.samsungsem.com/mlcc/CL21A106KAYNNN.do)
and [Samsung characteristic sheet](https://media.digikey.com/pdf/Data%20Sheets/Samsung%20PDFs/CL21A106KAYNNNE_Char.pdf)
identify C1/C2 and provide typical voltage-bias and ESR data. Typical curves remain
design-reference data, not manufactured minimum/maximum guarantees. Preserve that
distinction when selecting engineering margins and physical validation obligations.

Next: establish applicable capacitor and regulator models/bounds, then carry their
checks and the existing placement constraints into the complete build scope.
No requirement, native severity, or failed result has been weakened.

## Subsequent native model evidence

The [ngspice 47 correction](../benchmarks/ngspice-inverse-switch-2026-10-24.md)
establishes the unchanged circuit's nominal startup under the declared 20 mA
source. The [native copper check](../benchmarks/dc-rail-2026-10-24.md) now computes
the actual saved board's ideal supply/return bounds: approximately 5.1072 mV
combined at 20 mA/25 °C. The earlier unknown records remain valid historical
evidence; they are not rewritten as passes. These later model results still
require applicable physical dimension/material/contact bounds, capacitor
corners and the remaining component/operating conditions before closing the
corresponding whole-design requirements.
