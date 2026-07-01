# Aegis Agent — Architecture

## 0. What this is

Aegis is two things layered on top of each other:

1. **`aegis_core`** — a general-purpose, provider-agnostic agent framework. Not tied to any
   one LLM vendor, any one tool catalog, or any one application shape (CLI, daemon, bot,
   library). It is meant to be the substrate you'd reach for to build *any* agent.
2. **`aegis_sentinel`** — a deep, multi-domain cybersecurity analyst agent (SOC triage,
   vulnerability management, incident response/forensics, red team, data security) built
   entirely on `aegis_core`, as the flagship proof that the framework is real and not just
   a diagram.

This document is the comprehensive architecture for both. See `FEATURE_MATRIX.md` for the
literal feature-by-feature sourcing (which repo an idea came from, overlap vs. divergence,
implemented-now vs. roadmap), and `ROADMAP.md` for what's deferred.

## 1. Sources and how they were used

Four reference agent projects were studied in depth: **NousResearch/hermes-agent**,
**openclaw/openclaw**, **langchain-ai/deepagents**, and a fourth repo (`tanbiralam/claude-code`)
that was excluded as a direct architectural source — research turned up strong evidence
it's an extracted/leaked build of Anthropic's proprietary Claude Code rather than an
independent project, and copying its specific implementation into an OSS project is both a
legal and an ethical problem. Where ideas from that repo are also generic, widely-known
agent-engineering patterns independently present in the three legitimate sources (e.g.
cache-aware prompt assembly, deferring tool schemas to save tokens), they're used and
attributed to the legitimate sources, not to it.

The governing principle for the other three: **where they converge on an idea, that idea is
probably load-bearing — build it in as a default. Where they diverge, the divergence becomes
a configurable strategy, not a forced choice.** Concretely this means most of `aegis_core`'s
subsystems are defined as a small interface (Python `Protocol`/ABC) with multiple
interchangeable implementations, selected via config, rather than one hardcoded approach.

## 2. Design principles

- **Everything pluggable, nothing one-size-fits-all.** Context compaction, memory backend,
  subagent isolation model, permission policy, prompt assembly — all interfaces with swappable
  implementations. A consumer building a customer-support bot and a consumer building a
  pentesting agent should be able to pick different strategies for each without forking the
  framework.
- **Cache economics are an architectural concern, not an afterthought.** All three legitimate
  reference projects independently arrived at: stable content first, volatile content last,
  an explicit boundary marker between them. This is baked into the prompt builder and the
  compaction subsystem from day one, not bolted on later.
- **Audit and reversibility are first-class, not optional.** Because the flagship use case is
  a security agent, every tool call, permission decision, and delegation is logged in a
  structured, append-only way by default. This isn't a security-agent-only feature — it lives
  in `aegis_core` because any serious agent benefits from it.
- **The loop is a state machine you can read, not a recursive black box.** Explicit `State`
  object, named transitions, auditable recovery paths (budget exhaustion, retries,
  compaction-triggered restarts) — modeled on the most mature of the loops studied.
- **Subagents are isolated by default, not by discipline.** Fresh state on spawn, explicit
  allow-listed state/tool sharing, depth and concurrency bounds always enforced, never
  optional.
- **No silent feature loss.** If a tool is unavailable, a compaction pass is skipped, or a
  permission is denied, the framework surfaces *why* rather than quietly doing nothing.

