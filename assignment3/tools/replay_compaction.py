#!/usr/bin/env python3
"""Replay test for the layer-1 compaction redesign.

Part A: synthetic checks of the new memory.TaskMemory rules (born-preview
bash, hysteresis, window budget, watermark stability, layer-2 reset).
Part B: replay a real task trace through OLD (24K trickle, keep-12) and NEW
(40K + 8K hysteresis + 8K-token window + born-preview) algorithms and compare
batch frequency and request-prefix stability (the cache-hit proxy).
"""
import json
import os
import re
import sys
import types
from dataclasses import dataclass, field

# memory.py imports openai only for APIError; stub it out for offline tests.
sys.modules.setdefault("openai", types.SimpleNamespace(APIError=Exception))
HERE = os.path.dirname(os.path.abspath(__file__))          # assignment3/tools
STARTER = os.path.join(os.path.dirname(HERE), "starter_code")
sys.path.insert(0, STARTER)

from src import memory                                                # noqa: E402
from src.memory import TaskMemory                                     # noqa: E402


def msg(role, content, **kw):
    d = {"role": role, "content": content}
    d.update(kw)
    return d


def new_memory():
    return TaskMemory([msg("system", "S"), msg("user", "U")])


def add_tool(tm, name, body, arguments=None, is_error=False):
    call_id = f"call_{len(tm.tool_results) + 1}"
    born = tm.record_tool_result(call_id, name, body, arguments or {}, is_error)
    header = json.dumps({"tool": name, "content_chars": len(body)}, sort_keys=True)
    if born:
        header = json.dumps({"tool": name, "content_chars": len(body),
                             "output_id": born[0]}, sort_keys=True)
    content = f"[tool_result {header}]\n" + (born[1] if born else body)
    tm.messages.append(msg("tool", content, tool_call_id=call_id))
    return content


def part_a():
    print("== Part A: synthetic rules ==")
    tm = new_memory()

    # 1. bash born-preview
    big = "L" * 50 + "\n" + ("x" * 2500) + "\nFAILED tests/test_a.py::test_x - AssertionError"
    born = tm.record_tool_result("c1", "bash", big, {"command": "pytest -q"}, False)
    assert born and born[1].endswith("AssertionError"), "bash >=2000 must preview at birth"
    assert tm.archived_tool_outputs[born[0]][1] == big, "full raw must be archived"
    assert tm.archive_index[-1][0] == born[0]
    print("  1. bash born-preview .............. ok")

    # 2. hysteresis + threshold gating
    tm = new_memory()
    for i in range(30):
        add_tool(tm, "bash", "y" * 3000, {"command": f"pytest t{i}"})
        add_tool(tm, "read_file", "r" * 3000, {"path": f"/app/f{i}.py"})
    tm.prompt_tokens = 30000
    assert tm.compact_tool_outputs() == 0, "must not fire below 40K"
    tm.prompt_tokens = 41000
    first = tm.compact_tool_outputs()
    assert first > 0, "must fire past 40K"
    tm.prompt_tokens = 45000
    assert tm.compact_tool_outputs() == 0, "hysteresis must block <8K growth"
    for i in range(30, 40):
        add_tool(tm, "read_file", "r" * 2500, {"path": f"/app/g{i}.py"})
    tm.prompt_tokens = 50000
    second = tm.compact_tool_outputs()
    assert second > 0, "must fire again after 8K growth with new messages"
    print(f"  2. threshold+hysteresis .......... ok (batches {first}, {second})")

    # 3. watermark stability: nothing below the old watermark rewritten again
    tm = new_memory()
    for i in range(40):
        add_tool(tm, "read_file", "r" * 2500, {"path": f"/app/m{i}.py"})
    tm.prompt_tokens = 42000
    tm.compact_tool_outputs()
    wm_before = tm._archive_watermark
    snap = [m["content"] for m in tm.messages]
    for i in range(40, 50):
        add_tool(tm, "read_file", "r" * 2500, {"path": f"/app/m{i}.py"})
    tm.prompt_tokens = 51000
    tm.compact_tool_outputs()
    changed = [i for i, (a, b) in enumerate(zip(snap, [m["content"] for m in tm.messages]))
               if a != b]
    assert all(i >= wm_before for i in changed), \
        f"messages below watermark {wm_before} were rewritten: {changed}"
    assert tm._archive_watermark >= wm_before
    print(f"  3. watermark ..................... ok ({len(changed)} msgs rewritten, all >= {wm_before})")

    # 4. short outputs never archived, count constraint gone
    tm = new_memory()
    for i in range(60):
        add_tool(tm, "edit_file", "o" * 60, {"path": f"/app/e{i}.py"})
    tm.prompt_tokens = 45000
    assert tm.compact_tool_outputs() == 0, "short outputs must stay verbatim forever"
    print("  4. short-output exemption ........ ok")

    # 5. layer-2 reset + state checkpoint
    tm = new_memory()
    b = add_tool(tm, "bash", "z" * 3000, {"command": "go test ./..."})
    add_tool(tm, "edit_file", "ok", {"path": "/app/main.go"})
    tm.current_plan = [{"step": "verify"}]
    tm._compact_memory = lambda client, model_id: ("SUMMARY", {"prompt_tokens": 1})
    applied, _ = tm.apply_summary(None, "m")
    assert applied and len(tm.messages) == 2
    content = tm.messages[1]["content"]
    assert "tool_output_0001" in content and "/app/main.go" in content
    assert "go test" in content and "SUMMARY" in content
    assert tm._tokens_at_last_archive == 0 and tm.prompt_tokens == 0
    print("  5. layer-2 state checkpoint ...... ok")
    print("Part A passed.\n")


