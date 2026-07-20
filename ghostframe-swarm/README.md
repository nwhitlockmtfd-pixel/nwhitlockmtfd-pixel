# GhostFrame Swarm

**An operating system for AI workers.**

GhostFrame Swarm is an open-source orchestration platform where specialized AI
workers — planners, engineers, reviewers, QA, docs — collaborate like a real
software company: they delegate, review each other's work, reject bad output,
maintain layered memory, escalate to humans, and leave a complete, replayable
audit trail of every decision.

Most frameworks are `prompt → LLM → output` with a loop bolted on.
GhostFrame Swarm is the layer underneath: scheduling, messaging, memory,
permissions, cost control, observability, and replay — the boring
infrastructure that makes multi-agent work trustworthy.

> **Status: design phase.** This directory is the complete architecture and
> engineering plan. Nothing here is vaporware marketing — it is the blueprint
> we build against, including a [brutally honest design review](docs/design-review.md)
> of our own weaknesses.

---

## Why this exists

Multi-agent demos are easy. Multi-agent *systems* are hard, because the hard
parts aren't prompting — they're:

- **Trust** — you cannot ship work you cannot inspect. Every worker decision
  in GhostFrame is an event in an append-only log you can replay.
- **Failure** — agents fail constantly. The platform treats retry, review,
  rejection, and human escalation as first-class control flow, not exception
  handling.
- **Memory** — context windows are small and expensive. A layered memory
  engine (task / project / team / tool) with explicit retrieval strategies
  replaces "stuff everything in the prompt."
- **Cost** — token spend is a budget, tracked per task, per worker, per model,
  enforced by policy before the call is made, not discovered on the invoice.
- **Lock-in** — the model router treats Anthropic, OpenAI, Ollama, and local
  models as interchangeable capability providers behind one interface.

## Design principles

1. **No magic.** Every prompt a worker sends is inspectable. Every decision is
   an event. There are no hidden system prompts and no black boxes.
2. **Everything is replaceable.** Workers, tools, memory providers, model
   providers, and approval flows are plugins behind stable interfaces.
3. **The event log is the source of truth.** The dashboard, replay system,
   audit trail, and cost tracking are all *views* over one append-only stream.
4. **Humans are in the loop by design, not by accident.** Approval gates are a
   scheduling primitive, not an afterthought.
5. **Boring technology.** Python, Postgres, Redis, FastAPI, Docker. The novelty
   budget is spent on the orchestration model, not the stack.

## The 30-second mental model

```
                        ┌────────────────────────────┐
                        │         Dashboard          │
                        │  (mission control / replay)│
                        └─────────────┬──────────────┘
                                      │ REST + WebSocket
┌──────────┐   task    ┌──────────────▼──────────────┐
│  Human   ├──────────►│       Workflow Engine       │
│ (or CLI/ │◄──────────┤  scheduler · approvals ·    │
│   SDK)   │ approvals │  retries · budgets          │
└──────────┘           └──────┬───────────────┬──────┘
                              │               │
                     ┌────────▼───┐   ┌───────▼────────┐
                     │  Workers   │◄─►│  Event Bus +   │
                     │ (Planner,  │   │  Messaging     │
                     │  Engineer, │   └───────┬────────┘
                     │  QA, ...)  │           │ every decision = event
                     └─┬────┬───┬─┘   ┌───────▼────────┐
                       │    │   │     │  Audit Log +   │
              ┌────────▼┐ ┌─▼───▼──┐  │  Replay Store  │
              │ Memory  │ │ Tools  │  └────────────────┘
              │ Engine  │ │ Runner │
              └─────────┘ └────────┘
                       │
              ┌────────▼────────┐
              │  Model Router   │──► Anthropic / OpenAI / Ollama / local
              └─────────────────┘
```

A **task** enters the workflow engine. The engine assigns it to a **worker**
(a role + memory + permissions + tools + a model policy). Workers communicate
over the **messaging layer**, call **tools** in sandboxes, read and write
**memory**, and emit every decision to the **event bus**. Review, rejection,
retry, delegation, and human approval are workflow transitions — visible in
the dashboard, reconstructable in replay.

## Documentation map

| Document | What it covers |
|---|---|
| [docs/architecture.md](docs/architecture.md) | System architecture, component diagrams, data flow, event flow |
| [docs/workers.md](docs/workers.md) | Worker model: roles, permissions, review, delegation, escalation |
| [docs/core-systems.md](docs/core-systems.md) | Workflow engine, scheduler, event bus, model router, retry, approvals, cost/token tracking, secrets, versioning |
| [docs/memory.md](docs/memory.md) | Layered memory architecture and retrieval strategies |
| [docs/api.md](docs/api.md) | REST API, WebSocket API, Python SDK, CLI |
| [docs/plugins.md](docs/plugins.md) | Plugin SDK: workers, tools, providers, integrations |
| [docs/dashboard.md](docs/dashboard.md) | Mission-control UI: workflow graph, replay, approvals, diagnostics |
| [docs/database-schema.md](docs/database-schema.md) | PostgreSQL schema and Redis usage |
| [docs/repository-structure.md](docs/repository-structure.md) | Repo layout, coding standards, testing strategy, CI/CD, release process |
| [docs/roadmap.md](docs/roadmap.md) | v0.1 → v2.0, plus open-source / commercial / research split |
| [docs/design-review.md](docs/design-review.md) | Brutally honest review: weaknesses, risks, what maintainers will criticize |

## What GhostFrame Swarm is *not*

- Not a prompt library or a chain DSL.
- Not an autonomous "AGI employee" pitch. Workers are bounded, permissioned,
  budgeted, and supervised.
- Not tied to any single model vendor.
- Not a hosted service (the open-source core runs entirely on your machine;
  see the [roadmap](docs/roadmap.md) for the honest open-core split).

## License

Apache-2.0 (planned). The open-source core — engine, workers, memory, plugins,
SDK, dashboard — stays free forever. See [docs/roadmap.md](docs/roadmap.md)
for exactly what would and wouldn't be commercial.
