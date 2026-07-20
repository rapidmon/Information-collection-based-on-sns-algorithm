"""브리핑 중요도 스코어러.

카테고리 분류가 끝난 게시물 전체를 먼저 카테고리별로 상대평가한다.
병합된 사건은 새 점수를 다시 만들지 않고, 구성 게시물의 사전 계산 점수를
집계해서 대표 카테고리 하나로만 최종 노출한다.
"""

from __future__ import annotations

import bisect
import logging

from src.domain.services.ai_processor import MergedTopic
from src.infrastructure.config.settings import ScoringConfig
from src.infrastructure.delivery.categories import VALID_BRIEFING_CATEGORIES

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


def score_posts_by_category(baseline_posts: list, cfg: ScoringConfig) -> dict[str, dict[str, float]]:
    """분류 완료 게시물을 카테고리별 상대 점수로 변환한다.

    반환값은 {post_id: {category: normalized_score}} 형태다. 이 점수는 병합 전에
    계산되며, 병합 후에는 사건 점수 산정의 입력으로만 사용한다.
    """
    valid_categories = set(VALID_BRIEFING_CATEGORIES)
    engagement_weight = max(0.0, min(float(cfg.engagement_weight), 1.0))
    ai_weight = 1.0 - engagement_weight
    pmaps = _percentile_baselines(baseline_posts, cfg)

    raw_by_post: dict[str, dict[str, float]] = {}
    raws_by_category: dict[str, list[float]] = {}

    for post in baseline_posts:
        if post.id is None:
            continue
        categories = [cat for cat in post.category_names or [] if cat in valid_categories]
        if not categories:
            continue

        ai_score = float(post.importance_score if post.importance_score is not None else 0.5)
        eng_pct = _percentile(pmaps.get(post.source, []), _engagement_raw(post, cfg))
        raw = ai_weight * ai_score + engagement_weight * eng_pct

        pid = str(post.id)
        raw_by_post[pid] = {}
        for cat in categories:
            raw_by_post[pid][cat] = raw
            raws_by_category.setdefault(cat, []).append(raw)

    scores_by_post: dict[str, dict[str, float]] = {}
    for pid, cat_raws in raw_by_post.items():
        scores_by_post[pid] = {}
        for cat, raw in cat_raws.items():
            mx = max(raws_by_category.get(cat, [1.0]))
            if mx <= 0:
                mx = 1.0
            scores_by_post[pid][cat] = round(raw / mx, 4)

    return scores_by_post


def renormalize_topics_by_category(
    topics: list[MergedTopic], only_categories: set[str] | None = None
) -> None:
    """생존 토픽 기준으로 카테고리 최고점을 1.0으로 재정규화한다 (in-place).

    기브리핑 사건 dedup이 카테고리 기준점(1위 사건)을 제거하면, 남은 토픽들이
    사라진 1위 기준의 점수를 든 채 하한(min_importance)에 일괄 탈락할 수 있다.
    제거가 발생한 카테고리(only_categories)만 오늘 생존분 기준의 상대평가로
    되돌린다 — 나머지 카테고리의 선별 기준은 건드리지 않는다.
    """
    max_by_cat: dict[str, float] = {}
    for t in topics:
        if only_categories is not None and t.primary_category not in only_categories:
            continue
        score = t.importance_score or 0.0
        max_by_cat[t.primary_category] = max(max_by_cat.get(t.primary_category, 0.0), score)

    for t in topics:
        mx = max_by_cat.get(t.primary_category, 0.0)
        if mx <= 0:
            continue
        t.importance_score = round((t.importance_score or 0.0) / mx, 4)
        if isinstance(t.score_features, dict):
            t.score_features["final"] = t.importance_score
            t.score_features["renormalized_after_dedup"] = True


def score_topics(
    topics: list[MergedTopic],
    post_map: dict,
    baseline_posts: list,
    cfg: ScoringConfig,
    post_category_scores: dict[str, dict[str, float]] | None = None,
) -> None:
    """토픽별 중요도 점수 + 피처 스냅샷을 in-place로 설정한다.

    - post_map: {post.id: Post} — 토픽 구성 게시물 조회용
    - baseline_posts: 병합 전 카테고리별 상대평가 모집단
    - post_category_scores: score_posts_by_category() 결과

    병합된 토픽은 사전 계산된 게시물 카테고리 점수의 top-3 평균만 사용한다.
    한 토픽은 가장 높은 사건 점수의 대표 카테고리 하나로만 노출한다.
    """
    if not topics:
        return

    if post_category_scores is None:
        post_category_scores = score_posts_by_category(baseline_posts, cfg)
    valid_categories = set(VALID_BRIEFING_CATEGORIES)
    engagement_weight = max(0.0, min(float(cfg.engagement_weight), 1.0))
    ai_weight = 1.0 - engagement_weight

    for t in topics:
        members = [post_map[str(pid)] for pid in (t.post_ids or []) if str(pid) in post_map]
        category_scores: dict[str, list[float]] = {}

        for m in members:
            for cat, score in (post_category_scores.get(str(m.id), {}) or {}).items():
                category_scores.setdefault(cat, []).append(score)

        if not category_scores and t.primary_category in valid_categories:
            category_scores[t.primary_category] = [float(t.importance_score or 0.5)]

        topic_category_raws: dict[str, float] = {}
        for cat, scores in category_scores.items():
            top_scores = sorted(scores, reverse=True)[:3]
            raw = sum(top_scores) / len(top_scores)
            topic_category_raws[cat] = raw

        t.score_features = {
            "axis": "category_relative",
            "method": "premerge_post_category_score_top3_avg",
            "ai_weight": round(ai_weight, 3),
            "engagement_weight": round(engagement_weight, 3),
            "category_event_scores": {
                cat: round(raw, 4)
                for cat, raw in sorted(topic_category_raws.items())
            },
        }

    for t in topics:
        category_scores = t.score_features.get("category_event_scores") or {}

        if not category_scores:
            t.primary_category = "Other"
            t.importance_score = 0.0
            t.score_features["category_scores"] = {}
            t.score_features["final"] = 0.0
            continue

        selected_cat, final = max(
            category_scores.items(),
            key=lambda item: (item[1], -VALID_BRIEFING_CATEGORIES.index(item[0])),
        )
        t.primary_category = selected_cat
        t.importance_score = final
        t.score_features["category"] = selected_cat
        t.score_features["category_scores"] = category_scores
        t.score_features["raw"] = category_scores[selected_cat]
        t.score_features["final"] = final

    logger.info(
        f"중요도 산정 완료: {len(topics)}개 토픽 (카테고리별 정규화 max=1.0, "
        f"engagement_weight={engagement_weight})"
    )
