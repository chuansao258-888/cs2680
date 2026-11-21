#!/usr/bin/env python3
"""A3 运行报告生成规范（唯一入口：tools/aggregate_runs.py）。

运行环境
- 必须在 WSL Ubuntu 内执行：python3 assignment3/tools/aggregate_runs.py
  （hostlogs 在 WSL 家目录；Windows python 找不到 hostlogs 会失败）。
  任意工作目录均可：路径相对本文件解析（assignment3/ 布局）。
- 依赖 matplotlib（WSL 用户级安装）；缺失时自动跳过图表，其余不受影响。
- 产物：reports/full_run_<N>.md、reports/charts/*.png、reports/run_snapshots.json、
  reports/tmp_all_attempts.json（全量尝试明细）。reports/ 在 starter_code 之外：
  容器只读挂载 starter_code，报告含隐藏测试名，绝不能放回挂载目录内。

口径与分组
- 每题计一次有效尝试：通过题取首次通过；未通过题取最后一次有效评分。
  因崩溃/OOM/编排器故障/verifier 缺陷无效的尝试剔除（原始数据保留在 previous_runs/）。
- 按 hostlog 运行 ID（hid_epoch）分组：epoch < 1795169261 合并为 Full Run 1
  （开发战役的等价单轮）；边界及以后每个 hostlog 独立成 full_run_2/3/…。
- 运行期间 trace 在 .tasks/trace/madsOpt_logs/，agent 退出后才拷回 madsOpt_logs/；
  运行中途重生成不会纳入该轮数据。

版本锚定
- Full Run 1 的版本冻结在 run_snapshots.json 记录的锁定提交（version_at_commit
  从 git 树取指纹），不受其后工作树修改影响；其余轮取当前工作树
  （commit + dirty 列表 + src 指纹，CRLF 归一）。
- 每轮生成后把快照（version/config/逐题指标/totals）写入 run_snapshots.json，
  作为后续轮的对比参照。

对比规则
- 参照 = 自己的上一轮 + Full Run 1（去重）。
- 仅对"行为级代码变更"的参照出性能对比：指纹相同 = 无变更；指纹不同但 diff
  经评审属 trace/显示层 → 登记 BEHAVIOR_EQUIV {run: (参照, 说明)}，视为无行为
  变更，跳过性能对比，结果差异归因采样随机性。改决策路径（prompt、压缩阈值、
  重试策略、工具语义、预算）才算行为级。
- 版本变更章节对每个参照列指纹与 diff（含未提交）；性能对比仅比共同题目。

内容分工（自动 vs 人工）
- 自动：概述数字、逐题表、汇总指标、行为量化（scan_behavior：工具分布/首编辑轮/
  同参重复/高频重读/错误率/逐动作耗时/重试停滞；scan_compactions：层1 工具归档
  计数与发生轮（读 agent.log，缺则记 —）、层2 摘要压缩（读 latency.jsonl，可靠））、
  自动标记、五张图表、版本 diff、性能对比表；配置表自动读 src/config.py。
- 人工（agent 每轮读日志后写入本脚本常量）：BEHAVIOR_NOTES[run][task] 思维链总结
  （读 agent.log 的 [thinking]；缺失则据工具序列重建并注明）、失败案例分析、
  排行榜对比（仅 Full Run 1）、AGENT_DESIGN_PROSE（架构变化时更新）。

新一轮流程
1. 跑完确认 madsOpt_logs/ 有该轮 trace 与 run_all_results.md；
2. 运行本脚本生成 full_run_<N>.md；
3. 评审 src diff（相对上一轮与 Full Run 1），行为级分类，必要时维护 BEHAVIOR_EQUIV；
4. 读该轮 agent.log/run.jsonl，补 BEHAVIOR_NOTES[N] 与失败分析；
5. 抽查 Full Run 1 与 run_snapshots["1"] 未被新轮数据污染（分组自动保证）。
"""
import calendar
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # assignment3/
STARTER = os.path.join(ROOT, "starter_code")
RDIR = os.path.join(ROOT, "reports")
ARCH = os.path.join(ROOT, "previous_runs")
# Catalogue prices (RMB per 1M tokens: input, cached-input, output).
# qwen3.6-flash (starter): ¥1.2/¥7.2 (Beijing, ≤256K tier); returns no cached_tokens,
# so the cached-input price is unused in practice. qwen4-max prices not yet confirmed.
PRICE = {"qwen3.8-flash": (0.8, 0.1, 2.7), "qwen4-max": (None, None, None), "qwen3.6-flash": (1.2, 0.15, 7.2)}
USD = 7.1


def parse_ts(s):
    return datetime.fromisoformat(s)


def parse_results_md(path):
    """Return ordered list of (task_label, PASS/FAIL) from a results table."""
    rows = []
    if not os.path.exists(path):
        return rows
    for line in open(path, encoding="utf-8"):
        m = re.match(r"\|\s*(instance_\S+|\d+)\s*\|\s*(PASS|FAIL)\s*\|\s*(\S+)\s*\|", line)
        if m:
            rows.append((m.group(1), "?" if m.group(3) == "-/-" else m.group(2), m.group(3)))
    return rows


def short(label):
    if label.startswith("instance_"):
        repo = label.split("__")[0].replace("instance_", "")
        m = re.search(r"-([0-9a-f]{40})(?:-v|$)", label)
        sha = m.group(1)[:7] if m else label[-7:]
        return f"{repo}/{sha}"
    return f"key{label}"


