"""유즈케이스: 브리핑 생성.

파이프라인:
  ① 게시물 점수 산정 — 분류 완료 게시물을 카테고리별 상대평가
  ② 클러스터링 — 토큰 유사도 후보군을 결정적으로 병합 (LLM 미사용)
  ③ 사건 점수 집계 — 사전 계산된 게시물 점수를 사건 단위로 집계
  ③.5 기브리핑 dedup — 카테고리별 상위 후보를 청크 단위 LLM 판정으로 기다룬 사건 제거
  ④ 검증 — 리스트업된 상위(정규화 0.85+) 후보만 웹검색 교차검증, 과반 반박 클러스터 제거
  ⑤ 선별 — 점수 하한·카테고리 상한으로 발행 항목 확정
  ⑤.5 최종 가드 — 확정분(소규모)끼리 동일 사건 병합 + 기브리핑 사건 재확인
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
from src.infrastructure.ai.importance_scorer import (
    renormalize_topics_by_category,
    score_posts_by_category,
    score_topics,
)
from src.infrastructure.config.settings import ScoringConfig

logger = logging.getLogger(__name__)

# 리스트업된 항목 중 이 정규화 점수 이상만 신뢰도 검증(웹검색)
VERIFY_MIN_SCORE = 0.85

# 브리핑 완료 후 이 AI 중요도 이하 게시물은 즉시 삭제 (재사용처 없음)
PURGE_MAX_SCORE = 0.6

# 스캠/허위 웹검증 제외 소스 — producthunt(만든 결과물), news(공신력 매체 보도)
NON_VERIFIED_SOURCES = {"producthunt", "news"}

# 기브리핑 사건 dedup 시 비교할 최근 브리핑 수
RECENT_BRIEFINGS_FOR_DEDUP = 3

# 기브리핑 사건 dedup의 카테고리별 판정 대상 상한.
# 발행권(max_per_category=6)에 재정규화 승격 여지를 더한 값 — 발행 가능성이 없는
# 하위 후보까지 판정에 넣으면 호출 수만 늘고 recall은 떨어진다.
DEDUP_TOP_PER_CATEGORY = 15


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

        # ③.5 최근 브리핑에서 이미 다룬 사건 제거 — 검증·작문 토큰을 쓰기 전에 컷.
        #     (게시물은 매일 새로 유입돼 사건이 여러 날 반복 발행되는 것을 막는다)
        merged_topics = await self._drop_already_covered(merged_topics)

        # ④ 상위(0.85+) 후보만 검증 → 과반 반박 클러스터 제거
        merged_topics = await self._verify_top(merged_topics, post_map)

        # ⑤ 발행 항목 확정 → ⑤.5 확정분 최종 중복 가드 → ⑥ 확정분만 LLM 작문
        selected = self._gen.select_topics(merged_topics)
        logger.info(f"발행 항목 확정: {len(merged_topics)}개 토픽 중 {len(selected)}개")
        selected = await self._final_dedup_guard(selected)
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

    async def _drop_already_covered(self, topics: list) -> list:
        """최근 브리핑(RECENT_BRIEFINGS_FOR_DEDUP회)에서 이미 다룬 사건을 제거.

        판정 대상은 카테고리별 상위 DEDUP_TOP_PER_CATEGORY개(발행권 근처)로
        좁힌다 — 발행 불가능한 하위 후보까지 넣으면 판정 recall만 떨어진다.
        새로운 전개(후속 수치·결과)가 있는 토픽은 LLM 판정이 유지한다.
        조회·판정 실패 시 dedup을 건너뛴다 (중복 발행이 잘못 삭제보다 낫다).
        """
        if not topics:
            return topics
        try:
            recent = await self._briefing_repo.get_all(limit=RECENT_BRIEFINGS_FOR_DEDUP)
        except Exception as e:
            logger.warning(f"최근 브리핑 조회 실패 — 기브리핑 사건 dedup 스킵: {e}")
            return topics

        recent_items = [
            f"[{it.category_name}] {it.headline}"
            for b in recent
            for it in (b.items or [])
        ]
        if not recent_items:
            return topics

        # 카테고리별 점수 상위만 판정 대상으로 추린다 (전역 인덱스 유지)
        by_cat: dict[str, list[int]] = {}
        for i, t in enumerate(topics):
            by_cat.setdefault(t.primary_category, []).append(i)
        judge_indexes: list[int] = []
        for idxs in by_cat.values():
            idxs.sort(key=lambda i: topics[i].importance_score or 0, reverse=True)
            judge_indexes.extend(idxs[:DEDUP_TOP_PER_CATEGORY])
        judge_indexes.sort()
        candidates = [topics[i] for i in judge_indexes]

        try:
            dup_local = await self._ai.find_covered_topics(candidates, recent_items)
        except Exception as e:
            logger.warning(f"기브리핑 사건 판정 실패 — dedup 스킵: {e}")
            return topics
        dup_indexes = {
            judge_indexes[j] for j in set(dup_local) if 0 <= j < len(judge_indexes)
        }
        if not dup_indexes:
            return topics

        kept = []
        removed_categories: set[str] = set()
        for i, t in enumerate(topics):
            if i in dup_indexes:
                logger.info(f"기브리핑 사건 제외: {t.headline[:60]}")
                removed_categories.add(t.primary_category)
            else:
                kept.append(t)
        logger.info(f"기브리핑 사건 dedup: {len(topics)}개 중 {len(dup_indexes)}개 제외")

        # 제거가 발생한 카테고리는 생존 토픽 기준으로 재정규화 — 사라진 1위(기준점)
        # 점수에 눌려 오늘의 신선한 뉴스까지 하한(0.85) 탈락하는 것을 방지
        renormalize_topics_by_category(kept, only_categories=removed_categories)
        return kept

    async def _final_dedup_guard(self, selected: list) -> list:
        """발행 확정분에 대한 마지막 중복 방어선.

        앞 단계(클러스터링·③.5 dedup)는 수백 개 후보를 다뤄 놓치는 사건이
        생긴다. 확정분은 소규모라 같은 LLM 판정도 훨씬 정확하므로 여기서
        ⑴ 카테고리가 갈려 중복 선정된 같은 사건을 병합하고
        ⑵ 최근 브리핑에서 이미 다룬 사건을 한 번 더 걸러낸다.
        각 단계는 실패 시 스킵한다 (중복 발행이 잘못 삭제보다 낫다).
        """
        if not selected:
            return selected

        if len(selected) >= 2:
            try:
                merged = await self._ai.consolidate_topics(selected)
                if merged and len(merged) < len(selected):
                    logger.info(
                        f"발행 확정분 동일 사건 병합: {len(selected)}개 → {len(merged)}개"
                    )
                if merged:
                    selected = merged
            except Exception as e:
                logger.warning(f"발행 확정분 병합 실패(스킵): {e}")

        return await self._drop_already_covered(selected)

    async def _verify_top(self, topics: list, post_map: dict) -> list:
        """정규화 점수 0.85+ 클러스터의 구성 게시물만 신뢰도 검증.

        과반이 '반박(contradicted)'인 클러스터는 스캠/허위로 보고 제거한다.
        Product Hunt('만든 결과물')·news(공신력 매체 보도) 소스는 검증 대상이 아니므로 제외.
        """
        top = [t for t in topics if (t.importance_score or 0) >= VERIFY_MIN_SCORE]
        if not top:
            return topics

        vposts, seen = [], set()
        for t in top:
            for pid in (t.post_ids or []):
                p = post_map.get(str(pid))  # post_ids가 문자열일 수 있어 str 키 조회
                if p and p.id not in seen and p.source not in NON_VERIFIED_SOURCES:
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
