from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Post:
    """수집된 게시물 도메인 엔티티."""

    source: str
    external_id: str
    url: str
    author: str
    content_text: str

    id: Optional[int] = None
    author_url: Optional[str] = None
    content_html: Optional[str] = None
    media_urls: list[str] = field(default_factory=list)

    engagement_likes: int = 0
    engagement_reposts: int = 0
    engagement_comments: int = 0
    engagement_views: int = 0

    published_at: Optional[datetime] = None
    collected_at: datetime = field(default_factory=datetime.utcnow)

    # AI 처리 결과
    summary: Optional[str] = None
    importance_score: Optional[float] = None
    language: Optional[str] = None
    is_relevant: Optional[bool] = None
    category_names: list[str] = field(default_factory=list)

    # 중복 처리
    content_hash: Optional[str] = None
    dedup_cluster_id: Optional[int] = None

    raw_data: Optional[dict] = None
