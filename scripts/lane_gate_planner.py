#!/usr/bin/env python3
"""Lane Gate Planner (AGENTEE-614 Part 1 / Defect #42).

Computes affected test modules, transitive reverse dependents, and
repository-wide scanners for fast, deterministic lane gating (< 3s).
Execution-image changes select the full suite because native dependencies
can affect tests that do not import the changed files.
The plan is explicitly non-qualifying and does not replace formal release
qualification (scripts/strict_selection.py).
"""
import argparse
import ast
import json
from pathlib import Path
import subprocess
import sys


SCHEMA = 'agent-ee.lane-gate-plan/1'

CORE_SCANNERS = [
    'test_architecture_boundaries',
    'test_file_origins',
    'test_native_run_guard',
    'test_tool_source',
    'test_tree_hygiene',
]


def path_to_src_module(rel_path: Path) -> str | None:
    """Map a path under src/ to a python module name."""
    parts = list(rel_path.parts)
    if not parts or parts[0] != 'src':
        return None
    parts = parts[1:]  # remove 'src'
    if not parts or not parts[-1].endswith('.py'):
        return None
    if parts[-1] == '__init__.py':
        parts.pop()
    else:
        parts[-1] = parts[-1].removesuffix('.py')
    if not parts:
        return None
    return '.'.join(parts)


def build_src_dependency_graph(root: Path):
    """Scan src/agent_ee to build forward and reverse dependency graphs."""
    src_dir = root / 'src' / 'agent_ee'
    modules = {}
    if not src_dir.exists():
        return modules, {}, {}

    for py_file in src_dir.rglob('*.py'):
        rel = py_file.relative_to(root)
        mod = path_to_src_module(rel)
        if mod:
            modules[mod] = py_file

    forward_deps = {m: set() for m in modules}
    reverse_deps = {m: set() for m in modules}

    for mod, path in modules.items():
        try:
            tree = ast.parse(path.read_text(encoding='utf-8', errors='replace'), filename=str(path))
        except SyntaxError:
            continue

        mod_parts = mod.split('.')
        pkg_parts = mod_parts[:-1] if path.name != '__init__.py' else mod_parts

        deps = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith('agent_ee'):
                        deps.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    if node.module and node.module.startswith('agent_ee'):
                        deps.add(node.module)
                        for alias in node.names:
                            deps.add(f'{node.module}.{alias.name}')
                else:
                    if node.level <= len(pkg_parts):
                        base_parts = pkg_parts[:len(pkg_parts) - (node.level - 1)]
                        base = '.'.join(base_parts + [node.module]) if node.module else '.'.join(base_parts)
                        if base.startswith('agent_ee'):
                            deps.add(base)
                            for alias in node.names:
                                deps.add(f'{base}.{alias.name}')

        valid_deps = set()
        for d in deps:
            if d in modules:
                valid_deps.add(d)
            else:
                parts = d.split('.')
                while parts:
                    candidate = '.'.join(parts)
                    if candidate in modules:
                        valid_deps.add(candidate)
                        break
                    parts.pop()

        forward_deps[mod] = valid_deps
        for dep in valid_deps:
            reverse_deps[dep].add(mod)

    return modules, forward_deps, reverse_deps


def get_transitive_dependents(affected_modules: set[str], reverse_deps: dict[str, set[str]]) -> set[str]:
    """Compute transitive closure of modules that depend on affected_modules."""
    closure = set()
    queue = list(affected_modules)
    while queue:
        curr = queue.pop(0)
        if curr not in closure:
            closure.add(curr)
            for parent in reverse_deps.get(curr, ()):
                if parent not in closure:
                    queue.append(parent)
    return closure


