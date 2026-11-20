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
    add_tool(tm, "edit_file", '{"edited":["/app/main.go"]}',
             {"files": [{"path": "/app/main.go", "old_string": "a", "new_string": "b"}]})
    tm.current_plan = [{"step": "verify"}]
    tm._compact_memory = lambda client, model_id: ("SUMMARY", {"prompt_tokens": 1})
    applied, _ = tm.apply_summary(None, "m")
    assert applied and len(tm.messages) == 2
    content = tm.messages[1]["content"]
    assert "tool_output_0001" in content and "/app/main.go" in content
    assert "go test" in content and "SUMMARY" in content
    assert tm._tokens_at_last_archive == 0 and tm.prompt_tokens == 0
    print("  5. layer-2 state checkpoint ...... ok")

    # 6. read_file source cap: ReadFileTool.execute returns a leading page
    #    + forward-continuation hint; memory.py has no read_file branch.
    from src.tools import ReadFileTool

    class FakeSb:
        def __init__(self, text):
            self.text = text

        def read_text(self, path, start=1, end=None):
            return self.text

    big_read = "\n".join(f"{i:>4}  line_{i} = {'x' * 40}" for i in range(1, 400))
    mid_read = "\n".join(f"{i:>4}  y_{i}" for i in range(1, 80))          # ~1.1K chars
    tool = ReadFileTool()
    assert tool.execute(FakeSb(mid_read), {"path": "/app/m.py"}, None) == mid_read, \
        "reads under the cap stay verbatim"
    page = tool.execute(FakeSb(big_read), {"path": "/app/big.py"}, None)
    assert "continue with start_line=" in page and "showing lines 1-" in page
    assert page.startswith("   1  line_1") and big_read.splitlines()[-1] not in page
    assert len(page) < len(big_read) / 4
    m = re.search(r"continue with start_line=(\d+)", page)
    assert m and int(m.group(1)) == page[:m.start()].count("\n") + 1
    page50 = tool.execute(FakeSb(big_read), {"path": "/app/big.py", "start_line": 50}, None)
    assert "showing lines 50-" in page50 and "continue with start_line=" in page50
    tm = new_memory()
    assert tm.record_tool_result("r1", "read_file", page, {"path": "/app/big.py"}) is None, \
        "no born-preview branch for read_file"
    tm.messages = [msg("system", "S"), msg("user", "U"),
                   msg("tool", "[tool_result {}]\n" + page, tool_call_id="r1")]
    tm.tool_results = {"r1": ("read_file", page)}
    tm.prompt_tokens = 45000
    n = tm.compact_tool_outputs()
    assert n == 1 and tm.messages[2]["content"].startswith("[Archived tool output ref:"), \
        "batch should shrink the tool page to the standard archive form"
    print("  6. read_file source cap ...... ok")

    # 7. do_edit_batch atomicity: all hunks validate against evolving content
    #    before any write; one bad hunk rolls back the whole batch (all files).
    import tempfile
    from src import sandbox_server as srv
    with tempfile.TemporaryDirectory() as root:
        srv.ROOT = root
        srv.WORK_ROOT = os.path.join(root, "work")
        os.makedirs(srv.WORK_ROOT, exist_ok=True)
        with open(os.path.join(root, "a.py"), "w") as fh:
            fh.write("x1\nx2\nx3\n")
        with open(os.path.join(root, "b.py"), "w") as fh:
            fh.write("keep\n")
        # happy path: two hunks in a.py (sequential on evolving content) + b.py
        res = srv.do_edit_batch([
            {"path": "a.py", "old_string": "x1", "new_string": "y1"},
            {"path": "a.py", "old_string": "x3", "new_string": "y3"},
            {"path": "b.py", "old_string": "keep", "new_string": "changed"},
        ], root)
        assert res["hunks"] == 3 and sorted(res["edited"]) == ["a.py", "b.py"]
        assert open(os.path.join(root, "a.py")).read() == "y1\nx2\ny3\n"
        # rollback: valid first hunk + ambiguous second -> nothing written
        before_a = open(os.path.join(root, "a.py")).read()
        before_b = open(os.path.join(root, "b.py")).read()
        try:
            srv.do_edit_batch([
                {"path": "a.py", "old_string": "y1", "new_string": "z1"},
                {"path": "b.py", "old_string": "nope", "new_string": "q"},
            ], root)
            raise AssertionError("batch should have failed on missing old_string")
        except srv.PathError as err:
            assert "nope" in str(err) or "b.py" in str(err)
        assert open(os.path.join(root, "a.py")).read() == before_a, "a.py must be untouched on rollback"
        assert open(os.path.join(root, "b.py")).read() == before_b, "b.py must be untouched on rollback"
        # replace_all: every occurrence in one hunk; still atomic with others
        with open(os.path.join(root, "c.py"), "w") as fh:
            fh.write("foo bar\nkeep\nfoo baz\nfoo qux\n")
        srv.do_edit_batch([
            {"path": "c.py", "old_string": "foo ", "new_string": "quux ", "replace_all": True},
            {"path": "c.py", "old_string": "keep", "new_string": "held"},
        ], root)
        assert open(os.path.join(root, "c.py")).read() == "quux bar\nheld\nquux baz\nquux qux\n"
        before_c = open(os.path.join(root, "c.py")).read()
        try:
            srv.do_edit_batch([
                {"path": "c.py", "old_string": "held", "new_string": "gone"},
                {"path": "c.py", "old_string": "absent", "new_string": "x", "replace_all": True},
            ], root)
            raise AssertionError("replace_all with zero matches must fail")
        except srv.PathError as err:
            assert "absent" in str(err)
        assert open(os.path.join(root, "c.py")).read() == before_c, "failed batch must not write"
    print("  7. edit_batch atomicity ...... ok")
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
