# CS2680 Assignment 3 运行报告 · Full Run 6

生成日期：2026-11-20 · 统计规则：每题计一次有效尝试（见 §11）

## 1. 概述

- **成绩：4/15（26.7%）**
- 模型：全程 `qwen3.8-flash`（standard 档，reasoning_effort=medium）；expert/starter 配置未启用
- 环境：Windows 16GB 笔记本 + WSL2 Docker VM 12GB，2 并发 × 4g 沙箱，1 并发评分
- 计费：Qwen 目录价等价（¥0.8/M 输入、¥0.1/M 缓存输入、¥2.7/M 输出），非实际现金支出（Token Plan 订阅额度）；美元按 7.1 折算

## 2. Agent 配置与设计

- **参照版本**：git `40fd5f2`（src/ 含未提交修改，见下）
- src 指纹（sha256 前 16 位）：`c31c4da0f6a02268`

| 配置项 | 值 |
|---|---|
| MAX_ITERATIONS | `100` |
| MAX_OPEN_TASKS | `2` |
| API_RETRIES | `7` |
| API_BACKOFF_S | `4.0` |
| API_BACKOFF_MAX_S | `60.0` |
| PRESERVE_THINKING | `true` |
| MODEL_EXPERT | `qwen4-max` |
| MODEL_STANDARD | `qwen3.8-flash` |
| MODEL_STARTER | `qwen3.6-flash` |
| MODEL_EXTRA_BODY | `{"qwen4-max": {"reasoning_effort": "high"}, "qwen3.8-flash": {"reasoning_effort": "medium"}, "qwen3.6-flash": {"thinking_budget": 16384}}` |
| TOOL_COMPACT_THRESHOLD | `40000` |
| ARCHIVE_HYSTERESIS_TOKENS | `8000` |
| WINDOW_TOKEN_BUDGET | `8000` |
| BASH_INSERT_MIN_CHARS | `2000` |
| SUMMARY_COMPACT_THRESHOLD | `56000` |

**架构（三层）**

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

**上下文管理（memory.py，两层压缩 · V6 重设计）**

- 层 1 批量归档：prompt_tokens ≥ 40K 且距上次归档增长 ≥ 8K 才触发；活窗口 = 尾部累计 ≤ 8K token 的工具输出（无条数约束）；原始 ≥ 2× 预览（1200 字符）才归档为 600 字符预览（头 100/尾 500）+ 引用；水位线以下永不改写——两次批量之间请求前缀逐字节稳定，最大化前缀缓存命中。
- bash 出生即预览：≥ 2000 字符的 bash 输出在插入时即归档（头 100/尾 900，全文可 `read_archived_output` 按行取回），此后永不改写；trace 仍记全文。
- 层 2（≥ 56K）：全量摘要 compaction，历史替换为 system + 摘要 + 当前计划 + git status/diff 快照 + **结构化状态块**（已读/已改文件、scratch、最近命令、归档索引），压缩后取回通路不断头。

**工具集（7 个）**：read_file、read_archived_output、search（字面匹配）、edit_file、create_file、bash（拒绝 cat/sed/head/tail）、update_plan；scratch 文件限定 /work，避免污染补丁。

**系统提示词（prompts.py）**：预算、自主性、ground-truth（否定可靠记忆、防 PR-recall 螺旋）、行动偏向、工具纪律、验证六段；参考 openai/codex 编码提示词重写。

**追踪**：每任务 latency.jsonl（逐调用模型/延迟/token/缓存）+ run.jsonl（api_request/tool_call/tool_result/run_end）。

**未提交修改**（忽略换行符差异）：
```
assignment3/starter_code/src/agentic_loop.py
```

## 3. 版本变更

本轮：git `40fd5f2`，指纹 `c31c4da0f6a02268`（含未提交修改）。

