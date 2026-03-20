"""PostRepository — SQLite 구현 (로컬 저장소).

로컬 노트북에서 Posts를 SQLite로 저장/관리합니다.
Firestore 대신 파일 기반 데이터베이스 사용 (비용 $0).
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.domain.entities import Post


DB_PATH = Path("data/posts.db")

# SQLite 연결 풀 (스레드 로컬 저장소)
_thread_local = threading.local()


def _get_db() -> sqlite3.Connection:
    """스레드 로컬 SQLite 연결 획득 (연결 풀)."""
    if not hasattr(_thread_local, 'db') or _thread_local.db is None:
        _thread_local.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        _thread_local.db.row_factory = sqlite3.Row
    return _thread_local.db


def init_sqlite_db() -> None:
    """SQLite 데이터베이스 초기화 및 스키마 생성."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = _get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            external_id TEXT NOT NULL,
            url TEXT,
            author TEXT,
            author_url TEXT,
            content_text TEXT NOT NULL,
            content_html TEXT,
            media_urls TEXT,

            engagement_likes INTEGER DEFAULT 0,
            engagement_reposts INTEGER DEFAULT 0,
            engagement_comments INTEGER DEFAULT 0,
            engagement_views INTEGER DEFAULT 0,

            published_at TIMESTAMP,
            collected_at TIMESTAMP NOT NULL,

            summary TEXT,
            importance_score REAL,
            language TEXT,
            is_relevant INTEGER,
            category_names TEXT,
            keywords TEXT,
            briefed_at TIMESTAMP,

            content_hash TEXT,
            dedup_cluster_id INTEGER,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 인덱스 생성 (성능 최적화)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_collected_at ON posts(collected_at);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_source ON posts(source);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_external_id ON posts(external_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_is_relevant ON posts(is_relevant);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_source_is_relevant ON posts(source, is_relevant);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_is_relevant_collected ON posts(is_relevant, collected_at DESC);")

    conn.commit()


def _post_to_dict(post: Post) -> dict[str, Any]:
    """Post 엔티티를 SQLite 저장용 딕셔너리로 변환."""
    import json

    return {
        "id": post.external_id,  # external_id를 primary key로 사용
        "source": post.source,
        "external_id": post.external_id,
        "url": post.url,
        "author": post.author,
        "author_url": post.author_url,
        "content_text": post.content_text,
        "content_html": post.content_html,
        "media_urls": json.dumps(post.media_urls),
        "engagement_likes": post.engagement_likes,
        "engagement_reposts": post.engagement_reposts,
        "engagement_comments": post.engagement_comments,
        "engagement_views": post.engagement_views,
        "published_at": post.published_at,
        "collected_at": post.collected_at,
        "summary": post.summary,
        "importance_score": post.importance_score,
        "language": post.language,
        "is_relevant": 1 if post.is_relevant else 0 if post.is_relevant is not None else None,
        "category_names": json.dumps(post.category_names),
        "keywords": json.dumps(post.keywords),
        "briefed_at": post.briefed_at,
        "content_hash": post.content_hash,
        "dedup_cluster_id": post.dedup_cluster_id,
    }


def _post_from_row(row: sqlite3.Row) -> Post:
    """SQLite 행을 Post 엔티티로 변환."""
    import json

    return Post(
        id=row["id"],
        source=row["source"],
        external_id=row["external_id"],
        url=row["url"],
        author=row["author"],
        author_url=row["author_url"],
        content_text=row["content_text"],
        content_html=row["content_html"],
        media_urls=json.loads(row["media_urls"] or "[]"),
        engagement_likes=row["engagement_likes"],
        engagement_reposts=row["engagement_reposts"],
        engagement_comments=row["engagement_comments"],
        engagement_views=row["engagement_views"],
        published_at=row["published_at"],
        collected_at=row["collected_at"],
        summary=row["summary"],
        importance_score=row["importance_score"],
        language=row["language"],
        is_relevant=bool(row["is_relevant"]) if row["is_relevant"] is not None else None,
        category_names=json.loads(row["category_names"] or "[]"),
        keywords=json.loads(row["keywords"] or "[]"),
        briefed_at=row["briefed_at"],
        content_hash=row["content_hash"],
        dedup_cluster_id=row["dedup_cluster_id"],
    )


class PostRepositorySQLite:
    """SQLite 기반 Post 저장소."""

    def __init__(self):
        init_sqlite_db()

    def save(self, post: Post) -> str:
        """Post 저장 (신규 또는 업데이트)."""
        conn = _get_db()
        cursor = conn.cursor()
        data = _post_to_dict(post)

        cursor.execute("""
            INSERT OR REPLACE INTO posts
            (id, source, external_id, url, author, author_url, content_text,
             content_html, media_urls, engagement_likes, engagement_reposts,
             engagement_comments, engagement_views, published_at, collected_at,
             summary, importance_score, language, is_relevant, category_names,
             keywords, briefed_at, content_hash, dedup_cluster_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, tuple(data.values()))
        conn.commit()
        return data["id"]

    def find_by_id(self, post_id: str) -> Post | None:
        """ID로 Post 조회."""
        conn = _get_db()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM posts WHERE id = ?", (post_id,))
        row = cursor.fetchone()
        return _post_from_row(row) if row else None

    def find_by_external_id(self, external_id: str) -> Post | None:
        """external_id로 Post 조회."""
        return self.find_by_id(external_id)

    def find_recent(self, limit: int = 100) -> list[Post]:
        """최근 Post 조회."""
        conn = _get_db()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM posts
            ORDER BY collected_at DESC
            LIMIT ?
        """, (limit,))
        return [_post_from_row(row) for row in cursor.fetchall()]

    def find_by_source(self, source: str, limit: int = 100) -> list[Post]:
        """소스별 Post 조회."""
        conn = _get_db()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM posts
            WHERE source = ?
            ORDER BY collected_at DESC
            LIMIT ?
        """, (source, limit))
        return [_post_from_row(row) for row in cursor.fetchall()]

    def delete(self, post_id: str) -> None:
        """Post 삭제."""
        conn = _get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM posts WHERE id = ?", (post_id,))
        conn.commit()

    def update_many(self, posts: list[Post]) -> int:
        """여러 Post를 한 번에 업데이트 (배치 처리, 성능 최적화)."""
        conn = _get_db()
        cursor = conn.cursor()
        updated = 0

        for post in posts:
            if post.id is None:
                continue
            data = _post_to_dict(post)
            cursor.execute("""
                UPDATE posts SET
                    summary = ?, importance_score = ?, language = ?,
                    is_relevant = ?, category_names = ?, keywords = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                post.summary, post.importance_score, post.language,
                1 if post.is_relevant else 0,
                data["category_names"], data["keywords"],
                post.id
            ))
            updated += cursor.rowcount

        conn.commit()
        return updated

    def delete_older_than(self, days: int) -> int:
        """N일 이상 된 Post 삭제 (자동 정리용)."""
        cutoff_date = datetime.now() - timedelta(days=days)
        conn = _get_db()
        cursor = conn.cursor()

        cursor.execute(
            "DELETE FROM posts WHERE collected_at < ?",
            (cutoff_date,)
        )
        conn.commit()
        return cursor.rowcount

    def count(self) -> int:
        """전체 Post 수."""
        conn = _get_db()
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM posts")
        return cursor.fetchone()[0]

    def save_many(self, posts: list[Post]) -> int:
        """여러 Post 일괄 저장 (배치 처리, 성능 최적화)."""
        conn = _get_db()
        cursor = conn.cursor()
        saved = 0

        for post in posts:
            data = _post_to_dict(post)
            cursor.execute("""
                INSERT OR REPLACE INTO posts
                (id, source, external_id, url, author, author_url, content_text,
                 content_html, media_urls, engagement_likes, engagement_reposts,
                 engagement_comments, engagement_views, published_at, collected_at,
                 summary, importance_score, language, is_relevant, category_names,
                 keywords, briefed_at, content_hash, dedup_cluster_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, tuple(data.values()))
            saved += 1

        conn.commit()
        return saved

    def get_unprocessed(self, limit: int = 100) -> list[Post]:
        """AI 처리 안 된 게시물 조회 (summary가 None)."""
        conn = _get_db()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM posts
            WHERE summary IS NULL
            ORDER BY collected_at DESC
            LIMIT ?
        """, (limit,))
        return [_post_from_row(row) for row in cursor.fetchall()]

    async def search(
        self,
        query: str | None = None,
        source: str | None = None,
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Post]:
        """게시물 검색 (비동기 래퍼)."""
        def _search():
            import json
            conn = _get_db()
            cursor = conn.cursor()

            conditions = ["is_relevant = 1"]
            params = []

            if source:
                conditions.append("source = ?")
                params.append(source)

            if query:
                conditions.append(f"(content_text LIKE ? OR summary LIKE ?)")
                search_term = f"%{query}%"
                params.extend([search_term, search_term])

            if category:
                conditions.append(f"category_names LIKE ?")
                params.append(f'%"{category}"%')

            where_clause = " AND ".join(conditions)
            sql = f"""
                SELECT * FROM posts
                WHERE {where_clause}
                ORDER BY collected_at DESC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])

            cursor.execute(sql, params)
            return [_post_from_row(row) for row in cursor.fetchall()]

        return await asyncio.to_thread(_search)

    async def count_by_source(self, start: datetime, end: datetime) -> dict[str, int]:
        """기간별 소스별 게시물 수."""
        def _count():
            conn = _get_db()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT source, COUNT(*) as count
                FROM posts
                WHERE collected_at BETWEEN ? AND ?
                GROUP BY source
            """, (start, end))

            return {row[0]: row[1] for row in cursor.fetchall()}

        return await asyncio.to_thread(_count)

    async def get_by_period(
        self, start: datetime, end: datetime, relevant_only: bool = True
    ) -> list[Post]:
        """기간별 게시물 조회."""
        def _get():
            conn = _get_db()
            cursor = conn.cursor()

            sql = """
                SELECT * FROM posts
                WHERE collected_at BETWEEN ? AND ?
            """
            params = [start, end]

            if relevant_only:
                sql += " AND is_relevant = 1"

            sql += " ORDER BY collected_at DESC"

            cursor.execute(sql, params)
            return [_post_from_row(row) for row in cursor.fetchall()]

        return await asyncio.to_thread(_get)

    async def get_top_keywords(self, limit: int = 20, days: int = 2) -> list[dict]:
        """최근 N일간 is_relevant 게시물의 키워드 빈도 top K.
        결과가 없으면 날짜 제한 없이 전체에서 조회한다."""
        def _query():
            conn = _get_db()
            cursor = conn.cursor()

            def _run(date_filter: str | None):
                base = """
                    SELECT value AS keyword, COUNT(*) AS cnt
                    FROM posts, json_each(posts.keywords)
                    WHERE is_relevant = 1
                      AND keywords IS NOT NULL
                      AND keywords != '[]'
                """
                if date_filter:
                    base += f" AND collected_at >= datetime('now', '{date_filter}')"
                base += " GROUP BY value ORDER BY cnt DESC LIMIT ?"
                cursor.execute(base, (limit,))
                return [{"keyword": row[0], "count": row[1]} for row in cursor.fetchall()]

            results = _run(f"-{days} days")
            if not results:
                results = _run(None)  # 날짜 제한 없이 전체 재조회
            return results

        return await asyncio.to_thread(_query)

    def get_storage_info(self) -> dict[str, Any]:
        """저장 공간 정보."""
        if not DB_PATH.exists():
            return {"size_bytes": 0, "size_mb": 0}

        size_bytes = DB_PATH.stat().st_size
        size_mb = size_bytes / (1024 * 1024)

        conn = _get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM posts")
        count = cursor.fetchone()[0]

        return {
            "size_bytes": size_bytes,
            "size_mb": round(size_mb, 2),
            "document_count": count,
        }