def find_reverse_dependent_tests(root: Path, affected_src_modules: set[str]) -> set[str]:
    """Scan tests/test_*.py to find test modules importing any affected src module."""
    tests_dir = root / 'tests'
    if not tests_dir.exists() or not affected_src_modules:
        return set()

    # Pre-filter stems for quick text match
    stems = {m.split('.')[-1] for m in affected_src_modules}
    matched_tests = set()

    for test_file in sorted(tests_dir.glob('test_*.py')):
        try:
            content = test_file.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue

        # Fast pre-check: if no stem appears in the file text, skip AST parsing
        if not any(s in content for s in stems):
            continue

        try:
            tree = ast.parse(content, filename=str(test_file))
        except SyntaxError:
            continue

        imports_affected = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in affected_src_modules:
                        imports_affected = True
                        break
                    # Also check if alias.name is a parent of an affected module or child
                    parts = alias.name.split('.')
                    if any('.'.join(parts[:i]) in affected_src_modules for i in range(1, len(parts) + 1)):
                        imports_affected = True
                        break
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    mod_name = node.module
                    if mod_name in affected_src_modules:
                        imports_affected = True
                        break
                    for alias in node.names:
                        full_name = f'{mod_name}.{alias.name}'
                        if full_name in affected_src_modules or mod_name in affected_src_modules:
                            imports_affected = True
                            break
            if imports_affected:
                break

        if imports_affected:
            matched_tests.add(test_file.stem)

    return matched_tests


def plan_lane_gate(root: Path, changed_files: list[str], change_base: str | None = None, head: str | None = None) -> dict:
    """Generate structured lane gate plan from list of changed files."""
    norm_changed = []
    for f in changed_files:
        p = Path(f)
        if p.is_absolute():
            try:
                p = p.relative_to(root)
            except ValueError:
                pass
        norm_changed.append(p.as_posix())

    touched_test_modules = set()
    changed_src_modules = set()

    for f in norm_changed:
        p = Path(f)
        parts = p.parts
        if len(parts) >= 2 and parts[0] == 'tests' and parts[-1].startswith('test_') and parts[-1].endswith('.py'):
            touched_test_modules.add(p.stem)
        mod = path_to_src_module(p)
        if mod:
            changed_src_modules.add(mod)

    transitive_src_modules = set()
    reverse_dependent_test_modules = set()

    if changed_src_modules:
        modules, _, rev_deps = build_src_dependency_graph(root)
        transitive_src_modules = get_transitive_dependents(changed_src_modules, rev_deps)
        reverse_dependent_test_modules = find_reverse_dependent_tests(root, transitive_src_modules)

    # Core repository scanners are always included for repository health
    repo_scanners = [s for s in CORE_SCANNERS if (root / 'tests' / f'{s}.py').exists()]

    all_modules = sorted(touched_test_modules | reverse_dependent_test_modules | set(repo_scanners))

    image_changes = sorted(f for f in set(norm_changed)
                           if f in ('Dockerfile', '.dockerignore') or f.startswith('docker/'))
    if image_changes:
        all_modules = sorted(path.stem for path in (root / 'tests').glob('test_*.py'))

    reasons = []
    for f in sorted(norm_changed):
        reasons.append(f'{f}: changed file')
    for t in sorted(reverse_dependent_test_modules):
        reasons.append(f'reverse dependent: {t} imports affected agent_ee modules')
    for s in repo_scanners:
        reasons.append(f'repository scanner: {s}')
    for f in image_changes:
        reasons.append(f'{f}: execution image changed; full suite required')

    # Check native requirement
    native_required = bool(changed_src_modules or image_changes)
    if not native_required:
        for mod_name in all_modules:
            test_path = root / 'tests' / f'{mod_name}.py'
            if test_path.exists():
                text = test_path.read_text(encoding='utf-8', errors='replace')
                if 'native=True' in text:
                    native_required = True
                    break

    return {
        'schema': SCHEMA,
        'qualification': 'non-qualifying',
        'qualifying': False,
        'mode': 'full' if image_changes else 'affected-lane-gate',
        'native_required': native_required,
        'inputs': {
            'change_base': change_base,
            'head': head,
            'changed_files': sorted(set(norm_changed)),
        },
        'analysis': {
            'touched_test_modules': sorted(touched_test_modules),
            'changed_src_modules': sorted(changed_src_modules),
            'transitive_src_modules': sorted(transitive_src_modules),
            'reverse_dependent_test_modules': sorted(reverse_dependent_test_modules),
            'repository_scanners': repo_scanners,
        },
        'modules': all_modules,
        'reasons': reasons,
    }


