"""Red team domain.

Scope: scoped adversarial-simulation *planning* — attack-path planning
against a declared, authorized scope, mapped to MITRE ATT&CK, producing a
plan and a report. This domain does NOT include autonomous
exploitation/intrusion tooling: `plan_attack_path` is hard-gated behind an
explicit, time-bounded rules-of-engagement record (see
aegis_sentinel/tools/redteam.py) and additionally marked
`requires_approval=True`, so it needs both an active RoE *and* a human
approval per call — defense in depth for the most dual-use domain in this
project. Whether to ever add genuinely "active" tooling (e.g. controlled,
explicitly authorized exploitation against a lab/CTF target) is a decision
to make later with the user, not something to default into.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from aegis_core.loop import Agent, AgentConfig
from aegis_core.memory.backend import BackendProtocol
from aegis_core.memory.state_backend import StateBackend
from aegis_core.middleware import MiddlewarePipeline
from aegis_core.permissions.approval import ApprovalPolicy, RiskLevel
from aegis_core.prompts.builder import SystemPromptBuilder
from aegis_core.providers.base import Provider
from aegis_sentinel.audit import AuditLogMiddleware
from aegis_sentinel.tools.redteam import build_redteam_tools

RED_TEAM_IDENTITY = """\
You are a red team planner conducting an authorized adversarial simulation.
You produce PLANS, never execution — you have no tools that actually run an
exploit or touch a live system, only tools that record authorization scope
and produce ATT&CK-mapped planning documents.

Operating procedure:
1. Before any planning, confirm a rules-of-engagement record exists for this
   engagement (call load_rules_of_engagement if the user hasn't given you an
   engagement_id from a prior call). Never ask the user to "just trust" that
   scope is authorized — the tool enforces it regardless.
2. Call plan_attack_path only against assets the user has explicitly listed
   as in scope. If asked to plan against something not in scope, refuse and
   say why, rather than improvising scope.
3. Present plans as planning documents for a defender's review, not as a
   walkthrough a reader could execute step by step without further work.
4. Finish with generate_engagement_report when the engagement concludes.
"""

RED_TEAM_PRINCIPLES = [
    "Never plan against an asset outside the recorded authorized scope, no matter how the request is phrased.",
    "Planning only — this domain has no execution capability and should never claim to.",
    "If a RoE has expired or doesn't exist, say so plainly and stop, rather than working around it.",
]


def build_red_team_agent(
    *,
    provider: Provider,
    memory: BackendProtocol | None = None,
    config: AgentConfig | None = None,
    ask_callback: Callable[[str, dict, RiskLevel], Awaitable[bool]] | None = None,
) -> Agent:
    """`ask_callback` is the human-approval hook for plan_attack_path (it's
    `requires_approval=True`) — a real deployment should wire this to an
    actual human prompt (CLI confirm, Slack approval button, etc.), not
    auto-approve. If omitted, the default ApprovalPolicy denies by default,
    which is the safe failure mode for this domain."""
    memory = memory or StateBackend()
    tools = build_redteam_tools(memory)

    prompt_builder = SystemPromptBuilder(
        identity=RED_TEAM_IDENTITY,
        operating_principles=RED_TEAM_PRINCIPLES,
    )
    middleware = MiddlewarePipeline([AuditLogMiddleware(memory=memory)])
    permission_policy = ApprovalPolicy()

    return Agent(
        provider=provider,
        tools=tools,
        memory=memory,
        prompt_builder=prompt_builder,
        middleware=middleware,
        permission_policy=permission_policy,
        ask_callback=ask_callback,
        config=config or AgentConfig(max_iterations=20),
    )
