# ADS1115 power-up and conversion timing supplement

Agent-569, 2026-10-25. This is an agent-selected instruction correction under
chief allocation `d04af9d3-eca1-4d7a-b0fa-51d62535b503@1`. It supplements the
captured `OPERATING.md` identified below. It is not an owner requirement,
engineering judgment, witness, or evidence that a physical host passed testing.

Apply these clarifications to initial ADC communication and single-shot timing.
All other operating, electrical, mechanical, assembly and physical follow-up
requirements in the original `OPERATING.md` continue to apply.

## Power-up communication

1. Before applying J1 power, keep host SDA/SCL and both analog sources high
   impedance as the original contract requires. Do not use host pull-ups powered
   by an independent supply.
2. Verify that the board rail is stabilized within **3.234–3.366 V**, using the
   original power-good or measured-rail procedure. An elapsed delay does not
   establish valid power.
3. After VDD has stabilized, wait the manufacturer's **approximately 50 µs**
   power-up reset-settling interval before initial I2C communication [A]. Keep
   the host interface released until this sequence permits communication. The
   datasheet gives an approximate interval; it does not establish a guaranteed
   maximum reset time or prove readiness of a particular host/board by delay alone.

Preserve the original high-impedance sequencing before shutdown and while power
is absent or invalid. The board has no promised host isolator. Do not drive J2
pin 4, an analog input while unpowered, or an independently powered pull-up.
The original analog, host-voltage, 10–400 kHz clock, 100 pF capacitance,
leakage/sink, I2C timing and external-load bounds remain in force.

## Single-shot request and result

Keep at most one conversion request outstanding. Before retriggering or selecting
the next channel, complete the prior conversion: poll its OS bit ready and read
its conversion result. A start-bit write during an ongoing conversion has no
effect [B]; it must not be counted as a newly accepted conversion request.

For each new request, use the original register pointer, byte order and selected
configuration (`0xC583` for AIN0 or `0xD583` for AIN1, single-shot, 128 SPS,
PGA ±2.048 V). Require a successful configuration transfer, including I2C
acknowledgments; report a NACK or bus error as a failed request. A ready value
left from an earlier conversion or a failed configuration write is not evidence
that the requested new conversion completed.

After the accepted start write, allow for the manufacturer's approximately
25 µs internal startup stage and poll OS for the requested conversion's ready
state [B]. Account for that startup stage when scheduling polls; do not treat a
prior idle OS value as the new completion. When the new conversion is ready,
select pointer `0x00` and read its two-byte result using the original signed
conversion and scaling instructions. Only then issue the next conversion.

## Timing interpretation and failure policy

The conversion settles in one data-rate cycle [C]. With the selected 128 SPS
setting and the table's −10% data-rate variation [D], the lower rate is
`128 × 0.90 = 115.2 SPS`, giving a **conversion-only interval** of
`1 / 115.2 = 0.008680555… s`, or approximately **8.6806 ms**.

The original approximately 8.69 ms description applies to this conversion
interval. Command-to-result scheduling must additionally account for the
approximately 25 µs internal startup and I2C configuration, polling and read
overhead. Do not reinterpret 8.69 ms as a complete command-to-result deadline or
add the approximate startup value to create a guaranteed manufacturer maximum.
Use OS readiness for the current conversion rather than accepting data solely
because a calculated delay elapsed.

Retain the **20 ms host timeout** as an engineering failure policy for the
conversion attempt, including its configuration/ready-poll/result-read handling.
The host implementation must define and enforce that deadline consistently.
On timeout, bus failure or uncertain completion, report failure and reject stale
conversion data. This policy is not a manufacturer guarantee that every request
will finish within 20 ms, and no physical host compliance is claimed.

## Pinned sources and locators

Original operating contract, captured path `OPERATING.md`, SHA-256:
`b6e702b341a234e38da4ea0fb5f0831bd1fa61038d78e6e098d0c06a1e678319`.
The original remains unchanged; this additive supplement corrects the omitted
power-up wait and clarifies the conversion-time wording.

Manufacturer source: TI ADS1113/ADS1114/ADS1115, **SBAS444E, revised December
2024**. Captured text path `manufacturer-text/ads1115.txt`, SHA-256:
`7e50ff262cb5be665ccf33cfd082bf9d46baaa807648f2b118cf3fc077b0d034`.
Original PDF path:
`datasheets-core/221813224c23a7b649ef22e3e1116744e53b8cbaa31fc43afd6872174f300295.pdf`,
SHA-256 `eefff4d412ce2dcd7e659f47238edc9b2fa02e5971f2625af730502033701834`.
The existing page/byte map is `manufacturer-text/manifest.json`, SHA-256
`ee1c4d695cdd0ba20527a50bd73516dc2e955559a9bd59829edd5d35748fe43f`.

| Citation | Source locator | Relevant text or fact |
| --- | --- | --- |
| A | §10.1, physical PDF page 37, UTF-8 byte 189557 | “Wait approximately 50µs after VDD is stabilized before communicating” |
| B | §7.4.2.1, physical page 18, byte 101362; ongoing-conversion sentence at byte 101541 | “approximately 25 μs”; OS resets for the new conversion; writing OS during an ongoing conversion has no effect |
| C | §7.3.6, physical page 15, byte 92307 | Conversion time equals one data-rate cycle, `1 / DR` |
| D | §5.5, physical page 5, byte 26879 | Data-rate variation is −10% to +10% for all rates |

These are locators into the exact captured UTF-8 bytes, not new source fetches,
OCR, measurements or broader device-qualification conclusions.