def get_git_diff_paths(root: Path, change_base: str, allow_dirty: bool = False) -> tuple[list[str], str, str]:
    """Retrieve changed files from git diff between change_base and HEAD."""
    if not allow_dirty:
        status = subprocess.run(
            ['git', 'status', '--porcelain', '--untracked-files=all'],
            cwd=root, capture_output=True, text=True
        )
        if status.returncode != 0:
            raise RuntimeError(f'git status failed: {status.stderr.strip()}')
        if status.stdout.strip():
            raise RuntimeError('dirty checkout cannot justify affected lane gate; commit changes or use --allow-dirty')

    base_proc = subprocess.run(
        ['git', 'rev-parse', '--verify', f'{change_base}^{{commit}}'],
        cwd=root, capture_output=True, text=True
    )
    if base_proc.returncode != 0:
        raise RuntimeError(f'unknown change base: {change_base}')
    base_sha = base_proc.stdout.strip()

    head_proc = subprocess.run(
        ['git', 'rev-parse', 'HEAD'],
        cwd=root, capture_output=True, text=True
    )
    if head_proc.returncode != 0:
        raise RuntimeError(f'git rev-parse HEAD failed: {head_proc.stderr.strip()}')
    head_sha = head_proc.stdout.strip()

    diff_proc = subprocess.run(
        ['git', 'diff', '--name-only', '-z', '--no-renames', base_sha, 'HEAD', '--'],
        cwd=root, capture_output=True, text=True
    )
    if diff_proc.returncode != 0:
        raise RuntimeError(f'git diff failed: {diff_proc.stderr.strip()}')

    paths = [p for p in diff_proc.stdout.split('\0') if p]
    return paths, base_sha, head_sha


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Lane Gate Planner (AGENTEE-614 Part 1)')
    parser.add_argument('--change-base', help='Git commit/ref to diff against HEAD')
    parser.add_argument('--diff-paths', nargs='+', help='Explicit list of changed file paths')
    parser.add_argument('--root', default='.', help='Repository root directory')
    parser.add_argument('--format', choices=['space', 'lines', 'json'], default='space',
                        help='Output format for selected modules (default: space)')
    parser.add_argument('--write-plan', help='Write plan JSON to file')
    parser.add_argument('--gate-log', help='Write plan JSON to <PATH>.plan.json beside gate log')
    parser.add_argument('--allow-dirty', action='store_true',
                        help='Allow diffing even if git status reports uncommitted changes')

    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    if not args.change_base and args.diff_paths is None:
        sys.stderr.write('lane_gate_planner: error: one of --change-base or --diff-paths is required\n')
        return 1

    changed_files = []
    change_base = None
    head = None

    if args.diff_paths is not None:
        changed_files = args.diff_paths
        change_base = args.change_base
    elif args.change_base:
        try:
            changed_files, change_base, head = get_git_diff_paths(root, args.change_base, args.allow_dirty)
        except RuntimeError as err:
            sys.stderr.write(f'lane_gate_planner: error: {err}\n')
            return 1

    plan = plan_lane_gate(root, changed_files, change_base=change_base, head=head)

    if args.write_plan:
        out_path = Path(args.write_plan)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(plan, indent=2) + '\n', encoding='utf-8')

    if args.gate_log:
        log_plan = Path(f'{args.gate_log}.plan.json')
        log_plan.parent.mkdir(parents=True, exist_ok=True)
        log_plan.write_text(json.dumps(plan, indent=2) + '\n', encoding='utf-8')

    modules = plan['modules']
    if args.format == 'json':
        print(json.dumps(plan, indent=2))
    elif args.format == 'lines':
        for m in modules:
            print(m)
    else:  # space
        print(' '.join(modules))

    return 0


if __name__ == '__main__':
    sys.exit(main())
