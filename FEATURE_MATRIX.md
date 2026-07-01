# Feature Matrix

Legend — **Status**: `core` = implemented and verified · `credential-gated` =
fully implemented against the real, documented protocol/API but requires
the deployer's own account/token to exercise live · `partial` = a real
subset is implemented, broader coverage is explicitly deferred ·
**Pattern**: `convergent` = present in 2+ sources independently (built in
as default) · `divergent` = source-specific approach (built as a selectable
strategy, not forced).

`tanbiralam/claude-code` (the 4th repo originally studied) is excluded as a
direct source per the provenance finding in `ARCHITECTURE.md` §1.

## Core loop

| Feature | Source(s) | Pattern | Status | Location |
|---|---|---|---|---|
| Explicit state-machine loop | hermes-agent, openclaw | convergent | core | `loop.py` |
| Dual budget cap + "grace call" | hermes-agent | divergent | core | `budget.py` |
| Steering / follow-up queues | hermes-agent, openclaw | convergent | core | `loop.py`, `state.py` |
| Defensive message normalization | hermes-agent | divergent | core | `loop.py` |
| Mid-tool interrupt/cancellation | hermes-agent, openclaw | convergent | core | `loop.py` |
| Per-turn streaming tool dispatch | hermes-agent, openclaw | convergent | core | `loop.py::_call_model_streaming`, verified with MockProvider + real Anthropic/OpenAI stream() implementations |

## Tool system

| Feature | Source(s) | Pattern | Status | Location |
|---|---|---|---|---|
| Central registry + dispatch chokepoint | all three | convergent | core | `tools/registry.py` |
| Structured-error tool results | hermes-agent | divergent | core | `tools/registry.py` |
| `check_fn` probes + flake suppression | hermes-agent | divergent | core | `tools/registry.py` |
| Concurrency-safe parallel batching | openclaw | convergent | core | `tools/registry.py` |
| Declarative "why hidden" diagnostics | openclaw | divergent | core | `tools/registry.py::availability_report` |
| Deferred/searchable tool schemas | hermes-agent | divergent | core | `tools/search.py`, wired into `loop.py` (`tool_search`/`tool_resolve` meta-tools), verified end to end |

## Memory & context

| Feature | Source(s) | Pattern | Status | Location |
|---|---|---|---|---|
| Virtual-filesystem `BackendProtocol` | deepagents | divergent | core | `memory/backend.py` |
| State / Filesystem / Composite backends | deepagents | divergent | core | `memory/*_backend.py` |
| SQLite-backed persistent backend | — (new) | — | core | `memory/sqlite_backend.py`, live-tested |
| SQLite session (transcript) persistence | openclaw, hermes-agent | convergent | core | `persistence/session_store.py`, `Agent.resume()` |
| Pluggable `ContextEngine` | openclaw | divergent | core | `context/engine.py` |
| Tiered compression | hermes-agent | divergent | core | `context/tiered_compression.py` |
| Branch-tree session navigation | openclaw | divergent | core | `context/branch_tree.py`, verified (track/navigate/abandon-summary) |
| Auto-quarantine of misbehaving engine | openclaw | divergent | core | `context/quarantine.py`, verified |
| Persistent memory (write_memory/MEMORY.md) | hermes-agent, openclaw, deepagents | convergent | core | `self_improvement.py::build_memory_writing_tools` |
| Full RAG/embedding-based memory search | hermes-agent, openclaw | convergent | **partial** — append/read only, no semantic search yet | roadmap |

## Prompting

| Feature | Source(s) | Pattern | Status | Location |
|---|---|---|---|---|
| Tiered prompt assembly + cache boundary | all three | convergent | core | `prompts/builder.py` |
| Date-precision volatile timestamps | hermes-agent | divergent | core | `prompts/builder.py` |
| Model-family-conditional guidance | hermes-agent | divergent | core | `prompts/model_guidance.py`, wired into `loop.py`, verified |
| Skills-as-catalog, hash-versioned lazy load | openclaw, deepagents | convergent | core | `prompts/skills.py`, wired into `loop.py` (`skill_catalog=` param, refreshed every turn), verified |

## Multi-agent / subagents

| Feature | Source(s) | Pattern | Status | Location |
|---|---|---|---|---|
| `delegate_task` isolated child agent | all three | convergent | core | `subagents/delegate.py` |
| Fresh-state isolation, shared memory | deepagents | divergent | core | `subagents/delegate.py` |
| Depth/concurrency bounds, role policy | all three | convergent | core | `subagents/delegate.py` |
| MoA advisory fan-out | hermes-agent | divergent | core | `subagents/moa.py` |
| Async/background subagents, push completion | openclaw, hermes-agent | convergent | core | `subagents/orchestrator.py`, verified |
| Peer-messaging swarm | hermes-agent | divergent | core | `subagents/swarm.py`, verified |
| Cache-sharing fork subagents | hermes-agent | divergent | core | `subagents/cache_sharing.py`, verified byte-identical prompt reuse |
| Cross-vendor delegation (ACP) | openclaw | divergent | core | `integrations/acp_client.py`, live-verified against a real ACP wire-protocol round trip (toy agent; no third-party agent CLI ships in this environment) |
| Background skill-review self-improvement | hermes-agent | divergent | core | `self_improvement.py`, verified |

