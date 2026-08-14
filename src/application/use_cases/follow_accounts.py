"""유즈케이스: 좋아요 누적 계정 자동 팔로우.

같은 계정의 글을 반복해 좋아요했다면 계정 자체가 양질이라는 신호다.
팔로우해서 알고리즘 피드가 그 계정을 더 자주 노출하도록 유도한다.
"""

from __future__ import annotations

import logging

from src.infrastructure.config.settings import FollowConfig

logger = logging.getLogger(__name__)


class FollowAccountsUseCase:
    """좋아요가 임계값 이상 쌓인 계정을 팔로우하는 유즈케이스."""

    def __init__(self, post_repo, follower, config: FollowConfig):
        self._post_repo = post_repo
        self._follower = follower
        self._cfg = config

    async def execute(self) -> dict[str, int]:
        """플랫폼별 팔로우 후보를 조회해 팔로우. 플랫폼별 실제 팔로우 수를 반환."""
        if not self._cfg.enabled:
            return {}

        results: dict[str, int] = {}
        for source in self._cfg.platforms:
            candidates = self._post_repo.get_follow_candidates(
                source=source,
                min_likes=self._cfg.min_likes,
                limit=self._cfg.max_per_run,
                max_attempts=self._cfg.max_attempts,
            )
            if not candidates:
                continue

            outcomes = await self._follower.follow_accounts(source, candidates)

            # dry-run은 미리보기이므로 기록하지 않는다 — 기록하면 실제 적용 시
            # 후보에서 빠져 영영 팔로우되지 않는다.
            if not self._cfg.dry_run:
                for o in outcomes:
                    self._post_repo.record_follow(
                        author_url=o["author_url"],
                        source=source,
                        screen_name=o.get("screen_name") or "",
                        like_count=o.get("like_count") or 0,
                        status=o["status"],
                    )

            followed = sum(1 for o in outcomes if o["status"] == "followed")
            if followed:
                results[source] = followed

        return results
