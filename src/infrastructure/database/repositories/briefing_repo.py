"""BriefingRepository — Firebase Firestore 구현.

Firestore 컬렉션: 'briefings'
BriefingItem은 briefing 문서 내 'items' 배열로 임베드 (서브컬렉션 아님).
Firestore 문서 1MB 제한 내에서 20개 항목은 충분.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from src.domain.entities import Briefing, BriefingItem


def _briefing_to_dict(b: Briefing) -> dict[str, Any]:
    return {
        "title": b.title,
        "briefing_type": b.briefing_type,
        "generated_at": b.generated_at,
        "period_start": b.period_start,
        "period_end": b.period_end,
        "total_posts_analyzed": b.total_posts_analyzed,
        "total_items": b.total_items,
        "content_html": b.content_html,
        "content_text": b.content_text,
        "email_sent": b.email_sent,
        "email_sent_at": b.email_sent_at,
        "items": [_item_to_dict(item) for item in b.items],
    }


def _item_to_dict(item: BriefingItem) -> dict[str, Any]:
    return {
        "headline": item.headline,
        "body": item.body,
        "importance_score": item.importance_score,
        "category_name": item.category_name,
        "sort_order": item.sort_order,
        "source_count": item.source_count,
        "sources_summary": item.sources_summary,
        "source_post_ids": item.source_post_ids,
    }


def _briefing_from_doc(doc) -> Briefing:
    d = doc.to_dict()
    items_data = d.get("items", [])
    items = [
        BriefingItem(
            headline=i.get("headline", ""),
            body=i.get("body", ""),
            importance_score=i.get("importance_score", 0.5),
            category_name=i.get("category_name"),
            sort_order=i.get("sort_order", 0),
            source_count=i.get("source_count", 0),
            sources_summary=i.get("sources_summary", ""),
            source_post_ids=i.get("source_post_ids", []),
        )
        for i in items_data
    ]
    return Briefing(
        id=doc.id,
        title=d.get("title", ""),
        briefing_type=d.get("briefing_type", "daily"),
        generated_at=d.get("generated_at"),
        period_start=d.get("period_start"),
        period_end=d.get("period_end"),
        total_posts_analyzed=d.get("total_posts_analyzed", 0),
        total_items=d.get("total_items", 0),
        content_html=d.get("content_html"),
        content_text=d.get("content_text"),
        email_sent=d.get("email_sent", False),
        email_sent_at=d.get("email_sent_at"),
        items=items,
    )


class FirestoreBriefingRepository:
    COLLECTION = "briefings"

    def __init__(self, db):
        self._db = db

    def _col(self):
        return self._db.collection(self.COLLECTION)

    async def save(self, briefing: Briefing) -> Briefing:
        def _save():
            doc_ref = self._col().document()
            doc_ref.set(_briefing_to_dict(briefing))
            briefing.id = doc_ref.id
            return briefing

        return await asyncio.to_thread(_save)

    async def get_by_id(self, briefing_id: str) -> Briefing | None:
        def _get():
            doc = self._col().document(briefing_id).get()
            return _briefing_from_doc(doc) if doc.exists else None

        return await asyncio.to_thread(_get)

    async def get_latest(self) -> Briefing | None:
        def _get():
            query = (
                self._col()
                .order_by("generated_at", direction="DESCENDING")
                .limit(1)
            )
            docs = list(query.stream())
            return _briefing_from_doc(docs[0]) if docs else None

        return await asyncio.to_thread(_get)

    async def get_all(self, limit: int = 30, offset: int = 0) -> list[Briefing]:
        def _get():
            query = (
                self._col()
                .order_by("generated_at", direction="DESCENDING")
                .limit(limit + offset)
            )
            docs = list(query.stream())
            return [_briefing_from_doc(d) for d in docs[offset:]]

        return await asyncio.to_thread(_get)

    async def update(self, briefing: Briefing) -> Briefing:
        def _update():
            self._col().document(briefing.id).update({
                "email_sent": briefing.email_sent,
                "email_sent_at": briefing.email_sent_at,
                "content_html": briefing.content_html,
                "content_text": briefing.content_text,
            })
            return briefing

        return await asyncio.to_thread(_update)
