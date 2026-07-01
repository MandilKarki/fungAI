import tempfile
from pathlib import Path

import pytest

from aegis_core.memory.composite_backend import CompositeBackend
from aegis_core.memory.filesystem_backend import FilesystemBackend, PathEscapeError
from aegis_core.memory.sqlite_backend import SQLiteBackend
from aegis_core.memory.state_backend import StateBackend


async def _roundtrip(backend):
    await backend.write("/a/b.txt", "hello world")
    assert await backend.read("/a/b.txt") == "hello world"
    assert "/a/b.txt" in await backend.ls("/a")
    assert await backend.grep("hello") == [("/a/b.txt", 1, "hello world")]
    await backend.edit("/a/b.txt", "hello", "goodbye")
    assert await backend.read("/a/b.txt") == "goodbye world"
    await backend.delete("/a/b.txt")
    with pytest.raises(Exception):
        await backend.read("/a/b.txt")


async def test_state_backend_roundtrip():
    await _roundtrip(StateBackend())


async def test_filesystem_backend_roundtrip(tmp_path):
    await _roundtrip(FilesystemBackend(tmp_path))


async def test_filesystem_backend_rejects_path_escape(tmp_path):
    backend = FilesystemBackend(tmp_path)
    with pytest.raises(PathEscapeError):
        backend._resolve("../../etc/passwd")


async def test_sqlite_backend_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        await _roundtrip(SQLiteBackend(Path(d) / "mem.sqlite"))


async def test_composite_backend_routes_by_prefix():
    default = StateBackend()
    durable = StateBackend()
    composite = CompositeBackend(default=default, routes={"/memory/": durable})

    await composite.write("/memory/notes.txt", "durable note")
    await composite.write("/scratch/tmp.txt", "ephemeral note")

    assert await durable.read("/memory/notes.txt") == "durable note"
    assert await default.read("/scratch/tmp.txt") == "ephemeral note"
    with pytest.raises(Exception):
        await default.read("/memory/notes.txt")
