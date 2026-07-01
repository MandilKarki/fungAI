"""SOC alert/log triage domain — the flagship slice of aegis_sentinel built
out deep in this iteration. See ARCHITECTURE.md section 14.

Fast, high-volume, false-positive-tolerant: the operating posture deliberately
differs from incident_response (slow, evidence-preserving). Forcing both into
one prompt produces a mediocre agent at both; this domain is allowed to be
narrow and fast on purpose.
"""

from __future__ import annotations

from aegis_core.loop import Agent, AgentConfig
from aegis_core.memory.backend import BackendProtocol
from aegis_core.memory.state_backend import StateBackend
from aegis_core.middleware import MiddlewarePipeline
from aegis_core.prompts.builder import SystemPromptBuilder
from aegis_core.prompts.skills import SkillCatalog
from aegis_core.providers.base import Provider
from aegis_sentinel.audit import AuditLogMiddleware
from aegis_sentinel.tools.soc import build_soc_tools
from aegis_sentinel.tools.threat_intel import build_threat_intel_tools

SOC_TRIAGE_IDENTITY = """\
You are a SOC (Security Operations Center) triage analyst. Your job is high
volume, fast, and tolerant of false positives — you would rather flag
something for a human to dismiss than miss something real.

Operating procedure for every incoming alert:
1. Call parse_alert to normalize it and extract indicators of compromise.
2. Call correlate_alerts to see whether it's part of a larger pattern.
3. For any IP/hash/domain indicators found, call enrich_ioc to check live
   reputation data — if it reports configured=false, say so plainly rather
   than treating the absence of data as "clean".
4. Call classify_severity, passing the correlation cluster size and any
   asset criticality you know about.
5. Call recommend_response for the assigned severity and present the
   playbook steps to the user.
6. If severity is "critical" or "high" and the situation is ambiguous or
   spans multiple hosts/users, say explicitly that this should be escalated
   to the incident_response domain — do not try to run a full investigation
   yourself; that domain has the evidence-preservation discipline this one
   doesn't.
"""

SOC_TRIAGE_PRINCIPLES = [
    "Prefer flagging a false positive over missing a true positive.",
    "Every severity score must cite concrete evidence, not vibes.",
    "Escalate ambiguous high/critical findings rather than investigate solo.",
    "Never assign critical or high severity without at least one concrete "
    "piece of evidence beyond the raw source severity field.",
]


def build_soc_triage_agent(
    *,
    provider: Provider,
    memory: BackendProtocol | None = None,
    config: AgentConfig | None = None,
    skill_catalog: SkillCatalog | None = None,
) -> Agent:
    memory = memory or StateBackend()
    tools = build_soc_tools(memory) + build_threat_intel_tools()

    prompt_builder = SystemPromptBuilder(
        identity=SOC_TRIAGE_IDENTITY,
        operating_principles=SOC_TRIAGE_PRINCIPLES,
    )

    middleware = MiddlewarePipeline([AuditLogMiddleware(memory=memory)])

    return Agent(
        provider=provider,
        tools=tools,
        memory=memory,
        prompt_builder=prompt_builder,
        middleware=middleware,
        config=config or AgentConfig(max_iterations=20),
        skill_catalog=skill_catalog,
    )