## Permissions / safety

| Feature | Source(s) | Pattern | Status | Location |
|---|---|---|---|---|
| Dedicated approval subsystem | hermes-agent | divergent | core | `permissions/approval.py` |
| Context-scoped approval state | hermes-agent | divergent | core | `permissions/approval.py` |
| Bypass frozen at process start | hermes-agent | divergent | core | `permissions/approval.py` |
| Pattern-based risk classification | hermes-agent | divergent | core | `permissions/approval.py` |
| Persistent allow-list | hermes-agent | divergent | core | `permissions/approval.py::PersistentAllowList`, verified |
| LLM-based low-risk auto-approve | hermes-agent | divergent | core | `permissions/approval.py::_llm_auto_approve`, verified |
| Checkpoint/snapshot before destructive ops | hermes-agent | divergent | core | `checkpoints.py`, verified restore round trip |

## Providers, protocols & surfaces

| Feature | Source(s) | Pattern | Status | Location |
|---|---|---|---|---|
| Vendor-agnostic provider interface | hermes-agent | divergent | core | `providers/base.py` |
| Anthropic adapter (complete + real streaming) | — | — | **credential-gated** — structurally correct against the documented SDK, needs a real API key to run live | `providers/anthropic_provider.py` |
| OpenAI adapter (complete + real streaming) | — | — | **credential-gated** | `providers/openai_provider.py` |
| MCP client integration | hermes-agent, openclaw | convergent | core | `integrations/mcp_client.py`, **live-verified** end to end against a real local MCP server |
| ACP cross-vendor delegation | hermes-agent, openclaw | convergent | core | `integrations/acp_client.py`, **live-verified** against a real toy ACP agent |
| Multi-channel gateway daemon | openclaw | divergent | core | `aegis_gateway/server.py`, **live-verified** over a real local WebSocket |
| CLI channel | — | — | core | `aegis_gateway/channels/cli_channel.py` |
| Telegram channel | openclaw | divergent | **credential-gated** — real Bot API code, request shape verified against the live endpoint (401 on invalid token), needs a real bot token for a full run | `aegis_gateway/channels/telegram.py` |
| Discord channel | openclaw | divergent | **credential-gated** — real minimal Gateway v10 protocol (no reconnect/resume/sharding), Hello handshake verified against the live gateway, needs a real bot token + MESSAGE_CONTENT intent for a full run | `aegis_gateway/channels/discord.py` |
| Slack channel (Socket Mode) | openclaw | divergent | **credential-gated** — real Socket Mode protocol, `apps.connections.open` shape verified against the live endpoint, needs real bot+app tokens for a full run | `aegis_gateway/channels/slack.py` |

## `aegis_sentinel` security-agent domains

| Domain | Status | Notes |
|---|---|---|
| SOC alert/log triage | core | parse/correlate/classify/recommend + live IOC enrichment (see below) |
| Vulnerability management | core | **live-tested** against the real CISA KEV catalog and FIRST.org EPSS API (no keys required) |
| Incident response / forensics | core | case management, chain-of-custody, timeline, IOC pivot — reuses soc_triage's alert format directly, verified |
| Red team | core | planning-only, hard-gated by a time-bounded rules-of-engagement record + human approval; all four gate conditions (no RoE / valid / out-of-scope / expired) verified |
| Data security | core | real Luhn-validated PII detection, credential-pattern secret scanning, least-privilege access-grant review, verified |
| Cross-domain orchestrator | core | routes via `aegis_core.subagents.delegate_task`, verified end to end (SOC escalation -> incident_response child, shared case via memory backend) |
| ATT&CK technique coverage | **partial** | curated set spanning all 14 Enterprise tactics (~40 techniques); not the full ~600-technique STIX corpus |
| Threat-intel enrichment (`enrich_ioc`) | **credential-gated** | real AbuseIPDB + VirusTotal adapters, endpoint shape verified live (401 on invalid key), needs the deployer's own API keys |

## What's genuinely still open (see ROADMAP.md for detail)

- Full embedding/vector-search RAG memory (only append/read persistent notes today).
- Full MITRE ATT&CK STIX corpus (vs. the curated ~40-technique set).
- Live end-to-end runs of anything requiring a credential this environment doesn't have: Anthropic/OpenAI API keys, Telegram/Discord/Slack bot tokens, AbuseIPDB/VirusTotal API keys, a real third-party ACP agent CLI.
- Discord channel reconnect/RESUME handling and sharding (only a single-shard, no-resume implementation exists).
