"""Secret redaction for anything shown to a human or sent to an LLM: a tool
call's arguments might contain a credential (an API key passed to a
threat-intel tool, a password in a config being written) that must never be
displayed in plaintext when asking for approval, logged to an audit trail,
or embedded in a prompt sent to a provider for LLM-based auto-approval — even
though the tool itself still needs the real value to execute.

Source: hermes-agent shipped exactly this fix for its approval dialogs; this
closes the same gap in aegis_core. Found during the 2026-06-30 documentation
re-audit — see ROADMAP.md.
"""

from __future__ import annotations

import re

_SECRET_PATTERNS: list[re.Pattern] = [
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),  # GitHub token
    re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}"),  # Slack token
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI/Anthropic-shaped API key
    re.compile(
        r"-----BEGIN (?:RSA|EC|DSA|OPENSSH|PGP) PRIVATE KEY-----[\s\S]*?"
        r"-----END (?:RSA|EC|DSA|OPENSSH|PGP) PRIVATE KEY-----"
    ),
    re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|passwd|authorization)\b"
        r"\s*[:=]\s*['\"]?[A-Za-z0-9/_\-+=]{8,}"
    ),
]

REDACTED = "[REDACTED]"


def redact_value(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted


def redact_arguments(arguments: dict) -> dict:
    """Recursively redact secret-shaped substrings from argument values
    before they're shown to a human, an LLM auto-approve call, or an audit
    log. Keys and structure are preserved; only string values are scanned —
    this is a display/logging safeguard, not a substitute for not passing
    secrets as tool arguments in the first place."""

    def _redact(value):
        if isinstance(value, str):
            return redact_value(value)
        if isinstance(value, dict):
            return {k: _redact(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_redact(v) for v in value]
        return value

    return _redact(arguments)
