import json
import os
from openai import OpenAI

from src.logging_utils import create_log_file, write_event
from src.tools import READ_FILE_TOOL, read_file, SEARCH_FILE_TOOL, search_file, EDIT_FILE_TOOL, edit_file


def run_agent(problem, workdir, enable_log):
    log_file = None
    if enable_log:
        log_file = create_log_file()
        write_event(
            log_file,
            "run_start",
            model_id="qwen3.6-35b-a3b",
            workdir=str(workdir),
        )
    client = OpenAI(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        base_url="https://cn-beijing.maas.qianwenaiapi.com/compatible-mode/v1",
    )

    messages = [
        {"role": "system", "content": "You are a coding agent, help me to solve the problem."},
        {"role": "user", "content": problem}
    ]

    end_reason = "iteration_limit"
    num_iterations = 0
    #对话轮数
    for iteration in range(10):
        num_iterations = iteration + 1
        completion = client.chat.completions.create(
            model="qwen3.6-35b-a3b",
            messages=messages,
            tools=[READ_FILE_TOOL, SEARCH_FILE_TOOL, EDIT_FILE_TOOL],
            extra_body={"enable_thinking": True},
            stream=True,
            stream_options={"include_usage": True},
        )
        collected_tool_calls = {}

        for chunk in completion:
            if chunk.usage is not None and log_file is not None:
                write_event(log_file,
                            "api_request",
                            iteration = iteration+1,
                            prompt_tokens = chunk.usage.prompt_tokens,
                            completion_tokens = chunk.usage.completion_tokens,
                            total_tokens = chunk.usage.total_tokens,
                )


            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta
            if delta.tool_calls:
                for tool_calls in delta.tool_calls:
                    index = tool_calls.index
                    #一个字典结构用于储存一次对话中的多个工具调用
                    if index not in collected_tool_calls:
                        collected_tool_calls[index] = {
                            "id": "",
                            "name": "",
                            "arguments": "",
                        }
                    current = collected_tool_calls[index]

                    if tool_calls.id:
                        current["id"] += tool_calls.id

                    if tool_calls.function:
                        if tool_calls.function.name:
                            current["name"] += tool_calls.function.name

                        if tool_calls.function.arguments:
                            current["arguments"] += tool_calls.function.arguments

                # print("\nTool call fragment:", delta.tool_calls)


            if delta.content:
                print(delta.content, end="", flush=True)

        # print("\nCollected tool calls:", collected_tool_calls)

        if collected_tool_calls:
            assistant_calls = []

            # 第一遍：整理这一轮所有工具请求
            for index, call in collected_tool_calls.items():
                assistant_calls.append({
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": call["arguments"],
                    },
                })

            # 所有请求放入同一条 assistant 消息
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": assistant_calls,
            })

            # 第二遍：分别执行，并添加对应结果
            for index, call in collected_tool_calls.items():
                if log_file is not None:
                    try:
                        log_arguments = json.loads(call["arguments"])
                    except json.JSONDecodeError:
                        log_arguments = call["arguments"]
                    write_event(log_file,
                                "tool_call",
                                iteration = iteration+1,
                                tool_name = call["name"],
                                arguments = log_arguments,
                    )

                #工具调用
                try:
                    arguments = json.loads(call["arguments"])

                    if call["name"] == "read_file":
                        result = read_file(workdir, arguments["path"])

                    elif call["name"] == "search_file":
                        result = search_file(
                            workdir,
                            arguments["path"],
                            arguments["keyword"],
                        )
                    elif call["name"] == "edit_file":
                        result = edit_file(
                            workdir,
                            arguments["path"],
                            arguments["old_string"],
                            arguments["new_string"],
                        )

                    else:
                        result = f"Error: unknown tool '{call['name']}'."

                except json.JSONDecodeError:
                    result = "Error: tool arguments are not valid JSON."

                except (KeyError, TypeError):
                    result = "Error: the arguments must contain a path."
                if log_file is not None:
                    write_event(log_file,
                                "tool_result",
                                iteration = iteration+1,
                                tool_name = call["name"],
                                result = result,
                                is_error = result.startswith("Error:")
                                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result,
                })
                # print("\nFile content:", result)
        else:
            end_reason = "no_tool_calls"
            break
    if log_file is not None:
        write_event(log_file,
                    "run_end",
                    reason = end_reason,
                    num_iterations = num_iterations,)

