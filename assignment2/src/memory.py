import json
import os
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI, APIError
from src.prompts import COMPACTION_PROMPT, SUMMARY_CONTEXT_PROMPT

TOOL_OUTPUT_COMPACT_THRESHOLD = 20000
SUMMARY_COMPACT_THRESHOLD = 35000
KEEP_RECENT_TOOL_RESULTS = 8
MIN_TOOL_OUTPUT_CHARS = 600
TOOL_OUTPUT_HEAD_CHARS = 200
TOOL_OUTPUT_COMPACT_MARKER = "[Older tool output truncated to save context."


@dataclass(repr=False, eq=False)
class TaskMemory:
    messages: list[dict[str, Any]]
    current_plan: list[dict[str, str]] = field(default_factory=list, init=False)
    prompt_tokens: int = field(default=0, init=False)

    def record_usage(self, prompt_tokens):
        self.prompt_tokens = prompt_tokens

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
            if (
                not isinstance(content, str)
                or len(content) < MIN_TOOL_OUTPUT_CHARS
                or TOOL_OUTPUT_COMPACT_MARKER in content
            ):
                continue

            message["content"] = (
                content[:TOOL_OUTPUT_HEAD_CHARS]
                + f"\n{TOOL_OUTPUT_COMPACT_MARKER} "
                + f"Original length: {len(content)} characters.]"
            )
            compacted += 1

        return compacted

    def _compact_memory(self):
        client = OpenAI(
            # 如果没有配置环境变量，请用阿里云百炼API Key替换：api_key="sk-xxx"
            api_key=os.getenv("CS2680_API_KEY"),
            base_url="https://cn-beijing.maas.qianwenaiapi.com/compatible-mode/v1",
        )

        messages = [{"role": "system", "content": COMPACTION_PROMPT},
            {
                "role": "user",
                "content": json.dumps(self.messages, ensure_ascii=False),
            }]

        completion = client.chat.completions.create(
            model="qwen3.6-35b-a3b",  # 您可以按需更换为其它深度思考模型
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


    def apply_summary(self):
        try:
            summary = self._compact_memory()
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
                    summary=summary, current_plan=current_plan
                ),
            },
        ]
        self.prompt_tokens = 0
        return True
