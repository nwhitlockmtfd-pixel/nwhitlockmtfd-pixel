# GhostFrame Swarm — System Architecture

This document is the authoritative technical architecture. Every other doc
elaborates one box in the diagrams below.

## 1. Architectural stance

GhostFrame Swarm is an **event-sourced orchestration kernel** with pluggable
everything. Three decisions define the whole system:

1. **Event sourcing at the core.** Every state change — task created, worker
   assigned, message sent, tool invoked, model called, review rejected,
   human approved — is an immutable event in an append-only log (Postgres).
   Current state is a projection; the dashboard, audit trail, cost reports,
   and the replay system are all read models over the same log. This is the
   single decision that makes "no black boxes" true rather than aspirational.

2. **Workers are processes, not function calls.** A worker is a long-lived
   asyncio actor with an inbox, not a function invoked by a chain. Workers
   communicate only via messages; the workflow engine communicates with
   workers only via messages. This buys us: independent failure, backpressure,
   horizontal scaling later, and a communication record for free.

3. **The kernel is small.** The engine knows about tasks, events, messages,
   budgets, and approvals. It does *not* know about "Backend Engineer" or
   "code review" — those are worker definitions and workflow templates loaded
   as data/plugins. Roles evolve without engine releases.

## 2. Component architecture

```
┌───────────────────────────────────────────────────────────────────────────┐
│                              CLIENTS                                      │
│   CLI (ghost)      Python SDK       Dashboard (React)     3rd-party apps  │
└───────┬──────────────────┬───────────────┬────────────────────┬──────────┘
        │                  │               │                    │
        ▼                  ▼               ▼                    ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                        API GATEWAY  (FastAPI)                             │
│   REST /api/v1/*          WebSocket /ws/*          AuthN/AuthZ  RateLimit │
└──────────────────────────────────┬────────────────────────────────────────┘
                                   │  (in-process calls in single-node mode)
┌──────────────────────────────────▼────────────────────────────────────────┐
│                        ORCHESTRATION KERNEL                               │
│                                                                           │
│  ┌────────────────┐  ┌────────────────┐  ┌─────────────────────────────┐  │
│  │ Workflow Engine│  │ Task Scheduler │  │ Approval Engine             │  │
│  │ state machines │  │ priorities,    │  │ gates, policies, timeouts,  │  │
│  │ per task/flow  │  │ deps, leases   │  │ human queue                 │  │
│  └───────┬────────┘  └───────┬────────┘  └──────────────┬──────────────┘  │
│          │                   │                          │                 │
│  ┌───────▼───────────────────▼──────────────────────────▼──────────────┐  │
│  │                    EVENT BUS  (Redis Streams)                       │  │
│  │        all components publish/subscribe; persisted to Postgres      │  │
│  └───────┬───────────────────┬──────────────────────────┬──────────────┘  │
│          │                   │                          │                 │
│  ┌───────▼────────┐  ┌───────▼────────┐  ┌──────────────▼──────────────┐  │
│  │ Agent Registry │  │ Messaging Layer│  │ Retry Engine                │  │
│  │ worker defs,   │  │ worker inboxes,│  │ policies: backoff, reroute, │  │
│  │ capabilities,  │  │ threads, DLQ   │  │ re-plan, escalate           │  │
│  │ health         │  └────────────────┘  └─────────────────────────────┘  │
│  └────────────────┘                                                       │
└───────────────┬───────────────────────────────────────┬───────────────────┘
                │                                       │
┌───────────────▼───────────────┐       ┌───────────────▼───────────────────┐
│         WORKER RUNTIME        │       │          PLATFORM SERVICES        │
│  ┌─────────┐ ┌─────────┐      │       │  ┌──────────────┐ ┌────────────┐  │
│  │ Planner │ │Architect│ ...  │       │  │ Memory Engine│ │ Model      │  │
│  └────┬────┘ └────┬────┘      │       │  │ (layered +   │ │ Router     │  │
│       │agent loop │           │       │  │  retrieval)  │ │ (providers)│  │
│  ┌────▼───────────▼────────┐  │       │  └──────────────┘ └────────────┘  │
│  │      Tool Runner        │  │       │  ┌──────────────┐ ┌────────────┐  │
│  │ sandboxed, permissioned │  │       │  │ Cost & Token │ │ Secrets    │  │
│  └─────────────────────────┘  │       │  │ Tracker      │ │ Manager    │  │
└───────────────────────────────┘       │  └──────────────┘ └────────────┘  │
                                        │  ┌──────────────┐ ┌────────────┐  │
                                        │  │ Config Loader│ │ Plugin Host│  │
                                        │  └──────────────┘ └────────────┘  │
                                        └───────────────────────────────────┘
                ┌───────────────────────────────────────┐
                │              STORAGE                  │
                │  PostgreSQL: events, tasks, memory,   │
                │    approvals, costs, audit (pgvector) │
                │  Redis: streams, queues, locks, cache │
                │  Object store/FS: artifacts, blobs    │
                └───────────────────────────────────────┘
```

