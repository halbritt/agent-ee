"""Conservative affected-test plans; a plan is never qualification evidence.

Only reviewed narrative files and isolated gate scripts have dependency rules.
Product source, fixture data, shared test helpers and unknown paths require the
full roster. Extend these rules only with consumer-boundary evidence.
"""
import os
import subprocess


NARRATIVE_DOCS = {
    'docs/planning/2026-10-25-recovery/recovery-plan.md',
    'docs/planning/2026-10-25-recovery/execution.md',
}
# The action result reader is consumed by both CI's real RC fixtures and the
# consumer-action regressions in test_action_gate. No product imports it.
SCRIPT_MODULES = {
    'scripts/action_gate.py': {'test_action_gate'},
    'scripts/check_origins.py': {'test_file_origins', 'test_evidence_relocation', 'test_tree_hygiene'},
}
FAST_GUARDS = {'test_strict_suite', 'test_strict_selection'}


def select_paths(paths, available):
    reasons, selected = [], set()
    for path in sorted(set(paths)):
        if path in NARRATIVE_DOCS:
            reasons.append(f'{path}: reviewed narrative documentation; cheap checks')
        elif path in SCRIPT_MODULES:
            required = SCRIPT_MODULES[path] | FAST_GUARDS
            if not required.issubset(available):
                return full_plan(available, f'{path}: mapped consumer module missing')
            selected.update(required)
            reasons.append(f'{path}: affected gate and consumer-boundary modules')
        else:
            return full_plan(available, f'{path}: unknown affected dependencies')
    if not reasons:
        return full_plan(available, 'no changed paths; full qualification remains required')
    return dict(schema='agent-ee.test-selection/1', mode='affected' if selected else 'cheap',
                modules=sorted(selected), native_required=bool(selected), reasons=reasons)


def full_plan(available, reason):
    return dict(schema='agent-ee.test-selection/1', mode='full',
                modules=sorted(set(available)), native_required=True, reasons=[reason])


def from_change_base(root, base, available):
    """Missing bases, dirty trees, Git failures and unknown paths run everything.

    Resolve to a commit before diffing, preserving both sides of renames and
    deletions. Never treat an unavailable diff as an empty docs-only change.
    """
    if not base:
        return full_plan(available, 'unknown change base')
    try:
        dirty = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=all'],
                               cwd=root, capture_output=True, check=True).stdout
        if dirty:
            return full_plan(available, 'dirty checkout cannot justify affected selection')
        resolved = subprocess.run(['git', 'rev-parse', '--verify', '--end-of-options', base + '^{commit}'],
                                  cwd=root, capture_output=True, check=True).stdout.decode('ascii').strip()
        changed = subprocess.run(['git', 'diff', '--name-only', '-z', '--no-renames',
                                  resolved, 'HEAD', '--'], cwd=root,
                                 capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError, UnicodeError) as exc:
        return full_plan(available, f'change discovery failed ({type(exc).__name__}); unknown dependencies')
    return select_paths([os.fsdecode(path) for path in changed.split(b'\0') if path], available)
