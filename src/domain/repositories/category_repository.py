from __future__ import annotations

from typing import Protocol

from src.domain.entities import Category


class CategoryRepository(Protocol):
    """카테고리 저장소 인터페이스."""

    async def get_all(self) -> list[Category]: ...

    async def get_by_name(self, name: str) -> Category | None: ...

    async def upsert(self, category: Category) -> Category:
        """없으면 생성, 있으면 업데이트."""
        ...
