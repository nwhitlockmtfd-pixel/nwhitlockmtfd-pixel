# API Design

Four official surfaces — REST, WebSocket, Python SDK, CLI — all thin layers
over the same kernel services and the same Pydantic schemas. The OpenAPI spec
is generated from code and committed; SDK and CLI are built against it.

## 1. REST API (`/api/v1`)

Versioned in the path; additive changes only within a major version.

```
# Tasks & workflows
POST   /api/v1/tasks                     # submit work {goal, workflow, budget, priority}
GET    /api/v1/tasks/{id}                # state, current step, assignments, costs
GET    /api/v1/tasks/{id}/events         # paginated causal event history
POST   /api/v1/tasks/{id}/cancel
GET    /api/v1/workflows                 # registered workflow definitions
GET    /api/v1/workflows/runs/{run_id}   # live graph state (nodes, edges, statuses)

# Workers
GET    /api/v1/workers                   # registry: definitions + live instances + health
GET    /api/v1/workers/{id}/messages     # inbox/outbox threads
PATCH  /api/v1/workers/{id}              # pause / resume / drain

# Approvals
GET    /api/v1/approvals?state=pending
POST   /api/v1/approvals/{id}            # {decision: approve|deny, note, edits}

# Messages (human ↔ worker)
POST   /api/v1/messages                  # inject a message into a thread (answer an ASK)

# Memory
GET    /api/v1/memory/search?q=&layer=&scope=
POST   /api/v1/memory/entries            # human adds a fact/convention
POST   /api/v1/memory/entries/{id}/supersede

# Observability
GET    /api/v1/costs?group_by=task|worker|model|day
GET    /api/v1/events?kind=&since=&correlation_id=
GET    /api/v1/replay/{run_id}           # replay manifest (event stream + artifacts)

# Platform
GET    /api/v1/plugins                   # loaded plugins + versions + health
GET    /api/v1/healthz | /readyz | /metrics (Prometheus)
```

Example — submit and gate:

```bash
curl -X POST localhost:8700/api/v1/tasks \
  -H "Authorization: Bearer $GHOST_TOKEN" \
  -d '{
    "goal": "Add rate limiting to the public API",
    "workflow": "feature-dev",
    "budget": {"usd": 10.00},
    "approval_overrides": {"code_change_approval": "required"}
  }'
# → {"task_id": "01J...", "status": "queued", "watch": "/ws/tasks/01J..."}
```

Auth: bearer tokens (single-user token in v0.x; API keys with scopes in v1).
Errors: RFC 9457 problem+json with a machine-readable `code`.

## 2. WebSocket API (`/ws`)

Same event envelope as the bus; the dashboard is just another client.

```
/ws/firehose                    # all events (filterable by kinds=)
/ws/tasks/{id}                  # one task's causal stream, replay-from supported
/ws/workers/{id}                # a worker's deliberations and messages
/ws/approvals                   # pending-approval push
```

Protocol: JSON frames `{type: "event", event: {...}}` plus control frames
(`subscribe`, `replay_from: <event_id>`, `ping`). Reconnect with
`Last-Event-ID` semantics — no gaps for the dashboard after a blip.

## 3. Python SDK (`ghostframe`)

Async-first, typed, mirrors the REST nouns:

```python
from ghostframe import Swarm

async with Swarm.connect("http://localhost:8700", token=...) as swarm:
    task = await swarm.tasks.submit(
        goal="Add rate limiting to the public API",
        workflow="feature-dev",
        budget_usd=10.0,
    )

    async for event in task.events():          # live stream, typed events
        if event.kind == "approval.requested":
            await swarm.approvals.approve(event.payload["approval_id"],
                                          note="LGTM, ship it")

    result = await task.result()               # artifacts, costs, summary
    print(result.costs.total_usd, [a.path for a in result.artifacts])
```

Embedding mode (no server, in-process kernel — the same objects, DI'd with
SQLite/in-memory bus):

```python
from ghostframe import LocalSwarm
swarm = LocalSwarm(config="ghostframe.yaml")   # for tests, notebooks, CI
```

## 4. CLI (`ghost`)

The daily driver; every subcommand maps to API calls, so `--json` output is
scriptable.

```
ghost init                      # scaffold ghostframe.yaml + agents/ + workflows/
ghost dev                       # single-process mode: kernel + dashboard, SQLite
ghost up | down                 # docker-compose stack

ghost run "goal..." [-w feature-dev] [--budget 10] [--watch]
ghost tasks ls | show <id> | cancel <id>
ghost watch <id>                # live TUI: step graph, messages, costs
ghost approvals ls | approve <id> [-m note] | deny <id>
ghost ask <worker> "question"   # talk to a worker in-thread
ghost memory search "..." | add --layer project "we deploy on Fridays… never"
ghost replay <run_id> [--serve] # open timeline replay in browser
ghost costs [--by worker|model|day]
ghost plugins ls | add <pkg> | rm <pkg>
ghost doctor                    # env/config/provider connectivity checks
```

`ghost run --watch` streams the same WebSocket the dashboard uses, rendered
as a terminal UI — the observability story must work without a browser.

## 5. Future surfaces (design constraints only)

- **Desktop client**: wraps dashboard + local kernel; requires nothing beyond
  the public REST/WS APIs — if the dashboard needs a private API, that's a
  bug today.
- **Cloud service**: multi-tenant control plane; requires org/project scoping
  in tokens and event partitioning by tenant — which is why `project_id`
  exists on every table from v0.1 even though single-tenant.
