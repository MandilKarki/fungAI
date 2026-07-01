"""In-process, ephemeral backend — the default for short-lived agent runs.

Files live in a plain dict for the lifetime of the AgentState/session; nothing
persists across process restarts. Cheapest backend, no I/O.
"""

from __future__ import annotations

import fnmatch
import re

from aegis_core.memory.backend import NotFoundError


class StateBackend:
    def __init__(self, initial: dict[str, str] | None = None):
        self._files: dict[str, str] = dict(initial or {})

    async def ls(self, path: str = "/") -> list[str]:
        prefix = path if path.endswith("/") else path + "/"
        if path in ("/", ""):
            return sorted(self._files.keys())
        return sorted(p for p in self._files if p.startswith(prefix))

    async def read(self, path: str) -> str:
        try:
            return self._files[path]
        except KeyError:
            raise NotFoundError(path) from None

    async def write(self, path: str, content: str) -> None:
        self._files[path] = content

    async def edit(self, path: str, old: str, new: str) -> None:
        current = await self.read(path)
        if old not in current:
            raise ValueError(f"text to replace not found in {path!r}")
        self._files[path] = current.replace(old, new, 1)

    async def delete(self, path: str) -> None:
        self._files.pop(path, None)

    async def grep(self, pattern: str, path: str = "/") -> list[tuple[str, int, str]]:
        regex = re.compile(pattern)
        prefix = "" if path in ("/", "") else path
        matches: list[tuple[str, int, str]] = []
        for file_path, content in self._files.items():
            if prefix and not file_path.startswith(prefix):
                continue
            for line_no, line in enumerate(content.splitlines(), start=1):
                if regex.search(line):
                    matches.append((file_path, line_no, line))
        return matches

    async def glob(self, pattern: str) -> list[str]:
        return sorted(p for p in self._files if fnmatch.fnmatch(p, pattern))
