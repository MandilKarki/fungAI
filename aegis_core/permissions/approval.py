"""Dedicated approval/permission subsystem. See ARCHITECTURE.md section 11.

Source: hermes-agent. Two deliberate, security-motivated choices carried
over directly:

1. Approval state lives in a context-scoped variable, not a process-global or
   env-var, so concurrent sessions in the same process can't race or leak
   approvals into each other.
2. Any "bypass all approval" mode is frozen at process start, specifically so
   nothing running *during* a session — including a tool's own output — can
   flip it on. This matters most for a security agent: it must not be
   possible for untrusted ingested data (an alert payload, a log line) to
   talk the agent into raising its own privileges mid-session.

Two roadmap items completed here: a persistent (cross-session) allow-list
backed by a memory backend, and an LLM-based auto-approve path for
already-low-risk calls. A third, secret redaction before any argument is
shown to a human or an LLM, is implemented via `redact_arguments()` below.

KNOWN LIMITATION (found in the 2026-06-30 documentation re-audit, see
ROADMAP.md): `ApprovalRule.pattern`'s argument-glob matching (the
"tool_name:arg_glob" form in `classify()`) is a plain `fnmatch` glob over
the raw string value of each argument. hermes-agent shipped a fix for a
related bypass class: a value classifier that only recognizes a full flag
name (e.g. `--force`) can be evaded by an abbreviated, aliased, or
differently-quoted form of the same value. This module has no shell-command
tool to exploit that risk *today* (see ROADMAP.md §2 — no tool anywhere in
this project executes shell commands), but if one is ever added, do NOT
rely on `ApprovalRule` glob patterns over a raw command string as a security
boundary without first canonicalizing it (e.g. via `shlex` parsing plus
flag-alias normalization) — glob matching alone is not robust against
adversarial input shaping.
"""

from __future__ import annotations

import contextvars
import fnmatch
import json
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Awaitable, Callable

from aegis_core.permissions.redaction import redact_arguments
from aegis_core.state import Message

if TYPE_CHECKING:
    from aegis_core.memory.backend import BackendProtocol
    from aegis_core.providers.base import Provider


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


# Frozen once at import time. To actually change this, restart the process
# with the env var set differently — that inconvenience is the point.
_BYPASS_ALL_FROZEN: bool = os.environ.get("AEGIS_BYPASS_APPROVAL", "").lower() in (
    "1",
    "true",
    "yes",
)


def bypass_all_approval() -> bool:
    return _BYPASS_ALL_FROZEN


@dataclass
class ApprovalRule:
    # "tool_name" matches by tool name (fnmatch glob); "tool_name:arg_glob"
    # additionally requires some argument value to match arg_glob.
    pattern: str
    decision: Decision
    risk: RiskLevel = RiskLevel.MEDIUM


@dataclass
class ApprovalSession:
    """Per-session approval state, held in a contextvar — never a module
    global — so concurrent sessions in the same process never collide."""

    allow_list: set[str] = field(default_factory=set)
    deny_list: set[str] = field(default_factory=set)


_current_session: contextvars.ContextVar[ApprovalSession] = contextvars.ContextVar(
    "aegis_approval_session"
)


def get_or_create_session() -> ApprovalSession:
    try:
        return _current_session.get()
    except LookupError:
        session = ApprovalSession()
        _current_session.set(session)
        return session


AskCallback = Callable[[str, dict, RiskLevel], Awaitable[bool]]


@dataclass
class PersistentAllowList:
    """Cross-session allow-list, backed by any BackendProtocol — so it's
    durable on a FilesystemBackend/SQLiteBackend, or intentionally scoped to
    one process on a StateBackend. Distinct from ApprovalSession's in-session
    allow_list, which never survives a restart."""

    memory: "BackendProtocol"
    path: str = "/permissions/allow_list.json"

    async def load(self) -> set[str]:
        try:
            raw = await self.memory.read(self.path)
        except Exception:  # noqa: BLE001 — no allow-list persisted yet
            return set()
        return set(json.loads(raw))

    async def add(self, tool_name: str) -> None:
        current = await self.load()
        current.add(tool_name)
        await self.memory.write(self.path, json.dumps(sorted(current)))

    async def remove(self, tool_name: str) -> None:
        current = await self.load()
        current.discard(tool_name)
        await self.memory.write(self.path, json.dumps(sorted(current)))

    async def contains(self, tool_name: str) -> bool:
        return tool_name in await self.load()