**Deployment shapes.** The same codebase runs in three modes:

- **`ghost dev`** — single process, SQLite + in-memory bus, zero dependencies.
  Exists so `pip install ghostframe-swarm && ghost dev` works in 60 seconds.
- **Single-node** — docker-compose: API, kernel, worker runtime, Postgres,
  Redis, dashboard. The recommended production shape for v1.
- **Distributed** — multiple worker-runtime containers subscribing to the same
  Redis streams; kernel remains a single logical leader (see design review
  for why we deliberately postpone kernel HA).

Loose coupling rule: components interact **only** through (a) the event bus,
(b) the messaging layer, or (c) explicit service interfaces registered in the
dependency-injection container. No component imports another's internals.

## 3. Data flow — one task, end to end

Scenario: user runs `ghost run "add rate limiting to the API" --workflow feature-dev`.

```
 user            api        workflow      scheduler    planner     backend_eng   reviewer     human
  │  POST /tasks  │  engine     │            │            │            │            │
  ├──────────────►│             │            │            │            │            │
  │               ├─ TaskCreated (event) ────┼────────────┼────────────┼────────────┤
  │               │  ┌──────────►            │            │            │            │
  │               │  │ instantiate workflow  │            │            │            │
  │               │  │ "feature-dev" graph   │            │            │            │
  │               │  └──► step: plan ───────►│ assign     │            │            │
  │               │             │            ├───────────►│            │            │
  │               │             │            │  msg: TaskAssignment    │            │
  │               │             │            │            ├─ reads project memory   │
  │               │             │            │            ├─ model call (router)    │
  │               │             │            │            ├─ PlanProposed (event)   │
  │               │             │◄───────────┴────────────┤            │            │
  │               │  step: implement (fan-out per plan item)           │            │
  │               │             ├────────────► assign ────┼───────────►│            │
  │               │             │            │            │            ├─ tool: repo.read
  │               │             │            │            │            ├─ tool: code.edit
  │               │             │            │            │            ├─ WorkSubmitted
  │               │  step: review           │            │            │            │
  │               │             ├────────────► assign ────┼────────────┼──────────► │
  │               │             │            │            │            │  reviewer runs
  │               │             │            │            │            │  tests via ToolRunner
  │               │             │            │   ReviewRejected(reasons) ◄──────────┤
  │               │  retry policy: send back with review feedback      │            │
  │               │             ├────────────► reassign ──┼───────────►│ (attempt 2)│
  │               │             │            │            │            ├─ WorkSubmitted
  │               │             │            │            │  ReviewApproved ◄───────┤
  │               │  step: approval gate (policy: code changes need human OK)       │
  │               │             ├─ ApprovalRequested ─────┼────────────┼────────────┼──────►
  │               │             │            │            │            │            │ (queue)
  │               │             │  ApprovalGranted ◄──────┼────────────┼────────────┼───────┤
  │               │  step: finalize → TaskCompleted (event, with artifacts)         │
  │◄──────────────┤  WS pushes every event above to dashboard in real time          │
```

Key properties visible in this flow:

