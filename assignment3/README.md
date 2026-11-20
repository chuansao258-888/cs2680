# CS2680 Assignment 3 — 编码 Agent 编排实验记录

> 活文档：实验记录与版本变更史只追加；最后更新 2026-11-21。

## 1. 概览（随实验更新）

| | |
|---|---|
| 模型 | `qwen3.8-flash`（standard 档，reasoning_effort=medium），全程未用 expert |
| 任务集 | 15 题（5 道课程 v2 原题 + 10 道恢复的 A2 实例），官方 v2 verifier 评分 |
| 实验规模 | 14 轮 · 65 次任务尝试 · 4,146 次 API 调用 · 119.9M / 3.0M tokens |
| 当前成绩 | 跨轮并集 **15/15**（每题至少解出过一次）；单轮最佳仍 13/15（run4 = 11/15，字符串掷硬币此消彼长） |
| 成本 | 最新轮 $0.078/solved（run4，¥6.13/11）；缓存命中稳定在 91%（基准 83.1%） |
| 基准版本 | Full Run 1 锚定 `a731a16`（src 指纹 `3da72e34d286f470`），后续轮次逐轮登记 |

## 2. 任务集（稳定）

15 题均为 SWE-bench-Pro v2 格式（problem_statement / requirements / interface + 隐藏 f2p/p2p 测试），按官方链路 `test.sh → run_script.sh → parser.py` 评分。

| 来源 | 题目 |
|---|---|
| 课程 v2 原题（5） | internetarchive/openlibrary `53d376b` · flipt-io/flipt `2ca5dfb` · future-architect/vuls `5af1a22` · gravitational/teleport `1316e67` · element-hq/element-web `5dfde12` |
| 恢复的 A2 实例（10） | openlibrary `4a5d2a7d` · teleport `89f0432` · flipt `2eac0df` · webclients `944adbf` · qutebrowser `c580ebf` `de4a1c1` `21b426b` · ansible `f327e65` `a26c325` `83909bfa` |

其中 10 道 A2 实例已从课程数据快照中移除，其 verifier 由本项目按官方链路重建（见版本变更 V3、V4）。

## 3. 实验记录（追加式，一轮一行）

| 轮次 | 日期 | git 快照 | 模型档位 | 范围 | 结果 | 累计 | 报告 |
|---|---|---|---|---|---|---|---|
| run 1 | 11-18 18:20 | `c11ba61`+未提交 | qwen3.8-flash·xhigh | 1 题 | 7/8 测试，65 min 贴轮数上限 | 0/15 | [Full Run 1 §5](reports/full_run_1.md) |
| run 2 | 11-18 20:52 | `c11ba61`+未提交 | qwen3.8-flash·xhigh | 1 题 | 失败（上游断连） | 0/15 | 存档 |
| run 2b | 11-19 17:58 | `7b2ffa4` | qwen3.8-flash·xhigh | 1 题 | 80 轮零编辑弃单 | 0/15 | 存档 |
| run 3 | 11-19 22:35 | `7b2ffa4`+未提交 | qwen3.8-flash·medium | 1 题 | 53d376b 通过（20.7 min） | 1/15 | [Full Run 1](reports/full_run_1.md) |
| run 4 | 11-20 00:42 | `583a353` | qwen3.8-flash·medium | 全量 15 | 3/9 后 OOM 中断 | 4/15 | 存档 |
| run 5 | 11-20 03:15 | `583a353`+未提交 | qwen3.8-flash·medium | 全量 15 | 8/9 | 11/15* | 存档 |
| run 6 | 11-20 05:32 | ≈`a731a16` 内容 | qwen3.8-flash·medium | 续跑 7 | 4/7 | 11/15 | 存档 |
| run 7 | 11-20 15:53 | 同上 | qwen3.8-flash·medium | 续跑 3 | 编排器崩溃 | 11/15 | 存档 |
| run 8 | 11-20 16:16 | 同上 | qwen3.8-flash·medium | 续跑 3 | 1/3（a26c325） | **13/15** | [Full Run 1](reports/full_run_1.md) |
| 重跑 | 11-20 18:07 | `a731a16`+trace 标注 | qwen3.8-flash·medium | 失败 2 题 | 1/2（83909bfa） | **14/15** | [Full Run 2](reports/full_run_2.md) |
| full_run_3 | 11-21 02:15 | `68c363f` | qwen3.8-flash·medium | 全量 15 | 12/15（a26c325、83909bfa 回归） | **14/15** | [Full Run 3](reports/full_run_3.md) |
| full_run_4 | 11-21 02:17 | `afa3045` | qwen3.8-flash·medium | 全量 15 | 11/15（21b426b 首过；83909bfa 栽于 git-add 0 字节） | **15/15 并集** | [Full Run 4](reports/full_run_4.md) |

