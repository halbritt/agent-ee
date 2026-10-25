# Acquisition operating supplement — 2026-10-25

The captured `OPERATING.md` omitted the ADS1115 power-up reset-settling wait and
described approximately 8.69 ms without separating conversion time from startup
and bus overhead. The additive
[ADS1115 supplement](../../fixtures/acquisition-typed/operating-supplement/ADS1115-OPERATING.md)
corrects those instructions while preserving the original capture.

This is an agent-selected instruction correction under chief allocation
`d04af9d3-eca1-4d7a-b0fa-51d62535b503@1`, based on main
`e67d1cd9174c5075fd21576fa6aa574cca4fdc66`. It is not an owner requirement,
engineering judgment, witness or physical-host qualification. Chief review and
integration remain pending.

The supplement requires verified stable rail validity followed by the
manufacturer's approximately 50 µs wait before initial communication. It keeps
configuration-transfer success separate from conversion completion, requires
OS-ready polling for the requested conversion, and completes the previous
conversion before retriggering. The 8.6806 ms value describes conversion alone
at 128 SPS minus 10%; approximately 25 µs startup and I2C overhead are additional.
No approximate figure becomes a guaranteed manufacturer deadline. The 20 ms
host timeout remains an engineering failure policy; stale data is rejected.
The original high-impedance sequencing, rail verification, host bounds and all
other requirements remain in force.

## Fresh preparation

Use the original 198-file base and the accepted, separately retained 25-file CPL
and 14-file manufacturer-text trees. The new source is a committed fixture
directory, supplied through the existing generic added-input capability. Choose
a fresh private output whose parent exists; preserve the earlier prepared root.
Substitute the captured source locations for `BASE`, `CPL` and `TEXT` below:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m agent_ee.typed_overlay prepare BASE \
  fixtures/acquisition-typed/overlay.json --out /path/to/new-parent/prepared \
  --added-input cpl=CPL --added-input manufacturer-text=TEXT \
  --added-input operating-supplement=fixtures/acquisition-typed/operating-supplement
```

No preparer implementation change is required. The new overlay changes only
`added_inputs.operating-supplement`; the derived map gains only that input mount.
Requirement mappings, obligations and engineering intervals remain unchanged.
The established compilation command also supplies this third input when used;
this allocation ran preparation only, without native producers or a full suite.

## Verified identities and limits

| Item | SHA-256 |
| --- | --- |
| Supplement | `b909381ca17f2dfc57ace88221bbe87d7a743587419e9e0b348432d71f831f70` |
| New overlay | `24f3d3de25d4c9f80b07c174aaa94cfe072d16829241f410a91f9b91d15d2822` |
| Original `OPERATING.md` | `b6e702b341a234e38da4ea0fb5f0831bd1fa61038d78e6e098d0c06a1e678319` |
| ADS1115 captured text | `7e50ff262cb5be665ccf33cfd082bf9d46baaa807648f2b118cf3fc077b0d034` |
| Unchanged text capture manifest | `ee1c4d695cdd0ba20527a50bd73516dc2e955559a9bd59829edd5d35748fe43f` |
| New prepared manifest | `5962123caccf6406128de2a743deeaf866ef334ae8dd8d6860e03c057b754975` |

The public preparation completed successfully on a fresh private output. Its
accounting remains **47 rows / 14 absent, pending judgments**. Four source
citations resolve through `source_authority.verified_reference`; page and UTF-8
byte locators are recorded in the supplement. Exact arithmetic gives
`1 / (128 × 0.90) = 5/576 s = 8.680555… ms`.

All 198 originals retain inventory
`718b4619c597efdfb730727804614546fac174feacc93ec0fec2a7a39084cd54`.
Both existing additive input trees remain byte-identical, including final
polarity `23a0b7986e20c676c1ada1b5c09647743eb854b987b320104be741137468e851`.
The earlier prepared root is untouched. No judgment or witness was authored.

**Test caller limit:** `AcquisitionOverlayTests` currently supplies only CPL and
manufacturer-text sources. Its caller must also supply the committed
`operating-supplement` directory before that real-capture test can use the new
overlay. This allocation expressly restricts edits to fixtures/overlay/docs;
no test code or skip guard was changed and no full-suite result is claimed.
The successful public invocation above supplies all three sources explicitly.

Private verification and new prepared output:
`/home/halbritt/.cache/agent-verification/agent-ee-569-operating-supplement-20261025/`.
It retains `prepare.log`, `verification.json`, the original inventory, the prior
prepared inventory and `prepared/`. Actual host behavior, bus timing, reset
readiness, engineering applicability, supplier/physical results and acceptance
remain unverified by this documentation correction.
