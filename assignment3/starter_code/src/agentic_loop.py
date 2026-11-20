"""Task agent and the model <-> tool execution loop."""

import json
import os
import re
import sys
import threading
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from . import memory
from dispatcher import DispatcherShutdown
from openai import APIConnectionError, APIStatusError
from .config import (
    API_BACKOFF_MAX_S,
    API_BACKOFF_S,
    API_RETRIES,
    MAX_ITERATIONS,
    MAX_OPEN_TASKS,
    MODEL_EXTRA_BODY,
    MODEL_STANDARD,
    PRESERVE_THINKING,
    RETRYABLE_STATUS,
    api_settings,
)
from .prompts import AGENT_SYSTEM_PROMPT, PROGRESS_REMINDER, REPEATED_READ_REMINDER
from .sandbox import Sandbox, SandboxError, SandboxPathError
from .tools import TOOL_LIST, execute_tool


def _debug(message: str) -> None:
    print(f"[madsOpt] {message}", file=sys.stderr, flush=True)


LIVE_TRACE = os.getenv("MADSOPT_LIVE_TRACE", "1").lower() not in {"0", "false", "no"}
LIVE_TRACE_MAX_CHARS = int(os.getenv("MADSOPT_LIVE_TRACE_MAX_CHARS", "4000"))
# Tool results are the noisiest stream; show them only when explicitly asked.
LIVE_TOOL_RESULTS = (os.getenv("MADSOPT_LIVE_TOOL_RESULTS") or "0").lower() not in {"0", "false", "no"}
# A model's final text often repeats a code patch already visible through tool calls.
LIVE_MODEL_OUTPUT = (os.getenv("MADSOPT_LIVE_MODEL_OUTPUT") or "0").lower() not in {"0", "false", "no"}
REPEATED_READ_THRESHOLD = int(os.getenv("MADSOPT_REPEATED_READ_THRESHOLD", "3"))
TOOL_RESULT_PREVIEW_CHARS = int(os.getenv("MADSOPT_TOOL_RESULT_PREVIEW_CHARS", "400"))

# Which task this worker thread is serving, so interleaved live traces stay attributable.
_live_state = threading.local()


def _live_line(message: str) -> None:
    if not LIVE_TRACE:
        return
    task = getattr(_live_state, "task", None)
    _debug(f"[task {task}] {message}" if task is not None else message)


def _live(label: str, value) -> None:
    """Show the agent's progress in Docker logs without changing its trace schema."""
    if not LIVE_TRACE or value in (None, ""):
        return
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    if len(text) > LIVE_TRACE_MAX_CHARS:
        text = text[:LIVE_TRACE_MAX_CHARS] + "\n... [truncated]"
    _live_line(f"{label}\n{text}")


def _reasoning_content(message) -> str | None:
    """Read Qwen's optional OpenAI-compatible reasoning field when present."""
    return getattr(message, "reasoning_content", None) or (
            getattr(message, "model_extra", {}) or {}
    ).get("reasoning_content")


def _live_thinking(reasoning: str | None) -> None:
    """Show readable reasoning without copying the model's illustrative code blocks."""
    if not LIVE_TRACE or not reasoning:
        return
    text = re.sub(r"```.*?```", "\n[code block omitted]\n", reasoning, flags=re.DOTALL)
    if len(text) > LIVE_TRACE_MAX_CHARS:
        text = text[:LIVE_TRACE_MAX_CHARS] + "\n... [truncated]"
    _live_line(f"[thinking]\n{text}")


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
        _live_line(
            f"[summary] model calls={calls}  model_time={total / 60:.1f}min  "
            f"avg={total / calls:.1f}s  wall={wall_s / 60:.1f}min  "
            f"model share={total / max(wall_s, 1e-9) * 100:.0f}%"
        )


