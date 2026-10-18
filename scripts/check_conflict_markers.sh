#!/bin/sh
# Refuse committed merge-conflict markers anywhere in the tree (docs included).
cd "$(dirname "$0")/.." || exit 2
if git grep -n -E '^(<<<<<<< |>>>>>>> |=======$)' -- . ':!*.patch' ':!*.diff'; then
  echo "conflict markers found" >&2; exit 1
fi
echo "no conflict markers"