## 3. Layered architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Surfaces     CLI · library API · aegis_gateway (WebSocket daemon +  │
│               CLI/Telegram/Discord/Slack channels) · MCP · ACP       │
├─────────────────────────────────────────────────────────────────────┤
│  Application layer  aegis_sentinel (SOC / Vuln / IR / RedTeam / Data │
│                      / cross-domain orchestrator)                    │
├─────────────────────────────────────────────────────────────────────┤
│  aegis_core                                                          │
│  ┌───────────────┐ ┌───────────────┐ ┌───────────────────────────┐  │
│  │  Agent Loop   │ │  Middleware   │ │  Prompt Builder            │  │
│  │  (state mach.)│ │  Pipeline     │ │  (tiered, cache-aware)     │  │
│  └───────────────┘ └───────────────┘ └───────────────────────────┘  │
│  ┌───────────────┐ ┌───────────────┐ ┌───────────────────────────┐  │
│  │  Tool Registry│ │  Context      │ │  Memory Backends            │  │
│  │  + Dispatch   │ │  Engine       │ │  (virtual filesystem)       │  │
│  └───────────────┘ └───────────────┘ └───────────────────────────┘  │
│  ┌───────────────┐ ┌───────────────┐ ┌───────────────────────────┐  │
│  │  Subagents    │ │  Permissions  │ │  Provider Adapters          │  │
│  │  delegate/MoA/│ │  / Approval / │ │  (Anthropic/OpenAI, more    │  │
│  │  swarm/bg/ACP │ │  Checkpoints  │ │  via the same interface)    │  │
│  └───────────────┘ └───────────────┘ └───────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

Every box in the `aegis_core` grid is a separate module with a defined interface. The loop
depends on interfaces, never on a specific implementation — `Agent(provider=..., tools=...,
context_engine=..., memory=..., permission_policy=...)` is fully composable.

## 4. Core loop (`aegis_core/loop.py`, `state.py`, `budget.py`)

Modeled as an explicit state machine (the clearest of the studied loops did this specifically
to make recovery paths auditable rather than buried in recursive `continue`s). One iteration:

1. Drain any pending **steering messages** (injected by the caller mid-turn, e.g. a human
   correcting course before the next model call) — convergent pattern across two of the three
   sources, implemented with a configurable drain mode (`all` vs `one-at-a-time`).
2. Normalize the message list — repair malformed tool-call JSON, fix broken role-alternation,
   strip provider-incompatible fields. Defensive normalization that two sources do
   independently before every call; cheap insurance against provider quirks.
3. Call the provider, streaming. Tool calls are dispatched as they arrive when safe to do so
   (see §5), not only after the stream ends.
4. Check the **budget**: a dual cap — `max_iterations` (hard ceiling) and a finer-grained
   `iteration_budget` with a "grace call" — when the budget is nearly spent, the model gets one
   more call with an explicit nudge to wrap up gracefully, rather than being cut off mid-thought.
5. Drain any pending **follow-up messages** (queued only for once the agent would otherwise
   stop) and loop, or terminate with a typed reason (`completed`, `budget_exhausted`,
   `aborted`, `blocked_by_hook`).

Interrupts are checked at the top of every iteration and around every tool call, with
per-call cancellation tokens so a long-running tool can be killed without tearing down the
whole agent.

## 5. Tool system (`aegis_core/tools/`)

- **Registration**: a `Tool` is a plain dataclass — name, JSON-schema input, async/sync
  handler, optional `check_fn` (an availability probe, e.g. "is this API reachable"). Probes
  are TTL-cached with a flake-suppression grace window: a single transient probe failure
  doesn't strip an entire tool mid-session.
- **Dispatch**: `ToolRegistry.dispatch()` is the single chokepoint — every exception from a
  handler is converted into a structured `{"error": ...}` result rather than propagating, so a
  buggy tool can never crash the loop.
- **Concurrency**: tool calls in one turn are partitioned into a parallel-safe batch
  (`tool.concurrency_safe == True`, executed via a bounded worker pool) and a serial remainder
  — convergent pattern, since several sources independently identified that blindly
  parallelizing all tool calls is unsafe for stateful tools (e.g. sequential file edits).
- **Deferred/searchable tools** (opt in via `Agent(tool_search=True)`): once a catalog grows
  large (default threshold 40), full schemas for tools marked `deferred=True` are withheld
  from the prompt; the model calls the built-in `tool_search`/`tool_resolve` meta-tools to
  find and unlock a schema on demand, after which it stays visible for the rest of the
  session. Verified end to end (search by keyword and by `select:name`, then call the
  resolved tool).
