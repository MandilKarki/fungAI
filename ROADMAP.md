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
- **Automatic oversized-tool-result eviction — corrected, was falsely
  documented as done.** An earlier pass of `ARCHITECTURE.md` claimed this was
  implemented; a later audit found it was never actually wired into
  `ToolRegistry.dispatch()` or the loop. Fixed in the doc; still needs real
  code. Source pattern: deepagents (evict results over a token threshold to
  the memory backend, leave a head/tail preview inline).
- **No generic memory-read path.** `self_improvement.py` gives agents
  `write_memory`/`write_skill`, but there's no matching `read_memory` tool
  wired by default, and nothing automatically injects `/memory/MEMORY.md`
  into the system prompt the way deepagents' `MemoryMiddleware` does. Written
  memories are effectively write-only right now unless a specific domain
  builds its own reader.
- **No todo-list tool/middleware.** deepagents uses (an upstream LangChain)
  `TodoListMiddleware` — a `write_todos` tool plus a tracked state field —
  for step-by-step task tracking. Not built here at all.
- **`tenacity` retry/backoff — FIXED.** `AnthropicProvider.complete()` and
  `OpenAIProvider.complete()` now retry transient failures (rate limits,
  connection errors, 5xx) up to 3 times with exponential backoff (1-10s),
  using `tenacity.AsyncRetrying`. Deliberately **not** applied to `stream()`
  on either provider — retrying a request that already yielded partial
  chunks to the caller would either duplicate output or need buffering/
  replay logic this framework doesn't have; documented in both methods'
  docstrings. Verified the retry mechanism itself (3rd-attempt success)
  with a standalone repro; not verified against a live rate-limited API
  call, since that needs a real account (see §4).
- **`pydantic` — removed.** Declared as a base dependency, never imported
  anywhere in the codebase (every data shape here uses plain dataclasses).
  Removed from `pyproject.toml` rather than left declared-but-dead.
- **Lint was never run — now clean.** `ruff` (already a declared dev
  dependency) had never actually been executed; running it found and fixed
  2 real unused imports (`dataclasses.field` in `tools/base.py`, `asyncio`
  in a test file). Zero findings remain.
- **No CI, no Dockerfile.** Tests only run when someone manually runs
  `pytest`; there's no GitHub Actions workflow and no containerized way to
  deploy this.

## 2. Deliberately not mirrored from deepagents (not oversights — real scope decisions)

- **Sandboxed code/shell execution.** deepagents ships partner integrations
  for Modal, Daytona, Runloop, and QuickJS/WASM sandboxing, plus a
  `SandboxBackend`/`LocalShellBackend` with an `execute` method on its
  backend protocol. `aegis_core`'s `BackendProtocol` has no `execute` method
  at all, and no domain in `aegis_sentinel` has a shell/code-execution tool
  — so there's currently no attack surface a sandbox would need to contain.
  **This must be built before anyone adds a shell/code-exec tool to this
  project, not after.**
- **LangGraph itself.** deepagents is built on LangGraph's `create_agent`/
  `AgentMiddleware`/`Pregel` runtime end to end. `aegis_core` deliberately
  has its own from-scratch loop instead (see `ARCHITECTURE.md` for why) — so
  LangGraph-specific plumbing (its `DeltaChannel` checkpoint-diffing
  optimization, its `HumanInTheLoopMiddleware`, its required-middleware
  allowlist protecting against harness misconfiguration) has no direct
  equivalent here. `aegis_core/persistence/session_store.py` re-serializes
  the full message list on every save, which has a similar (unaddressed)
  cost-grows-with-history-length property that `DeltaChannel` exists to fix
  in LangGraph — worth profiling if very long sessions become common.
- **`RubricMiddleware` (self-grading loop).** deepagents can spawn a grader
  sub-agent to evaluate a transcript against a rubric and force another
  iteration via injected feedback before yielding control. Not built here.
- **Remote async subagents.** deepagents' `AsyncSubAgentMiddleware` targets a
  LangGraph-Platform deployment (start/check/cancel remote runs via
  `langgraph_sdk`). `aegis_core/subagents/orchestrator.py`'s
  `BackgroundOrchestrator` is in-process `asyncio.Task`-based only — no
  remote deployment target exists in this project.
- **Glob-pattern file-permission ACLs.** deepagents' `FilesystemPermission`
  is a first-match-wins ACL keyed on (operation, path-glob, allow/deny/
  interrupt). `aegis_core`'s `ApprovalPolicy` is tool-name-and-argument-glob
  based instead — a different, more general shape, not a port of theirs.
- **Auto-added default subagent.** deepagents auto-registers a
  `general-purpose` subagent unless the caller disables it. `aegis_core`
  never auto-wires a delegate tool into any `Agent` — every consumer
  (like `aegis_sentinel/orchestrator.py`) builds and registers it explicitly.

## 3. Findings from a live documentation re-audit (2026-06-30)

All three source repos had new commits the same day this audit ran — they're
actively developed, so this was a fresh check, not a rehash. Three parallel
research passes re-scanned each repo's refreshed docs/code and, where one
exists, its official hosted documentation site, specifically to catch drift
since the original research and anything the first pass missed.

### Security findings — status as of 2026-06-30

- **Secrets shown in plaintext during approval prompts — FIXED.**
  `aegis_core/permissions/redaction.py` now scrubs secret-shaped values
  (AWS/GitHub/Slack/OpenAI-Anthropic-style keys, private key blocks, generic
  `keyword: value` credential assignments) before arguments are ever passed
  to `ask_callback`, embedded in an LLM auto-approve prompt, or written to
  `aegis_sentinel`'s audit log. The tool itself still receives the real,
  unredacted value — only display/logging paths are affected. Verified with
  4 new tests (`tests/test_redaction.py`), including one confirming a secret
  never reaches the LLM auto-approve provider's prompt.
