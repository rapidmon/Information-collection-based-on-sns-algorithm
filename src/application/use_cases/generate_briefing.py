"""유즈케이스: 브리핑 생성.

파이프라인:
  ① 게시물 점수 산정 — 분류 완료 게시물을 카테고리별 상대평가
  ② 클러스터링 — 토큰 유사도 후보군을 결정적으로 병합 (LLM 미사용)
  ③ 사건 점수 집계 — 사전 계산된 게시물 점수를 사건 단위로 집계
  ④ 검증 — 리스트업된 상위(정규화 0.85+) 후보만 웹검색 교차검증, 과반 반박 클러스터 제거
  ⑤ 선별 — 점수 하한·카테고리 상한으로 발행 항목 확정
  ⑥ 작문 — 발행 확정 항목만 LLM으로 headline/불릿 리라이트 (탈락분 작문 토큰 절약)
  ⑦ 문서 생성

하나의 사건이 여러 카테고리에 걸쳐도 최종 브리핑에는 대표 카테고리 하나로만 노출한다.
"""

from __future__ import annotations

import logging
from datetime import datetime

from src.domain.entities import Briefing
from src.domain.repositories.briefing_repository import BriefingRepository
from src.domain.repositories.post_repository import PostRepository
from src.domain.services.ai_processor import AIProcessor
from src.domain.services.briefing_generator import BriefingGenerator
from src.infrastructure.ai.importance_scorer import score_posts_by_category, score_topics
from src.infrastructure.config.settings import ScoringConfig

logger = logging.getLogger(__name__)

# 리스트업된 항목 중 이 정규화 점수 이상만 신뢰도 검증(웹검색)
VERIFY_MIN_SCORE = 0.85

# 브리핑 완료 후 이 AI 중요도 이하 게시물은 즉시 삭제 (재사용처 없음)
PURGE_MAX_SCORE = 0.6


class GenerateBriefingUseCase:
    def __init__(
        self,
        post_repo: PostRepository,
        briefing_repo: BriefingRepository,
        ai_processor: AIProcessor,
        briefing_generator: BriefingGenerator,
        scoring_config: ScoringConfig,
        feedback_repo=None,
    ):
        self._post_repo = post_repo
        self._briefing_repo = briefing_repo
        self._ai = ai_processor
        self._gen = briefing_generator
        self._scoring = scoring_config
        self._feedback_repo = feedback_repo

    async def execute(self, period_start: datetime, period_end: datetime) -> Briefing:
        """미브리핑 게시물로 브리핑 생성."""
        posts = await self._post_repo.get_unbriefed(limit=10000)
        if not posts:
            logger.warning("브리핑 생성할 미브리핑 게시물 없음")
            return Briefing(
                title=f"{period_end.strftime('%Y-%m-%d')} 기술 모닝 브리핑 (데이터 없음)",
                period_start=period_start,
                period_end=period_end,
            )

        logger.info(f"브리핑 생성 시작: {len(posts)}건 미브리핑 게시물")
        # LLM이 post_ids를 문자열/정수 어느 쪽으로 반환해도 맞도록 str 키로 통일
        post_map = {str(p.id): p for p in posts if p.id is not None}

        # ① 분류 완료 게시물을 카테고리별 상대 점수로 먼저 산정한다.
        post_category_scores = score_posts_by_category(posts, self._scoring)

        # ② 클러스터링 (같은 사건 병합)
        merged_topics = await self._ai.deduplicate_and_merge(posts)
        if not merged_topics:
            logger.warning("클러스터링 결과 없음")
            merged_topics = []

        # ③ 병합된 사건은 사전 계산된 게시물 점수를 집계만 한다.
        score_topics(
            merged_topics,
            post_map,
            posts,
            self._scoring,
            post_category_scores=post_category_scores,
        )

        # ④ 상위(0.85+) 후보만 검증 → 과반 반박 클러스터 제거
        merged_topics = await self._verify_top(merged_topics, post_map)

        # ⑤ 발행 항목 확정 → ⑥ 확정분만 LLM 작문 (탈락 토픽 작문 토큰 절약)
        selected = self._gen.select_topics(merged_topics)
        logger.info(f"발행 항목 확정: {len(merged_topics)}개 토픽 중 {len(selected)}개")
        selected = await self._ai.compose_topics(selected, posts)

        # ⑦ 브리핑 문서 생성 (generate 내부 재선별은 멱등)
        briefing = await self._gen.generate(
            merged_topics=selected,
            period_start=period_start,
            period_end=period_end,
            total_posts_analyzed=len(posts),
        )

        briefing = await self._briefing_repo.save(briefing)

        # 후보 전체(선별 탈락 포함)를 완료 마킹한다.
        # 포함분만 마킹하면 탈락 글이 30일 정리 전까지 매일 재클러스터링(LLM)·재검증을
        # 반복해 토큰이 새고, 후보 풀이 누적 성장해 dedup(O(n²))이 계속 느려진다.
        post_ids = [p.id for p in posts if p.id is not None]
        if post_ids:
            marked = await self._post_repo.mark_briefed(post_ids, datetime.utcnow())
            logger.info(f"브리핑 완료 마킹: {marked}건")

        # 브리핑이 끝난 저중요도 게시물은 즉시 삭제 (30일 정리를 기다리지 않음)
        try:
            purged = await self._post_repo.delete_low_importance(PURGE_MAX_SCORE)
            if purged:
                logger.info(f"저중요도({PURGE_MAX_SCORE} 이하) 브리핑 완료 게시물 삭제: {purged}건")
        except Exception as e:
            logger.warning(f"저중요도 게시물 삭제 실패(무시): {e}")

        logger.info(f"브리핑 생성 완료: '{briefing.title}' ({briefing.total_items}건 항목)")
        return briefing

    async def _verify_top(self, topics: list, post_map: dict) -> list:
        """정규화 점수 0.85+ 클러스터의 구성 게시물만 신뢰도 검증.

        과반이 '반박(contradicted)'인 클러스터는 스캠/허위로 보고 제거한다.
        Product Hunt 등 '만든 결과물' 소스는 검증 대상이 아니므로 제외.
        """
        top = [t for t in topics if (t.importance_score or 0) >= VERIFY_MIN_SCORE]
        if not top:
            return topics

        vposts, seen = [], set()
        for t in top:
            for pid in (t.post_ids or []):
                p = post_map.get(str(pid))  # post_ids가 문자열일 수 있어 str 키 조회
                if p and p.id not in seen and p.source != "producthunt":
                    seen.add(p.id)
                    vposts.append(p)
        if not vposts:
            return topics

        try:
            vres = await self._ai.verify_claims(vposts)
        except Exception as e:
            logger.warning(f"후보 신뢰도 검증 실패(스킵): {e}")
            return topics

        # LLM이 post_id를 숫자/문자열 어느 쪽으로 반환해도 맞도록 str로 통일
        contradicted = {str(r.post_id) for r in vres if r.credibility == "contradicted"}
        if not contradicted:
            return topics

        kept = []
        for t in topics:
            ids = t.post_ids or []
            # 각 post_id를 실제 Post로 풀어 그 .id가 반박 집합에 있는지 본다(타입 통일)
            member_posts = [post_map.get(str(pid)) for pid in ids]
            contra = [p for p in member_posts if p and str(p.id) in contradicted]
            if ids and len(contra) > len(ids) / 2:
                logger.info(
                    f"스캠/허위로 클러스터 제외: {t.headline[:50]} "
                    f"({len(contra)}/{len(ids)} 반박)"
                )
                continue
            kept.append(t)
        return kept
