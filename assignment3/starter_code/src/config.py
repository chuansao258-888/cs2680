"""Central configuration for the agent.

Secrets remain environment variables. ``run_all.py`` sets ``CS2680_BASE_URL``
inside the agent container, so this module never hard-codes a provider URL.
"""

import os


MAX_ITERATIONS = int(os.getenv("MADSOPT_MAX_ITERATIONS", "100"))
# Two live sandboxes at 4g each plus one unbounded grading container fit the
# 12GB Docker VM; five live tasks OOM-killed sandboxes mid-run (2026-11-19).
MAX_OPEN_TASKS = int(os.getenv("MADSOPT_MAX_OPEN_TASKS", "2"))

# Transient upstream failures (rate limits, 5xx, dropped connections) are
# retried with exponential backoff before the request is given up on.
API_RETRIES = int(os.getenv("MADSOPT_API_RETRIES", "7"))
API_BACKOFF_S = float(os.getenv("MADSOPT_API_BACKOFF_S", "4"))
API_BACKOFF_MAX_S = float(os.getenv("MADSOPT_API_BACKOFF_MAX_S", "60"))
RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})

# Echo each turn's reasoning_content back in the assistant history message
# (Qwen preserve_thinking). Arm A of the thinking-continuity experiment;
# set MADSOPT_PRESERVE_THINKING=0 for arm B (reasoning dropped).
PRESERVE_THINKING = (os.getenv("MADSOPT_PRESERVE_THINKING") or "1").lower() not in {"0", "false", "no"}

# Defaults reflect the selected Qwen tiers.  Environment variables override
# them, which keeps the agent compatible with the course API or another
# OpenAI-compatible provider.
MODEL_EXPERT = os.getenv("CS2680_MODEL_EXPERT", "qwen4-max")
MODEL_STANDARD = os.getenv("CS2680_MODEL_STANDARD", "qwen3.8-flash")
MODEL_STARTER = os.getenv("CS2680_MODEL_STARTER", "qwen3.6-flash")

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


def api_settings() -> tuple[str, str]:
    """Return required API credentials at runtime, without storing secrets in code."""
    api_key = os.getenv("CS2680_API_KEY")
    base_url = os.getenv("CS2680_BASE_URL")
    if not api_key:
        raise RuntimeError("CS2680_API_KEY is not set")
    if not base_url:
        raise RuntimeError("CS2680_BASE_URL is not set; run via evaluation_scripts/run_all.py")
    return api_key, base_url
