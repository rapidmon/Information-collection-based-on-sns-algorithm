"""Hybrid AI processor: 배치 단계 백엔드 라우팅 + 백엔드 장애 시 상호 폴백."""

from __future__ import annotations

import logging

from src.domain.entities import Post
from src.domain.services.ai_processor import MergedTopic
from src.infrastructure.ai.claude_code_processor import ClaudeCodeProcessor
from src.infrastructure.ai.openai_processor import OpenAIProcessor

logger = logging.getLogger(__name__)


class HybridAIProcessor:
    """호출 단계별로 Claude Code CLI(정액 구독)와 OpenAI(종량 과금)를 나눠 쓴다.

    - 고빈도 배치(필터·분류·검증·기브리핑판정·통합가드): `routine_backend` 설정으로
      선택. claude면 구독 한도를 쓰고 OpenAI 청구액이 ~0이 된다. 호출당 고정
      오버헤드(~23k)는 거의 전부 cache_read(0.1x)이고, 물량 지배 단계인 필터는
      lean 플래그(추론 끔)로 호출당 총 등가 −45%.
    - 저빈도·품질 민감(발행 확정분 작문·큐레이션·티어 판정): 항상 Claude —
      브리핑 문장 품질에 직결되는 단계만 상위 모델로.
    - deduplicate_and_merge: LLM 미사용(결정적)이라 백엔드 무관.

    폴백: Claude 경로의 백엔드 장애(LLMBackendError — CLI 미설치·인증 만료·
    한도 소진·타임아웃)는 배치 루프의 예외 삼킴을 통과해 여기까지 전파되고,
    OpenAI로 같은 호출을 재시도한다. 무인 파이프라인이 대화형 작업과 구독
    한도를 공유하므로, 한도가 소진돼도 브리핑이 멈추거나 게시물이 조용히
    비관련 처리되지 않게 하는 안전망이다.
    """

    def __init__(self, openai_processor: OpenAIProcessor, claude_processor: ClaudeCodeProcessor):
        self._openai = openai_processor
        self._claude = claude_processor
        self._config = openai_processor._config

    def _routine_on_claude(self) -> bool:
        return getattr(self._config, "routine_backend", "openai") == "claude"

    async def _route(self, method: str, *args, **kwargs):
        """고빈도 배치 공통 라우팅 — claude 우선(설정 시), 실패하면 OpenAI로 완주.

        LLMBackendError만이 아니라 모든 예외를 폴백 트리거로 삼는다: Claude
        경로의 예상 못 한 버그로 파이프라인이 멈추는 것보다 OpenAI로 한 번 더
        시도하는 쪽이 안전하다. (배치 일부가 Claude에서 성공한 뒤 실패하면
        OpenAI가 전체를 다시 돌린다 — 단계가 멱등이라 중복 비용만 발생.)
        """
        if self._routine_on_claude():
            try:
                return await getattr(self._claude, method)(*args, **kwargs)
            except Exception as e:
                logger.warning(f"Claude {method} 실패, OpenAI 폴백: {e}")
        return await getattr(self._openai, method)(*args, **kwargs)

    def set_feedback_examples(self, examples: list[dict]) -> None:
        # 중요도 채점(categorize)이 어느 백엔드로 라우팅되어도 보정이 걸리도록 양쪽 주입
        self._openai.set_feedback_examples(examples)
        self._claude.set_feedback_examples(examples)

    async def filter_and_summarize(self, posts: list[Post]):
        return await self._route("filter_and_summarize", posts)

    async def categorize(self, posts: list[Post]):
        return await self._route("categorize", posts)

    async def verify_claims(self, posts: list[Post]):
        # Claude 경로는 내장 WebSearch(구독), OpenAI 경로는 DuckDuckGo 검색
        return await self._route("verify_claims", posts)

    async def find_covered_topics(self, topics: list[MergedTopic], recent_items: list[str]):
        # 브리핑 생성 시 1회의 헤드라인 비교 배치 — 고빈도 배치와 같은 라우팅
        return await self._route("find_covered_topics", topics, recent_items)

    async def consolidate_topics(self, topics: list[MergedTopic]):
        # 발행 확정분 최종 병합 가드 — 클러스터링과 같은 라우팅
        return await self._route("consolidate_topics", topics)

    async def judge_tiers(self, topics: list[MergedTopic], calibration_examples: list | None = None):
        # 티어 판정은 백엔드 설정과 무관하게 Claude 우선(한 번의 압축 호출) +
        # 백엔드 장애 시 전부-minor 강등 대신 OpenAI로 판정을 완주한다.
        try:
            return await self._claude.judge_tiers(topics, calibration_examples=calibration_examples)
        except Exception as e:
            logger.warning(f"Claude 티어 판정 실패, OpenAI 폴백: {e}")
            return await self._openai.judge_tiers(topics, calibration_examples=calibration_examples)

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
