"""WebSocket message protocol for the aegis gateway: plain JSON messages
over one connection per client. Deliberately minimal — a real deployment
would add auth/pairing tokens and idempotency keys (as openclaw's protocol
does); this is the wire shape, not the security layer. See ROADMAP.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Client -> server
MSG_USER_MESSAGE = "user_message"
# Server -> client
MSG_TEXT_DELTA = "text_delta"
MSG_TOOL_START = "tool_start"
MSG_TOOL_END = "tool_end"
MSG_DONE = "done"
MSG_ERROR = "error"


@dataclass
class GatewayMessage:
    type: str
    session_id: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"type": self.type, "session_id": self.session_id, "payload": self.payload})

    @classmethod
    def from_json(cls, raw: str) -> "GatewayMessage":
        data = json.loads(raw)
        return cls(type=data["type"], session_id=data["session_id"], payload=data.get("payload", {}))
