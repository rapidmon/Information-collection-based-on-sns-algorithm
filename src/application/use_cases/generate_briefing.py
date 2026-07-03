"""유즈케이스: 브리핑 생성.

새 파이프라인(중요도 재설계):
  ① 클러스터링 — 같은 사건끼리 병합 (LLM)
  ② 티어 판정 — 이벤트별 뉴스가치 절대 등급 major/notable/minor (LLM)
  ③ 중요도 산정 — 객관 신호(고유출처 빈도 + 인게이지먼트 백분위) + 티어 보정,
                  그날 최고점=1.0으로 정규화 (코드, importance_scorer)
  ④ 검증 — 리스트업된 상위(정규화 0.85+) 후보만 웹검색 교차검증, 과반 반박 클러스터 제거
  ⑤ 문서 생성 — 정렬·컷·카테고리 상한 적용

중요도를 개별 게시물 배치에서 매기던 방식(주관·배치 상대성)을 걷어내고,
클러스터(이벤트) 단위 객관 신호로 산정한다.
"""

from __future__ import annotations

import logging
from datetime import datetime

from src.domain.entities import Briefing
from src.domain.repositories.briefing_repository import BriefingRepository
from src.domain.repositories.post_repository import PostRepository
from src.domain.services.ai_processor import AIProcessor
from src.domain.services.briefing_generator import BriefingGenerator
from src.infrastructure.ai.importance_scorer import score_topics
from src.infrastructure.config.settings import ScoringConfig

logger = logging.getLogger(__name__)

# 리스트업된 항목 중 이 정규화 점수 이상만 신뢰도 검증(웹검색)
VERIFY_MIN_SCORE = 0.85


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

        # ① 클러스터링 (같은 사건 병합)
        merged_topics = await self._ai.deduplicate_and_merge(posts)
        if not merged_topics:
            logger.warning("클러스터링 결과 없음")
            merged_topics = []

        # ② LLM 티어(절대 등급) 판정 — 사용자 피드백(과대/과소)을 few-shot 보정으로 주입
        calibration = None
        if self._feedback_repo is not None:
            try:
                calibration = self._feedback_repo.get_examples(limit=40)
                if calibration:
                    logger.info(f"티어 판정에 사용자 보정 예시 {len(calibration)}건 적용")
            except Exception as e:
                logger.warning(f"피드백 예시 조회 실패(무시): {e}")
        tiers = await self._ai.judge_tiers(merged_topics, calibration_examples=calibration)
        if len(tiers) != len(merged_topics):
            logger.warning(
                f"티어 개수 불일치 (topics={len(merged_topics)}, tiers={len(tiers)}) — "
                f"부족분은 minor로 남는다"
            )
        for t, tier in zip(merged_topics, tiers):
            t.tier = tier

        # ③ 객관 신호 + 티어 결합 중요도 (정규화 max=1.0), 피처 스냅샷 저장
        score_topics(merged_topics, post_map, posts, self._scoring)

        # ④ 상위(0.85+) 후보만 검증 → 과반 반박 클러스터 제거
        merged_topics = await self._verify_top(merged_topics, post_map)

        # ⑤ 브리핑 문서 생성 (정렬·최소점수·카테고리 상한은 generator가 처리)
        briefing = await self._gen.generate(
            merged_topics=merged_topics,
            period_start=period_start,
            period_end=period_end,
            total_posts_analyzed=len(posts),
        )

        briefing = await self._briefing_repo.save(briefing)

        # 포함 후보 게시물 브리핑 완료 마킹 (재등장·중복 브리핑 방지)
        post_ids = [p.id or p.external_id for p in posts if p.id or p.external_id]
        if post_ids:
            marked = await self._post_repo.mark_briefed(post_ids, datetime.utcnow())
            logger.info(f"브리핑 완료 마킹: {marked}건")

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

        contradicted = {r.post_id for r in vres if r.credibility == "contradicted"}
        if not contradicted:
            return topics

        kept = []
        for t in topics:
            ids = t.post_ids or []
            # 각 post_id를 실제 Post로 풀어 그 .id가 반박 집합에 있는지 본다(타입 통일)
            member_posts = [post_map.get(str(pid)) for pid in ids]
            contra = [p for p in member_posts if p and p.id in contradicted]
            if ids and len(contra) > len(ids) / 2:
                logger.info(
                    f"스캠/허위로 클러스터 제외: {t.headline[:50]} "
                    f"({len(contra)}/{len(ids)} 반박)"
                )
                continue
            kept.append(t)
        return kept
