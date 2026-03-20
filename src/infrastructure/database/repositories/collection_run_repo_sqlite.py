"""CollectionRunRepository — SQLite 구현."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from src.domain.entities import CollectionRun
from src.infrastructure.database.repositories.post_repo_sqlite import _get_db, DB_PATH


def _init_table() -> None:
    conn = _get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS collection_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            started_at TIMESTAMP NOT NULL,
            completed_at TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'running',
            posts_collected INTEGER DEFAULT 0,
            error_message TEXT
        )
    """)
    conn.commit()


def _run_from_row(row: sqlite3.Row) -> CollectionRun:
    return CollectionRun(
        id=row["id"],
        source=row["source"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        status=row["status"],
        posts_collected=row["posts_collected"],
        error_message=row["error_message"],
    )


class SQLiteCollectionRunRepository:
    def __init__(self):
        _init_table()

    async def save(self, run: CollectionRun) -> CollectionRun:
        conn = _get_db()
        cur = conn.execute(
            """INSERT INTO collection_runs (source, started_at, status, posts_collected, error_message)
               VALUES (?, ?, ?, ?, ?)""",
            (run.source, run.started_at, run.status, run.posts_collected, run.error_message),
        )
        conn.commit()
        run.id = cur.lastrowid
        return run

    async def update(self, run: CollectionRun) -> CollectionRun:
        conn = _get_db()
        conn.execute(
            """UPDATE collection_runs
               SET completed_at=?, status=?, posts_collected=?, error_message=?
               WHERE id=?""",
            (run.completed_at, run.status, run.posts_collected, run.error_message, run.id),
        )
        conn.commit()
        return run

    async def get_last_successful(self, source: str) -> CollectionRun | None:
        conn = _get_db()
        row = conn.execute(
            """SELECT * FROM collection_runs
               WHERE source=? AND status='success'
               ORDER BY completed_at DESC LIMIT 1""",
            (source,),
        ).fetchone()
        return _run_from_row(row) if row else None

    async def count_consecutive_failures(self, source: str) -> int:
        conn = _get_db()
        rows = conn.execute(
            """SELECT status FROM collection_runs
               WHERE source=? ORDER BY started_at DESC LIMIT 10""",
            (source,),
        ).fetchall()
        count = 0
        for row in rows:
            if row["status"] == "failed":
                count += 1
            else:
                break
        return count

    async def get_recent(self, limit: int = 20) -> list[CollectionRun]:
        conn = _get_db()
        rows = conn.execute(
            "SELECT * FROM collection_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_run_from_row(r) for r in rows]