**相对 Full Run 5**（git `e1cd0e6`，指纹 `da02c9571e6a8864`）：代码有变更（行为级）。
未提交修改（相对该参照提交）：
```
assignment3/starter_code/src/agentic_loop.py | 3 ++-
 1 file changed, 2 insertions(+), 1 deletion(-)
```


**相对 Full Run 1**（git `a731a16`，指纹 `3da72e34d286f470`）：代码有变更（行为级）。
提交：
```
e1cd0e6 feat(a3): per-hunk edit results + git-discipline and replace_all prompt rules
afa3045 feat(a3): read_file source pagination and atomic batch edit with replace_all
68c363f feat(a3): batch compaction with prefix-cache stability, unified config, tools/reports layout
```

文件变更：
```
assignment3/starter_code/src/agentic_loop.py   |  43 ++-
 assignment3/starter_code/src/config.py         |  98 ++++--
 assignment3/starter_code/src/memory.py         | 420 +++++++++++++++----------
 assignment3/starter_code/src/prompts.py        | 201 ++++++------
 assignment3/starter_code/src/sandbox.py        |   5 +-
 assignment3/starter_code/src/sandbox_server.py |  95 ++++--
 assignment3/starter_code/src/tools.py          |  43 ++-
 7 files changed, 583 insertions(+), 322 deletions(-)
```

未提交修改（相对该参照提交）：
```
assignment3/starter_code/src/agentic_loop.py   |  46 ++++---
 assignment3/starter_code/src/config.py         |  98 ++++++++++----
 assignment3/starter_code/src/memory.py         | 172 +++++++++++++++++++------
 assignment3/starter_code/src/prompts.py        |  25 +++-
 assignment3/starter_code/src/sandbox.py        |   5 +-
 assignment3/starter_code/src/sandbox_server.py |  95 +++++++++++---
 assignment3/starter_code/src/tools.py          |  43 +++++--
 7 files changed, 373 insertions(+), 111 deletions(-)
```



## 4. 性能对比

仅对比两轮共同题目；Δ = (本轮 − 参照) / 参照。无行为级变更的参照不做性能对比（差异仅为采样噪声）。

### 相对 Full Run 5

| 题目 | 结果 参→本 | 墙钟 min 参→本 | Δ墙钟 | 花费 ¥ 参→本 | Δ花费 |
|---|---|---|---:|---|---:|
| flipt-io/2eac0df | PASS→PASS | 17.9→15.2 | -15% | 0.250→3.689 | +1379% |
| internetarchive/4a5d2a7 | PASS→PASS | 1.6→1.4 | -13% | 0.028→0.303 | +981% |
| qutebrowser/c580ebf | PASS→PASS | 17.3→11.0 | -37% | 0.559→3.820 | +583% |
| qutebrowser/de4a1c1 | PASS→PASS | 28.7→4.6 | -84% | 0.835→2.156 | +158% |
| ansible/83909bf | FAIL→FAIL | 15.6→6.3 | -59% | 0.533→3.541 | +565% |
| ansible/a26c325 | PASS→FAIL | 15.4→6.8 | -56% | 0.559→1.762 | +215% |
| ansible/f327e65 | PASS→FAIL | 27.6→3.7 | -87% | 0.598→1.167 | +95% |
| element-hq/5dfde12 | PASS→FAIL | 39.5→12.1 | -69% | 0.678→3.180 | +369% |
| flipt-io/2ca5dfb | PASS→FAIL | 29.2→25.5 | -13% | 0.650→3.248 | +400% |
| future-architect/5af1a22 | PASS→FAIL | 35.5→18.5 | -48% | 0.421→3.951 | +839% |
| gravitational/1316e67 | PASS→FAIL | 26.7→5.2 | -81% | 0.763→2.727 | +258% |
| gravitational/89f0432 | PASS→FAIL | 14.6→9.1 | -38% | 0.226→3.555 | +1471% |
| internetarchive/53d376b | PASS→FAIL | 15.0→6.2 | -59% | 0.518→3.438 | +563% |
| protonmail/944adbf | FAIL→FAIL | 10.7→6.6 | -39% | 0.105→0.245 | +132% |
| qutebrowser/21b426b | PASS→FAIL | 9.1→2.7 | -70% | 0.216→0.530 | +145% |

