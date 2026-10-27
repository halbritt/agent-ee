# Preparing the acquisition interface variant for AGENTEE-525

This procedure prepares one changed interface brief using the existing captured
acquisition templates, `spec_record`, `typed_overlay` and `interval_assertions`.
It adds no production producer or validator. The
[brief](acquisition-variant-525-brief.md) changes only the allowed
I2C capacitance from 100 to 150 pF per line. It is preparation under agent-669's
assignment to agent-673, not qualification or permission to expand M2.

## Inputs and preservation

The inspected source is `64da514a09ca7f5b152ed78a594ff4d68120847e`.
Use an isolated managed worktree from that revision. Supply the original
198-file `build/acquisition-release/inputs` capture, the 25-file CPL supplement
and the 14-file manufacturer-text capture. Access and redistribution limits are
in [the runtime procedure](../recovery-runtime.md); missing or hash-mismatched
inputs stop preparation. Do not download replacements or edit the retained root.

Set `BASE`, `CPL`, `TEXT` to those directories. Set `DEST` to a new directory
outside every source tree. The examples require Python 3.12 or later with
`PYTHONPATH=src`, from the isolated checkout. They use existing package APIs;
the complete authored edit transcript below is part of this procedure, not an
unrecorded external preparation script.

```sh
export BASE=/absolute/path/to/original-inputs
export CPL=/absolute/path/to/cpl-supplement
export TEXT=/absolute/path/to/manufacturer-text
export DEST=/absolute/path/to/new-output
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src
```

For the authorized native runtime, copy the inputs into the owned worktree's
ignored `build/lane525/` directory, and run these commands through
`AE_CI_MAX_WAIT=300 ae-ci-exec WORKTREE -- COMMAND`. Host paths outside the
worktree are not mounted by that runner. Retain its admission, image and command
records alongside the outputs. Do not bypass admission with direct Docker.

## Explicit engineering edits

The transcript verifies the original acquisition inventory against the recovery
manifest and all three added-input trees against the existing overlay. It copies
the original inputs, archives the files it changes, recaptures the spec, and
records every old/new hash. The equal-length `100` → `150` edit preserves byte
locators; any different edit requires fresh span authoring and review.

The part selection and circuit stay unchanged. Source hashes in the parts
declaration, interval sources and embedded locality records are rebound to the
new spec. Locality quotes, bounds and geometry stay unchanged. Historical
diagnostic/context files retain their original identities. The new brief states
that those records do not establish current acceptance.

