from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class BriefingItem:
    """브리핑 내 개별 항목 (통합된 하나의 토픽)."""

    headline: str
    body: str
    importance_score: float

    id: Optional[int] = None
    briefing_id: Optional[int] = None
    category_name: Optional[str] = None
    sort_order: int = 0
    source_count: int = 0
    sources_summary: str = ""
    source_post_ids: list[int] = field(default_factory=list)


@dataclass
class Briefing:
    """일일 브리핑 문서."""

    title: str
    period_start: datetime
    period_end: datetime

    id: Optional[int] = None
    briefing_type: str = "daily"
    generated_at: datetime = field(default_factory=datetime.utcnow)
    total_posts_analyzed: int = 0
    total_items: int = 0
    content_html: Optional[str] = None
    content_text: Optional[str] = None
    email_sent: bool = False
    email_sent_at: Optional[datetime] = None
    items: list[BriefingItem] = field(default_factory=list)
