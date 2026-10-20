"""SQLite-backed store for household facts.

Stdlib only (sqlite3). Thread-safe via an internal lock because the HTTP
server handles each connection on its own thread.
"""

from __future__ import annotations

import os
import sqlite3
import threading

DEFAULT_DB = os.path.expanduser("~/.fitlog/fitlog.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    content TEXT NOT NULL,
    category TEXT,
    visibility TEXT,
    added_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class Store:
    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("FITLOG_DB", DEFAULT_DB)
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        # check_same_thread=False because the HTTP server is threaded;
        # all access is serialized with the lock.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    def save_fact(
        self,
        content: str,
        category: str | None,
        visibility: str | None,
        added_by: str | None,
    ) -> int:
        """Persist a new household fact and return its row id."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO facts (content, category, visibility, added_by)"
                " VALUES (?, ?, ?, ?)",
                (content, category, visibility, added_by),
            )
            self._conn.commit()
            return cur.lastrowid

    def recall_fact(self, query: str) -> list[dict]:
        """Return facts whose content matches ``query``.

        Since vector embeddings are not wired up yet, this is a simple
        case-insensitive keyword search using SQL ``LIKE`` against the
        ``content`` column.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, content, category, visibility, added_by, created_at"
                " FROM facts WHERE LOWER(content) LIKE LOWER(?)",
                (f"%{query}%",),
            ).fetchall()
        return [dict(r) for r in rows]

    def forget_fact(self, fact_id: int) -> int:
        """Delete a fact by id; return the number of rows deleted."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM facts WHERE id = ?",
                (fact_id,),
            )
            self._conn.commit()
            return cur.rowcount
