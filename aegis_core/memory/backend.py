"""BackendProtocol: one interface for all agent-visible state.

Source: deepagents' BackendProtocol — the single best idea found across the
sources studied. Scratch files, large tool results that would otherwise blow
the context budget, durable memory, cross-agent shared case data: all of it
goes through the same `ls/read/write/edit/delete/grep/glob` surface, with the
*policy* (ephemeral vs. durable vs. routed-by-prefix) living in which backend
implementation is plugged in, not in the tools that use it. See
ARCHITECTURE.md section 6.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class NotFoundError(Exception):
    pass


@runtime_checkable
class BackendProtocol(Protocol):
    async def ls(self, path: str = "/") -> list[str]: ...

    async def read(self, path: str) -> str: ...

    async def write(self, path: str, content: str) -> None: ...

    async def edit(self, path: str, old: str, new: str) -> None: ...

    async def delete(self, path: str) -> None: ...

    async def grep(self, pattern: str, path: str = "/") -> list[tuple[str, int, str]]:
        """Returns (file_path, line_number, line_text) tuples."""
        ...

    async def glob(self, pattern: str) -> list[str]: ...
