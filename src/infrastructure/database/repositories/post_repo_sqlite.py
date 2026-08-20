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

# 이메일 브리핑에는 넣지 않고 슬랙에만 별도 섹션으로 붙이는 소스.
# AI 필터·채점을 태우지 않으므로 일반 브리핑의 상대평가에 섞이면 안 된다.
SLACK_ONLY_SOURCES = ("donga_series",)

# SQLite 연결 풀 (스레드 로컬 저장소)
_thread_local = threading.local()


def _get_db() -> sqlite3.Connection:
    """스레드 로컬 SQLite 연결 획득 (연결 풀).

    WAL + busy_timeout으로 스케줄러/웹 스레드가 동시에 쓸 때 'database is locked'를 완화.
    """
    if not hasattr(_thread_local, 'db') or _thread_local.db is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
        except sqlite3.Error:
            pass
        _thread_local.db = conn
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

    # liked_at 컬럼 마이그레이션 (기존 DB 호환 — 자동 좋아요 완료 시각)
    cursor.execute("PRAGMA table_info(posts)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    if "liked_at" not in existing_cols:
        cursor.execute("ALTER TABLE posts ADD COLUMN liked_at TIMESTAMP")

    # 자동 팔로우 이력 — 계정당 1행. 재시도 폭주와 중복 팔로우를 막는 상태 저장소.
    # status: followed(팔로우함) / already(이미 팔로우 중이었음) / failed(실패, attempts로 재시도 제한)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS followed_accounts (
            author_url TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            screen_name TEXT,
            like_count INTEGER,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            followed_at TIMESTAMP,
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


# 재수집 시 처리 상태(is_relevant·summary·category·briefed_at·liked_at 등)를 보존하는 UPSERT.
# 과거 INSERT OR REPLACE는 같은 게시물을 재수집할 때 행을 통째로 덮어써 처리상태를 NULL로
# 리셋했고, 그 결과 이미 처리한 글이 매 사이클 재필터링(토큰 낭비)·재브리핑·재추천됐다.
# 충돌(=이미 존재) 시엔 인게이지먼트/본문/collected_at만 갱신하고 처리·상태 필드는 건드리지 않는다.
# (liked_at은 INSERT 컬럼에 없어 신규행에선 기본 NULL, 기존행에선 보존된다.)
_UPSERT_SQL = """
    INSERT INTO posts
    (id, source, external_id, url, author, author_url, content_text,
     content_html, media_urls, engagement_likes, engagement_reposts,
     engagement_comments, engagement_views, published_at, collected_at,
     summary, importance_score, language, is_relevant, category_names,
     keywords, briefed_at, content_hash, dedup_cluster_id, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ON CONFLICT(id) DO UPDATE SET
        url=excluded.url,
        author=excluded.author,
        author_url=excluded.author_url,
        content_text=excluded.content_text,
        content_html=excluded.content_html,
        media_urls=excluded.media_urls,
        engagement_likes=excluded.engagement_likes,
        engagement_reposts=excluded.engagement_reposts,
        engagement_comments=excluded.engagement_comments,
        engagement_views=excluded.engagement_views,
        collected_at=excluded.collected_at,
        updated_at=CURRENT_TIMESTAMP
"""


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


def _parse_dt(val):
    """SQLite의 timestamp 값(주로 문자열)을 datetime으로 변환.

    sqlite3 연결에 detect_types가 없어 TIMESTAMP 컬럼이 문자열로 반환되므로,
    엔티티의 datetime 타입 계약을 지키도록 여기서 파싱한다.
    """
    if val is None or isinstance(val, datetime):
        return val
    if not isinstance(val, str) or not val.strip():
        return None
    s = val.strip()
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    s2 = s.replace("T", " ").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s2, fmt)
        except ValueError:
            continue
    return None


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
        published_at=_parse_dt(row["published_at"]),
        collected_at=_parse_dt(row["collected_at"]),
        summary=row["summary"],
        importance_score=row["importance_score"],
        language=row["language"],
        is_relevant=bool(row["is_relevant"]) if row["is_relevant"] is not None else None,
        category_names=json.loads(row["category_names"] or "[]"),
        keywords=json.loads(row["keywords"] or "[]"),
        briefed_at=_parse_dt(row["briefed_at"]),
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

        cursor.execute(_UPSERT_SQL, tuple(data.values()))
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

    def find_rejected_hashes(self, hashes: list[str]) -> set[str]:
        """주어진 content_hash 중 비관련(is_relevant=0) 판정 전례가 있는 해시 집합.

        동일 텍스트를 다계정으로 뿌리는 복제 스팸이 LLM 필터의 비결정성을
        물량으로 뚫는 것을 막는다 — process_posts가 필터 호출 전에 조회.
        """
        if not hashes:
            return set()
        conn = _get_db()
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(hashes))
        cursor.execute(f"""
            SELECT DISTINCT content_hash FROM posts
            WHERE is_relevant = 0 AND content_hash IN ({placeholders})
        """, hashes)
        return {row[0] for row in cursor.fetchall()}

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

    def delete_irrelevant_older_than(self, days: int) -> int:
        """필터 탈락(is_relevant=0) 게시물 중 N일간 재수집되지 않은 것 삭제.

        collected_at은 재수집 때마다 갱신되므로 'N일 경과' = 'N일간 피드에 없음'.
        수집 컷오프(max_age_days)보다 길게 잡으면 삭제 직후 같은 글이 신규로
        재수집돼 재필터링(토큰 낭비)되는 루프가 원천 차단된다.
        """
        cutoff_date = datetime.now() - timedelta(days=days)
        conn = _get_db()
        cursor = conn.cursor()

        cursor.execute(
            "DELETE FROM posts WHERE is_relevant = 0 AND collected_at < ?",
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
            cursor.execute(_UPSERT_SQL, tuple(data.values()))
            saved += 1

        conn.commit()
        return saved

    def get_likeable(self, source: str, min_importance: float, limit: int) -> list[Post]:
        """자동 좋아요 대상 조회.

        관련 O(is_relevant=1) + 중요도 임계값 이상 + 아직 좋아요 안 함(liked_at IS NULL)
        + URL 보유. 중요도 높은 순으로 반환.
        """
        conn = _get_db()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM posts
            WHERE source = ?
              AND is_relevant = 1
              AND importance_score >= ?
              AND liked_at IS NULL
              AND url IS NOT NULL AND url != ''
            ORDER BY importance_score DESC, collected_at DESC
            LIMIT ?
            """,
            (source, min_importance, limit),
        )
        return [_post_from_row(row) for row in cursor.fetchall()]

    def mark_liked(self, post_ids: list[str], liked_at: datetime) -> int:
        """게시물들의 liked_at 설정 (자동 좋아요 완료 마킹)."""
        if not post_ids:
            return 0
        conn = _get_db()
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(post_ids))
        cursor.execute(
            f"UPDATE posts SET liked_at = ? WHERE id IN ({placeholders})",
            [liked_at.isoformat(), *post_ids],
        )
        conn.commit()
        return cursor.rowcount

    # ─── 자동 팔로우 ───

    def get_follow_candidates(
        self, source: str, min_likes: int, limit: int, max_attempts: int = 3
    ) -> list[dict]:
        """좋아요 누적이 임계값 이상인 계정을 팔로우 후보로 반환 (좋아요 많은 순).

        author_url이 계정 식별자다 — 트위터는 2026-08 이전 수집분에 작성자가
        비어 있어(스키마 변경 미추적) 집계에서 자연히 빠진다.
        이미 처리된 계정(followed/already)과 재시도 한도를 넘긴 실패 계정은 제외한다.
        """
        conn = _get_db()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT p.author_url, MAX(p.author) AS author, COUNT(*) AS like_count
            FROM posts p
            LEFT JOIN followed_accounts f ON f.author_url = p.author_url
            WHERE p.source = ?
              AND p.liked_at IS NOT NULL
              AND p.author_url IS NOT NULL AND p.author_url != ''
              AND (f.author_url IS NULL
                   OR (f.status = 'failed' AND f.attempts < ?))
            GROUP BY p.author_url
            HAVING COUNT(*) >= ?
            ORDER BY like_count DESC
            LIMIT ?
            """,
            (source, max_attempts, min_likes, limit),
        )
        return [
            {
                "author_url": row["author_url"],
                "author": row["author"],
                # threads는 .../@handle 형태라 @를 떼야 프로필 URL을 다시 만들 수 있다
                "screen_name": (row["author_url"] or "").rstrip("/").rsplit("/", 1)[-1].lstrip("@"),
                "like_count": row["like_count"],
            }
            for row in cursor.fetchall()
        ]

    def record_follow(
        self, author_url: str, source: str, screen_name: str, like_count: int, status: str
    ) -> None:
        """팔로우 시도 결과 기록. 실패는 attempts를 올려 무한 재시도를 막는다."""
        conn = _get_db()
        cursor = conn.cursor()
        followed_at = datetime.utcnow().isoformat() if status in ("followed", "already") else None
        cursor.execute(
            """
            INSERT INTO followed_accounts
                (author_url, source, screen_name, like_count, status, attempts, followed_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(author_url) DO UPDATE SET
                status=excluded.status,
                like_count=excluded.like_count,
                attempts=followed_accounts.attempts + 1,
                followed_at=COALESCE(excluded.followed_at, followed_accounts.followed_at),
                updated_at=CURRENT_TIMESTAMP
            """,
            (author_url, source, screen_name, like_count, status, followed_at),
        )
        conn.commit()

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

    async def get_unbriefed(self, limit: int = 500) -> list[Post]:
        """브리핑에 포함되지 않은 관련 게시물 조회 (briefed_at IS NULL).

        SLACK_ONLY_SOURCES는 제외한다 — AI 채점을 안 거쳐 importance_score가 없고,
        일반 브리핑의 상대평가·dedup에 섞이면 안 되기 때문이다. 이들은 슬랙 발송
        단계에서 별도 섹션으로 붙는다(get_slack_only_unbriefed).
        """
        def _query():
            conn = _get_db()
            cursor = conn.cursor()
            placeholders = ",".join("?" * len(SLACK_ONLY_SOURCES))
            cursor.execute(f"""
                SELECT * FROM posts
                WHERE is_relevant = 1
                  AND briefed_at IS NULL
                  AND source NOT IN ({placeholders})
                ORDER BY collected_at DESC
                LIMIT ?
            """, (*SLACK_ONLY_SOURCES, limit))
            return [_post_from_row(row) for row in cursor.fetchall()]

        return await asyncio.to_thread(_query)

    async def get_slack_only_unbriefed(self, limit: int = 20) -> list[Post]:
        """슬랙 전용 소스의 미발송분 (오래된 것부터 — 올라온 순서대로 보여준다)."""
        def _query():
            conn = _get_db()
            cursor = conn.cursor()
            placeholders = ",".join("?" * len(SLACK_ONLY_SOURCES))
            cursor.execute(f"""
                SELECT * FROM posts
                WHERE briefed_at IS NULL
                  AND source IN ({placeholders})
                ORDER BY published_at ASC, collected_at ASC
                LIMIT ?
            """, (*SLACK_ONLY_SOURCES, limit))
            return [_post_from_row(row) for row in cursor.fetchall()]

        return await asyncio.to_thread(_query)

    async def mark_briefed(self, post_ids: list[str], briefed_at: datetime) -> int:
        """게시물들의 briefed_at을 설정 (브리핑 완료 마킹)."""
        if not post_ids:
            return 0

        def _update():
            conn = _get_db()
            cursor = conn.cursor()
            placeholders = ",".join("?" * len(post_ids))
            cursor.execute(f"""
                UPDATE posts
                SET briefed_at = ?
                WHERE id IN ({placeholders})
            """, [briefed_at.isoformat(), *post_ids])
            conn.commit()
            return cursor.rowcount

        return await asyncio.to_thread(_update)

    async def delete_low_importance(self, max_score: float) -> int:
        """브리핑 완료(briefed_at NOT NULL)이고 중요도 max_score 이하인 게시물 삭제.

        브리핑이 끝난 저중요도 게시물은 재사용처가 없으므로 30일 정리를 기다리지
        않고 즉시 지워 저장공간·조회 부담을 줄인다.
        """
        def _delete():
            conn = _get_db()
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM posts
                WHERE briefed_at IS NOT NULL
                  AND importance_score IS NOT NULL
                  AND importance_score <= ?
            """, (max_score,))
            conn.commit()
            return cursor.rowcount

        return await asyncio.to_thread(_delete)

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