- **Streaming tool dispatch**: with `AgentConfig(enable_streaming=True)`, concurrency-safe tool
  calls are kicked off via `asyncio.create_task` the moment the provider's stream yields a
  completed tool-call chunk, rather than waiting for the whole response to finish; unsafe
  calls run inline to preserve ordering. `AnthropicProvider.stream()` implements genuine
  per-content-block incremental streaming (text deltas and per-tool-call dispatch, verified
  against the documented SDK event shape); any provider that only implements `complete()`
  still works via `Provider.stream()`'s default, which just delivers the full response as a
  compatible one-shot stream.
- **Guardrails layer**: pre-dispatch hooks (permission check, destructive-op checkpointing)
  and post-dispatch hooks (logging, result-size capping) are middleware, not hardcoded into
  dispatch — see §9.

## 6. Memory backends (`aegis_core/memory/`)

A single `BackendProtocol` — `ls`, `read`, `write`, `edit`, `delete`, `grep`, `glob` — is the
uniform interface for *all* state an agent might want to persist or share: scratch files,
large tool results that would otherwise blow the context budget, cross-session notes,
durable memory. Multiple implementations:

- `StateBackend` — ephemeral, lives in the in-process agent state (cheapest, default for
  single-turn/short-lived agents).
- `FilesystemBackend` — real disk, for agents that need durable local artifacts.
- `CompositeBackend` — routes by path prefix to different backends (e.g. `/memory/` →
  persistent store, everything else → ephemeral) — this was the most elegant idea found in
  any of the sources studied: one interface, policy-driven routing, no special-casing in the
  tools that use it.
- `SQLiteBackend` — durable, survives process restarts; sync `sqlite3` calls offloaded via
  `asyncio.to_thread` so it's a drop-in async backend alongside the others. Usable directly or
  as a `CompositeBackend` route target for the paths that need durability.

**Correction (found during a later audit, not implemented despite earlier claims here):**
automatic oversized-tool-result eviction to the backend (head/tail preview kept inline, full
content readable on demand, source: deepagents) was designed and documented but never actually
wired into `ToolRegistry.dispatch()` or the loop — there is no code path that does this today.
`middleware.py`'s docstring lists "result eviction" only as an example of what a middleware
*could* implement, not something built. See `ROADMAP.md`.

Separately, `aegis_core/persistence/session_store.py`'s `SQLiteSessionStore` persists the
*transcript itself* (not files) — pass `session_store=`/`session_id=` to `Agent` and it saves
after every iteration; `Agent.resume(session_store=, session_id=)` reconstructs an agent with
its prior messages/iteration count/scratch restored.

## 7. Context engine / compaction (`aegis_core/context/`)

A `ContextEngine` ABC (`should_compress`, `compress`, `update_from_response`) is the pluggable
interface. Two convergent default strategies ship:

- **Tiered compression** (default): a cheap, zero-LLM-call pre-pass (dedupe identical tool
  outputs by hash, replace stale tool results outside a protected tail with one-line
  summaries, strip oversized multimodal payloads) — only if that's insufficient does an actual
  LLM summarization call run, over everything before a token/message-floor-bounded protected
  tail that's always aligned to message-role boundaries so tool-call/result pairs are never
  split. If the summarization call itself fails, a deterministic non-LLM fallback summary is
  used so compaction never hard-fails the turn. An anti-thrashing guard skips compression
  if the last two passes each saved under a configurable threshold, to avoid spending tokens
  compressing on every single turn near the limit.
- **Branch-tree sessions** (alternate strategy, selectable): transcripts are a navigable DAG
  rather than a flat log — `BranchTreeContextEngine` tracks every appended message as a node
  keyed by ID, and `navigate_to(node_id, state)` jumps to an earlier point and continues from
  there, auto-summarizing the abandoned branch into `branch_summaries` rather than discarding
  it. Useful for interactive/exploratory use (a human steering an investigation) more than for
  unattended pipelines, hence a strategy choice rather than the default. Verified: tracking,
  navigation, and abandoned-branch summarization all round-trip correctly.

A bad/misbehaving custom context engine can be wrapped in `QuarantineContextEngine`, which
falls back to a pass-through engine after `max_failures` exceptions rather than taking the
whole agent down — convergent defensive pattern, verified to quarantine after one failure and
stay quarantined for the rest of the session.

