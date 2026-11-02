#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

export PYTHONPATH="${REPO_ROOT}/src:${REPO_ROOT}/tests:${PYTHONPATH:-}"

echo "=== Running Agent-EE End-to-End Test Suite ==="
python3 -m pytest tests/e2e/ "$@"
