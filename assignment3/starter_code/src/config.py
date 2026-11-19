"""Central configuration for the agent.

Secrets remain environment variables. ``run_all.py`` sets ``CS2680_BASE_URL``
inside the agent container, so this module never hard-codes a provider URL.
"""

import os


MAX_ITERATIONS = int(os.getenv("MADSOPT_MAX_ITERATIONS", "100"))
# The dispatcher permits five live tasks. This subscription supports up to four
# concurrent agents, so keep the agent-side limit at four.
MAX_OPEN_TASKS = int(os.getenv("MADSOPT_MAX_OPEN_TASKS", "4"))

# Defaults reflect the selected Qwen tiers.  Environment variables override
# them, which keeps the agent compatible with the course API or another
# OpenAI-compatible provider.
MODEL_EXPERT = os.getenv("CS2680_MODEL_EXPERT", "qwen4-max")
MODEL_STANDARD = os.getenv("CS2680_MODEL_STANDARD", "qwen3.8-flash")
MODEL_STARTER = os.getenv("CS2680_MODEL_STARTER", "qwen3.6-flash")


def api_settings() -> tuple[str, str]:
    """Return required API credentials at runtime, without storing secrets in code."""
    api_key = os.getenv("CS2680_API_KEY")
    base_url = os.getenv("CS2680_BASE_URL")
    if not api_key:
        raise RuntimeError("CS2680_API_KEY is not set")
    if not base_url:
        raise RuntimeError("CS2680_BASE_URL is not set; run via evaluation_scripts/run_all.py")
    return api_key, base_url
