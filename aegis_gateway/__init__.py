"""aegis_gateway: an always-on daemon that owns Agent sessions and exposes
them to thin clients (CLI, channel adapters, admin UI) over a shared
WebSocket protocol — rather than embedding the agent loop in each surface.

Source: openclaw's gateway architecture. Optional dependency: `pip install
aegis-agent[gateway]` (the `websockets` package). See ARCHITECTURE.md
section 13 / ROADMAP.md.
"""

from aegis_gateway.protocol import GatewayMessage
from aegis_gateway.server import GatewayServer

__all__ = ["GatewayMessage", "GatewayServer"]
