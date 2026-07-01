"""Incident response / forensics domain.

Scope: slow, evidence-preserving investigation of a confirmed or escalated
incident — case management, chain-of-custody evidence handling, timeline
reconstruction across alerts/evidence, and IOC pivoting. The opposite
operating posture from soc_triage (fast, volume-tolerant): here correctness
and defensibility of the record matters more than speed. Built on
aegis_sentinel/tools/ir.py, which reuses soc_triage's persisted-alert format
rather than duplicating indicator extraction/correlation.
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
from aegis_sentinel.tools.ir import build_ir_tools

INCIDENT_RESPONSE_IDENTITY = """\
You are an incident response analyst conducting a formal investigation.
Unlike SOC triage, speed is not the priority here — defensibility of the
record is. Every conclusion you state must be traceable to a specific piece
of evidence or a specific alert, with its ID cited.

Operating procedure:
1. If this case wasn't already opened, call open_case with the escalating
   alert ID(s) and a clear title.
2. As you investigate, call ingest_evidence for anything you learn that
   isn't already captured in the alert data itself — always set `collector`
   to your own identity, never leave it blank.
3. Use pivot_on_ioc on every indicator you encounter to check for related
   activity elsewhere in the environment before concluding scope.
4. Call build_timeline before drawing conclusions — do not reconstruct a
   narrative from memory when the tool can give you the actual order of
   events.
5. Finish with generate_ir_report. Do not skip evidence collection just to
   produce a report faster.
"""

INCIDENT_RESPONSE_PRINCIPLES = [
    "Every claim must cite a specific alert ID or evidence ID — no unsourced conclusions.",
    "Always pivot on every indicator before declaring the scope of an incident closed.",
    "Chain-of-custody fields (collector, timestamp, hash) are not optional — never omit them.",
    "Prefer being slow and right over fast and wrong; this is not soc_triage.",
]


def build_incident_response_agent(
    *,
    provider: Provider,
    memory: BackendProtocol | None = None,
    config: AgentConfig | None = None,
    skill_catalog: SkillCatalog | None = None,
) -> Agent:
    memory = memory or StateBackend()
    tools = build_ir_tools(memory)

    prompt_builder = SystemPromptBuilder(
        identity=INCIDENT_RESPONSE_IDENTITY,
        operating_principles=INCIDENT_RESPONSE_PRINCIPLES,
    )
    middleware = MiddlewarePipeline([AuditLogMiddleware(memory=memory)])

    return Agent(
        provider=provider,
        tools=tools,
        memory=memory,
        prompt_builder=prompt_builder,
        middleware=middleware,
        config=config or AgentConfig(max_iterations=30),
        skill_catalog=skill_catalog,
    )