def call_model(client, model_id: str, messages: list, logger, iteration: int, **kwargs):
    """Make one chat-completions request, retrying transient upstream failures.

    Latency is measured on the successful attempt only; waiting time shows up
    as api_retry events in the graded trace instead.
    """
    extra_body = dict(kwargs.pop("extra_body", None) or {})
    extra_body.update(MODEL_EXTRA_BODY.get(model_id, {}))
    if extra_body:
        kwargs["extra_body"] = extra_body
    delay = API_BACKOFF_S
    for attempt in range(API_RETRIES + 1):
        t0 = time.perf_counter()
        try:
            response = client.chat.completions.create(
                model=model_id, messages=messages, **kwargs)
            break
        except APIConnectionError as error:
            if attempt == API_RETRIES:
                raise
            error_name = type(error).__name__
        except APIStatusError as error:
            if error.status_code not in RETRYABLE_STATUS or attempt == API_RETRIES:
                raise
            error_name = f"{type(error).__name__}({error.status_code})"
        logger.api_retry(iteration, error_name, delay)
        _live_line(
            f"[api retry] attempt {attempt + 1}/{API_RETRIES}: "
            f"{error_name}; waiting {delay:.0f}s")
        time.sleep(delay)
        delay = min(delay * 2, API_BACKOFF_MAX_S)
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
    _live_line(
        f"[api] {latency_s:.1f}s  model={model_id}  "
        f"in={prompt_tokens}  out={completion_tokens}  cached={cached_tokens}"
    )
    return response


def _valid_arguments(raw: str) -> str:
    # The provider rejects histories containing non-JSON function.arguments
    # (400). The execution path still sees the raw string and reports the parse
    # error back to the model, so only the history copy is normalized.
    try:
        if isinstance(json.loads(raw or "{}"), dict):
            return raw
    except ValueError:
        pass
    return "{}"


def _assistant_message(message, tool_calls: list) -> dict:
    """The history entry for one model turn.

    reasoning_content is echoed back only when PRESERVE_THINKING is on:
    Qwen's preserve_thinking then feeds prior reasoning into the next call.
    """
    entry = {"role": "assistant", "content": message.content}
    if tool_calls:
        entry["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": _valid_arguments(call.function.arguments),
                },
            }
            for call in tool_calls
        ]
    if PRESERVE_THINKING:
        reasoning = _reasoning_content(message)
        if reasoning:
            entry["reasoning_content"] = reasoning
    return entry


def _tool_call_summary(name: str, arguments_json: str) -> str:
    """One readable line per call: tool name plus its identifying fields only."""
    try:
        args = json.loads(arguments_json or "{}")
    except ValueError:
        return name
    if not isinstance(args, dict):
        return name
    if name == "bash":
        return f"bash: {re.sub(chr(10), ' ', str(args.get('command', '')))[:100]}"
    if name == "read_file":
        return (f"read_file: {args.get('path', '')} "
                f"[{args.get('start_line', 1)}-{args.get('end_line', 'EOF')}]")
    if name == "search":
        return (f"search: {args.get('mode', '')} {str(args.get('query', ''))[:60]!r} "
                f"in {args.get('path', '.')}")
    if name in {"edit_file", "create_file"}:
        return f"{name}: {args.get('path', '')}"
    if name == "read_archived_output":
        return (f"read_archived_output: {args.get('output_id', '')} "
                f"[{args.get('start_line', 1)}-{args.get('end_line', 'EOF')}]")
    if name == "update_plan":
        return f"update_plan: {len(args.get('steps') or [])} step(s)"
    return name