@dataclass
class ApprovalPolicy:
    rules: list[ApprovalRule] = field(default_factory=list)
    default_risk: RiskLevel = RiskLevel.MEDIUM
    persistent_allow_list: PersistentAllowList | None = None
    # LLM-based auto-approve only ever fires for calls already classified as
    # LOW risk by the static rules above — it narrows an already-narrow set,
    # it never substitutes for risk classification.
    auto_approve_low_risk: bool = False
    auto_approve_provider: "Provider | None" = None

    def classify(self, tool_name: str, arguments: dict) -> tuple[Decision, RiskLevel]:
        for rule in self.rules:
            if ":" in rule.pattern:
                name_pat, arg_pat = rule.pattern.split(":", 1)
                if fnmatch.fnmatch(tool_name, name_pat) and any(
                    fnmatch.fnmatch(str(v), arg_pat) for v in arguments.values()
                ):
                    return rule.decision, rule.risk
            elif fnmatch.fnmatch(tool_name, rule.pattern):
                return rule.decision, rule.risk
        return Decision.ASK, self.default_risk

    async def resolve(
        self,
        tool_name: str,
        arguments: dict,
        ask_callback: AskCallback | None,
    ) -> bool:
        if bypass_all_approval():
            return True

        session = get_or_create_session()
        if tool_name in session.allow_list:
            return True
        if tool_name in session.deny_list:
            return False
        if self.persistent_allow_list is not None and await self.persistent_allow_list.contains(
            tool_name
        ):
            return True

        decision, risk = self.classify(tool_name, arguments)
        if decision is Decision.ALLOW:
            return True
        if decision is Decision.DENY:
            return False

        if self.auto_approve_low_risk and risk is RiskLevel.LOW and self.auto_approve_provider:
            approved = await self._llm_auto_approve(tool_name, arguments)
            if approved is not None:
                return approved
            # Provider call failed or was ambiguous — fall through to ask_callback
            # rather than silently approving or denying.

        if ask_callback is None:
            return False
        # Redacted, not raw, arguments: ask_callback is typically a CLI
        # prompt, chat message, or similar human-facing surface, and a
        # secret-shaped value (an API key, a password) must never be
        # displayed in plaintext just because the tool call happened to
        # carry one — see aegis_core.permissions.redaction.
        return bool(await ask_callback(tool_name, redact_arguments(arguments), risk))

    async def allow_permanently(self, tool_name: str) -> None:
        """Used by a CLI/UI's "always allow" choice: persists if a
        persistent_allow_list is configured, otherwise falls back to the
        in-session allow_list (so it still works without one)."""
        if self.persistent_allow_list is not None:
            await self.persistent_allow_list.add(tool_name)
        else:
            get_or_create_session().allow_list.add(tool_name)

    async def _llm_auto_approve(self, tool_name: str, arguments: dict) -> bool | None:
        """Ask a cheap LLM call whether this specific, already-low-risk call
        looks safe — given only its name/arguments, no broader session
        context. Returns None (defer to ask_callback) if the call fails or
        is ambiguous, never silently approving on an unclear answer.

        Arguments are redacted before being embedded in the prompt: this
        call goes to a third-party LLM provider, so a secret-shaped
        argument value must never leave the process in plaintext here any
        more than it should be shown to a human in ask_callback."""
        safe_arguments = redact_arguments(arguments)
        prompt = (
            f"A tool call is requesting approval. Tool: {tool_name!r}. "
            f"Arguments: {safe_arguments!r}. This was pre-classified as LOW risk "
            "by static rules. Reply with exactly one word: APPROVE or DENY."
        )
        try:
            response = await self.auto_approve_provider.complete(
                system_prompt="You are a careful security approval gate. Reply with exactly one word.",
                messages=[Message(role="user", content=prompt)],
                tools=[],
            )
        except Exception:  # noqa: BLE001 — a failed auto-approve call must not silently approve
            return None
        text = (response.message.content or "").strip().upper()
        if text.startswith("APPROVE"):
            return True
        if text.startswith("DENY"):
            return False
        return None
