# Dashboard — Mission Control

React + TypeScript + Vite, served by the API container, talking only to the
public REST/WS APIs. Design language: GitHub Actions' run view × Kubernetes
dashboard density × VS Code's panel discipline. Dark-mode-first, keyboard
palette (`⌘K`) everywhere, every entity deep-linkable
(`/runs/<id>?event=<id>`).

## Layout

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ⬢ GhostFrame   ▸ project: api-server     🔍 ⌘K     🔔 3 approvals   ⚙   │
├───────────────┬──────────────────────────────────────────────────────────┤
│ NAV           │  RUN #142 · feature-dev · "add rate limiting"   ⏸ ✖     │
│  ▸ Overview   │  ┌────────────────────────────────────────────────────┐  │
│  ▸ Runs       │  │            WORKFLOW GRAPH (live)                   │  │
│  ▸ Workers    │  │   [plan ✓]──[gate ✓]──[implement ⟳]──[review ]     │  │
│  ▸ Approvals● │  │                 └──────[implement ✓]──[qa ]        │  │
│  ▸ Memory     │  │   nodes: status, worker avatar, cost, duration     │  │
│  ▸ Costs      │  │   edges: animate on message flow                   │  │
│  ▸ Events     │  └────────────────────────────────────────────────────┘  │
│  ▸ Plugins    │  ┌──────────────────────────┬─────────────────────────┐  │
│               │  │ COMMUNICATION            │ INSPECTOR               │  │
│ WORKERS (live)│  │ threaded worker messages │ selected node/event:    │  │
│  ● planner    │  │ w/ verdicts, ASKs, and   │ exact prompt, exact     │  │
│  ● backend ⟳  │  │ human replies inline     │ model output, parsed    │  │
│  ● security   │  │                          │ action, memory used &   │  │
│  ○ qa (idle)  │  │                          │ pruned, cost delta      │  │
│               │  └──────────────────────────┴─────────────────────────┘  │
│ BURN: $3.12   │  ├─ TIMELINE ──────────────────────●────────────────┤    │
│ of $10.00     │  │  drag to any moment = full state at that event    │    │
└───────────────┴──────────────────────────────────────────────────────────┘
```

## Views

| View | Contents |
|---|---|
| **Overview** | Active runs, live worker roster with health/current action, queue depth, burn rate vs. budgets, approvals badge, recent failures |
| **Runs** | List → run detail (the screen above): live workflow graph, communication pane, inspector, timeline scrubber |
| **Workers** | Per worker: definition (role file, permissions, budgets — rendered from the YAML), calibration curve, throughput/success stats, current deliberation |
| **Task Queue** | Scheduler state: ready/leased/parked/dead-letter, priorities, lease holders, stall warnings |
| **Approvals** | The human queue. Each card = decision pack: question, options w/ tradeoffs, cost so far, diff/artifact preview. One-click approve/deny/edit; keyboard-driven triage |
| **Memory** | Browse/search by layer & scope; provenance chain per entry (which event created it, what superseded it); manual add/supersede |
| **Costs** | Rollups by task/worker/model/provider/day; burn-down against budgets; anomaly flags ("this step cost 8× median") |
| **Events** | Raw firehose with kind/correlation filters — the escape hatch when a projection confuses; export selection |
| **Diagnostics** | Failure center: dead letters, exhausted retries, stalled steps, provider circuit-breaker states — each linking to its causal timeline |

## Replay (the flagship)

The timeline scrubber turns the event log into time travel: drag to any
moment and every pane — graph, messages, memory, costs — re-projects to that
instant. Step forward event-by-event through a failure; diff two attempts of
the same step side-by-side (prompt diff, output diff, cost diff). This is a
pure client-side projection over `GET /replay/{run_id}` — no server state.

## Principles

- **Read fast, act deliberately.** Everything is visible in ≤2 clicks;
  mutations (approve, cancel, pause) get confirmation with consequence
  summary ("cancels 2 in-flight substeps, ~$0.40 spent will be kept").
- **No dashboard-only APIs.** If the UI needs it, the public API gets it.
- **Live-first, poll-never.** WS subscriptions with `Last-Event-ID` resume;
  the UI shows a stale-badge if the socket drops rather than silently lying.
- **Latency budget**: graph updates < 250 ms behind the bus on a laptop run.
