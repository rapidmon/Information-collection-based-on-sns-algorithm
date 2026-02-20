"""CategoryRepository — Firebase Firestore 구현.

Firestore 컬렉션: 'categories'
문서 ID: 카테고리 name (예: 'AI', 'Semiconductor')
"""

from __future__ import annotations

import asyncio

from src.domain.entities import Category


def _category_from_doc(doc) -> Category:
    d = doc.to_dict()
    return Category(
        id=doc.id,
        name=d.get("name", doc.id),
        name_ko=d.get("name_ko", doc.id),
        color=d.get("color", "#888888"),
    )


class FirestoreCategoryRepository:
    COLLECTION = "categories"

    def __init__(self, db):
        self._db = db

    def _col(self):
        return self._db.collection(self.COLLECTION)

    async def get_all(self) -> list[Category]:
        def _get():
            return [_category_from_doc(d) for d in self._col().stream()]

        return await asyncio.to_thread(_get)

    async def get_by_name(self, name: str) -> Category | None:
        def _get():
            doc = self._col().document(name).get()
            return _category_from_doc(doc) if doc.exists else None

        return await asyncio.to_thread(_get)

    async def upsert(self, category: Category) -> Category:
        def _upsert():
            self._col().document(category.name).set({
                "name": category.name,
                "name_ko": category.name_ko,
                "color": category.color,
            })
            category.id = category.name
            return category

        return await asyncio.to_thread(_upsert)
