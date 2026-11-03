# Agent-EE E2E Test Suite Infrastructure

## Overview
This document specifies the end-to-end testing architecture, test layout, runner instructions, and feature coverage mapping for the Agent-EE general-tool architecture campaign (`AGENTEE-571`..`577`, findings `F-01`..`F-04`, `F-09`).

## Unified Test Runner
The test suite can be run via pytest or the provided runner script:

```bash
# Direct runner script
./tests/e2e/run_tests.sh

# With pytest directly
PYTHONPATH=src:tests python3 -m pytest tests/e2e/ -v

# Run a specific tier or domain
PYTHONPATH=src:tests python3 -m pytest tests/e2e/test_tier4_scenarios.py -v
PYTHONPATH=src:tests python3 -m pytest tests/e2e/test_tier3_combinations.py -v
```

## 5-Tier Test Architecture
The test suite follows the 5-tier testing methodology:

1. **Tier 1: Feature Coverage** (>=5 tests per feature, 23 features across 6 domain suites)
   - `test_tier1_m1_harnesses.py`: Features 1-4 (Harness & schema contracts)
   - `test_tier1_m2_checks.py`: Features 5-9 (Land pattern, checks, qualification, DFM, purpose review)
   - `test_tier1_m3_artwork.py`: Features 10-11 (Artwork isolation, no coordinator pcbnew)
   - `test_tier1_m4_placement.py`: Features 12-15 (Data-driven mechanics, seeds, MILP repair, zero acquisition constants)
   - `test_tier1_m5_completion.py`: Features 16-20 (Checkpoints, build completion, rating lineage, benchmark driver, AST checks)
   - `test_tier1_m6_m7_stages.py`: Features 21-23 (Stage confinement, KiCad staging, clean workspace)

2. **Tier 2: Boundary & Corner Cases** (>=5 boundary cases per feature, 115 tests)
   - `test_tier2_boundaries_part1.py`: Boundaries for Features 1-11
   - `test_tier2_boundaries_part2.py`: Boundaries for Features 12-23

3. **Tier 3: Cross-Feature Combinations** (Pairwise/multi-module interaction coverage, 10 tests)
   - `test_tier3_combinations.py`: Interactions across harnesses, qualification, artwork, placement, completion, and stage isolation.

4. **Tier 4: Real-World Application Scenarios** (End-to-end multi-board workflows, 10 tests)
   - `test_tier4_scenarios.py`: Full lifecycle scenarios covering minimal 555-oscillator, I2C sensor boards, mixed SMT/THT boards, stage recovery, typed completion pipeline, and review draft cycles.

5. **Tier 5: White-Box Adversarial Coverage Hardening** (Adversarial stress-testing and failure mode hardening, 33 tests)
   - `test_tier5_adversarial.py`: Deep adversarial test scenarios covering boundary corruptions, resource exhaustion edge cases, corrupted mountinfo/confinement views, candidate directory integrity, invalid hash receipts, and failure modes across all 6 campaign components.

**Total Test Count**: 283 tests.

## Progressive Testability & Isolation
- Tests are self-contained using isolated temporary directories and synthetic mock fixtures (`e2e_common.py`).
- Milestones currently in active worker development (e.g., M5 completion extraction, DFM-014) use progressive testability: they pass when merged and cleanly skip when the implementation is in-flight, preventing build breakages while guaranteeing strict coverage once landed.