**汇总对比（仅共同题目，相对 Full Run 5）**：

| 指标 | Full Run 5 | 本轮 | Δ |
|---|---:|---:|---:|
| 解题数 | 13/15 | 4/15 | -9 |
| 花费 ¥ | 6.94 | 37.31 | +438% |
| 平均解题时间 min | 21.4 | 8.1 | -62% |
| $/solved | 0.075 | 1.314 | +1648% |

共同题目中 15 题明显更快（墙钟降幅 >5%），0 题明显更慢。
### 相对 Full Run 1

| 题目 | 结果 参→本 | 墙钟 min 参→本 | Δ墙钟 | 花费 ¥ 参→本 | Δ花费 |
|---|---|---|---:|---|---:|
| flipt-io/2eac0df | PASS→PASS | 11.2→15.2 | +36% | 0.113→3.689 | +3172% |
| internetarchive/4a5d2a7 | PASS→PASS | 5.9→1.4 | -76% | 0.157→0.303 | +94% |
| qutebrowser/c580ebf | PASS→PASS | 20.0→11.0 | -45% | 0.385→3.820 | +891% |
| qutebrowser/de4a1c1 | PASS→PASS | 25.5→4.6 | -82% | 0.733→2.156 | +194% |
| ansible/83909bf | FAIL→FAIL | 16.8→6.3 | -62% | 0.657→3.541 | +439% |
| ansible/a26c325 | PASS→FAIL | 16.8→6.8 | -59% | 0.594→1.762 | +196% |
| ansible/f327e65 | PASS→FAIL | 60.6→3.7 | -94% | 0.724→1.167 | +61% |
| element-hq/5dfde12 | PASS→FAIL | 45.0→12.1 | -73% | 0.916→3.180 | +247% |
| flipt-io/2ca5dfb | PASS→FAIL | 31.1→25.5 | -18% | 0.768→3.248 | +323% |
| future-architect/5af1a22 | PASS→FAIL | 24.3→18.5 | -24% | 0.205→3.951 | +1824% |
| gravitational/1316e67 | PASS→FAIL | 33.7→5.2 | -85% | 1.098→2.727 | +148% |
| gravitational/89f0432 | PASS→FAIL | 18.2→9.1 | -50% | 0.399→3.555 | +791% |
| internetarchive/53d376b | PASS→FAIL | 20.7→6.2 | -70% | 0.712→3.438 | +383% |
| protonmail/944adbf | PASS→FAIL | 7.8→6.6 | -15% | 0.061→0.245 | +298% |
| qutebrowser/21b426b | FAIL→FAIL | 6.8→2.7 | -60% | 0.117→0.530 | +352% |

**汇总对比（仅共同题目，相对 Full Run 1）**：

| 指标 | Full Run 1 | 本轮 | Δ |
|---|---:|---:|---:|
| 解题数 | 13/15 | 4/15 | -9 |
| 花费 ¥ | 7.64 | 37.31 | +388% |
| 平均解题时间 min | 24.7 | 8.1 | -67% |
| $/solved | 0.083 | 1.314 | +1487% |

共同题目中 14 题明显更快（墙钟降幅 >5%），1 题明显更慢。

任务集不同时解题数不可直接比。

## 5. 总结与反思

本节回答：**换用 starter 档（qwen3.6-flash）跑同一套 harness，agent 表现如何**——这是跨模型档的通用能力验证，非调参。

**结论：starter 档在本 harness 上全面劣于 standard 档，且不只是能力问题。**

