"""SQLite-backed BackendProtocol implementation — a durable virtual
filesystem that survives process restarts. See ROADMAP.md.

sqlite3 is synchronous; every call is offloaded via asyncio.to_thread so this
backend is a drop-in async BackendProtocol alongside State/Filesystem/
Composite, usable anywhere those are (including as a CompositeBackend route
target for e.g. `/memory/` -> durable, everything else -> ephemeral).
"""

from __future__ import annotations

import asyncio
import fnmatch
import re
import sqlite3
import time
from pathlib import Path

from aegis_core.memory.backend import NotFoundError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


class SQLiteBackend:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ls_sync(self, path: str) -> list[str]:
        conn = self._connect()
        try:
            if path in ("/", ""):
                rows = conn.execute("SELECT path FROM files ORDER BY path").fetchall()
            else:
                rows = conn.execute(
                    "SELECT path FROM files WHERE path LIKE ? ORDER BY path",
                    (path.rstrip("/") + "/%",),
                ).fetchall()
            return [r["path"] for r in rows]
        finally:
            conn.close()

    def _read_sync(self, path: str) -> str:
        conn = self._connect()
        try:
            row = conn.execute("SELECT content FROM files WHERE path = ?", (path,)).fetchone()
            if row is None:
                raise NotFoundError(path)
            return row["content"]
        finally:
            conn.close()

    def _write_sync(self, path: str, content: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO files (path, content, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET content = excluded.content, "
                "updated_at = excluded.updated_at",
                (path, content, time.time()),
            )
            conn.commit()
        finally:
            conn.close()

    def _delete_sync(self, path: str) -> None:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM files WHERE path = ?", (path,))
            conn.commit()
        finally:
            conn.close()

    def _glob_sync(self, pattern: str) -> list[str]:
        return sorted(p for p in self._ls_sync("/") if fnmatch.fnmatch(p, pattern))

    async def ls(self, path: str = "/") -> list[str]:
        return await asyncio.to_thread(self._ls_sync, path)

    async def read(self, path: str) -> str:
        return await asyncio.to_thread(self._read_sync, path)

    async def write(self, path: str, content: str) -> None:
        await asyncio.to_thread(self._write_sync, path, content)

    async def edit(self, path: str, old: str, new: str) -> None:
        current = await self.read(path)
        if old not in current:
            raise ValueError(f"text to replace not found in {path!r}")
        await self.write(path, current.replace(old, new, 1))

    async def delete(self, path: str) -> None:
        await asyncio.to_thread(self._delete_sync, path)

    async def grep(self, pattern: str, path: str = "/") -> list[tuple[str, int, str]]:
        regex = re.compile(pattern)
        matches: list[tuple[str, int, str]] = []
        for p in await self.ls(path):
            content = await self.read(p)
            for line_no, line in enumerate(content.splitlines(), start=1):
                if regex.search(line):
                    matches.append((p, line_no, line))
        return matches

    async def glob(self, pattern: str) -> list[str]:
        return await asyncio.to_thread(self._glob_sync, pattern)
