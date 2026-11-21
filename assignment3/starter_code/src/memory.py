import json
from dataclasses import dataclass, field
from typing import Any

from openai import APIError
from .config import (
    ARCHIVE_HYSTERESIS_TOKENS,
    BASH_INSERT_MIN_CHARS,
    BASH_PREVIEW_TAIL_CHARS,
    CHARS_PER_TOKEN,
    PREVIEW_HEAD_CHARS,
    PREVIEW_TAIL_CHARS,
    SUMMARY_COMPACT_THRESHOLD,
    TOOL_COMPACT_THRESHOLD,
    WINDOW_TOKEN_BUDGET,
)
from .prompts import COMPACTION_PROMPT, SUMMARY_CONTEXT_PROMPT, format_plan

# Archive only when the raw output is at least twice the preview size, so an
# archived message is always smaller than the short outputs kept verbatim.
ARCHIVE_MIN_CHARS = 2 * (PREVIEW_HEAD_CHARS + PREVIEW_TAIL_CHARS)
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
    # Index into messages: everything below is final and never rewritten.
    _archive_watermark: int = field(default=0, init=False)
    _tokens_at_last_archive: int = field(default=0, init=False)
    # Structured work state, carried across layer-2 compaction.
    files_read: dict[str, int] = field(default_factory=dict, init=False)
    files_modified: list[str] = field(default_factory=list, init=False)
    scratch_files: set[str] = field(default_factory=set, init=False)
    recent_commands: list[tuple[str, str]] = field(default_factory=list, init=False)
    archive_index: list[tuple[str, str]] = field(default_factory=list, init=False)

    def record_usage(self, prompt_tokens):
        self.prompt_tokens = prompt_tokens

    def record_tool_result(self, tool_call_id: str, tool_name: str, body: str,
                           arguments: dict | None = None, is_error: bool = False):
        """Store the raw output body; return (output_id, preview) when a large
        bash output is archived at birth, else None."""
        self.tool_results[tool_call_id] = (tool_name, body)
        args = arguments or {}
        path = str(args.get("path", ""))
        if tool_name == "read_file" and path:
            self.files_read[path] = self.files_read.get(path, 0) + 1
        elif tool_name == "edit_file" and not is_error:
            if path:
                if path not in self.files_modified:
                    self.files_modified.append(path)
            else:
                for hunk in args.get("files") or []:
                    if not isinstance(hunk, dict):
                        continue
                    hp = str(hunk.get("path", ""))
                    if hp and hp not in self.files_modified:
                        self.files_modified.append(hp)
        elif tool_name == "create_file" and path:
            if path not in self.files_modified:
                self.files_modified.append(path)
            if path.startswith("/work"):
                self.scratch_files.add(path)
        elif tool_name == "bash":
            self.recent_commands.append(
                (str(args.get("command", ""))[:100], "error" if is_error else "ok"))
            del self.recent_commands[:-8]
        if tool_name == "bash" and len(body) >= BASH_INSERT_MIN_CHARS:
            cmd = str(args.get("command", ""))[:60]
            output_id = self._archive_output(tool_name, body, f"bash `{cmd}` ({len(body)} chars)")
            return output_id, self._preview(body, BASH_PREVIEW_TAIL_CHARS)
        return None

    def _archive_output(self, tool_name: str, raw: str, desc: str) -> str:
        self._archive_sequence += 1
        output_id = f"tool_output_{self._archive_sequence:04d}"
        self.archived_tool_outputs[output_id] = (tool_name, raw)
        self.archive_index.append((output_id, desc))
        del self.archive_index[:-40]
        return output_id

    @staticmethod
    def _preview(raw: str, tail_chars: int) -> str:
        preview = raw[:PREVIEW_HEAD_CHARS]
        if len(raw) > PREVIEW_HEAD_CHARS + tail_chars:
            preview += "\n... [middle omitted; use the archive ref for full output] ...\n"
        preview += raw[-tail_chars:]
        return preview

    def read_archived_output(self, output_id: str, start_line: int = 1,
                             end_line: int | None = None) -> str:
        """Return the stored raw output of an earlier call, sliced by line."""
        _, content = self.archived_tool_outputs[output_id]
        return "\n".join(content.splitlines()[start_line - 1:end_line])

    def should_compress(self):
        return self.prompt_tokens >= SUMMARY_COMPACT_THRESHOLD

    def compact_tool_outputs(self):
        """Batch-archive tool outputs outside the live window. Runs rarely (see
        hysteresis) so requests stay byte-identical prefixes between batches."""
        if self.prompt_tokens < TOOL_COMPACT_THRESHOLD:
            return 0
        if self.prompt_tokens - self._tokens_at_last_archive < ARCHIVE_HYSTERESIS_TOKENS:
            return 0

        boundary = len(self.messages)
        budget = WINDOW_TOKEN_BUDGET * CHARS_PER_TOKEN
        acc = 0
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i].get("role") != "tool":
                continue
            acc += len(self.messages[i].get("content") or "")
            if acc > budget:
                boundary = i
                break

        compacted = 0
        for i in range(self._archive_watermark, boundary):
            message = self.messages[i]
            if message.get("role") != "tool":
                continue
            content = message.get("content")
            if not isinstance(content, str):
                continue
            if (content.startswith(TOOL_OUTPUT_COMPACT_MARKER)
                    or content.endswith(RECOLLAPSE_HINT)):
                continue
            tool_name, raw = self.tool_results.get(
                message.get("tool_call_id", ""), (None, None))
            if tool_name is None:
                tool_name, raw = "unknown", content
            meta = {}
            first, _, _ = content.partition("\n")
            if first.startswith("[tool_result "):
                try:
                    meta = json.loads(first[len("[tool_result "):-1])
                except ValueError:
                    meta = {}
            if meta.get("output_id"):
                if tool_name != "read_archived_output":
                    continue        # born as a preview: nothing left to gain
                # A re-read that aged out: collapse to a ref pointing at the
                # original archive; the slice's content is already stored.
                message["content"] = (
                    f"{TOOL_OUTPUT_COMPACT_MARKER} {meta['output_id']}]\n{RECOLLAPSE_HINT}")
                compacted += 1
                continue
            if len(raw) < ARCHIVE_MIN_CHARS:
                continue
            desc = f"{tool_name} {meta.get('path', '')} ({len(raw)} chars)".strip()
            output_id = self._archive_output(tool_name, raw, desc)
            message["content"] = (
                f"{TOOL_OUTPUT_COMPACT_MARKER} {output_id}; {desc}]\n"
                + self._preview(raw, PREVIEW_TAIL_CHARS))
            compacted += 1

        if compacted:
            self._archive_watermark = boundary
            self._tokens_at_last_archive = self.prompt_tokens
        return compacted

    def state_checkpoint(self) -> str:
        """Structured facts from tool execution, injected into every layer-2
        summary so compaction never loses the work site."""
        parts = []
        if self.files_modified:
            parts.append("Files you modified: " + ", ".join(self.files_modified))
        if self.files_read:
            top = sorted(self.files_read.items(), key=lambda kv: -kv[1])[:8]
            parts.append("Files you read: " + ", ".join(f"{p} (x{n})" for p, n in top))
        if self.scratch_files:
            parts.append("Scratch files under /work (delete before finishing): "
                         + ", ".join(sorted(self.scratch_files)))
        if self.recent_commands:
            parts.append("Recent commands: "
                         + "; ".join(f"`{c}` -> {s}" for c, s in self.recent_commands[-5:]))
        if self.archive_index:
            parts.append("Archive index (fetch with read_archived_output; do NOT "
                         "re-run commands to see old output again):")
            parts += [f"  {oid} = {desc}" for oid, desc in self.archive_index[-20:]]
        return "\n".join(parts) if parts else "(no state recorded)"

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
        current_plan = format_plan(self.current_plan) or "(no plan)"
        self.messages = [
            self.messages[0],
            {
                "role": "user",
                "content": SUMMARY_CONTEXT_PROMPT.format(
                    summary=summary,
                    current_plan=current_plan,
                    git_snapshot=git_snapshot or "(no snapshot captured)",
                    state_checkpoint=self.state_checkpoint(),
                ),
            },
        ]
        self.prompt_tokens = 0
        self._archive_watermark = len(self.messages)
        self._tokens_at_last_archive = 0
        return True, usage
