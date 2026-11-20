"""Single source of truth for every A3 knob: host orchestration, provider,
model tiers, agent loop, context compaction, live display. Hardcoded on
purpose — edit this file, then `bash a3run.sh [limit]`.

Secrets stay out: run_all.py injects CS2680_BASE_URL inside the agent
container, and CS2680_API_KEY is prompted at launch by a3run.sh.
"""

import os


# --- Provider (host side; consumed by run_all.py's egress proxy) ---
API_UPSTREAM = "https://token-plan.maas.qianwenaiapi.com"
API_PATH_PREFIX = "/compatible-mode"

# --- Host orchestration (local machine only; NOT portable with src/) ---
# Two live sandboxes at 4g each plus one unbounded grading container fit the
# 12GB Docker VM; five live tasks OOM-killed sandboxes mid-run (2026-11-19).
HOST_MAX_LIVE = 2
HOST_MAX_GRADING = 1
# Local runs are not bound by the leaderboard's 4h window; 0 = no cap.
HOST_RUN_LIMIT_S = 0

# --- Model tiers (Qwen Token Plan) ---
MODEL_EXPERT = "qwen4-max"
MODEL_STANDARD = "qwen3.8-flash"
MODEL_STARTER = "qwen3.6-flash"

# Thinking control per model id, merged into extra_body by call_model.
# Qwen rejects reasoning_effort and thinking_budget together, so each model
# carries exactly one. qwen3.8-flash: medium = budget 16384; qwen3.6-flash
# does not accept reasoning_effort, its budget 16384 maps back to medium.
# qwen4-max runs at the high tier on purpose: it is the deep-repair model.
MODEL_EXTRA_BODY = {
    MODEL_EXPERT: {"reasoning_effort": "high"},
    MODEL_STANDARD: {"reasoning_effort": "medium"},
    MODEL_STARTER: {"thinking_budget": 16384},
}

# --- Agent loop ---
MAX_ITERATIONS = 100
MAX_OPEN_TASKS = 2
# Transient upstream failures (rate limits, 5xx, dropped connections) are
# retried with exponential backoff before the request is given up on.
API_RETRIES = 7
API_BACKOFF_S = 4.0
API_BACKOFF_MAX_S = 60.0
RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
# Echo each turn's reasoning_content back in the assistant history message
# (Qwen preserve_thinking). Arm A of the thinking-continuity experiment;
# set False for arm B (reasoning dropped).
PRESERVE_THINKING = True

# --- Context compaction (see src/memory.py for the algorithm) ---
# Layer 2: full summary compaction.
SUMMARY_COMPACT_THRESHOLD = 56000
# Layer 1: batch tool-output archiving. A batch runs only past the trigger
# threshold AND after this much growth since the last batch, so between
# batches the history is append-only and the provider prefix cache survives.
TOOL_COMPACT_THRESHOLD = 40000
ARCHIVE_HYSTERESIS_TOKENS = 8000
# Live window: the most recent tool outputs up to this token budget stay
# verbatim (counted by content length; no per-message count constraint).
WINDOW_TOKEN_BUDGET = 8000
CHARS_PER_TOKEN = 3.5
PREVIEW_HEAD_CHARS = 100
PREVIEW_TAIL_CHARS = 500
# Bash outputs are archived at birth: never rewritten later, never busts the
# cache, and the tail preview keeps the test summary the model acts on.
BASH_INSERT_MIN_CHARS = 2000
BASH_PREVIEW_TAIL_CHARS = 900
# Very large read_file results are capped AT THE SOURCE (ReadFileTool): the
# model gets a leading page plus the next start_line, like a pager. The file
# itself is the source, so no archive round-trip is involved.
READ_MAX_CHARS = 4000

# --- Live display ---
LIVE_TRACE = True
LIVE_TRACE_MAX_CHARS = 4000
LIVE_TOOL_RESULTS = False
LIVE_MODEL_OUTPUT = False
REPEATED_READ_THRESHOLD = 3
TOOL_RESULT_PREVIEW_CHARS = 400


def api_settings() -> tuple[str, str]:
    """Return required API credentials at runtime, without storing secrets in code."""
    api_key = os.getenv("CS2680_API_KEY")
    base_url = os.getenv("CS2680_BASE_URL")
    if not api_key:
        raise RuntimeError("CS2680_API_KEY is not set")
    if not base_url:
        raise RuntimeError("CS2680_BASE_URL is not set; run via evaluation_scripts/run_all.py")
    return api_key, base_url


def host_exports() -> str:
    """Shell assignments for the host-side knobs run_all.py reads from the
    environment; a3run.sh evaluates this and nothing else."""
    return "\n".join([
        f'export A3_API_UPSTREAM="{API_UPSTREAM}"',
        f'export A3_API_PATH_PREFIX="{API_PATH_PREFIX}"',
        f"export A3_MAX_LIVE={HOST_MAX_LIVE}",
        f"export A3_MAX_GRADING={HOST_MAX_GRADING}",
        f"export A3_RUN_LIMIT_S={HOST_RUN_LIMIT_S}",
    ])
