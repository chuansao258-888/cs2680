#!/usr/bin/env bash
# Launch the A3 runner with the Qwen Token Plan settings baked in.
# Usage: bash a3run.sh [limit]     (default: 1; 0 = preflight only)
set -euo pipefail
cd "$(dirname "$0")"

limit="${1:-1}"

# Move the previous run's outputs aside, or run_all.py refuses to start.
stamp=$(date +%Y%m%d-%H%M%S)
mkdir -p "previous_runs/$stamp"
for target in madsOpt_logs run_logs model_patch_*.diff pro_eval run_all_results.md predictions.json; do
    [ -e "$target" ] && mv -- "$target" "previous_runs/$stamp/" || true
done

# Qwen Token Plan OpenAI-compatible upstream (reaches the egress proxy).
export A3_API_UPSTREAM="https://token-plan.maas.qianwenaiapi.com"
export A3_API_PATH_PREFIX="/compatible-mode"

# The three model tiers.
export CS2680_MODEL_EXPERT="qwen4-max"
export CS2680_MODEL_STANDARD="qwen3.8-flash"
export CS2680_MODEL_STARTER="qwen3.6-flash"

# Ask for the key only when it is not already in the environment.
if [ -z "${CS2680_API_KEY:-}" ]; then
    read -r -s -p "Qwen API key: " CS2680_API_KEY
    echo
    export CS2680_API_KEY
fi

export MADSOPT_LIVE_TRACE=1
export MADSOPT_LIVE_TRACE_MAX_CHARS=4000
# Experiment arm A: echo reasoning_content back into assistant history.
# Set to 0 to run arm B (reasoning dropped, the pre-experiment behavior).
export MADSOPT_PRESERVE_THINKING="${MADSOPT_PRESERVE_THINKING:-1}"

python3 evaluation_scripts/run_all.py --limit "$limit"
