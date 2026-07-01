"""aegis_sentinel: a deep, multi-domain cybersecurity analyst agent built on
aegis_core. See ARCHITECTURE.md section 14."""

from aegis_sentinel.personas import DOMAINS, DomainSpec, build_domain_agent

__all__ = ["DOMAINS", "DomainSpec", "build_domain_agent"]
