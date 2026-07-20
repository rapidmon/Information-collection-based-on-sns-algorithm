"""Hybrid AI processor: OpenAI for routine work, Claude Code only where explicitly needed."""

from __future__ import annotations

import logging

from src.domain.entities import Post
from src.domain.services.ai_processor import MergedTopic
from src.infrastructure.ai.claude_code_processor import ClaudeCodeProcessor
from src.infrastructure.ai.openai_processor import OpenAIProcessor

logger = logging.getLogger(__name__)


class HybridAIProcessor:
    """호출 빈도·품질 민감도에 따라 OpenAI와 Claude Code를 나눠 쓴다.

    - 고빈도 배치(필터·분류·검증, 30분마다): OpenAI — Claude CLI는 호출당
      ~28k 토큰 고정 오버헤드가 있어 고빈도 호출에 쓰면 구독 한도를 잠식한다.
    - 저빈도·품질 민감(발행 확정분 작문·독자군 큐레이션, 하루 1~3회): Claude —
      브리핑 문장 품질에 직결되는 단계만 상위 모델로.
    - deduplicate_and_merge: LLM 미사용(결정적)이라 백엔드 무관.
    """

    def __init__(self, openai_processor: OpenAIProcessor, claude_processor: ClaudeCodeProcessor):
        self._openai = openai_processor
        self._claude = claude_processor
        self._config = openai_processor._config

    def set_feedback_examples(self, examples: list[dict]) -> None:
        # 분류(중요도 채점)는 OpenAI가 수행하므로 그쪽에만 주입
        self._openai.set_feedback_examples(examples)

    async def filter_and_summarize(self, posts: list[Post]):
        return await self._openai.filter_and_summarize(posts)

    async def categorize(self, posts: list[Post]):
        return await self._openai.categorize(posts)

    async def verify_claims(self, posts: list[Post]):
        return await self._openai.verify_claims(posts)

    async def find_covered_topics(self, topics: list[MergedTopic], recent_items: list[str]):
        # 브리핑 생성 시 1회의 헤드라인 비교 배치 — 고빈도 배치와 같은 OpenAI 경로
        return await self._openai.find_covered_topics(topics, recent_items)

    async def judge_tiers(self, topics: list[MergedTopic], calibration_examples: list | None = None):
        # Tier judgement is one compact call after merging; Claude is useful here.
        return await self._claude.judge_tiers(topics, calibration_examples=calibration_examples)

    async def generate_curation(self, topics: list[MergedTopic], audience: str):
        # 하루 독자군당 1회 — 문체 품질이 중요해 Claude(sonnet) 사용, 실패 시 OpenAI 폴백
        try:
            return await self._claude.generate_curation(topics, audience)
        except Exception as e:
            logger.warning(f"Claude 큐레이션 실패, OpenAI 폴백: {e}")
            return await self._openai.generate_curation(topics, audience)

    async def deduplicate_and_merge(self, posts: list[Post]) -> list[MergedTopic]:
        if not posts:
            return []
        topics = await self._openai.deduplicate_and_merge(posts)
        logger.info("하이브리드 병합 완료: 게시물 %s건 → %s개 토픽", len(posts), len(topics))
        return topics

    async def compose_topics(self, topics: list[MergedTopic], posts: list[Post]) -> list[MergedTopic]:
        # 하루 1~3회 배치 — 발행 문장 품질에 직결되므로 Claude(sonnet) 사용.
        # 배치 실패는 compose_topics 내부에서 결정적 초안 유지로 처리되고,
        # CLI 자체가 죽는 경우만 여기서 OpenAI로 폴백한다.
        try:
            return await self._claude.compose_topics(topics, posts)
        except Exception as e:
            logger.warning(f"Claude 발행 작문 실패, OpenAI 폴백: {e}")
            return await self._openai.compose_topics(topics, posts)
