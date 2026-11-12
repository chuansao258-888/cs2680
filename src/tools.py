from pathlib import Path

# TOOLS DESCRIPTION
READ_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a text file inside the working directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file, relative to the working directory.",
                },
            },
            "required": ["path"],
        },
    },
}

SEARCH_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "search_file",
        "description": "Search for text in a file inside the working directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file, relative to the working directory.",
                },
                "keyword": {
                    "type": "string",
                    "description": "Text to search for.",
                },
            },
            "required": ["path", "keyword"],
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

def read_file(workdir, path):
    root = Path(workdir).resolve()
    file_path = (root / path).resolve()

    if not file_path.is_relative_to(root):
        return "Error: path is outside the working directory."

    if not file_path.is_file():
        return "Error: file does not exist or is not a regular file."
    try:
        return file_path.read_text(encoding="utf-8")
    except OSError as error:
        return f"Error reading file: {error}"
    except UnicodeError:
        return "Error: file is not valid UTF-8 text."

def search_file(workdir, path, keyword):
    content = read_file(workdir, path)

    if content.startswith("Error:") or content.startswith("Error reading file:"):
        return content

    matches = []

    for line_number, line in enumerate(content.splitlines(), start=1):
        if keyword in line:
            matches.append(f"{line_number}: {line}")

    if not matches:
        return "No matches found."

    return "\n".join(matches)

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