- The **workflow engine** owns control flow; workers own judgment. A worker
  can *propose* ("reject this work"), but the transition happens in the engine
  where it is recorded, budgeted, and retry-policy-checked.
- **Review rejection is a normal edge**, not an error. The retry engine
  decides: retry same worker with feedback, reroute to a different worker,
  re-plan, or escalate to a human.
- **The approval gate is a scheduling primitive.** The task parks; no tokens
  burn while waiting for a human.

## 4. Event flow

Every event has one canonical envelope (Pydantic model, versioned schema):

```python
class Event(BaseModel):
    id: UUID                    # ULID, time-ordered
    kind: str                   # "task.created", "worker.message", "model.call", ...
    schema_version: int
    occurred_at: datetime
    workflow_id: UUID | None
    task_id: UUID | None
    worker_id: str | None
    causation_id: UUID | None   # event that directly caused this one
    correlation_id: UUID        # the root request; threads the whole story
    payload: dict               # kind-specific, schema-validated
    cost: CostDelta | None      # tokens/dollars attributable to this event
```

`causation_id` + `correlation_id` are what make replay and the dashboard's
timeline possible: the full causal tree of any outcome is a single indexed
query.

Event lifecycle:

```
 producer ──publish──► Redis Stream (per-category: tasks, messages, models, tools)
                          │
          ┌───────────────┼──────────────────┬───────────────────┐
          ▼               ▼                  ▼                   ▼
    persister        projectors         ws-broadcaster      plugin hooks
    (Postgres,       (task state,       (dashboard          (metrics,
     append-only,     cost rollups,      subscriptions)      integrations)
     the truth)       memory triggers)
```

Consumers are Redis consumer groups: at-least-once delivery, per-consumer
acks, dead-letter stream after N failed deliveries. All projectors are
idempotent (event `id` is the dedup key). Redis is transport; Postgres is
truth — on restart, projectors can rebuild from the log.

## 5. The worker agent loop

Each worker runs the same kernel-provided loop; role definitions only supply
data (prompts, tools, permissions, policies). No subclass overrides control
flow — this keeps every worker observable in the same way.

```
            ┌─────────────────────────────────────────────┐
            │                inbox message                │
            └───────────────────────┬─────────────────────┘
                                    ▼
       ┌──────────── 1. HYDRATE CONTEXT ────────────────────┐
       │ memory.retrieve(task, role, budget) → context pack │
       └───────────────────────┬────────────────────────────┘
                               ▼
       ┌──────────── 2. DELIBERATE ─────────────────────────┐
       │ model call via router (role policy picks model)    │
       │ output = structured Action (Pydantic union):       │
       │   Respond | UseTool | Delegate | AskClarification  │
       │   | SubmitWork | RejectWork | Escalate             │
       └───────────────────────┬────────────────────────────┘
                               ▼
       ┌──────────── 3. CHECK ──────────────────────────────┐
       │ permissions gate · budget gate · confidence gate   │
       │ (below threshold → auto-escalate or ask)           │
       └───────────────────────┬────────────────────────────┘
                               ▼
       ┌──────────── 4. ACT ────────────────────────────────┐
       │ tool runner / send message / submit to engine      │
       └───────────────────────┬────────────────────────────┘
                               ▼
       ┌──────────── 5. RECORD ─────────────────────────────┐
       │ emit events (always) · write working memory ·      │
       │ update confidence & progress                       │
       └────────────────────────────────────────────────────┘
```

Every iteration emits `worker.deliberation` with the **exact prompt, exact
model output, and the parsed action** — this is the "no hidden prompts"
guarantee, mechanically enforced because the loop is the only code path that
calls the model router.

## 6. Class hierarchy (suggested, kept shallow)

Composition over inheritance; interfaces are `Protocol`s so plugins never
need our base classes.