- **能力**：4/15（standard run5 为 13/15）。失败的多是 standard 轻松通过的题（53d376b 5/8、2ca5dfb 2/2、5af1a22 4/11、1316e67 5/5、f327e65 4/175、a26c325 4/42），且 trace 里出现文件路径幻觉（如 `galaxy.galaxygalle`）——模型本身更弱、更旧。
- **成本**：¥37.31 ≈ standard run5（¥6.94）的 5.4 倍；$/solved **$1.31**（standard $0.075 的 17.5 倍）。根因两条叠加：(1) **qwen3.6-flash 不返回 cached_tokens（全为 None）**——前缀缓存机制在该档不可观测/不生效，每轮全量重计输入；(2) 价格倒挂——qwen3.6-flash 目录价（¥1.2/¥7.2）本就高于 qwen3.8-flash（¥0.8/¥2.7）。
- **指令遵循**：git 纪律提示词（run5 里 standard 全 15 题 0 次违反）在 starter 下**有 3/15 题违反**（git add/stash），83909bfa 因此再次 0 字节补丁——弱模型更不可靠地遵守提示词约束。

**判定**：starter 档不是「更便宜的选择」，而是「更贵且更差」——能力、成本、指令遵循三面同时劣于 standard。本 harness 的正确档位是 standard（qwen3.8-flash）；跨档实验证明档位选择本身就是关键工程变量。

## 6. 逐题结果

| 题目 | 结果 | 轮数 | 墙钟 min | 模型 min | 输入 M | 输出 K | 缓存% | 花费 ¥ |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| flipt-io/2eac0df | PASS | 98 | 15.2 | 6.1 | 2.87 | 33.3 | 0.0 | 3.689 |
| internetarchive/4a5d2a7 | PASS | 20 | 1.4 | 1.0 | 0.22 | 5.0 | 0.0 | 0.303 |
| qutebrowser/c580ebf | PASS | 99 | 11.0 | 8.5 | 2.82 | 60.4 | 0.0 | 3.820 |
| qutebrowser/de4a1c1 | PASS | 57 | 4.6 | 4.4 | 1.57 | 38.4 | 0.0 | 2.156 |
| ansible/83909bf | FAIL | 95 | 6.3 | 5.7 | 2.82 | 21.4 | 0.0 | 3.541 |
| ansible/a26c325 | FAIL | 47 | 6.8 | 5.6 | 1.03 | 73.4 | 0.0 | 1.762 |
| ansible/f327e65 | FAIL | 53 | 3.7 | 2.8 | 0.90 | 11.4 | 0.0 | 1.167 |
| element-hq/5dfde12 | FAIL | 85 | 12.1 | 8.2 | 2.26 | 64.8 | 0.0 | 3.180 |
| flipt-io/2ca5dfb | FAIL | 70 | 25.5 | 10.4 | 1.84 | 143.7 | 0.0 | 3.248 |
| future-architect/5af1a22 | FAIL | 99 | 18.5 | 6.5 | 3.14 | 25.7 | 0.0 | 3.951 |
| gravitational/1316e67 | FAIL | 77 | 5.2 | 4.4 | 2.17 | 16.9 | 0.0 | 2.727 |
| gravitational/89f0432 | FAIL | 99 | 9.1 | 5.5 | 2.86 | 17.7 | 0.0 | 3.555 |
| internetarchive/53d376b | FAIL | 90 | 6.2 | 5.7 | 2.67 | 32.4 | 0.0 | 3.438 |
| protonmail/944adbf | FAIL | 17 | 6.6 | 0.9 | 0.18 | 4.0 | 0.0 | 0.245 |
| qutebrowser/21b426b | FAIL | 26 | 2.7 | 1.9 | 0.38 | 10.6 | 0.0 | 0.530 |

## 7. 汇总指标