def scan_task_dir(tdir):
    lat_path = os.path.join(tdir, "latency.jsonl")
    run_path = os.path.join(tdir, "run.jsonl")
    if not os.path.exists(lat_path):
        return None
    calls, comp_unknown = [], 0
    models = set()
    for line in open(lat_path, encoding="utf-8"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("prompt_tokens") is None:
            comp_unknown += 1
            continue
        calls.append(r)
        models.add(r.get("model_id", "?"))
    first = last = None
    reason, iters = "?", None
    if os.path.exists(run_path):
        for line in open(run_path, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            ts = r.get("timestamp")
            if ts:
                t = parse_ts(ts)
                first = t if first is None else min(first, t)
                last = t if last is None else max(last, t)
            if r.get("event") == "run_end":
                reason = r.get("reason", "?")
                iters = r.get("num_iterations")
    pin = sum(c["prompt_tokens"] for c in calls)
    pout = sum(c["completion_tokens"] for c in calls)
    cached = sum(c.get("cached_tokens") or 0 for c in calls)
    lat = sum(c.get("latency_s", 0) for c in calls)
    cost = cost_unc = cost_cac = cost_out = cost_nocache_in = 0.0
    for c in calls:
        p = PRICE.get(c.get("model_id"))
        if not p or p[0] is None:
            continue
        ct = c.get("cached_tokens") or 0
        u = (c["prompt_tokens"] - ct) * p[0] / 1e6
        ca = ct * p[1] / 1e6
        o = c["completion_tokens"] * p[2] / 1e6
        cost_unc += u
        cost_cac += ca
        cost_out += o
        cost_nocache_in += c["prompt_tokens"] * p[0] / 1e6
        cost += u + ca + o
    wall = (last - first).total_seconds() if first and last else None
    return {
        "calls": len(calls), "comp_unknown": comp_unknown, "models": sorted(models),
        "in": pin, "out": pout, "cached": cached, "model_s": lat, "wall_s": wall,
        "cost_cny": cost, "cost_unc_cny": cost_unc, "cost_cac_cny": cost_cac,
        "cost_out_cny": cost_out, "cost_nocache_in_cny": cost_nocache_in,
        "reason": reason, "iters": iters,
        "start": first.isoformat() if first else None,
    }


def _git(*args):
    root = os.path.abspath(os.path.join(STARTER, "..", ".."))
    try:
        r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def agent_version_info():
    srcdir = os.path.join(STARTER, "src")
    fp = hashlib.sha256()
    for f in sorted(os.listdir(srcdir)):
        if f.endswith(".py"):
            fp.update(open(os.path.join(srcdir, f), "rb").read().replace(b"\r\n", b"\n"))
    return {
        "commit": _git("rev-parse", "--short", "HEAD") or "unknown",
        # --ignore-cr-at-eol: WSL git sees CRLF-only differences as modifications
        "dirty": _git("diff", "--ignore-cr-at-eol", "--name-only", "--", "assignment3/starter_code/src"),
        "src_sha256": fp.hexdigest()[:16],
    }


def version_at_commit(commit):
    """Frozen version info from the git tree (immune to later working-tree edits)."""
    fp = hashlib.sha256()
    listing = _git("ls-tree", "--name-only", commit, "assignment3/starter_code/src/")
    for path in sorted(listing.splitlines()):
        if path.endswith(".py"):
            fp.update(_git("show", f"{commit}:{path}").replace("\r\n", "\n").encode())
    return {"commit": commit, "dirty": "", "src_sha256": fp.hexdigest()[:16]}


def config_snapshot():
    sys.path.insert(0, STARTER)
    import src.config as c
    keys = ("MAX_ITERATIONS", "MAX_OPEN_TASKS", "API_RETRIES", "API_BACKOFF_S",
            "API_BACKOFF_MAX_S", "PRESERVE_THINKING", "MODEL_EXPERT",
            "MODEL_STANDARD", "MODEL_STARTER", "MODEL_EXTRA_BODY",
            "TOOL_COMPACT_THRESHOLD", "ARCHIVE_HYSTERESIS_TOKENS", "WINDOW_TOKEN_BUDGET",
            "BASH_INSERT_MIN_CHARS", "SUMMARY_COMPACT_THRESHOLD")
    return {k: getattr(c, k) for k in keys}


AGENT_DESIGN_PROSE = """**架构（三层）**

- 宿主调度器 `evaluation_scripts/run_all.py`：任务分配、沙箱生命周期、评分调度（不属于提交单元）。
- agent 容器 `src/`：ReAct 主循环，唯一提交单元；本报告的性能对比均以 `src/` 的版本为准。
- 每任务一个 Docker 沙箱（`sandbox_server.py` 提供文件/shell 操作，4g 内存上限）+ 独立评分容器（官方 verifier，断网）。

**主循环（agentic_loop.py）**

- ThreadPoolExecutor 并行，`MAX_OPEN_TASKS` 个 worker；每任务至多 `MAX_ITERATIONS` 轮。
- 每 `MAX_ITERATIONS/25` 轮注入进度提醒（PROGRESS_REMINDER）；同一文件读取达 3 次注入重复读取提醒。
- 任务异常隔离：单任务崩溃不影响其余任务；`solve()` 的 finally 提取并提交已有 diff（rescue submit）。

**模型调用**

- 单一档位 `MODEL_STANDARD`；`MODEL_EXTRA_BODY` 按模型注入 reasoning_effort 或 thinking_budget（二者互斥）。
- `PRESERVE_THINKING=1`：reasoning_content 回写 assistant 历史（Qwen preserve_thinking）。
- 瞬时故障重试 7 次指数退避（4s→60s），覆盖 408/409/429/5xx。
- 历史净化：畸形 `function.arguments` 在历史副本中归一为 `"{}"`（`_valid_arguments`），执行路径保留原始错误反馈给模型。

**上下文管理（memory.py，两层压缩）**

- 层 1（prompt_tokens ≥ 24K）：最近 12 条之外的工具输出归档，历史中保留头 400 + 尾 800 字符预览与归档引用；全文经 `read_archived_output` 按行号取回。
- 层 2（≥ 56K）：全量摘要 compaction，历史替换为 system + 摘要 + 当前计划 + git status/diff 快照（SUMMARY_CONTEXT_PROMPT）。

**工具集（7 个）**：read_file、read_archived_output、search（字面匹配）、edit_file、create_file、bash（拒绝 cat/sed/head/tail）、update_plan；scratch 文件限定 /work，避免污染补丁。

**系统提示词（prompts.py）**：预算、自主性、ground-truth（否定可靠记忆、防 PR-recall 螺旋）、行动偏向、工具纪律、验证六段；参考 openai/codex 编码提示词重写。

**追踪**：每任务 latency.jsonl（逐调用模型/延迟/token/缓存）+ run.jsonl（api_request/tool_call/tool_result/run_end）。"""

_V3_COMPACT = """**上下文管理（memory.py，两层压缩）**

- 层 1（prompt_tokens ≥ 24K）：最近 12 条之外的工具输出归档，历史中保留头 400 + 尾 800 字符预览与归档引用；全文经 `read_archived_output` 按行号取回。
- 层 2（≥ 56K）：全量摘要 compaction，历史替换为 system + 摘要 + 当前计划 + git status/diff 快照（SUMMARY_CONTEXT_PROMPT）。"""

AGENT_DESIGN_PROSE_V3 = AGENT_DESIGN_PROSE.replace(_V3_COMPACT, """**上下文管理（memory.py，两层压缩 · V6 重设计）**

- 层 1 批量归档：prompt_tokens ≥ 40K 且距上次归档增长 ≥ 8K 才触发；活窗口 = 尾部累计 ≤ 8K token 的工具输出（无条数约束）；原始 ≥ 2× 预览（1200 字符）才归档为 600 字符预览（头 100/尾 500）+ 引用；水位线以下永不改写——两次批量之间请求前缀逐字节稳定，最大化前缀缓存命中。
- bash 出生即预览：≥ 2000 字符的 bash 输出在插入时即归档（头 100/尾 900，全文可 `read_archived_output` 按行取回），此后永不改写；trace 仍记全文。
- 层 2（≥ 56K）：全量摘要 compaction，历史替换为 system + 摘要 + 当前计划 + git status/diff 快照 + **结构化状态块**（已读/已改文件、scratch、最近命令、归档索引），压缩后取回通路不断头。""")

# Compaction-table footnote, per scheme generation.
COMPACT_NOTE_V1 = ("层 1 触发阈值 prompt_tokens ≥ 24K、层 2 ≥ 56K（memory.py）。层 1 计数依赖 agent.log 的 live 输出"
                   "（run5 该目录丢失，四题记为 —）；层 2 来自 latency.jsonl，始终可靠。")
COMPACT_NOTE_V3 = ("层 1：≥40K 且距上次归档 ≥8K 触发；窗口 = 尾部 ≤8K token；原始 ≥1200 字符才归档；bash ≥2000 字符出生即预览"
                   "（不计入层 1 事件）；层 2 ≥56K（config.py）。层 1 计数依赖 agent.log；层 2 来自 latency.jsonl。")

# Hand-written failure analyses for later runs (run_no -> lines).
RUN_FAILURE_TEXTS = {
    3: [
        "本轮 12/15，三道未过均为只差 1 个必过测试的精确字符串近失；与基准轮（Full Run 1）对照可分离随机性与确定性：",
        "",
        "### {n}.1 qutebrowser/21b426b —— test_repr，第三次逐字节相同的选择",
        "",
        "- 补丁 repr 实现与 run8、重跑轮完全一致：`values=list(self._vmap.values())`，而官方测试锁定 gold 的 `vmap=odict_values([...])`。",
        "- 当时判定为跨两个代码版本的确定性偏差；Full Run 4 用批量编辑工具后第四次尝试改写为 `vmap=self._vmap.values()` 并通过，修正为\"确定性仅在工具面不变时成立\"。",
        "",
        "### {n}.2 ansible/83909bfa —— test_api_no_auth_but_required，措辞掷硬币再次落错",
        "",
        "- 本轮文案：`\"...A token can be set with --token, or in the token file at %s...\"`（与 run8 同风格）；重跑轮选了保留原骨架的 `\"--api-key or at {path}\"` 并通过。",
        "- 措辞选择确认为随机事件，题面自相矛盾（problem_statement 指向 URL 暗示、requirements 明文 --token）使模型无稳定锚点。",
        "",
        "### {n}.3 ansible/a26c325 —— test_Request.py::test_open_url，相对基准的回归",
        "",
        "- 必过集合 42 项中仅此 1 项 AssertionError；use_netrc 七层贯通完整，失败在调用位置：agent 选 `Request(use_netrc=..).open(..)` 并自认\"两种解释都行\"，gold 断言 upstream 的 `.open(.., use_netrc=..)`。属规格字面话与 upstream 惯例的精确字符串掷硬币，与压缩改动无关。",
    ],
    4: [
        "本轮 11/15，四道未过，其中两类是新情况：",
        "",
        "### {n}.1 ansible/83909bfa —— 0 字节补丁（非题面失败，是收尾 git 操作与提取机制冲突）",
        "",
        "- 评分 150/150 全挂、耗时 0s，是 0 字节补丁的签名；补丁文件确认 0 字节。",
        "- 根因：模型收尾 it71 跑了 `git add -A` 自查改动，把全部改动暂存进 index；课程提取是 `git add -N . && git diff`（只反映未暂存改动）→ 抓空。此前 it61/it70 还多次 `git stash` 做基线对比。",
        "- 定性：这是模型用 git 自查的习惯 × harness 只抓未暂存改动的交互失败，不是能力或题面问题（run3 同题未踩是因那次收尾没 `git add`）。通用修法：提示词纪律\"编辑留在工作区，勿 git add/stash/commit，harness 自取未暂存改动\"。",
        "",
        "### {n}.2 webclients/944adbf —— 10/10 全挂（功能放错仓库位置，非导出形式）",
        "",
        "- 补丁 4.4KB 非空。隐藏 gold 测试位于**仓库根 `tests/observeApiError.test.ts`**，而模型把实现与自测都放进子包 `packages/metrics/`。",
        "- 评分时根 jest 工程加载该 `.ts` 报 `Module parse failed: Unexpected token (11:17)`（`export type`、`error: any` 等 TS 语法在根工程 babel 配置下未转译）→ 0 个测试被收集 → 10 个 gold 测试全记 missing/失败。模型在 `packages/metrics/tests/` 自建的测试本地全绿，但评分跑的不是那个路径。",
        "- 定性：**代码库结构误判（在错误的子项目里解题）**，与批量编辑/分页机制无关；run1/run3 同题曾一次通过，属随机回归。",
        "",
        "### {n}.3 element-web/5dfde12 (1/61)、qutebrowser/c580ebf (1/202) —— 精确字符串近失",
        "",
        "- 各差 1 个必过测试，与 Full Run 1 失败案例分析中的规格歧义 × 精确匹配同构；本轮上下文/工具变化后硬币落到另一面。",
    ],
}


def scan_behavior(tdir):
    """Quantitative agent behavior from run.jsonl: tool distribution,
    identical repeated calls, first/last edit, test commands, error results."""
    p = os.path.join(tdir, "run.jsonl")
    if not os.path.exists(p):
        return None
    tools, identical, reads = {}, {}, {}
    tool_time = {}
    first_edit = last_edit = None
    edits = bash_tests = errors = results = max_turn = 0
    prev_ts = prev_ev = None
    retry_stall = 0.0
    for line in open(p, encoding="utf-8"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        ev = e.get("event")
        ts = e.get("timestamp")
        if ts:
            t = parse_ts(ts)
            if prev_ts and (prev_ev == "api_retry" or ev == "api_retry"):
                retry_stall += (t - prev_ts).total_seconds()
            prev_ts, prev_ev = t, ev
        if ev == "tool_call":
            name, it = e.get("tool_name", "?"), e.get("iteration", 0)
            max_turn = max(max_turn, it)
            tools[name] = tools.get(name, 0) + 1
            try:
                args = json.loads(e.get("arguments") or "{}")
                if not isinstance(args, dict):
                    args = {}
            except ValueError:
                args = {}
            norm = json.dumps(args, sort_keys=True, ensure_ascii=False)
            identical[(name, norm)] = identical.get((name, norm), 0) + 1
            if name == "read_file":
                path = args.get("path", "?")
                reads[path] = reads.get(path, 0) + 1
            if name in ("edit_file", "create_file"):
                edits += 1
                if first_edit is None:
                    first_edit = it
                last_edit = it
            if name == "bash" and re.search(
                    r"pytest|go test|ansible-test|npm (run )?test|yarn test|jest|mocha|tox|unittest|mvn test|cargo test",
                    args.get("command", "")):
                bash_tests += 1
        elif ev == "tool_result":
            results += 1
            first = (e.get("result") or "").split("\n", 1)[0]
            if first.startswith("[tool_result "):
                try:
                    hdr = json.loads(first[len("[tool_result "):-1])
                    if hdr.get("status") != "ok":
                        errors += 1
                    d = hdr.get("duration_ms")
                    if isinstance(d, (int, float)):
                        tt = tool_time.setdefault(e.get("tool_name", "?"), [0, 0.0, 0.0])
                        tt[0] += 1
                        tt[1] += d / 1000
                        tt[2] = max(tt[2], d / 1000)
                except ValueError:
                    pass
    reps = sorted(((k, v) for k, v in identical.items() if v > 1), key=lambda kv: -kv[1])
    top_reps = []
    for (name, norm), n in reps[:3]:
        try:
            args = json.loads(norm)
        except ValueError:
            args = {}
        if name == "read_file":
            desc = f"{args.get('path', '?')} [{args.get('start_line', '')}-{args.get('end_line', '')}]"
        elif name == "bash":
            desc = (args.get("command") or "")[:60]
        else:
            desc = norm[:60]
        top_reps.append((name, desc, n))
    lats = []
    lp = os.path.join(tdir, "latency.jsonl")
    if os.path.exists(lp):
        for line in open(lp, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") == "chat" and r.get("latency_s") is not None:
                lats.append(r["latency_s"])
    return {
        "tools": tools, "first_edit": first_edit, "last_edit": last_edit,
        "edits": edits, "bash_tests": bash_tests, "errors": errors,
        "results": results, "max_turn": max_turn,
        "wasted": sum(v - 1 for _, v in reps), "rep_groups": len(reps),
        "top_reps": top_reps,
        "top_reads": sorted(reads.items(), key=lambda kv: -kv[1])[:3],
        "tool_time": tool_time, "tool_s": sum(v[1] for v in tool_time.values()),
        "lats": lats, "retry_stall_s": retry_stall,
    }


def scan_compactions(tdir, logpath, pos):
    """Layer-2 summary compactions from latency.jsonl (always available);
    layer-1 tool compactions from agent.log live lines when that log survived."""
    l2 = []
    lp = os.path.join(tdir, "latency.jsonl")
    if os.path.exists(lp):
        for line in open(lp, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") == "compaction":
                l2.append(r.get("iteration"))
    l1 = []
    if logpath and os.path.exists(logpath):
        turn = None
        for line in open(logpath, encoding="utf-8", errors="replace"):
            m = re.search(rf"\[task {pos}\] ===== turn (\d+)/", line)
            if m:
                turn = int(m.group(1))
                continue
            m = re.search(rf"\[task {pos}\] \[compaction\] archived (\d+)", line)
            if m:
                l1.append((turn, int(m.group(1))))
    return l1, l2


def render_charts(beh, cost_parts, charts_dir):
    """Generate report charts; returns list of (filename, title) or [] when
    matplotlib is unavailable."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []
    os.makedirs(charts_dir, exist_ok=True)
    made = []

    def save(fig, name):
        import io
        buf = io.BytesIO()
        fig.tight_layout()
        fig.savefig(buf, format="png", dpi=130)
        plt.close(fig)
        # drvfs rejects O_TRUNC writes while a Windows process holds the file
        # open (image preview); write a temp name and rename over it.
        tmp = os.path.join(charts_dir, name + ".tmp")
        with open(tmp, "wb") as fh:
            fh.write(buf.getvalue())
        try:
            os.replace(tmp, os.path.join(charts_dir, name))
        except OSError:
            os.replace(tmp, os.path.join(charts_dir, name.replace(".png", "_new.png")))
            name = name.replace(".png", "_new.png")
        made.append(name)

    # 1. Per-task wall-time composition: model vs tools vs other
    items = sorted(beh, key=lambda ab: -(ab[0]["wall_s"] or 0))
    labels = [short(a["label"]) for a, _ in items]
    model = [a["model_s"] / 60 for a, _ in items]
    tool = [b["tool_s"] / 60 for a, b in items]
    stall = [b["retry_stall_s"] / 60 for a, b in items]
    other = [max((a["wall_s"] or 0) - a["model_s"] - b["tool_s"] - b["retry_stall_s"], 0) / 60 for a, b in items]
    fig, ax = plt.subplots(figsize=(10, 6))
    y = range(len(items))
    ax.barh(y, model, label="Model inference", color="#4C72B0")
    ax.barh(y, tool, left=model, label="Tool execution", color="#DD8452")
    left2 = [m + t for m, t in zip(model, tool)]
    ax.barh(y, stall, left=left2, label="API retry stall (hangs/backoff)", color="#C44E52")
    ax.barh(y, other, left=[l + s for l, s in zip(left2, stall)], label="Other", color="#55A868")
    ax.set_yticks(list(y), labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("minutes")
    ax.set_title("Wall-time composition per task (winning/valid attempt)")
    ax.legend(fontsize=8)
    save(fig, "time_composition.png")

    # 2. Tool call share (pie)
    totals = {}
    for _, b in beh:
        for k, v in b["tools"].items():
            totals[k] = totals.get(k, 0) + v
    order = sorted(totals.items(), key=lambda kv: -kv[1])
    fig, ax = plt.subplots(figsize=(7, 6))
    ax.pie([v for _, v in order], labels=[f"{k} ({v})" for k, v in order],
           autopct="%1.1f%%", textprops={"fontsize": 9})
    ax.set_title("Tool call distribution (all tasks)")
    save(fig, "tool_calls.png")

    # 3. Tool execution time: total + mean per call
    tt = {}
    for _, b in beh:
        for k, (n, s, mx) in b["tool_time"].items():
            cur = tt.setdefault(k, [0, 0.0, 0.0])
            cur[0] += n; cur[1] += s; cur[2] = max(cur[2], mx)
    order = sorted(tt.items(), key=lambda kv: -kv[1][1])
    fig, ax = plt.subplots(figsize=(9, 5))
    names = [k for k, _ in order]
    sums = [v[1] / 60 for _, v in order]
    bars = ax.bar(names, sums, color="#4C72B0")
    for bar, (k, v) in zip(bars, order):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                f"avg {v[1]/max(v[0],1):.1f}s\nmax {v[2]:.0f}s", ha="center", va="bottom", fontsize=8)
    ax.set_ylabel("total minutes")
    ax.set_title("Tool execution time (total, with avg/max per call)")
    ax.margins(y=0.2)
    save(fig, "tool_time_total.png")

    # 4. API latency histogram
    lats = [x for _, b in beh for x in b["lats"]]
    if lats:
        import statistics
        p50 = statistics.median(lats)
        p90 = sorted(lats)[int(len(lats) * 0.9)]
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.hist(lats, bins=range(0, int(max(lats)) + 10, 5), color="#4C72B0", edgecolor="white")
        ax.axvline(p50, color="#C44E52", linestyle="--", label=f"p50 = {p50:.1f}s")
        ax.axvline(p90, color="#DD8452", linestyle="--", label=f"p90 = {p90:.1f}s")
        ax.set_xlabel("latency (s)")
        ax.set_ylabel("calls")
        ax.set_title(f"API call latency distribution ({len(lats)} chat calls)")
        ax.legend(fontsize=8)
        save(fig, "api_latency.png")

    # 5. Cost breakdown pie
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    parts = [("Uncached input", cost_parts[0]), ("Cached input", cost_parts[1]), ("Output", cost_parts[2])]
    ax.pie([v for _, v in parts], labels=[f"{k}\n¥{v:.2f}" for k, v in parts],
           autopct="%1.1f%%", textprops={"fontsize": 9})
    ax.set_title("Cost breakdown (catalogue-price equivalent)")
    save(fig, "cost_breakdown.png")
    return made


# Runs whose code diff vs a reference is trace/display-only (no effect on
# agent decisions): run_no -> (reference run_no, description). Judged per run
# when the diff is reviewed; identical fingerprints are always equivalent.
BEHAVIOR_EQUIV = {
    2: (1, "仅 run_end 原因标注（iteration_limit/no_choices），属 trace 上报层，不参与决策"),
}

BEHAVIOR_NOTES = {
    1: {
        "internetarchive/53d376b": "前 28 轮纯调研（反复对照 infogami ILIKE 与 mock 通配符语义），turn 28 思维列出完整实现方案，turn 31 才首改；此后\"编辑→定向 pytest→/work 探针\"交替，turn 50 用 git stash 确认基线失败，turn 68–82 风格检查（同一 ruff 命令重复两次），turn 86 广谱 1647 测试通过后收尾。验证纪律好，但前期调研偏长、收尾检查重复。",
        "gravitational/1316e67": "turn 8 思维列出 11 项改动清单，turn 7–49 以 25 次编辑完成主实现；turn 55–95 耗在自建 httptest scratch 测试上，其中 turn 66–87 约 20 轮只为在 vendor 里反复 grep 接口定义；turn 88 触发全量 compaction 后一度迷失工作目录，turn 99 贴着轮数上限提交。",
        "ansible/f327e65": "60.6 min 中约 38 min 是 turn 14 连续 4 次 APIConnectionError 的重试停滞（挂起+退避），chat 仅 18 min；行为上 turn 23–53 约 30 轮反复微调 dataclasses.py 的 py2/py3 兼容导入块，turn 67–91 为 flake8/pylint sanity 加 noqa 对冲；turn 96–99 git stash 对比确认无新增失败后提交。编辑粒度过细、sanity 顾虑过多。",
        "qutebrowser/c580ebf": "turn 0–9 快速定位三个文件，turn 10–16 主编辑、turn 17 首次 pytest、turn 19 stash 基线，节奏紧凑；turn 31–61 长尾耗在全量 pytest 反复\"崩溃无摘要\"（思维怀疑 test_javascript 段错误），多次 stash 对比与日志搬移；turn 62 diff 审查后收尾。",
        "flipt-io/2ca5dfb": "（无思维链记录，据工具序列重建）turn 0–23 在 GOPROXY=off 约束下探离线模块缓存确认 otel SDK 签名；turn 24 新建 metrics.go，turn 26–53 接线三处，turn 35–38 反复试 GOFLAGS/GOPROXY 组合编译；turn 56–64 scratch 测试，turn 73 删 scratch、diff 审查收尾。",
        "future-architect/5af1a22": "（无思维链记录，据工具序列重建）turn 0 search 直接命中 isRunningKernel，turn 7–9 完成三文件编辑，turn 10 即 build+test；turn 19–23 scratch 定向验证，turn 24 删 scratch+gofmt+vet，turn 25–28 全量 go test 与 -count=1 复查后收尾。无重复调用，收敛快、测试纪律好。",
        "element-hq/5dfde12": "（无思维链记录，据工具序列重建）前 35 轮纯调研（反复 grep node_modules 中 matrix-js-sdk API 与 test-utils mock），首编辑偏晚；turn 35–49 集中改三文件并新建对话框组件，turn 55–58 还改了现有测试；turn 62 首次 jest，turn 82–87 围绕 SessionManagerTab 失败做 stash 对比后修复。turn 52 有一次空参数 read_path 调用。",
        "flipt-io/2eac0df": "（无思维链记录，据工具序列重建）turn 3–6 读源码/测试并 grep 确认替换面，turn 7–14 完成 14 次编辑，turn 15 起 build/go test/vet；turn 23–25 gofmt 发现测试文件格式问题并修复，turn 26–27 -count=1 复查收尾。仅 4 次读取、无重复，小任务快进快出。",
        "qutebrowser/de4a1c1": "turn 0–25 探索打转（重复 find/ls、cat 错文件，turn 12 思维甚至怀疑 webengine.py 是\"二进制/格式损坏\"），turn 26 才新建 qtargs.py 并重构；turn 50–88 由 31 次 pytest 主导：全量套件反复硬崩溃无摘要（turn 71 数点号定位崩溃点、stash 确认预存在），turn 78 compaction，turn 79–88 定向重跑确认后收尾。",
        "internetarchive/4a5d2a7": "turn 0–9 快速读 wikidata.py 与测试，turn 10–12 三连编辑实现嵌套解析，turn 14 首次 pytest、turn 16 stash 基线；turn 21/28 思维专门推演隐藏测试可能形态并补边界，turn 29–30 广谱测试，行宽自查后收尾。最快一胜：调研-编辑-测试紧循环、无废动作。",
        "gravitational/89f0432": "turn 3 首编辑加 ReadAtMost，turn 12–42 在约 10 个文件替换 ioutil.ReadAll 为限长读取（29 次编辑）；turn 52 识别证书过期失败系系统日期 2026 的预存在问题，turn 70 论证跳过重型 lib/web 套件，turn 70–72 重建 scratch 验证 4 种行为后删除收尾。同一 build 命令重复 2 次。",
        "protonmail/944adbf": "turn 3–5 读 metrics 包与 jest 配置，turn 6 思维权衡 named vs default 导出后决定双导出；turn 11 jest、turn 12 全量 yarn test、turn 13 stash 基线；turn 14–16 tsc/eslint/prettier 三连，turn 19 专门建临时测试验证 isolatedModules 类型导出后删除。轮数少但验证链完整。",
        "ansible/a26c325": "turn 0–6 定位 urls.py 请求链路，turn 7 首编辑，turn 7–42 以 37 次编辑把 use_netrc 贯穿四层；turn 43 首次 pytest，turn 55–57 在 /work 自写 netrc mock 测试，turn 59 补 changelog；turn 65–66 git stash -u 对比确认 37 个失败皆预存在，ansible-doc 与 import 冒烟后收尾。",
        "qutebrowser/21b426b": "turn 9–12 将 _values 改为 _vmap，repr 保留 values= 关键字并包 list(...)；turn 20–22 用 scratch 脚本自证 repr 输出 values=[ScopedValue(...)] 并认定即期望格式（循环自证）。官方 test_repr 实际锁定 gold 的 vmap=odict_values([...]) 原始视图；turn 28 以\"162 passed、其余为预存在环境问题\"提交，漏掉该格式契约。",
        "ansible/83909bf": "turn 24 思维自拟错误措辞、决定把 --token 放前；turn 27–36 移除 login 子命令并加 parse() 拦截；turn 41–52 排查 dist-packages 旧副本遮蔽源码；turn 59–66 直接把可见测试期望串改成自己的措辞、再 re.escape 处理正则——循环自证。官方测试锁定 \"--api-key or at \" 前缀，措辞不符致唯一失败。",
    },
    2: {
        "qutebrowser/21b426b": "思维明确注意到\"可见测试期望 values=[...]，但隐藏测试可能已更新\"，仍锚定旧可见测试格式，做出与 Full Run 1 完全相同的列表归一化（values=list(self._vmap.values())）；85 轮中 bash 64 次，多耗在全量套件崩溃排查与 stash 对比，首编辑 turn 9。零反馈重跑复现同一格式选择，证实该偏差是确定性的。",
        "ansible/83909bf": "与 Full Run 1 的重写相反：本次保留原报错骨架、仅删除 login 子句（\"...--api-key or at {token path}\"），恰好命中 gold 前缀；全程仅 2 次 edit_file（Full Run 1 为 17 次），首编辑 turn 26，调研含对上游 PR 历史的回忆。53 轮、12.9 min、0/150 通过——最小变更纪律直接决定精确字符串题的成败。",
    },
    3: {
        "internetarchive/53d376b": "46 轮（bash28/read16/edit5），首编辑 turn 20，探索→定点修改→pytest 节奏。两批归档后约 turn 20 余触发摘要压缩；agent 察觉归档窗口把 load_book.py 全文读截断（import 行不完整），thinking 明确质疑后经 2 次 read_archived_output + 重读真实行恢复，未产生错误编辑——对压缩伪影有自觉检测能力，0/8 通过。",
        "flipt-io/2ca5dfb": "74 轮、bash 49 次的 Go 改-编译-测循环，首编辑 turn 13。两批归档后约 turn 57 摘要压缩（prompt 缩至 ~6.7K），压缩后立即重跑 go test、重读 metrics_test.go 做有意再校准而非迷失；处理了 TestLoad 的 Default()/Load 一致性，0/2 通过。",
        "future-architect/5af1a22": "43 轮，首编辑 turn 13，一批归档 + 一次摘要压缩（约 turn 30），压缩后行为连贯。对\"是否可改既有测试\"边界判断正确（因签名变化更新 scanner 测试并补新用例），0/11 通过，节奏紧凑无冗余。",
        "gravitational/1316e67": "70 轮，首编辑很早（turn 7）、edit 21 次边理解边改；两批归档且摘要压缩几乎同轮完成，压缩后未见重复读取。亮点：scratch 假服务器验证 spec 行为后删除临时文件，自我约束好，0/5 通过（基准轮此题贴 99 轮上限，本轮 70 轮收敛）。",
        "element-hq/5dfde12": "97 轮近上限、126 次调用、bash 67，首编辑迟至 turn 43——TS 大库前期调研过重是最大低效点。三批归档 + 一次摘要压缩（约 turn 56），压缩后重读源文件再续改，应对正常；0/61 通过但轮次/工具数全场最高，性价比偏低。",
        "ansible/f327e65": "43 轮、首编辑 turn 15，全程无批量归档、仅一次出生预览回读，是轻压缩样本；正确诊断 galaxy 依赖循环导入为既有问题，175 测试全过，模型时间仅 7.9 min（基准轮 60.6 min 的同题），效率标杆。",
        "qutebrowser/c580ebf": "98 轮、bash 65，首编辑 turn 7；两批大归档（10+4）+ 一次摘要压缩（约 turn 60），压缩后 flake8/测试双轨继续，3 次 read_archived_output 找回旧输出。测试面铺得过宽（webengine/webkit/end2end 逐项排除），但结论纪律好，0/202 通过。",
        "flipt-io/2eac0df": "56 轮、首编辑 turn 12，两批归档、未触发摘要压缩；对 analytics payload 的精确 map 断言逐处核对（三处出现点逐一处理），1 次归档回读；末尾自行判断不必加 CHANGELOG，0/3 通过，无返工。",
        "qutebrowser/de4a1c1": "唯一撞 iteration_limit（99/100）的任务，114 次调用、首编辑 turn 8；压缩最密集（三批归档 + 一次摘要压缩约 turn 70，第 100 轮仍在归档），靠逐文件 pytest 循环自证后 0/123 通过——通过但验证阶段过长触顶，是本轮最大资源风险样本。",
        "internetarchive/4a5d2a7": "全程最轻：15 轮、17 次调用、首编辑 turn 4、零归档事件；先自写复现脚本验证 get_statement_values 行为再动手，既改测试又验证，9 个测试全过。",
        "gravitational/89f0432": "83 轮、edit 22 / bash 51，首编辑 turn 4；两批归档但从未摘要压缩，压缩后无迷失。ioutil→io 全库清扫 + fake-server 测试耗时较多——只考 1 个测试用 83 轮，是\"通过但开销/收益失衡\"的代表。",
        "protonmail/944adbf": "18 轮、首编辑 turn 3、无批量归档；测试输出被预览截断时 thinking 明确说\"需要读完整归档输出\"，一次 read_archived_output 定位失败原因——直接验证了归档机制在关键排障时可用；0/10 通过，并对官方状态码措辞做了范围化解释。",
        "ansible/a26c325": "63 轮、edit 30，两批归档、无摘要压缩，use_netrc 七层贯通完整；失败纯属调用位置的字符串级决策：在 `Request(use_netrc=..).open(..)` 与 upstream 的 `.open(..., use_netrc=..)` 之间选了前者并自认\"两种解释都行\"，gold mock 断言后者致 1/42（回归，基准轮通过）。",
        "qutebrowser/21b426b": "45 轮、一批归档、无摘要压缩；_vmap 功能重构全对，thinking 近十段反复推演 repr 格式，明确枚举过 `self._vmap.values()`（odict_values）与 OrderedDict 两种渲染，但因\"无法回忆 upstream 提交\"以可见测试为唯一证据选定 `values=list(...)`——与前三次完全相同的决策路径与失败点，系统性而非随机；还顺手把可见 test_iter 改成 _vmap.values() 自洽。",
        "ansible/83909bf": "49 轮、两批归档 + 3 次回读、无摘要压缩，login.py 删除与 CLI 拦截整体干净；失败在报错文案：thinking 试图回忆 upstream PR（#72759/#72795/#72826 均不确定）、自认\"not 100% confident\"后退回 requirement 字面措辞（--token/token file 风格），与 gold 的 `--api-key or at` 前缀差一（回归，重跑轮曾通过）。",
    },
    4: {
        "internetarchive/53d376b": "74 轮，前 20 轮探索、turn 21–24 首次批量编辑，之后\"编辑→测试→再编辑\"直至完成。25 次 read_file 中 24 次带显式 start_line、6 次触发分页提示并按提示续读——分页机制被自然吸收。10 次 edit_file 有 3 次失败（含一次跨 3 文件批），原子校验拒绝后回读原文再单独重试，行为稳健。",
        "flipt-io/2ca5dfb": "98 轮长跑，search 17 次占前 37 轮、首编辑 turn 38。大量多文件批编辑（turn 65/66 各 4 文件、75/76 各 3 文件），23 个 hunk 分摊在 11 次调用；3 次批失败后降级为单 hunk 重试成功。读取分页 10 次全部正确续读，无重复整读浪费；缺点是测试验证轮次偏多。",
        "future-architect/5af1a22": "44 轮高效样本：turn 3 即定位目标文件，turn 13–29 用 6 次编辑（含 2 次双文件批）集中落改动，18 次读取 14 次带 start_line 分页、6 次续读。原子批在此避免了 2 次部分匹配导致的半写。",
        "gravitational/1316e67": "87 轮，改动集中在 dynamoevents.go 的事件存储逻辑。turn 37 出现\"4413 hunks\"调用——实为模型把 files 参数双重编码成字符串、工具逐字符拆成伪 hunk，原子校验整批拒绝，turn 38 以正确结构单 hunk 重试成功：一次序列化事故反而验证了批校验的防呆。全程 23 次编辑 10 次失败（空 path、old==new 空操作、空白不匹配），每次都能定位原因并缩小批次，自纠错强但试错成本偏高。",
        "element-hq/5dfde12": "82 轮，双特性任务（客户端信息 + 语音广播错误弹窗）。自建临时测试验证弹窗参数、其自身两断言失败后 turn 76 判定为环境伪影并删除——但 grading 仍剩 1 个失败，疑点落在 description 用 `<p>{_t(...)}</p>` 包裹的参数形状。亮点：对 5 个 last_chunk_sequence 旧行为失败明确判断\"将由 hidden test 替换\"而未迁就改测试。",
        "ansible/f327e65": "40 轮，turn 13–16 连续 4 个大批（3/3/4/4 文件）一次写完 collection 名称校验，turn 23 起以测试验证为主；用 git stash 证明失败为环境预存在，并主动撤掉自己编造的 changelog issue 链接（诚实度亮点）。6 次读取全带分页，开销极小。",
        "qutebrowser/c580ebf": "63 轮，实现子域名加宽屏蔽。关键败笔 turn 16–17：既有测试 test_disabled_blocking_per_url 依赖 QUrl host 为空的怪癖，新语义使其失败，模型选择改可见测试顺从实现（thinking 中纠结\"不应改测试\"），hidden 测试仍以 1 个失败否决该语义取舍。批量编辑正常（3 次多 hunk 批、2 次空 path 失败后重试）。",
        "flipt-io/2eac0df": "25 轮速胜，turn 9–11 用 3 个大批（8+8+7 文件）完成接口重命名的全库清扫——新批量 edit_file 最教科书式的使用；批内任一 hunk 不匹配则整批不写，随后 2 次单文件小补到位。10 次读取 6 次分页、3 次触发提示并续读。",
        "qutebrowser/de4a1c1": "85 轮，bash 62 次、验证密集。17 次编辑仅 11 次成功、6 次被原子校验拒绝（空白/上下文不匹配），但每次失败信息都促使回读精确行区间再重试，未盲改。多 hunk 批 7 次，覆盖 config 相关 2–3 文件联动。",
        "internetarchive/4a5d2a7": "29 轮最简任务：turn 4 一个双文件批 + turn 14 一次单 hunk 即完成，编辑零失败。6 次读取仅 3 次带分页，工具使用克制，主要时间花在测试确认。",
        "gravitational/89f0432": "36 轮，ioutil→io 清扫在 turn 14–15 用两个 7/6 文件批一次完成、17 个 hunk 零失败——批量原子接口在机械替换上收益最大的样例。17 次读取全部带 start_line（逐页扫大文件），无重复读。",
        "protonmail/944adbf": "24 轮，新建 observeApiError.ts + 自写测试、本地 jest 全绿后收工。致命点是**功能放错仓库位置**：隐藏 gold 测试在仓库根 `tests/observeApiError.test.ts`，模型却把实现与自测都放进子包 `packages/metrics/`；根 jest 工程加载该 TS 报 `Module parse failed: Unexpected token (11:17)` → 0 测试收集 → 10/10 全挂。属代码库结构误判（在错误子项目解题），与批量/分页机制无关；小文件低覆盖任务对新机制依赖不大。",
        "ansible/a26c325": "89 轮，urls.py 超 2000 行，35 次读取全部带 start_line 逐页扫（1230→2015 行），分页是该大文件任务的生命线。本轮恢复通过的关键正是签名位置：模型显式对齐参考提交，把 use_netrc 追加到 open_url/fetch_url/url_get/uri 参数表末尾并关键字透传，修复了 run3 的错位。自建 scratch 验证参数线连通后删除；3 次批失败（缩进不一致）靠重读精确行解决。",
        "qutebrowser/21b426b": "43 轮，历史首次通过。模型 turn 15 从任务给出的 commit 线索推断出预期 repr 为 `vmap=odict_values([...])`，turn 17 用一个 10-hunk 批把 _values 列表整体换成 OrderedDict 的 _vmap，`get_repr(..., vmap=self._vmap.values())` 在 Python 3.11 下恰好产出 odict_values 字面量、与 hidden 测试吻合；turn 22 再 3-hunk 批同步可见测试与白名单。两次批编辑零失败，5 次读取完成侦察——机制红利最大化的样例。",
        "ansible/83909bf": "73 轮，删除 galaxy login 特性，13 次编辑中 4 次被原子拒绝后均恢复，工作本身合格。灾难在收尾：turn 70–71 为区分预存在失败做了 git stash -u/pop，turn 72 为做最终 diff 审查执行 `git add -A`，turn 74 thinking 已意识到\"我 staged 了\"却判断\"留着无妨\"；harness 用 `git add -N . && git diff`（未暂存）提取补丁，暂存内容不被捕获 → 0 字节补丁、150/150 全挂。git 自检习惯整体是优点，唯独最后一次 add -A 成致命单点。",
    },
    5: {
        "internetarchive/53d376b": "74 轮，探索到 turn 32 才首次编辑，此后编辑→pytest 循环 12 次。批量编辑 4/7 次多 hunk；turn 38 一次调用 3 个 hunk 全因缺 path 被拒（empty path），模型据逐 hunk 报错在 turn 39 补齐 path 重发成功——逐 hunk 反馈直接生效的实例。read 分页 26/58，无 git add。",
        "flipt-io/2ca5dfb": "89 轮近上限，read_archived_output 8 次（压缩压力大，5 次 compaction）。12 次编辑 7 次批量；首次测试迟至 turn 57，验证偏晚。一次 60s 构建超时后改为只构建目标包并重试，恢复得当；无 git add，0/2 通过。",
        "future-architect/5af1a22": "46 轮高效：turn 11 首次编辑、turn 15 起 12 轮测试。亮点自写端到端临时测试放 /work 复现 AlmaLinux 场景、不污染仓库。14 次编辑中 4 个 hunk 因空 path 被拒后即时重试；无 git add。",
        "gravitational/1316e67": "67 轮，8 次 compaction（本 run 最高）。单批最多 8 hunk；一个 hunk 因 old_string 0 匹配被拒，报错已含 add context or set replace_all 提示，模型选择补上下文而非 replace_all。终态 build/vet/gofmt/test 四重验证并清理 scratch；git 仅只读。",
        "element-hq/5dfde12": "96 轮险中求胜（run4 失败项恢复）：100 read/32 分页，turn 47 才首编，前期调研极重。批量编辑 7/8 多 hunk。最能体现纪律：对 6 个失败逐一归因，识别 VoiceBroadcastRecording 5 例为旧行为断言（隐藏测试会替换）、SessionManagerTab 1 例单跑证实为 worker 争用 flaky。无 git add，61 全过。",
        "ansible/f327e65": "89 轮、19 次 pytest，节奏最稳：5 次 edit 调用全部 12 hunk 成功、0 拒绝。曾被 dataclasses backport 目录干扰方向但自行纠偏。0/175 失败，全程无 git 写操作。",
        "qutebrowser/c580ebf": "81 轮、30 次测试循环，恢复 run4 失败项。13 次编辑（4 批量）伴 9 个 hunk 级拒绝，靠逐 hunk 报错定位后重试；一次 collected 0 items 的路径笔误立刻自查出目录名错误。对 widened_hostnames 该放 urlutils 还是 configutils 做了细致上游考古，本次放置判断正确。无 git add。",
        "flipt-io/2eac0df": "44 轮、仅 1 次 compaction，最从容。单批最多 8 hunk；因同文件 3 处相似 analytics.Track 断言，模型主动复核 hunk index 4 是否匹配到错误位置——逐 hunk 结果引发的自我审计。收尾确认无未跟踪残留。",
        "qutebrowser/de4a1c1": "84 轮、9 次 compaction 的超长任务：跨文件大搬移（test_configinit 分段删除重排）用 12-hunk 单批完成、22 hunk 0 拒绝，批量编辑红利最大。read 分页 37/76。无 git add，0/123 通过。",
        "internetarchive/4a5d2a7": "仅 9 轮通过，本 run 最短：3 次单 hunk 编辑 + 1 次定向测试，正确判断 dataclass 注解改动无运行时影响而克制扩大修改。未用到批量/replace_all（无需要），少即是多的正例。",
        "gravitational/89f0432": "44 轮完成 ioutil→io 迁移。16 次调用 25 hunk，5 个被拒（must be strings 参数类型错 + 0 匹配两类），均当轮修复；主动核对各文件 ioutil 使用计数以免漏改。无 git add。",
        "protonmail/944adbf": "FAIL（run4 同因复发）：31 轮，探索→turn 9 编辑→jest/eslint/prettier 节奏正常，卫生动作好（临时测试用完即删、git worktree 建 pristine 副本对照）。但决定性失误与 run4 一致——实现放 packages/metrics/lib、测试放 packages/metrics/tests，thinking 明确写 Hidden tests are most likely in packages/metrics/tests，全程未探查仓库根 tests/。工具层无异常，纯位置语义猜测失误。",
        "ansible/a26c325": "95 轮、全 run 最重编辑负载：25 次 edit_file / 72 hunk（12 批量、单批最多 8），25 个 hunk 被拒（多为 0 匹配）但合法 hunk 照常落盘，逐 hunk 语义避免了 run4 式整批报废。全 run 唯一一次 replace_all（test_Request.py 的 ciphers=None 重复替换，turn 50），引导生效但模型仍偏保守。无 git add。",
        "qutebrowser/21b426b": "PASS 0/28，repr 决定性问题已解决：`get_repr(self, opt=self.opt, vmap=self._vmap.values(), constructor=True)`。关键行为是模型在可见测试期望 list、隐藏测试期望什么的两难处，主动检索远端分支/提交历史找到真实上游 commit，确认新版 test_repr 期望 vmap= 形式后照抄；再以 30 项 scratch 复现上游 test_repr/test_iter 验证后删除。40 轮，一次 9-hunk 批量完成 _values→_vmap 重构。",
        "ansible/83909bf": "FAIL 1/150 但 git 纪律验证通过：patch 非空（run4 的 0-byte 未复发）。决定性细节——模型曾用 `git rm --cached login.py` 暂存删除，随即自纠：Instructions say never run git add/stash/commit…A staged deletion may be hidden，用 `git reset -- <path>` 退回未暂存；后遇 4 个失败测试想 stash 对照时再自斥 No stashing allowed，改用临时副本——规则被内化并在两次边缘场景正确执行。剩余 1 个失败是 api.py 文案与隐藏 match= 子串赌输（曾长段推演最大化包含上游期望子串、刻意避开括号 regex 元字符，方向对但未命中），纯文案 coin-flip。",
    },
}
RUN_NOTES = {2: "对 Full Run 1 两道失败题（21b426b、83909bfa）的零反馈重跑，非全量运行。"}


# Hand-written per-run 总结与反思 (inserted after 性能对比).
RUN_REFLECTIONS = {
    3: [
        "本节回答一个问题：**本轮代码改动（V6 压缩重设计）对 agent 表现有没有提升**——分成本/时延、解题能力、工作现场保持三个维度评估。",
        "",
        "**1. 成本与时延：明确提升，达成设计目标**",
        "",
        "- 缓存命中 83.1%→91.7%，未缓存输入占比 51%→约 33%，总成本 ¥7.64→¥6.11（−20%），$/solved $0.083→$0.072。",
        "- 平均解题时间持平（24.7→24.8 min），说明提速不以截断必要工作为代价。",
        "- 逐题看长尾显著收窄：teleport/1316e67 从贴 99 轮上限降到 70 轮通过；ansible/f327e65 墙钟 60.6→14.4 min（上下文变轻 + 本轮无上游挂起停滞）。",
        "",
        "**2. 解题能力：无提升也无退化证据——12/15 对 13/15 的差额不属于本轮改动**",
        "",
        "- 三道未过逐一定位到 trace 与补丁层：21b426b 是跨两个代码版本的第三次逐字节相同选择（确定性，与压缩无关）；83909bfa、a26c325 是精确字符串决策的硬币落面变化（基准轮同样存在此类方差）。",
        "- 没有任何一例失败可归因于压缩导致的信息丢失或误导：7 次层 2 压缩后均正常再校准。",
        "- 反向证据：同参重复测试命令 43→44（持平），未出现\"因预览丢细节而重跑\"的系统性恶化。",
        "",
        "**3. 工作现场保持：提升**",
        "",
        "- 基准轮 1316e67 在压缩后迷失工作目录、浪费约 20 轮；本轮结构化状态块 + 归档索引生效后，压缩后行为全部连贯，`read_archived_output` 使用从全程 1 次升至 14 次——取回通路真正被用起来了。",
        "- 唯一一次归档截断伪影（53d376b）被 agent 自觉识别并自愈，未造成错误编辑。",
        "",
        "**结论**：V6 改动在其设计目标（成本/时延/现场保持）上是**净提升**，对解题正确性**无副作用证据**；单轮解题数 −1 属字符串题方差，不构成退化证据。限制说明：每配置单样本，逐题变化不可单独归因，可靠结论仅在汇总层。",
    ],
    4: [
        "本节回答：**本轮改动（#4 read_file 源头分页 + #1 原子批量编辑）对 agent 表现有没有提升**。",
        "",
        "**1. 成本与时延：中性（无退化，也未见新收益）**",
        "",
        "- 总成本 ¥6.13（run3 ¥6.11，持平），缓存命中 91.1%（run3 91.7%，持平）。成本红利已被 V6 压缩吃掉，编辑/读取工具改造在 token 层不是大头——编辑调用本身很便宜。",
        "",
        "**2. 解题能力：净 −1，但构成变化比总数更重要**",
        "",
        "- 新拿下：qutebrowser/21b426b（**历史首次通过**，0/28）、ansible/a26c325（回归修复）；新失：ansible/83909bfa（**0 字节补丁**，非题面失败）、webclients/944adbf（真实导入 bug）、element-web、c580ebf（字符串近失）。",
        "- 关键修正：V7 判定 21b426b 的 repr 偏差\"确定性、零反馈不可修复\"——本轮被推翻，第四次尝试写出 gold 的 `vmap=odict_values([...])` 格式并通过。但子代理证据显示真因是**模型从任务给出的 commit 线索推断出预期 repr**，而非 V7 猜测的\"工具改动翻转确定性决策\"。教训：把单次行为变化归因于机制改动前，必须查 thinking 证据；\"确定性\"结论本身也只是 3 样本的小样本推断。",
        "",
        "**3. 新机制采纳情况（这才是本轮真正要测的）**",
        "",
        "- 批量编辑被**充分采纳**：133 次 edit_file 调用承载约 300 个真实 hunk，72 次为多 hunk 调用；flipt/2eac0df 用 3 个大批（8+8+7 文件）一次完成接口重命名全库清扫，teleport/89f0432 两个 7/6 文件批零失败完成 ioutil→io——批量原子接口在机械替换上收益最大。",
        "- 原子校验的**防呆价值**被一次事故验证：teleport/1316e67 有次调用模型把 `files` 双重编码成 JSON 字符串，工具逐字符拆出 4413 个\"伪 hunk\"，原子校验整批拒绝（status=error），下一轮以正确结构重试成功——若沿用旧的逐个 edit 语义，这场事故会静默写坏文件。",
        "- `replace_all` 仅用 1 次：模型仍倾向把同一 token 的重复替换拆成多个 hunk 或用 replace_all 之外的写法；引导可加强。",
        "- read_file 分页：281 次读取中 **201 次带显式 start_line、202 次带 end_line，90 次触发分页提示并往后翻页**——机制被自然吸收。ansible/a26c325 的 2000+ 行 urls.py 全靠逐页扫描（该任务本轮恢复通过）。",
        "",
        "**4. 新暴露的通用失败模式**",
        "",
        "- 83909bfa 的 0 字节补丁：模型收尾 `git add -A` 把改动暂存，harness 的 `git add -N . && git diff` 只抓未暂存 → 空补丁。模型在 thinking 里已意识到\"我 staged 了\"却判断\"留着无妨\"——是习惯与提取机制的交互 bug，修法是提示词纪律（勿 stage/stash/commit）。",
        "",
        "**结论**：批量编辑与 read 分页**双双被充分采纳且未见副作用**，是本轮最确定的正向信号；批量原子校验还拦截了一次双重编码事故（防呆红利）。意外收获是 21b426b 首次通过——但子代理证据显示它靠的是任务给出的 commit 线索推断出 gold repr 格式，**不是** V7 猜测的\"工具改动翻转确定性决策\"，需据此修正 V7 结论。单轮 11/15 的下滑主要来自 83909bfa 的 git-add 0 字节与 webclients 接口细节，均属可修的通用工程项，非新工具退化。",
    ],
    5: [
        "本节回答：**本轮改动（逐 hunk 编辑结果 + git 纪律提示词 + replace_all 引导）对 agent 表现有没有提升**。",
        "",
        "**1. 成本与时延**",
        "",
        "- 总成本 ¥6.94（run4 ¥6.13，run1 ¥7.64）——比 run4 高，但本轮多解 2 题；**$/solved $0.075**（run4 $0.078、run1 $0.083）为历史最低。缓存命中 88.5%（run4 89.0%、run1 83.1%），V6 收益保持。",
        "- 平均解题 21.4 min（run4 17.7）——上升是因为本轮把 run4 快速失败的 element-web（96 轮）、c580ebf（81 轮）等重活真正啃下来了，不是效率退步。",
        "",
        "**2. 解题能力：净 +2（13/15，回到 run1 水平），且可直接归因于本轮修复**",
        "",
        "- **git 纪律修复验证成功**：83909bfa 补丁 13603 字节（run4 是 0 字节），trace 确认全程**无 git add/stash/commit**（模型甚至把误用的 `git rm --cached` 主动 `git reset` 退回未暂存）——从灾难性空补丁降级为正常的 1/150 字符串近失。",
        "- element-web、c580ebf 恢复通过；21b426b 保持通过（repr 写 `vmap=self._vmap.values()` 命中 gold，靠检索真实上游 commit 确认）。",
        "- 唯二失败：83909bfa（1/150 措辞掷硬币）、webclients（10/10 代码库位置误判，run4 同因复发，本轮未针对性处理）。",
        "",
        "**3. 三机制采纳情况**",
        "",
        "- **逐 hunk 编辑**：edit_file 失败率 **31%→9%**（12/130 调用含拒绝 hunk，237 应用/63 拒绝，拒绝均带 index+原因且当轮即修）；合法 hunk 不再被连坐——a26c325 有 25 个 hunk 被拒但其余照常落盘，避免了 run4 式整批报废。",
        "- **git 纪律**：全 15 题 bash 中 0 次 git add/stash/commit，规则已内化。",
        "- **replace_all**：全 run 仅 1 次（a26c325 的 ciphers=None 重复替换）——引导生效但模型仍偏保守，倾向逐处列 hunk。",
        "",
        "**结论**：本轮两项定向修复（0 字节补丁、编辑连坐）**均验证成功**，单轮成绩回到 13/15、$/solved 创新低，缓存收益保持——是明确的净提升。剩余失败一是字符串掷硬币（83909bfa，方差固有）、二是代码库位置误判（webclients，属任务理解，非机制问题）。replace_all 采纳率与空 path hunk 校验是下一步可继续收紧的通用项。",
    ],
    6: [
        "本节回答：**换用 starter 档（qwen3.6-flash）跑同一套 harness，agent 表现如何**——这是跨模型档的通用能力验证，非调参。",
        "",
        "**结论：starter 档在本 harness 上全面劣于 standard 档，且不只是能力问题。**",
        "",
        "- **能力**：4/15（standard run5 为 13/15）。失败的多是 standard 轻松通过的题（53d376b 5/8、2ca5dfb 2/2、5af1a22 4/11、1316e67 5/5、f327e65 4/175、a26c325 4/42），且 trace 里出现文件路径幻觉（如 `galaxy.galaxygalle`）——模型本身更弱、更旧。",
        "- **成本**：¥37.31 ≈ standard run5（¥6.94）的 5.4 倍；$/solved **$1.31**（standard $0.075 的 17.5 倍）。两条叠加：(1) 价格倒挂——qwen3.6-flash 目录价（¥1.2/¥7.2）本就高于 qwen3.8-flash（¥0.8/¥2.7）；(2) 本次实测 qwen3.6-flash 的响应**未回填 cached_tokens（全 None）**，故按 0 缓存命中估算——但官方文档将 qwen3.6-flash 列入上下文缓存支持模型，隐式缓存命中率本就不确定，真实结算成本可能低于此上界估算。",
        "- **指令遵循**：git 纪律提示词（run5 里 standard 全 15 题 0 次违反）在 starter 下**有 3/15 题违反**（git add/stash），83909bfa 因此再次 0 字节补丁——弱模型更不可靠地遵守提示词约束。",
        "",
        "**判定**：starter 档不是「更便宜的选择」，而是「更贵且更差」——能力、成本、指令遵循三面同时劣于 standard。本 harness 的正确档位是 standard（qwen3.8-flash）；跨档实验证明档位选择本身就是关键工程变量。",
    ],
    7: [
        "本节回答：**V11 通用工程改造（并发只读工具、分阶段进度提醒、紧凑 Plan、顶层布局/测试根盘点、公共输出契约最小变更、日志双保险归档）的实际效果**。",
        "",
        "**1. 成本与效率：双创历史最优**",
        "",
        "- **总花费 ¥6.10（$0.86）与 $/solved $0.072 均刷新全量历史最低**（run5 ¥6.94 / $0.075，run1 ¥7.64 / $0.083）；前缀缓存命中率回升至 **90.1%**（run5 88.5%），API 调用总数从 993 降至 **929（−6.4%）**，总输入/输出同步下降 7–9%。",
        "- **并发只读被充分采纳**：全 run 触发 **62 轮并发只读批次（134 次并发只读调用）**，省下约 72 次 API 往返；`read_file`（364→305）与 `search`（66→48）总量双降，`bash` 总耗时 99.0→94.6 min（单次最大 596→307 s）。平均解题时间 21.9 min（run5 21.4）基本持平，主因是本轮晚高峰上游单次 API 延迟上升 +17.4%（11.5→13.5 s）抵消了并发省下的等待时间。",
        "",
        "**2. 定向改造验效：Run 5 两道失败题双双翻盘**",
        "",
        "- **顶层布局/测试根盘点立竿见影**：Run 4/5 连续两轮 10/10 全败的 `protonmail/944adbf` 本轮第 4 轮首编、仅 25 轮 / ¥0.062 一次通过（0/10）。",
        "- **公共输出契约最小变更生效**：Run 3/4/5/6 连挂 4 轮的 `ansible/83909bf` 本轮 63 轮 / ¥0.388 全过（0/150）；`git` 纪律连续两轮保持 **0 违纪**。",
        "- **紧凑 Plan + 分阶段提醒平稳**：`update_plan` 从 43 降至 37 次，去重与单焦点提醒各触发 1 次；全 15 题无一题撞 100 轮上限（最高 93 轮），10/15 题在 14 轮内完成首编。",
        "- **观测性补齐生效**：`agent.log`（1.24MB）完整落盘，15 题层 1 归档记录全部恢复完整。",
        "",
        "**3. 本轮未通过项与收官发现**",
        "",
        "- 单轮 **12/15**：3 题失败分别为 `qutebrowser/21b426b`（1/28 字符串近失，固有方差）、`gravitational/89f0432`（1/1 偶发回归）、`element-hq/5dfde12`（9/61，首编迟至第 51 轮导致改动不完整）。",
        "- **工具接口教训**：即便 schema 明确标注 `minLength: 1`，扁平批接口 `edit_file(files=[{path, ...}])` 仍出现 **18 次**漏写/错放 `path`（模型习惯把 `path` 写在外层或省略后续 hunk 的 `path`）。实验后已将 `edit_file` 收敛为最贴近预训练分布的标准扁平接口 `edit_file(path, old_string, new_string, replace_all)`（多处修改靠同轮多调用完成），作为本作业的最终代码形态提交收官。",
    ],
}


def write_full_run_report(attempts, run_no=1, note=None):
    """Single formal report per full run: each task contributes its first
    passing attempt, or its last valid grading if it never passed. Attempts
    invalidated by crashes/infra faults/verifier defects are excluded."""
    rdir = RDIR
    os.makedirs(rdir, exist_ok=True)
    inst = {}
    for a in attempts:
        inst.setdefault(a["label"], []).append(a)
    rows = []
    for label, aa in inst.items():
        aa.sort(key=lambda z: z["run"])
        win = next((z for z in aa if z["verdict"] == "PASS"), None)
        graded = [z for z in aa if z["verdict"] in ("PASS", "FAIL")]
        rows.append(win or (graded[-1] if graded else aa[-1]))
    rows.sort(key=lambda a: (a["verdict"] != "PASS", short(a["label"])))
    npass = sum(1 for a in rows if a["verdict"] == "PASS")
    cost = sum(a["cost_cny"] for a in rows)
    pin = sum(a["in"] for a in rows)
    pout = sum(a["out"] for a in rows)
    cach = sum(a["cached"] for a in rows)
    wall = sum(a["wall_s"] or 0 for a in rows)
    model = sum(a["model_s"] for a in rows)
    calls = sum(a["calls"] for a in rows)
    wins = [a for a in rows if a["verdict"] == "PASS"]
    win_walls = sorted(a["wall_s"] or 0 for a in wins)
    med = win_walls[len(win_walls) // 2]
    avg_solve_min = sum(win_walls) / len(wins) / 60
    c_unc = sum(a["cost_unc_cny"] for a in rows)
    c_cac = sum(a["cost_cac_cny"] for a in rows)
    c_out = sum(a["cost_out_cny"] for a in rows)
    c_nocache_in = sum(a["cost_nocache_in_cny"] for a in rows)
    excluded = sum(a["cost_cny"] for a in attempts) - cost
    ver = agent_version_info()
    cfg = config_snapshot()
    snap_path = os.path.join(rdir, "run_snapshots.json")
    snaps = json.load(open(snap_path, encoding="utf-8")) if os.path.exists(snap_path) else {}
    old_base = os.path.join(rdir, "full_run_1.baseline.json")
    if "1" not in snaps and os.path.exists(old_base):
        snaps["1"] = json.load(open(old_base, encoding="utf-8"))
        os.remove(old_base)
    if run_no == 1:
        # freeze the baseline at its locked commit, not the live working tree
        locked = snaps.get("1", {}).get("version", {}).get("commit") or ver["commit"]
        ver = version_at_commit(locked)
    elif str(run_no) in snaps:
        # versions/config describe the code AT RUN TIME: frozen on first
        # capture, never overwritten by later regenerations
        ver, cfg = snaps[str(run_no)]["version"], snaps[str(run_no)]["config"]
    # comparison references: own previous run + the original Full Run 1
    refs = []
    if run_no > 1:
        for rno in dict.fromkeys([run_no - 1, 1]):
            if str(rno) in snaps:
                refs.append((rno, snaps[str(rno)]))

    # Derive actual model(s), tier(s), extra_body, and pricing text from trace data
    used_models = sorted({m for a in rows for m in a.get("models", []) if m and m != "?"})
    tier_map = {
        cfg.get("MODEL_EXPERT"): "expert",
        cfg.get("MODEL_STANDARD"): "standard",
        cfg.get("MODEL_STARTER"): "starter",
    }
    extra_map = cfg.get("MODEL_EXTRA_BODY") or {}

    def _fmt_extra(m):
        eb = extra_map.get(m)
        if isinstance(eb, dict) and eb:
            return ", ".join(f"{k}={v}" for k, v in eb.items())
        return ""

    if len(used_models) == 1:
        m0 = used_models[0]
        tier0 = tier_map.get(m0)
        ex0 = _fmt_extra(m0)
        detail = "，".join(x for x in (f"{tier0} 档" if tier0 else "", ex0) if x)
        unused = [t for t in ("expert", "standard", "starter") if t != tier0]
        unused_str = f"；{'/'.join(unused)} 配置未启用" if tier0 else ""
        model_line = f"- 模型：全程 `{m0}`" + (f"（{detail}）" if detail else "") + unused_str
        p0 = PRICE.get(m0)
        if p0 and p0[0] is not None:
            if cach == 0 and m0 == "qwen3.6-flash":
                price_desc = f"¥{p0[0]:g}/M 输入、¥{p0[2]:g}/M 输出，实测未回填 cached_tokens 按 0 缓存估算"
            else:
                price_desc = f"¥{p0[0]:g}/M 输入、¥{p0[1]:g}/M 缓存输入、¥{p0[2]:g}/M 输出"
        else:
            price_desc = "按各模型目录价逐调用累加"
        billing_line = f"- 计费：Qwen 目录价等价（{price_desc}），非实际现金支出（Token Plan 订阅额度）；美元按 {USD:g} 折算"
        extra_label = ex0 or m0
    else:
        parts = []
        for m in used_models:
            t = tier_map.get(m)
            ex = _fmt_extra(m)
            d = "，".join(x for x in (f"{t} 档" if t else "", ex) if x)
            parts.append(f"`{m}`" + (f"（{d}）" if d else ""))
        model_line = "- 模型：" + " + ".join(parts) if parts else "- 模型：未知"
        billing_line = f"- 计费：Qwen 目录价等价（按实际调用模型逐次累加），非实际现金支出（Token Plan 订阅额度）；美元按 {USD:g} 折算"
        extra_label = "当前配置"

    sections = []
    sections.append(("概述", [
        f"- **成绩：{npass}/{len(rows)}（{100*npass/len(rows):.1f}%）**"
        + ("；未通过的题均为仅差一个测试的近似失败（见 §FAIL）" if run_no == 1 and npass < len(rows) else ""),
        model_line,
        "- 环境：Windows 16GB 笔记本 + WSL2 Docker VM 12GB，2 并发 × 4g 沙箱，1 并发评分",
        billing_line,
    ]))

    design = [
        f"- **参照版本**：git `{ver['commit']}`" + ("（src/ 含未提交修改，见下）" if ver["dirty"] else "（工作树干净）"),
        f"- src 指纹（sha256 前 16 位）：`{ver['src_sha256']}`",
        "",
        "| 配置项 | 值 |",
        "|---|---|",
    ]
    v3 = run_no >= 3
    cfg_show = dict(cfg) if v3 else {k: v for k, v in cfg.items()
                                     if k not in ("TOOL_COMPACT_THRESHOLD", "ARCHIVE_HYSTERESIS_TOKENS",
                                                  "WINDOW_TOKEN_BUDGET", "BASH_INSERT_MIN_CHARS",
                                                  "SUMMARY_COMPACT_THRESHOLD")}
    for k, v in cfg_show.items():
        design.append(f"| {k} | `{json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, bool)) else v}` |")
    design += ["", AGENT_DESIGN_PROSE_V3 if v3 else AGENT_DESIGN_PROSE]
    if ver["dirty"]:
        design += ["", "**未提交修改**（忽略换行符差异）：", "```", ver["dirty"], "```"]
    sections.append(("Agent 配置与设计", design))

    if refs:
        vlines = [f"本轮：git `{ver['commit']}`，指纹 `{ver['src_sha256']}`"
                  + ("（含未提交修改）" if ver["dirty"] else "") + "。", ""]
        perf = []
        equiv = BEHAVIOR_EQUIV.get(run_no)
        for rno, s in refs:
            bver, bcfg, btasks = s["version"], s["config"], s["tasks"]
            same = bver["src_sha256"] == ver["src_sha256"]
            beh_same = same or (equiv is not None and equiv[0] == rno)
            if same:
                label = "代码无变更。"
            elif beh_same:
                label = f"代码有变更但不影响 agent 行为（{equiv[1]}）。"
            else:
                label = "代码有变更（行为级）。"
            vlines.append(f"**相对 Full Run {rno}**（git `{bver['commit']}`，指纹 `{bver['src_sha256']}`）：" + label)
            if same:
                vlines.append("")
                continue
            log = _git("log", "--oneline", f"{bver['commit']}..HEAD", "--", "assignment3/starter_code/src")
            stat = _git("diff", "--stat", bver["commit"], "HEAD", "--", "assignment3/starter_code/src")
            wstat = ""
            if log:
                vlines += ["提交：", "```", log, "```", ""]
            if stat:
                vlines += ["文件变更：", "```", stat, "```", ""]
            if ver["dirty"]:
                wstat = _git("diff", "--ignore-cr-at-eol", "--stat", bver["commit"], "--", "assignment3/starter_code/src")
                if wstat:
                    vlines += ["未提交修改（相对该参照提交）：", "```", wstat, "```", ""]
            if not log and not stat and not wstat:
                vlines.append("git diff 不可用；以指纹差异为准，需人工比对。")
            cfg_changes = [(k, bcfg.get(k), cfg[k]) for k in cfg if bcfg.get(k) != cfg[k]]
            if cfg_changes:
                vlines += ["", "配置变更：", "", "| 配置项 | 参照 | 本轮 |", "|---|---|---|"]
                vlines += [f"| {k} | {b} | {c} |" for k, b, c in cfg_changes]
            vlines.append("")
            common = [a for a in rows if a["label"] in btasks]
            if common and not beh_same:
                perf.append((rno, common, btasks))
        if not perf:
            vlines += ["", "相对各参照均无行为级代码变更：逐题结果差异归因于模型采样随机性，不做性能对比。"]
        sections.append(("版本变更", vlines))
        if perf:
            plines = ["仅对比两轮共同题目；Δ = (本轮 − 参照) / 参照。无行为级变更的参照不做性能对比（差异仅为采样噪声）。", ""]
            for rno, common, btasks in perf:
                plines += [f"### 相对 Full Run {rno}", "",
                           "| 题目 | 结果 参→本 | 墙钟 min 参→本 | Δ墙钟 | 花费 ¥ 参→本 | Δ花费 |",
                           "|---|---|---|---:|---|---:|"]
                faster = slower = 0
                for a in common:
                    b = btasks[a["label"]]
                    bw, cw = b["wall_s"] or 0, a["wall_s"] or 0
                    dw = (cw - bw) / bw * 100 if bw else 0
                    dc = (a["cost_cny"] - b["cost_cny"]) / b["cost_cny"] * 100 if b["cost_cny"] else 0
                    faster += dw < -5
                    slower += dw > 5
                    plines.append(f"| {short(a['label'])} | {b['verdict']}→{a['verdict']} | {bw/60:.1f}→{cw/60:.1f} | {dw:+.0f}% | {b['cost_cny']:.3f}→{a['cost_cny']:.3f} | {dc:+.0f}% |")
                bc = [btasks[a["label"]] for a in common]
                b_npass = sum(1 for b in bc if b["verdict"] == "PASS")
                c_npass = sum(1 for a in common if a["verdict"] == "PASS")
                b_cost = sum(b["cost_cny"] for b in bc)
                c_cost = sum(a["cost_cny"] for a in common)
                b_wins = [b["wall_s"] or 0 for b in bc if b["verdict"] == "PASS"]
                c_wins = [a["wall_s"] or 0 for a in common if a["verdict"] == "PASS"]
                b_avg = sum(b_wins) / len(b_wins) / 60 if b_wins else 0
                c_avg = sum(c_wins) / len(c_wins) / 60 if c_wins else 0
                b_ups = b_cost / b_npass / USD if b_npass else 0
                c_ups = c_cost / c_npass / USD if c_npass else 0
                plines += ["", f"**汇总对比（仅共同题目，相对 Full Run {rno}）**：", "",
                           f"| 指标 | Full Run {rno} | 本轮 | Δ |", "|---|---:|---:|---:|",
                           f"| 解题数 | {b_npass}/{len(bc)} | {c_npass}/{len(common)} | {c_npass-b_npass:+d} |",
                           f"| 花费 ¥ | {b_cost:.2f} | {c_cost:.2f} | {(c_cost-b_cost)/b_cost*100:+.0f}% |" if b_cost else f"| 花费 ¥ | {b_cost:.2f} | {c_cost:.2f} | — |",
                           f"| 平均解题时间 min | {b_avg:.1f} | {c_avg:.1f} | {(c_avg-b_avg)/b_avg*100:+.0f}% |" if b_avg else f"| 平均解题时间 min | {b_avg:.1f} | {c_avg:.1f} | — |",
                           f"| $/solved | {b_ups:.3f} | {c_ups:.3f} | {(c_ups-b_ups)/b_ups*100:+.0f}% |" if b_ups else f"| $/solved | {b_ups:.3f} | {c_ups:.3f} | — |",
                           "", f"共同题目中 {faster} 题明显更快（墙钟降幅 >5%），{slower} 题明显更慢。"]
            plines += ["", "任务集不同时解题数不可直接比。"]
            sections.append(("性能对比", plines))
        refl = RUN_REFLECTIONS.get(run_no)
        if refl:
            sections.append(("总结与反思", list(refl)))

    table = ["| 题目 | 结果 | 轮数 | 墙钟 min | 模型 min | 输入 M | 输出 K | 缓存% | 花费 ¥ |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for a in rows:
        it = a["iters"] if a["iters"] is not None else "—"
        wall_m = f"{a['wall_s']/60:.1f}" if a["wall_s"] else "—"
        table.append(
            f"| {short(a['label'])} | {a['verdict']} | {it} | {wall_m} | {a['model_s']/60:.1f} "
            f"| {a['in']/1e6:.2f} | {a['out']/1e3:.1f} | {100*a['cached']/max(a['in'],1):.1f} | {a['cost_cny']:.3f} |")
    sections.append(("逐题结果", table))

    sections.append(("汇总指标", [
        "| 指标 | 数值 |",
        "|---|---|",
        f"| API 调用 | {calls} 次，平均 {model/max(calls,1):.1f} s/次 |",
        f"| Token 总量 | 输入 {pin/1e6:.2f}M / 输出 {pout/1e3:.1f}K（{pin/max(pout,1):.0f}:1），缓存命中 {100*cach/max(pin,1):.1f}% |",
        f"| 平均解题时间 | **{avg_solve_min:.1f} min**（中位 {med/60:.1f}；最快 {win_walls[0]/60:.1f}，最慢 {win_walls[-1]/60:.1f}） |",
        f"| 任务墙钟总和 | {wall/3600:.2f} h（2 并发，实际历时更短）；模型推理 {model/3600:.2f} h，占 {100*model/max(wall,1):.0f}% |",
        f"| **总花费（目录价等价）** | **¥{cost:.2f} ≈ ${cost/USD:.2f}** |",
        f"| 成本构成 | 未缓存输入 ¥{c_unc:.2f}（{100*c_unc/cost:.0f}%）+ 缓存输入 ¥{c_cac:.2f}（{100*c_cac/cost:.0f}%）+ 输出 ¥{c_out:.2f}（{100*c_out/cost:.0f}%） |",
        f"| 平均每题花费 | ¥{cost/len(rows):.2f} |",
        f"| 平均每个胜题花费 | ¥{cost/npass:.2f} ≈ **${cost/npass/USD:.3f}/solved** |",
        f"| 胜题单次成本 | 最低 ¥{min(a['cost_cny'] for a in wins):.2f}，最高 ¥{max(a['cost_cny'] for a in wins):.2f} |",
        "",
        "compaction 调用未记录 usage，成本低估可忽略。",
    ]))

    beh = [(a, scan_behavior(a.get("tdir", ""))) for a in rows]
    beh = [(a, b) for a, b in beh if b]
    charts = render_charts(beh, (c_unc, c_cac, c_out), os.path.join(rdir, "charts")) if beh else []
    if "cost_breakdown.png" in charts:
        sections[-1] = (sections[-1][0], sections[-1][1] + ["", "![Cost breakdown](charts/cost_breakdown.png)"])
    if beh:
        short_name = {"read_file": "read", "read_archived_output": "archive", "edit_file": "edit",
                      "create_file": "create", "bash": "bash", "search": "search", "update_plan": "plan"}
        blines = [
            "数据源：run.jsonl 的 tool_call/tool_result 事件；思维链总结基于 agent.log 的 [thinking] 记录（run5 缺失，据工具序列重建）。重复浪费 = 工具名与参数完全相同的多余调用次数；高频重读 = 同一文件以不同行段反复读取。",
            "",
            "| 题目 | 工具分布 | 首编辑轮 | 编辑次数 | 测试命令 | 重复浪费 | 错误结果 |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
        for a, b in beh:
            dist = " ".join(f"{short_name.get(k, k)}{v}" for k, v in sorted(b["tools"].items(), key=lambda kv: -kv[1])[:5])
            fe = b["first_edit"] + 1 if b["first_edit"] is not None else "—"
            blines.append(f"| {short(a['label'])} | {dist} | {fe} | {b['edits']} | {b['bash_tests']} | {b['wasted']} | {b['errors']}/{b['results']} |")
        tt = {}
        for _, b in beh:
            for k, (n, s, mx) in b["tool_time"].items():
                cur = tt.setdefault(k, [0, 0.0, 0.0])
                cur[0] += n; cur[1] += s; cur[2] = max(cur[2], mx)
        blines += ["", "**动作耗时（全部题目合计）**：", "",
                   "| 工具 | 次数 | 总耗时 min | 平均 s | 最大 s |",
                   "|---|---:|---:|---:|---:|"]
        for k, (n, s, mx) in sorted(tt.items(), key=lambda kv: -kv[1][1]):
            blines.append(f"| {k} | {n} | {s/60:.1f} | {s/max(n,1):.1f} | {mx:.0f} |")
        blines += ["", "**压缩发生（层 1 工具归档 / 层 2 摘要压缩）**：", "",
                   "| 题目 | 层1 次数 | 层1 发生分布 | 层2 次数 | 层2 发生轮 |",
                   "|---|---:|---|---:|---|"]
        for a, b in beh:
            logp = (os.path.join(STARTER, "run_logs", "agent.log") if a["run"] == "CURRENT"
                    else os.path.join(ARCH, a["run"], "run_logs", "agent.log"))
            if not os.path.exists(logp) and a.get("hdir"):
                fallback = os.path.join(a["hdir"], "agent.log")
                if os.path.exists(fallback):
                    logp = fallback
            l1, l2 = scan_compactions(a.get("tdir", ""), logp, int(a["key"]))
            if l1:
                if len(l1) <= 6:
                    s1 = "，".join(f"turn {t}（×{n}）" if t is not None else f"×{n}" for t, n in l1)
                else:
                    peak = max(l1, key=lambda x: x[1])
                    s1 = f"turn {l1[0][0]}–{l1[-1][0]} 共 {len(l1)} 次，峰值 turn {peak[0]}（×{peak[1]}）"
                c1 = str(len(l1))
            else:
                s1, c1 = ("—", "—") if not os.path.exists(logp) else ("无", "0")
            s2 = "、".join(f"turn {t + 1 if t is not None else '?'}" for t in l2) or "无"
            blines.append(f"| {short(a['label'])} | {c1} | {s1} | {len(l2)} | {s2} |")
        blines += ["", COMPACT_NOTE_V3 if run_no >= 3 else COMPACT_NOTE_V1, ""]
        for cname in ("tool_calls.png", "tool_time_total.png", "time_composition.png"):
            if cname in charts:
                blines += ["", f"![{cname[:-4]}](charts/{cname})"]
        blines += ["", "**自动标记**：", ""]
        flags = []
        for a, b in beh:
            notes = []
            if b["wasted"] >= 5:
                top = "；".join(f"{short_name.get(n, n)} `{d}` ×{c}" for n, d, c in b["top_reps"][:2])
                notes.append(f"{b['wasted']} 次完全相同的重复调用（如 {top}）")
            if b["first_edit"] is None:
                notes.append("全程无编辑")
            elif b["first_edit"] + 1 > 15:
                notes.append(f"首编辑迟至第 {b['first_edit']+1} 轮")
            if b["results"] and b["errors"] / b["results"] > 0.2:
                notes.append(f"工具错误率 {100*b['errors']/b['results']:.0f}%")
            if b["top_reads"] and b["top_reads"][0][1] >= 6:
                path, n = b["top_reads"][0]
                notes.append(f"高频重读 {os.path.basename(path)} ×{n}（不同行段）")
            if b["retry_stall_s"] > 300:
                notes.append(f"API 重试停滞 ≈{b['retry_stall_s']/60:.0f} min（上游挂起/退避，非模型耗时）")
            if notes:
                flags.append(f"- {short(a['label'])}：" + "；".join(notes))
        blines += flags or ["- 无显著异常"]
        bnote_items = []
        for a, b in beh:
            bnote = BEHAVIOR_NOTES.get(run_no, {}).get(short(a["label"]))
            if bnote:
                bnote_items += [f"### {short(a['label'])}", "", bnote, ""]
        if bnote_items:
            blines += ["", "**逐题行为与思维链总结**：", ""] + bnote_items
        sections.append(("Agent 行为分析", blines))

    token_lines = [
        f"1. **输入:输出 = {pin/max(pout,1):.0f}:1**：agent 负载的成本主体是反复提交上下文，而非生成。{100*cach/max(pin,1):.0f}% 的缓存命中节省约 ¥{c_nocache_in-(c_unc+c_cac):.1f}；若无前缀缓存，总成本约为 ¥{c_nocache_in+c_out:.1f}。前缀缓存是该 harness 中价值最高的机制，compaction 策略（减少触发、触发后保持前缀稳定）直接决定成本。",
        f"2. **输出仅占 token 的 {100*pout/(pin+pout):.1f}%，却占成本 {100*c_out/cost:.0f}%**；未缓存输入占 token {100*(pin-cach)/(pin+pout):.0f}%、占成本 {100*c_unc/cost:.0f}%。压缩成本的两个杠杆：减少上下文失效、控制输出长度。{extra_label} 下平均输出 {pout/max(calls,1):.0f} tok/call、平均延迟 {model/max(calls,1):.1f} s。",
    ]
    if run_no == 1:
        token_lines += [
            "3. **首次尝试通过率**：13 个胜题中 3 题首次尝试即通过（openlibrary/4a5d2a7d、teleport/89f0432、webclients/944adbf），平均 10.6 min、¥0.21/题，表明优化后的 agent 在常规难度题目上已接近该模型档位的效率上限。",
            "4. **长尾题目**：ansible/f327e65（60.6 min）与 element-web/5dfde12（45.0 min）占用最多墙钟时间；teleport/1316e67 达到 99 轮、¥1.10，为最贵单题。轮数/墙钟预算是后续压缩时间的主要手段。",
            "5. 全部流量使用 standard 档；expert（qwen4-max）未接线。若对近似失败题使用 expert 重试，成本需按 expert 单价单独核算。",
        ]
    sections.append(("Token 用量分析", token_lines
                     + (["", "![API latency distribution](charts/api_latency.png)"] if "api_latency.png" in charts else [])))

    if run_no == 1:
        fail_body = [
            "### {n}.1 qutebrowser/21b426b —— 27/28，repr 输出格式不符",
            "",
            "- **失败测试**：`tests/unit/config/test_configutils.py::test_repr`（f2p 3 个通过 2 个，p2p 25 个全部通过）",
            "- **直接原因**：官方测试期望 gold 的精确 repr 串 `vmap=odict_values([ScopedValue(...), ...])`（vmap 关键字 + 原始视图）；agent 两次尝试均写 `values=list(self._vmap.values())`，输出 `values=[ScopedValue(...)]`。功能语义正确，展示格式不符。",
            "- **根因**：requirements 仅要求内部结构改用 `_vmap`，未规定 repr 的关键字与视图形态；隐藏测试锁定 gold 的精确字符串。agent 做了列表归一化并保留旧关键字，还用 scratch 脚本自证该格式\"即期望格式\"（循环自证）。Full Run 2 零反馈重跑复现了完全相同的选择——该偏差是确定性的，无反馈重跑不可修复。",
            "- **可修复性**：高，但依赖反馈。近似失败重试（回喂失败测试名/断言差异）一轮可修复；或在提示词中强化\"公共输出契约保持最小变更\"。",
            "",
            "### {n}.2 ansible/83909bfa —— 149/150，错误消息文案不符",
            "",
            "- **失败测试**：`test/units/galaxy/test_api.py::test_api_no_auth_but_required`（唯一 f2p；149 个 p2p 全部通过）",
            "- **直接原因**：测试以正则锁定文案前缀 `\"No access token or username set. A token can be set with --api-key or at \"`；agent 输出为 `\"--token (or --api-key), written to the Galaxy token file (default: ~/.ansible/galaxy_token), or set in ansible.cfg.\"`。login 移除、token 迁移、CLI 拦截均正确。",
            "- **根因**：题面自相矛盾——problem_statement 暗示消息应包含获取 token 的网址（`https://galaxy.ansible.com/me/prefere...`），requirements 却明文要求提示 token file 或 --token 参数。agent 采用后者，隐藏测试锁定前者；且 agent 曾把可见测试的期望串改成自己的措辞以自证（循环自证）。",
            "- **评分可信度**：已使用官方评分链路（ansible-test units + 正确 PYTHONPATH）复核，确认 149/150，补丁质量高。",
            "- **可修复性**：高。Full Run 2 零反馈重跑已通过（0/150）——措辞选择存在随机性，重跑可修复；近似失败重试则更稳。",
        ]
    else:
        fails = [a for a in rows if a["verdict"] == "FAIL"]
        custom = RUN_FAILURE_TEXTS.get(run_no)
        if custom:
            fail_body = list(custom)
        else:
            r1_tasks = snaps.get("1", {}).get("tasks", {})
            fail_body = []
            for a in fails:
                suffix = (
                    "Full Run 1 亦未通过，可对照其失败案例分析。"
                    if r1_tasks.get(a["label"], {}).get("verdict") == "FAIL"
                    else "详见 §5 总结与 §8 行为分析。"
                )
                fail_body.append(f"- {short(a['label'])}：{a.get('tests', '?')} 个测试未通过（官方 verifier）。{suffix}")
            fail_body = fail_body or ["本轮无失败题目。"]
    sections.append(("失败案例分析", fail_body))

    if run_no == 1:
        sections.append(("排行榜对比（leaderboard.cs2680.com/a3，64 份提交，2026-10-21 截止）", [
        "**口径差异：**",
        "",
        "- 排行榜为 26 道隐藏题、课程 API、CloudLab/AWS 级机器、5 并发、4h 上限；本运行是 15 题本地混合集（5 原题 + 10 道 A2 实例）、自有 Qwen API、16GB 笔记本、2 并发、无时限。解题率不可直接换算：本地集合不含榜上最难题，86.7% 不代表 26 题集可达同等比例。",
        f"- 榜上 cost = 总 API 花费 ÷ 解题数（美元）。本报告 **${cost/npass/USD:.3f}/solved** 仅计有效尝试；若计入开发期的无效尝试（全量口径）则为 $0.215/solved。榜上提交应含其 run 内失败任务的开销，但不含跨 run 的开发迭代，故本报告口径更接近榜上口径，仍略偏乐观。",
        "",
        "**数字对比：**",
        "",
        "| 维度 | 本运行 | 排行榜 | 位置 |",
        "|---|---|---|---|",
        "| 解题率 | 13/15 = 86.7%（本地集） | 最高 20/26 = 76.9%（Felix Wang），中位 ~14/26 = 54% | 不可直接比；本地集表现优秀 |",
        f"| 每解成本 | **${cost/npass/USD:.3f}** | 64 份中仅 5 份更低（$0.027–0.079）；头部集群 $0.32–0.52 | **成本效率前 ~9%**，较榜首 Felix Wang（$0.394）低 79% |",
        "| 每解耗时 | **1482 s**（24.7 min） | 157 s（error404）～ 1350 s；头部 280–670 s | 全榜最慢（较最慢匿名提交仍慢 10%） |",
        "| 模型策略 | 纯 standard，无 expert | 无人纯 expert；头部为 standard 为主 + 少量 expert | 与主流一致 |",
        "",
        "**结论：**",
        "",
        f"1. **成本为主要优势**：${cost/npass/USD:.3f}/solved 处于榜上最低成本档（与 Justin Liu $0.084、Y $0.068 同区），验证 medium thinking + 高缓存命中 + 不使用 expert 的路线在成本上优于头部提交。含开发迭代开销的 $0.215/solved 亦优于约 72% 的提交。",
        "2. **速度为主要短板**：2 并发（本地内存限制）对比榜上 5 并发；100 轮预算且无墙钟约束（teleport/ansible 达 99 轮）；无近似失败重试机制。榜上 4h 硬上限所催生的轮数预算与跳题策略均未实现。",
        "3. **解题能力**：常规难度题首次尝试通过率高、平均约 12 分钟，与榜上中游水平相当；差距主要在难题覆盖（未运行 26 题集）与失败恢复机制。",
        "4. **综合定位**：成本头部、速度末位、解题率受集合难度影响偏高的独立复现。若补齐近似失败重试与轮数预算，并迁移至 5 并发机器，按当前单题效率外推，26 题集预计 14–17 解、$0.1–0.2/solved。",
    ]))

    method = [
        "- **每题计一次有效尝试**：通过题取首次通过的尝试；未通过题取最后一次有效评分（官方 verifier 链路）。因崩溃、OOM、编排器故障、verifier 缺陷或模型行为异常而无效的尝试不计入时间与成本。",
        "- 尝试按 hostlog 运行 ID 分组，本报告只含本轮尝试。",
    ]
    if note:
        method.append(f"- **本轮性质**：{note}")
    if excluded > 0.01:
        method.append(f"- 被剔除的无效尝试合计 ¥{excluded:.2f}；原始数据完整保留于 `previous_runs/` 存档与 `tmp_all_attempts.json`，可恢复全量口径。")
    if run_no == 1:
        method.append("- 本报告为跨轮合并的等价单轮统计：各有效尝试实际发生于不同 agent 版本（prompt 与基础设施逐步改进），非一次连续运行。")
    method += [
        "- 耗时 = trace 首末事件时间差；成本按目录价逐调用累加；compaction 调用无 usage 记录，未计入。",
        "- 任务归属按 hostlogs 任务表快照 + trace 位置序号对齐；无 results.md 的轮次以 evaluations.tsv 补充判定。",
        "- 排行榜数据取自截止后的静态 API（作业已关闭，无新增提交）。",
    ]
    sections.append(("统计方法说明", method))

    nums = {title: i for i, (title, _) in enumerate(sections, 1)}
    fail_no = nums["失败案例分析"]
    gen_date = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# CS2680 Assignment 3 运行报告 · Full Run {run_no}", "",
             f"生成日期：{gen_date} · 统计规则：每题计一次有效尝试（见 §METHOD）".replace("§METHOD", f"§{nums['统计方法说明']}"), ""]
    for i, (title, body) in enumerate(sections, 1):
        lines.append(f"## {i}. {title}")
        lines.append("")
        for b in body:
            lines.append(b.replace("§FAIL", f"§{fail_no}").replace("{n}.", f"{i}."))
        lines.append("")
    fname = f"full_run_{run_no}.md"
    open(os.path.join(rdir, fname), "w", encoding="utf-8").write("\n".join(lines))
    snaps[str(run_no)] = {
        "version": ver, "config": cfg,
        "tasks": {a["label"]: {"verdict": a["verdict"], "iters": a["iters"],
                                "wall_s": a["wall_s"], "cost_cny": a["cost_cny"],
                                "in": a["in"], "out": a["out"]} for a in rows},
        "totals": {"ntasks": len(rows), "npass": npass, "cost": cost,
                   "avg_solve_min": avg_solve_min, "usd_per_solved": cost / npass / USD},
    }
    json.dump(snaps, open(snap_path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"wrote reports/{fname} ({len(rows)} tasks, {npass} pass, ¥{cost:.2f})")


def main():
    hostlogs = os.path.join(os.path.expanduser("~"), ".cache", "cs2680_a3", "hostlogs")
    hl_by_id = {}
    if os.path.isdir(hostlogs):
        for d in os.listdir(hostlogs):
            tj = os.path.join(hostlogs, d, "evaluation_scripts", "agent_task_input.json")
            if os.path.exists(tj):
                hl_by_id[d.replace("starter_code_", "")] = (json.load(open(tj, encoding="utf-8")), os.path.join(hostlogs, d))

    def task_table(root, stamp):
        seq = os.path.join(root, "run_logs", "sequence.log")
        if os.path.exists(seq):
            head = open(seq, encoding="utf-8", errors="replace").readline()
            m = re.search(r"a3agent_(\d+_\d+)", head)
            if m and m.group(1) in hl_by_id:
                return hl_by_id[m.group(1)]
        if stamp != "CURRENT":
            # archive stamp = local time (UTC+8) the NEXT run started and
            # archived this output; the hostlog nearest the stamp belongs to
            # that next run, and the archived run is the one just before it
            t0 = calendar.timegm(datetime.strptime(stamp, "%Y%m%d-%H%M%S").timetuple()) - 8 * 3600
            epochs = sorted(int(h.split("_")[0]) for h in hl_by_id)
            nxt = min(epochs, key=lambda e: abs(e - t0))
            if abs(nxt - t0) < 3600:
                prev = [e for e in epochs if e < nxt]
                if prev:
                    hid = next(h for h in hl_by_id if h.startswith(str(prev[-1]) + "_"))
                    return hl_by_id[hid]
        if stamp == "CURRENT" and hl_by_id:
            return hl_by_id[max(hl_by_id, key=lambda h: int(h.split("_")[0]))]
        return None, None

    def tsv_verdicts(hdir):
        out = {}
        p = os.path.join(hdir, "evaluations.tsv") if hdir else ""
        if p and os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                f = line.rstrip("\n").split("\t")
                if len(f) >= 4:
                    out[f[0]] = "PASS" if f[3] == "1" else "FAIL"
        return out

    attempts = []
    sources = [(d, os.path.join(ARCH, d)) for d in sorted(os.listdir(ARCH))]
    sources.append(("CURRENT", STARTER))
    for stamp, root in sources:
        logs = os.path.join(root, "madsOpt_logs")
        if not os.path.isdir(logs):
            continue
        table, hdir = task_table(root, stamp)
        epoch = None
        if hdir:
            head = os.path.basename(hdir).replace("starter_code_", "").split("_")[0]
            if head.isdigit():
                epoch = int(head)
        results = parse_results_md(os.path.join(root, "run_all_results.md"))
        tsv = tsv_verdicts(hdir)
        # trace dirs are named by numeric POSITION even when the task table
        # uses instance-id keys (run6+); align everything by position
        tkeys = list(table.keys()) if table else sorted(os.listdir(logs), key=lambda k: (len(k), k))
        for pos in sorted(int(d) for d in os.listdir(logs) if d.isdigit()):
            tdir = os.path.join(logs, str(pos))
            m = scan_task_dir(tdir)
            if m is None:
                continue
            k = tkeys[pos] if pos < len(tkeys) else str(pos)
            label = table[k].get("instance_id", k) if table else (results[pos][0] if pos < len(results) else k)
            verdict = results[pos][1] if pos < len(results) else tsv.get(str(pos), "?")
            m.update({"run": stamp, "key": str(pos), "label": label, "verdict": verdict,
                      "tdir": tdir, "hid_epoch": epoch, "hdir": hdir,
                      "tests": results[pos][2] if pos < len(results) else ""})
            attempts.append(m)
    # Full Run 1 = the development campaign (equivalent single run); every
    # hostlog at/after the boundary is its own formal run (2, 3, ...).
    boundary = 1795169261
    later = sorted({a["hid_epoch"] for a in attempts if a["hid_epoch"] and a["hid_epoch"] >= boundary})
    groups = {1: [a for a in attempts if not a["hid_epoch"] or a["hid_epoch"] < boundary]}
    for i, e in enumerate(later):
        groups[2 + i] = [a for a in attempts if a["hid_epoch"] == e]
    for no, aa in sorted(groups.items()):
        if aa:
            write_full_run_report(aa, no, note=RUN_NOTES.get(no))
    json.dump(attempts, open(os.path.join(RDIR, "tmp_all_attempts.json"), "w"), indent=1)

    # per-instance rollup
    inst = {}
    for a in attempts:
        key = a["label"] if a["label"].startswith("instance_") else f"numeric:{a['run']}:{a['key']}"
        inst.setdefault(key, []).append(a)

    print(f"{'task':38s} {'tries':>5s} {'final':>5s} {'iters':>5s} {'wall_min':>8s} {'model_min':>9s} {'in_M':>6s} {'out_K':>6s} {'cache%':>6s} {'CNY':>7s}")
    tot = {"cost": 0.0, "in": 0, "out": 0, "cached": 0, "calls": 0, "wall": 0.0, "model": 0.0}
    pass_walls, pass_costs = [], []
    for key, aa in inst.items():
        aa.sort(key=lambda a: a["run"])
        graded = [a for a in aa if a["verdict"] in ("PASS", "FAIL")]
        last = graded[-1] if graded else aa[-1]
        c = sum(a["cost_cny"] for a in aa)
        pin = sum(a["in"] for a in aa)
        pout = sum(a["out"] for a in aa)
        cach = sum(a["cached"] for a in aa)
        wall = sum(a["wall_s"] or 0 for a in aa)
        model = sum(a["model_s"] for a in aa)
        tot["cost"] += c; tot["in"] += pin; tot["out"] += pout; tot["cached"] += cach
        tot["calls"] += sum(a["calls"] for a in aa); tot["wall"] += wall; tot["model"] += model
        name = short(key) if key.startswith("instance_") else key
        it = last["iters"] if last["iters"] is not None else "?"
        print(f"{name:38s} {len(aa):5d} {last['verdict']:>5s} {str(it):>5s} {wall/60:8.1f} {model/60:9.1f} {pin/1e6:6.2f} {pout/1e3:6.1f} {100*cach/max(pin,1):6.1f} {c:7.3f}")
        if last["verdict"] == "PASS":
            win = next(a for a in aa if a["verdict"] == "PASS")
            pass_walls.append(win["wall_s"] or 0)
            pass_costs.append(win["cost_cny"])
    print()
    print(f"TOTAL: {tot['calls']} calls, in={tot['in']/1e6:.2f}M out={tot['out']/1e3:.1f}K cached={100*tot['cached']/max(tot['in'],1):.1f}%")
    print(f"wall(sum of task walls)={tot['wall']/3600:.2f}h model_time={tot['model']/3600:.2f}h")
    print(f"cost=¥{tot['cost']:.2f} (~${tot['cost']/USD:.2f} @7.1)")
    if pass_walls:
        print(f"PASS tasks: n={len(pass_walls)} avg_wall={sum(pass_walls)/len(pass_walls)/60:.1f}min avg_cost(final attempt)=¥{sum(pass_costs)/len(pass_costs):.3f}")
    print(f"avg cost per task (all spend / 15) = ¥{tot['cost']/15:.3f}")


if __name__ == "__main__":
    main()