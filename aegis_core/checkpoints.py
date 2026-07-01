"""Checkpoint/snapshot middleware: before a destructive tool call mutates a
file, snapshot its prior content through the memory backend so it can be
rolled back. See ROADMAP.md / ARCHITECTURE.md section 9.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field

from aegis_core.memory.backend import BackendProtocol
from aegis_core.middleware import Middleware
from aegis_core.state import AgentState

DEFAULT_DESTRUCTIVE_TOOLS = {
    "write_file": "path",
    "edit_file": "path",
    "delete_file": "path",
}


@dataclass
class CheckpointMiddleware(Middleware):
    memory: BackendProtocol
    # Tool name -> which argument key on that tool holds the file path being
    # mutated. Override/extend per domain (e.g. aegis_sentinel tools that
    # write case files).
    destructive_tools: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_DESTRUCTIVE_TOOLS)
    )
    checkpoint_dir: str = "/checkpoints"
    name: str = "checkpoint"

    async def before_tool_call(self, *, tool_name: str, arguments: dict, state: AgentState):
        path_key = self.destructive_tools.get(tool_name)
        if path_key is None:
            return None
        path = arguments.get(path_key)
        if not path:
            return None

        try:
            previous_content = await self.memory.read(path)
            existed = True
        except Exception:  # noqa: BLE001 — file not existing yet is a valid prior state to record
            previous_content = None
            existed = False

        checkpoint_id = uuid.uuid4().hex[:12]
        record = {
            "checkpoint_id": checkpoint_id,
            "tool": tool_name,
            "path": path,
            "existed": existed,
            "previous_content": previous_content,
            "ts": time.time(),
        }
        await self.memory.write(
            f"{self.checkpoint_dir}/{checkpoint_id}.json", json.dumps(record, default=str)
        )
        state.scratch.setdefault("checkpoints", []).append(checkpoint_id)
        return None  # observes/snapshots only — arguments pass through unchanged


async def restore_checkpoint(
    memory: BackendProtocol, checkpoint_id: str, checkpoint_dir: str = "/checkpoints"
) -> bool:
    """Roll a file back to its pre-tool-call state. Returns False if the
    checkpoint doesn't exist."""
    try:
        raw = await memory.read(f"{checkpoint_dir}/{checkpoint_id}.json")
    except Exception:  # noqa: BLE001
        return False
    record = json.loads(raw)
    if record["existed"]:
        await memory.write(record["path"], record["previous_content"])
    else:
        try:
            await memory.delete(record["path"])
        except Exception:  # noqa: BLE001
            pass
    return True
