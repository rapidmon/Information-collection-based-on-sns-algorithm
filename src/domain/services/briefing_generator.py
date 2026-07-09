from __future__ import annotations

from datetime import datetime
from typing import Protocol

from src.domain.entities import Briefing
from src.domain.services.ai_processor import MergedTopic


class BriefingGenerator(Protocol):
    """브리핑 문서 생성기 인터페이스."""

    def select_topics(self, merged_topics: list[MergedTopic]) -> list[MergedTopic]:
        """발행할 토픽 선별 (점수 하한·카테고리 상한·전체 상한).

        LLM 작문(compose_topics)을 발행 확정분에만 수행할 수 있도록
        generate()와 분리해 노출한다.
        """
        ...

    async def generate(
        self,
        merged_topics: list[MergedTopic],
        period_start: datetime,
        period_end: datetime,
        total_posts_analyzed: int,
    ) -> Briefing:
        """통합된 토픽 목록으로부터 브리핑 문서를 생성."""
        ...
