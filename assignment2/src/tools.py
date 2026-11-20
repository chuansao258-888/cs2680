import os
import subprocess
from pathlib import Path

# TOOLS DESCRIPTION
READ_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read numbered lines from a text file, up to 100 lines and 20000 characters per call.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file, relative to the working directory.",
                },
                "start_line": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "First line to read, starting at 1; defaults to 1.",
                },
                "end_line": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Last line to read, inclusive; defaults to the end of the file.",
                },
            },
            "required": ["path"],
        },
    },
}

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search",
        "description": "Find file names or search text within a file or directory,literal text, not regular expressions.",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["files", "text"],
                    "description": "Search file names (files) or file contents (text).",
                },
                "query": {
                    "type": "string",
                    "description": "Text to search for.",
                },
                "path": {
                    "type": "string",
                    "description": "Optional search location relative to the working directory; defaults to the root.",
                },
            },
            "required": ["mode", "query"],
        },
    },
}

EDIT_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": "Replace one exact occurrence of text in a file.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to the working directory.",
                },
                "old_string": {
                    "type": "string",
                    "description": "Exact text to replace.",
                },
                "new_string": {
                    "type": "string",
                    "description": "Text to use as the replacement.",
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
}

CREATE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "create_file",
        "description": "Create a new text file inside the working directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path of the new file, relative to the working directory.",
                },
                "content": {
                    "type": "string",
                    "description": "Text to write into the new file.",
                },
            },
            "required": ["path", "content"],
        },
    },
}

BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Run a Bash command in the working directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The Bash command to execute.",
                },
            },
            "required": ["command"],
        },
    },
}

UPDATE_PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "update_plan",
        "description": (
            "Create a short plan at the start of the task and update it as work "
            "progresses or the approach changes. Provide the complete current "
            "plan each time. Mark a step completed only when tool results support it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "description": "The current plan as an ordered list of steps.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step": {
                                "type": "string",
                                "description": "A short description of the step.",
                            },
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                                "description": "pending: not started; in_progress: actively working; completed: finished based on tool results.",
                            },
                        },
                        "required": ["step", "status"],
                    },
                },
            },
            "required": ["steps"],
        },
    },
}


def read_file(workdir, path, start_line=1, end_line=None):
    if type(start_line) is not int or start_line < 1:
        return "Error: start_line must be a positive integer."

    if end_line is not None:
        if type(end_line) is not int or end_line < start_line:
            return "Error: end_line must be an integer >= start_line."

    root = Path(workdir).resolve()
    file_path = (root / path).resolve()

    if not file_path.is_relative_to(root):
        return "Error: path is outside the working directory."

    if not file_path.is_file():
        return "Error: file does not exist or is not a regular file."

    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()

        if start_line > len(lines):
            return "Error: start_line is beyond the end of the file."

        # 每次最多返回 100 行
        last_line = min(end_line or len(lines), len(lines), start_line + 99)

        selected_lines = []

        for line_number in range(start_line, last_line + 1):
            line = lines[line_number - 1]
            selected_lines.append(f"{line_number}: {line}")

        result = "\n".join(selected_lines)

        # 避免单行非常长时仍然产生过大的输出
        if len(result) > 20000:
            return result[:20000] + "\n[Output truncated.]"

        if last_line < len(lines) and (end_line is None or last_line < end_line):
            result += "\n[More lines available; request the next range.]"

        return result

    except OSError as error:
        return f"Error reading file: {error}"
    except UnicodeError:
        return "Error: file is not valid UTF-8 text."
def edit_file(workdir, path, old_string, new_string):
    root = Path(workdir).resolve()
    file_path = (root / path).resolve()

    if not file_path.is_relative_to(root):
        return "Error: path is outside the working directory."

    if not file_path.is_file():
        return "Error: file does not exist or is not a regular file."

    try:
        content = file_path.read_text(encoding="utf-8")

        count = content.count(old_string)

        if count != 1:
            return f"Error: old_string appeared {count} times; expected exactly once."

        updated_content = content.replace(old_string, new_string, 1)
        file_path.write_text(updated_content, encoding="utf-8")

        return "File updated successfully."

    except OSError as error:
        return f"Error editing file: {error}"
    except UnicodeError:
        return "Error: file is not valid UTF-8 text."

def create_file(workdir, path, content):
    root = Path(workdir).resolve()
    file_path = (root / path).resolve()
    if not file_path.is_relative_to(root):
        return "Error: path is outside the working directory."
    try:
        with file_path.open("x", encoding="utf-8") as file:
            file.write(content)

        return "File created successfully."

    except FileExistsError:
        return "Error: file already exists."

    except OSError as error:
        return f"Error creating file: {error}"

def search(workdir, mode, query, path="."):
    if mode not in ("files", "text"):
        return "Error: mode must be 'files' or 'text'."
    if not isinstance(query, str) or not query:
        return "Error: query must be a non-empty string."
    if not isinstance(path, str):
        return "Error: path must be a string."

    root = Path(workdir).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        return "Error: path is outside the working directory."
    if not target.exists():
        return "Error: search path does not exist."
    if mode == "files" and not target.is_dir():
        return "Error: files mode requires a directory."
    if not target.is_dir() and not target.is_file():
        return "Error: search path must be a file or directory."

    matches = []
    def candidates():
        if target.is_file():
            yield target
            return
        for directory, folders, files in os.walk(target, followlinks=False):
            folders[:] = sorted(folder for folder in folders if folder != ".git")
            for filename in sorted(files):
                yield Path(directory) / filename

    for file_path in candidates():
        try:
            if not file_path.resolve().is_relative_to(root) or not file_path.is_file():
                continue
            relative_path = file_path.relative_to(root)
            if mode == "files":
                if query in file_path.name:
                    matches.append(str(relative_path))
            else:

                if file_path.stat().st_size > 1_000_000:
                    continue
                content = file_path.read_text(encoding="utf-8")
                if "\x00" in content:
                    continue
                for line_number, line in enumerate(content.splitlines(), start=1):
                    if query in line:
                        matches.append(f"{relative_path}:{line_number}: {line[:500]}")
                        if len(matches) >= 100:
                            return "\n".join(matches) + "\n[Results limited to 100 matches.]"
        except (OSError, UnicodeError):
            if target.is_file():
                return "Error: unable to read search file as UTF-8 text."
            continue
        if len(matches) >= 100:
            return "\n".join(matches) + "\n[Results limited to 100 matches.]"

    return "\n".join(matches) if matches else "No matches found."


def run_bash(workdir, command):
    command_env = os.environ.copy()
    command_env.pop("CS2680_API_KEY", None)

    try:
        completed = subprocess.run(
            command,
            shell=True,
            executable="/bin/bash",
            cwd=workdir,
            env=command_env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        return (
            f"Exit code: {completed.returncode}\n"
            f"Stdout:\n{completed.stdout}\n"
            f"Stderr:\n{completed.stderr}"
        )

    except subprocess.TimeoutExpired:
        return "Error: command timed out after 60 seconds."

    except OSError as error:
        return f"Error running command: {error}"


def validate_plan(steps):
    if not isinstance(steps, list) or not steps:
        return "Error: steps must be a non-empty list."

    for item in steps:
        if not isinstance(item, dict):
            return "Error: each step must be an object."

        step = item.get("step")
        status = item.get("status")

        if not isinstance(step, str) or not step.strip():
            return "Error: step must be a non-empty string."

        if status not in ("pending", "in_progress", "completed"):
            return "Error: invalid step status."

    return None
