# Architecture

EquiSeek separates product runtime truth from graph checkpoints.

```text
Client -> FastAPI -> runs / plans / events / approvals / artifacts
                      |-> SQLite/WAL (single-user local default)
                      +-> PostgreSQL (optional multi-worker service)
                      |
Worker -- lease ------+----> Plan DAG -> Subtask -> Policy -> Invocation journal
                          |         |          |            +-> Tool
                          |         |          +-> HITL
                          |         +-> task workspace -> Sandbox
                          +-> runtime_state + atomic plan.json
                                   +-> LangGraph Checkpoint / Artifact backend
```

The product `Run` owns externally visible status, lease, budget, terminal reason, and monotonic Event sequence. LangGraph owns graph state and checkpoint persistence. An official checkpoint table is not reused as approval or audit data.

Worker claim uses a short transaction. PostgreSQL multi-worker deployment adds `FOR UPDATE SKIP LOCKED`; local SQLite mode intentionally runs one Worker and does not promise that row-lock behavior. Slow model and Tool work runs outside the claim transaction. The durable invocation is written before work. Read-only interrupted Tools may retry; interrupted side-effecting Tools become `unknown_outcome` and require reconciliation instead of automatic replay.

An execution plan is a validated dependency DAG. The database copy in `runs.runtime_state.plan` is the product truth for API runs; `.state/plan.json` is an inspectable workspace mirror and the store for standalone research runs. A task carries timeout, attempt limit, network flag, isolation requirement, approval boundary, side-effect classification, and idempotency key. Ready tasks may run concurrently within the configured bound. Restarted read-only tasks return to `pending`; an interrupted side effect becomes `unknown_outcome` and blocks dependants.

Each Run owns `shared/`, `tasks/`, `artifacts/`, `.state/`, and `logs/`. Each task owns `input/`, `output/`, `tmp/`, and `logs/`. IDs and paths are canonicalized, managed JSON writes are atomic and quota checked, and public APIs return `workspace://` logical identifiers rather than host paths.

Policy is frozen on Run creation and enforced both when listing model-visible Tools and again immediately before execution. Approval has a unique `(run_id, invocation_id)` boundary plus optimistic versioning. Artifacts keep large output out of prompts and record checksum, size, type, creator, and workspace revision.

The desktop investment page adds a business-scoped long-running harness rather than embedding a general code agent. `InvestmentAgentRuntime` runs a bounded lead-agent loop over registered portfolio, evidence, macro, market-analysis, screening, optional Tavily search, and current-workspace text tools. It progressively activates built-in or user-overridden Skills, writes paired tool events and a Markdown report, and uses compressed investment context. DeepSeek may select the next structured action; the same boundary works with a deterministic local planner. Host shell, arbitrary file access, credentials, and raw portfolio cost fields are excluded. Local persistence remains SQLite plus per-run files; PostgreSQL is only an optional future multi-worker coordination backend.

SQLite/WAL is the supported single-user local-service default. It stores Run, Plan, Event, Lease, approval and Artifact metadata under `~/.equiseek/user-data/equiseek.sqlite3`, enables foreign keys and a bounded busy timeout, and stores LangGraph state separately in `~/.equiseek/user-data/equiseek-checkpoints.sqlite3`. Both stores are verified across cold Coordinator/database restart and use private file permissions. Local mode intentionally uses one Worker; it does not claim PostgreSQL-style competing-worker row locks.

PostgreSQL is an optional deployment extra for multi-user or multiple competing Worker processes that require `FOR UPDATE SKIP LOCKED`. That deployment may also select PostgreSQL-backed LangGraph checkpoints. It is not required by the investment desktop client or a user's local service. Install it with `equiseek[postgres]`; the base local runtime does not load PostgreSQL driver or checkpoint code.

## Harness session kernel

Standalone and desktop Agent runs use a provider-neutral Harness kernel:

```text
LocalAgentRuntime
  -> EventStore -> InvariantRegistry -> append-only session
  -> PromptRegistry -> stable system + dynamic context + tool schemas
  -> Raw Events -> Conversation Surface -> exact Request Header
  -> AgentProfile generation -> SkillRegistry -> one-shot/continuable Subagent
  -> ToolPipeline -> policy/approval -> ordered safe batch -> paired result
  -> Projection -> plan/task/subagent/model/surface views
```

The default `WorkspaceEventStore` writes canonical JSONL under `.state/events.jsonl`, fsyncs every committed record, validates continuous sequence and package-owned lifecycle invariants, and refuses an incomplete/corrupt tail. Plan and delegation JSON are compatibility projections, not replacements for the event stream. The API database event journal remains on its existing contract in this release; no cross-store dual-write is claimed.

Capabilities and lifecycles are explicit seams. Prompt contributions are ordered, layered, reversible, strictly interpolated, and snapshotted before a model call. Dynamic context is appended only when its digest changes. Model history is derived only from the Conversation Surface; raw facts remain append-only, while `surface/replace` can hide obsolete messages without deleting evidence. Every prepared model call writes a credential-free `request/header` and a `model/request` that must reference the exact header sequence.

Agent profiles are published as digest-derived immutable generations. A registration and every child keep the exact snapshot they joined; profile/tool/network/depth authority may only narrow. Skill providers publish complete/incomplete catalog snapshots with last-good retention. Tool policy decisions happen before the call event and handler; only consecutive side-effect-free calls explicitly marked concurrency-safe can overlap, and returned outcomes retain request order. One-shot children always settle through a structured stop reason. Continuable children have a durable descriptor, one activation at a time, FIFO follow-up turns, independent business reports, runtime settlement, and cold resume; an interrupted open turn is recorded as outcome-unknown and is never blindly replayed.

## Investment conversation application layer

The desktop investment page follows the DeerFlow harness pattern without exposing a general-purpose super-agent. A deterministic lead router maps natural-language turns to bounded research capabilities. Per-security threads keep a compact summary and recent turns; a separate user memory stores only explicit durable investment preferences. Structured market evidence, conversation summary, user preferences, active-skill references, and recent messages remain separate prompt channels.

`SkillWorkspace` composes a built-in provider with higher-ranked user filesystem providers. A user package can replace a built-in package by name, or built-ins can be omitted entirely. Skill text never grants tools, network access, or permission to bypass investment evidence and output guardrails.

Candidate-screening Skills may additionally declare `strategy.json` using the versioned `aegisrun-candidate-strategy/v1` schema. The parser rejects unknown or executable fields, bounds every numeric value, canonicalizes symbols, and limits output to 50 rows. The platform safety filter always runs before the custom filter, so an extension can narrow or rank eligible local candidates but cannot reintroduce exit or sell-side actions. The parsed strategy name, Skill provider, package hashes, and resulting score form an auditable execution boundary.
