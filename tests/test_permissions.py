from aegis_core.memory.state_backend import StateBackend
from aegis_core.permissions.approval import (
    ApprovalPolicy,
    ApprovalRule,
    Decision,
    PersistentAllowList,
    RiskLevel,
)
from aegis_core.providers.mock_provider import MockProvider
from aegis_core.state import Message


async def test_default_policy_denies_without_ask_callback():
    policy = ApprovalPolicy()
    assert await policy.resolve("some_tool", {}, ask_callback=None) is False


async def test_explicit_allow_rule():
    policy = ApprovalPolicy(rules=[ApprovalRule(pattern="safe_*", decision=Decision.ALLOW)])
    assert await policy.resolve("safe_read", {}, ask_callback=None) is True


async def test_explicit_deny_rule_ignores_ask_callback():
    policy = ApprovalPolicy(rules=[ApprovalRule(pattern="danger_*", decision=Decision.DENY)])

    async def always_yes(name, args, risk):
        return True

    assert await policy.resolve("danger_delete", {}, ask_callback=always_yes) is False


async def test_persistent_allow_list_survives_across_policy_instances():
    memory = StateBackend()
    pal = PersistentAllowList(memory=memory)
    policy_a = ApprovalPolicy(persistent_allow_list=pal)
    assert await policy_a.resolve("tool_x", {}, ask_callback=None) is False

    await policy_a.allow_permanently("tool_x")

    # A brand new ApprovalPolicy backed by the same memory should also allow it.
    policy_b = ApprovalPolicy(persistent_allow_list=PersistentAllowList(memory=memory))
    assert await policy_b.resolve("tool_x", {}, ask_callback=None) is True


async def test_llm_auto_approve_only_fires_for_low_risk_and_defers_on_ambiguous_reply():
    approve_provider = MockProvider(response_fn=lambda m, t: Message(role="assistant", content="APPROVE"))
    policy = ApprovalPolicy(
        rules=[ApprovalRule(pattern="safe_*", decision=Decision.ASK, risk=RiskLevel.LOW)],
        auto_approve_low_risk=True,
        auto_approve_provider=approve_provider,
    )
    assert await policy.resolve("safe_read", {}, ask_callback=None) is True

    ambiguous_provider = MockProvider(response_fn=lambda m, t: Message(role="assistant", content="not sure"))
    policy_ambiguous = ApprovalPolicy(
        rules=[ApprovalRule(pattern="safe_*", decision=Decision.ASK, risk=RiskLevel.LOW)],
        auto_approve_low_risk=True,
        auto_approve_provider=ambiguous_provider,
    )
    # ambiguous reply -> defer to ask_callback, which is None here -> denied, not silently approved
    assert await policy_ambiguous.resolve("safe_read", {}, ask_callback=None) is False


def test_bypass_all_approval_requires_env_var_frozen_at_import():
    from aegis_core.permissions.approval import bypass_all_approval

    # In the default test environment (no AEGIS_BYPASS_APPROVAL set), bypass must be off.
    assert bypass_all_approval() is False
