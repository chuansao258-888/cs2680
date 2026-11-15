# CS2680 Assignment 2：madsLoop

这是一个用于学习的 coding agent：接收问题，在指定工作目录内阅读、搜索、编辑代码和运行命令，然后由脚本导出 Git 补丁。当前使用阿里云百炼的 `qwen3.6-35b-a3b`，并非课程代理；同名模型的服务配置和结果不一定一致。

## 文件位置

| 路径 | 用途 |
| --- | --- |
| `madsLoop.py` | 命令行入口，解析问题、日志开关和工作目录 |
| `src/` | Agent 循环、工具、提示词、日志及上下文管理 |
| `docker_env.sh` | 任务容器中的 Agent 依赖安装 |
| `evaluation_scripts/` | 课程任务数据、运行、补丁打包和官方评测脚本 |
| `mytest/` | 自建候选题、Dockerfile、参考补丁、测试和验证脚本 |
| `test_results/` | 历史运行日志、补丁、评测结果、备份和实验说明；默认不纳入 Git |
| `test_results/reference/project_description_assignment.md` | 本地保存的作业说明 |

`.venv-eval/` 是本地评测 Python 环境，`SWE-bench_Pro-os/` 是官方评测工具的本地检出，`.idea/` 是 PyCharm 配置；它们属于本地工具，不是提交内容。

## 实现和实验

- Agent 支持读取、搜索、编辑、创建文件、执行命令和更新计划；请求及工具活动写入 JSONL 日志。
- 当前最多运行 100 轮，每 50 轮提醒；旧工具输出压缩门槛为 20,000 prompt tokens，LLM 摘要门槛为 35,000 prompt tokens。具体行为以 `src/` 当前代码为准。
- 课程题第一次记录为 1/10；额外提供 `requirements` 和 `interface` 的一组记录为 5/10。两组运行的代码与配置并未全部固定，不能将结果差异简单归因于输入字段。详细过程、限制和每题结果见 [实验记录](test_results/EXPERIMENTS.md)。
- 四个自建候选题的 Agent 补丁通过了各自的复现和回归测试（4/4）。每题测试覆盖范围有限，不能保证覆盖所有边界；原始代码、参考补丁和 Agent 补丁的验证日志见 `mytest/results/`，分别以 `.before.log`、`.gold.log`、`.agent.log` 结尾。
- 另有缓存复制和跨模块集合两个练习候选。跨模块任务运行时 API 连接超时，存档的对应补丁为空；尚未完成有效评测。
- 自建候选目前填写了非空 `requirements` 和 `interface`，用于当前实验；作业 2.6 要求自建题的两个字段为空，并将全部描述放入一段 `problem_statement`。四道候选还没有完成“两题修改 Agent 后才解决、两题最终未解决”的分组。

## 运行与验证

在 Ubuntu / WSL 终端进入本目录后设置环境变量（不要把实际 Key 写进文件或提交）：

```bash
export CS2680_API_KEY="你的API Key"
```

运行课程第 1 题或自建候选第 1 题（索引从 0 开始）：

```bash
bash evaluation_scripts/run_task.sh 0
TASKS_JSON="$PWD/mytest/candidates.json" bash evaluation_scripts/run_task.sh 0
```

运行会调用模型并产生费用。自建题的验证脚本直接读取补丁，不调用模型：

```bash
bash mytest/verify.sh boltons__issue393 test_results/patches/model_patch_boltons__issue393.diff
```

历史补丁已归档到 `test_results/patches/`。现有脚本仍按课程约定从根目录读取或写入 `model_patch_*.diff`、`patches.json`，并在根目录生成新的 `madsLoop_logs/` 和 `pro_eval/`；重新运行后这些目录和文件会再次出现。若要重新打包归档的课程补丁，可先执行 `cp test_results/patches/model_patch_instance_*.diff .`，再运行 `python3 evaluation_scripts/make_patches.py`。评测前请确认不会覆盖尚未备份的新结果。

当前主循环未处理最终 API 连接超时；请求失败可能中断运行，已发生的模型调用仍可能计费。`mytest/verify.sh` 目前针对已准备的候选题写死验证项，尚未实现通用读取 `task_test.json`。