模型档位 = `CS2680_MODEL_STANDARD`（qwen3.8-flash）全程不变，expert/starter 从未启用；xhigh→medium 指 thinking 预算（reasoning_effort，V2 提示词同期切换，平均输出 4,515 → 840 tok/call）。

\* run5 的 8 胜含 run4 已通过题的重验；"累计"按首次通过口径。存档链接：[previous_runs/](previous_runs/)。
每轮改了什么代码、表现为什么变，登记在第 4 节版本变更史，本表不重复。

## 4. 版本变更史（追加式，最新在下）

条目模板：**Vn · 日期 · git 快照 · 变更内容 → 表现解释（证据链接）**

**V1 · 11-18 · `c11ba61`+未提交** — A2 agent 移植首跑（run 1–2）。
→ 暴露两个基线问题：xhigh thinking 下平均 4,515 tok/call、67.5s/call（成本与时延主因）；100 轮贴顶无 reason 标注。

**V2 · 11-19 · `7b2ffa4`+未提交** — 六段式提示词重写（ground-truth 段否定可靠记忆、行动偏向段）+ API 重试 + trace 抢救（run 2b→3）。
→ 同题对照：53d376b 从 65 min 失败（run1）到 20.7 min 通过（run3）；输出降至 ~505 tok/call。PR-recall 螺旋未再复现。

**V3 · 11-20 · `583a353`→`a731a16`** — 基础设施与韧性：12GB VM + 2×4g 并发（run4 OOM 后）、畸形 arguments 历史净化 + 任务异常隔离（run5 崩溃后，提交 `a731a16`）。
→ run6 一次过 4 题（均 11.9 min、¥0.28/题）；崩溃救援提交使 run7 类事故不再丢已完成的编辑。

**V4 · 11-20 · `a731a16`** — verifier 官方链路重建（run7 缺 config.json、run8 直跑 pytest 两起误判后；`tools/rebuild_official_verifiers.py`）。
→ 复评改变结论：83909bfa 从"150/150 全败"到真实 149/150、21b426b 确认 27/28——两题从"疑似基础设施问题"定性为"精确字符串近失"。

**V5 · 11-20 · `a731a16`+trace 标注** — run_end 原因标注（iteration_limit/no_choices/done/error，纯 trace 层）。
→ 行为级门禁判定：重跑与基准无行为差异，83909bfa FAIL→PASS 归因采样随机性（本次保留原报错骨架），21b426b FAIL→FAIL 且补丁与 run8 逐字节同型 → 判定确定性偏差，零反馈重跑不可修复。

**V6 · 11-20 · `68c363f`（待 run）** — 压缩重设计：层 1 批量归档（40K+8K 迟滞+8K token 窗口+水位线）、bash 出生即预览、层 2 结构化状态块；参数收敛 config.py。
→ 离线重放：批量击穿轮次 25–34 → 1–2，前缀稳定度 80–86% → 93–96%。行为级变更，预期实测指标：cache% 83→90+、未缓存输入占比 51→<40%、重复测试命令数不升（[replay_compaction.py](tools/replay_compaction.py)）。

**V7 · 11-21 · `68c363f` 实测（full_run_3）** — V6 重设计首次全量运行。
→ **成本目标全部达成**：cache% 83.1→**91.7**、总花费 ¥7.64→¥6.11（−20%）、$/solved 0.083→**0.072**、层 1 归档事件 ~190 次涓流→30 批、同参重复测试命令 43→44（无恶化）、平均解题时间持平；压缩后再校准健康（7 次摘要压缩均"重跑/定点重读"恢复，`read_archived_output` 使用 1→14 次，仅 1 次归档截断伪影且自愈）。
→ **但单轮成绩 12/15（−1）**：a26c325、83909bfa 各差 1 测试回归。读 trace 定位后**三失败均与压缩无关**，全部是精确字符串决策：21b426b 第三次逐字节相同选择（跨两个代码版本，确定性实锤）；83909bfa 措辞掷硬币再次落错面（3 样本 1 中）；a26c325 在 `Request(use_netrc=..).open(..)` 与 upstream `.open(.., use_netrc=..)` 之间选了前者（mock 断言后者）。V6 的净效应判定：成本/时延纯收益，通过率变化属字符串题方差，非压缩副作用。