## 8. Prompt builder (`aegis_core/prompts/`)

Three-tier assembly, independently arrived at by every legitimate source studied:

1. **Stable** — identity, tool-usage guidance, operating principles. Rendered once, cached for
   the life of the session.
2. **Context** — caller-supplied system content, discovered project/repo context
   (`AGENTS.md`-style files).
3. **Volatile** — anything that changes turn to turn: memory snapshot, timestamp (deliberately
   date-precision, not minute-precision, so it doesn't needlessly invalidate prompt caches),
   session metadata.

An explicit cache-boundary marker separates stable+context from volatile content so
prefix-caching backends (and self-hosted backends with KV-cache reuse) get maximum benefit.
Model-family-conditional guidance (`prompts/model_guidance.py`) is pattern-matched against
`provider.model` at `Agent` construction time and injected as a stable section automatically
— different model families fail in characteristically different ways (some stop early, some
over-narrate, some drift from a strict tool schema), verified to inject correctly per family.

Skills-as-catalog (`prompts/skills.py`): pass `Agent(skill_catalog=SkillCatalog(memory=...))`
and a lightweight index (name/description/path/content-hash) is discovered from
`/skills/*/SKILL.md` frontmatter and refreshed into a dedicated, in-place-replaced prompt
section every iteration — cheap, since discovery only reads frontmatter, never full skill
bodies. The model reads a skill's full body on demand via its own file-reading tools, and only
needs to re-read if the hash in the index changes. Verified: index renders, refreshes without
duplicating the section turn over turn.

## 9. Middleware pipeline (`aegis_core/middleware.py`)

The composability idea worth borrowing most directly: every cross-cutting concern (permission
checks, checkpointing before destructive ops, logging, prompt-section injection, tool-result
eviction) is a `Middleware` with optional hooks — `before_tool_call`, `after_tool_call`,
`wrap_model_call` — registered into an ordered pipeline, rather than hardcoded into the loop.
Building a new capability (e.g. a SOC-specific evidence-chain-of-custody logger) means writing
one middleware class, not patching the core loop.

## 10. Subagents (`aegis_core/subagents/`)

Three distinct, complementary mechanisms — convergent finding: no single source treats
"subagent" as one concept, they each have 2+ flavors:

- **`delegate`** — spawn a full isolated child agent (fresh message history, restricted
  toolset, own budget) for a bounded subtask. Depth-bounded (default 1, configurable),
  concurrency-bounded, parent only sees the delegation call and the final result, never the
  child's intermediate tool calls. Children share the parent's memory backend (so they can
  read/write files the parent sees) but never its message history — this isolation model
  (fresh state, shared filesystem) is the cleanest balance of context-cost vs. coordination
  found across the sources.
- **Mixture-of-advisors (MoA)** — fan the *current turn's* context out to N reference
  models/configs that are explicitly advisory-only (cannot call tools, cannot act), whose
  responses are appended as input for the acting model's next step. Cheap way to get
  ensemble reasoning without giving up single-agent execution control — useful for, e.g.,
  getting a second opinion on a severity classification before it's finalized.
- **Orchestrator mode** — a delegate child may itself be granted the `delegate` tool
  (recursive orchestration up to the same depth cap), with role-based tool policy fixed at
  spawn time so a restored/resumed session can't silently regain orchestrator privileges.
  `aegis_sentinel/orchestrator.py` is the real, verified consumer of this: it adapts each
  domain's own `build_X_agent(provider, memory)` factory to `delegate_task`'s generic
  `agent_factory(*, tools, memory, system_prompt_extra)` shape, so delegating to a domain
  spins up that domain's actual toolset, not a generic child.
- **Background/async subagents** (`subagents/orchestrator.py`) — `BackgroundOrchestrator.spawn()`
  returns a `task_id` immediately (an `asyncio.Task` under the hood) and reports completion via
  an optional push callback rather than requiring the caller to poll; in-process, since there's
  no remote LangGraph-Platform-style deployment target here. Verified: immediate `RUNNING`
  status, later `COMPLETED` with result, `on_complete` callback fired.
- **Peer-messaging swarm** (`subagents/swarm.py`) — `AgentSwarm` is a registry of named
  mailboxes; `build_swarm_tools(swarm, name)` gives an agent `send_message`/`check_messages`
  tools to talk to other named participants directly, not only through a parent/child
  hierarchy. Verified: message sent by one participant is received by another via
  `check_messages`.
- **Cache-sharing fork subagents** (`subagents/cache_sharing.py`) — `fork_subagent(parent)`
  builds a child that reuses the parent's exact rendered system prompt (captured once via
  `FrozenPromptBuilder`, whose `build()` always returns that frozen string) plus its full tool
  registry and memory — for cache-economical forks (background summarization, the
  self-improvement review pass below) where reuse, not isolation, is the goal. Verified:
  child's rendered prompt is byte-identical to the parent's.
