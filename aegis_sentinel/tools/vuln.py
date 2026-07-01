"""Vulnerability management tools: real integration with two public, no-key
data sources — the CISA Known Exploited Vulnerabilities (KEV) catalog and
FIRST.org's EPSS (Exploit Prediction Scoring System) API. Both endpoints are
live, no-auth, and reachable from this environment, which is exactly why
this domain — unlike most other aegis_sentinel domains still on the roadmap
— isn't stubbed: real exploitability scoring needs real data, and these two
feeds happen to be free.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field

import httpx

from aegis_core.memory.backend import BackendProtocol
from aegis_core.tools.base import Tool

KEV_FEED_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
EPSS_API_URL = "https://api.first.org/data/v1/epss"
KEV_CACHE_PATH = "/cache/kev_catalog.json"
KEV_CACHE_TTL_SECONDS = 24 * 3600

SEVERITY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


@dataclass
class NormalizedFinding:
    finding_id: str
    cve_id: str | None
    title: str
    raw_severity: str
    description: str
    asset: str | None = None
    internet_facing: bool = False
    raw: dict = field(default_factory=dict)


async def get_kev_catalog(memory: BackendProtocol) -> dict[str, dict]:
    """24h-TTL-cached fetch of the real CISA KEV catalog, cache persisted
    through the memory backend so repeated calls within a session (or across
    a SQLiteBackend-backed session) don't re-fetch a multi-MB feed."""
    try:
        cached = json.loads(await memory.read(KEV_CACHE_PATH))
        if time.time() - cached["fetched_at"] < KEV_CACHE_TTL_SECONDS:
            return cached["by_cve"]
    except Exception:  # noqa: BLE001 — no cache yet, or it's stale/corrupt
        pass

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(KEV_FEED_URL)
        resp.raise_for_status()
        data = resp.json()

    by_cve = {entry["cveID"]: entry for entry in data.get("vulnerabilities", [])}
    await memory.write(KEV_CACHE_PATH, json.dumps({"fetched_at": time.time(), "by_cve": by_cve}))
    return by_cve


async def get_epss_score(cve_id: str) -> dict:
    """Live call to FIRST.org's EPSS API — no API key required."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(EPSS_API_URL, params={"cve": cve_id})
        resp.raise_for_status()
        data = resp.json()
    results = data.get("data", [])
    if not results:
        return {"epss": 0.0, "percentile": 0.0}
    return {"epss": float(results[0]["epss"]), "percentile": float(results[0]["percentile"])}


async def ingest_scan_results(
    raw_findings: list[dict], memory: BackendProtocol
) -> list[NormalizedFinding]:
    findings = []
    for raw in raw_findings:
        finding_id = str(raw.get("id") or uuid.uuid4().hex[:12])
        finding = NormalizedFinding(
            finding_id=finding_id,
            cve_id=raw.get("cve_id"),
            title=str(raw.get("title", raw.get("cve_id", "untitled finding"))),
            raw_severity=str(raw.get("severity", "medium")).lower(),
            description=str(raw.get("description", ""))[:2000],
            asset=raw.get("asset"),
            internet_facing=bool(raw.get("internet_facing", False)),
            raw=raw,
        )
        await memory.write(f"/vulns/{finding_id}.json", json.dumps(asdict(finding)))
        findings.append(finding)
    return findings


async def assess_exploitability(
    finding_id: str, memory: BackendProtocol, *, asset_criticality: str = "unknown"
) -> dict:
    """Exploitability scoring: actively-exploited-in-the-wild (CISA KEV)
    always maxes the score regardless of other factors — that's a real
    security judgment, not a tie-breaker. Otherwise, build up from CVSS-band
    base severity using live EPSS likelihood, internet exposure, and asset
    criticality. Every score ships with a rationale, same discipline as
    aegis_sentinel/tools/soc.py's classify_severity."""
    data = json.loads(await memory.read(f"/vulns/{finding_id}.json"))
    finding = NormalizedFinding(**data)

    kev_hit: dict | None = None
    epss = {"epss": 0.0, "percentile": 0.0}
    if finding.cve_id:
        kev_catalog = await get_kev_catalog(memory)
        kev_hit = kev_catalog.get(finding.cve_id.upper())
        try:
            epss = await get_epss_score(finding.cve_id)
        except Exception as exc:  # noqa: BLE001 — EPSS being briefly unreachable shouldn't block scoring
            epss = {"epss": 0.0, "percentile": 0.0, "error": str(exc)}

    base = SEVERITY_RANK.get(finding.raw_severity, SEVERITY_RANK["medium"])

    if kev_hit:
        score = SEVERITY_RANK["critical"]
    else:
        score = base
        epss_value = epss.get("epss", 0.0)
        if epss_value >= 0.5:
            score = min(4, score + 2)
        elif epss_value >= 0.1:
            score = min(4, score + 1)
        if finding.internet_facing:
            score = min(4, score + 1)
        if asset_criticality in ("critical", "high"):
            score = min(4, score + 1)

    level = next(k for k, v in SEVERITY_RANK.items() if v == score)

    rationale = [
        f"raw severity: {finding.raw_severity} (base={base})",
        "CISA KEV listed: "
        + (f"YES, added {kev_hit.get('dateAdded')} — forces max exploitability" if kev_hit else "no"),
        f"EPSS score: {epss.get('epss', 0):.3f} (percentile {epss.get('percentile', 0):.3f})",
        f"internet-facing: {finding.internet_facing}",
        f"asset criticality: {asset_criticality}",
    ]

    return {
        "finding_id": finding_id,
        "cve_id": finding.cve_id,
        "title": finding.title,
        "exploitability": level,
        "score": score,
        "kev_listed": bool(kev_hit),
        "epss": epss,
        "rationale": rationale,
    }


