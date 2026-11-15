#!/usr/bin/env bash
# Check each clean baseline and upstream reference patch in offline containers.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p mytest/results
for number in 393 297 235 241; do
  id="boltons__issue$number"
  before="mytest/results/$id.before.log"
  gold="mytest/results/$id.gold.log"
  status=0
  bash mytest/verify.sh "$id" >"$before" 2>&1 || status=$?
  if [[ "$status" -ne 1 ]] || ! grep -q "unresolved: $id" "$before" || ! grep -q '^OK$' "$before"; then
    echo "Unexpected baseline for $id; see $before" >&2
    exit 1
  fi
  if ! bash mytest/verify.sh "$id" "mytest/tasks/$id/gold.diff" >"$gold" 2>&1; then
    echo "Reference patch failed for $id; see $gold" >&2
    exit 1
  fi
  echo "$id: baseline fails; reference patch passes"
done