def _tool_content(value) -> str:
    """OpenAI tool messages require a string, even for structured results."""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _tool_result_header(
        tool_name: str,
        arguments: dict,
        value,
        is_error: bool,
        duration_s: float,
        body: str,
) -> str:
    """Presentation header for a live tool message; the body stays raw."""
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
    return "[tool_result " + json.dumps(metadata, ensure_ascii=False, sort_keys=True) + "]"


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
                    # solve()'s finally already extracts/submits/done()s a crashed
                    # task; swallow its exception so the remaining tasks still run.
                    for future in done:
                        try:
                            future.result()
                        except Exception as error:
                            _live_line(f"task crashed, continuing with remaining tasks: {error!r}")
                            traceback.print_exc()

    def solve(self, task: dict):

        k = task["task"]
        _live_state.task = k
        logger = self.dispatcher.logger(k)
        sb = Sandbox(task["sandbox_url"], task["workdir"])
        client = self._new_client()
        # Roughly 25 progress checks spread across the whole turn budget.
        pace_every = max(1, round(MAX_ITERATIONS / 25))
        task_memory = memory.TaskMemory([
            {"role": "system", "content": AGENT_SYSTEM_PROMPT.format(
                max_iterations=MAX_ITERATIONS, pace_every=pace_every)},
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
                _live_line(f"===== turn {iteration + 1}/{MAX_ITERATIONS} =====")
                if (iteration + 1) % pace_every == 0 and iteration + 1 < MAX_ITERATIONS:
                    task_memory.messages.append({
                        "role": "user",
                        "content": PROGRESS_REMINDER.format(
                            iteration=iteration + 1,
                            max_iterations=MAX_ITERATIONS,
                            remaining=MAX_ITERATIONS - iteration - 1,
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
                _live_thinking(_reasoning_content(message))
                if LIVE_MODEL_OUTPUT:
                    _live("[output]", message.content)
                for call in tool_calls:
                    _live_line(
                        f"[tool call] {_tool_call_summary(call.function.name, call.function.arguments)}")
                task_memory.messages.append(_assistant_message(message, tool_calls))

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

                    body = _tool_content(result)
                    header = _tool_result_header(
                        name,
                        arguments,
                        result,
                        is_error,
                        time.perf_counter() - tool_started,
                        body,
                    )
                    content = f"{header}\n{body}"

                    logger.tool_result(iteration, name, content, is_error)
                    task_memory.record_tool_result(call.id, name, body)
                    if LIVE_TOOL_RESULTS:
                        preview = body[:TOOL_RESULT_PREVIEW_CHARS]
                        if len(body) > TOOL_RESULT_PREVIEW_CHARS:
                            preview += "\n... [preview]"
                        _live(
                            f"[tool result] {name} "
                            f"({'error' if is_error else 'ok'}, {len(body)} chars)",
                            preview)
                    elif is_error:
                        _live_line(f"[tool error] {name}: {body[:200]}")
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

                archived = task_memory.compact_tool_outputs()
                if archived:
                    _live_line(f"[compaction] archived {archived} older tool output(s)")
                if task_memory.should_compress():
                    _live_line("[compaction] summarizing context...")
                    t0 = time.perf_counter()
                    snapshot = _state_snapshot(sb)
                    applied, compaction_usage = task_memory.apply_summary(
                        client, self.model_id, snapshot)
                    _live_line(
                        f"[compaction] {'applied' if applied else 'failed; context unchanged'}")
                    compaction_record = {
                        "kind": "compaction",
                        "iteration": iteration,
                        "model_id": self.model_id,
                        "latency_s": round(time.perf_counter() - t0, 2),
                    }
                    if compaction_usage:
                        compaction_record.update(compaction_usage)
                        # The course trace charges every actual model request.  The
                        # sidecar additionally identifies this one as compaction.
                        logger.api_request(
                            iteration,
                            compaction_usage["prompt_tokens"],
                            compaction_usage["completion_tokens"],
                            compaction_usage["total_tokens"],
                            self.model_id,
                        )
                    _record_latency(logger, compaction_record)

        except Exception:
            reason = "error"
            raise
        finally:
            # Submit whatever the repo holds, however the loop ended: an error
            # before extraction would otherwise discard every edit (0-byte patch).
            try:
                size = self.dispatcher.extract_patch(k)
                if size:
                    evaluation = self.dispatcher.evaluate(k)
                    _debug(f"task {k}: {evaluation['tests_failed']}/{evaluation['tests_total']} tests failing")
            except DispatcherShutdown:
                pass
            except Exception as submit_error:
                _debug(f"task {k}: final submit failed: {submit_error}")
            try:
                _log_latency_summary(logger, time.perf_counter() - t_task0)
            except OSError:
                pass
            try:
                self.dispatcher.done(k, reason, last_iteration)
            except DispatcherShutdown:
                pass
