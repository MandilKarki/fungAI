"""Chain-of-custody audit middleware: every tool call in aegis_sentinel is
logged to a timestamped, hash-chained action log by default.

See ARCHITECTURE.md section 14 — "audit-first by construction": in a domain
where "what did the agent actually look at and do" must be reconstructable,
this isn't optional instrumentation, it's a default middleware on every
domain agent.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field

from aegis_core.memory.backend import BackendProtocol
from aegis_core.middleware import Middleware
from aegis_core.permissions.redaction import redact_arguments
from aegis_core.state import AgentState
from aegis_core.tools.base import ToolResult


@dataclass
class AuditLogMiddleware(Middleware):
    memory: BackendProtocol
    log_path: str = "/audit/log.jsonl"
    name: str = "audit_log"
    _prev_hash: str = field(default="0" * 16, init=False)

    async def after_tool_call(
        self, *, tool_name: str, arguments: dict, result: ToolResult, state: AgentState
    ):
        # Redacted before it's ever written to disk: an audit log is
        # typically long-lived and shared with people beyond whoever ran
        # the original session, making it the worst place for a
        # secret-shaped argument or result value to sit in plaintext.
        result_preview = result.content if result.ok else result.error
        if isinstance(result_preview, (dict, list, str)):
            result_preview = redact_arguments({"_": result_preview})["_"]

        entry = {
            "ts": time.time(),
            "tool": tool_name,
            "arguments": redact_arguments(arguments),
            "ok": result.ok,
            "result_preview": result_preview,
            "prev_hash": self._prev_hash,
        }
        entry_hash = hashlib.sha256(
            json.dumps(entry, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]
        entry["hash"] = entry_hash
        self._prev_hash = entry_hash

        try:
            existing = await self.memory.read(self.log_path)
        except Exception:  # noqa: BLE001 — first write, file doesn't exist yet
            existing = ""
        await self.memory.write(self.log_path, existing + json.dumps(entry, default=str) + "\n")
        return None
