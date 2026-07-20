"""v0.1 memory: task and project layers with provenance.

No semantic search yet (v0.5, per roadmap) — retrieval is the `frame`
strategy: recent entries per layer/scope, keyword filter optional. Entries
are corrected by superseding, never mutated.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from pydantic import BaseModel

from ..events.envelope import new_id, utcnow

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_entries (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    layer           TEXT NOT NULL,          -- 'task' | 'project'
    scope_id        TEXT NOT NULL,
    kind            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    source_event_id TEXT,
    superseded_by   TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_mem_scope
    ON memory_entries (project_id, layer, scope_id, id);
"""


class MemoryEntry(BaseModel):
    id: str
    layer: str
    scope_id: str
    kind: str
    content: str
    created_by: str
    source_event_id: str | None = None


class SQLiteMemoryStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def write(
        self,
        *,
        layer: str,
        scope_id: str,
        kind: str,
        content: str,
        created_by: str,
        project_id: str = "default",
        source_event_id: str | None = None,
        supersedes: str | None = None,
    ) -> MemoryEntry:
        entry_id = new_id()
        with self._lock:
            self._conn.execute(
                "INSERT INTO memory_entries VALUES (?,?,?,?,?,?,?,?,NULL,?)",
                (entry_id, project_id, layer, scope_id, kind, content,
                 created_by, source_event_id, utcnow().isoformat()),
            )
            if supersedes:
                self._conn.execute(
                    "UPDATE memory_entries SET superseded_by = ? WHERE id = ?",
                    (entry_id, supersedes),
                )
            self._conn.commit()
        return MemoryEntry(id=entry_id, layer=layer, scope_id=scope_id, kind=kind,
                           content=content, created_by=created_by,
                           source_event_id=source_event_id)

    def retrieve(
        self,
        *,
        layer: str,
        scope_id: str,
        project_id: str = "default",
        limit: int = 20,
        contains: str | None = None,
    ) -> list[MemoryEntry]:
        """Head-of-chain entries, newest first."""
        query = (
            "SELECT id, layer, scope_id, kind, content, created_by, source_event_id "
            "FROM memory_entries WHERE project_id=? AND layer=? AND scope_id=? "
            "AND superseded_by IS NULL"
        )
        params: list = [project_id, layer, scope_id]
        if contains:
            query += " AND content LIKE ?"
            params.append(f"%{contains}%")
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [
            MemoryEntry(id=r[0], layer=r[1], scope_id=r[2], kind=r[3],
                        content=r[4], created_by=r[5], source_event_id=r[6])
            for r in rows
        ]

    def frame(self, *, task_id: str, project_id: str = "default",
              budget_chars: int = 6000) -> str:
        """The always-include context section: project conventions + this
        task's accumulated decisions/feedback, budget-bounded."""
        parts: list[str] = []
        for entry in self.retrieve(layer="project", scope_id=project_id,
                                   project_id=project_id, limit=10):
            parts.append(f"[project/{entry.kind}] {entry.content}")
        for entry in reversed(self.retrieve(layer="task", scope_id=task_id,
                                            project_id=project_id, limit=10)):
            parts.append(f"[task/{entry.kind}] {entry.content}")
        text = "\n".join(parts)
        return text[-budget_chars:] if len(text) > budget_chars else text

    def close(self) -> None:
        self._conn.close()
