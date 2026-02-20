"""CollectionRunRepository — Firebase Firestore 구현.

Firestore 컬렉션: 'collection_runs'
"""

from __future__ import annotations

import asyncio

from src.domain.entities import CollectionRun


def _run_to_dict(run: CollectionRun) -> dict:
    return {
        "source": run.source,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "status": run.status,
        "posts_collected": run.posts_collected,
        "error_message": run.error_message,
    }


def _run_from_doc(doc) -> CollectionRun:
    d = doc.to_dict()
    return CollectionRun(
        id=doc.id,
        source=d.get("source", ""),
        started_at=d.get("started_at"),
        completed_at=d.get("completed_at"),
        status=d.get("status", "running"),
        posts_collected=d.get("posts_collected", 0),
        error_message=d.get("error_message"),
    )


class FirestoreCollectionRunRepository:
    COLLECTION = "collection_runs"

    def __init__(self, db):
        self._db = db

    def _col(self):
        return self._db.collection(self.COLLECTION)

    async def save(self, run: CollectionRun) -> CollectionRun:
        def _save():
            doc_ref = self._col().document()
            doc_ref.set(_run_to_dict(run))
            run.id = doc_ref.id
            return run

        return await asyncio.to_thread(_save)

    async def update(self, run: CollectionRun) -> CollectionRun:
        def _update():
            self._col().document(run.id).update({
                "completed_at": run.completed_at,
                "status": run.status,
                "posts_collected": run.posts_collected,
                "error_message": run.error_message,
            })
            return run

        return await asyncio.to_thread(_update)

    async def get_last_successful(self, source: str) -> CollectionRun | None:
        def _get():
            query = (
                self._col()
                .where("source", "==", source)
                .where("status", "==", "success")
                .order_by("completed_at", direction="DESCENDING")
                .limit(1)
            )
            docs = list(query.stream())
            return _run_from_doc(docs[0]) if docs else None

        return await asyncio.to_thread(_get)

    async def count_consecutive_failures(self, source: str) -> int:
        def _count():
            query = (
                self._col()
                .where("source", "==", source)
                .order_by("started_at", direction="DESCENDING")
                .limit(10)
            )
            count = 0
            for doc in query.stream():
                if doc.to_dict().get("status") == "failed":
                    count += 1
                else:
                    break
            return count

        return await asyncio.to_thread(_count)

    async def get_recent(self, limit: int = 20) -> list[CollectionRun]:
        def _get():
            query = (
                self._col()
                .order_by("started_at", direction="DESCENDING")
                .limit(limit)
            )
            return [_run_from_doc(d) for d in query.stream()]

        return await asyncio.to_thread(_get)
