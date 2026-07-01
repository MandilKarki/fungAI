import time

import pytest

from aegis_core.memory.state_backend import StateBackend
from aegis_sentinel.tools.redteam import (
    RoENotAuthorizedError,
    generate_engagement_report,
    load_rules_of_engagement,
    plan_attack_path,
)


async def test_plan_without_roe_is_blocked():
    memory = StateBackend()
    with pytest.raises(RoENotAuthorizedError):
        await plan_attack_path("no-such-engagement", "phishing", ["host1"], memory)


async def test_plan_within_authorized_scope_succeeds():
    memory = StateBackend()
    roe = await load_rules_of_engagement(
        ["web-01"], ["T1566"], "jane@corp.com", time.time() - 10, time.time() + 3600, memory
    )
    plan = await plan_attack_path(roe.engagement_id, "phishing campaign against web-01", ["web-01"], memory)
    assert plan["steps"]
    assert all(step["technique_id"] == "T1566" for step in plan["steps"])


async def test_plan_against_out_of_scope_asset_is_blocked():
    memory = StateBackend()
    roe = await load_rules_of_engagement(
        ["web-01"], [], "jane@corp.com", time.time() - 10, time.time() + 3600, memory
    )
    with pytest.raises(RoENotAuthorizedError):
        await plan_attack_path(roe.engagement_id, "phishing", ["some-other-host"], memory)


async def test_plan_with_expired_roe_is_blocked():
    memory = StateBackend()
    roe = await load_rules_of_engagement(
        ["host9"], [], "bob@corp.com", time.time() - 1000, time.time() - 10, memory
    )
    with pytest.raises(RoENotAuthorizedError):
        await plan_attack_path(roe.engagement_id, "recon", ["host9"], memory)


async def test_roe_rejects_empty_scope():
    memory = StateBackend()
    with pytest.raises(ValueError):
        await load_rules_of_engagement([], [], "jane@corp.com", time.time(), time.time() + 1, memory)


async def test_engagement_report_reflects_produced_plans():
    memory = StateBackend()
    roe = await load_rules_of_engagement(
        ["web-01"], ["T1566"], "jane@corp.com", time.time() - 10, time.time() + 3600, memory
    )
    await plan_attack_path(roe.engagement_id, "phishing", ["web-01"], memory)
    report = await generate_engagement_report(roe.engagement_id, memory)
    assert report["plans_produced"] == 1
    assert "T1566" in report["techniques_covered"]
