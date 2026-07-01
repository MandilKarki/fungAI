# Aegis Agent

A comprehensive, provider-agnostic agent framework (`aegis_core`), with a
deep, multi-domain cybersecurity analyst agent (`aegis_sentinel`) as its
flagship use case. Not a commercial product — open source, MIT licensed.

Start here:

- **`ARCHITECTURE.md`** — the full design: every subsystem, why it's shaped
  the way it is, and which reference projects' ideas it draws on.
- **`FEATURE_MATRIX.md`** — feature-by-feature sourcing and status: which
  repo an idea came from, convergent (default) vs. divergent (selectable
  strategy), and core / credential-gated / partial.
- **`ROADMAP.md`** — what's genuinely left: real gaps vs. things that are
  fully built but need a credential this environment doesn't have to run live.

## Quick start

```bash
pip install -e ".[anthropic,openai,mcp,acp,gateway,dev]"   # or just [dev] for the no-network demos

# Run end-to-end with no API key or external services, using MockProvider:
python examples/minimal_agent.py            # core loop + a tool call
python examples/soc_triage_demo.py          # SOC triage domain, full pipeline + audit log
python examples/gateway_demo.py             # real WebSocket gateway round trip
python examples/mcp_client_demo.py          # real MCP server spawn + tool call
python examples/acp_client_demo.py          # real ACP agent spawn + prompt round trip

# Run against a real model:
export ANTHROPIC_API_KEY=sk-...
python -m aegis_core.cli "What's 17 * 24?"

# Run the automated test suite (48 tests; 2 hit the real public CISA
# KEV/EPSS APIs and skip automatically if there's no network):
pytest
```

## Layout

```
aegis_core/         the framework
  loop.py            explicit state-machine agent loop (streaming + non-streaming)
  tools/             registry, dispatch, deferred/searchable schemas
  memory/            virtual filesystem: State/Filesystem/Composite/SQLite backends
  persistence/       durable session (transcript) storage
  context/           tiered compression, branch-tree, auto-quarantine
  prompts/           tiered cache-aware builder, model-family guidance, skills catalog
  subagents/         delegate, MoA, background, swarm, cache-sharing fork
  permissions/       approval policy, persistent allow-list, LLM auto-approve
  providers/         Anthropic / OpenAI / Mock adapters
  integrations/      MCP client, ACP client
  self_improvement.py, checkpoints.py

aegis_gateway/       always-on daemon + WebSocket protocol + channels
  server.py, client.py, channels/{cli,telegram,discord,slack}.py

aegis_sentinel/      the security analyst agent, built on aegis_core
  domains/           soc_triage, vuln_management, incident_response,
                     red_team, data_security — all real, not stubs
  orchestrator.py    cross-domain routing via aegis_core's delegate_task
  tools/             real domain logic (correlation, live EPSS/KEV, RoE
                     gating, PII/secret detection, threat-intel enrichment)

examples/            runnable demos — most need no API key or external service
```

## What's real vs. what needs your credentials

Everything above is implemented for real — no placeholder logic, no fake
data. Most of it is also *verified* in this environment: the framework's
own examples exercise a real local MCP server, a real ACP protocol round
trip, a real WebSocket gateway, and `aegis_sentinel`'s vulnerability domain
calls the actual public CISA KEV and EPSS APIs live. A handful of pieces are
real code that this sandbox simply has no credentials to run live —
Anthropic/OpenAI API keys, Telegram/Discord/Slack bot tokens, AbuseIPDB/
VirusTotal API keys. See `ROADMAP.md` for the exact list and what each needs.

## Provenance

Architecture and patterns synthesized from three independently-developed
open source agent projects (NousResearch/hermes-agent, openclaw/openclaw,
langchain-ai/deepagents). A fourth project initially considered was excluded
as a direct source after research found strong evidence it's an extracted
build of proprietary, non-open-source code rather than an independent
implementation — see `ARCHITECTURE.md` section 1 for the full reasoning.
