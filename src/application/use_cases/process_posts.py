"""유즈케이스: AI 처리 파이프라인.

미처리 게시물(SQLite)을 청크 단위로 가져와 필터링·요약·분류를 수행하고
결과를 DB에 반영한다. 비관련 게시물은 삭제하지 않고 is_relevant=False로만 표시한다.
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

    async def execute(
        self,
        limit: int = 200,
        chunk_size: int = 50,
        min_posts_threshold: int = 0,
    ) -> dict[str, int]:
        """미처리 게시물을 청크 단위로 가져와 AI 처리. 처리 통계를 반환."""
        totals = {"total": 0, "relevant": 0, "filtered_out": 0}
        processed_total = 0
        first_fetch = True

        while processed_total < limit:
            remaining = limit - processed_total
            # 첫 fetch는 threshold 검사를 위해 max(chunk_size, threshold)만큼 가져옴
            fetch_size = (
                max(chunk_size, min_posts_threshold) if first_fetch
                else min(chunk_size, remaining)
            )
            chunk = self._post_repo.get_unprocessed(limit=fetch_size)

            if not chunk:
                if first_fetch:
                    logger.info("처리할 새 게시물 없음")
                break

            if first_fetch and len(chunk) < min_posts_threshold:
                logger.info(
                    f"처리 건수 부족 ({len(chunk)}건 < {min_posts_threshold}건), 스킵"
                )
                break

            # 첫 fetch에서 chunk_size를 초과해 가져왔어도 처리는 chunk_size씩
            chunk = chunk[: min(chunk_size, remaining)]
            first_fetch = False

            chunk_stats = await self._process_chunk(chunk)
            totals["total"] += chunk_stats["total"]
            totals["relevant"] += chunk_stats["relevant"]
            totals["filtered_out"] += chunk_stats["filtered_out"]
            processed_total += len(chunk)

        logger.info(
            f"AI 처리 완료: 전체 {totals['total']}건, "
            f"관련 {totals['relevant']}건, 비관련 {totals['filtered_out']}건"
        )
        return totals

    async def _process_chunk(self, posts: list) -> dict[str, int]:
        """단일 청크에 대해 필터→검증→분류→업데이트 파이프라인을 실행."""
        logger.info(f"AI 처리 시작: {len(posts)}건")

        # 1. 관련성 필터 + 요약
        filter_results = await self._ai.filter_and_summarize(posts)

        post_map = {p.id: p for p in posts}
        relevant_posts = []
        irrelevant_posts = []
        processed_ids: set[str] = set()

        for result in filter_results:
            post = post_map.get(result.post_id)
            if post is None:
                continue
            processed_ids.add(result.post_id)
            post.is_relevant = result.is_relevant
            post.summary = result.summary or ("[filtered]" if not result.is_relevant else None)
            post.language = result.language
            if result.is_relevant:
                relevant_posts.append(post)
            else:
                irrelevant_posts.append(post)

        # AI가 응답에 포함하지 않은 게시물도 비관련으로 처리 (재처리 루프 방지)
        for post in posts:
            if post.id not in processed_ids:
                post.is_relevant = False
                post.summary = "[filtered]"
                irrelevant_posts.append(post)

        # 2. (웹 검색 교차 검증은 여기서 하지 않는다 — 처리량 확보)
        #    검증은 비싸고(웹검색) 발행할 항목에만 필요하므로 브리핑 직전 후보에만 수행한다.
        #    (generate_briefing에서 verify_claims 실행)

        # 3. 관련 게시물만 분류 + 중요도
        if relevant_posts:
            cat_results = await self._ai.categorize(relevant_posts)
            cat_map = {r.post_id: r for r in cat_results}

            for post in relevant_posts:
                if cr := cat_map.get(post.id):
                    post.category_names = cr.categories
                    post.importance_score = cr.importance_score
                    post.keywords = cr.keywords or []

        # 4. 청크 단위로 즉시 DB 업데이트 — 메모리 해제 + 크래시 시 진행분 보존
        all_processed = relevant_posts + irrelevant_posts
        if all_processed:
            self._post_repo.update_many(all_processed)

        return {
            "total": len(posts),
            "relevant": len(relevant_posts),
            "filtered_out": len(irrelevant_posts),
        }