- **Cross-vendor delegation (ACP)** — see §15.

## 11. Background skill-review self-improvement (`aegis_core/self_improvement.py`)

After a turn, `run_background_skill_review(parent, memory_tools=...)` spawns a cache-sharing
fork of the parent restricted to memory/skill-writing tools only (never the parent's full
toolset), and asks it whether the most recent turn is worth turning into a new skill
(`write_skill`) or a durable memory note (`write_memory`) — both real default tools
(`build_memory_writing_tools`) that append to `/skills/<name>/SKILL.md` /
`/memory/MEMORY.md` via the shared `BackendProtocol`. Source: hermes-agent's
`background_review.py`. A failed review is swallowed, never propagated to the parent's own
turn. Verified end to end: a scripted review session writes a real skill file the way a real
LLM call deciding "this is worth remembering" would.

## 12. Permissions / approval (`aegis_core/permissions/`)

A dedicated approval subsystem, not a scattered set of `if dangerous: ask()` checks:
pattern-based risk classification, per-session approval state held in a context-scoped
variable (not a process-global/env-var — deliberately, so concurrent sessions in the same
process can't race or leak approvals into each other), a `PersistentAllowList` backed by any
`BackendProtocol` (cross-session, distinct from the in-session allow-list), and an LLM-based
auto-approve path that only ever fires for calls the static rules already classified as LOW
risk (it narrows an already-narrow set, it never substitutes for classification, and returns
`None`/defers to a human on any ambiguous or failed provider call rather than silently
approving). Any "bypass approval" mode is frozen at process start, specifically so nothing
running *during* a session — including a tool's own output — can flip it on. This matters
more for a security agent than almost anywhere else: it must not be possible for ingested,
untrusted alert/log data to talk the agent into raising its own privileges mid-session. All of
the above verified, including the LLM auto-approve path with a scripted approving provider.

**Checkpointing** (`aegis_core/checkpoints.py`): `CheckpointMiddleware` snapshots a file's
prior content to the memory backend before a destructive tool call touches it (configurable
tool-name → path-argument-key mapping), and `restore_checkpoint(memory, checkpoint_id)` rolls
it back — verified with a full write → simulate-mutation → restore round trip.

**Secret redaction** (`aegis_core/permissions/redaction.py`, added 2026-06-30 from a
documentation re-audit finding hermes-agent does this and we didn't): `redact_arguments()`
recursively scrubs secret-shaped values (cloud provider keys, tokens, private key blocks,
generic `keyword: value` credential assignments) from a tool call's arguments before they're
shown to a human via `ask_callback`, embedded in an LLM auto-approve prompt, or written to
`aegis_sentinel`'s audit log — the tool itself still executes with the real, unredacted value.
Verified with 4 tests, including one confirming a secret never reaches a third-party LLM
provider via the auto-approve path.

*Known, documented limitation*: `ApprovalRule`'s argument-glob matching is plain `fnmatch` over
raw string values, which is not robust against value normalization tricks (abbreviated flags,
aliasing) the way a real command parser would be — a real risk *if* a shell-command tool is
ever added, moot today since no tool anywhere in this project executes shell commands. Flagged
directly in `approval.py`'s module docstring for whoever adds one.

## 13. Provider adapters (`aegis_core/providers/`)

A thin `Provider` interface (`complete`, `stream`, `count_tokens`) with adapters per vendor.
The framework's loop, tools, and middleware never talk to a vendor SDK directly — this is
what makes "pick any model" actually true rather than aspirational. `AnthropicProvider` and
`OpenAIProvider` both implement real completion *and* real incremental streaming against each
vendor's documented API (structurally correct; needs the deployer's own API key to run live —
see `ROADMAP.md`). Both retry transient failures (rate limits, connection errors, 5xx) on
`complete()` with exponential backoff via `tenacity` — deliberately not on `stream()`, since
retrying a request that already yielded partial chunks to the caller would duplicate output
without buffering/replay logic this framework doesn't have. `MockProvider` (scripted or
callback-driven) is the no-network provider used throughout this project's own examples/tests.