- **Approval-bypass via abbreviated flags — documented, not fixed (no
  live attack surface exists yet).** hermes-agent shipped a fix for GNU
  long-option abbreviation bypassing naive command classification. Our
  `ApprovalPolicy.classify()` is `fnmatch`-glob based over argument string
  values and has the same theoretical weakness, but there is currently no
  shell-command tool anywhere in this project for it to apply to (see §2 —
  no tool executes shell commands at all). Rather than write a fix with
  nothing to test it against, the risk and the requirement (canonicalize via
  `shlex` + flag-alias normalization *before* trusting a glob match, if a
  shell tool is ever added) are now documented directly in
  `approval.py`'s module docstring so whoever adds that tool sees the
  warning first.

### Cross-project-validated gaps (2+ of the 3 repos have real, mature versions)

- **Sandboxed execution.** Confirmed as a bigger gap than originally scoped
  — openclaw's version alone is a mature subsystem (Docker/SSH/OpenShell
  backends, `mode`/`scope`/`workspaceAccess` controls, hardened bind-mount
  blocking resistant to symlink escapes, blocks `docker.sock`/`/etc`/
  `~/.ssh` specifically, plus a deliberate "elevated" bypass escape hatch).
  Useful context, not an excuse: deepagents' own `deepagents_talon` runtime
  daemon documents **itself** as having no sandbox-backed isolation either
  — so this is a genuinely hard, unsolved-by-default problem industry-wide,
  not something we're unusually behind on.
- **Observability/tracing.** openclaw exports OpenTelemetry + Prometheus;
  deepagents wires LangSmith tracing through both its runtime and its eval
  harness. We have `AuditLogMiddleware`'s hash-chained log and nothing else
  — no traces, no metrics export, no dashboards.

### New capability gaps, not previously identified

- **Background memory consolidation ("Dreaming"), openclaw** — a real
  alternative to embedding/RAG for the memory gap in §1: a cron-scheduled,
  three-phase pipeline (stage recent signals → score and promote to
  `MEMORY.md` using frequency/relevance/recency/diversity/consolidation
  signals → reflective theme extraction). Worth evaluating as an option
  *alongside* vector search, not just vector search by default — it's
  cheaper (no embedding index to maintain) and produces human-readable
  output.
- **Scheduling/cron, deepagents (`deepagents_talon`)** — agent-facing cron
  tools for recurring tasks. We have zero scheduling capability anywhere.
- **Agent evaluation/benchmarking harness, deepagents (`deepagents_evals`)**
  — scores real trajectories (tool calls, file mutations, final response)
  against a rubric, with CI integration and Harbor/Terminal-Bench 2.0
  sandboxed benchmark suites. We have 48 unit/integration tests and nothing
  that scores end-to-end agent *behavior* quality. Distinct category from
  "more tests."
- **Durable, resumable approvals, deepagents** — their HITL `interrupt`
  mode pauses the entire graph via LangGraph checkpointing and can resume
  later, potentially from a different process. Our `ApprovalPolicy.resolve()`
  blocks synchronously in-process; an approval can't survive a restart.
  This is a durability-model gap, not just "a different permission shape."
- **Model failover, openclaw** — automatic fallback across providers/auth
  profiles on failure. Not built; our `Provider` interface has no failover
  wrapper.
- **Cost tracking in dollars, openclaw** — we track tokens/iterations, not
  spend.
- **Loop detection, openclaw** — detecting a stuck/repeating agent. Not built.
- **LSP integration, hermes-agent** — attaching real language servers for
  code-editing intelligence. Not applicable unless a code-editing domain is
  ever added; noted for completeness.
- **Credential pooling / OAuth token rotation, hermes-agent** — multi-key
  pools per provider with routing and fallback. Our `Provider` adapters take
  exactly one API key each.
- **Skills marketplace, hermes-agent** — browsable/installable skills
  (official + community), vs. our catalog which only discovers what's
  already on a memory backend. No discovery/install layer.
- **Remote/OAuth-protected MCP servers, hermes-agent** — our
  `integrations/mcp_client.py` only supports stdio-launched local servers;
  hermes-agent's MCP client handles a full OAuth flow (401 handling,
  token refresh) for remote MCP servers. `FEATURE_MATRIX.md`'s MCP row was
  updated to reflect this as understated for hermes-agent specifically.
- **Declarative MCP config loading, deepagents (`deepagents_talon`)** — MCP
  servers configured via a manifest directory rather than registered in code.

### Explicitly not gaps — correctly out of scope

- **Channel/provider count** (openclaw ~35 channels/~50 providers,
  hermes-agent ~15 channels vs. our 3 channels + CLI, 2 providers). A
  breadth difference from deliberately smaller scope, not a missing
  capability — the architecture supports adding more of either without
  redesign.
- **"Mantis," openclaw** — their internal visual QA/E2E-testing
  infrastructure for their own CI. Engineering tooling, not an agent-facing
  feature; irrelevant to parity.
- **Production deployment tooling** (Docker/K8s/Ansible install docs,
  openclaw's "ClawHub" plugin marketplace) — real gaps, but already covered
  by the existing "no CI, no Dockerfile" item in §1; not duplicating here.

## 4. Implemented for real, but not exercised live in this environment

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