```python
import copy
import json
import os
import shutil
from decimal import Decimal
from pathlib import Path

from agent_ee import requirement_map, spec_record, typed_overlay
from agent_ee.storage import json_bytes

base = Path(os.environ['BASE']).resolve()
cpl = Path(os.environ['CPL']).resolve()
text = Path(os.environ['TEXT']).resolve()
dest = Path(os.environ['DEST']).resolve()
for source in (base, cpl, text):
    assert source.is_dir()
    assert dest != source and source not in dest.parents and dest not in source.parents
assert not dest.exists()
sha = spec_record.sha256
inventory = requirement_map.input_inventory
overlay = json.loads(Path('fixtures/acquisition-typed/overlay.json').read_bytes())
before = inventory(base)
frozen = json.loads(Path('fixtures/recovery/inputs.json').read_bytes())
# The recovery manifest also contains the build plan and esp_prog inputs.
expected = {p.removeprefix('build/acquisition-release/inputs/'): h['sha256']
            for p, h in frozen['files'].items()
            if p.startswith('build/acquisition-release/inputs/')}
assert len(expected) == 198 and before == expected
sources = {'cpl': cpl, 'manufacturer-text': text,
           'operating-supplement': Path('fixtures/acquisition-typed/operating-supplement').resolve()}
for at, source in sources.items():
    assert inventory(source) == overlay['added_inputs'][at]['files']
dest.mkdir(parents=True)
inputs = dest / 'variant-base'
shutil.copytree(base, inputs)
(dest / 'original-inventory.json').write_bytes(json_bytes(before))
old_spec = (base / 'spec.md').read_bytes()
assert old_spec.count(b'at most 100 pF total line capacitance') == 1
new_spec = old_spec.replace(b'at most 100 pF total line capacitance',
                            b'at most 150 pF total line capacitance')
assert len(new_spec) == len(old_spec)
old_sha, new_sha = sha(old_spec), sha(new_spec)
(inputs / 'spec.md').write_bytes(new_spec)
old_record = spec_record.verify((base / 'spec-record.json').read_bytes(), old_spec)
record = spec_record.capture(new_spec, old_record['ids']['prefix'], old_record['ids']['codes'])
assert [r['locator'] for r in record['requirements']] == [r['locator'] for r in old_record['requirements']]
(inputs / 'spec-record.json').write_bytes(json_bytes(record))
mapping = json.loads((inputs / 'requirement-map.json').read_bytes())
mapping['spec_record_sha256'] = sha((inputs / 'spec-record.json').read_bytes())
(inputs / 'requirement-map.json').write_bytes(json_bytes(mapping))

# Rebind only operative copies of unchanged source-backed claims.
def rebind(value):
    if isinstance(value, dict):
        return {k: rebind(v) for k, v in value.items()}
    if isinstance(value, list):
        return [rebind(v) for v in value]
    if value == old_sha:
        return new_sha
    if value == old_spec.decode('utf-8'):
        return new_spec.decode('utf-8')
    return value

for name in ('parts.json', 'electrical-locality.json', 'placement.json'):
    doc = json.loads((inputs / name).read_bytes())
    (inputs / name).write_bytes(json_bytes(rebind(doc)))
interval = json.loads((inputs / 'analysis/electrical-intervals.json').read_bytes())
interval = rebind(interval)
assert interval['parameters']['bus_c']['min'] == '100e-12 F'
assert interval['parameters']['bus_c']['max'] == '100e-12 F'
interval['parameters']['bus_c'].update(min='150e-12 F', max='150e-12 F')
(inputs / 'analysis/electrical-intervals.json').write_bytes(json_bytes(interval))
operating = (inputs / 'OPERATING.md').read_bytes()
assert operating.count(b'100 pF per line') == 1
(inputs / 'OPERATING.md').write_bytes(operating.replace(b'100 pF per line', b'150 pF per line'))
calculations = json.loads((inputs / 'engineering-calculations.json').read_bytes())
rise = Decimal('.8473') * Decimal('2222') * Decimal('150e-12')
calculations['values']['i2c_rise_time'] = {
    'value': str(rise), 'unit': 's',
    'basis': 'agent-673 variant 525: captured 30..70 percent RC model, total150pF, Rmax2222ohm; not a waveform measurement'}
calculations['sources'] = {'spec.md' if v == old_sha else k: new_sha if v == old_sha else v
                           for k, v in calculations['sources'].items()}
(inputs / 'engineering-calculations.json').write_bytes(json_bytes(calculations))
review = (inputs / 'ENGINEERING-REVIEW.md').read_text()
assert review.count('At100pF') == 1 and review.count('188.27ns') == 1
review = review.replace('At100pF', 'At150pF').replace('188.27ns', '282.40509ns')
review = ('Variant 525 transcription by agent-673: arithmetic updated; this inherited\n'
          'review narrative is not independent judgment or acceptance of the variant.\n\n' + review)
(inputs / 'ENGINEERING-REVIEW.md').write_text(review)
brief = Path('docs/qualification/acquisition-variant-525-brief.md').read_bytes()
(inputs / 'PREPARATION-525.md').write_bytes(brief)

after = inventory(inputs)
changed = {p: {'before': h, 'after': after[p]} for p, h in before.items() if after[p] != h}
reasons = {'spec.md': 'Apply the 150 pF brief; preserve all other requirement bytes',
           'spec-record.json': 'Recapture the revised spec with existing ids and locators',
           'requirement-map.json': 'Bind the recaptured spec record; keep check declarations',
           'parts.json': 'Rebind unchanged native-part declaration to the revised spec',
           'electrical-locality.json': 'Rebind unchanged locality quotes and embedded spec',
           'placement.json': 'Rebind embedded locality; keep placement geometry and policy',
           'analysis/electrical-intervals.json': 'Apply 150 pF boundary operand and new spec hash',
           'OPERATING.md': 'Apply the revised bus-capacitance instruction',
           'engineering-calculations.json': 'Recompute rise time and name the revised spec',
           'ENGINEERING-REVIEW.md': 'Update arithmetic narrative and withhold inherited acceptance'}
assert set(changed) == set(reasons)
for name, change in changed.items():
    change['basis'] = reasons[name]
originals = inputs / 'history/variant-525-original'
for name in changed:
    target = originals / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(base / name, target)
ledger = {'author': 'agent-673', 'date': '2026-10-27', 'issue': 'AGENTEE-525',
          'kind': 'agent-selected engineering input revision; not owner requirement or acceptance',
          'brief_sha256': sha(brief), 'original_inventory_sha256': sha(json_bytes(before)),
          'changed': changed, 'new': ['PREPARATION-525.md'],
          'originals': 'history/variant-525-original',
          'pending': 'All judgments, native checks, full build/resume, supplier and physical qualification'}
(inputs / 'input-revision-525.json').write_bytes(json_bytes(ledger))
overlay['name'] = 'AGENTEE-525 interface capacitance 150pF revision 1'
overlay['base'] = {'spec_sha256': new_sha,
                   'spec_record_sha256': sha((inputs / 'spec-record.json').read_bytes()),
                   'requirement_map_sha256': sha((inputs / 'requirement-map.json').read_bytes())}
questions = overlay['judgments']['judgments/acq-elec-5-host-bus-contract.json']['questions']
assert len(questions) == 1 and '100 pF' in questions[0]
questions[0] = questions[0].replace('100 pF', '150 pF')
(dest / 'overlay.json').write_bytes(json_bytes(overlay))
assert inventory(base) == before
print(json.dumps({'out': str(dest), 'changed': sorted(changed),
                  'original_files_preserved': len(before), 'rise_s': str(rise)}, indent=2))
```

