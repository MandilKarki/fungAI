"""End-to-end demo of aegis_core's ACP client integration: spawns the toy ACP
agent (acp_demo_agent.py) as a subprocess and delegates one prompt to it via
ACPAgentDelegate, exercising the real wire protocol: initialize -> new_session
-> prompt -> session_update streaming -> final response.

Run: `python examples/acp_client_demo.py`
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aegis_core.integrations.acp_client import ACPAgentDelegate  # noqa: E402

AGENT_SCRIPT = str(Path(__file__).resolve().parent / "acp_demo_agent.py")


async def main() -> None:
    delegate = ACPAgentDelegate(command=sys.executable, args=[AGENT_SCRIPT])
    result = await delegate.delegate("Summarize today's alerts.")
    print("response text:", result.text)
    print("stop reason:", result.stop_reason)
    assert "Summarize today's alerts" in result.text
    assert result.stop_reason == "end_turn"


if __name__ == "__main__":
    asyncio.run(main())
