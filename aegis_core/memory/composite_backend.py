"""Routes paths to different backends by prefix.

The cleanest idea found in the sources studied: one BackendProtocol surface,
no special-casing in callers, policy lives entirely in the routing table.
Typical use: `/memory/` -> a durable FilesystemBackend, everything else ->
an ephemeral StateBackend. aegis_sentinel uses this to route `/cases/*` to
durable storage while scratch analysis files stay ephemeral.
"""

from __future__ import annotations

from dataclasses import dataclass

from aegis_core.memory.backend import BackendProtocol


@dataclass
class _Route:
    prefix: str
    backend: BackendProtocol


class CompositeBackend:
    def __init__(self, default: BackendProtocol, routes: dict[str, BackendProtocol] | None = None):
        self.default = default
        # Longest-prefix-first so the most specific route always wins.
        self._routes = sorted(
            (_Route(prefix, backend) for prefix, backend in (routes or {}).items()),
            key=lambda r: len(r.prefix),
            reverse=True,
        )

    def add_route(self, prefix: str, backend: BackendProtocol) -> None:
        self._routes.append(_Route(prefix, backend))
        self._routes.sort(key=lambda r: len(r.prefix), reverse=True)

    def _backend_for(self, path: str) -> BackendProtocol:
        for route in self._routes:
            if path.startswith(route.prefix):
                return route.backend
        return self.default

    async def ls(self, path: str = "/") -> list[str]:
        return await self._backend_for(path).ls(path)

    async def read(self, path: str) -> str:
        return await self._backend_for(path).read(path)

    async def write(self, path: str, content: str) -> None:
        await self._backend_for(path).write(path, content)

    async def edit(self, path: str, old: str, new: str) -> None:
        await self._backend_for(path).edit(path, old, new)

    async def delete(self, path: str) -> None:
        await self._backend_for(path).delete(path)

    async def grep(self, pattern: str, path: str = "/") -> list[tuple[str, int, str]]:
        return await self._backend_for(path).grep(pattern, path)

    async def glob(self, pattern: str) -> list[str]:
        # Glob fans out to every distinct backend since a pattern may span routes.
        seen: dict[str, None] = {}
        backends = {id(r.backend): r.backend for r in self._routes}
        backends[id(self.default)] = self.default
        for backend in backends.values():
            for match in await backend.glob(pattern):
                seen[match] = None
        return sorted(seen.keys())
