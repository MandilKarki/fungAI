# Roadmap

Nearly everything originally listed here has been implemented — see
`FEATURE_MATRIX.md` for the full status table. What's left falls into two
real categories, not vague future work:

## 1. Genuinely un-implemented

- **Full embedding/vector-search RAG memory.** Today, persistent memory is
  real but simple: `write_memory`/`write_skill` append to
  `/memory/MEMORY.md` and `/skills/*/SKILL.md` via any `BackendProtocol`
  (see `aegis_core/self_improvement.py`), and the skill catalog does
  hash-versioned lazy loading (`aegis_core/prompts/skills.py`, wired into
  the loop). There's no embedding index or semantic search over that
  content yet — retrieval is "the model reads the file," not "the model
  gets the top-k relevant chunks." Building this well means picking an
  embedding provider (or a local model) and a vector store, which is a real
  design decision to make with the user, not a default to sneak in.
- **Full MITRE ATT&CK STIX corpus.** `aegis_sentinel/tools/soc.py`'s
  `ATTACK_KEYWORD_MAP` now spans all 14 Enterprise tactics (~40 curated
  techniques, up from the original ~10), but it's still a curated regex map,
  not the full ~600-technique STIX bundle MITRE publishes. Wiring the real
  corpus means parsing MITRE's STIX JSON (large, includes deprecated
  techniques and relationship graphs) and deciding how to map free-text
  alert descriptions to technique IDs at that scale — likely embedding-based
  matching rather than regex, which ties into the RAG item above.
- **Discord channel robustness.** `aegis_gateway/channels/discord.py`
  implements the real Gateway v10 handshake (Hello/Identify/Heartbeat/
  Dispatch) but has no reconnect/RESUME logic and no sharding — fine for a
  small single-server bot, not production-grade for a bot in many guilds.

## 2. Implemented for real, but not exercised live in this environment

These aren't gaps in the code — they're gaps in *this sandbox's credentials*.
Each is written against the actual documented protocol/API (verified where
possible against real endpoints using invalid credentials, to confirm
request/response shapes), but needs the deployer's own account to run
end-to-end:

- **Anthropic / OpenAI providers** (`aegis_core/providers/`) — need a real
  `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`. Structurally verified against each
  SDK's documented streaming/completion shapes; not run against a live model.
- **Telegram / Discord / Slack channels** (`aegis_gateway/channels/`) — need
  real bot tokens (`TELEGRAM_BOT_TOKEN`, `DISCORD_BOT_TOKEN`,
  `SLACK_BOT_TOKEN`+`SLACK_APP_TOKEN`). Each adapter's request shape was
  checked against the real live endpoint (getting a real 401/Hello-frame
  back), but no message ever round-tripped through an actual chat.
- **AbuseIPDB / VirusTotal enrichment** (`aegis_sentinel/tools/threat_intel.py`)
  — need `ABUSEIPDB_API_KEY` / `VIRUSTOTAL_API_KEY`. Same story: endpoint
  shape confirmed live, no authenticated call made.
- **A real third-party ACP agent** (Claude Code, Codex, Gemini CLI, OpenCode
  in ACP mode) — `aegis_core/integrations/acp_client.py` is verified against
  a toy conformance agent (`examples/acp_demo_agent.py`) that speaks the same
  real wire protocol, but no actual vendor CLI is installed in this
  environment to delegate to.

## Process notes for whoever picks this up next

- `FEATURE_MATRIX.md` is the source of truth for status; update it alongside
  this file when something here gets built or gets a live credential to test
  against.
- Nothing in category 2 needs new code to "finish" — it needs the deployer
  to export an env var and run the relevant example/module once to confirm.
