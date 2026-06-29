"""유즈케이스: AI 통과 게시물 자동 좋아요.

AI 처리로 '관련 O + 중요도 높음'으로 판정된 게시물에만 좋아요를 눌러,
SNS 알고리즘이 양질의 기술 콘텐츠를 더 많이 노출하도록 유도한다.
"""

from __future__ import annotations

import logging
from datetime import datetime

from src.domain.repositories.post_repository import PostRepository
from src.domain.services.liker import PostLiker
from src.infrastructure.config.settings import LikeConfig

logger = logging.getLogger(__name__)


class LikePostsUseCase:
    """선별된 게시물에 좋아요를 누르는 유즈케이스."""

    def __init__(self, post_repo: PostRepository, liker: PostLiker, config: LikeConfig):
        self._post_repo = post_repo
        self._liker = liker
        self._cfg = config

    async def execute(self) -> dict[str, int]:
        """플랫폼별로 좋아요 대상을 조회해 좋아요. 플랫폼별 처리 건수를 반환."""
        if not self._cfg.enabled:
            return {}

        results: dict[str, int] = {}
        for source in self._cfg.platforms:
            posts = self._post_repo.get_likeable(
                source=source,
                min_importance=self._cfg.min_importance,
                limit=self._cfg.max_per_run,
            )
            if not posts:
                continue

            liked_ids = await self._liker.like_posts(source, posts)

            # 실제 좋아요한 경우만 DB에 마킹 (dry-run은 미리보기이므로 마킹 안 함)
            if liked_ids and not self._cfg.dry_run:
                self._post_repo.mark_liked(liked_ids, datetime.utcnow())

            results[source] = len(liked_ids)

        return results
