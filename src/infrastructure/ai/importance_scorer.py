"""브리핑 중요도 스코어러 (객관 신호 기반).

클러스터링(같은 사건 병합)이 끝난 토픽에 대해, **주관적 LLM 자유 점수 대신**
아래 객관 신호와 LLM 티어(등급) 보정을 결합해 중요도를 산정한다.

  최종 raw = freq_weight × 고유출처수
           + engagement_weight × 인게이지먼트_백분위(플랫폼별)
           + tier_bonus[tier]
  → 그날 최고점이 1.0이 되도록 정규화.

각 토픽에 `importance_score`(정규화값)와 `score_features`(채점 근거 스냅샷)를
채워 넣는다. 스냅샷은 나중에 사용자 피드백(적절/과대/과소)과 짝지어 가중치를
학습·보정하는 데 쓰인다.
"""

from __future__ import annotations

import bisect
import logging

from src.domain.services.ai_processor import MergedTopic
from src.infrastructure.config.settings import ScoringConfig

logger = logging.getLogger(__name__)


def _engagement_raw(post, cfg: ScoringConfig) -> float:
    """게시물 인게이지먼트 원점수 (좋아요·리포스트·댓글 가중합)."""
    likes = post.engagement_likes or 0
    reposts = post.engagement_reposts or 0
    comments = post.engagement_comments or 0
    return likes * cfg.w_likes + reposts * cfg.w_reposts + comments * cfg.w_comments


def _percentile_baselines(posts, cfg: ScoringConfig) -> dict[str, list[float]]:
    """플랫폼별 인게이지먼트 원점수 정렬 리스트 (백분위 계산용)."""
    by_src: dict[str, list[float]] = {}
    for p in posts:
        by_src.setdefault(p.source, []).append(_engagement_raw(p, cfg))
    for src in by_src:
        by_src[src].sort()
    return by_src


def _percentile(sorted_vals: list[float], v: float) -> float:
    """정렬된 값들 중 v보다 '엄격히 작은' 값의 비율 (0.0~1.0).

    bisect_left를 써서, 값이 모두 같거나(스크랩 글은 인게이지먼트 0) 표본이
    1개뿐인 경우 1.0으로 뻥튀기되지 않고 0.0(신호 없음)으로 수렴하게 한다.
    """
    if not sorted_vals:
        return 0.0
    idx = bisect.bisect_left(sorted_vals, v)
    return idx / len(sorted_vals)


def score_topics(
    topics: list[MergedTopic],
    post_map: dict,
    baseline_posts: list,
    cfg: ScoringConfig,
) -> None:
    """토픽별 중요도 점수 + 피처 스냅샷을 계산해 in-place로 설정한다.

    - topics: 클러스터링·티어 판정이 끝난 토픽들 (t.tier 설정되어 있어야 함)
    - post_map: {post.id: Post} — 토픽 구성 게시물 조회용
    - baseline_posts: 인게이지먼트 백분위 산정 모집단 (그날 후보 게시물 전체)
    """
    if not topics:
        return

    pmaps = _percentile_baselines(baseline_posts, cfg)
    raws: list[float] = []

    for t in topics:
        # post_ids가 LLM JSON에서 문자열로 올 수 있어 str 키로 통일 조회
        members = [post_map[str(pid)] for pid in (t.post_ids or []) if str(pid) in post_map]

        # ① 빈도 = 고유 출처(계정) 수 (여러 계정이 다룰수록 화제).
        #    author가 있으면 그 계정으로 묶어(1인 다(多)글=1) 도배를 방지하고,
        #    author를 못 긁는 소스(DCInside 등)는 각 글을 별개 화자로 본다(계정URL→글ID).
        voices = {(m.source, (m.author or m.author_url or m.id)) for m in members}
        freq = len(voices) if voices else max(1, len(t.post_ids or []))

        # ② 인게이지먼트 = 구성 게시물 중 '플랫폼별 백분위' 최고값 (플랫폼 스케일 차이 보정)
        eng = 0.0
        for m in members:
            pct = _percentile(pmaps.get(m.source, []), _engagement_raw(m, cfg))
            eng = max(eng, pct)

        # ③ 카테고리별 점수 축 분기 (뉴스가치 vs 흥미로운 결과물).
        #    - 뉴스 카테고리: LLM 절대 등급(tier)으로 보정 → 여러 출처에 반복될수록↑.
        #    - Showcase 등(category_base 지정): 뉴스 티어가 무의미(1인 결과물 자랑)하므로
        #      티어 대신 카테고리 기본 가중을 주고 인게이지먼트로 변별한다.
        cat = t.primary_category or ""
        tier = (t.tier or "minor").lower()
        if cat in cfg.category_base:
            base_comp = cfg.category_base[cat]  # 티어 대체(카테고리 축)
            axis = f"category:{cat}"
        else:
            base_comp = cfg.tier_bonus.get(tier, 0.0)  # 뉴스 티어 축
            axis = "tier"

        freq_comp = cfg.freq_weight * freq
        eng_comp = cfg.engagement_weight * eng
        raw = freq_comp + eng_comp + base_comp

        t.score_features = {
            "frequency": freq,
            "engagement_pct": round(eng, 3),
            "tier": tier,
            "axis": axis,
            "freq_comp": round(freq_comp, 4),
            "eng_comp": round(eng_comp, 4),
            "tier_comp": round(base_comp, 4),
            "raw": round(raw, 4),
        }
        raws.append(raw)

    # 정규화: 그날 최고점 = 1.0
    mx = max(raws) if raws else 1.0
    if mx <= 0:
        mx = 1.0
    for t in topics:
        final = round((t.score_features.get("raw", 0.0)) / mx, 4)
        t.importance_score = final
        t.score_features["final"] = final

    logger.info(
        f"중요도 산정 완료: {len(topics)}개 토픽 (정규화 max=1.0, "
        f"major={sum(1 for t in topics if (t.tier or '').lower() == 'major')}건)"
    )
