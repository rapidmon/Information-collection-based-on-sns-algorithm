from __future__ import annotations

from typing import Protocol

from src.domain.entities import Briefing


class BriefingRepository(Protocol):
    """브리핑 저장소 인터페이스."""

    async def save(self, briefing: Briefing) -> Briefing: ...

    async def get_by_id(self, briefing_id: int) -> Briefing | None: ...

    async def get_latest(self) -> Briefing | None: ...

    async def get_all(self, limit: int = 30, offset: int = 0) -> list[Briefing]: ...

    async def update(self, briefing: Briefing) -> Briefing: ...
