"""SOC alert/log triage logic: real, deterministic, auditable — not
LLM-dependent stubs.

The agent calls these as tools; the model handles narrative/judgment, these
functions handle the structured, repeatable parts (parsing, IOC extraction,
correlation, scoring) that should not be left to free-form generation in a
security context, where reproducibility matters.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field

from aegis_core.memory.backend import BackendProtocol
from aegis_core.tools.base import Tool

# A curated subset of MITRE ATT&CK Enterprise techniques spanning all 14
# tactics (not the full STIX corpus — that's still a roadmap item, see
# ROADMAP.md), keyed by regex patterns commonly seen in alert text/category
# fields. Broadened from an initial ~10-technique, credential/lateral-
# movement-heavy set to cover every tactic so triage isn't blind to e.g.
# reconnaissance, discovery, or impact-stage activity.
ATTACK_KEYWORD_MAP: dict[str, tuple[str, str]] = {
    # Reconnaissance
    r"port.?scan|network.?scan|active.?scanning": ("T1595", "Active Scanning"),
    r"recon|footprint|osint.*gather": ("T1592", "Gather Victim Host Information"),
    # Resource Development
    r"infrastructure.*stood up|attacker.?controlled.*(domain|server)": ("T1583", "Acquire Infrastructure"),
    r"compromised.*account.*for sale|purchased.*credentials": ("T1586", "Compromise Accounts"),
    # Initial Access
    r"phish|malicious.*attachment|suspicious.*link": ("T1566", "Phishing"),
    r"exploit.*public.?facing|web.?shell.*upload|sqli|sql injection": ("T1190", "Exploit Public-Facing Application"),
    r"\bvpn\b.*(compromise|brute)|exposed.*rdp|external remote service": ("T1133", "External Remote Services"),
    r"valid.*account.*misuse|stolen.*credential.*login": ("T1078", "Valid Accounts"),
    # Execution
    r"powershell.*encoded": ("T1059.001", "Command and Scripting Interpreter: PowerShell"),
    r"exploit.*client|malicious.*document.*macro": ("T1203", "Exploitation for Client Execution"),
    r"malicious.*service.*install|system service abuse": ("T1569", "System Services"),
    # Persistence
    r"persistence|scheduled.*task|registry.*run.*key": ("T1053", "Scheduled Task/Job"),
    r"autostart|startup.*folder|boot.*logon": ("T1547", "Boot or Logon Autostart Execution"),
    r"new.*account.*created|rogue.*admin.*account": ("T1136", "Create Account"),
    # Privilege Escalation
    r"privilege.*escalation|token.*impersonation": ("T1134", "Access Token Manipulation"),
    r"kernel.*exploit|privesc.*exploit": ("T1068", "Exploitation for Privilege Escalation"),
    r"process.*inject|dll.*inject|reflective.*loading": ("T1055", "Process Injection"),
    # Defense Evasion
    r"log.*clear|event.*log.*delet|indicator.*removal": ("T1070", "Indicator Removal"),
    r"obfuscat|base64.*encoded.*payload|packed.*binary": ("T1027", "Obfuscated Files or Information"),
    r"disable.*(av|edr|antivirus|defender)|impair.*defense": ("T1562", "Impair Defenses"),
    # Credential Access
    r"mimikatz|lsass.*dump|credential.*dump": ("T1003", "OS Credential Dumping"),
    r"brute.?force|repeated.*failed.*login": ("T1110", "Brute Force"),
    r"plaintext.*password.*found|credentials.*in.*config": ("T1552", "Unsecured Credentials"),
    r"mfa.*bypass|auth.*process.*modif|pass.?the.?hash": ("T1556", "Modify Authentication Process"),
    # Discovery
    r"system.*info.*discovery|whoami|systeminfo": ("T1082", "System Information Discovery"),
    r"account.*enumerat|net user /domain": ("T1087", "Account Discovery"),
    r"network.*share.*enum|remote.*system.*discovery": ("T1018", "Remote System Discovery"),
    # Lateral Movement
    r"lateral.*movement|psexec|wmi.*exec": ("T1021", "Remote Services"),
    r"lateral.*tool.*transfer|pushed.*tool.*to.*host": ("T1570", "Lateral Tool Transfer"),
    # Collection
    r"staged.*local.*files|collect.*sensitive.*files": ("T1005", "Data from Local System"),
    r"mailbox.*export|email.*collection|outlook.*rule abuse": ("T1114", "Email Collection"),
    r"keylog|input.*capture|clipboard.*capture": ("T1056", "Input Capture"),
    # Command and Control
    r"\bc2\b|command.?and.?control|beacon": ("T1071", "Application Layer Protocol (C2)"),
    r"protocol.*tunnel|dns.*tunnel": ("T1572", "Protocol Tunneling"),
    r"\bproxy\b.*c2|socks.*proxy.*traffic": ("T1090", "Proxy"),
    # Exfiltration
    r"exfil|data.*staging|large.*upload": ("T1041", "Exfiltration Over C2 Channel"),
    r"exfil.*(dropbox|google drive|pastebin|webhook)": ("T1567", "Exfiltration Over Web Service"),
    r"exfil.*(dns|icmp|ftp).*alternative": ("T1048", "Exfiltration Over Alternative Protocol"),
    # Impact
    r"ransomware|file.*encrypt": ("T1486", "Data Encrypted for Impact"),
    r"shadow.*copy.*delet|backup.*deleted|recovery.*disabled": ("T1490", "Inhibit System Recovery"),
    r"wiper|data.*destruction|disk.*wipe": ("T1485", "Data Destruction"),
    r"\bdos\b|denial.*of.*service|flood.*attack": ("T1498", "Network Denial of Service"),
}

ASSET_CRITICALITY_DEFAULT = "unknown"
SEVERITY_RANK = {"informational": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

RESPONSE_PLAYBOOKS: dict[str, list[str]] = {
    "critical": [
        "Isolate affected host(s) from the network immediately.",
        "Disable/reset credentials for any implicated accounts.",
        "Escalate to the incident_response domain — open a formal case.",
        "Preserve volatile evidence (memory, running processes) before remediation.",
        "Block all correlated indicators at perimeter/EDR.",
    ],
    "high": [
        "Isolate or heavily restrict affected host(s).",
        "Block correlated indicators at perimeter/EDR.",
        "Notify asset owner and SOC lead.",
        "Open a case for incident_response if scope is unclear.",
    ],
    "medium": [
        "Block correlated indicators where low-risk to do so.",
        "Monitor affected asset(s) for escalation over the next 24h.",
        "Document and close if no further activity within SLA.",
    ],
    "low": [
        "Log and monitor. No immediate action required.",
        "Re-evaluate if recurrence or correlation with other alerts.",
    ],
    "informational": ["No action required. Retain for trend analysis."],
}

IOC_PATTERNS = {
    "ip": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "domain": re.compile(r"\b(?:[a-zA-Z0-9-]+\.)+(?:com|net|org|io|ru|cn|xyz|info|biz)\b"),
    "hash": re.compile(r"\b[a-fA-F0-9]{32,64}\b"),
    "user": re.compile(r"\buser[:=]\s*([A-Za-z0-9._-]+)", re.IGNORECASE),
}


@dataclass
class NormalizedAlert:
    alert_id: str
    timestamp: float
    source: str
    category: str
    raw_severity: str
    description: str
    indicators: dict[str, list[str]] = field(default_factory=dict)
    asset: str | None = None
    raw: dict = field(default_factory=dict)


def extract_indicators(text: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for kind, pattern in IOC_PATTERNS.items():
        matches = pattern.findall(text)
        if matches:
            found[kind] = sorted(set(matches))
    return found


def map_attack_techniques(text: str) -> list[tuple[str, str]]:
    text_lower = text.lower()
    return [tech for pattern, tech in ATTACK_KEYWORD_MAP.items() if re.search(pattern, text_lower)]


def _shares_indicator(a: NormalizedAlert, b: NormalizedAlert) -> bool:
    for kind, values in a.indicators.items():
        if set(values) & set(b.indicators.get(kind, [])):
            return True
    return False


async def parse_alert(raw_alert: dict, memory: BackendProtocol) -> NormalizedAlert:
    """Normalize a raw alert dict (any source format) into a NormalizedAlert,
    extract indicators, and persist to /alerts/<id>.json so correlate_alerts
    can find it — demonstrates the framework's virtual-filesystem-as-memory
    pattern in real use, not just within aegis_core's own tests."""

    alert_id = str(raw_alert.get("id") or uuid.uuid4().hex[:12])
    text_blob = " ".join(str(v) for v in raw_alert.values() if isinstance(v, (str, int, float)))
    indicators = extract_indicators(text_blob)
    if "user" in raw_alert:
        indicators.setdefault("user", []).append(str(raw_alert["user"]))

    alert = NormalizedAlert(
        alert_id=alert_id,
        timestamp=float(raw_alert.get("timestamp", time.time())),
        source=str(raw_alert.get("source", "unknown")),
        category=str(raw_alert.get("category", "uncategorized")),
        raw_severity=str(raw_alert.get("severity", "informational")).lower(),
        description=str(raw_alert.get("description", text_blob))[:2000],
        indicators=indicators,
        asset=raw_alert.get("asset"),
        raw=raw_alert,
    )
    await memory.write(f"/alerts/{alert_id}.json", json.dumps(asdict(alert)))
    return alert


