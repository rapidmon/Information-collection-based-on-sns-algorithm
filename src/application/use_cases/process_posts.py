"""유즈케이스: AI 처리 파이프라인.

미처리 게시물을 가져와 필터링, 요약, 분류를 수행한다.
관련 없는 게시물은 Firestore에서 삭제한다.
"""

from __future__ import annotations

import logging

from src.domain.repositories.post_repository import PostRepository
from src.domain.services.ai_processor import AIProcessor

logger = logging.getLogger(__name__)


class ProcessPostsUseCase:
    """미처리 게시물에 대해 AI 처리를 수행하는 유즈케이스."""

    def __init__(self, post_repo: PostRepository, ai_processor: AIProcessor):
        self._post_repo = post_repo
        self._ai = ai_processor

    async def execute(self, limit: int = 200) -> dict[str, int]:
        """미처리 게시물을 AI로 처리. 처리 통계를 반환."""
        posts = await self._post_repo.get_unprocessed(limit=limit)
        if not posts:
            logger.info("처리할 새 게시물 없음")
            return {"total": 0, "relevant": 0, "filtered_out": 0, "deleted": 0}

        logger.info(f"AI 처리 시작: {len(posts)}건")

        # 1. 관련성 필터 + 요약
        filter_results = await self._ai.filter_and_summarize(posts)

        post_map = {p.id: p for p in posts}
        relevant_posts = []
        irrelevant_ids = []

        for result in filter_results:
            post = post_map.get(result.post_id)
            if post is None:
                continue
            post.is_relevant = result.is_relevant
            post.summary = result.summary
            post.language = result.language
            if result.is_relevant:
                relevant_posts.append(post)
            else:
                # 관련 없는 게시물은 삭제 대상
                doc_id = post.id or post.external_id
                if doc_id:
                    irrelevant_ids.append(doc_id)

        # 2. 관련 게시물만 분류 + 중요도
        if relevant_posts:
            cat_results = await self._ai.categorize(relevant_posts)
            cat_map = {r.post_id: r for r in cat_results}

            for post in relevant_posts:
                if cr := cat_map.get(post.id):
                    post.category_names = cr.categories
                    post.importance_score = cr.importance_score
                    post.keywords = cr.keywords or []

        # 3. 관련 게시물만 DB 업데이트
        for post in relevant_posts:
            if post.id is not None:
                await self._post_repo.update(post)

        # 4. 관련 없는 게시물 삭제
        deleted = 0
        if irrelevant_ids:
            deleted = await self._post_repo.delete_many(irrelevant_ids)
            logger.info(f"관련 없는 게시물 {deleted}건 삭제")

        stats = {
            "total": len(posts),
            "relevant": len(relevant_posts),
            "filtered_out": len(irrelevant_ids),
            "deleted": deleted,
        }
        logger.info(
            f"AI 처리 완료: 전체 {stats['total']}건, "
            f"관련 {stats['relevant']}건, 삭제 {stats['deleted']}건"
        )
        return stats
