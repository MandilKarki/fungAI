from aegis_core.permissions.approval import ApprovalPolicy, ApprovalRule, Decision, RiskLevel
from aegis_core.permissions.redaction import redact_arguments, redact_value
from aegis_core.providers.mock_provider import MockProvider
from aegis_core.state import Message


def test_redact_value_catches_common_secret_shapes():
    assert redact_value("my key is AKIAABCDEFGHIJKLMNOP") == "my key is [REDACTED]"
    assert redact_value("token: ghp_" + "a" * 40) == "token: [REDACTED]"
    assert "[REDACTED]" in redact_value("password: hunter2_super_secret_value")
    assert redact_value("nothing sensitive here") == "nothing sensitive here"


def test_redact_arguments_is_recursive_and_preserves_structure():
    arguments = {
        "path": "/configs/app.env",
        "content": "AWS_KEY=AKIAABCDEFGHIJKLMNOP",
        "nested": {"list": ["clean value", "api_key: sk-" + "b" * 25]},
    }
    redacted = redact_arguments(arguments)
    assert redacted["path"] == "/configs/app.env"  # not secret-shaped, untouched
    assert "[REDACTED]" in redacted["content"]
    assert redacted["nested"]["list"][0] == "clean value"
    assert "[REDACTED]" in redacted["nested"]["list"][1]


async def test_ask_callback_never_sees_raw_secret_in_arguments():
    seen = {}

    async def capture_ask_callback(tool_name, arguments, risk):
        seen["arguments"] = arguments
        return True

    policy = ApprovalPolicy(rules=[ApprovalRule(pattern="write_config", decision=Decision.ASK, risk=RiskLevel.MEDIUM)])
    approved = await policy.resolve(
        "write_config", {"content": "password: hunter2_super_secret"}, ask_callback=capture_ask_callback
    )
    assert approved is True
    assert "hunter2_super_secret" not in seen["arguments"]["content"]
    assert "[REDACTED]" in seen["arguments"]["content"]


async def test_llm_auto_approve_prompt_never_contains_raw_secret():
    seen_prompts = []

    def capturing_response(messages, tools):
        seen_prompts.append(messages[-1].content)
        return Message(role="assistant", content="APPROVE")

    policy = ApprovalPolicy(
        rules=[ApprovalRule(pattern="safe_*", decision=Decision.ASK, risk=RiskLevel.LOW)],
        auto_approve_low_risk=True,
        auto_approve_provider=MockProvider(response_fn=capturing_response),
    )
    await policy.resolve("safe_write", {"token": "sk-" + "c" * 25}, ask_callback=None)
    assert seen_prompts, "expected the auto-approve provider to be called"
    assert "sk-" + "c" * 25 not in seen_prompts[0]
    assert "[REDACTED]" in seen_prompts[0]
