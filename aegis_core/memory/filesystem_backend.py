"""Real-disk backend, rooted and path-traversal-safe.

Every path is resolved against `root` and verified to stay inside it before
any I/O — deliberately strict, since this backend is the one most likely to
be handed agent-controlled paths (including, for aegis_sentinel, paths
derived from untrusted ingested data).
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from aegis_core.memory.backend import NotFoundError


class PathEscapeError(Exception):
    pass


class FilesystemBackend:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        candidate = (self.root / path.lstrip("/")).resolve()
        if self.root not in candidate.parents and candidate != self.root:
            raise PathEscapeError(f"{path!r} escapes backend root")
        return candidate

    async def ls(self, path: str = "/") -> list[str]:
        target = self._resolve(path)
        if not target.exists():
            return []
        if target.is_file():
            return [path]
        # Leading "/" is added back deliberately: every other BackendProtocol
        # implementation (State/SQLite/Composite) returns paths exactly as
        # written, leading slash included. Without this, glob()/grep() calls
        # using absolute-style patterns (e.g. "/alerts/*.json", used
        # throughout aegis_sentinel's tools) would silently never match here.
        return sorted(
            "/" + p.relative_to(self.root).as_posix()
            for p in target.rglob("*")
            if p.is_file()
        )

    async def read(self, path: str) -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise NotFoundError(path)
        return target.read_text(encoding="utf-8", errors="replace")

    async def write(self, path: str, content: str) -> None:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    async def edit(self, path: str, old: str, new: str) -> None:
        current = await self.read(path)
        if old not in current:
            raise ValueError(f"text to replace not found in {path!r}")
        await self.write(path, current.replace(old, new, 1))

    async def delete(self, path: str) -> None:
        target = self._resolve(path)
        if target.is_file():
            target.unlink()

    async def grep(self, pattern: str, path: str = "/") -> list[tuple[str, int, str]]:
        regex = re.compile(pattern)
        matches: list[tuple[str, int, str]] = []
        for rel_path in await self.ls(path):
            content = await self.read(rel_path)
            for line_no, line in enumerate(content.splitlines(), start=1):
                if regex.search(line):
                    matches.append((rel_path, line_no, line))
        return matches

    async def glob(self, pattern: str) -> list[str]:
        return sorted(p for p in await self.ls("/") if fnmatch.fnmatch(p, pattern))