| 指标 | 数值 |
|---|---|
| API 调用 | 1044 次，平均 4.5 s/次 |
| Token 总量 | 输入 27.74M / 输出 558.9K（50:1），缓存命中 0.0% |
| 平均解题时间 | **8.1 min**（中位 11.0；最快 1.4，最慢 15.2） |
| 任务墙钟总和 | 2.25 h（2 并发，实际历时更短）；模型推理 1.29 h，占 57% |
| **总花费（目录价等价）** | **¥37.31 ≈ $5.26** |
| 成本构成 | 未缓存输入 ¥22.19（59%）+ 缓存输入 ¥0.00（0%）+ 输出 ¥1.51（4%） |
| 平均每题花费 | ¥2.49 |
| 平均每个胜题花费 | ¥9.33 ≈ **$1.314/solved** |
| 胜题单次成本 | 最低 ¥0.30，最高 ¥3.82 |

compaction 调用未记录 usage，成本低估可忽略。

![Cost breakdown](charts/cost_breakdown.png)

## 8. Agent 行为分析

数据源：run.jsonl 的 tool_call/tool_result 事件；思维链总结基于 agent.log 的 [thinking] 记录（run5 缺失，据工具序列重建）。重复浪费 = 工具名与参数完全相同的多余调用次数；高频重读 = 同一文件以不同行段反复读取。

| 题目 | 工具分布 | 首编辑轮 | 编辑次数 | 测试命令 | 重复浪费 | 错误结果 |
|---|---|---:|---:|---:|---:|---:|
| flipt-io/2eac0df | bash48 read25 edit23 plan5 search3 | 13 | 23 | 9 | 19 | 4/107 |
| internetarchive/4a5d2a7 | bash10 read7 plan4 edit2 search1 | 5 | 2 | 3 | 0 | 0/24 |
| qutebrowser/c580ebf | bash55 read26 plan13 edit7 search1 | 22 | 7 | 28 | 13 | 1/102 |
| qutebrowser/de4a1c1 | read29 bash21 create8 edit4 plan3 | 15 | 12 | 0 | 25 | 28/65 |
| ansible/83909bf | bash76 read17 plan12 search5 edit2 | 15 | 2 | 16 | 21 | 2/113 |
| ansible/a26c325 | read24 bash14 edit10 search7 plan2 | 30 | 10 | 0 | 7 | 9/57 |
| ansible/f327e65 | bash33 read12 search10 edit4 plan2 | 22 | 4 | 3 | 0 | 0/62 |
| element-hq/5dfde12 | read46 bash28 search17 edit12 plan9 | 36 | 14 | 5 | 20 | 3/114 |
| flipt-io/2ca5dfb | bash52 read15 create7 edit7 plan1 | 15 | 14 | 1 | 14 | 9/82 |
| future-architect/5af1a22 | bash56 read33 edit6 search4 plan2 | 29 | 8 | 3 | 16 | 1/103 |
| gravitational/1316e67 | read33 bash22 edit16 archive6 plan1 | 15 | 16 | 0 | 10 | 7/78 |
| gravitational/89f0432 | bash52 plan25 read17 edit10 search2 | 9 | 10 | 19 | 24 | 1/106 |
| internetarchive/53d376b | bash38 read31 plan13 search10 edit4 | 25 | 4 | 9 | 15 | 0/96 |
| protonmail/944adbf | bash14 read7 create2 edit1 | 8 | 3 | 3 | 1 | 0/24 |
| qutebrowser/21b426b | bash12 read9 plan3 edit3 archive3 | 9 | 3 | 6 | 2 | 0/30 |

**动作耗时（全部题目合计）**：

| 工具 | 次数 | 总耗时 min | 平均 s | 最大 s |
|---|---:|---:|---:|---:|
| bash | 531 | 45.9 | 5.2 | 180 |
| search | 60 | 0.2 | 0.2 | 6 |
| read_file | 331 | 0.0 | 0.0 | 0 |
| edit_file | 111 | 0.0 | 0.0 | 0 |
| create_file | 21 | 0.0 | 0.0 | 0 |
| read_archived_output | 14 | 0.0 | 0.0 | 0 |
| update_plan | 95 | 0.0 | 0.0 | 0 |

