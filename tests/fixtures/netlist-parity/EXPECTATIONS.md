Original independently authored intent contract, committed before adapter implementation.
The port's numbered pins distinguish IN/OUT/GND; both resistors have the SAME
symbol and value but are separate instances. Full ref/net renaming is equivalent.
A port pin1/pin2 exchange changes two nets. A VBUS/VCC label merge removes their
separation. Moving the label off series pin1 removes a required binding. Moving
shunt pin1 from output to input is a non-isomorphic same-part mutation. A pure
exchange of resistor instance identities with identical incidence is equivalent;
an instance-specific predicate without an immutable UUID anchor is unknown when
multiple mappings exist. Multiset exactly rejects extra or missing resistors;
at_least rejects missing but permits extras. Exhausted search never passes.
These are owned synthetic logical expectations, not observed KiCad output.

Explicit no-connect pins are accounted outside resolved nets. A native generated
unconnected singleton carrying `pintype=...+no_connect` may be ignored only for a
compatible declared no-connect physical pin. An unmarked off-pin label singleton
must remain a missing binding. Declaring NC on port3 and shunt2, with no ground
net, is a valid two-net topology and must pass an actual native export.

Stronger collapse regression: exchange series.pin2 and shunt.pin2 between output
and ground. The series resistor becomes input-to-ground and both shunt terminals
land on output. Every net retains exactly the same SET of (lib_id,value,pin)
tokens, but the physical instance graph is non-isomorphic and must be a defect.
`resolved.json` is not an authored expectation. It is the exact output of
`connectivity_ir.resolve_intent(intent.json, {Owned.kicad_sym})`, and the
`resolved` adapter rederives and compares it byte-for-byte at check time.
