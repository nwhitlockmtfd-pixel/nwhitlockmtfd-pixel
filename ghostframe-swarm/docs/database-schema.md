# Database Schema Recommendations

PostgreSQL 16+ with `pgvector`. Naming: snake_case, plural tables, `id` ULIDs
(time-ordered UUIDs). Every table carries `project_id` from day one
(future multi-tenancy; see api.md §5). Alembic owns migrations.

## Core tables

```sql
-- The source of truth. Append-only; no UPDATE/DELETE grants for app role.
CREATE TABLE events (
    id              UUID PRIMARY KEY,               -- ULID
    kind            TEXT NOT NULL,                  -- 'task.created', 'model.call', ...
    schema_version  SMALLINT NOT NULL DEFAULT 1,
    occurred_at     TIMESTAMPTZ NOT NULL,
    project_id      UUID NOT NULL,
    workflow_run_id UUID,
    task_id         UUID,
    worker_id       TEXT,
    causation_id    UUID,                           -- parent event
    correlation_id  UUID NOT NULL,                  -- root request thread
    payload         JSONB NOT NULL,
    cost_tokens_in  INTEGER,
    cost_tokens_out INTEGER,
    cost_usd        NUMERIC(12,6),
    batch_hash      BYTEA                           -- tamper-evidence chain
) PARTITION BY RANGE (occurred_at);                 -- monthly partitions

CREATE INDEX ON events (correlation_id, id);
CREATE INDEX ON events (task_id, id) WHERE task_id IS NOT NULL;
CREATE INDEX ON events (kind, occurred_at);

-- Large payloads (full prompts/responses) stored by reference to keep the
-- hot table lean; digest in events.payload, body here (or object store).
CREATE TABLE event_blobs (
    event_id    UUID PRIMARY KEY REFERENCES events(id),
    body        BYTEA NOT NULL,                     -- zstd-compressed JSON
    body_sha256 BYTEA NOT NULL
);
```

```sql
-- PROJECTIONS: rebuildable from events; UPDATEs allowed here.

CREATE TABLE tasks (
    id              UUID PRIMARY KEY,
    project_id      UUID NOT NULL,
    workflow_run_id UUID,
    parent_task_id  UUID REFERENCES tasks(id),      -- delegation tree
    goal            TEXT NOT NULL,
    status          TEXT NOT NULL,   -- queued|leased|working|parked|review|done|failed|cancelled
    priority        SMALLINT NOT NULL DEFAULT 100,
    assigned_worker TEXT,
    lease_expires   TIMESTAMPTZ,
    attempt         SMALLINT NOT NULL DEFAULT 0,
    budget_tokens   INTEGER,
    budget_usd      NUMERIC(12,6),
    spent_tokens    INTEGER NOT NULL DEFAULT 0,
    spent_usd       NUMERIC(12,6) NOT NULL DEFAULT 0,
    acceptance      JSONB,                          -- criteria from planner/PM
    result          JSONB,
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL
);
CREATE INDEX ON tasks (project_id, status, priority);
CREATE INDEX ON tasks (lease_expires) WHERE status = 'leased';

CREATE TABLE workflow_runs (
    id               UUID PRIMARY KEY,
    project_id       UUID NOT NULL,
    workflow_id      TEXT NOT NULL,
    workflow_version INTEGER NOT NULL,              -- pinned at start
    definition       JSONB NOT NULL,                -- frozen copy
    status           TEXT NOT NULL,
    step_states      JSONB NOT NULL,                -- {step_id: {status, task_ids, loops}}
    created_at       TIMESTAMPTZ NOT NULL,
    finished_at      TIMESTAMPTZ
);

CREATE TABLE messages (
    id          UUID PRIMARY KEY,
    project_id  UUID NOT NULL,
    thread_id   UUID NOT NULL,
    from_actor  TEXT NOT NULL,                      -- worker id or 'human:<user>'
    to_actor    TEXT NOT NULL,
    verb        TEXT NOT NULL,                      -- delegate|submit|review|ask|... (workers.md §3)
    body        TEXT,
    structured  JSONB,                              -- verdicts, decision packs
    event_id    UUID NOT NULL REFERENCES events(id),
    created_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX ON messages (thread_id, id);

CREATE TABLE approvals (
    id            UUID PRIMARY KEY,
    project_id    UUID NOT NULL,
    task_id       UUID NOT NULL REFERENCES tasks(id),
    policy        TEXT NOT NULL,
    decision_pack JSONB NOT NULL,
    state         TEXT NOT NULL,                    -- pending|approved|denied|expired
    decided_by    TEXT,
    decided_note  TEXT,
    requested_at  TIMESTAMPTZ NOT NULL,
    expires_at    TIMESTAMPTZ,
    decided_at    TIMESTAMPTZ
);
CREATE INDEX ON approvals (project_id, state, requested_at);
```

