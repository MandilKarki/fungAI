"""Data security domain.

Scope: sensitive-data discovery and classification, exposed-secret scanning,
and access-grant review — the "where is our sensitive data and who can
reach it" half of security, distinct from SOC/IR's "is something actively
attacking us" half. Built on aegis_sentinel/tools/data_security.py's real
pattern-based detection (Luhn-validated card numbers, credential-shaped
regexes, least-privilege heuristics) — not a placeholder keyword list.
"""

from __future__ import annotations

from aegis_core.loop import Agent, AgentConfig
from aegis_core.memory.backend import BackendProtocol
from aegis_core.memory.state_backend import StateBackend
from aegis_core.middleware import MiddlewarePipeline
from aegis_core.prompts.builder import SystemPromptBuilder
from aegis_core.providers.base import Provider
from aegis_sentinel.audit import AuditLogMiddleware
from aegis_sentinel.tools.data_security import build_data_security_tools

DATA_SECURITY_IDENTITY = """\
You are a data security analyst. Your job is to find where sensitive data
is exposed and where access is broader than it should be — not to guess,
and not to treat every match as confirmed sensitive data without saying so.

Operating procedure:
1. Use classify_data_sensitivity on any sample text you're given directly.
2. Use scan_for_exposed_secrets over the relevant memory path(s) to find
   credential-shaped patterns at rest.
3. Use review_access_grants on any grant list you're given to flag
   overly-broad or stale access.
4. Use summarize_exposure to roll findings into one prioritized view when
   asked for an overall picture.

Pattern matches are signals, not proof — say "looks like" rather than
"is" for anything not independently confirmed, especially for content you
haven't seen in full (e.g. a credit-card-shaped, Luhn-valid number could
still be a test fixture, not a real card).
"""

DATA_SECURITY_PRINCIPLES = [
    "Treat pattern matches as signals to investigate, not as confirmed findings.",
    "Every flagged access grant must cite a concrete reason (broad scope, staleness), never a bare 'looks risky'.",
    "Prefer precise, low-noise findings — repeatedly flagging known-test data erodes trust in the tool.",
]


def build_data_security_agent(
    *,
    provider: Provider,
    memory: BackendProtocol | None = None,
    config: AgentConfig | None = None,
) -> Agent:
    memory = memory or StateBackend()
    tools = build_data_security_tools(memory)

    prompt_builder = SystemPromptBuilder(
        identity=DATA_SECURITY_IDENTITY,
        operating_principles=DATA_SECURITY_PRINCIPLES,
    )
    middleware = MiddlewarePipeline([AuditLogMiddleware(memory=memory)])

    return Agent(
        provider=provider,
        tools=tools,
        memory=memory,
        prompt_builder=prompt_builder,
        middleware=middleware,
        config=config or AgentConfig(max_iterations=20),
    )
