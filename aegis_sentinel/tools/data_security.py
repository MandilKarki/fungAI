"""Data security tools: real pattern-based sensitive-data discovery
(PII/secrets) and access-grant review — not a placeholder keyword list.
Distinct from SOC/IR's "is something actively attacking us" half of
security; this is the "where is our sensitive data and who can reach it"
half.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from aegis_core.memory.backend import BackendProtocol
from aegis_core.tools.base import Tool

SENSITIVITY_PATTERNS: dict[str, re.Pattern] = {
    "us_ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    "phone_us": re.compile(r"\b\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
}

SECRET_PATTERNS: dict[str, re.Pattern] = {
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
    "private_key_block": re.compile(r"-----BEGIN (?:RSA|EC|DSA|OPENSSH|PGP) PRIVATE KEY-----"),
    "generic_secret_assignment": re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*['\"]?[A-Za-z0-9/_\-+=]{12,}"
    ),
}


def _luhn_valid(raw: str) -> bool:
    digits = [int(d) for d in raw if d.isdigit()]
    if len(digits) < 13:
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def classify_data_sensitivity(sample: str) -> dict:
    """Pattern-based classification of a text sample. Credit-card-shaped
    matches are Luhn-validated to cut the obvious false positives (e.g.
    random 16-digit IDs that aren't actually card numbers)."""
    findings: dict[str, list[str]] = {}
    for kind, pattern in SENSITIVITY_PATTERNS.items():
        matches = pattern.findall(sample)
        if kind == "credit_card":
            matches = [m for m in matches if _luhn_valid(m)]
        if matches:
            findings[kind] = matches[:20]

    categories = set()
    if "us_ssn" in findings or "credit_card" in findings:
        categories.add("pii_sensitive")
    if "email" in findings or "phone_us" in findings:
        categories.add("pii_contact")
    if not findings:
        categories.add("none_detected")

    return {"categories": sorted(categories), "findings": findings}


async def scan_for_exposed_secrets(path: str, memory: BackendProtocol) -> list[dict]:
    """Walk a memory-backend path for credential-shaped patterns, reusing
    BackendProtocol.grep rather than reimplementing file traversal."""
    hits = []
    for kind, pattern in SECRET_PATTERNS.items():
        for file_path, line_no, line in await memory.grep(pattern.pattern, path):
            hits.append(
                {"kind": kind, "path": file_path, "line": line_no, "preview": line.strip()[:200]}
            )
    return hits


@dataclass
class AccessGrant:
    principal: str
    scope: str  # e.g. "s3:*", "repo:read", "admin:*"
    last_used_days_ago: int | None = None


_BROAD_SCOPE_PATTERNS = (
    re.compile(r":\*$"),
    re.compile(r"^\*$"),
    re.compile(r"^(admin|root|owner)", re.IGNORECASE),
)
STALE_THRESHOLD_DAYS = 90


def review_access_grants(grants: list[dict]) -> list[dict]:
    """Least-privilege heuristic: flags grants with wildcard/admin-shaped
    scopes and grants unused for STALE_THRESHOLD_DAYS+, with a reason for
    each flag — never a bare "looks risky"."""
    flagged = []
    for raw in grants:
        grant = AccessGrant(**raw)
        reasons = []
        if any(p.search(grant.scope) for p in _BROAD_SCOPE_PATTERNS):
            reasons.append(f"scope {grant.scope!r} looks overly broad (wildcard/admin-shaped)")
        if grant.last_used_days_ago is not None and grant.last_used_days_ago >= STALE_THRESHOLD_DAYS:
            reasons.append(
                f"unused for {grant.last_used_days_ago} days (>= {STALE_THRESHOLD_DAYS}-day threshold)"
            )
        if reasons:
            flagged.append({"principal": grant.principal, "scope": grant.scope, "reasons": reasons})
    return flagged


async def summarize_exposure(path: str, grants: list[dict], memory: BackendProtocol) -> dict:
    secrets = await scan_for_exposed_secrets(path, memory)
    flagged_grants = review_access_grants(grants)
    score = min(4, len(secrets) // 2 + (2 if flagged_grants else 0) + (1 if secrets else 0))
    return {
        "score": score,
        "exposed_secrets_count": len(secrets),
        "exposed_secrets": secrets,
        "flagged_grants_count": len(flagged_grants),
        "flagged_grants": flagged_grants,
    }


def build_data_security_tools(memory: BackendProtocol) -> list[Tool]:
    def _classify_handler(sample: str) -> dict:
        return classify_data_sensitivity(sample)

    async def _scan_handler(path: str = "/") -> list[dict]:
        return await scan_for_exposed_secrets(path, memory)

    def _review_handler(grants: list[dict]) -> list[dict]:
        return review_access_grants(grants)

    async def _summarize_handler(path: str = "/", grants: list[dict] | None = None) -> dict:
        return await summarize_exposure(path, grants or [], memory)

    return [
        Tool(
            name="classify_data_sensitivity",
            description="Classify a text sample for PII/sensitive-data patterns (SSNs, card numbers, emails, phone numbers).",
            input_schema={
                "type": "object",
                "properties": {"sample": {"type": "string"}},
                "required": ["sample"],
            },
            handler=_classify_handler,
            concurrency_safe=True,
            owner="domain",
        ),
        Tool(
            name="scan_for_exposed_secrets",
            description="Scan a memory-backend path for credential-shaped patterns (API keys, tokens, private keys, password assignments).",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string", "default": "/"}},
                "required": [],
            },
            handler=_scan_handler,
            concurrency_safe=True,
            owner="domain",
        ),
        Tool(
            name="review_access_grants",
            description="Flag overly broad or stale access grants against a least-privilege heuristic.",
            input_schema={
                "type": "object",
                "properties": {
                    "grants": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "principal": {"type": "string"},
                                "scope": {"type": "string"},
                                "last_used_days_ago": {"type": "integer"},
                            },
                            "required": ["principal", "scope"],
                        },
                    }
                },
                "required": ["grants"],
            },
            handler=_review_handler,
            concurrency_safe=True,
            owner="domain",
        ),
        Tool(
            name="summarize_exposure",
            description="Roll up secret exposure and access-grant findings into one scored summary.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "/"},
                    "grants": {"type": "array", "items": {"type": "object"}},
                },
                "required": [],
            },
            handler=_summarize_handler,
            concurrency_safe=True,
            owner="domain",
        ),
    ]
