# Reference and model applicability

The reference architecture is a passive first-order low-pass, nominal R=10 kohm
and C=100 nF, driven by an ideal voltage step with no output load. The analytical
reference is Vout(t)=Vin*(1-exp(-t/(R*C))). Native ngspice verification uses the
captured populated passive components. A bench residual envelope is applicable
only to its stated stimulus and sample time under this unloaded model family;
it must not be extrapolated to loaded, nonlinear or temperature-dependent use.
