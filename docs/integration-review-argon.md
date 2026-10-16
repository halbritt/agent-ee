# Argon Integration Review of Coordinator Modules & Operator Discovery

**Reviewer**: `gemini-argon` (Cairn `agent-414`)
**Scope**: Read-only verification of [`src/agent_ee/candidate.py`](../src/agent_ee/candidate.py), [`src/agent_ee/executor.py`](../src/agent_ee/executor.py), [`src/agent_ee/reviewers.py`](../src/agent_ee/reviewers.py), [`src/agent_ee/cli.py`](../src/agent_ee/cli.py), [`scopes/donor.json`](../scopes/donor.json), [`README.md`](../README.md), and [`skills/agent-ee/SKILL.md`](../skills/agent-ee/SKILL.md).

---

## 1. Evidence-Based Dispositions of Prior Review Findings (Items 1–6)

| ID | Summary | Disposition | Verification Evidence |
| --- | --- | --- | --- |
| **1** | `outcome(result)` must enforce all declared check IDs, all declared reviewer roles, and non-waivable native check defects; `capture` must verify declared `schematic`, `board`, and `requirements` exist in `m["files"]`. | **Closed (Verified Fixed)** | [`src/agent_ee/cli.py#L87-L108`](../src/agent_ee/cli.py#L87-L108) checks `expected_check_ids`, `expected_review_roles` (across both `reviews` and `review_executions`), and `any(c["outcome"] == "defect" for c in result.get("checks", []))`. [`src/agent_ee/cli.py#L256-L263`](../src/agent_ee/cli.py#L256-L263) verifies declared `schematic`, `board`, and `requirements` exist in `m["files"]`. Exercised by [`tests/test_cli.py#L43-L58`](../tests/test_cli.py#L43-L58) (`test_dismissal_cannot_override_a_failed_declared_criterion`, `test_all_declared_work_is_required`). |
| **2** | `link_correction` must verify unchanged `scope["requirements"]` list and real hash keys in `om["files"]` / `nm["files"]`, and record all `changed_inputs`. | **Closed (Verified Fixed)** | [`src/agent_ee/cli.py#L344-L360`](../src/agent_ee/cli.py#L344-L360) checks `om["scope"].get("requirements", []) != nm["scope"].get("requirements", [])`, verifies `req in om["files"]` and matching digests in `nm["files"]`, and records `event["changed_inputs"]`. Matches [`validate_closure`](../src/agent_ee/findings.py#L494-L578) in `findings.py`. |
| **3** | `build_packet` check evidence prioritization and byte budget on large boards (`Polygonus-Universal-112`). | **Partially Closed — See New Finding A Below** | [`src/agent_ee/reviewers.py#L47-L74`](../src/agent_ee/reviewers.py#L47-L74) and [`scopes/donor.json#L72-L103`](../scopes/donor.json#L72-L103) add `packet_inputs`, `packet_max_bytes: 500000`, `packet_artifacts`, and explicit partial/binary `packet_limits`. However, fresh `/tmp` discovery on `scopes/donor.json` uncovered a blocking interaction when a check (`netlist`) fails to produce a declared `packet_artifacts` file (see Section 2). |
| **4** | Manual `agent-ee adjudicate` (`apply_decisions`) state transition and automatic `adjudicator_call` schema error handling. | **Closed (Verified Fixed)** | [`src/agent_ee/cli.py#L278-L288`](../src/agent_ee/cli.py#L278-L288) verifies all finding IDs are dispositioned and catches `(ValueError, KeyError, TypeError)` as `execution="malformed"` before writing `adjudicator-execution.json`. [`src/agent_ee/cli.py#L324-L330`](../src/agent_ee/cli.py#L324-L330) transitions `result["state"] = "complete"` only when checks, reviews, and findings are all complete and dispositioned. |
| **5** | Separate `stdout.log` and `stderr.log` in `executor.execute` so harness warnings on `stderr` cannot corrupt `claude_call` JSON parsing. | **Closed (Verified Fixed)** | [`src/agent_ee/executor.py#L57-L79`](../src/agent_ee/executor.py#L57-L79) captures `stdout.log` and `stderr.log` separately while preserving combined `console.log`, and [`src/agent_ee/reviewers.py#L109`](../src/agent_ee/reviewers.py#L109) parses `work / "stdout.log"`. Exercised by [`tests/test_execution_failures.py#L72-L76`](../tests/test_execution_failures.py#L72-L76) (`test_stderr_warning_does_not_corrupt_structured_stdout`). |
| **6** | `source_path` resolves root input path before symlink check. | **Dismissed (By Design)** | Coordinator confirmed that explicit root input aliases (e.g. `~/git/fome-mc`) are intentionally resolved and their files copied/hashed into the snapshot, while undeclared nested symlinks inside input trees remain refused ([`src/agent_ee/candidate.py#L39-L40`](../src/agent_ee/candidate.py#L39-L40), [`src/agent_ee/candidate.py#L95-L96`](../src/agent_ee/candidate.py#L95-L96)). |

---

## 2. New Finding from `/tmp` Donor Discovery (Blocker for Full `scopes/donor.json` Run)

### Finding A [HIGH — Crash in `build_packet` on `scopes/donor.json` When `netlist` Check Fails]

- **Location**: [`src/agent_ee/reviewers.py#L49-L53`](../src/agent_ee/reviewers.py#L49-L53) and [`scopes/donor.json#L99-L103`](../scopes/donor.json#L99-L103)
- **Observed Evidence from `/tmp` Discovery**:
  1. In `/home/halbritt/git/Polygonus-Universal-112/Polygonus-Universal-Base.kicad_sch` (line 21553), the root schematic references `(property "Sheetfile" "VR Conditioner.kicad_sch")` (capital `C`), whereas the committed file in the donor repository is `VR conditioner.kicad_sch` (lowercase `c` — a real Windows/Linux case-sensitivity defect in the saved donor hierarchy).
  2. When running `agent-ee review /home/halbritt/git/agent-ee/scopes/donor.json --out /tmp/agent-ee-argon-donor-checks --checks-only --max-seconds 120`, both `erc` and `netlist` fail in [`verified_hierarchy`](../src/agent_ee/checks.py#L217-L227) with:
     - `execution`: `"malformed"`
     - `outcome`: `"unknown"`
     - `summary`: `"hierarchy references missing sheet files: ['VR Conditioner.kicad_sch']; coverage is incomplete"`
     - `evidence`: `[]` (so `work/netlist/connectivity.json` is never written).
  3. Meanwhile, [`scopes/donor.json#L99-L103`](../scopes/donor.json#L99-L103) declares:
     ```json
     "packet_artifacts": [
       "work/netlist/connectivity.json",
       "work/geometry/geometry.json",
       "work/drc/drc.json"
     ]
     ```
  4. In a full review of `scopes/donor.json` (without `--checks-only`), [`build_packet`](../src/agent_ee/reviewers.py#L49-L52) executes:
     ```python
     evidence_paths = [p for c in checks for p in c.get("evidence", [])]
     priorities = scope.get("packet_artifacts", [])
     if any(p not in evidence_paths for p in priorities):
         raise ValueError("packet_artifacts must name evidence produced by this run's declared checks")
     ```
     Because `work/netlist/connectivity.json` is not in `evidence_paths` when `netlist` fails, `build_packet` raises `ValueError("packet_artifacts must name evidence produced by this run's declared checks")`, crashing `run_review` before `initial_reviews` or `adjudicator_call` can execute!
- **Recommended Fix**:
  - In [`src/agent_ee/reviewers.py#L49-L59`](../src/agent_ee/reviewers.py#L49-L59), do not raise `ValueError` when a declared `packet_artifacts` file was not produced because its check failed/did not complete; instead let the loop at lines 57–59 record `packet["packet_limits"].append(f"artifact unavailable: {rel}")` (or validate in `validate_scope` that `packet_artifacts` starts with `work/<declared-check-id>/` or `checks/`).
  - Also note: `checks/<id>.json` (e.g. `checks/erc.json`, `checks/drc.json`, `checks/netlist.json`, `checks/geometry.json`) are written to `run_dir / "checks"`, and `packet["checks"] = checks` is already embedded in `packet.json`, but reviewers citing a failed check that produced `evidence: []` (like `erc` or `netlist` on the donor) have no file path in `evidence` unless `checks/<id>.json` is included in `check["evidence"]` or cited directly as `checks/erc.json` (which `validate_citation` allows because `checks/` is an allowed prefix).

---

## 3. Fresh Operator Discovery from `/tmp`

Executed from `/tmp` using the installed CLI (`/home/halbritt/.local/bin/agent-ee` $\rightarrow$ `/home/halbritt/git/agent-ee/bin/agent-ee`), following [`README.md`](../README.md) and [`skills/agent-ee/SKILL.md`](../skills/agent-ee/SKILL.md) (no model calls invoked):

### 3.1 CLI & Skill Discovery
- `which agent-ee` $\rightarrow$ `/home/halbritt/.local/bin/agent-ee` (symlink to `/home/halbritt/git/agent-ee/bin/agent-ee`).
- Skill linked at `/home/halbritt/.claude/skills/agent-ee/SKILL.md`.
- `agent-ee doctor` from `/tmp` exited `0` with all 5 prerequisites (`kicad-cli` 10.0.7, `ngspice` 42, `bwrap` 0.9.0, `pcbnew` 10.0.7, `claude` 2.1.302) reporting `exit_code: 0`.

### 3.2 Control Fixture Discovery (`fixtures/rc/control.scope.json --checks-only`)
- **Command**:
  ```sh
  cd /tmp
  agent-ee review /home/halbritt/git/agent-ee/fixtures/rc/control.scope.json \
    --out /tmp/agent-ee-argon-ctrl-checks --checks-only --max-seconds 120
  agent-ee inspect /tmp/agent-ee-argon-ctrl-checks
  agent-ee verify /tmp/agent-ee-argon-ctrl-checks
  ```
- **Observed Result**:
  - `review --checks-only` exited `1` (`incomplete: /tmp/agent-ee-argon-ctrl-checks/REPORT.md`), as required for a checks-only run without agent reviews/adjudication.
  - `candidate_id`: `7b6d5a949427df9a9100abf2208581f5564cf9ec758e1b0f1fbe731ff3a60283`
  - All 5 checks executed with `execution: "ok"`:
    - `erc`: `outcome: "pass"` (1 sheet instance, 0 violations)
    - `drc`: `outcome: "pass"` (0 violations, 0 unconnected, 0 schematic parity issues)
    - `netlist`: `outcome: "info"` (3 components `C1, J1, R1`, 3 nets, 0 DNP)
    - `geometry`: `outcome: "pass"` (5 track segments, narrowest `0.25 mm` $\ge$ `0.2 mm` minimum)
    - `rc-step`: `outcome: "pass"` (`vout_1ms = 0.6321201 V` within `[0.62, 0.65]`, `vout_5ms = 0.9932621 V`, 1025 raw points)
  - `agent-ee inspect` reported `freshness: "current"`, `findings: []`.
  - `agent-ee verify` exited `0` (`fresh incomplete: 7b6d5a949427df9a9100abf2208581f5564cf9ec758e1b0f1fbe731ff3a60283`).
  - Temporary directory `/tmp/agent-ee-argon-ctrl-checks` removed after verification.

### 3.3 Donor Baseline Discovery (`scopes/donor.json --checks-only`)
- **Command**:
  ```sh
  cd /tmp
  agent-ee review /home/halbritt/git/agent-ee/scopes/donor.json \
    --out /tmp/agent-ee-argon-donor-checks --checks-only --max-seconds 120
  agent-ee inspect /tmp/agent-ee-argon-donor-checks
  agent-ee verify /tmp/agent-ee-argon-donor-checks
  ```
- **Observed Result**:
  - `review --checks-only` exited `1` (`incomplete: /tmp/agent-ee-argon-donor-checks/REPORT.md`).
  - `candidate_id`: `f3717c91fdffe4ac7dfaf448870d40118f4e390fd2989e024e062347c682dcc8`
  - Check results:
    - `erc`: `execution: "malformed"`, `outcome: "unknown"` (`hierarchy references missing sheet files: ['VR Conditioner.kicad_sch']; coverage is incomplete` — saved file on disk is lowercase `VR conditioner.kicad_sch`).
    - `drc`: `execution: "ok"`, `outcome: "defect"` (`142 violation(s), 0 unconnected item(s), 114 parity issue(s)` — `13 error`, `243 warning`), producing finding `check/drc` (`status: "unresolved"`, `severity: "blocking"`).
    - `netlist`: `execution: "malformed"`, `outcome: "unknown"` (`hierarchy references missing sheet files: ['VR Conditioner.kicad_sch']; coverage is incomplete`).
    - `geometry`: `execution: "ok"`, `outcome: "info"` (`199 nets, 719 pads, 2207 track_segments, 1224 vias, 18 zones, 0 unconnected_items, 10 DNP refs`).
  - `agent-ee inspect` reported `freshness: "current"`.
  - `agent-ee verify` exited `0` (`fresh incomplete: f3717c91fdffe4ac7dfaf448870d40118f4e390fd2989e024e062347c682dcc8`).
  - Temporary directory `/tmp/agent-ee-argon-donor-checks` removed after verification.

---

## 4. Stability Confirmation for `findings.py` and `test_findings.py`

- [`src/agent_ee/findings.py`](../src/agent_ee/findings.py) and [`tests/test_findings.py`](../tests/test_findings.py) are **stable and frozen**.
- Full repository test suite (`PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -v`) passes all **59 tests** in `15.7s`.

## Coordinator disposition after the integration review

Finding A was reproduced by the first recorded donor attempt. The shipped donor
scope now requires only the generated geometry and DRC artifacts; its missing
hierarchy/netlist check remains explicitly incomplete. The strict declaration
rule is retained: `packet_artifacts` names required evidence, so a missing entry
fails the run instead of silently weakening that request. The default packet
uses available check evidence without an explicit required-artifact declaration.
The current README documents this distinction. The original failed attempt is
retained under `evidence/donor-baseline/`.

After this review, a fresh control agent exposed a descriptive-role protocol
error and a real deprecated via API call. Both were corrected, and 61 tests pass
in `evidence/verification/tests-final.log`. See `docs/via-probe-correction.md`.
