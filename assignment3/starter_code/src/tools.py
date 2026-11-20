"""Model tool schemas and their agent-side dispatch.

Each tool operates through ``Sandbox``; this process never directly accesses a
task repository.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

from .config import READ_MAX_CHARS


def _reads_files_via_bash(command: str) -> bool:
    """True when a shell segment prints file contents (cat/head/tail/sed -n...).

    Real writes (``> file``, heredocs) and ``/dev/null`` redirects are let
    through; only content dumps into the conversation are blocked.
    """
    without_null = re.sub(r"\d*>>?\s*/dev/null", "", command)
    if ">" in without_null:
        return False
    for segment in re.split(r"&&|;|\|\|", command):
        seg = segment.strip()
        if re.match(r"(cat|head|tail|less|more)\s+\S", seg):
            return True
        if re.match(r"sed\s+(-\S*n\S*|--silent)", seg):
            return True
    return False


class Tool(ABC):
    """One model-visible tool with an agent-side implementation."""

    name: str
    schema: dict[str, Any]

    @abstractmethod
    def execute(self, sb, args: dict[str, Any], task_memory) -> Any:
        """Run via the supplied sandbox and return a JSON-serializable result."""


class ReadFileTool(Tool):
    name = "read_file"
    schema = {"type": "function", "function": {
        "name": name,
        "description": (
            "Read numbered lines from a text file. Without end_line, returns the "
            "entire file from start_line to EOF. Very large results are returned "
            "as a leading page; continue with the reported start_line."
        ),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Path relative to the working directory."},
            "start_line": {"type": "integer", "minimum": 1, "description": "First line; defaults to 1."},
            "end_line": {"type": "integer", "minimum": 1, "description": "Last inclusive line; defaults to EOF."},
        }, "required": ["path"]},
    }}

    def execute(self, sb, args, task_memory):
        start = args.get("start_line", 1)
        text = sb.read_text(args["path"], start, args.get("end_line"))
        if len(text) <= READ_MAX_CHARS:
            return text
        # Leading page, line aligned; the file is the source, so the model
        # just pages forward with the reported start_line.
        cut = text.rfind("\n", 0, READ_MAX_CHARS)
        if cut < 200:
            cut = READ_MAX_CHARS
        head = text[:cut]
        shown = head.count("\n") + 1
        return (head
                + f"\n... [showing lines {start}-{start + shown - 1} of {start + text.count(chr(10))}; "
                  f"continue with start_line={start + shown}] ...")


class ReadArchivedOutputTool(Tool):
    name = "read_archived_output"
    schema = {"type": "function", "function": {
        "name": name,
        "description": (
            "Read a complete older tool result that was compacted from conversation memory. "
            "Use the output reference shown as tool_output_####. Without end_line, returns "
            "the entire archived result. The content is historical: the stored output from "
            "the original call, not a fresh execution. The archived marker line reports "
            "original_chars: for large results, pass start_line and end_line to fetch only "
            "the section you need instead of re-adding the whole output to context."
        ),
        "parameters": {"type": "object", "properties": {
            "output_id": {"type": "string", "description": "The tool_output_#### reference."},
            "start_line": {"type": "integer", "minimum": 1, "description": "First output line; defaults to 1."},
            "end_line": {"type": "integer", "minimum": 1, "description": "Last inclusive output line; defaults to EOF."},
        }, "required": ["output_id"]},
    }}

    def execute(self, sb, args, task_memory):
        return task_memory.read_archived_output(
            args["output_id"], args.get("start_line", 1), args.get("end_line")
        )


class SearchTool(Tool):
    name = "search"
    schema = {"type": "function", "function": {
        "name": name,
        "description": "Find file names or literal text in a file or directory.",
        "parameters": {"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["files", "text"]},
            "query": {"type": "string"},
            "path": {"type": "string", "description": "Optional location; defaults to the workdir."},
        }, "required": ["mode", "query"]},
    }}

    def execute(self, sb, args, task_memory):
        return sb.search_text(args["mode"], args["query"], args.get("path", "."))


class EditFileTool(Tool):
    name = "edit_file"
    schema = {"type": "function", "function": {
        "name": name,
        "description": (
            "Apply a batch of exact-match edits. Each old_string must appear "
            "exactly once in its file (or set replace_all for every "
            "occurrence); hunks apply in order to the evolving content. Valid "
            "hunks are applied and every hunk's outcome is reported, so a "
            "rejected hunk can be fixed alone without redoing the rest. Put "
            "every hunk of one logical change in a single call, across files."
        ),
        "parameters": {"type": "object", "properties": {
            "files": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
                "path": {"type": "string", "description": "Path relative to the working directory."},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean",
                                "description": "Replace every occurrence instead of requiring a unique match."},
            }, "required": ["path", "old_string", "new_string"]}},
        }, "required": ["files"]},
    }}

    def execute(self, sb, args, task_memory):
        return sb.edit_batch(args.get("files") or [])


class CreateFileTool(Tool):
    name = "create_file"
    schema = {"type": "function", "function": {
        "name": name,
        "description": "Write a text file; creates it if it does not exist.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"},
        }, "required": ["path", "content"]},
    }}

    def execute(self, sb, args, task_memory):
        existed = sb.write_text(args["path"], args["content"])
        return {"path": args["path"], "created": not existed, "overwritten": existed}


class BashTool(Tool):
    name = "bash"
    schema = {"type": "function", "function": {
        "name": name,
        "description": ("Run a shell command in the task working directory. "
                        "Use it for tests, git, and running programs; reading "
                        "file contents is blocked - use read_file instead."),
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"},
            "timeout_s": {"type": "integer", "minimum": 1, "maximum": 900},
        }, "required": ["command"]},
    }}

    def execute(self, sb, args, task_memory):
        command = args["command"]
        if _reads_files_via_bash(command):
            return ("Blocked: read file contents with the read_file tool "
                    "(path, start_line, end_line) instead of bash.")
        return sb.exec(command, args.get("timeout_s", 60))


class UpdatePlanTool(Tool):
    name = "update_plan"
    schema = {"type": "function", "function": {
        "name": name,
        "description": "Set the complete current task plan and each step's status.",
        "parameters": {"type": "object", "properties": {
            "steps": {"type": "array", "items": {"type": "object", "properties": {
                "step": {"type": "string"},
                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
            }, "required": ["step", "status"]}},
        }, "required": ["steps"]},
    }}

    def execute(self, sb, args, task_memory):
        steps = args["steps"]
        if not isinstance(steps, list) or not steps:
            raise ValueError("steps must be a non-empty list")
        for step in steps:
            if not isinstance(step, dict) or not isinstance(step.get("step"), str):
                raise ValueError("every plan step must have text")
            if step.get("status") not in {"pending", "in_progress", "completed"}:
                raise ValueError("every plan step must have a valid status")
        task_memory.current_plan = steps
        return "Plan updated:\n" + json.dumps(steps, ensure_ascii=False, indent=2)


TOOLS = [
    ReadFileTool(), ReadArchivedOutputTool(), SearchTool(), EditFileTool(),
    CreateFileTool(), BashTool(), UpdatePlanTool(),
]
TOOL_LIST = [tool.schema for tool in TOOLS]
TOOL_BY_NAME = {tool.name: tool for tool in TOOLS}


def execute_tool(sb, name: str, args: dict[str, Any], task_memory) -> Any:
    """Dispatch a model tool call without local task-repository access."""
    try:
        tool = TOOL_BY_NAME[name]
    except KeyError as exc:
        raise ValueError(f"unknown tool: {name}") from exc
    return tool.execute(sb, args, task_memory)