**V8 · 11-21 · `afa3045` 实测（full_run_4）** — #4 read_file 源头分页 + #1 原子批量编辑首次全量运行。
→ **两项新机制均被充分采纳、无副作用**：批量编辑 133 次调用承载约 300 hunk、72 次多 hunk（2eac0df 用 8+8+7 文件批一次完成接口重命名，89f0432 两批零失败做 ioutil→io 清扫）；read 分页 281 次读取中 201 次带 start_line、90 次触发分页并往后翻页（a26c325 的 2000+ 行 urls.py 靠逐页扫描恢复通过）。成本 ¥6.13、cache 91.1% 持平——红利已被 V6 吃掉，本轮预期就是"验证采纳、不劣化"。
→ **意外收获与一处修正**：21b426b 历史首次通过（0/28），但子代理证据显示真因是**模型从任务给出的 commit 线索推断出 gold repr 格式**，非 V7 猜测的"工具改动翻转确定性决策"——V7 的"确定性实锤"结论据实修正（且它本就是 3 样本小推断）。原子校验还拦截了一次双重编码事故（1316e67 把 files 序列化成字符串被逐字符拆成 4413 伪 hunk、整批拒绝），验证了防呆。
→ **新暴露的通用 bug**：83909bfa 收尾 `git add -A` 把改动暂存，harness `git add -N . && git diff` 只抓未暂存 → 0 字节补丁、150/150 全挂（模型 thinking 已意识到 staged 却判断"留着无妨"）。修法为提示词纪律"勿 git add/stash/commit"，待落地。单轮 11/15 的下滑来自此 bug + webclients 代码库位置误判（实现放进 `packages/metrics/` 子包，gold 测试在仓库根 `tests/`，根 jest 工程 TS 转译失败 → 0 测试收集）+ 两道字符串近失，均非新工具退化。

## 5. Token 定价对标（稳定）

课程 API 三档（project_description.md L212-214）与本实验 config.py 配置的三档 Qwen 模型目录价对照（Qwen 价取阿里云百炼北京区 2026-11-20，¥ 按 7.1 折 $；每 M token）：

| 档位 | 课程模型价 输入/缓存/输出 | 本实验模型 | Qwen 目录价 输入/缓存/输出 | 折 $ | 相对课程同档 |
|---|---|---|---|---|---|
| expert | $2.00 / $0.10 / $10.00 | `qwen4-max` | ¥10 / ¥0.5 / ¥30 | $1.41 / $0.07 / $4.23 | 输入 0.70× / 输出 0.42× |
| standard | $0.08 / $0.004 / $0.28 | `qwen3.8-flash` | ¥0.8 / ¥0.1* / ¥2.7 | $0.113 / $0.014 / $0.380 | 输入 1.41× / 输出 1.36× |
| starter | $0.01 / $0.0005 / $0.04 | `qwen3.6-flash` | ¥1.2 / — / ¥7.2 | $0.169 / — / $1.014 | 输入 16.9× / 输出 25.4× |

\* flash 缓存命中价 ¥0.1/M 为本项目实测记录口径（api_cost_report.md）；qwen4-max 缓存命中 ¥0.5/M 为官方明示。

- **主力档（standard）**：flash 单价略高于课程 standard（约 1.4 倍），介于课程 standard 与 expert 之间；等效榜上"standard 主力、零 expert"路线。
- **expert 档反而便宜**：qwen4-max 目录价只有课程 expert 的 4–7 折——本实验的单价结构对高档位调用并不苛刻（虽全程未用）。
- **starter 档倒挂**：`qwen3.6-flash` 比 `qwen3.8-flash` 贵 1.5–2.7 倍（新一代 flash 降价），且是课程 starter 价的 17–25 倍——配置里的 starter 档实际是最不经济选择，全程未用是正确决策。
- 同一批 token 按课程 standard 价重算：Full Run 1 = $0.63 → **$0.048/solved**（Qwen 口径 $0.083），成本结论对单价体系不敏感。
- 缓存折扣率：课程 standard 5%、qwen4-max 5%、qwen3.8-flash 12.5%——两种体系下前缀缓存都是第一成本杠杆，单 M 命中节省 $0.076（课程 standard）vs $0.099（flash），量级相近。

## 6. 结果与报告索引（追加式）

