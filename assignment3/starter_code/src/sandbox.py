"""HTTP client for the task sandbox server."""

import json
import time
import urllib.error
import urllib.request


HTTP_TIMEOUT_MARGIN_S = 30
CONNECT_RETRIES = 3


class SandboxError(Exception):
    """The sandbox cannot be reached or returned an unexpected failure."""


class SandboxPathError(Exception):
    """The sandbox refused a model-supplied path or request."""


class Sandbox:
    def __init__(self, url: str, workdir: str):
        if not url:
            raise SandboxError("task has no sandbox_url")
        self.url = url.rstrip("/")
        self.workdir = workdir

    def __repr__(self):
        return f"Sandbox({self.url!r}, {self.workdir!r})"

    def _post(self, route: str, body: dict, http_timeout: float) -> dict:
        data = json.dumps(body).encode()
        request = urllib.request.Request(
            self.url + route,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        for attempt in range(CONNECT_RETRIES):
            try:
                with urllib.request.urlopen(request, timeout=http_timeout) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                try:
                    message = json.load(error).get("error", "")
                except Exception:
                    message = ""
                if error.code == 400:
                    raise SandboxPathError(message or "bad request")
                raise SandboxError(f"sandbox returned HTTP {error.code}: {message}")
            except urllib.error.URLError as error:
                if isinstance(error.reason, ConnectionRefusedError) and attempt < CONNECT_RETRIES - 1:
                    time.sleep(1)
                    continue
                raise SandboxError(f"cannot reach sandbox {self.url}: {error.reason}")
            except (ConnectionError, TimeoutError, OSError) as error:
                raise SandboxError(f"cannot reach sandbox {self.url}: {error}")
        raise SandboxError(f"cannot reach sandbox {self.url}")

    def health(self) -> dict:
        with urllib.request.urlopen(self.url + "/health", timeout=10) as response:
            return json.load(response)

    def exec(self, command: str, timeout_s: int, cwd: str | None = None) -> dict:
        return self._post(
            "/exec",
            {"command": command, "timeout_s": int(timeout_s), "cwd": cwd or self.workdir},
            http_timeout=int(timeout_s) + HTTP_TIMEOUT_MARGIN_S,
        )

    def read_text(self, path: str, start_line: int = 1,
                  end_line: int | None = None) -> str:
        return self._post(
            "/read",
            {"path": path, "cwd": self.workdir, "start_line": start_line, "end_line": end_line},
            http_timeout=60,
        )["content"]

    def write_text(self, path: str, content: str) -> bool:
        return self._post(
            "/write",
            {"path": path, "content": content, "cwd": self.workdir},
            http_timeout=60,
        )["existed"]

    def edit_text(self, path: str, old_string: str, new_string: str) -> dict:
        return self._post(
            "/edit",
            {"path": path, "old_string": old_string, "new_string": new_string,
             "cwd": self.workdir},
            http_timeout=60,
        )

    def search_text(self, mode: str, query: str, path: str = ".") -> str:
        return self._post(
            "/search",
            {"mode": mode, "query": query, "path": path, "cwd": self.workdir},
            http_timeout=60,
        )["content"]
