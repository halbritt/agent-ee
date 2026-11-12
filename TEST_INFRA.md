# Agent-EE E2E Test Suite Infrastructure

## Overview
This document describes the layout and runner of the end-to-end tests under `tests/e2e/`, written during the general-tool architecture work (`AGENTEE-571`..`577`, findings `F-01`..`F-04`, `F-09`). Most of them run on temporary directories and synthetic fixtures; they are not board runs and say nothing about board quality. The rest of the suite is under `tests/`, and the [nightly suite](docs/nightly-full-suite.md) runs all of it.

## Unified Test Runner
The test suite can be run via its mise task, pytest or the provided runner script:

```bash
# Host task: admission, a deadline, and the native toolchain required instead of skipped
mise run test:integration
mise run test:integration -- tests/e2e/test_tier4_scenarios.py -v

# Direct runner script
./tests/e2e/run_tests.sh

# With pytest directly
PYTHONPATH=src:tests python3 -m pytest tests/e2e/ -v

# Run a specific tier or domain
PYTHONPATH=src:tests python3 -m pytest tests/e2e/test_tier4_scenarios.py -v
PYTHONPATH=src:tests python3 -m pytest tests/e2e/test_tier3_combinations.py -v
```

## Five tiers
The files are grouped in five tiers:

1. **Tier 1: Features** (23 features in six files)
   - `test_tier1_m1_harnesses.py`: Features 1-4 (Harness & schema contracts)
   - `test_tier1_m2_checks.py`: Features 5-9 (Land pattern, checks, qualification, DFM, purpose review)
   - `test_tier1_m3_artwork.py`: Features 10-11 (Artwork isolation, no coordinator pcbnew)
   - `test_tier1_m4_placement.py`: Features 12-15 (Data-driven mechanics, seeds, MILP repair, zero acquisition constants)
   - `test_tier1_m5_completion.py`: Features 16-20 (Checkpoints, build completion, rating lineage, benchmark driver, AST checks)
   - `test_tier1_m6_m7_stages.py`: Features 21-23 (Stage confinement, KiCad staging, clean workspace)

2. **Tier 2: Boundary cases**
   - `test_tier2_boundaries_part1.py`: Boundaries for Features 1-11
   - `test_tier2_boundaries_part2.py`: Boundaries for Features 12-23

3. **Tier 3: Combinations of features**
   - `test_tier3_combinations.py`: Interactions across harnesses, qualification, artwork, placement, completion, and stage isolation.

4. **Tier 4: Workflow scenarios**
   - `test_tier4_scenarios.py`: Workflow scenarios on synthetic fixtures: a minimal 555 oscillator, an I2C sensor board, a mixed SMT/THT board, stage recovery, the typed completion pipeline and review drafts.

5. **Tier 5: Failure modes**
   - `test_tier5_adversarial.py`: Corrupted inputs, resource limits, damaged confinement views, changed candidate directories and malformed receipts in the six components.

The number of tests changes; count them with `python3 -m pytest tests/e2e/ --collect-only -q`.

## Isolation and skips
- Tests are self-contained using isolated temporary directories and synthetic mock fixtures (`e2e_common.py`).
- A test whose module is absent skips instead of failing. A skip is not a pass.
