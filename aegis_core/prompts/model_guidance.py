"""Model-family-conditional prompt guidance: different model families fail
in characteristically different ways — some stop early and narrate instead
of finishing, some under-use available tools, some drift from a strict
schema. Addressed prompt-side, per family, rather than baked into one
generic prompt. Source: hermes-agent.
"""

from __future__ import annotations

import re

# (regex over the model name, guidance text). Checked in order, first match
# wins — list rather than dict so more specific patterns can be ordered
# ahead of broader ones if that's ever needed.
MODEL_FAMILY_GUIDANCE: list[tuple[str, str]] = [
    (
        r"gemini|gemma",
        "You sometimes stop and summarize before actually finishing a task. "
        "If you still have pending tool calls needed to complete the "
        "user's request, make them — don't describe what you would do "
        "instead of doing it.",
    ),
    (
        r"gpt|o[0-9]|codex|grok",
        "Avoid narrating every intermediate step in prose; prefer making "
        "the next tool call directly. Do not claim a task is complete "
        "unless you've actually verified it (e.g. by reading back a file "
        "you just wrote).",
    ),
    (
        r"claude|sonnet|opus|haiku",
        "Default to taking the next concrete action rather than asking for "
        "permission to proceed, unless the action is irreversible or the "
        "user's intent is genuinely ambiguous.",
    ),
    (
        r"llama|mistral|qwen|deepseek",
        "Strictly follow the tool-call JSON schema provided — do not "
        "invent additional fields or omit required ones. If you're unsure "
        "a tool applies, say so in text rather than calling it with "
        "guessed arguments.",
    ),
]


def guidance_for_model(model_name: str) -> str | None:
    name_lower = model_name.lower()
    for pattern, guidance in MODEL_FAMILY_GUIDANCE:
        if re.search(pattern, name_lower):
            return guidance
    return None
