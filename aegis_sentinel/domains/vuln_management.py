"""Vulnerability management domain.

Scope: ingest scan results (Nessus/OpenVAS/Qualys-style exports, or a CVE
list), assess real-world exploitability (live CISA KEV catalog membership +
live FIRST.org EPSS score + internet-facing exposure), weigh that against
asset criticality, and produce a prioritized remediation queue — mirroring
soc_triage's parse -> correlate -> score -> recommend shape, but for
vulnerabilities rather than alerts. See aegis_sentinel/tools/vuln.py for the
real implementation (no fake scoring — both data sources are live, free,
public APIs, so there was no reason to stub this one).
"""

from __future__ import annotations

from aegis_core.loop import Agent, AgentConfig
from aegis_core.memory.backend import BackendProtocol
from aegis_core.memory.state_backend import StateBackend
from aegis_core.middleware import MiddlewarePipeline
from aegis_core.prompts.builder import SystemPromptBuilder
from aegis_core.providers.base import Provider
from aegis_sentinel.audit import AuditLogMiddleware
from aegis_sentinel.tools.vuln import build_vuln_tools

VULN_MANAGEMENT_IDENTITY = """\
You are a vulnerability management analyst. Your job is to turn a pile of
scan findings into a small, defensible, prioritized remediation list — not
to re-litigate CVSS scores, and not to treat every "critical"-labeled
finding as equally urgent.

Operating procedure:
1. Call ingest_scan_results with the findings you've been given.
2. For each finding (or all of them via prioritize_remediation), call
   assess_exploitability — pass the best asset_criticality you know.
3. Present the ranked list from prioritize_remediation as your answer,
   leading with anything CISA KEV-listed (actively exploited in the wild)
   regardless of its original CVSS label.
4. Be explicit when a finding's raw severity disagrees with its computed
   exploitability — that disagreement is the most useful thing you can
   surface (e.g. "labeled medium, but KEV-listed and internet-facing:
   treat as urgent").
"""

VULN_MANAGEMENT_PRINCIPLES = [
    "CISA KEV membership (actively exploited in the wild) always outranks a raw CVSS label.",
    "Every exploitability score must cite EPSS/KEV/exposure evidence, not just restate CVSS.",
    "Surface raw-severity-vs-computed-exploitability disagreements explicitly — that's the value-add.",
]


def build_vuln_management_agent(
    *,
    provider: Provider,
    memory: BackendProtocol | None = None,
    config: AgentConfig | None = None,
) -> Agent:
    memory = memory or StateBackend()
    tools = build_vuln_tools(memory)

    prompt_builder = SystemPromptBuilder(
        identity=VULN_MANAGEMENT_IDENTITY,
        operating_principles=VULN_MANAGEMENT_PRINCIPLES,
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
