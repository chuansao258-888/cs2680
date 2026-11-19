"""Task agent and the model <-> tool execution loop."""

import json
import os
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from . import memory
from dispatcher import DispatcherShutdown
from .config import MAX_ITERATIONS, MAX_OPEN_TASKS, MODEL_STANDARD, api_settings
from .prompts import AGENT_SYSTEM_PROMPT, PROGRESS_REMINDER, REPEATED_READ_REMINDER
from .sandbox import Sandbox, SandboxError, SandboxPathError
from .tools import TOOL_LIST, execute_tool


def _debug(message: str) -> None:
    print(f"[madsOpt] {message}", file=sys.stderr, flush=True)


LIVE_TRACE = os.getenv("MADSOPT_LIVE_TRACE", "1").lower() not in {"0", "false", "no"}
LIVE_TRACE_MAX_CHARS = int(os.getenv("MADSOPT_LIVE_TRACE_MAX_CHARS", "4000"))
# Tool results are the noisiest stream; show them only when explicitly asked.
LIVE_TOOL_RESULTS = (os.getenv("MADSOPT_LIVE_TOOL_RESULTS") or "0").lower() not in {"0", "false", "no"}
REPEATED_READ_THRESHOLD = int(os.getenv("MADSOPT_REPEATED_READ_THRESHOLD", "3"))


def _live(label: str, value) -> None:
    """Show the agent's progress in Docker logs without changing its trace schema."""
    if not LIVE_TRACE or value in (None, ""):
        return
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    if len(text) > LIVE_TRACE_MAX_CHARS:
        text = text[:LIVE_TRACE_MAX_CHARS] + "\n... [truncated]"
    _debug(f"[{label}]\n{text}")


def _reasoning_content(message) -> str | None:
    """Read Qwen's optional OpenAI-compatible reasoning field when present."""
    return getattr(message, "reasoning_content", None) or (
            getattr(message, "model_extra", {}) or {}
    ).get("reasoning_content")


def _latency_path(logger) -> str | None:
    """Per-task sidecar for timings; the graded run.jsonl schema stays untouched."""
    fh = getattr(logger, "_fh", None)
    if fh is None:
        return None
    return os.path.join(os.path.dirname(fh.name), "latency.jsonl")


def _record_latency(logger, record: dict) -> None:
    path = _latency_path(logger)
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _log_latency_summary(logger, wall_s: float) -> None:
    path = _latency_path(logger)
    if not path or not os.path.exists(path):
        return
    total = 0.0
    calls = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            total += record["latency_s"]
            calls += 1
    if calls:
        _live(
            "api summary",
            f"model calls={calls}  model_time={total / 60:.1f}min  "
            f"avg={total / calls:.1f}s  wall={wall_s / 60:.1f}min  "
            f"model share={total / max(wall_s, 1e-9) * 100:.0f}%",
        )


def call_model(client, model_id: str, messages: list, logger, iteration: int, **kwargs):
    """Make and record one OpenAI-compatible chat-completions request."""
    t0 = time.perf_counter()
    response = client.chat.completions.create(model=model_id, messages=messages, **kwargs)
    latency_s = time.perf_counter() - t0
    usage = response.usage
    prompt_tokens = getattr(usage, "prompt_tokens", None)
    completion_tokens = getattr(usage, "completion_tokens", None)
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    if isinstance(prompt_details, dict):
        cached_tokens = prompt_details.get("cached_tokens")
    else:
        cached_tokens = getattr(prompt_details, "cached_tokens", None)
    logger.api_request(
        iteration,
        prompt_tokens,
        completion_tokens,
        getattr(usage, "total_tokens", None),
        model_id,
    )
    _record_latency(logger, {
        "kind": "chat",
        "iteration": iteration,
        "model_id": model_id,
        "latency_s": round(latency_s, 2),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_tokens": cached_tokens,
    })
    _live(
        "api latency",
        f"{latency_s:.1f}s  iter={iteration}  model={model_id}  "
        f"in={prompt_tokens}  cached={cached_tokens}  out={completion_tokens}",
    )
    return response


def _tool_content(value) -> str:
    """OpenAI tool messages require a string, even for structured results."""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _format_tool_result(
        tool_name: str,
        arguments: dict,
        value,
        is_error: bool,
        duration_s: float,
) -> str:
    """Add a small, uniform header without discarding a tool's native output."""
    body = _tool_content(value)
    metadata = {
        "tool": tool_name,
        "status": "error" if is_error else "ok",
        "duration_ms": round(duration_s * 1000),
        "content_chars": len(body),
        "content_lines": len(body.splitlines()),
    }
    # Paths identify the affected resource without repeating large arguments such as
    # edit replacement text or create-file contents. Bash returns these fields from
    # the sandbox directly, so preserve them when available.
    if isinstance(arguments.get("path"), str):
        metadata["path"] = arguments["path"]
    if tool_name == "read_archived_output":
        metadata["output_id"] = arguments.get("output_id")
    if isinstance(value, dict):
        if "exit_code" in value:
            metadata["exit_code"] = value["exit_code"]
        if "timed_out" in value:
            metadata["timed_out"] = value["timed_out"]
    return "[tool_result " + json.dumps(metadata, ensure_ascii=False, sort_keys=True) + "]\n" + body


def _state_snapshot(sb, max_chars: int = 6000) -> str:
    try:
        status = sb.exec("git status --short && git diff", timeout_s=60)
        git_output = status.get("output", "")
        if len(git_output) >= max_chars:
            git_output = git_output[:max_chars] + "\n[diff truncated]"
        return git_output
    except (SandboxError, SandboxPathError):
        return ""