```sql
-- MEMORY (see memory.md)

CREATE TABLE memory_entries (
    id              UUID PRIMARY KEY,
    project_id      UUID NOT NULL,
    layer           TEXT NOT NULL,      -- task|project|team|tool  (short-term lives in Redis)
    scope_id        TEXT NOT NULL,
    kind            TEXT NOT NULL,
    content         TEXT NOT NULL,
    structured      JSONB,
    confidence      REAL NOT NULL DEFAULT 0.5,
    created_by      TEXT NOT NULL,
    source_event_id UUID NOT NULL REFERENCES events(id),   -- provenance
    supersedes      UUID REFERENCES memory_entries(id),
    superseded_by   UUID REFERENCES memory_entries(id),    -- head = NULL
    embedding       vector(1024),
    accessed_at     TIMESTAMPTZ,
    archived        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL
);
CREATE INDEX ON memory_entries (project_id, layer, scope_id)
    WHERE superseded_by IS NULL AND NOT archived;
CREATE INDEX ON memory_entries
    USING hnsw (embedding vector_cosine_ops);
```

```sql
-- REGISTRY, COSTS, ARTIFACTS

CREATE TABLE worker_definitions (
    id          TEXT NOT NULL,
    project_id  UUID NOT NULL,
    version     INTEGER NOT NULL,
    definition  JSONB NOT NULL,                     -- validated WorkerDefinition
    source      TEXT NOT NULL,                      -- 'config'|'plugin:<name>'
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (project_id, id, version)
);

CREATE TABLE cost_rollups (                          -- projector-maintained
    project_id  UUID NOT NULL,
    day         DATE NOT NULL,
    dimension   TEXT NOT NULL,                       -- 'task'|'worker'|'model'|'provider'
    dim_key     TEXT NOT NULL,
    tokens_in   BIGINT NOT NULL DEFAULT 0,
    tokens_out  BIGINT NOT NULL DEFAULT 0,
    usd         NUMERIC(14,6) NOT NULL DEFAULT 0,
    PRIMARY KEY (project_id, day, dimension, dim_key)
);

CREATE TABLE artifacts (
    id           UUID PRIMARY KEY,
    project_id   UUID NOT NULL,
    task_id      UUID NOT NULL REFERENCES tasks(id),
    kind         TEXT NOT NULL,                      -- 'diff'|'file'|'report'|'plan'
    path         TEXT,
    content_ref  TEXT NOT NULL,                      -- object store / fs key
    sha256       BYTEA NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL
);
```

## Redis layout

| Key/stream | Purpose |
|---|---|
| `gf:{proj}:bus:{category}` | Event streams (tasks/messages/models/tools), consumer groups per subscriber |
| `gf:{proj}:inbox:{worker}` | Durable worker inboxes |
| `gf:{proj}:queue:ready` | Scheduler priority queue (ZSET) |
| `gf:{proj}:lease:{task}` | Task leases w/ TTL heartbeats |
| `gf:{proj}:wm:{task}` | Short-term working memory (hash, TTL) |
| `gf:{proj}:budget:{task}` | Budget reservations (atomic pre-spend check) |
| `gf:{proj}:dlq` | Dead-letter stream |

Rules: Redis contents are always reconstructible or disposable — a Redis
flush loses in-flight speed, never truth. Any datum that must survive is in
Postgres before it is acted on.

## Retention

- `events`: monthly partitions; detach + archive to object storage after a
  configurable window (default: keep hot 6 months). Replay of archived runs
  loads partitions back.
- `event_blobs`: same policy; blobs may age out earlier (digest remains).
- `memory_entries`: archived flag, never hard-deleted by the system;
  `ghost memory purge` exists for humans and is itself an audited event.
