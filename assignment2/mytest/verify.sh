#!/usr/bin/env bash
# Verify a candidate patch in a fresh task container, with no network access.
set -euo pipefail

TASK_ID="${1:?usage: verify.sh <instance_id> [patch.diff]}"
if [[ ! "$TASK_ID" =~ ^boltons__(issue(393|297|235|241)|cache_copy|collections440)$ ]]; then
  echo "Unknown task: $TASK_ID" >&2
  exit 2
fi
TASK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/tasks/$TASK_ID" && pwd)"
IMAGE="cs2680-mytest/$TASK_ID:v1"
PATCH_MOUNTS=()
if [[ $# -ge 2 ]]; then
  if [[ ! -f "$2" ]]; then
    echo "Patch not found: $2" >&2
    exit 2
  fi
  PATCH_PATH="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
  PATCH_MOUNTS=(-v "$PATCH_PATH:/assessment/patch.diff:ro")
fi
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Image not found: $IMAGE. Build it before verifying." >&2
  exit 2
fi

set +e
docker run --rm --network none --entrypoint bash \
  -v "$TASK_DIR/test_cases.py:/assessment/test_cases.py:ro" \
  "${PATCH_MOUNTS[@]}" "$IMAGE" -c '
    set -u
    cd /app || exit 2
    if [ -f /assessment/patch.diff ]; then
      git apply --check /assessment/patch.diff || exit 2
      git apply /assessment/patch.diff || exit 2
    fi
    export PYTHONPATH=/app
    echo "== fail_to_pass: Reproduction =="
    python /assessment/test_cases.py Reproduction -v
    reproduction=$?
    echo "== pass_to_pass: Regression =="
    python /assessment/test_cases.py Regression -v
    regression=$?
    if [ "$reproduction" -eq 0 ] && [ "$regression" -eq 0 ]; then
      exit 0
    fi
    exit 1
  '
status=$?
set -e
if [[ $status -eq 0 ]]; then
  echo "RESOLVED: $TASK_ID"
elif [[ $status -eq 1 ]]; then
  echo "unresolved: $TASK_ID"
else
  echo "ERROR: verification could not run (exit $status)" >&2
fi
exit "$status"
