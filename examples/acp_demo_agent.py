"""A minimal, protocol-conformant ACP agent, used to verify aegis_core's ACP
client integration (aegis_core/integrations/acp_client.py) end to end. Not
LLM-backed — canned responses only, for wire-protocol-conformance testing.
Not launched directly: examples/acp_client_demo.py spawns it as a subprocess.
"""

from __future__ import annotations

import asyncio

import acp


class ToyEchoAgent:
    """Implements just enough of the acp.Agent protocol (a typing.Protocol,
    not an ABC, so partial implementations are fine) to handle one
    initialize -> new_session -> prompt round trip."""

    def __init__(self) -> None:
        self._conn: acp.Client | None = None
        self._sessions: dict[str, str] = {}

    def on_connect(self, conn: acp.Client) -> None:
        self._conn = conn

    async def initialize(self, protocol_version, client_capabilities=None, client_info=None, **kwargs):
        return acp.InitializeResponse(protocolVersion=acp.PROTOCOL_VERSION)

    async def new_session(self, cwd, additional_directories=None, mcp_servers=None, **kwargs):
        session_id = f"sess-{len(self._sessions) + 1}"
        self._sessions[session_id] = cwd
        return acp.NewSessionResponse(sessionId=session_id)

    async def prompt(self, prompt, session_id, message_id=None, **kwargs):
        text_in = "".join(getattr(block, "text", "") for block in prompt)
        reply = f"[toy-acp-agent echo] received: {text_in}"
        assert self._conn is not None
        await self._conn.session_update(session_id, acp.update_agent_message_text(reply))
        return acp.PromptResponse(stopReason="end_turn")

    async def cancel(self, session_id, **kwargs):
        return None


if __name__ == "__main__":
    asyncio.run(acp.run_agent(ToyEchoAgent()))
