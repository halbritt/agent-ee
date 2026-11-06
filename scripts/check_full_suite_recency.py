#!/usr/bin/env python3
"""Check Full Suite Recency (AGENTEE-614 Part 3 / Defect #42).

Validates that a commit has a 'local-full-suite' status recorded for the exact monitored commit. Completed unchanged commits do not expire.
GitHub Actions executes this completion monitor instead of actual test suites.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import re
from pathlib import Path
import subprocess
import sys


CLOCK_SKEW = timedelta(seconds=5)


def parse_iso_timestamp(ts_str: str) -> datetime:
    """Parse ISO 8601 timestamp string into timezone-aware UTC datetime."""
    norm = ts_str.replace('Z', '+00:00')
    dt = datetime.fromisoformat(norm)
    if dt.tzinfo is None:
        raise ValueError('timestamp must include a timezone')
    return dt.astimezone(timezone.utc)


def check_recency(
    statuses: list[dict],
    max_hours: float = 36.0,
    now_utc: datetime | None = None,
    commit_sha: str | None = None,
) -> tuple[bool, str]:
    """Check authoritative completion for an exact SHA; max_hours is legacy compatibility.

    Completed unchanged commits do not expire. Ambiguous timestamps cannot be
    ordered safely, and newer incomplete evidence never borrows an older pass.
    """
    if not statuses:
        return False, "No commit statuses found"
    if not isinstance(statuses, list) or not all(isinstance(status, dict) for status in statuses):
        return False, "Malformed commit status records"
    matching = [status for status in statuses if status.get('context') == 'local-full-suite']
    if not matching:
        return False, "No 'local-full-suite' commit status found"
    try:
        ordered = [(parse_iso_timestamp(status['created_at']), status) for status in matching]
    except (KeyError, AttributeError, ValueError, TypeError):
        return False, 'Malformed status timestamp; completion ordering is unknown'
    created, latest = max(ordered, key=lambda pair: pair[0])
    if any((status.get('state'), status.get('description')) != (latest.get('state'), latest.get('description'))
           for timestamp, status in ordered if timestamp == created):
        return False, 'Conflicting statuses share the latest timestamp; completion ordering is unknown'
    now = now_utc if now_utc is not None else datetime.now(timezone.utc)
    if created > now + CLOCK_SKEW:
        return False, 'Future-dated status cannot establish completion'
    state = latest.get('state', 'unknown')
    if state not in ('success', 'failure'):
        return False, f'local-full-suite status does not record a completed suite (state={state})'
    description = latest.get('description')
    marker = (re.match(r'^strict-complete ([0-9a-f]{40}) (\S+) problems=(\d+)(?:\s|$)', description)
              if isinstance(description, str) else None)
    if marker is None:
        return False, 'Status has no authoritative strict-suite completion marker'
    recorded_sha, finished_text, problems = marker.groups()
    if commit_sha is not None and recorded_sha != commit_sha.lower():
        return False, 'Completed result belongs to a different commit SHA'
    try:
        finished = parse_iso_timestamp(finished_text)
    except (AttributeError, ValueError, TypeError):
        return False, 'Malformed completion timestamp'
    if finished > now + CLOCK_SKEW or finished > created + CLOCK_SKEW:
        return False, 'Future or inconsistent completion timestamp'
    if (state == 'success') != (int(problems) == 0):
        return False, 'Suite status contradicts its problem count'
    age_hours = (now - created).total_seconds() / 3600
    return True, (f'Completed local-full-suite for {recorded_sha}: age {age_hours:.1f}h; '
                  f'no expiration (suite state={state}, problems={problems}, finished={finished_text})')


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
    parser.add_argument('--max-hours', type=float, default=36.0, help='Legacy compatibility option; exact-commit completed evidence does not expire')
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

    ok, message = check_recency(statuses, max_hours=args.max_hours, commit_sha=commit)
    if ok:
        print(f"RECENCY OK: commit {commit or 'mock'}: {message}")
        return 0
    else:
        sys.stderr.write(f"RECENCY FAILURE: commit {commit or 'mock'}: {message}\n")
        return 1


if __name__ == '__main__':
    sys.exit(main())
