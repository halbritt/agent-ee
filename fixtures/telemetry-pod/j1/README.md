# Portable J1 courtyard test inputs

`tests.test_pod_j1_courtyard` uses the existing exact footprint at
`fixtures/fome-mc/ampseal/capture/ampseal.pretty/ampseal-23.kicad_mod` and the
two original geometry observation records in this directory. The records are
byte-identical to the reviewed J1 source-coordinate and native-registration
metadata; historical paths inside them are provenance, not runtime locations.
No vendor drawing or additional third-party CAD file is included here.

The production decision remains
`fixtures/telemetry-pod/electrical/j1-placement-exclusion.json`. Its four source
hashes are enforced by the test loader and production derivation. Fixture hashes:

| Input | SHA-256 |
|---|---|
| Existing AMPSEAL footprint | `09eeab39c4b2c21c8cca8b38da3c996540768856771830b1f57094843852e950` |
| `geometry-proposal-v2.json` | `1500fba44b93b59148d85d54d85af6733b01d5305eac4d50964c94e1dcaa65ea` |
| `native-registration.json` | `a1b032b0af0e99c54d03f5ab0d83a2f6b1fa92d2a606b1264c3ececa11bc4b16` |
| External TE drawing | `14bd5c3bb2ca16bfcf01918b678eab1aa19df10001c17735dd1f11356a6e4fe8` |

## CI provisioning outside tests and the tool sandbox

From the checkout root, run this explicit acquisition step on the outer host.
The PDF stays outside the checkout. A transport or hash failure stops the step;
do not replace the expected hash with the downloaded hash.

```sh
set -eu
J1_INPUT_DIR=$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/agent-ee-j1.XXXXXX")
curl --fail --location --retry 2 --max-time 90 \
  https://www.farnell.com/cad/2916153.pdf \
  --output "$J1_INPUT_DIR/te-776228-1.pdf"
printf '%s  %s\n' \
  14bd5c3bb2ca16bfcf01918b678eab1aa19df10001c17735dd1f11356a6e4fe8 \
  "$J1_INPUT_DIR/te-776228-1.pdf" | sha256sum --check --strict
export AGENT_EE_J1_DRAWING="$J1_INPUT_DIR/te-776228-1.pdf"
unset AGENT_EE_POD_INPUTS
AGENT_EE_REQUIRE_TOOLCHAIN=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests \
  python3 -B -m unittest tests.test_pod_j1_courtyard -v
```

For a later CI step, persist `AGENT_EE_J1_DRAWING` through that runner's normal
environment mechanism and make the same file available to the test process.
The full strict gate must inherit it too. No test performs a network request.
Missing or incorrect drawing bytes fail, in both ordinary and strict mode;
there is no skip or host-specific fallback. Native assertions still require
the declared KiCad toolchain and its `pcbnew` bindings.

Alternatively, set `AGENT_EE_POD_INPUTS` to a complete captured input root
containing all four source paths named in the decision. That explicit root
supplies the footprint and both metadata records as well as the drawing. If
`AGENT_EE_J1_DRAWING` is also set, it overrides only the drawing location;
every source must still match the unchanged production hash.

These checks preserve the nominal zero-added-margin front-only exclusion.
They do not qualify maximum body tolerance, assembly/fastener clearance,
connector orientation or installed height, publication rights, or physical
pod/bench acceptance. Existing source and qualification holds remain.
