"""브리핑 문서 생성기 구현.

MergedTopic 목록으로부터 Briefing 도메인 엔티티를 생성하고,
HTML/텍스트 형식으로 렌더링한다.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from src.domain.entities import Briefing, BriefingItem
from src.domain.services.ai_processor import MergedTopic
from src.infrastructure.config.settings import BriefingConfig

# 카테고리 한국어 매핑
CATEGORY_KO = {
    "AI": "AI",
    "Semiconductor": "반도체",
    "Cloud": "클라우드",
    "Startup": "스타트업",
    "BigTech": "빅테크",
    "Regulation": "규제/정책",
    "Other": "기타",
}

# 카테고리 정렬 우선순위
CATEGORY_ORDER = ["AI", "Semiconductor", "Cloud", "BigTech", "Startup", "Regulation", "Other"]


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
        # 중요도 기준 정렬 후 최대 항목 수 제한
        merged_topics.sort(key=lambda t: t.importance_score, reverse=True)
        if self._config.max_items:
            merged_topics = merged_topics[: self._config.max_items]

        # BriefingItem 생성
        items: list[BriefingItem] = []
        for idx, topic in enumerate(merged_topics):
            body = "\n".join(f"- {b}" for b in topic.body_bullets)
            items.append(
                BriefingItem(
                    headline=topic.headline,
                    body=body,
                    importance_score=topic.importance_score,
                    category_name=topic.primary_category,
                    sort_order=idx,
                    source_count=len(topic.post_ids),
                    sources_summary=", ".join(sorted(set(topic.sources))),
                    source_post_ids=topic.post_ids,
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
                lines.append(f"중요도: {stars} | 출처: {item.sources_summary}")
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
                        bullets_html += f'<div style="font-size:14px;line-height:1.7;color:#444;margin-bottom:4px;padding-left:16px;text-indent:-8px;">• {line}</div>\n'

                items_html += f"""
                <div style="margin-bottom:20px;padding:16px;background:#f8f9fa;border-radius:6px;border-left:4px solid #4a90d9;">
                    <div style="font-size:16px;font-weight:bold;margin-bottom:10px;color:#1a1a2e;">{item.headline}</div>
                    {bullets_html}
                    <div style="font-size:12px;color:#999;margin-top:10px;">중요도: {stars} | 출처: {item.sources_summary}</div>
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