async def correlate_alerts(memory: BackendProtocol, window_seconds: float = 3600.0) -> list[dict]:
    """Cluster persisted alerts that share at least one indicator within
    `window_seconds` of each other. Deterministic clustering, not an LLM
    guess — correlation is exactly the kind of structured task that should
    be reproducible and auditable in a security tool."""

    paths = await memory.glob("/alerts/*.json")
    alerts = [NormalizedAlert(**json.loads(await memory.read(p))) for p in paths]
    alerts.sort(key=lambda a: a.timestamp)

    clusters: list[list[NormalizedAlert]] = []
    for alert in alerts:
        placed = False
        for cluster in clusters:
            if _shares_indicator(alert, cluster[-1]) and (
                alert.timestamp - cluster[0].timestamp <= window_seconds
            ):
                cluster.append(alert)
                placed = True
                break
        if not placed:
            clusters.append([alert])

    result = []
    for cluster in clusters:
        all_indicators: dict[str, set[str]] = {}
        for a in cluster:
            for kind, values in a.indicators.items():
                all_indicators.setdefault(kind, set()).update(values)
        result.append(
            {
                "alert_ids": [a.alert_id for a in cluster],
                "size": len(cluster),
                "shared_indicators": {k: sorted(v) for k, v in all_indicators.items()},
                "time_span_seconds": cluster[-1].timestamp - cluster[0].timestamp,
                "categories": sorted({a.category for a in cluster}),
            }
        )
    return result


