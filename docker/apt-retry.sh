#!/bin/sh
# Retry a complete apt command after outages longer than its per-file retries.
set -eu
if [ "$#" -eq 0 ]; then
    echo "apt-retry: a command is required" >&2
    exit 2
fi
attempt=0
for delay in 0 60 120 240; do
    if [ "$delay" -ne 0 ]; then
        echo "apt-retry: waiting ${delay}s before retry" >&2
        sleep "$delay"
        # Keep complete indexes: install retries still need package metadata.
        rm -rf /var/lib/apt/lists/partial/* /var/cache/apt/archives/partial/*
    fi
    attempt=$((attempt + 1))
    echo "apt-retry: attempt ${attempt}/4: $*" >&2
    if "$@"; then
        exit 0
    else
        status=$?
    fi
done
echo "apt-retry: exhausted 4 attempts (exit ${status})" >&2
exit "$status"
