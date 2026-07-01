"""Incident response / forensics tools: case management, evidence
chain-of-custody, timeline reconstruction, and IOC pivoting.

Built on top of aegis_sentinel/tools/soc.py's persisted-alert format rather
than duplicating indicator extraction or correlation — a case is,
structurally, a long-lived correlation cluster with an attached evidence
chain, per ROADMAP.md's design note for this domain.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field

from aegis_core.memory.backend import BackendProtocol
from aegis_core.tools.base import Tool


@dataclass
class EvidenceRecord:
    evidence_id: str
    case_id: str
    description: str
    content: str
    collector: str
    collected_at: float
    integrity_hash: str


@dataclass
class CaseRecord:
    case_id: str
    title: str
    opened_at: float
    alert_ids: list[str] = field(default_factory=list)
    status: str = "open"


async def open_case(title: str, alert_ids: list[str], memory: BackendProtocol) -> CaseRecord:
    case_id = uuid.uuid4().hex[:12]
    case = CaseRecord(case_id=case_id, title=title, opened_at=time.time(), alert_ids=list(alert_ids))
    await memory.write(f"/cases/{case_id}/case.json", json.dumps(asdict(case)))
    return case


async def ingest_evidence(
    case_id: str, description: str, content: str, collector: str, memory: BackendProtocol
) -> EvidenceRecord:
    """Persist evidence with an integrity hash and timestamp — the minimal
    chain-of-custody discipline this domain exists for. Distinct from
    aegis_sentinel.audit.AuditLogMiddleware (which logs every tool call
    generically): this is evidence-specific, with the fields a real
    investigation needs — who collected it, when, and a hash proving it
    hasn't changed since."""
    evidence_id = uuid.uuid4().hex[:12]
    integrity_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    record = EvidenceRecord(
        evidence_id=evidence_id,
        case_id=case_id,
        description=description,
        content=content,
        collector=collector,
        collected_at=time.time(),
        integrity_hash=integrity_hash,
    )
    await memory.write(f"/cases/{case_id}/evidence/{evidence_id}.json", json.dumps(asdict(record)))
    return record


async def build_timeline(case_id: str, memory: BackendProtocol) -> list[dict]:
    """Chronologically orders every alert pulled into the case plus every
    piece of evidence collected, into one reconstructed timeline."""
    case = json.loads(await memory.read(f"/cases/{case_id}/case.json"))
    events: list[dict] = []

    for alert_id in case["alert_ids"]:
        try:
            alert = json.loads(await memory.read(f"/alerts/{alert_id}.json"))
        except Exception:  # noqa: BLE001 — referenced alert may not exist in this memory backend
            continue
        events.append(
            {
                "ts": alert["timestamp"],
                "kind": "alert",
                "id": alert_id,
                "summary": f"[{alert['source']}] {alert['category']}: {alert['description'][:200]}",
            }
        )

    for path in await memory.glob(f"/cases/{case_id}/evidence/*.json"):
        ev = json.loads(await memory.read(path))
        events.append(
            {
                "ts": ev["collected_at"],
                "kind": "evidence",
                "id": ev["evidence_id"],
                "summary": f"evidence collected by {ev['collector']}: {ev['description']}",
            }
        )

    events.sort(key=lambda e: e["ts"])
    return events


async def pivot_on_ioc(indicator: str, memory: BackendProtocol) -> list[dict]:
    """Search every persisted alert for any other appearance of a given
    indicator — reuses soc.py's persisted-alert indicator shape rather than
    reinventing extraction."""
    hits = []
    for path in await memory.glob("/alerts/*.json"):
        alert = json.loads(await memory.read(path))
        all_values = {v for values in alert.get("indicators", {}).values() for v in values}
        if indicator in all_values:
            hits.append(
                {
                    "alert_id": alert["alert_id"],
                    "timestamp": alert["timestamp"],
                    "category": alert["category"],
                }
            )
    return hits


async def generate_ir_report(case_id: str, memory: BackendProtocol) -> dict:
    case = json.loads(await memory.read(f"/cases/{case_id}/case.json"))
    timeline = await build_timeline(case_id, memory)
    evidence = [
        json.loads(await memory.read(p)) for p in await memory.glob(f"/cases/{case_id}/evidence/*.json")
    ]

    all_indicators: dict[str, set[str]] = {}
    for alert_id in case["alert_ids"]:
        try:
            alert = json.loads(await memory.read(f"/alerts/{alert_id}.json"))
        except Exception:  # noqa: BLE001
            continue
        for kind, values in alert.get("indicators", {}).items():
            all_indicators.setdefault(kind, set()).update(values)

    report = {
        "case_id": case_id,
        "title": case["title"],
        "status": case["status"],
        "opened_at": case["opened_at"],
        "alert_count": len(case["alert_ids"]),
        "evidence_count": len(evidence),
        "indicators": {k: sorted(v) for k, v in all_indicators.items()},
        "timeline": timeline,
    }
    await memory.write(f"/cases/{case_id}/report.json", json.dumps(report, default=str))
    return report


def build_ir_tools(memory: BackendProtocol) -> list[Tool]:
    async def _open_case_handler(title: str, alert_ids: list[str]) -> dict:
        return asdict(await open_case(title, alert_ids, memory))

    async def _ingest_evidence_handler(case_id: str, description: str, content: str, collector: str) -> dict:
        return asdict(await ingest_evidence(case_id, description, content, collector, memory))

    async def _build_timeline_handler(case_id: str) -> list[dict]:
        return await build_timeline(case_id, memory)

    async def _pivot_handler(indicator: str) -> list[dict]:
        return await pivot_on_ioc(indicator, memory)

    async def _report_handler(case_id: str) -> dict:
        return await generate_ir_report(case_id, memory)

    return [
        Tool(
            name="open_case",
            description="Open a new incident case from one or more escalated alert IDs (from soc_triage's parse_alert).",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "alert_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "alert_ids"],
            },
            handler=_open_case_handler,
            concurrency_safe=False,
            owner="domain",
        ),
        Tool(
            name="ingest_evidence",
            description="Record a piece of evidence for a case with chain-of-custody fields (collector, timestamp, integrity hash).",
            input_schema={
                "type": "object",
                "properties": {
                    "case_id": {"type": "string"},
                    "description": {"type": "string"},
                    "content": {"type": "string"},
                    "collector": {"type": "string"},
                },
                "required": ["case_id", "description", "content", "collector"],
            },
            handler=_ingest_evidence_handler,
            concurrency_safe=False,
            owner="domain",
        ),
        Tool(
            name="build_timeline",
            description="Reconstruct a chronological timeline of every alert and evidence item in a case.",
            input_schema={
                "type": "object",
                "properties": {"case_id": {"type": "string"}},
                "required": ["case_id"],
            },
            handler=_build_timeline_handler,
            concurrency_safe=True,
            owner="domain",
        ),
        Tool(
            name="pivot_on_ioc",
            description="Find every other alert (across the whole session) referencing a given indicator of compromise.",
            input_schema={
                "type": "object",
                "properties": {"indicator": {"type": "string"}},
                "required": ["indicator"],
            },
            handler=_pivot_handler,
            concurrency_safe=True,
            owner="domain",
        ),
        Tool(
            name="generate_ir_report",
            description="Generate the structured incident report for a case: scope, timeline, indicators, evidence count.",
            input_schema={
                "type": "object",
                "properties": {"case_id": {"type": "string"}},
                "required": ["case_id"],
            },
            handler=_report_handler,
            concurrency_safe=True,
            owner="domain",
        ),
    ]