async def classify_severity(
    alert: dict | NormalizedAlert,
    *,
    cluster_size: int = 1,
    asset_criticality: str = ASSET_CRITICALITY_DEFAULT,
) -> dict:
    """Rule-based severity scoring: raw source severity + ATT&CK technique
    weighting + correlation-cluster size + asset criticality. Deterministic
    and explainable — every score ships with a rationale, never a bare
    number, because an unexplained severity score is not actionable."""

    if isinstance(alert, dict):
        alert = NormalizedAlert(**alert)

    base = SEVERITY_RANK.get(alert.raw_severity, SEVERITY_RANK["informational"])
    techniques = map_attack_techniques(alert.description + " " + alert.category)
    technique_bump = min(2, len(techniques))
    cluster_bump = 1 if cluster_size >= 3 else 0
    criticality_bump = {"critical": 2, "high": 1}.get(asset_criticality, 0)

    score = min(4, base + technique_bump + cluster_bump + criticality_bump)
    level = next(k for k, v in SEVERITY_RANK.items() if v == score)

    rationale = [
        f"raw source severity: {alert.raw_severity} (base={base})",
        f"ATT&CK techniques matched: {[t[0] for t in techniques]} (+{technique_bump})",
        f"correlation cluster size: {cluster_size} (+{cluster_bump})",
        f"asset criticality: {asset_criticality} (+{criticality_bump})",
    ]

    return {
        "alert_id": alert.alert_id,
        "severity": level,
        "score": score,
        "attack_techniques": [{"id": t[0], "name": t[1]} for t in techniques],
        "rationale": rationale,
    }


