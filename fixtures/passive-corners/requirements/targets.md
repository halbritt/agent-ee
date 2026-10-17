# Qualification boundaries

Ngspice 42 with pinned executable bytes, bundled model/parser identities and
frozen solver controls; positive step amplitude 0.1 to 5 V, observation times
0.1 to 5 ms, stop at 5 ms, each corner RC time constant 0.1 to 10 ms. At most
four unique endpoint corners, one nominal analysis and eight scalar observations;
30 seconds per analysis and 150 seconds per ensemble, also bounded by run budget.

Unmodeled parts, missing tolerances, incomplete ensembles and tool/parser/model
changes cannot establish passing coverage. Temperature, loading, aging,
correlation, active/RLC behavior and physical validation remain outside this
qualification. Nominal empirical residuals cannot be transferred to hypothetical
corner designs, even when captured design dependencies match exactly.
