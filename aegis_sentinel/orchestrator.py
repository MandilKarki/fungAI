"""Cross-domain orchestrator persona: routes work between aegis_sentinel
domains via aegis_core.subagents.delegate.delegate_task — e.g. a SOC-triaged
alert that escalates gets handed to incident_response as a delegated child,
sharing the case through the common memory backend. See ARCHITECTURE.md
section 14.

This is the real consumer proving aegis_core's generic delegation mechanism
works for cross-domain routing, not a bespoke routing layer: the adapter
below is exactly the shape delegate_task expects (an `agent_factory(*,
tools, memory, system_prompt_extra)` callable) — it just builds the right
*domain* agent (with its own real toolset) instead of a generic one.
"""

from __future__ import annotations

from aegis_core.loop import Agent, AgentConfig
from aegis_core.memory.backend import BackendProtocol
from aegis_core.memory.state_backend import StateBackend
from aegis_core.middleware import MiddlewarePipeline
from aegis_core.prompts.builder import SystemPromptBuilder
from aegis_core.providers.base import Provider
from aegis_core.subagents.delegate import DelegationLimiter, DelegationPolicy, delegate_task
from aegis_core.tools.base import Tool
from aegis_sentinel.audit import AuditLogMiddleware

ORCHESTRATOR_IDENTITY = """\
You are a security orchestrator. You don't do the deep work of any single
domain yourself — you triage incoming requests and delegate to the right
specialist domain, then synthesize their results for the user.

Routing guide:
- Alert/log triage, fast classification -> delegate to "soc_triage".
- An alert that's escalated (critical/high severity, ambiguous scope, or the
  user explicitly says "investigate") -> delegate to "incident_response".
- Vulnerability scan results, CVE prioritization -> delegate to "vuln_management".
- Sensitive data exposure, secret scanning, access review -> delegate to "data_security".

Always delegate rather than attempting domain-specific work yourself — you
don't have any domain tools, only `delegate`. Give the delegate a complete,
self-sufficient task description: you only see its final summary, not its
intermediate tool calls.
"""


def _make_domain_agent_factory(domain_name: str, provider: Provider):
    """Adapts a domain's own build_X_agent(provider=, memory=) factory to
    the generic agent_factory(*, tools, memory, system_prompt_extra) shape
    delegate_task calls. `tools` is intentionally ignored — the domain
    factory builds its own correct toolset; only `memory` (for shared case
    data) and `system_prompt_extra` (the task description) are used."""

    def factory(*, tools, memory: BackendProtocol, system_prompt_extra: str | None = None) -> Agent:
        from aegis_sentinel.personas import DOMAINS  # deferred: avoids a personas<->orchestrator cycle

        spec = DOMAINS[domain_name]
        agent = spec.builder(provider=provider, memory=memory)
        if system_prompt_extra:
            agent.prompt_builder.add_stable_section("Delegated task", system_prompt_extra)
        return agent

    return factory


def build_orchestrator_tools(
    provider: Provider, memory: BackendProtocol, limiter: DelegationLimiter
) -> list[Tool]:
    from aegis_sentinel.personas import DOMAINS  # deferred: avoids a personas<->orchestrator cycle

    # Excludes "orchestrator" itself — routing to yourself isn't a real
    # delegation target and would just recurse.
    available_domains = [
        name for name, spec in DOMAINS.items() if spec.status == "core" and name != "orchestrator"
    ]

    async def _delegate_handler(domain: str, description: str, prompt: str) -> str:
        if domain not in available_domains:
            return f"unknown or not-yet-implemented domain: {domain!r}; available: {available_domains}"
        factory = _make_domain_agent_factory(domain, provider)
        result = await delegate_task(
            agent_factory=factory,
            description=description,
            prompt=prompt,
            tools=[],
            shared_memory=memory,
            limiter=limiter,
        )
        return result.to_model_text()

    return [
        Tool(
            name="delegate",
            description=f"Delegate a task to a specialist security domain. Available domains: {available_domains}",
            input_schema={
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "enum": available_domains},
                    "description": {
                        "type": "string",
                        "description": "Short label for what this delegation is for.",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "The full, self-sufficient task for the domain agent.",
                    },
                },
                "required": ["domain", "description", "prompt"],
            },
            handler=_delegate_handler,
            concurrency_safe=False,
            owner="core",
        )
    ]


def build_orchestrator_agent(
    *,
    provider: Provider,
    memory: BackendProtocol | None = None,
    config: AgentConfig | None = None,
) -> Agent:
    memory = memory or StateBackend()
    limiter = DelegationLimiter(policy=DelegationPolicy(max_spawn_depth=1, max_concurrent_children=3))
    tools = build_orchestrator_tools(provider, memory, limiter)

    prompt_builder = SystemPromptBuilder(identity=ORCHESTRATOR_IDENTITY)
    middleware = MiddlewarePipeline([AuditLogMiddleware(memory=memory)])

    return Agent(
        provider=provider,
        tools=tools,
        memory=memory,
        prompt_builder=prompt_builder,
        middleware=middleware,
        config=config or AgentConfig(max_iterations=15),
    )