async def prioritize_remediation(memory: BackendProtocol) -> list[dict]:
    paths = [p for p in await memory.glob("/vulns/*.json") if not p.startswith("/vulns/_")]
    results = [
        await assess_exploitability(NormalizedFinding(**json.loads(await memory.read(p))).finding_id, memory)
        for p in paths
    ]
    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def build_vuln_tools(memory: BackendProtocol) -> list[Tool]:
    async def _ingest_handler(raw_findings: list[dict]) -> list[dict]:
        findings = await ingest_scan_results(raw_findings, memory)
        return [asdict(f) for f in findings]

    async def _assess_handler(finding_id: str, asset_criticality: str = "unknown") -> dict:
        return await assess_exploitability(finding_id, memory, asset_criticality=asset_criticality)

    async def _prioritize_handler() -> list[dict]:
        return await prioritize_remediation(memory)

    return [
        Tool(
            name="ingest_scan_results",
            description=(
                "Ingest raw vulnerability scan findings (a list of objects with "
                "cve_id/title/severity/description/asset/internet_facing fields) "
                "and persist them for assessment."
            ),
            input_schema={
                "type": "object",
                "properties": {"raw_findings": {"type": "array", "items": {"type": "object"}}},
                "required": ["raw_findings"],
            },
            handler=_ingest_handler,
            concurrency_safe=False,
            owner="domain",
        ),
        Tool(
            name="assess_exploitability",
            description=(
                "Score a previously-ingested finding's real-world exploitability using "
                "the live CISA KEV catalog and FIRST.org EPSS score, plus exposure and "
                "asset criticality. Returns a level, score, and rationale."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "finding_id": {"type": "string"},
                    "asset_criticality": {
                        "type": "string",
                        "enum": ["unknown", "low", "medium", "high", "critical"],
                        "default": "unknown",
                    },
                },
                "required": ["finding_id"],
            },
            handler=_assess_handler,
            concurrency_safe=True,
            owner="domain",
        ),
        Tool(
            name="prioritize_remediation",
            description="Rank all ingested findings by exploitability, highest first.",
            input_schema={"type": "object", "properties": {}},
            handler=_prioritize_handler,
            concurrency_safe=True,
            owner="domain",
        ),
    ]
