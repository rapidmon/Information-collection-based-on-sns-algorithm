"""CategoryRepository — 인메모리 구현 (settings.yaml 기반)."""

from __future__ import annotations

from src.domain.entities import Category


class MemoryCategoryRepository:
    def __init__(self, categories: list[Category]):
        self._categories = {cat.name: cat for cat in categories}

    async def get_all(self) -> list[Category]:
        return list(self._categories.values())

    async def get_by_name(self, name: str) -> Category | None:
        return self._categories.get(name)

    async def upsert(self, category: Category) -> Category:
        self._categories[category.name] = category
        return category
