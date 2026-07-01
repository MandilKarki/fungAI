from aegis_core.memory.state_backend import StateBackend
from aegis_sentinel.tools.soc import (
    classify_severity,
    correlate_alerts,
    map_attack_techniques,
    parse_alert,
    recommend_response,
)


def test_map_attack_techniques_covers_multiple_tactics():
    text = "mimikatz lsass dump followed by lateral movement via psexec, then exfil over c2"
    hits = {tid for tid, _name in map_attack_techniques(text)}
    assert "T1003" in hits  # credential access
    assert "T1021" in hits  # lateral movement
    assert "T1071" in hits  # command and control
    assert "T1041" in hits  # exfiltration


async def test_parse_and_correlate_shared_indicator():
    memory = StateBackend()
    await parse_alert(
        {"id": "a1", "source": "edr", "category": "c2", "severity": "medium", "description": "beacon to 1.2.3.4"},
        memory,
    )
    await parse_alert(
        {"id": "a2", "source": "firewall", "category": "c2", "severity": "low", "description": "conn to 1.2.3.4 blocked"},
        memory,
    )
    clusters = await correlate_alerts(memory, window_seconds=3600)
    assert len(clusters) == 1
    assert set(clusters[0]["alert_ids"]) == {"a1", "a2"}
    assert "1.2.3.4" in clusters[0]["shared_indicators"]["ip"]


async def test_classify_severity_kev_style_escalation():
    memory = StateBackend()
    alert = await parse_alert(
        {
            "id": "a1",
            "source": "edr",
            "category": "credential access",
            "severity": "medium",
            "description": "mimikatz lsass dump detected",
            "asset": "finance-01",
        },
        memory,
    )
    result = await classify_severity(alert, cluster_size=1, asset_criticality="high")
    assert result["severity"] == "critical"
    assert any(t["id"] == "T1003" for t in result["attack_techniques"])
    assert len(result["rationale"]) >= 3


def test_recommend_response_matches_severity_playbook():
    critical_steps = recommend_response("critical")
    assert any("Isolate" in s for s in critical_steps)
    low_steps = recommend_response("low")
    assert critical_steps != low_steps