def recommend_response(severity: str) -> list[str]:
    return RESPONSE_PLAYBOOKS.get(severity, RESPONSE_PLAYBOOKS["informational"])


def build_soc_tools(memory: BackendProtocol) -> list[Tool]:
    """Bind the SOC triage logic above to a specific memory backend and wrap
    each as a Tool with a model-facing schema."""

    async def _parse_alert_handler(raw_alert: dict) -> dict:
        alert = await parse_alert(raw_alert, memory)
        return asdict(alert)

    async def _correlate_handler(window_seconds: float = 3600.0) -> list[dict]:
        return await correlate_alerts(memory, window_seconds)

    async def _classify_handler(
        alert_id: str, cluster_size: int = 1, asset_criticality: str = "unknown"
    ) -> dict:
        data = json.loads(await memory.read(f"/alerts/{alert_id}.json"))
        return await classify_severity(
            data, cluster_size=cluster_size, asset_criticality=asset_criticality
        )

    def _recommend_handler(severity: str) -> list[str]:
        return recommend_response(severity)

    def _attack_map_handler(text: str) -> list[dict]:
        return [{"id": t[0], "name": t[1]} for t in map_attack_techniques(text)]

    return [
        Tool(
            name="parse_alert",
            description=(
                "Normalize a raw alert (any source format, as a JSON object) into "
                "structured form, extract indicators of compromise, and persist it "
                "so correlate_alerts can find it later in this session."
            ),
            input_schema={
                "type": "object",
                "properties": {"raw_alert": {"type": "object", "description": "The raw alert fields."}},
                "required": ["raw_alert"],
            },
            handler=_parse_alert_handler,
            concurrency_safe=False,
            owner="domain",
        ),
        Tool(
            name="correlate_alerts",
            description="Cluster all persisted alerts that share an indicator within a time window.",
            input_schema={
                "type": "object",
                "properties": {
                    "window_seconds": {"type": "number", "default": 3600.0}
                },
                "required": [],
            },
            handler=_correlate_handler,
            concurrency_safe=True,
            owner="domain",
        ),
        Tool(
            name="classify_severity",
            description=(
                "Score a previously-parsed alert's severity using ATT&CK mapping, "
                "correlation-cluster size, and asset criticality. Returns a severity "
                "level, numeric score, matched techniques, and a rationale."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "alert_id": {"type": "string"},
                    "cluster_size": {"type": "integer", "default": 1},
                    "asset_criticality": {
                        "type": "string",
                        "enum": ["unknown", "low", "medium", "high", "critical"],
                        "default": "unknown",
                    },
                },
                "required": ["alert_id"],
            },
            handler=_classify_handler,
            concurrency_safe=True,
            owner="domain",
        ),
        Tool(
            name="recommend_response",
            description="Get the standard response playbook steps for a given severity level.",
            input_schema={
                "type": "object",
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": list(RESPONSE_PLAYBOOKS.keys()),
                    }
                },
                "required": ["severity"],
            },
            handler=_recommend_handler,
            concurrency_safe=True,
            owner="domain",
        ),
        Tool(
            name="map_attack_techniques",
            description="Map free text to known MITRE ATT&CK technique IDs (curated subset).",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=_attack_map_handler,
            concurrency_safe=True,
            owner="domain",
        ),
    ]
