"""Auto-quarantine wrapper: if a custom ContextEngine misbehaves (raises),
fall back to PassthroughContextEngine for the rest of the session instead of
taking the whole agent down. See ARCHITECTURE.md section 7.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aegis_core.context.engine import ContextEngine, PassthroughContextEngine
from aegis_core.state import AgentState


@dataclass
class QuarantineContextEngine(ContextEngine):
    inner: ContextEngine
    max_failures: int = 1
    fallback: ContextEngine = field(default_factory=PassthroughContextEngine)

    _failures: int = field(default=0, init=False)
    _quarantined: bool = field(default=False, init=False)
    last_error: str | None = field(default=None, init=False)

    def _active(self) -> ContextEngine:
        return self.fallback if self._quarantined else self.inner

    async def should_compress(self, state: AgentState) -> bool:
        try:
            return await self._active().should_compress(state)
        except Exception as exc:  # noqa: BLE001 — exactly the failure mode this class exists to contain
            self._record_failure(exc)
            return await self.fallback.should_compress(state)

    async def compress(self, state: AgentState) -> AgentState:
        try:
            return await self._active().compress(state)
        except Exception as exc:  # noqa: BLE001
            self._record_failure(exc)
            return await self.fallback.compress(state)

    async def update_from_response(self, state: AgentState, response: Any) -> None:
        try:
            await self._active().update_from_response(state, response)
        except Exception as exc:  # noqa: BLE001
            self._record_failure(exc)

    def _record_failure(self, exc: Exception) -> None:
        self._failures += 1
        self.last_error = f"{type(exc).__name__}: {exc}"
        if self._failures >= self.max_failures:
            self._quarantined = True

    @property
    def is_quarantined(self) -> bool:
        return self._quarantined
