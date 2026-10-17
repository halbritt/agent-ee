# Owned DC path analytic expectations

These expectations precede native qualification. All boards are original generated
fixtures, with no third-party design content. They test a declared ideal 1D DC
model, not measured copper, fabrication approval, thermal fields or pad spreading.

Declared material: rho_ref = 1.724e-8 ohm m at 20 C; alpha = .00393 / C;
qualified external temperature interval 20..80 C. Nominal thickness 35 um.
Ideal round SMD terminal contacts are centered on native track endpoints.
No component/contact resistance. Current is explicitly 1 A.

Independent formula: R = rho L / (width thickness). Each straight fixture has
width .25 mm. A 10 mm straight gives .019702857142857143 ohm. A 20 mm straight
and a 10 mm straight narrowed to .125 mm each give .03940571428571429 ohm.
A .03 V upper limit passes the control and fails both mutations at 1 A.
Series: two collinear 5 mm segments give .019702857142857143 ohm.
Parallel: four 5 mm segments form a diamond with two 10 mm branches, giving
.009851428571428572 ohm (each branch .019702857142857143 ohm).
Arc: semicircle radius 5 mm, angle pi rad, length 5*pi mm, gives
.0309491756273646 ohm (tolerance 1e-8 ohm for integer native geometry resolution).

Zero dimensional tolerance must reproduce nominal. Width factors [.9, 1.1]
and thickness factors [.9, 1.1] imply resistance bounds nominal/1.21 and
nominal/.81. Length factors must be explicitly [1,1]: length uncertainty that
moves junctions is not qualified. Width envelopes must preserve separation.
At 80 C resistance is nominal*1.2358. Total drop = I R, loss = I^2 R.
Series/parallel reduction must account for EVERY edge; dangling branches,
disconnected islands, bridges, zones, vias, extra pads, overlapping tracks,
interior contacts and ambiguous terminal identities must refuse.

Qualification requires retained raw native geometry, graph and all case solver
outputs with KCL/energy residuals. Missing material/stackup/temperature/contact
input or an unexpected native/tool/solver failure yields incomplete coverage.
Tampering retained inputs, extracted graph or solutions must fail public sealing
and witness validation. Scripted reviewers establish coordinator wiring only.

Substrate qualification revision: each owned board has an axis-aligned rectangular
outer Edge.Cuts outline from (20,20) to (60,45) mm. Copper dimensions and the
independent resistance expectations above do not change. Closed disjoint inner
rectangles may remove substrate; a (34,29)..(36,31) mm cutout crossing the straight
trace MUST refuse, while a (22,22)..(24,24) mm cutout may qualify. Missing, open,
curved, overlapping, nested or ambiguous boundaries refuse. Board and footprint
Edge.Cuts count together. Maximum-width conductor and terminal envelopes must
lie strictly inside retained substrate. Visible Reference, Value and user fields
on copper refuse as unmodeled graphics, just like board and footprint graphics.
