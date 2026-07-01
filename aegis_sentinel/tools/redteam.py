"""Red team tools: scoped adversarial-simulation *planning* only — never
execution or exploitation. Every planning tool is hard-gated behind an
explicit, recorded, time-bounded rules-of-engagement (RoE) record; there is
no code path to call plan_attack_path without one. This is enforced the same
way aegis_core.permissions.approval freezes bypass_all_approval() at process
start — a required precondition, not an optional safety check the model
could be talked out of.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field

from aegis_core.memory.backend import BackendProtocol
from aegis_core.tools.base import Tool
from aegis_sentinel.tools.soc import map_attack_techniques


class RoENotAuthorizedError(Exception):
    pass


@dataclass
class RulesOfEngagement:
    engagement_id: str
    authorized_targets: list[str]
    allowed_techniques: list[str]
    point_of_contact: str
    window_start: float
    window_end: float
    created_at: float = field(default_factory=time.time)


async def load_rules_of_engagement(
    authorized_targets: list[str],
    allowed_techniques: list[str],
    point_of_contact: str,
    window_start: float,
    window_end: float,
    memory: BackendProtocol,
) -> RulesOfEngagement:
    if not authorized_targets:
        raise ValueError(
            "authorized_targets must not be empty — a RoE with no scope authorizes nothing"
        )
    if not point_of_contact:
        raise ValueError("point_of_contact is required")
    if window_end <= window_start:
        raise ValueError("window_end must be after window_start")

    engagement_id = uuid.uuid4().hex[:12]
    roe = RulesOfEngagement(
        engagement_id=engagement_id,
        authorized_targets=authorized_targets,
        allowed_techniques=allowed_techniques,
        point_of_contact=point_of_contact,
        window_start=window_start,
        window_end=window_end,
    )
    await memory.write(f"/redteam/{engagement_id}/roe.json", json.dumps(asdict(roe)))
    return roe


async def _get_active_roe(engagement_id: str, memory: BackendProtocol) -> RulesOfEngagement:
    try:
        data = json.loads(await memory.read(f"/redteam/{engagement_id}/roe.json"))
    except Exception as exc:  # noqa: BLE001
        raise RoENotAuthorizedError(
            f"no rules of engagement recorded for {engagement_id!r} — call "
            "load_rules_of_engagement first"
        ) from exc
    roe = RulesOfEngagement(**data)
    now = time.time()
    if not (roe.window_start <= now <= roe.window_end):
        raise RoENotAuthorizedError(
            f"rules of engagement for {engagement_id!r} are not currently active "
            f"(authorized window {roe.window_start}-{roe.window_end}, now {now})"
        )
    return roe


async def plan_attack_path(
    engagement_id: str, objective: str, in_scope_assets: list[str], memory: BackendProtocol
) -> dict:
    """ATT&CK-technique-based planning only — never execution. Hard-gated:
    raises RoENotAuthorizedError if there is no currently-active RoE for
    engagement_id. Out-of-scope assets are rejected outright rather than
    silently dropped, so a caller can't accidentally widen scope by typo or
    by the model guessing additional targets."""
    roe = await _get_active_roe(engagement_id, memory)

    out_of_scope = [a for a in in_scope_assets if a not in roe.authorized_targets]
    if out_of_scope:
        raise RoENotAuthorizedError(
            f"these assets are not in the authorized scope for {engagement_id!r}: {out_of_scope}"
        )

    techniques = map_attack_techniques(objective)
    if roe.allowed_techniques:
        techniques = [t for t in techniques if t[0] in roe.allowed_techniques]

    plan = {
        "engagement_id": engagement_id,
        "objective": objective,
        "in_scope_assets": in_scope_assets,
        "steps": [
            {"technique_id": tid, "technique_name": tname, "targets": in_scope_assets}
            for tid, tname in techniques
        ],
        "note": "Planning only — no exploitation/execution is performed by this tool.",
    }
    await memory.write(f"/redteam/{engagement_id}/plan_{uuid.uuid4().hex[:8]}.json", json.dumps(plan))
    return plan


async def generate_engagement_report(engagement_id: str, memory: BackendProtocol) -> dict:
    roe_data = json.loads(await memory.read(f"/redteam/{engagement_id}/roe.json"))
    plans = [
        json.loads(await memory.read(p))
        for p in await memory.glob(f"/redteam/{engagement_id}/plan_*.json")
    ]

    report = {
        "engagement_id": engagement_id,
        "authorized_targets": roe_data["authorized_targets"],
        "point_of_contact": roe_data["point_of_contact"],
        "plans_produced": len(plans),
        "techniques_covered": sorted(
            {step["technique_id"] for plan in plans for step in plan["steps"]}
        ),
        "plans": plans,
    }
    await memory.write(f"/redteam/{engagement_id}/report.json", json.dumps(report))
    return report


def build_redteam_tools(memory: BackendProtocol) -> list[Tool]:
    async def _load_roe_handler(
        authorized_targets: list[str],
        allowed_techniques: list[str],
        point_of_contact: str,
        window_start: float,
        window_end: float,
    ) -> dict:
        return asdict(
            await load_rules_of_engagement(
                authorized_targets, allowed_techniques, point_of_contact, window_start, window_end, memory
            )
        )

    async def _plan_handler(engagement_id: str, objective: str, in_scope_assets: list[str]) -> dict:
        return await plan_attack_path(engagement_id, objective, in_scope_assets, memory)

    async def _report_handler(engagement_id: str) -> dict:
        return await generate_engagement_report(engagement_id, memory)

    return [
        Tool(
            name="load_rules_of_engagement",
            description=(
                "Record an explicit, time-bounded authorization scope before any planning can "
                "happen. Required before plan_attack_path will work for a given engagement_id."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "authorized_targets": {"type": "array", "items": {"type": "string"}},
                    "allowed_techniques": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "ATT&CK technique IDs permitted; empty list = no technique restriction beyond scope.",
                    },
                    "point_of_contact": {"type": "string"},
                    "window_start": {"type": "number", "description": "Unix timestamp."},
                    "window_end": {"type": "number", "description": "Unix timestamp."},
                },
                "required": [
                    "authorized_targets",
                    "allowed_techniques",
                    "point_of_contact",
                    "window_start",
                    "window_end",
                ],
            },
            handler=_load_roe_handler,
            concurrency_safe=False,
            owner="domain",
        ),
        Tool(
            name="plan_attack_path",
            description=(
                "Plan (never execute) an ATT&CK-technique-based attack path against in-scope "
                "assets for an authorized engagement. Fails if no active rules of engagement "
                "exist, or if any requested asset is out of scope."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "engagement_id": {"type": "string"},
                    "objective": {"type": "string"},
                    "in_scope_assets": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["engagement_id", "objective", "in_scope_assets"],
            },
            handler=_plan_handler,
            concurrency_safe=True,
            owner="domain",
            requires_approval=True,
        ),
        Tool(
            name="generate_engagement_report",
            description="Generate the engagement report: scope, plans produced, techniques covered.",
            input_schema={
                "type": "object",
                "properties": {"engagement_id": {"type": "string"}},
                "required": ["engagement_id"],
            },
            handler=_report_handler,
            concurrency_safe=True,
            owner="domain",
        ),
    ]
