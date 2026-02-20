from __future__ import annotations

from datetime import datetime
from typing import Protocol

from src.domain.entities import Briefing
from src.domain.services.ai_processor import MergedTopic


class BriefingGenerator(Protocol):
    """브리핑 문서 생성기 인터페이스."""

    async def generate(
        self,
        merged_topics: list[MergedTopic],
        period_start: datetime,
        period_end: datetime,
        total_posts_analyzed: int,
    ) -> Briefing:
        """통합된 토픽 목록으로부터 브리핑 문서를 생성."""
        ...
