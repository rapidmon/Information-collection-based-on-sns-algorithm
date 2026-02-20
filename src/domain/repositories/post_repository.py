from __future__ import annotations

from datetime import datetime
from typing import Protocol

from src.domain.entities import Post


class PostRepository(Protocol):
    """게시물 저장소 인터페이스 (의존성 역전)."""

    async def save(self, post: Post) -> Post:
        """게시물 저장. 중복(content_hash/external_id) 시 무시하고 기존 반환."""
        ...

    async def save_many(self, posts: list[Post]) -> int:
        """여러 게시물 일괄 저장. 저장된 건수 반환."""
        ...

    async def get_by_id(self, post_id: int) -> Post | None: ...

    async def get_by_external_id(self, external_id: str) -> Post | None: ...

    async def get_unprocessed(self, limit: int = 100) -> list[Post]:
        """AI 처리 안 된 게시물 조회 (summary가 None)."""
        ...

    async def get_by_period(
        self,
        start: datetime,
        end: datetime,
        relevant_only: bool = True,
    ) -> list[Post]:
        """기간 내 게시물 조회."""
        ...

    async def update(self, post: Post) -> Post:
        """게시물 업데이트 (AI 처리 결과 등)."""
        ...

    async def update_many(self, posts: list[Post]) -> int:
        """여러 게시물 일괄 업데이트."""
        ...

    async def search(
        self,
        query: str | None = None,
        source: str | None = None,
        category: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Post]:
        """조건에 맞는 게시물 검색."""
        ...

    async def count_by_source(self, start: datetime, end: datetime) -> dict[str, int]:
        """소스별 게시물 수 집계."""
        ...
