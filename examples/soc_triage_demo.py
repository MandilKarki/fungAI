"""End-to-end demo of the SOC triage domain agent processing one alert,
driven by MockProvider so it runs with no API key.

Run: `python examples/soc_triage_demo.py`
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aegis_core.loop import AgentConfig  # noqa: E402
from aegis_core.providers.mock_provider import MockProvider  # noqa: E402
from aegis_core.state import Message, ToolCall  # noqa: E402
from aegis_sentinel.domains.soc_triage import build_soc_triage_agent  # noqa: E402

SAMPLE_ALERT = {
    "id": "alert-001",
    "timestamp": 1751270400.0,
    "source": "edr",
    "category": "credential access",
    "severity": "medium",
    "description": (
        "Detected mimikatz-like lsass dump attempt on host WIN-FIN-07 by "
        "user=jsmith, connecting to 45.33.22.11 shortly after."
    ),
    "asset": "WIN-FIN-07",
}

STEPS = ["parse_alert", "correlate_alerts", "classify_severity", "recommend_response"]
_step_index = {"i": 0}


def _last_tool_result(messages: list[Message], tool_name: str) -> dict:
    msg = next(m for m in reversed(messages) if m.role == "tool" and m.name == tool_name)
    return json.loads(msg.content)


def scripted_response(messages, tools):
    """Stands in for the LLM: follows the exact operating procedure laid out
    in SOC_TRIAGE_IDENTITY, one tool call per turn, then a final summary."""
    i = _step_index["i"]
    if i >= len(STEPS):
        severity = _last_tool_result(messages, "classify_severity")["severity"]
        escalate_note = (
            "This is critical/high severity — escalating to incident_response per "
            "operating procedure, see the recommend_response playbook above."
            if severity in ("critical", "high")
            else "Severity does not warrant escalation; monitoring per the playbook above."
        )
        return Message(
            role="assistant",
            content=f"Triage complete for alert-001: severity={severity}. {escalate_note}",
        )

    step = STEPS[i]
    _step_index["i"] += 1

    if step == "parse_alert":
        return Message(
            role="assistant",
            tool_calls=[ToolCall(name="parse_alert", arguments={"raw_alert": SAMPLE_ALERT})],
        )
    if step == "correlate_alerts":
        return Message(
            role="assistant",
            tool_calls=[ToolCall(name="correlate_alerts", arguments={"window_seconds": 3600})],
        )
    if step == "classify_severity":
        alert_id = _last_tool_result(messages, "parse_alert")["alert_id"]
        return Message(
            role="assistant",
            tool_calls=[
                ToolCall(
                    name="classify_severity",
                    arguments={
                        "alert_id": alert_id,
                        "cluster_size": 1,
                        "asset_criticality": "high",
                    },
                )
            ],
        )
    if step == "recommend_response":
        severity = _last_tool_result(messages, "classify_severity")["severity"]
        return Message(
            role="assistant",
            tool_calls=[ToolCall(name="recommend_response", arguments={"severity": severity})],
        )

    raise AssertionError(step)


async def main() -> None:
    provider = MockProvider(response_fn=scripted_response)
    agent = build_soc_triage_agent(provider=provider, config=AgentConfig(max_iterations=10))
    agent.state.append(Message(role="user", content="New alert came in, please triage it."))

    final_state = await agent.run()

    print("--- transcript ---")
    for m in final_state.messages:
        if m.role == "tool":
            print(f"[tool:{m.name}] {m.content}")
        elif m.content:
            print(f"[{m.role}] {m.content}")

    print(f"\n[stopped: {final_state.stop_reason}]")
    assert final_state.stop_reason.value == "completed"

    print("\n--- audit log (/audit/log.jsonl) ---")
    print(await agent.memory.read("/audit/log.jsonl"))


if __name__ == "__main__":
    asyncio.run(main())
