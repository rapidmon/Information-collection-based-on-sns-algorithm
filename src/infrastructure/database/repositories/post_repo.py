"""PostRepository — Firebase Firestore 구현.

Firestore 컬렉션: 'posts'
문서 ID: external_id (플랫폼 고유 ID를 그대로 문서 ID로 사용)
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Callable

from src.domain.entities import Post


def _post_to_dict(post: Post) -> dict[str, Any]:
    return {
        "source": post.source,
        "external_id": post.external_id,
        "url": post.url,
        "author": post.author,
        "author_url": post.author_url,
        "content_text": post.content_text,
        "content_html": post.content_html,
        "media_urls": post.media_urls,
        "engagement_likes": post.engagement_likes,
        "engagement_reposts": post.engagement_reposts,
        "engagement_comments": post.engagement_comments,
        "engagement_views": post.engagement_views,
        "published_at": post.published_at,
        "collected_at": post.collected_at,
        "summary": post.summary,
        "importance_score": post.importance_score,
        "language": post.language,
        "is_relevant": post.is_relevant,
        "category_names": post.category_names,
        "briefed_at": post.briefed_at,
        "content_hash": post.content_hash,
        "dedup_cluster_id": post.dedup_cluster_id,
    }


def _post_from_doc(doc) -> Post:
    d = doc.to_dict()
    return Post(
        id=doc.id,
        source=d.get("source", ""),
        external_id=d.get("external_id", ""),
        url=d.get("url", ""),
        author=d.get("author", ""),
        author_url=d.get("author_url"),
        content_text=d.get("content_text", ""),
        content_html=d.get("content_html"),
        media_urls=d.get("media_urls", []),
        engagement_likes=d.get("engagement_likes", 0),
        engagement_reposts=d.get("engagement_reposts", 0),
        engagement_comments=d.get("engagement_comments", 0),
        engagement_views=d.get("engagement_views", 0),
        published_at=d.get("published_at"),
        collected_at=d.get("collected_at"),
        summary=d.get("summary"),
        importance_score=d.get("importance_score"),
        language=d.get("language"),
        is_relevant=d.get("is_relevant"),
        category_names=d.get("category_names", []),
        briefed_at=d.get("briefed_at"),
        content_hash=d.get("content_hash"),
        dedup_cluster_id=d.get("dedup_cluster_id"),
    )


class FirestorePostRepository:
    """Firestore 기반 PostRepository 구현."""

    COLLECTION = "posts"
    _BATCH_LIMIT = 400

    def __init__(self, db):
        self._db = db

    def _col(self):
        return self._db.collection(self.COLLECTION)

    def _run_batch(self, items: list, operation: Callable) -> int:
        """Firestore batch 작업을 400건 단위로 실행. operation(batch, item)을 호출."""
        batch = self._db.batch()
        count = 0
        for item in items:
            operation(batch, item)
            count += 1
            if count % self._BATCH_LIMIT == 0:
                batch.commit()
                batch = self._db.batch()
        if count % self._BATCH_LIMIT != 0:
            batch.commit()
        return count

    async def save(self, post: Post) -> Post:
        def _save():
            doc_ref = self._col().document(post.external_id)
            doc = doc_ref.get()
            if doc.exists:
                post.id = doc.id
                return post
            doc_ref.set(_post_to_dict(post))
            post.id = post.external_id
            return post

        return await asyncio.to_thread(_save)

    async def save_many(self, posts: list[Post]) -> int:
        def _save_many():
            batch = self._db.batch()
            saved = 0
            for post in posts:
                doc_ref = self._col().document(post.external_id)
                doc = doc_ref.get()
                if doc.exists:
                    continue
                batch.set(doc_ref, _post_to_dict(post))
                saved += 1
                if saved % self._BATCH_LIMIT == 0:
                    batch.commit()
                    batch = self._db.batch()
            if saved % self._BATCH_LIMIT != 0:
                batch.commit()
            return saved

        return await asyncio.to_thread(_save_many)

    async def get_by_id(self, post_id: str) -> Post | None:
        def _get():
            doc = self._col().document(post_id).get()
            return _post_from_doc(doc) if doc.exists else None

        return await asyncio.to_thread(_get)

    async def get_by_external_id(self, external_id: str) -> Post | None:
        return await self.get_by_id(external_id)

    async def get_unprocessed(self, limit: int = 100) -> list[Post]:
        def _get():
            query = (
                self._col()
                .where("summary", "==", None)
                .order_by("collected_at", direction="DESCENDING")
                .limit(limit)
            )
            return [_post_from_doc(doc) for doc in query.stream()]

        return await asyncio.to_thread(_get)

    async def get_by_period(
        self, start: datetime, end: datetime, relevant_only: bool = True
    ) -> list[Post]:
        def _get():
            query = (
                self._col()
                .where("collected_at", ">=", start)
                .where("collected_at", "<=", end)
            )
            docs = list(query.stream())
            posts = [_post_from_doc(d) for d in docs]
            if relevant_only:
                posts = [p for p in posts if p.is_relevant is True]
            posts.sort(key=lambda p: p.collected_at or datetime.min, reverse=True)
            return posts

        return await asyncio.to_thread(_get)

    async def update(self, post: Post) -> Post:
        def _update():
            doc_id = post.id or post.external_id
            self._col().document(doc_id).update({
                "summary": post.summary,
                "importance_score": post.importance_score,
                "language": post.language,
                "is_relevant": post.is_relevant,
                "category_names": post.category_names,
                "dedup_cluster_id": post.dedup_cluster_id,
            })
            return post

        return await asyncio.to_thread(_update)

    async def update_many(self, posts: list[Post]) -> int:
        def _op(batch, post):
            doc_id = post.id or post.external_id
            if doc_id:
                batch.update(self._col().document(doc_id), {
                    "summary": post.summary,
                    "importance_score": post.importance_score,
                    "language": post.language,
                    "is_relevant": post.is_relevant,
                })

        return await asyncio.to_thread(self._run_batch, posts, _op)

    async def search(
        self,
        query: str | None = None,
        source: str | None = None,
        category: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
        relevant_only: bool = True,
    ) -> list[Post]:
        def _search():
            col = self._col()

            if relevant_only:
                q = col.where("is_relevant", "==", True)
                if source:
                    q = q.where("source", "==", source)
                q = q.order_by("collected_at", direction="DESCENDING")
            elif source:
                q = col.where("source", "==", source).order_by(
                    "collected_at", direction="DESCENDING"
                )
            else:
                q = col.order_by("collected_at", direction="DESCENDING")

            if start:
                q = q.where("collected_at", ">=", start)
            if end:
                q = q.where("collected_at", "<=", end)

            q = q.limit(limit + offset)
            docs = list(q.stream())
            posts = [_post_from_doc(d) for d in docs]

            if query:
                query_lower = query.lower()
                posts = [
                    p for p in posts
                    if query_lower in (p.content_text or "").lower()
                    or query_lower in (p.summary or "").lower()
                ]
            if category:
                posts = [p for p in posts if category in (p.category_names or [])]

            return posts[offset : offset + limit]

        return await asyncio.to_thread(_search)

    async def delete(self, post_id: str) -> None:
        def _delete():
            self._col().document(post_id).delete()
        await asyncio.to_thread(_delete)

    async def delete_many(self, post_ids: list[str]) -> int:
        def _op(batch, pid):
            batch.delete(self._col().document(pid))

        return await asyncio.to_thread(self._run_batch, post_ids, _op)

    async def get_unbriefed(self, limit: int = 500) -> list[Post]:
        def _get():
            query = (
                self._col()
                .where("is_relevant", "==", True)
                .where("briefed_at", "==", None)
                .order_by("collected_at", direction="DESCENDING")
                .limit(limit)
            )
            return [_post_from_doc(doc) for doc in query.stream()]
        return await asyncio.to_thread(_get)

    async def mark_briefed(self, post_ids: list[str], briefed_at: datetime) -> int:
        def _op(batch, pid):
            batch.update(self._col().document(pid), {"briefed_at": briefed_at})

        return await asyncio.to_thread(self._run_batch, post_ids, _op)

    async def count_by_source(self, start: datetime, end: datetime) -> dict[str, int]:
        def _count():
            query = (
                self._col()
                .where("collected_at", ">=", start)
                .where("collected_at", "<=", end)
            )
            counts: dict[str, int] = {}
            for doc in query.stream():
                source = doc.to_dict().get("source", "unknown")
                counts[source] = counts.get(source, 0) + 1
            return counts

        return await asyncio.to_thread(_count)
