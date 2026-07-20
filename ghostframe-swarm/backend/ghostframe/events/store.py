"""Append-only event persistence.

v0.1 dev mode: SQLite. The store is a bus subscriber like everything else —
persistence is not special-cased inside publishers.

SQLite calls are synchronous but each write is tiny; for the single-process
dev kernel this is acceptable (documented v0.1 debt: the Postgres store is
async and partitioned).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .envelope import Event

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id             TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    occurred_at    TEXT NOT NULL,
    project_id     TEXT NOT NULL,
    workflow_run_id TEXT,
    task_id        TEXT,
    worker_id      TEXT,
    causation_id   TEXT,
    correlation_id TEXT NOT NULL,
    payload        TEXT NOT NULL,
    cost           TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_correlation ON events (correlation_id, id);
CREATE INDEX IF NOT EXISTS ix_events_task ON events (task_id, id);
CREATE INDEX IF NOT EXISTS ix_events_kind ON events (kind, id);
"""


class SQLiteEventStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()

    async def handle(self, event: Event) -> None:
        """Bus-subscriber entrypoint. Idempotent on event id."""
        self.append(event)

    def append(self, event: Event) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event.id,
                    event.kind,
                    event.schema_version,
                    event.occurred_at.isoformat(),
                    event.project_id,
                    event.workflow_run_id,
                    event.task_id,
                    event.worker_id,
                    event.causation_id,
                    event.correlation_id,
                    json.dumps(event.payload, default=str),
                    event.cost.model_dump_json() if event.cost else None,
                ),
            )
            self._conn.commit()

    def query(
        self,
        *,
        kind: str | None = None,
        task_id: str | None = None,
        correlation_id: str | None = None,
        limit: int = 1000,
    ) -> list[Event]:
        clauses, params = [], []
        if kind:
            clauses.append("kind GLOB ?")
            params.append(kind.replace(".*", ".*"))
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if correlation_id:
            clauses.append("correlation_id = ?")
            params.append(correlation_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM events {where} ORDER BY id LIMIT ?", (*params, limit)
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

    @staticmethod
    def _row_to_event(r: tuple) -> Event:
        return Event(
            id=r[0], kind=r[1], schema_version=r[2], occurred_at=r[3],
            project_id=r[4], workflow_run_id=r[5], task_id=r[6], worker_id=r[7],
            causation_id=r[8], correlation_id=r[9],
            payload=json.loads(r[10]),
            cost=json.loads(r[11]) if r[11] else None,
        )

    def close(self) -> None:
        self._conn.close()
