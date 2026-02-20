"""유즈케이스: 브리핑 생성.

아직 브리핑에 포함되지 않은 관련 게시물을 모아
중복 제거 & 통합 후 브리핑을 생성한다.
"""

from __future__ import annotations

import logging
from datetime import datetime

from src.domain.entities import Briefing
from src.domain.repositories.briefing_repository import BriefingRepository
from src.domain.repositories.post_repository import PostRepository
from src.domain.services.ai_processor import AIProcessor
from src.domain.services.briefing_generator import BriefingGenerator

logger = logging.getLogger(__name__)


class GenerateBriefingUseCase:
    def __init__(
        self,
        post_repo: PostRepository,
        briefing_repo: BriefingRepository,
        ai_processor: AIProcessor,
        briefing_generator: BriefingGenerator,
    ):
        self._post_repo = post_repo
        self._briefing_repo = briefing_repo
        self._ai = ai_processor
        self._gen = briefing_generator

    async def execute(self, period_start: datetime, period_end: datetime) -> Briefing:
        """미브리핑 게시물로 브리핑 생성."""
        # 아직 브리핑에 포함되지 않은 관련 게시물 조회
        posts = await self._post_repo.get_unbriefed(limit=500)
        if not posts:
            logger.warning("브리핑 생성할 미브리핑 게시물 없음")
            return Briefing(
                title=f"{period_end.strftime('%Y-%m-%d')} 기술 모닝 브리핑 (데이터 없음)",
                period_start=period_start,
                period_end=period_end,
            )

        logger.info(f"브리핑 생성 시작: {len(posts)}건 미브리핑 게시물")

        # AI로 중복 제거 & 토픽 통합
        merged_topics = await self._ai.deduplicate_and_merge(posts)

        # 브리핑 문서 생성
        briefing = await self._gen.generate(
            merged_topics=merged_topics,
            period_start=period_start,
            period_end=period_end,
            total_posts_analyzed=len(posts),
        )

        # DB 저장
        briefing = await self._briefing_repo.save(briefing)

        # 포함된 게시물들을 브리핑 완료로 마킹
        post_ids = [p.id or p.external_id for p in posts if p.id or p.external_id]
        if post_ids:
            marked = await self._post_repo.mark_briefed(post_ids, datetime.utcnow())
            logger.info(f"브리핑 완료 마킹: {marked}건")

        logger.info(f"브리핑 생성 완료: '{briefing.title}' ({briefing.total_items}건 항목)")
        return briefing
