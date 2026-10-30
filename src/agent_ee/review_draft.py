"""Read-only draft feedback inside an initial review's existing tool turn."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import stat

from .findings import safe_id
from .storage import digest, read_json, write_json

INSTRUCTIONS = (
    "DRAFT VALIDATION: Before submitting, save your complete review JSON to /work/draft.json and run "
    "python3 -m agent_ee.review_draft /work/draft.json. This checks the exact frozen citation schema, "
    "candidate identity and fresh-review rules against the captured files. Correct reported errors by "
    "inspecting the cited evidence; preserve substantive findings and unresolved concerns. Recheck after "
    "editing. Success applies only to the reported draft hash; it is not engineering acceptance. "
    "Return the checked review JSON as your final structured response. This command consumes the SAME "
    "call deadline; it adds no turn, retry or time. Final admission still validates your actual response.\n"
)

MAX_DRAFT_BYTES = 8 * 1024 * 1024


def _read_draft(path):
    # Nonblocking admission prevents a FIFO/device from consuming the call budget.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_DRAFT_BYTES:
            raise ValueError('draft must be a regular file of at most 8 MiB')
        raw = stream.read(MAX_DRAFT_BYTES + 1)
        if len(raw) > MAX_DRAFT_BYTES:
            raise ValueError('draft exceeds 8 MiB')
        return raw


def prepare(run_dir, role, packet, schema):
    """Coordinator-owned bindings, outside the reviewer's writable workspace."""
    run = Path(run_dir)
    control = run / 'review-validation' / safe_id(role, 'reviewer role')
    write_json(control / 'schema.json', schema)
    write_json(control / 'context.json', {
        'schema': 'agent-ee.review-draft-context/1', 'role': role,
        **{key: packet[key] for key in ('candidate_id', 'run_id', 'packet_sha256')},
        'evidence_schema_sha256': digest(control / 'schema.json'),
        'candidate_file_sha256': digest(run / 'candidate.json'),
        # initial_reviews also supports a packet supplied only in memory.
        # When a retained packet was supplied, losing it must still refuse.
        'packet_file_sha256': digest(run / 'packet.json') if (run / 'packet.json').is_file() else None,
    })
    return control


def check(draft_path, run_dir, control_dir):
    """Validate one byte snapshot without changing the draft or admitting it."""
    from .reviewers import validate_evidence_paths, validate_fresh_review

    result = {'valid': False, 'draft_sha256': None}
    try:
        raw = _read_draft(draft_path)
        result['draft_sha256'] = sha256(raw).hexdigest()
        value = json.loads(raw)
        run, control = Path(run_dir), Path(control_dir)
        context = read_json(control / 'context.json')
        if context['schema'] != 'agent-ee.review-draft-context/1':
            raise ValueError('unknown draft validation context')
        for path, key in ((control / 'schema.json', 'evidence_schema_sha256'),
                          (run / 'candidate.json', 'candidate_file_sha256'),
                          (run / 'packet.json', 'packet_file_sha256')):
            if key == 'packet_file_sha256' and context[key] is None:
                continue
            if digest(path) != context[key]:
                raise ValueError(f'draft validation binding changed: {key}')
        validate_evidence_paths(value, read_json(control / 'schema.json'))
        validate_fresh_review(value, context, run, context['role'])
    except (OSError, ValueError, KeyError, TypeError) as error:
        result['error'] = str(error)
    else:
        result.update(valid=True, notice='Draft syntax and evidence validation only; final admission remains required.')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('draft', help='Complete draft JSON file in your writable workspace')
    args = parser.parse_args()
    # Expected identity, evidence and schema are coordinator mounts, never CLI options.
    result = check(args.draft, '/evidence', '/review-validation')
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
