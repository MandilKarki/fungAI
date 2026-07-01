"""ACP (Agent Client Protocol) integration: delegate a subtask to an
external ACP-compliant agent CLI (Claude Code, Codex, Gemini CLI, OpenCode,
etc.) as a subagent — the cross-vendor counterpart to
subagents.delegate.delegate_task, which spawns an in-process aegis_core
Agent instead. Source: openclaw's `sessions_spawn(runtime="acp")` — rather
than reimplementing every vendor's harness, just speak the protocol they
already expose. See ARCHITECTURE.md section 10 / ROADMAP.md.

Optional dependency: `pip install aegis-agent[acp]` (the
`agent-client-protocol` package). Verified end to end against a toy
ACP-conformant agent in examples/acp_demo_agent.py +
examples/acp_client_demo.py — no real third-party agent CLI ships with this
environment, but the wire protocol itself is genuinely exercised, not mocked.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aegis_core.memory.backend import BackendProtocol
    from aegis_core.permissions.approval import ApprovalPolicy


@dataclass
class ACPDelegateResult:
    text: str
    stop_reason: str


class _AegisACPClient:
    """Implements the ACP Client role on aegis_core's behalf: buffers
    streaming session_update text, routes file read/write requests through
    a BackendProtocol memory backend, and resolves permission requests via
    an ApprovalPolicy (or auto-allows, if none is configured)."""

    def __init__(
        self,
        memory: "BackendProtocol | None" = None,
        permission_policy: "ApprovalPolicy | None" = None,
    ):
        self.memory = memory
        self.permission_policy = permission_policy
        self.text_chunks: list[str] = []

    def on_connect(self, conn: Any) -> None:
        return None

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        text = getattr(getattr(update, "content", None), "text", None)
        if text:
            self.text_chunks.append(text)

    async def request_permission(
        self, options: list, session_id: str, tool_call: Any, **kwargs: Any
    ):
        import acp

        approved = True
        if self.permission_policy is not None:
            tool_label = getattr(tool_call, "title", None) or "acp_external_action"
            approved = await self.permission_policy.resolve(
                f"acp:{tool_label}", {}, ask_callback=None
            )
        wanted_kinds = ("allow_once", "allow_always") if approved else ("reject_once", "reject_always")
        chosen = next((o for o in options if o.kind in wanted_kinds), options[0])
        return acp.RequestPermissionResponse(
            outcome=acp.AllowedOutcome(outcome="selected", optionId=chosen.option_id)
        )

    async def read_text_file(self, path: str, session_id: str, **kwargs: Any):
        import acp

        if self.memory is None:
            raise RuntimeError("no memory backend configured for ACP file access")
        content = await self.memory.read(path)
        return acp.ReadTextFileResponse(content=content)

    async def write_text_file(self, path: str, content: str, session_id: str, **kwargs: Any):
        import acp

        if self.memory is not None:
            await self.memory.write(path, content)
        return acp.WriteTextFileResponse()

    async def create_terminal(self, *args: Any, **kwargs: Any):
        raise NotImplementedError("terminal delegation is not supported by this ACP client")

    async def ext_method(self, method: str, params: dict) -> dict:
        return {}

    async def ext_notification(self, method: str, params: dict) -> None:
        return None


class ACPAgentDelegate:
    """Spawns an external ACP-compliant agent CLI as a subprocess and
    delegates one prompt to it, returning its final response text."""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        memory: "BackendProtocol | None" = None,
        permission_policy: "ApprovalPolicy | None" = None,
    ):
        self.command = command
        self.args = args or []
        self.memory = memory
        self.permission_policy = permission_policy

    async def delegate(self, prompt: str, *, cwd: str = ".") -> ACPDelegateResult:
        try:
            import acp
        except ImportError as exc:
            raise ImportError(
                "ACPAgentDelegate requires the 'agent-client-protocol' package: "
                "pip install 'aegis-agent[acp]'"
            ) from exc

        client = _AegisACPClient(memory=self.memory, permission_policy=self.permission_policy)
        async with acp.spawn_agent_process(client, self.command, *self.args) as (conn, _process):
            await conn.initialize(protocol_version=acp.PROTOCOL_VERSION)
            session = await conn.new_session(cwd=cwd)
            response = await conn.prompt(
                prompt=[acp.text_block(prompt)], session_id=session.session_id
            )
            # The agent's session_update notifications carrying response text
            # are dispatched by the connection's background reader as
            # separate tasks from the prompt() RPC response future, so a
            # well-behaved agent sending its last chunk just before
            # returning can still have that notification arrive a tick
            # after prompt() resolves. A short grace wait lets it land
            # before we read text_chunks, rather than racing it.
            await asyncio.sleep(0.05)
            return ACPDelegateResult(
                text="".join(client.text_chunks), stop_reason=response.stop_reason
            )
