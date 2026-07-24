# Memory Engine

LLMs are stateless and context windows are expensive. The Memory Engine gives
workers durable, layered, *queryable* state — and makes retrieval an explicit,
inspectable decision instead of "stuff the prompt."

## 1. The layers and why each exists

```
┌────────────────────────────────────────────────────────────────────┐
│ SHORT-TERM (working) — per task attempt, in Redis, TTL = task life │
│ why: the scratchpad; cheap, disposable, never pollutes durable     │
│ memory with half-finished reasoning                                │
├────────────────────────────────────────────────────────────────────┤
│ TASK — per task, Postgres; attempts, decisions, review feedback    │
│ why: attempt 3 must know what attempts 1–2 tried and why they      │
│ failed; dies with the task's relevance, archived not deleted       │
├────────────────────────────────────────────────────────────────────┤
│ PROJECT — per project; architecture decisions, conventions,        │
│ glossary, "we use uv not poetry", key file map                     │
│ why: the difference between a contractor and a teammate; this is   │
│ what makes month-2 output better than day-1 output                 │
├────────────────────────────────────────────────────────────────────┤
│ TEAM — cross-project; review standards, calibration curves,        │
│ "security reviewer always rejects raw SQL", worker performance     │
│ why: how the swarm learns its own norms; feeds routing and         │
│ confidence calibration                                             │
├────────────────────────────────────────────────────────────────────┤
│ TOOL — per tool; failure patterns, flaky endpoints, cost/latency   │
│ stats, "this API paginates at 100"                                 │
│ why: stops every worker re-learning the same tool quirks           │
├────────────────────────────────────────────────────────────────────┤
│ LONG-TERM (semantic store) — pgvector; embedded summaries of all   │
│ of the above plus artifacts and docs                               │
│ why: the recall index across everything; layers above are the      │
│ organized truth, this is the search accelerator over it            │
└────────────────────────────────────────────────────────────────────┘
```

Writes are permissioned per worker (workers.md) and **memory writes are
events** — you can see exactly when and why a "fact" entered project memory,
and revert it.

## 2. Storage model

One `MemoryProvider` protocol; default implementation is Postgres + pgvector
(no extra infrastructure). Entries are typed:

```python
class MemoryEntry(BaseModel):
    id: UUID
    layer: Layer                  # short_term | task | project | team | tool
    scope_id: str                 # task_id / project_id / team / tool name
    kind: str                     # "decision", "convention", "failure", "fact", ...
    content: str                  # markdown
    structured: dict | None       # machine-usable form when applicable
    source_event_id: UUID         # provenance — every memory traces to an event
    confidence: float
    created_by: str               # worker id or human
    embedding: vector | None
    supersedes: UUID | None       # corrections chain instead of edits
```

`supersedes` matters: memories are corrected by superseding, not mutating —
provenance survives, and retrieval takes the head of the chain.

## 3. Retrieval strategies

Retrieval is a per-role, per-step **strategy**, declared in config and
recorded in the deliberation event (so you can see *why* the model saw what
it saw):

| Strategy | What it does | Used by |
|---|---|---|
| `frame` | Always-include set: project conventions, task acceptance criteria, active decisions | everyone |
| `semantic` | pgvector top-k against the current subgoal, filtered by layer + recency decay | research, planning |
| `structural` | Follow links: task → its reviews → superseding decisions | reviewers |
| `episodic` | "Have we done something like this before?" — prior similar tasks with outcomes | planner, retry engine |
| `tool_priors` | Inject known quirks for the tools this step permits | any tool-using step |

Strategies compose with a token budget (context-manager section budget). Each
retrieved item carries its provenance ID into the context pack.

## 4. Pruning and compression

- **Pruning (read-time)**: budget enforcement with priority ordering +
  recency/relevance decay. Items pruned from a pack are listed (by id) in the
  deliberation event, so "the model didn't know X" is diagnosable.
- **Compression (write-time)**: when a task completes, a summarizer pass
  (cheap model tier) distills task memory → durable entries: decisions,
  outcomes, failure lessons. Raw working memory expires; the distillate is
  what enters project/team layers. Compression outputs are reviewable — a
  workflow can gate "what the swarm remembers" behind human approval.
- **Hygiene**: contradiction detection at write time (new entry vs. head
  entries of same kind/scope → flagged for supersede-or-reject); staleness
  decay on unaccessed entries; hard caps per layer with LRU archival to cold
  storage (never silent deletion — archival is an event too).

## 5. Failure modes we design against

- **Memory poisoning**: a bad "fact" written once and retrieved forever.
  Mitigations: provenance on every entry, supersede chains, hygiene pass,
  human-gateable compression.
- **Context stuffing**: retrieval that grabs 30 marginal items. Mitigations:
  hard section budgets, strategy-scoped retrieval, pruned-item visibility.
- **Cross-project leakage**: scope_id filtering is enforced in the provider
  query layer, not by prompt discipline.
