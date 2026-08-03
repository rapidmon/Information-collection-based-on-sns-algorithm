"""브리핑 문서 생성기 구현.

MergedTopic 목록으로부터 Briefing 도메인 엔티티를 생성하고,
HTML/텍스트 형식으로 렌더링한다.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from html import escape as _esc

from src.domain.entities import Briefing, BriefingItem
from src.domain.services.ai_processor import MergedTopic, normalize_topic_bullets
from src.infrastructure.config.settings import BriefingConfig
from src.infrastructure.delivery.categories import CATEGORY_KO, VALID_BRIEFING_CATEGORIES  # 한국어 라벨 단일 소스

# 카테고리 정렬 우선순위
CATEGORY_ORDER = list(VALID_BRIEFING_CATEGORIES)


def trim_items_per_category(
    items: list[BriefingItem],
    default_max: int,
    category_limits: dict[str, int] | None = None,
) -> list[BriefingItem]:
    """수신자별 카테고리 한도로 브리핑 항목을 트리밍.

    저장된 브리핑은 전 수신자 요구치의 슈퍼셋(cap_for)으로 뽑혀 있으므로,
    발송 시 각 수신자 뷰는 여기서 카테고리당 상한만큼 잘라낸다.
    default_max=0은 무제한. category_limits에 있는 카테고리는 그 값이 우선.
    """
    limits = category_limits or {}
    by_cat: dict[str, list[BriefingItem]] = defaultdict(list)
    for it in items:
        by_cat[it.category_name or "Other"].append(it)

    out: list[BriefingItem] = []
    ordered = CATEGORY_ORDER + [c for c in by_cat if c not in CATEGORY_ORDER]
    for cat in ordered:
        cat_items = sorted(
            by_cat.get(cat, []), key=lambda x: x.importance_score or 0, reverse=True
        )
        n = limits.get(cat, default_max)
        if n:
            cat_items = cat_items[:n]
        out.extend(cat_items)
    return out


def _safe_url(url: str) -> str:
    """http/https URL만 허용하고 속성값으로 이스케이프 (javascript: 등 스킴 차단)."""
    url = (url or "").strip()
    if url.lower().startswith(("http://", "https://")):
        return _esc(url, quote=True)
    return "#"


def _importance_to_stars(score: float) -> str:
    if score >= 0.9:
        return "★★★★★"
    if score >= 0.7:
        return "★★★★☆"
    if score >= 0.5:
        return "★★★☆☆"
    if score >= 0.3:
        return "★★☆☆☆"
    return "★☆☆☆☆"


class DefaultBriefingGenerator:
    """브리핑 생성기 기본 구현."""

    def __init__(self, config: BriefingConfig):
        self._config = config

    async def generate(
        self,
        merged_topics: list[MergedTopic],
        period_start: datetime,
        period_end: datetime,
        total_posts_analyzed: int,
    ) -> Briefing:
        merged_topics = self.select_topics(merged_topics)

        # BriefingItem 생성
        items: list[BriefingItem] = []
        for idx, topic in enumerate(merged_topics):
            body_bullets = normalize_topic_bullets(topic.body_bullets)
            body = "\n".join(f"- {b}" for b in body_bullets)
            items.append(
                BriefingItem(
                    headline=topic.headline,
                    body=body,
                    body_bullets=body_bullets,
                    importance_score=topic.importance_score,
                    category_name=topic.primary_category,
                    sort_order=idx,
                    source_count=len(topic.post_ids),
                    sources_summary=", ".join(sorted(set(topic.sources))),
                    source_post_ids=topic.post_ids,
                    source_urls=topic.source_urls or [],
                    tier=getattr(topic, "tier", "minor"),
                    score_features=getattr(topic, "score_features", {}) or {},
                )
            )

        date_str = period_end.strftime("%Y-%m-%d")
        briefing = Briefing(
            title=f"{date_str} 기술 모닝 브리핑",
            period_start=period_start,
            period_end=period_end,
            total_posts_analyzed=total_posts_analyzed,
            total_items=len(items),
            items=items,
        )

        # 텍스트 및 HTML 렌더링
        briefing.content_text = self._render_text(briefing)
        briefing.content_html = self._render_html(briefing)

        return briefing

    # ─── 텍스트 렌더링 ───

    def select_topics(self, topics: list[MergedTopic]) -> list[MergedTopic]:
        """카테고리별 상대 점수로 브리핑 항목을 고른다.

        각 카테고리 안에서 importance_score >= min_importance인 항목만 남기고,
        중요도순으로 카테고리별 상한(cap_for)까지 선택한다. 상한은 수신자
        개인화 한도의 슈퍼셋이라 기본값(max_per_category)보다 클 수 있으며,
        기본 뷰는 발송 시 trim_items_per_category로 다시 잘린다. 유즈케이스가
        이 결과에만 LLM 작문(compose_topics)을 수행하도록 공개 메서드로 노출한다(멱등).
        """
        by_cat: dict[str, list[MergedTopic]] = defaultdict(list)
        for topic in topics:
            if topic.primary_category not in VALID_BRIEFING_CATEGORIES:
                continue
            by_cat[topic.primary_category].append(topic)

        selected: list[MergedTopic] = []
        for cat in CATEGORY_ORDER:
            cat_topics = sorted(
                by_cat.get(cat, []),
                key=lambda t: t.importance_score or 0,
                reverse=True,
            )
            cat_topics = [
                t for t in cat_topics
                if (t.importance_score or 0) >= self._config.min_importance
            ]
            cap = self._config.cap_for(cat)
            if cap:
                cat_topics = cat_topics[:cap]
            selected.extend(cat_topics)

        if self._config.max_items:
            selected = selected[: self._config.max_items]
        return selected

    def render_text(self, briefing: Briefing) -> str:
        """트리밍된 수신자 뷰의 플레인텍스트 대안 렌더 (발송 파이프라인용)."""
        return self._render_text(briefing)

    def _render_text(self, briefing: Briefing) -> str:
        lines: list[str] = []
        lines.append(f"===== {briefing.title} =====")
        lines.append(
            f"기간: {briefing.period_start.strftime('%Y-%m-%d %H:%M')} ~ "
            f"{briefing.period_end.strftime('%Y-%m-%d %H:%M')}"
        )
        lines.append("")

        # 카테고리별 그룹화
        by_cat = self._group_by_category(briefing.items)

        for cat_name in CATEGORY_ORDER:
            cat_items = by_cat.get(cat_name, [])
            if not cat_items:
                continue

            cat_ko = CATEGORY_KO.get(cat_name, cat_name)
            lines.append(f"[{cat_ko}] ({len(cat_items)}건)")
            lines.append("")

            for item in cat_items:
                stars = _importance_to_stars(item.importance_score)
                lines.append(f"**{item.headline}**")
                lines.append(item.body)
                meta = f"중요도: {stars} | 출처: {item.sources_summary}"
                lines.append(meta)
                if item.source_urls:
                    for url in item.source_urls:
                        lines.append(f"  🔗 {url}")
                lines.append("")

            lines.append("---")
            lines.append("")

        if self._config.include_stats:
            lines.append(f"===== 수집 통계 =====")
            lines.append(
                f"분석 게시물: {briefing.total_posts_analyzed}건 | "
                f"브리핑 항목: {briefing.total_items}건"
            )

        return "\n".join(lines)

    # ─── HTML 렌더링 ───

    def _render_html(self, briefing: Briefing) -> str:
        by_cat = self._group_by_category(briefing.items)

        cat_sections = ""
        for cat_name in CATEGORY_ORDER:
            cat_items = by_cat.get(cat_name, [])
            if not cat_items:
                continue

            cat_ko = CATEGORY_KO.get(cat_name, cat_name)
            items_html = ""
            for item in cat_items:
                stars = _importance_to_stars(item.importance_score)
                bullets_html = ""
                for line in item.body.split("\n"):
                    line = line.strip()
                    if line.startswith("- "):
                        line = line[2:]
                    if line:
                        bullets_html += f'<div style="font-size:14px;line-height:1.7;color:#444;margin-bottom:4px;padding-left:16px;text-indent:-8px;">• {_esc(line)}</div>\n'

                links_html = ""
                if item.source_urls:
                    link_items = []
                    for i, url in enumerate(item.source_urls, 1):
                        link_items.append(f'<a href="{_safe_url(url)}" style="color:#4a90d9;text-decoration:none;" target="_blank">[원문 {i}]</a>')
                    links_html = f'<div style="font-size:12px;margin-top:6px;">{" ".join(link_items)}</div>'

                items_html += f"""
                <div style="margin-bottom:20px;padding:16px;background:#f8f9fa;border-radius:6px;border-left:4px solid #4a90d9;">
                    <div style="font-size:16px;font-weight:bold;margin-bottom:10px;color:#1a1a2e;">{_esc(item.headline)}</div>
                    {bullets_html}
                    <div style="font-size:12px;color:#999;margin-top:10px;">중요도: {stars} | 출처: {_esc(item.sources_summary)}</div>
                    {links_html}
                </div>"""

            cat_sections += f"""
            <div style="margin-bottom:28px;">
                <div style="font-size:15px;color:#555;border-bottom:2px solid #e0e0e0;padding-bottom:4px;margin-bottom:14px;font-weight:bold;">[{cat_ko}] ({len(cat_items)}건)</div>
                {items_html}
            </div>"""

        stats_html = ""
        if self._config.include_stats:
            stats_html = f"""
            <div style="background:#f0f0f0;padding:12px;border-radius:6px;font-size:13px;color:#666;margin-top:20px;">
                분석 게시물: {briefing.total_posts_analyzed}건 | 브리핑 항목: {briefing.total_items}건<br>
                생성 시각: {briefing.generated_at.strftime('%Y-%m-%d %H:%M')} KST
            </div>"""

        return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
</head><body style="font-family:-apple-system,'Malgun Gothic','Noto Sans KR',sans-serif;max-width:700px;margin:0 auto;padding:20px;color:#333;">
<div style="background:#1a1a2e;color:white;padding:20px;border-radius:8px;margin-bottom:24px;">
    <h1 style="margin:0;font-size:20px;">{briefing.title}</h1>
    <div style="color:#a0a0c0;font-size:14px;margin-top:4px;">
        {briefing.period_start.strftime('%Y-%m-%d %H:%M')} ~ {briefing.period_end.strftime('%Y-%m-%d %H:%M')}
    </div>
</div>
{cat_sections}
{stats_html}
</body></html>"""

    def _group_by_category(self, items: list[BriefingItem]) -> dict[str, list[BriefingItem]]:
        by_cat: dict[str, list[BriefingItem]] = defaultdict(list)
        for item in items:
            cat = item.category_name or "Other"
            by_cat[cat].append(item)
        # 각 카테고리 내에서 중요도순 정렬
        for cat_items in by_cat.values():
            cat_items.sort(key=lambda x: x.importance_score, reverse=True)
        return by_cat