**压缩发生（层 1 工具归档 / 层 2 摘要压缩）**：

| 题目 | 层1 次数 | 层1 发生分布 | 层2 次数 | 层2 发生轮 |
|---|---:|---|---:|---|
| flipt-io/2eac0df | 1 | turn 66（×10） | 0 | 无 |
| internetarchive/4a5d2a7 | 0 | 无 | 0 | 无 |
| qutebrowser/c580ebf | 1 | turn 49（×12） | 0 | 无 |
| qutebrowser/de4a1c1 | 0 | 无 | 0 | 无 |
| ansible/83909bf | 1 | turn 66（×14） | 0 | 无 |
| ansible/a26c325 | 0 | 无 | 0 | 无 |
| ansible/f327e65 | 0 | 无 | 0 | 无 |
| element-hq/5dfde12 | 1 | turn 41（×20） | 1 | turn 67 |
| flipt-io/2ca5dfb | 1 | turn 59（×9） | 0 | 无 |
| future-architect/5af1a22 | 2 | turn 44（×13），turn 74（×13） | 0 | 无 |
| gravitational/1316e67 | 1 | turn 58（×11） | 0 | 无 |
| gravitational/89f0432 | 1 | turn 81（×8） | 0 | 无 |
| internetarchive/53d376b | 1 | turn 46（×17） | 0 | 无 |
| protonmail/944adbf | 0 | 无 | 0 | 无 |
| qutebrowser/21b426b | 0 | 无 | 0 | 无 |

层 1：≥40K 且距上次归档 ≥8K 触发；窗口 = 尾部 ≤8K token；原始 ≥1200 字符才归档；bash ≥2000 字符出生即预览（不计入层 1 事件）；层 2 ≥56K（config.py）。层 1 计数依赖 agent.log；层 2 来自 latency.jsonl。


![tool_calls](charts/tool_calls.png)

![tool_time_total](charts/tool_time_total.png)

![time_composition](charts/time_composition.png)

**自动标记**：

- flipt-io/2eac0df：19 次完全相同的重复调用（如 bash `cd /app && git diff --stat` ×10；bash `cd /app && git diff 2>&1` ×3）；高频重读 telemetry.go ×16（不同行段）
- qutebrowser/c580ebf：13 次完全相同的重复调用（如 plan `{"steps": [{"status": "completed", "step": "Implement widene` ×6；bash `cd /app && git diff` ×5）；首编辑迟至第 22 轮；高频重读 urlutils.py ×9（不同行段）
- qutebrowser/de4a1c1：25 次完全相同的重复调用（如 bash `` ×11；read `? [-]` ×10）；工具错误率 43%；高频重读 configinit.py ×12（不同行段）
- ansible/83909bf：21 次完全相同的重复调用（如 bash `cd /app && git diff --stat HEAD` ×5；bash `cd /app && git diff HEAD --stat` ×4）；高频重读 galaxy.py ×8（不同行段）
- ansible/a26c325：7 次完全相同的重复调用（如 edit `{}` ×8）；首编辑迟至第 30 轮；高频重读 urls.py ×13（不同行段）
- ansible/f327e65：首编辑迟至第 22 轮；高频重读 dataclasses.py ×6（不同行段）
- element-hq/5dfde12：20 次完全相同的重复调用（如 read `/app/src/utils/device/clientInformation.ts [-]` ×13；search `{"mode": "files", "path": "/app/src", "query": "SessionsAndD` ×3）；首编辑迟至第 36 轮；高频重读 clientInformation.ts ×21（不同行段）
- flipt-io/2ca5dfb：14 次完全相同的重复调用（如 read `/app/internal/metrics/metrics.go [-]` ×7；bash `` ×6）；高频重读 metrics.go ×9（不同行段）
- future-architect/5af1a22：16 次完全相同的重复调用（如 read `/app/scanner/utils.go [-]` ×8；bash `cd /app && git diff --stat` ×4）；首编辑迟至第 29 轮；高频重读 redhatbase.go ×14（不同行段）
- gravitational/1316e67：10 次完全相同的重复调用（如 archive `{"output_id": "tool_output_0028"}` ×6；read `/app/lib/events/dynamoevents/dynamoevents.go [-]` ×4）；高频重读 dynamoevents.go ×32（不同行段）
- gravitational/89f0432：24 次完全相同的重复调用（如 bash `cd /app && git diff --stat` ×7；bash `cd /app && go build ./lib/utils/... 2>&1` ×5）；高频重读 utils.go ×11（不同行段）
- internetarchive/53d376b：15 次完全相同的重复调用（如 bash `cd /app && git diff --stat` ×9；read `/app/openlibrary/mocks/mock_infobase.py [-]` ×4）；首编辑迟至第 25 轮；高频重读 mock_infobase.py ×14（不同行段）