class Agent:
    def __init__(self, dispatcher):
        from openai import OpenAI

        self.dispatcher = dispatcher
        self.model_id = MODEL_STANDARD
        self._openai = OpenAI

    def _new_client(self):
        """Each concurrent task owns an API client and its connection pool."""
        api_key, base_url = api_settings()
        return self._openai(base_url=base_url, api_key=api_key)

    def run(self):
        futures = set()
        no_more_tasks = False

        with ThreadPoolExecutor(max_workers=MAX_OPEN_TASKS) as pool:
            while futures or not no_more_tasks:

                # 仅在未达到 5 个运行中 task 时领取新任务
                while not no_more_tasks and len(futures) < MAX_OPEN_TASKS:
                    try:
                        task = self.dispatcher.next_task()
                    except DispatcherShutdown:
                        no_more_tasks = True
                        break

                    if task is None:
                        no_more_tasks = True
                        break
                    futures.add(pool.submit(self.solve, task))
                # 已满 5 个，或没有更多 task：等待至少一个完成
                if futures:
                    done, futures = wait(
                        futures,
                        return_when=FIRST_COMPLETED,
                    )
                    # 让 worker 内部异常在主线程显式暴露
                    for future in done:
                        future.result()

    def solve(self, task: dict):

        k = task["task"]
        logger = self.dispatcher.logger(k)
        sb = Sandbox(task["sandbox_url"], task["workdir"])
        client = self._new_client()
        task_memory = memory.TaskMemory([
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Problem:\n{task['problem_statement']}\n\n"
                    f"Requirements:\n{task['requirements']}\n\n"
                    f"Interface:\n{task['interface']}\n\n"
                    "Inspect, edit, and test the repository with the provided tools."
                ),
            },
        ])

        last_iteration = 0
        reason = "done"
        t_task0 = time.perf_counter()
        read_counts: dict[str, int] = {}
        reminded_paths: set[str] = set()
        try:
            for iteration in range(MAX_ITERATIONS):
                last_iteration = iteration
                # iteration is zero-based, so this adds the two intended reminders
                # immediately before model turns 50 and 100.
                if (iteration + 1) % 50 == 0:
                    task_memory.messages.append({
                        "role": "user",
                        "content": PROGRESS_REMINDER.format(
                            iteration=iteration + 1,
                            max_iterations=MAX_ITERATIONS,
                        ),
                    })

                response = call_model(
                    client,
                    self.model_id,
                    task_memory.messages,
                    logger,
                    iteration,
                    tools=TOOL_LIST,
                )
                task_memory.record_usage(getattr(response.usage, "prompt_tokens", 0) or 0)
                if not response.choices:
                    break

                message = response.choices[0].message
                tool_calls = list(message.tool_calls or [])
                _live("model thinking", _reasoning_content(message))
                _live("model output", message.content)
                for call in tool_calls:
                    _live(f"tool call: {call.function.name}", call.function.arguments)
                assistant_message = {"role": "assistant", "content": message.content}
                if tool_calls:
                    assistant_message["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.function.name,
                                "arguments": call.function.arguments,
                            },
                        }
                        for call in tool_calls
                    ]
                task_memory.messages.append(assistant_message)

                if not tool_calls:
                    break

                reminder_path = None
                for call in tool_calls:
                    name = call.function.name
                    arguments_json = call.function.arguments or "{}"
                    logger.tool_call(iteration, name, arguments_json)
                    is_error = False
                    tool_started = time.perf_counter()
                    arguments = {}
                    try:
                        arguments = json.loads(arguments_json)
                        if not isinstance(arguments, dict):
                            raise ValueError("tool arguments must be a JSON object")
                        if name == "read_file":
                            read_path = str(arguments.get("path", ""))
                            read_counts[read_path] = read_counts.get(read_path, 0) + 1
                            if (read_counts[read_path] >= REPEATED_READ_THRESHOLD
                                    and read_path not in reminded_paths):
                                reminder_path = read_path
                        result = execute_tool(sb, name, arguments, task_memory)
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError,
                            SandboxError, SandboxPathError) as error:
                        is_error = True
                        result = f"Error: {error}"

                    content = _format_tool_result(
                        name,
                        arguments,
                        result,
                        is_error,
                        time.perf_counter() - tool_started,
                    )

                    logger.tool_result(iteration, name, content, is_error)
                    task_memory.record_tool_result(call.id, name, content)
                    if LIVE_TOOL_RESULTS:
                        _live(f"tool result: {name}", content)
                    elif is_error:
                        _live(f"tool error: {name}", content)
                    task_memory.messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": content,
                    })

                if reminder_path:
                    reminded_paths.add(reminder_path)
                    task_memory.messages.append({
                        "role": "user",
                        "content": REPEATED_READ_REMINDER.format(path=reminder_path),
                    })

                task_memory.compact_tool_outputs()
                if task_memory.should_compress():
                    t0 = time.perf_counter()
                    snapshot = _state_snapshot(sb)
                    task_memory.apply_summary(client, self.model_id,snapshot)
                    _record_latency(logger, {
                        "kind": "compaction",
                        "iteration": iteration,
                        "model_id": self.model_id,
                        "latency_s": round(time.perf_counter() - t0, 2),
                    })

            size = self.dispatcher.extract_patch(k)
            if size:
                evaluation = self.dispatcher.evaluate(k)
                _debug(f"task {k}: {evaluation['tests_failed']}/{evaluation['tests_total']} tests failing")
        except Exception:
            reason = "error"
            raise
        finally:
            try:
                _log_latency_summary(logger, time.perf_counter() - t_task0)
            except OSError:
                pass
            try:
                self.dispatcher.done(k, reason, last_iteration)
            except DispatcherShutdown:
                pass