| 报告 | 覆盖 | 要点 |
|---|---|---|
| [Full Run 1](reports/full_run_1.md) | 15 题基准 | 13/15、¥7.64、行为分析、失败根因、排行榜对比 |
| [Full Run 2](reports/full_run_2.md) | 2 题重跑 | 83909bfa 通过 → 14/15；采样噪声判定示范 |
| [Full Run 3](reports/full_run_3.md) | 15 题（V6 实测） | 12/15、¥6.11、cache 91.7%、两回归根因、逐题行为总结 |
| [Full Run 4](reports/full_run_4.md) | 15 题（#4+#1 实测） | 11/15、¥6.13、新机制采纳证据、git-add 0 字节 bug、21b426b 首过 |
| [图表目录](reports/charts/) | 全部轮次 | 时间构成/工具分布/动作耗时/延迟分布/成本构成 |
| [run_snapshots.json](reports/run_snapshots.json) | 逐轮 | 版本指纹+配置+逐题指标（对比锚点） |
| [tmp_all_attempts.json](reports/tmp_all_attempts.json) | 全量 | 35 次尝试明细 |
| [previous_runs/](previous_runs/) | 全量 | 历轮 trace/补丁/评分存档 |

**排行榜对照**（64 份提交、26 题、已截止）：$0.083/solved 低于除 5 份外的全部提交；每解 1482s 慢于全部提交（2 并发 vs 榜上 5 并发）。任务集不同，解题率不可直接换算。

## 7. 结论（随证据修订，修订时保留旧版判断的痕迹）

1. **成本处于头部、速度是短板**：medium thinking + 高缓存命中 + 零 expert 的成本路线在两种定价口径下都优于 ~90% 榜上提交；速度受本地并发与无轮数预算约束。（11-20 立；V7 实测 $/solved 进一步至 0.072）
2. **失败题根因是规格歧义 × 精确匹配评分**：83909bfa 措辞、a26c325 调用位置、webclients 导出形式均为精确字符串掷硬币（单样本命中率高方差大）。（11-21 修订：V5/V7 曾判 21b426b 的 repr 偏差为"跨版本确定性、零反馈不可修复"，V8 被推翻——第四次尝试靠任务给出的 commit 线索推断出 gold 格式并通过；教训是"确定性"结论受小样本支配，且单次行为变化归因于机制前必须查 thinking 证据。曾提议的"近 miss 重试"利用评分反馈被否决——属刷题机制，与设计红线相悖；此类方差视为基准固有噪声，通过率以多轮并集口径评估，run1–4 并集已覆盖 15/15。）
3. **上下文经济学是第一成本杠杆——已验证**：（11-20 立论时旧压缩每轮击穿缓存致未缓存输入占 51%；V7 实测批量+水位线+出生预览把 cache% 提到 91.7%、成本 −20%，且通过率变化经 trace 定位与压缩无关。原"待验证"结论兑现为设计正确。）

## 8. 方法与局限性声明（稳定）

- Full Run 1 为开发迭代各轮最优尝试的等价单轮汇总（报告 §8 声明口径），非一次连续运行；跨轮对比以 git 快照标注版本差异，行为级变更才做性能归因（trace/显示层改动记入 BEHAVIOR_EQUIV，结果差异归采样噪声）。
- 本地 15 题集不含课程隐藏最难题，解题率与榜上 26 题不可换算；成本口径与榜上同形但单价体系不同，只比数量级。
- 本实验未选课、未使用课程 API、未提交排行榜；计费为目录价等价（实际走订阅额度）。
- **设计红线（11-21 定）**：目标是通用 coding agent，不是刷题 agent。不引入利用评测协议反馈的机制（如基于隐藏测试计数/结果的定向重试、按题定制提示）；优化只面向通用工程质量——上下文管理、提示词、可靠性、成本时延。基准上的单轮通过率是这种工程质量的观测值，而非优化目标本身。

## 9. 维护规程

每轮实验结束后，按顺序：

1. `python3 tools/aggregate_runs.py` 生成/更新 `reports/full_run_<N>.md`（口径、分组、版本锚定规则见其 docstring 规范）；
2. 阅读本轮 trace 与上轮差异，**追加**第 3 节一行、第 4 节一条 V 条目（写清：git 快照、变更内容、表现解释、证据链接）；人工分析文字（行为总结、失败根因）按 aggregate 规范补入脚本常量；
3. 更新第 1 节概览数字与"最后更新/当前状态/下一个实验"行；
4. 结论（第 7 节）只有被新证据推翻时才改写，且保留旧判断的修订痕迹（如"V5 曾认为…，V7 修正为…"）；
5. 只追加不重写：实验记录与版本变更史是账本，历史行有错用"修订:"附注，不抹掉。