**逐题行为与思维链总结**：


## 9. Token 用量分析

1. **输入:输出 = 50:1**：agent 负载的成本主体是反复提交上下文，而非生成。0% 的缓存命中节省约 ¥0.0；若无前缀缓存，总成本约为 ¥23.7。前缀缓存是该 harness 中价值最高的机制，compaction 策略（减少触发、触发后保持前缀稳定）直接决定成本。
2. **输出仅占 token 的 2.0%，却占成本 4%**；未缓存输入占 token 98%、占成本 59%。压缩成本的两个杠杆：减少上下文失效、控制输出长度。reasoning_effort=medium 下平均输出 535 tok/call、平均延迟 4.5 s。

![API latency distribution](charts/api_latency.png)

## 10. 失败案例分析

- ansible/83909bf：150/150 个测试未通过（官方 verifier）。根因分析见 Full Run 1 报告失败案例分析章节。
- ansible/a26c325：4/42 个测试未通过（官方 verifier）。根因分析见 Full Run 1 报告失败案例分析章节。
- ansible/f327e65：4/175 个测试未通过（官方 verifier）。根因分析见 Full Run 1 报告失败案例分析章节。
- element-hq/5dfde12：9/61 个测试未通过（官方 verifier）。根因分析见 Full Run 1 报告失败案例分析章节。
- flipt-io/2ca5dfb：2/2 个测试未通过（官方 verifier）。根因分析见 Full Run 1 报告失败案例分析章节。
- future-architect/5af1a22：4/11 个测试未通过（官方 verifier）。根因分析见 Full Run 1 报告失败案例分析章节。
- gravitational/1316e67：5/5 个测试未通过（官方 verifier）。根因分析见 Full Run 1 报告失败案例分析章节。
- gravitational/89f0432：1/1 个测试未通过（官方 verifier）。根因分析见 Full Run 1 报告失败案例分析章节。
- internetarchive/53d376b：5/8 个测试未通过（官方 verifier）。根因分析见 Full Run 1 报告失败案例分析章节。
- protonmail/944adbf：10/10 个测试未通过（官方 verifier）。根因分析见 Full Run 1 报告失败案例分析章节。
- qutebrowser/21b426b：1/28 个测试未通过（官方 verifier）。根因分析见 Full Run 1 报告失败案例分析章节。

## 11. 统计方法说明

- **每题计一次有效尝试**：通过题取首次通过的尝试；未通过题取最后一次有效评分（官方 verifier 链路）。因崩溃、OOM、编排器故障、verifier 缺陷或模型行为异常而无效的尝试不计入时间与成本。
- 尝试按 hostlog 运行 ID 分组，本报告只含本轮尝试。
- 耗时 = trace 首末事件时间差；成本按目录价逐调用累加；compaction 调用无 usage 记录，未计入。
- 任务归属按 hostlogs 任务表快照 + trace 位置序号对齐；无 results.md 的轮次以 evaluations.tsv 补充判定。
- 排行榜数据取自截止后的静态 API（作业已关闭，无新增提交）。
