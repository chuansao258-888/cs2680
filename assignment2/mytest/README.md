# 自建任务候选

四个候选均来自 [boltons](https://github.com/mahmoud/boltons) 的真实 issue 和已合并修复；`candidates.json` 是 Agent 可读的单段题目，`task_test.json` 和 `tasks/*/test_cases.py` 是独立验证数据，不传给 Agent。测试使用 Python 标准库 `unittest`，不需要测试时联网。

按当前实验设置，`candidates.json` 的 `requirements` 和 `interface` 也包含题目信息，并由 `run_task.sh` 传给 Agent。这与作业 2.6 节要求这两个字段为空字符串的提交格式不同；正式按作业格式整理时需要将信息放回 `problem_statement` 并清空这两个字段。

| 候选 | 上游 issue | 上游修复 |
|---|---|---|
| `boltons__issue393` | [#393](https://github.com/mahmoud/boltons/issues/393) | [PR #394](https://github.com/mahmoud/boltons/pull/394) |
| `boltons__issue297` | [#297](https://github.com/mahmoud/boltons/issues/297) | [PR #302](https://github.com/mahmoud/boltons/pull/302) |
| `boltons__issue235` | [#235](https://github.com/mahmoud/boltons/issues/235) | [PR #234](https://github.com/mahmoud/boltons/pull/234) |
| `boltons__issue241` | [#241](https://github.com/mahmoud/boltons/issues/241) | [PR #245](https://github.com/mahmoud/boltons/pull/245) |

每题的 `gold.diff` 是上游合并修复的源码差异，不能提供给 Agent。镜像构建时联网下载 Git 和指定的修复前版本，不包含后续 Git 历史；验证时使用 `--network none`，不需要联网。

固定的修复前版本和上游修复版本记录在 `provenance.json`。运行 `bash mytest/check_references.sh` 可对四题重新生成基线日志和参考补丁验证日志，分别保存在 `results/*.before.log` 和 `results/*.gold.log`。

在仓库根目录执行：

```bash
docker build -t cs2680-mytest/boltons__issue393:v1 mytest/tasks/boltons__issue393
bash mytest/verify.sh boltons__issue393
bash mytest/verify.sh boltons__issue393 mytest/tasks/boltons__issue393/gold.diff
```

无补丁的基线应显示 `unresolved`；参考补丁应显示 `RESOLVED`。要运行 Agent，可用 `TASKS_JSON="$PWD/mytest/candidates.json" bash evaluation_scripts/run_task.sh 0`，再把生成的补丁传给 `verify.sh`。题号按 `candidates.json` 的顺序从 0 开始。

这些题暂时是候选，尚不能声称满足课程规定的“两道改进后解决、两道仍未解决”；需要先对当前 Agent 和改进后的 Agent 分别实测，再根据结果分类。检索未发现这些 issue 属于已知 Agent benchmark，但无法证明所有公开或未公开基准中均不存在，应继续核对。
