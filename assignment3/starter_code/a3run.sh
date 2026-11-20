#!/usr/bin/env bash
# Launch the A3 runner. Every knob lives in src/config.py — edit that file,
# not this script. Usage: bash a3run.sh [limit]   (default: 1; 0 = preflight only)
set -euo pipefail
cd "$(dirname "$0")"

limit="${1:-1}"

# Move the previous run's outputs aside, or run_all.py refuses to start.
# Archives live OUTSIDE the container-mounted dir: grading outputs there
# contain hidden test names the agent must never be able to read.
stamp=$(date +%Y%m%d-%H%M%S)
mkdir -p "../previous_runs/$stamp"
for target in madsOpt_logs run_logs model_patch_*.diff pro_eval run_all_results.md predictions.json; do
    [ -e "$target" ] && mv -- "$target" "../previous_runs/$stamp/" || true
done

# Hand the host-side knobs (provider upstream, concurrency, time limit) to
# run_all.py; they are defined in src/config.py.
eval "$(python3 -c 'import sys; sys.path.insert(0, "."); from src import config; print(config.host_exports())')"

# Ask for the key only when it is not already in the environment.
if [ -z "${CS2680_API_KEY:-}" ]; then
    read -r -s -p "Qwen API key: " CS2680_API_KEY
    echo
    export CS2680_API_KEY
fi

python3 evaluation_scripts/run_all.py --limit "$limit"
