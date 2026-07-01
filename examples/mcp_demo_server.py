"""A minimal real MCP server (stdio transport), used by
examples/mcp_client_demo.py to verify aegis_core's MCP client integration
end to end. Not launched directly — mcp_client_demo.py spawns it as a
subprocess via stdio_client.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("aegis-demo-server")


@mcp.tool()
def lookup_cve_severity(cve_id: str) -> str:
    """Look up a (fake, demo-only) severity rating for a CVE ID."""
    fake_db = {"CVE-2021-44228": "critical", "CVE-2014-0160": "high"}
    return fake_db.get(cve_id.upper(), "unknown")


if __name__ == "__main__":
    mcp.run(transport="stdio")
