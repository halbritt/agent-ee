#!/usr/bin/env python3
"""Check Full Suite Recency (AGENTEE-614 Part 3 / Defect #42).

Validates that a commit has a 'local-full-suite' status recorded within
the maximum age window (default: 36 hours). Replaces running actual test suites
inside GitHub Actions CI.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


def parse_iso_timestamp(ts_str: str) -> datetime:
    """Parse ISO 8601 timestamp string into timezone-aware UTC datetime."""
    norm = ts_str.replace('Z', '+00:00')
    dt = datetime.fromisoformat(norm)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def check_recency(
    statuses: list[dict],
    max_hours: float = 36.0,
    now_utc: datetime | None = None,
) -> tuple[bool, str]:
    """Evaluate whether any 'local-full-suite' status is fresh within max_hours."""
    if not statuses:
        return False, "No commit statuses found"

    matching = [s for s in statuses if s.get('context') == 'local-full-suite']
    if not matching:
        return False, "No 'local-full-suite' commit status found"

    # Sort matching statuses descending by created_at
    def sort_key(item):
        ts = item.get('created_at') or item.get('updated_at') or ''
        return ts

    matching.sort(key=sort_key, reverse=True)
    latest = matching[0]

    ts_str = latest.get('created_at') or latest.get('updated_at')
    if not ts_str:
        return False, "Status record is missing created_at timestamp"

    try:
        created_dt = parse_iso_timestamp(ts_str)
    except (ValueError, TypeError) as err:
        return False, f"Failed to parse status timestamp '{ts_str}': {err}"

    now = now_utc if now_utc is not None else datetime.now(timezone.utc)
    age_seconds = (now - created_dt).total_seconds()
    age_hours = age_seconds / 3600.0

    state = latest.get('state', 'unknown')
    desc = latest.get('description', '')

    if age_hours <= max_hours:
        return True, (
            f"local-full-suite status is fresh: age {max_hours if age_hours < 0 else age_hours:.1f}h "
            f"<= {max_hours}h (state={state}, description={desc!r})"
        )
    else:
        return False, (
            f"local-full-suite status is stale: age {age_hours:.1f}h > {max_hours}h "
            f"(created_at={ts_str}, state={state}, description={desc!r})"
        )


def fetch_statuses_via_gh(repo: str, commit_sha: str, token: str | None = None) -> list[dict]:
    """Fetch commit statuses via gh api."""
    cmd = ['gh', 'api', f'repos/{repo}/commits/{commit_sha}/statuses']
    env = os.environ.copy()
    if token:
        env['GH_TOKEN'] = token
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"gh api failed ({proc.returncode}): {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Check local-full-suite recency (AGENTEE-614 Part 3)')
    parser.add_argument('--commit', help='Commit SHA to inspect (default: HEAD)')
    parser.add_argument('--max-hours', type=float, default=36.0, help='Maximum permitted age in hours (default: 36)')
    parser.add_argument('--status-json', help='Path to mock or cached statuses JSON array file')
    parser.add_argument('--repo', default='halbritt/agent-ee', help='Repository (owner/repo)')
    parser.add_argument('--gh-token', help='GitHub API token (default: env GH_TOKEN or GITHUB_TOKEN)')

    args = parser.parse_args(argv)

    token = args.gh_token or os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')

    commit = args.commit
    if not commit and not args.status_json:
        proc = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True)
        if proc.returncode == 0:
            commit = proc.stdout.strip()
        else:
            sys.stderr.write("check_full_suite_recency: error: --commit is required\n")
            return 1

    if args.status_json:
        try:
            statuses = json.loads(Path(args.status_json).read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as err:
            sys.stderr.write(f"check_full_suite_recency: error reading {args.status_json}: {err}\n")
            return 1
    else:
        try:
            statuses = fetch_statuses_via_gh(args.repo, commit, token=token)
        except (RuntimeError, OSError) as err:
            sys.stderr.write(f"check_full_suite_recency: error: {err}\n")
            return 1

    ok, message = check_recency(statuses, max_hours=args.max_hours)
    if ok:
        print(f"RECENCY OK: commit {commit or 'mock'}: {message}")
        return 0
    else:
        sys.stderr.write(f"RECENCY FAILURE: commit {commit or 'mock'}: {message}\n")
        return 1


if __name__ == '__main__':
    sys.exit(main())
