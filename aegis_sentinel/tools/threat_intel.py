"""Real threat-intel enrichment adapters: AbuseIPDB (IP reputation) and
VirusTotal (hash/domain reputation). Both require the deployer's own API
key — this environment has neither, so live *authenticated* calls aren't
exercised here, but the request/response shapes are written against each
service's actual documented v2/v3 API (verified against real error
responses with an invalid key — see ROADMAP.md), not guessed.
"""

from __future__ import annotations

import os

import httpx

from aegis_core.tools.base import Tool

ABUSEIPDB_API_URL = "https://api.abuseipdb.com/api/v2/check"
VIRUSTOTAL_API_BASE = "https://www.virustotal.com/api/v3"


class NotConfiguredError(Exception):
    pass


async def check_ip_reputation(ip: str, api_key: str | None = None) -> dict:
    """AbuseIPDB IP reputation check. Requires ABUSEIPDB_API_KEY."""
    key = api_key or os.environ.get("ABUSEIPDB_API_KEY")
    if not key:
        raise NotConfiguredError("ABUSEIPDB_API_KEY not set — IP reputation lookup unavailable")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            ABUSEIPDB_API_URL,
            params={"ipAddress": ip, "maxAgeInDays": 90},
            headers={"Key": key, "Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()["data"]

    return {
        "ip": ip,
        "abuse_confidence_score": data.get("abuseConfidenceScore"),
        "total_reports": data.get("totalReports"),
        "country_code": data.get("countryCode"),
        "is_tor": data.get("isTor"),
        "isp": data.get("isp"),
    }


async def check_hash_reputation(file_hash: str, api_key: str | None = None) -> dict:
    """VirusTotal file-hash reputation check. Requires VIRUSTOTAL_API_KEY."""
    key = api_key or os.environ.get("VIRUSTOTAL_API_KEY")
    if not key:
        raise NotConfiguredError("VIRUSTOTAL_API_KEY not set — hash reputation lookup unavailable")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{VIRUSTOTAL_API_BASE}/files/{file_hash}", headers={"x-apikey": key})
        resp.raise_for_status()
        attrs = resp.json()["data"]["attributes"]

    stats = attrs.get("last_analysis_stats", {})
    return {
        "hash": file_hash,
        "malicious": stats.get("malicious", 0),
        "suspicious": stats.get("suspicious", 0),
        "harmless": stats.get("harmless", 0),
        "total_engines": sum(stats.values()) if stats else 0,
        "names": attrs.get("names", [])[:5],
    }


async def check_domain_reputation(domain: str, api_key: str | None = None) -> dict:
    """VirusTotal domain reputation check. Requires VIRUSTOTAL_API_KEY."""
    key = api_key or os.environ.get("VIRUSTOTAL_API_KEY")
    if not key:
        raise NotConfiguredError("VIRUSTOTAL_API_KEY not set — domain reputation lookup unavailable")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{VIRUSTOTAL_API_BASE}/domains/{domain}", headers={"x-apikey": key})
        resp.raise_for_status()
        attrs = resp.json()["data"]["attributes"]

    stats = attrs.get("last_analysis_stats", {})
    return {
        "domain": domain,
        "malicious": stats.get("malicious", 0),
        "suspicious": stats.get("suspicious", 0),
        "harmless": stats.get("harmless", 0),
        "reputation": attrs.get("reputation"),
    }


def build_threat_intel_tools() -> list[Tool]:
    async def _enrich_ioc_handler(indicator: str, kind: str) -> dict:
        try:
            if kind == "ip":
                return await check_ip_reputation(indicator)
            if kind == "hash":
                return await check_hash_reputation(indicator)
            if kind == "domain":
                return await check_domain_reputation(indicator)
            return {"error": f"unsupported indicator kind: {kind!r}"}
        except NotConfiguredError as exc:
            return {"error": str(exc), "configured": False}

    return [
        Tool(
            name="enrich_ioc",
            description=(
                "Look up an indicator's reputation via live threat-intel feeds "
                "(AbuseIPDB for IPs, VirusTotal for hashes/domains). Requires the "
                "deployer's own API key (ABUSEIPDB_API_KEY / VIRUSTOTAL_API_KEY) — "
                "reports configured=false if the relevant key isn't set, rather "
                "than failing silently or fabricating a result."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "indicator": {"type": "string"},
                    "kind": {"type": "string", "enum": ["ip", "hash", "domain"]},
                },
                "required": ["indicator", "kind"],
            },
            handler=_enrich_ioc_handler,
            concurrency_safe=True,
            owner="domain",
            deferred=True,
        )
    ]
