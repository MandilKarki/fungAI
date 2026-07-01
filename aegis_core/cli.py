"""Minimal CLI entrypoint.

Kept deliberately thin: the loop is UI-agnostic by design (see
ARCHITECTURE.md section 13) so a daemon/multi-channel gateway surface can be
added later (see ROADMAP.md) without touching aegis_core.loop at all.
"""

from __future__ import annotations

import asyncio
import os
import sys

from aegis_core.loop import Agent
from aegis_core.state import Message


def _build_default_agent() -> Agent:
    from aegis_core.providers.anthropic_provider import AnthropicProvider

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Set ANTHROPIC_API_KEY to use the CLI with a real model.", file=sys.stderr)
        sys.exit(1)

    provider = AnthropicProvider(api_key=api_key)
    return Agent(
        provider=provider,
        on_text_delta=lambda text: print(text, end="", flush=True),
        on_tool_start=lambda name, args: print(f"\n[tool] {name}({args})", file=sys.stderr),
    )


async def _run(prompt: str) -> None:
    agent = _build_default_agent()
    agent.state.append(Message(role="user", content=prompt))
    final_state = await agent.run()
    print()
    print(f"[stopped: {final_state.stop_reason}]", file=sys.stderr)


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: aegis <prompt>", file=sys.stderr)
        sys.exit(1)
    prompt = " ".join(sys.argv[1:])
    asyncio.run(_run(prompt))


if __name__ == "__main__":
    main()