```
Protocols (sdk/interfaces.py)
├── ModelProvider        # complete(), stream(), count_tokens(), capabilities()
├── MemoryProvider       # store(), retrieve(), search(), compress()
├── Tool                 # name, schema (Pydantic), run(ctx, args), permissions
├── ApprovalPolicy       # evaluate(action, ctx) -> Allow | RequireHuman | Deny
├── RetryPolicy          # next(attempt_history) -> RetryDecision
└── EventSink            # handle(event)  (dashboards, metrics, integrations)

Core classes
├── Worker               # the agent loop; final, not subclassed
│   └── WorkerDefinition # data: role, prompt, tools, permissions, model policy,
│                        #       confidence thresholds, escalation rules
├── WorkflowEngine
│   ├── Workflow         # graph of Steps (data, loaded from YAML/plugin)
│   └── StepExecutor     # per step-type: agent_step, gate, fan_out, join, human
├── TaskScheduler
├── ModelRouter          # provider registry + selection policy + fallback
├── MemoryEngine         # layer coordinator over MemoryProviders
├── ToolRunner           # validation, sandbox, timeout, permission enforcement
├── EventBus             # publish/subscribe façade over Redis Streams
├── ApprovalEngine
├── CostTracker          # projector over model.call / tool.run events
└── PluginHost           # discovery (entry points), lifecycle, isolation
```

## 7. Technology stack — what and why

| Choice | Why | Rejected alternative |
|---|---|---|
| Python 3.12+, AsyncIO | The AI ecosystem lives here; workers are I/O-bound actors, asyncio fits | Go core + Python workers: better perf, kills contributor accessibility |
| FastAPI + Pydantic v2 | Typed schemas shared between API, events, SDK; OpenAPI for free | Django (too heavy), raw Starlette (reinvents FastAPI) |
| PostgreSQL (+ pgvector) | One database for events, state, memory, vectors; transactional projections | Dedicated vector DB at v0.x = ops burden; revisit via MemoryProvider plugin |
| Redis Streams | Bus + queues + locks in one dependency ops teams already run | Kafka/NATS: right at big scale, wrong first dependency |
| SQLite + in-memory bus (dev mode) | `pip install` → running in one minute; adoption depends on this | Requiring docker-compose for hello-world |
| React + TypeScript + Vite dashboard | Table-stakes for mission-control UI | Server-rendered HTMX: simpler, but the graph/replay UI needs a real client |
| Docker / docker-compose | Boring, universal | k8s manifests ship as *examples*, not requirements |
| MCP (Model Context Protocol) | Tool ecosystem interop: any MCP server becomes a GhostFrame tool via adapter | Proprietary tool format only — wastes the existing ecosystem |
| `uv` + `ruff` + `mypy --strict` + `pytest` | Fast, standard, contributor-friendly | Poetry (slower), no type checking (unacceptable for a kernel) |

## 8. Dependency injection & configuration

- A single explicit container (hand-rolled, ~200 lines — not a framework)
  wires interfaces to implementations at startup. Registration order:
  defaults → config file → plugins → test overrides.
- Configuration is layered Pydantic Settings: `defaults < ghostframe.yaml <
  env vars < CLI flags`. Every worker definition, workflow, model policy, and
  budget lives in versioned config (YAML in the project repo), so a swarm
  setup is reviewable in a pull request — infrastructure-as-code for agent
  teams.
- Secrets are never in config values — config holds *references*
  (`anthropic_api_key: {secret: ANTHROPIC_API_KEY}`) resolved by the Secrets
  Manager (env/file/vault backends). Events and logs redact by reference,
  so a leaked log never contains a key.

## 9. Cross-cutting guarantees

| Guarantee | Mechanism |
|---|---|
| Inspectability | Agent loop is the sole model-call path; every call emitted as event with full prompt/response |
| Reproducible replay | Event log + recorded model responses; replay re-runs projections or re-executes with recorded I/O (see core-systems.md §replay) |
| Budget safety | Budgets checked *before* model calls; hard stop parks task in approval queue |
| Permission safety | Tool runner enforces per-worker allowlists + resource scopes (fs paths, network hosts, repo branches) |
| Crash safety | Tasks leased, not owned; lease expiry → scheduler reassigns; events idempotent |
| Schema evolution | Versioned event schemas with upcasters; old logs replay forever |