## 14. Surfaces

`aegis_core` ships a CLI, and the loop is UI-agnostic by design (callback-based
streaming/progress/approval hooks) specifically so more surfaces could be added without
touching the loop — which is exactly what `aegis_gateway` (§16) is: a second surface built
without a single change to `aegis_core/loop.py`.

## 15. Protocol integrations (`aegis_core/integrations/`)

- **MCP client** (`integrations/mcp_client.py`) — `MCPClient` spawns an MCP server as a stdio
  subprocess, discovers its tools via the real `mcp` SDK, and wraps each as a plain
  `aegis_core.tools.base.Tool` (prefixed `mcp_<server>_<tool>`, `deferred=True` by default so a
  large MCP tool catalog doesn't blow the prompt budget) — from that point on, dispatch,
  middleware, approval, and audit logging treat an MCP tool exactly like a built-in one.
  **Verified live end to end** against a real local `FastMCP` server
  (`examples/mcp_demo_server.py` + `examples/mcp_client_demo.py`): the server is spawned as a
  real subprocess, its tool is discovered over the real wire protocol, and an `Agent` calls it
  through the normal dispatch path.
- **ACP client** (`integrations/acp_client.py`) — `ACPAgentDelegate` spawns an external
  ACP-compliant agent CLI (Claude Code, Codex, Gemini CLI, OpenCode, etc., in ACP mode) via the
  real `agent-client-protocol` SDK's `spawn_agent_process`, implements the ACP `Client` role
  (buffers streaming `session_update` text, routes file read/write through a
  `BackendProtocol`, resolves permission requests via an `ApprovalPolicy`), and returns the
  external agent's final response — the cross-vendor counterpart to `subagents.delegate`,
  avoiding reimplementing every vendor's own harness by just speaking the protocol they
  already expose (source: openclaw's `sessions_spawn(runtime="acp")`). **Verified live** against
  a real toy ACP-conformant agent (`examples/acp_demo_agent.py`) — the actual
  initialize → new_session → prompt → session_update wire sequence, including a real timing
  race between the prompt RPC response and the trailing notification that had to be handled
  with a short grace wait. No third-party agent CLI ships in this environment, so a real vendor
  hasn't been exercised, but the protocol itself has.

## 16. Multi-channel gateway (`aegis_gateway/`)

Source: openclaw's architecture — one long-lived daemon owns the actual `Agent` sessions;
every surface (CLI, bot, admin tool) is a thin client speaking a shared WebSocket protocol
(`aegis_gateway/protocol.py`: `user_message` in, `text_delta`/`tool_start`/`tool_end`/`done`/
`error` out), rather than embedding the loop in each surface.

- `GatewayServer` (`server.py`) keeps one `Agent` per `session_id`, and funnels all outgoing
  frames for a connection through a single writer task draining an `asyncio.Queue` — Agent's
  `on_text_delta`/`on_tool_start`/`on_tool_end` callbacks fire from sync contexts, so without a
  single writer, concurrent `ws.send()` calls could interleave or race connection teardown.
  **Verified live** over a real local WebSocket: `examples/gateway_demo.py` starts the server,
  connects a real client, and gets a correct streamed tool-call + response round trip.
- `channels/cli_channel.py` is a complete, real, no-credential-needed interactive terminal
  client — the one channel that's fully runnable in any environment.
- `channels/telegram.py`, `channels/discord.py`, `channels/slack.py` are real implementations
  against each platform's actual documented protocol (Telegram Bot API long-polling; Discord
  Gateway v10 Hello/Identify/Heartbeat/Dispatch handshake, minimal — no reconnect/resume/
  sharding; Slack Socket Mode via `apps.connections.open`), each gated on the deployer's own
  bot credentials. Each was checked against the **real, live endpoint** using invalid/no
  credentials specifically to confirm the request/response shape is currently correct
  (Telegram's structured 404, Discord's actual Hello frame, Slack's actual `invalid_auth`
  response) — as much verification as is honestly possible without the user's own account.

## 17. `aegis_sentinel` — the cybersecurity analyst agent

Built entirely on `aegis_core` as a consumer, not a fork. Structure:

- **Personas/domains**, all real, not one fixed workflow: `soc_triage`, `vuln_management`,
  `incident_response`, `red_team`, `data_security`, plus a cross-domain `orchestrator`. Each
  domain is a system-prompt module + a real tool bundle + domain-specific middleware, selected
  at agent construction time via `aegis_sentinel.personas.build_domain_agent(domain, provider=...)`.
- **Why domains, not one generic "security agent" prompt**: the domains have genuinely
  different operating postures — SOC triage is fast/high-volume/false-positive-tolerant,
  incident response is slow/evidence-preserving/chain-of-custody-sensitive, red team is
  adversarial-simulation planning hard-gated by explicit rules of engagement, vuln management
  is live-data-driven prioritization, data security is pattern-based discovery/classification.
  Forcing them into one prompt produces a mediocre agent at all five; separate domains let each
  be deep. (`aegis_sentinel/tools/soc.py`'s `ATTACK_KEYWORD_MAP` — used by both `soc_triage` and
  `red_team`'s planning — now spans all 14 MITRE ATT&CK Enterprise tactics with ~40 curated
  techniques, up from an initial ~10; the full ~600-technique STIX corpus is still a roadmap
  item, see `ROADMAP.md`.)
- **Cross-domain orchestration** (`aegis_sentinel/orchestrator.py`) — the `orchestrator` persona
  routes via a real `delegate` tool built on `aegis_core.subagents.delegate.delegate_task`,
  e.g. handing a SOC-escalated alert to `incident_response` as a delegated child that shares
  the case through the common memory backend. **Verified end to end**: an orchestrator
  delegates to a real `incident_response` child agent, which opens a case, pivots on an
  indicator, and its case file is independently readable from the shared memory backend after
  the run.
- **Audit-first by construction**: every tool call in every `aegis_sentinel` domain runs
  through `AuditLogMiddleware` (timestamped, hash-chained action log) by default — appropriate
  for a domain where "what did the agent actually look at and do" must be reconstructable.
- **Red team's authorization gate is enforced in code, not policy**: `plan_attack_path` raises
  `RoENotAuthorizedError` if there's no rules-of-engagement record, if it's expired, or if any
  requested target is out of scope — verified for all three failure modes plus the success
  path — and is additionally marked `requires_approval=True` for defense in depth.
- **Vulnerability management uses live data, not fabricated scores**: `assess_exploitability`
  calls the real CISA KEV catalog and FIRST.org EPSS API (both public, no key required) —
  verified live against a known KEV-listed CVE (Log4Shell) and an old, unlisted one, producing
  correctly different exploitability scores with cited evidence.

See `FEATURE_MATRIX.md` for the full per-domain status table and `ROADMAP.md` for what's
credential-gated vs. genuinely unbuilt.

## 18. Non-goals

- Not a commercial product; no telemetry phone-home, no licensing gates.
- Not aiming to be a drop-in replacement for any single studied project — it's a synthesis,
  and will diverge from all of them over time.
- Not shipping real SIEM/EDR/ticketing connectors in this iteration — `aegis_sentinel`'s tools
  are built against clear interfaces with documented integration points; wiring a specific
  vendor API is left to whoever deploys it (or a follow-up iteration).