# ---- old algorithm (24K trickle, keep last 12, min 600, head400/tail800) ----
def old_compact(messages, tool_raw):
    if tool_raw["prompt"] < 24000:
        return 0
    tools = [m for m in messages if m.get("role") == "tool"]
    n = 0
    for message in tools[:-12]:
        content = message["content"]
        if content.startswith("[Archived tool output ref:") or len(content) < 600:
            continue
        message["content"] = ("[Archived tool output ref: old]\n"
                              + content[:400] + content[-800:])
        n += 1
    return n


def prefix_fraction(prev, cur):
    """Chars identical from the start, over total length of cur."""
    total = sum(len(m["content"]) for m in cur)
    if total == 0:
        return 1.0
    shared = 0
    for a, b in zip(prev, cur):
        if a["content"] == b["content"]:
            shared += len(b["content"])
        else:
            break
    return shared / total


def replay_new(events, tokens_by_iter):
    tm = new_memory()
    batches = rewrites = 0
    fractions = []
    prev = [dict(m) for m in tm.messages]
    for ev in events:
        if ev["kind"] == "tool":
            add_tool(tm, ev["tool"], ev["body"], ev.get("args", {}))
        tm.prompt_tokens = tokens_by_iter.get(ev["iter"], tm.prompt_tokens)
        n = tm.compact_tool_outputs()
        if n:
            batches += 1
            rewrites += n
        cur = tm.messages
        fractions.append(prefix_fraction(prev, [{"content": m["content"]} for m in cur]))
        prev = [dict(m) for m in cur]
    return batches, rewrites, sum(fractions) / max(len(fractions), 1)


def replay_old(events, tokens_by_iter):
    messages = [msg("system", "S"), msg("user", "U")]
    state = {"prompt": 0}
    batches = rewrites = 0
    fractions = []
    prev = [dict(m) for m in messages]
    for ev in events:
        if ev["kind"] == "tool":
            # old scheme: full body at insertion, rewritten only when archived
            messages.append(msg("tool", "[tool_result x]\n" + ev["body"]))
        state["prompt"] = tokens_by_iter.get(ev["iter"], state["prompt"])
        n = old_compact(messages, state)
        if n:
            batches += 1
            rewrites += n
        cur = [{"content": m["content"]} for m in messages]
        fractions.append(prefix_fraction(prev, cur))
        prev = [dict(m) for m in messages]
    return batches, rewrites, sum(fractions) / max(len(fractions), 1)


def part_b(tdir):
    print(f"== Part B: replay {tdir} ==")
    events, tokens = [], {}
    for line in open(os.path.join(tdir, "run.jsonl"), encoding="utf-8"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("event") == "tool_result":
            first, _, body = (e.get("result") or "").partition("\n")
            events.append({"kind": "tool", "iter": e.get("iteration", 0),
                           "tool": e.get("tool_name", "?"), "body": body})
    for line in open(os.path.join(tdir, "latency.jsonl"), encoding="utf-8"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("kind") == "chat" and r.get("prompt_tokens"):
            tokens[r.get("iteration")] = r["prompt_tokens"]
    # align: token for iteration i is the request BEFORE turn i's tools; use i+1
    tokens_by_iter = {i - 1: t for i, t in tokens.items() if i >= 1}
    ob, orw, of_ = replay_old(events, tokens_by_iter)
    nb, nrw, nf_ = replay_new(events, tokens_by_iter)
    print(f"  old: {ob:3d} batch turns, {orw:4d} msgs rewritten, "
          f"mean prefix-stable fraction {of_ * 100:.1f}%")
    print(f"  new: {nb:3d} batch turns, {nrw:4d} msgs rewritten, "
          f"mean prefix-stable fraction {nf_ * 100:.1f}%")
    assert nb < ob and nf_ > of_, "new scheme must reduce batch turns and stabilize prefixes"
    print("Part B passed.\n")


if __name__ == "__main__":
    part_a()
    default = os.path.join(os.path.dirname(HERE), "previous_runs",
                           "20261120-004216", "madsOpt_logs", "0")
    for arg in sys.argv[1:] or [default]:
        part_b(arg)
