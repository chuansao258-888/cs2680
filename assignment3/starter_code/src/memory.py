import json
from dataclasses import dataclass, field
from typing import Any

from openai import APIError
from .prompts import COMPACTION_PROMPT, SUMMARY_CONTEXT_PROMPT

TOOL_OUTPUT_COMPACT_THRESHOLD = 40000
SUMMARY_COMPACT_THRESHOLD = 80000
KEEP_RECENT_TOOL_RESULTS = 12
MIN_TOOL_OUTPUT_CHARS = 600
TOOL_OUTPUT_HEAD_CHARS = 400
TOOL_OUTPUT_TAIL_CHARS = 800
TOOL_OUTPUT_COMPACT_MARKER = "[Archived tool output ref:"
TOOL_OUTPUT_RETRIEVED_MARKER = "[Retrieved archived tool output ref:"


@dataclass(repr=False, eq=False)
class TaskMemory:
    messages: list[dict[str, Any]]
    current_plan: list[dict[str, str]] = field(default_factory=list, init=False)
    prompt_tokens: int = field(default=0, init=False)
    tool_result_names: dict[str, str] = field(default_factory=dict, init=False)
    archived_tool_outputs: dict[str, tuple[str, str]] = field(default_factory=dict, init=False)
    _archive_sequence: int = field(default=0, init=False)

    def record_usage(self, prompt_tokens):
        self.prompt_tokens = prompt_tokens

    def record_tool_result(self, tool_call_id: str, tool_name: str, content: str) -> None:
        """Remember result metadata without adding non-API fields to messages."""
        self.tool_result_names[tool_call_id] = tool_name

    def read_archived_output(
        self, output_id: str, start_line: int = 1, end_line: int | None = None
    ) -> str:
        """Return a previously compacted tool result, optionally sliced by output line."""
        if output_id not in self.archived_tool_outputs:
            raise ValueError(f"unknown archived output: {output_id}")
        if type(start_line) is not int or start_line < 1:
            raise ValueError("start_line must be a positive integer")
        if end_line is not None and (type(end_line) is not int or end_line < start_line):
            raise ValueError("end_line must be an integer >= start_line")
        tool_name, content = self.archived_tool_outputs[output_id]
        lines = content.splitlines()
        if start_line > len(lines):
            raise ValueError("start_line is beyond the archived output")
        last_line = min(end_line or len(lines), len(lines))
        body = "\n".join(
            f"{line_number}: {lines[line_number - 1]}"
            for line_number in range(start_line, last_line + 1)
        )
        return (
            f"{TOOL_OUTPUT_RETRIEVED_MARKER} {output_id}; tool={tool_name}; "
            f"lines {start_line}-{last_line}]\n{body}"
        )

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
            if isinstance(content, str) and content.startswith(TOOL_OUTPUT_RETRIEVED_MARKER):
                # A re-read should remain in context temporarily, but once it becomes old
                # again it can point back to the already stored original result.
                message["content"] = content.split("\n", 1)[0] + "\n[Use the same ref to read it again.]"
                compacted += 1
                continue
            if (
                    not isinstance(content, str)
                    or len(content) < MIN_TOOL_OUTPUT_CHARS
                    or content.startswith(TOOL_OUTPUT_COMPACT_MARKER)
            ):
                continue
            self._archive_sequence += 1
            output_id = f"tool_output_{self._archive_sequence:04d}"
            tool_name = self.tool_result_names.get(message.get("tool_call_id", ""), "unknown")
            self.archived_tool_outputs[output_id] = (tool_name, content)
            preview = content[:TOOL_OUTPUT_HEAD_CHARS]
            if len(content) > TOOL_OUTPUT_HEAD_CHARS + TOOL_OUTPUT_TAIL_CHARS:
                preview += "\n... [middle omitted; use the archive ref for full output] ...\n"
            preview += content[-TOOL_OUTPUT_TAIL_CHARS:]
            message["content"] = (
                f"{TOOL_OUTPUT_COMPACT_MARKER} {output_id}; tool={tool_name}; "
                f"original_chars={len(content)}]\n{preview}"
            )
            compacted += 1

        return compacted

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
        if not completion.choices:
            return None

        choice = completion.choices[0]
        message = choice.message

        # 读取思考内容
        reasoning = getattr(message, "reasoning_content", None)

        if reasoning:
            print("\n" + "=" * 20 + "思考过程" + "=" * 20)
            print(reasoning)

        # 读取最终摘要
        summary = message.content

        if choice.finish_reason != "stop" or not summary or not summary.strip():
            return None

        print("\n" + "=" * 20 + "摘要" + "=" * 20)
        print(summary)

        return summary

    def apply_summary(self, client, model_id: str, git_snapshot: str = ""):
        try:
            summary = self._compact_memory(client, model_id)
        except APIError as e:
            print(f"Compact Failed：{e}")
            return False

        if not isinstance(summary, str) or not summary.strip():
            return False

        if not self.messages or self.messages[0].get("role") != "system":
            return False
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
        return True
