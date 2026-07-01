"""Domain registry for aegis_sentinel. See ARCHITECTURE.md section 14.

Includes the cross-domain "orchestrator" persona (aegis_sentinel/orchestrator.py),
which routes work between the other domains via
aegis_core.subagents.delegate.delegate_task, sharing case data through the
common memory backend. orchestrator.py imports DOMAINS from this module
lazily (inside its functions, not at module load time) specifically to
avoid a personas<->orchestrator import cycle, since it's registered here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from aegis_core.loop import Agent
from aegis_core.providers.base import Provider
from aegis_sentinel.domains import data_security, incident_response, red_team, vuln_management
from aegis_sentinel.domains.soc_triage import build_soc_triage_agent
from aegis_sentinel.orchestrator import build_orchestrator_agent


@dataclass
class DomainSpec:
    name: str
    builder: Callable[..., Agent]
    status: str  # "core" | "roadmap"
    description: str


DOMAINS: dict[str, DomainSpec] = {
    "soc_triage": DomainSpec(
        "soc_triage",
        build_soc_triage_agent,
        "core",
        "Fast, high-volume alert/log triage: parse, correlate, score, recommend response.",
    ),
    "vuln_management": DomainSpec(
        "vuln_management",
        vuln_management.build_vuln_management_agent,
        "core",
        "Vulnerability scan ingestion, exploitability/impact assessment (live CISA KEV + EPSS), remediation prioritization.",
    ),
    "incident_response": DomainSpec(
        "incident_response",
        incident_response.build_incident_response_agent,
        "core",
        "Evidence-preserving investigation: case management, timeline reconstruction, IOC pivoting, chain of custody.",
    ),
    "red_team": DomainSpec(
        "red_team",
        red_team.build_red_team_agent,
        "core",
        "Scoped adversarial-simulation planning, hard-gated by explicit rules of engagement + human approval.",
    ),
    "data_security": DomainSpec(
        "data_security",
        data_security.build_data_security_agent,
        "core",
        "Sensitive-data discovery/classification, secret scanning, access-grant review.",
    ),
    "orchestrator": DomainSpec(
        "orchestrator",
        build_orchestrator_agent,
        "core",
        "Cross-domain router: delegates to the specialist domain above via aegis_core's delegate_task.",
    ),
}


def build_domain_agent(domain: str, *, provider: Provider, **kwargs) -> Agent:
    try:
        spec = DOMAINS[domain]
    except KeyError:
        raise ValueError(f"unknown domain {domain!r}; available: {sorted(DOMAINS)}") from None
    return spec.builder(provider=provider, **kwargs)
