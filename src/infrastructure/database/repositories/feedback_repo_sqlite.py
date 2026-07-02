"""브리핑 항목 피드백 저장소 — SQLite (data/posts.db 공용).

사용자가 대시보드 브리핑 뷰에서 항목별로 매긴 '적절/과대/과소' 라벨을,
채점 근거 스냅샷(score_features)과 함께 저장한다. 이 데이터로 중요도
산정 가중치를 캘리브레이션(few-shot·가중치 조정)한다.

라벨: appropriate(적절) | over(과대) | under(과소)
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

DB_PATH = Path("data/posts.db")
VALID_LABELS = {"appropriate", "over", "under"}

_thread_local = threading.local()


def _get_db() -> sqlite3.Connection:
    if not hasattr(_thread_local, "fb_db") or _thread_local.fb_db is None:
        _thread_local.fb_db = sqlite3.connect(DB_PATH, check_same_thread=False)
        _thread_local.fb_db.row_factory = sqlite3.Row
    return _thread_local.fb_db


class FeedbackRepositorySQLite:
    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = _get_db()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS briefing_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                briefing_id TEXT NOT NULL,
                item_index INTEGER NOT NULL,
                headline TEXT,
                category TEXT,
                importance_score REAL,
                tier TEXT,
                features TEXT,
                label TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(briefing_id, item_index)
            )
            """
        )
        conn.commit()

    def upsert(
        self,
        briefing_id: str,
        item_index: int,
        headline: str,
        category: str | None,
        importance_score: float | None,
        tier: str | None,
        features: dict | None,
        label: str,
    ) -> None:
        """항목 피드백 저장(같은 항목 재클릭 시 라벨 갱신)."""
        conn = _get_db()
        conn.execute(
            """
            INSERT INTO briefing_feedback
                (briefing_id, item_index, headline, category, importance_score,
                 tier, features, label, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(briefing_id, item_index) DO UPDATE SET
                label=excluded.label,
                headline=excluded.headline,
                category=excluded.category,
                importance_score=excluded.importance_score,
                tier=excluded.tier,
                features=excluded.features,
                created_at=CURRENT_TIMESTAMP
            """,
            (
                str(briefing_id), int(item_index), headline, category,
                importance_score, tier, json.dumps(features or {}, ensure_ascii=False), label,
            ),
        )
        conn.commit()

    def get_for_briefing(self, briefing_id: str) -> dict[int, str]:
        """해당 브리핑의 {item_index: label} 조회 (버튼 상태 표시용)."""
        conn = _get_db()
        rows = conn.execute(
            "SELECT item_index, label FROM briefing_feedback WHERE briefing_id = ?",
            (str(briefing_id),),
        ).fetchall()
        return {r["item_index"]: r["label"] for r in rows}

    def get_examples(self, limit: int = 40) -> list[dict]:
        """캘리브레이션용 예시 — 과대/과소로 라벨된 최근 항목 (few-shot 재료)."""
        conn = _get_db()
        rows = conn.execute(
            """
            SELECT headline, category, label, tier FROM briefing_feedback
            WHERE label IN ('over', 'under')
            ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        conn = _get_db()
        return conn.execute("SELECT COUNT(*) FROM briefing_feedback").fetchone()[0]
