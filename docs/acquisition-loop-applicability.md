# Nominal 25 C capacitor and regulator application assessment

This is a candidate for engineering review, not a file-release or physical
validation result. It applies to the generated ADP7104/Rubycon MU development
reference under OPERATING.md. No frozen G4 reference replacement is authorized.

The ADP7104 Rev I data sheet gives two recommendations: Table 2 specifies
0.001–0.2 ohm ESR, and the output-capacitor discussion on page 17 recommends
at least 1 uF with ESR at most 1 ohm for stability. Neither passage specifies
a measurement frequency. Both statements remain part of the evidence. This
assessment does not assign a new frequency to a manufacturer guarantee or
claim that the film part's 1 kHz ESR meets the 0.2 ohm recommendation.

For this nominal application, the relevant new evidence is the complete
feedback response with the selected capacitor's frequency-dependent model.
The regulator model has useful loop dynamics through its exposed Sense pin.
A dual voltage/current injection pilot matched a separate analytically soluble
control circuit within 1.1e-13 relative error. The regulator's voltage-injection,
current-injection and unmodified copies had identical 3.299676134 V DC output.
The 1 uF/0.05 ohm reference crossed unity at 229.37 kHz with 69.65 degrees of
phase margin. This establishes measurement suitability, not model calibration.

The comparison covers two regulator input voltages (4.355 and 5.25 V), the
released and maximum active modeled loads, and low/nominal/high film-capacitance
sensitivities. Each has independent voltage/current injection copies. All
capacitor values receive the small +0.26% transfer suggested by Rubycon's
approximately +520 ppm/C coefficient from 20 to 25 C. Resistance and inductance
retain the reference values. This is a nominal engineering extrapolation, not
a guaranteed joint RLC, lot, aging or temperature envelope. The capacitor's
tolerance minimum of 1.76 uF exceeds both the 0.7 uF effective-capacitance
minimum and the 1 uF nominal recommendation. Rubycon's technical note supports
the absence of a material DC-bias capacitance reduction for this film type.

The first comparison exposed LTspice's implicit 1 milliohm series resistance
per inductor. An independent passive calculation reproduced that interpretation.
The final twelve film cases explicitly set Rser=0 on all three inductors.
They preserve every vendor R/L value and the declared capacitance scaling.
The resulting film response has one falling unity crossing per case:

| Quantity | Final film cases |
| --- | --- |
| Phase margin | 52.199–60.508 degrees |
| Unity-crossover frequency | 175.308–211.187 kHz |
| Film ESR at each case's crossover | 0.018153–0.019333 ohm |
| Negative-real-axis crossings in the evaluated band | None |
| Independent capacitor impedance calculation error | Below 3.7e-10 relative |

The minimum margin occurs with the high-capacitance sensitivity, 5.25 V input
and released load. The additional simulator resistance had improved margin
by less than 0.1 degree. The reference comparison with ideal 1 uF capacitors
and 0.001, 0.2 and 1 ohm ESR gives 65.95–82.72 degrees across the same voltage
and load states. These are numerical model observations, not measured hardware.
No infinite gain margin is claimed from the absence of a crossing in a finite
frequency band.

The final analysis uses 100 Hz–10 MHz, inside Rubycon's stated model range.
Raw data below 100 Hz remain retained but support no capacitor-applicability
claim. The approximately 0.55 ohm ESR near 1 kHz is not the ESR seen at the
observed loop crossover. The model includes that frequency dependence instead
of replacing it with a single favorable number. The crossover ESR is roughly
an order of magnitude below Table 2's upper recommendation, and the complete
return-ratio calculation retains positive phase margin. This supplies a
specific nominal application basis for the selected film capacitor despite
the two differently stated data-sheet recommendations. It does not establish
a universal frequency-specific product limit absent from those sources.

C1 is included as a film capacitor in each powered loop case. The source uses
the retained current-limited model's local 0.01 ohm derivative; every DC case
remains below the 20 mA limit. The model is not a qualification of arbitrary
external supply or cable impedance. C3 retains the earlier separate X7R
sensitivity. C1/C2 capacitances are scaled together; the cases are not claimed
to exhaust independent manufacturing corners.

The native board bound before this investigation has 17 components, 18 checked
functional relationships, zero opens and passing ERC/DRC. Its input and output
local-loop lengths are 7.107110 and 5.894679 mm. The new evidence changes no
component, circuit, coordinate, trace or numerical geometry limit. Those native
locality checks support using a lumped local model; they do not supply extracted
board parasitics or a bench stability measurement. Fresh checks and judgments
must bind the complete package containing this analysis.

Supply removal remains a separate time-varying condition. The retained
physical-open transient releases host loading 499 us before source removal
and records zero source current after disconnection. It uses the earlier
capacitor sensitivity and LTspice's implicit inductor resistance. It remains
explicitly conditional evidence, not a new exact-RLC removal run. This AC
analysis makes no gain-margin claim after the regulator loses its operating
point. OPERATING.md's high-impedance sequencing and zero external-load conditions
remain in force. Supplier preview and physical startup/shutdown, load-step and
stability follow-up remain unperformed.

Reproduce the calculations from analysis/film-loop/REPRODUCE.md. Raw native
logs retain the failed Newton/Gmin initialization and successful source-stepping
recovery. The earlier default-resistance result and failed crosscheck are
retained as historical evidence; neither is substituted for the final result.

Method sources: [ADI sense-pin injection](https://www.analog.com/en/resources/analog-dialogue/articles/loop-response-measurement-options.html),
[ADI dual-injection method](https://ez.analog.com/ez-blogs/b/engineerzone-spotlight/posts/so-many-middlebrooks-which-stability-method-wins-part-5-of-7),
[LTspice AC and bias commands](https://github.com/analogdevicesinc/ltspice-reference/blob/main/ai_ref/SIMULATION-COMMANDS-REFERENCE.md),
and [LTspice inductor defaults](https://github.com/analogdevicesinc/ltspice-reference/blob/main/ai_ref/CIRCUIT-ELEMENTS-REFERENCE.md#l--inductor).
The captured ADP and Rubycon source documents remain in power-reference and
film-reference/engineering. The model evidence does not override their limits.
