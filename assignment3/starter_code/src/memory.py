import json
from dataclasses import dataclass, field
from typing import Any

from openai import APIError
from .prompts import COMPACTION_PROMPT, SUMMARY_CONTEXT_PROMPT

TOOL_OUTPUT_COMPACT_THRESHOLD = 24000
SUMMARY_COMPACT_THRESHOLD = 56000
KEEP_RECENT_TOOL_RESULTS = 12
MIN_TOOL_OUTPUT_CHARS = 600
TOOL_OUTPUT_HEAD_CHARS = 400
TOOL_OUTPUT_TAIL_CHARS = 800
TOOL_OUTPUT_COMPACT_MARKER = "[Archived tool output ref:"
RECOLLAPSE_HINT = "[Use the same ref to read it again.]"


@dataclass(repr=False, eq=False)
class TaskMemory:
    messages: list[dict[str, Any]]
    current_plan: list[dict[str, str]] = field(default_factory=list, init=False)
    prompt_tokens: int = field(default=0, init=False)
    tool_results: dict[str, tuple[str, str]] = field(default_factory=dict, init=False)
    archived_tool_outputs: dict[str, tuple[str, str]] = field(default_factory=dict, init=False)
    _archive_sequence: int = field(default=0, init=False)

    def record_usage(self, prompt_tokens):
        self.prompt_tokens = prompt_tokens

    def record_tool_result(self, tool_call_id: str, tool_name: str, body: str) -> None:
        """Store the raw output body; presentation headers are composed per sink."""
        self.tool_results[tool_call_id] = (tool_name, body)

    def read_archived_output(self, output_id: str, start_line: int = 1,
                             end_line: int | None = None) -> str:
        """Return the stored raw output of an earlier call, sliced by line."""
        _, content = self.archived_tool_outputs[output_id]
        return "\n".join(content.splitlines()[start_line - 1:end_line])

    def should_compress(self):
        return self.prompt_tokens >= SUMMARY_COMPACT_THRESHOLD

    def compact_tool_outputs(self):
        """Shorten older tool results while preserving the conversation structure."""
        if self.prompt_tokens < TOOL_OUTPUT_COMPACT_THRESHOLD:
            return 0

        tool_messages = [
            message for message in self.messages if message.get("role") == "tool"
        ]
        old_messages = tool_messages[:-KEEP_RECENT_TOOL_RESULTS]
        compacted = 0
        for message in old_messages:
            content = message.get("content")
            if not isinstance(content, str) or content.endswith(RECOLLAPSE_HINT):
                continue
            tool_name, raw = self.tool_results.get(
                message.get("tool_call_id", ""), (None, None))
            if tool_name is None:
                tool_name, raw = "unknown", content
            if tool_name == "read_archived_output":
                # A re-read that aged out: collapse to a ref pointing at the
                # original archive; the slice's content is already stored.
                first, _, _ = content.partition("\n")
                ref = ""
                if first.startswith("[tool_result "):
                    try:
                        ref = json.loads(first[len("[tool_result "):-1]).get("output_id", "")
                    except ValueError:
                        pass
                message["content"] = f"{TOOL_OUTPUT_COMPACT_MARKER} {ref}]\n{RECOLLAPSE_HINT}"
                compacted += 1
                continue
            if content.startswith(TOOL_OUTPUT_COMPACT_MARKER) or len(raw) < MIN_TOOL_OUTPUT_CHARS:
                continue
            self._archive_sequence += 1
            output_id = f"tool_output_{self._archive_sequence:04d}"
            self.archived_tool_outputs[output_id] = (tool_name, raw)
            preview = raw[:TOOL_OUTPUT_HEAD_CHARS]
            if len(raw) > TOOL_OUTPUT_HEAD_CHARS + TOOL_OUTPUT_TAIL_CHARS:
                preview += "\n... [middle omitted; use the archive ref for full output] ...\n"
            preview += raw[-TOOL_OUTPUT_TAIL_CHARS:]
            message["content"] = (
                f"{TOOL_OUTPUT_COMPACT_MARKER} {output_id}; tool={tool_name}; "
                f"original_chars={len(raw)}]\n{preview}"
            )
            compacted += 1

        return compacted

    @staticmethod
    def _usage_fields(usage) -> dict[str, int | None]:
        """Extract provider-neutral usage fields for compaction accounting."""
        details = getattr(usage, "prompt_tokens_details", None)
        if isinstance(details, dict):
            cached_tokens = details.get("cached_tokens")
        else:
            cached_tokens = getattr(details, "cached_tokens", None)
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
            "cached_tokens": cached_tokens,
        }

    def _compact_memory(self, client, model_id: str):
        messages = [{"role": "system", "content": COMPACTION_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(self.messages, ensure_ascii=False),
                    }]

        completion = client.chat.completions.create(
            model=model_id,
            messages=messages,
            extra_body={"enable_thinking": True},
            stream=False
        )
        usage = self._usage_fields(completion.usage)
        if not completion.choices:
            return None, usage

        choice = completion.choices[0]
        message = choice.message

        # The compaction exchange is internal agent state.  Do not dump its
        # reasoning or summary to stdout: live display is owned by the loop.
        summary = message.content

        if choice.finish_reason != "stop" or not summary or not summary.strip():
            return None, usage

        return summary, usage

    def apply_summary(self, client, model_id: str, git_snapshot: str = ""):
        try:
            summary, usage = self._compact_memory(client, model_id)
        except APIError as e:
            print(f"Compact Failed：{e}")
            return False, None

        if not isinstance(summary, str) or not summary.strip():
            return False, usage

        if not self.messages or self.messages[0].get("role") != "system":
            return False, usage
        current_plan = json.dumps(self.current_plan, ensure_ascii=False)
        self.messages = [
            self.messages[0],
            {
                "role": "user",
                "content": SUMMARY_CONTEXT_PROMPT.format(
                    summary=summary,
                    current_plan=current_plan,
                    git_snapshot=git_snapshot or "(no snapshot captured)",
                ),
            },
        ]
        self.prompt_tokens = 0
        return True, usage
