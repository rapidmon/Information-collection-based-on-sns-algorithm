from __future__ import annotations

from typing import Protocol

from src.domain.entities import CollectionRun


class CollectionRunRepository(Protocol):
    """수집 실행 로그 저장소 인터페이스."""

    async def save(self, run: CollectionRun) -> CollectionRun: ...

    async def update(self, run: CollectionRun) -> CollectionRun: ...

    async def get_last_successful(self, source: str) -> CollectionRun | None: ...

    async def count_consecutive_failures(self, source: str) -> int: ...

    async def get_recent(self, limit: int = 20) -> list[CollectionRun]: ...
