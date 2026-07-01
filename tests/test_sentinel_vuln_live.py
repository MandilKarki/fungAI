"""Live-network tests against the real, public, no-auth CISA KEV and
FIRST.org EPSS APIs. Skipped automatically if the network is unreachable
(e.g. a locked-down office network) rather than failing the whole suite --
everything else in this repo runs fully offline.
"""

import httpx
import pytest

from aegis_core.memory.state_backend import StateBackend
from aegis_sentinel.tools.vuln import assess_exploitability, ingest_scan_results


def _network_available() -> bool:
    try:
        httpx.get("https://api.first.org/data/v1/epss", params={"cve": "CVE-2021-44228"}, timeout=5.0)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _network_available(), reason="no network access to public threat-intel APIs")


async def test_log4shell_scores_as_kev_listed_critical():
    memory = StateBackend()
    [finding] = await ingest_scan_results(
        [{"cve_id": "CVE-2021-44228", "title": "Log4Shell", "severity": "critical", "internet_facing": True}],
        memory,
    )
    result = await assess_exploitability(finding.finding_id, memory, asset_criticality="high")
    assert result["kev_listed"] is True
    assert result["exploitability"] == "critical"
    assert result["epss"]["epss"] > 0.9


async def test_obscure_old_cve_scores_lower_than_log4shell():
    memory = StateBackend()
    findings = await ingest_scan_results(
        [
            {"cve_id": "CVE-2021-44228", "title": "Log4Shell", "severity": "critical"},
            {"cve_id": "CVE-1999-0001", "title": "ancient finding", "severity": "low"},
        ],
        memory,
    )
    results = {
        f.cve_id: await assess_exploitability(f.finding_id, memory, asset_criticality="unknown")
        for f in findings
    }
    assert results["CVE-2021-44228"]["score"] > results["CVE-1999-0001"]["score"]