## Typed preparation and contradictory control

Run the existing public preparer on that revision. It records the new base,
typed map, obligations, derived interval bindings and captured additions.

```sh
python3 -m agent_ee.typed_overlay prepare "$DEST/variant-base" "$DEST/overlay.json" \
  --out "$DEST/prepared" --added-input "cpl=$CPL" \
  --added-input "manufacturer-text=$TEXT" \
  --added-input operating-supplement=fixtures/acquisition-typed/operating-supplement
```

For a functional arithmetic check, copy `prepared` to `arithmetic-150` and
provide `requirements-map/spec.md` there as an exact copy of its new `spec.md`.
This is an explicit diagnostic stand-in for the build compiler's requirement
mount, not a native candidate or stage receipt. Run:

```sh
python3 -m agent_ee.interval_assertions "$DEST/arithmetic-150" \
  analysis/electrical-intervals.json --out "$DEST/interval-150.json"
```

Make two separate diagnostic copies: set only `parameters.bus_c.min/max` to
`100e-12 F` in `arithmetic-100`, and to `200e-12 F` in `arithmetic-200`. Retain
the 150 pF spec, sources, equation, resistor interval and 300 ns limit in both.
Run the same public interval command for each. The 100 pF copy is a numerical
baseline under the new source binding; the 200 pF copy deliberately contradicts
the 150 pF brief. Preserve their full input inventories and reports.

The interval CLI writes the report and does not return failure merely because
an electrical assertion is a defect. Inspect `execution`, `outcome` and the
`i2c-rise` verdict; a zero process exit alone proves no electrical pass.

| Diagnostic | Worst-case rise | Required result |
| --- | --- | --- |
| 100 pF | 188.27006 ns | `execution: ok`, `outcome: pass` |
| 150 pF | 282.40509 ns | `execution: ok`, `outcome: pass` |
| 200 pF contradiction | 376.54012 ns | `execution: ok`, `outcome: defect`, `i2c-rise: defect` |

These expectations use the retained equation and operands; no threshold is
chosen after evaluating the candidate. Source applicability, finite slew,
non-lumped interconnects, host compliance and actual capacitance are unmeasured.

The following completes those diagnostic copies and checks the actual reports.
Run it after public preparation, with the same environment:

```python
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from agent_ee.requirement_map import input_inventory
from agent_ee.storage import json_bytes

dest = Path(os.environ['DEST']).resolve()
summaries = {}
for capacitance in (100, 150, 200):
    root = dest / f'arithmetic-{capacitance}'
    shutil.copytree(dest / 'prepared', root)
    source = root / 'requirements-map'
    source.mkdir()
    shutil.copyfile(root / 'spec.md', source / 'spec.md')
    path = root / 'analysis/electrical-intervals.json'
    document = json.loads(path.read_bytes())
    document['parameters']['bus_c'].update(min=f'{capacitance}e-12 F', max=f'{capacitance}e-12 F')
    path.write_bytes(json_bytes(document))
    output = dest / f'interval-{capacitance}.json'
    command = [sys.executable, '-m', 'agent_ee.interval_assertions', str(root),
               'analysis/electrical-intervals.json', '--out', str(output)]
    result = subprocess.run(command, check=True)
    report = json.loads(output.read_bytes())
    verdicts = {a['id']: a['verdict'] for a in report['assertions']}
    expected = 'defect' if capacitance == 200 else 'pass'
    assert report['execution'] == 'ok' and report['outcome'] == expected
    assert verdicts['i2c-rise'] == expected
    assert all(v == 'pass' for k, v in verdicts.items() if k != 'i2c-rise')
    summaries[str(capacitance)] = {'argv': command, 'exit_code': result.returncode,
                                  'execution': report['execution'], 'outcome': report['outcome'],
                                  'verdicts': verdicts}
    (dest / f'arithmetic-{capacitance}-inventory.json').write_bytes(json_bytes(input_inventory(root)))
(dest / 'arithmetic-summary.json').write_bytes(json_bytes(summaries))
print(json.dumps(summaries, indent=2))
```

