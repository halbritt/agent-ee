# Fable independent assessment (agent-413)

Recorded as durable evidence of the independent judgment formed before and
during reconciliation. Sources read directly: `fome-mc/toolchain/eda.py`
(whole file), `fome-mc/toolchain/versions.toml`, vendored kicad-happy v2.3.1
(README, script inventory), the rc_filter fixture, the donor tree, and this
repo's `docs/evaluation-plan.md` and `docs/contracts.md`. The other agent's
recommendations were not read.

## Architecture judgment

The snapshot + native-runner direction is correct and is independently
corroborated by a working prior implementation: `fome-mc/toolchain/eda.py`
already proves hermetic snapshots, content hashing of all inputs/outputs,
execution status separated from design findings, report-shape validation
(ERC sheet coverage vs. hierarchy walk; DRC silent-parity-failure detection),
and a `.meas`-validating ngspice runner. agent-ee's bwrap read-only candidate
is strictly stronger than eda.py's copy-by-convention protection and directly
satisfies evaluation challenge 6 (OS-refused writes to protected inputs).

Reuse mode: vendor/adapt, never call fome-mc in place. Running fome-mc's
eda.py writes into `fome-mc/build/` and `fome-mc/reports/`, and that repo is
actively modified by another session. Checks in this repo adapt eda.py's
validation logic with provenance noted, under the agent-ee executor API.

## Key recommendations carried into implementation

1. Stock KiCad libraries: identity matters, not possession. Capture only the
   libraries the design references (resolved from the project lib tables),
   plus the stock global table templates; record the dpkg-pinned KiCad
   version. Copying all of `/usr/share/kicad` per candidate is waste.
2. kicad-cli needs a writable `KICAD_CONFIG_HOME` and stock global library
   tables seeded into it, or ERC/DRC fail for non-design reasons. The
   executor sets `KICAD_CONFIG_HOME=/work/kicad-config`; checks pre-seed the
   tables from `inputs/stock/template/`.
3. Fill-state discipline: the candidate is read-only, so DRC/geometry always
   evaluate the as-saved zone fill. This is recorded as an explicit
   limitation on every board check; a refill variant would need a disposable
   copy and a new candidate.
4. Simulation honesty: derive the deck from KiCad's exported XML netlist
   (values and connectivity), not from fixture knowledge. Run ngspice via a
   generated control script (`-n -b`; `-b -r` silently skips `.meas`),
   validate `.meas` values for presence and finiteness, and independently
   parse the binary raw file (stdlib `array`, no numpy) checking point count
   and finiteness. A sim that "ran" but produced garbage must not pass.
5. Status vocabulary: execution `ok|failed|unavailable|timeout|malformed|
   not_run|interrupted|unsupported` with outcome `pass|defect|unknown|info`.
   Incomplete coverage (sheet-count mismatch, missing report keys, silent
   parity skip) is `malformed`/`unknown`, never a clean result — evaluation
   challenge 3.
6. Findings immutability: append-only records keyed by candidate/run/
   reviewer/finding id; repairs create a new candidate; closure only against
   fresh evidence. (Owned by coordinator/Argon; recorded here as the agreed
   model.)
7. Fixed criteria must be captured: the declared fixture requirements
   (0.62–0.65 V at 1 ms; 0.20 mm minimum signal track width) live in hashed
   candidate inputs and in check parameters, so challenge 1 (change a
   requirement → stale evidence) has an actual hash to bite on.

## Donor cautions

- The donor will not be ERC/DRC clean (legacy SamacSys `.lib/.mod`,
  uninitialized `fome-kicad-libs` submodule mapped to the shared
  `~/git/kicad-libraries`). Library resolution is where silent wrongness
  lives; submodule-pin vs. library-HEAD must be recorded as evidence.
- No invented manufacturing/current/thermal requirements for donor geometry;
  donor geometry checks report facts with source locators only.
- Coordinator carrier-netlist observation (2026-10-16): CAN2 termination on
  the actual carrier is R36=120 Ω and R37=120 Ω across the same nets (60 Ω if both fitted);
  R10=100k sits on different nets. The FOME DESIGN.md "triple 120" claim must
  not be repeated by reviewers without attached brain-board evidence.
- Subsequent native XML and saved-board inspection established both R36 and R37
  are DNP. The all-fitted 60 Ω calculation does not describe the specified
  population. This correction supersedes any inference of fitted parallel loading.
- Reviewer backends verified on this host: `claude` 2.1.302 and `agy`
  (gemini-4-argon-high listed by `agy models`). Subscription-only; quota or
  auth failure must surface as reviewer failure, never a fallback route.

## Residual risks accepted

- KiCad's XML netlist, not SPICE netlist export, is the simulation source;
  component value tokens are passed through to ngspice after a syntactic
  SPICE-scalar check. Components other than R/C/L (beyond connectors and
  power flags, which carry no two-terminal element) make `rc_transient`
  honestly `unsupported` rather than approximated.
- pcbnew bindings run as `/usr/bin/python3` inside the sandbox; the probe
  records `pcbnew.Version()` so a mismatch against kicad-cli is visible in
  evidence rather than assumed away.
