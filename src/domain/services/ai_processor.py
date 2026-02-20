from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.domain.entities import Post


@dataclass
class FilterResult:
    post_id: int
    is_relevant: bool
    summary: str | None = None
    language: str | None = None


@dataclass
class CategoryResult:
    post_id: int
    categories: list[str]
    importance_score: float


@dataclass
class MergedTopic:
    post_ids: list[int]
    headline: str
    body_bullets: list[str]
    primary_category: str
    importance_score: float
    sources: list[str]


class AIProcessor(Protocol):
    """AI 처리 파이프라인 인터페이스."""

    async def filter_and_summarize(self, posts: list[Post]) -> list[FilterResult]:
        """관련성 필터링 + 요약 (배치)."""
        ...

    async def categorize(self, posts: list[Post]) -> list[CategoryResult]:
        """카테고리 분류 + 중요도 점수 (배치)."""
        ...

    async def deduplicate_and_merge(self, posts: list[Post]) -> list[MergedTopic]:
        """중복 제거 및 유사 토픽 통합 브리핑 항목 생성."""
        ...