## Build handoff and acceptance boundary

The prepared directory retains requirements/methods, parts, circuit, layout,
placement, groups, locality, models and captured sources in existing formats.
Keep the original build plan as a template. The
[typed build contract](../build-workflow.md#qualification-and-current-claims)
uses `schema: agent-ee.build-plan/2`, `requirements.obligations: obligations.json`
and an optional top-level `judgments: judgments` directory. Preserve all other
plan settings; select a fresh output for any subsequently authorized build.
Fresh scaffold generation must consume the complete new input revision.
Do not compile it over the old scaffold by copying only the typed overlay's
changed files: that would leave the scaffold's old spec/parts/source bindings.

No old stage receipt, package, judgment or witness qualifies this variant.
Compare the new typed accounting to the original: 47 rows, including 14 absent
judgments, two physical obligations and one operational obligation. Counts alone
do not prove coverage or engineering adequacy. A fresh operator must reproduce
this procedure, then the coordinator must allocate supported public build,
finalization, cancellation/resume, native checks and independent candidate-bound
judgments after the prerequisites are accepted. Stock captures are historical;
their freshness must be assessed by the normal build contract.

The inherited 1 mA external-load budget and instruction forbidding a load on
J2 pin 4 are retained; resolving their intended relationship is outside this
capacitance change. Exact-part capacitor applicability, broader temperature
claims, startup/removal/stability, physical I2C and supplier confirmation remain
explicit obligations. This preparation does not resolve those questions.

## Retained run

The lane evidence root is
`/home/halbritt/.local/share/agent-ee/fleet-20261027-agent669/lane-525/`.
The result record there identifies the tested source, input inventories, actual
interventions, runtime provenance, commands, reports and remaining acceptance.
AGENTEE-525 and M1 remain open pending independent review and the dependent
workflow qualification. The telemetry-pod, orders and uploads remain held.

Agent-673 executed both Python transcripts and the public preparer on 2026-10-27
under admitted `ae-ci-exec`. Preparation produced 210 base files including the
ten archived originals and the brief/edit ledger. The typed output contains 47
rows: 29 executable, 14 judgments, two physical, one operational and one context.
No judgment records are present. All 198 original files were verified unchanged;
all 17 part declarations retain their component data, with only the spec binding
revised. Circuit, layout, groups, ratings and ADC ties retain their original bytes.

The three public interval runs produced the table's expected outcomes. The
focused strict suite passed 34 tests in `test_typed_overlay` and
`test_interval_assertions`, with no skips. Documentation, origins and diff checks
passed. No full build, native board check or typed scaffold compilation was run
for this variant. The focused suite includes native check-path tests, not a
qualification of these prepared inputs through all public stages.

The 300 ns source locator was checked in the captured ADS1115 text, SHA-256
`7e50ff262cb5be665ccf33cfd082bf9d46baaa807648f2b118cf3fc077b0d034`,
physical page 6, §5.6, UTF-8 byte 35284. The calculation retains the authored RC
coefficient and resistor bounds; the source locator does not independently
validate that lumped model for a physical host.

| Prepared artifact | SHA-256 |
| --- | --- |
| Revised spec | `0db8867881a7c47a92e953aabc27aeaa8c0d202a262a476edce1617ede2075e0` |
| Typed requirement map | `7576ab231e2770d2aeb5359b7fb42a3b001d5d07016362181199a6758e03e0f2` |
| Typed obligations | `5533073ab434c4a04cd4383ae55cef0d1b6698a2eae42c935ba0fe57fc8a1a1a` |
| Derived interval model | `70de1c1f1fdc260fb19eb911a8a07e4b04b6d4edb64129822b2db283a6d44564` |

Actual container events bind the preparation to lease `af0756f33160` and the
two-worker suite to lease `45935eece4e8`. Both select runner build-input hash
`7a405961fba05ae10fcb46de2e9bd1b0ebdef14da7d3df2bfe980e6cbca9e534`.
The selected image was read back as
`sha256:63cb53f57f8ca334f7bb202564247c20fa9ff3799aef052840e109733ebbe4bb`.
Preparation recorded Python 3.12.3 and KiCad CLI 10.0.7. The retained host ledger,
container events and image readback establish the runtime record; no historical
image tag is substituted for those observations.
