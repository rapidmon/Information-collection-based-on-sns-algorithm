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
    keywords: list[str] | None = None


@dataclass
class VerificationResult:
    post_id: int
    credibility: str  # "verified" | "unverified" | "contradicted"
    reason: str | None = None


@dataclass
class MergedTopic:
    post_ids: list[int]
    headline: str
    body_bullets: list[str]
    primary_category: str
    importance_score: float
    sources: list[str]
    source_urls: list[str] = None
    tier: str = "minor"          # LLM 절대 등급: major/notable/minor (중요도 보정용)
    score_features: dict = None  # 채점 근거 스냅샷(빈도·인게이지먼트·티어·부분점수)

    def __post_init__(self):
        if self.source_urls is None:
            self.source_urls = []
        if self.score_features is None:
            self.score_features = {}


@dataclass
class CategoryCuration:
    """카테고리별 큐레이션 (B-2: 후크 + 핵심 + 시사점)."""
    hook: str
    bullets: list[str]
    insight: str


@dataclass
class Curation:
    """독자층별 큐레이션. 전체(에디토리얼 리드 + 킥) + 카테고리별."""
    title: str
    paragraphs: list[str]
    kick: str
    categories: dict[str, CategoryCuration]


class AIProcessor(Protocol):
    """AI 처리 파이프라인 인터페이스."""

    async def filter_and_summarize(self, posts: list[Post]) -> list[FilterResult]:
        """관련성 필터링 + 요약 (배치)."""
        ...

    async def categorize(self, posts: list[Post]) -> list[CategoryResult]:
        """카테고리 분류 + 중요도 점수 (배치)."""
        ...

    async def verify_claims(self, posts: list[Post]) -> list[VerificationResult]:
        """게시물의 핵심 주장을 웹 검색으로 교차 검증."""
        ...

    async def judge_tiers(
        self, topics: list["MergedTopic"], calibration_examples: list | None = None
    ) -> list[str]:
        """클러스터(이벤트)별 뉴스가치 절대 등급(major/notable/minor) 판정.

        calibration_examples: 사용자 과대/과소 피드백(few-shot 보정용, 선택).
        """
        ...

    async def deduplicate_and_merge(self, posts: list[Post]) -> list[MergedTopic]:
        """중복 제거 및 유사 토픽 통합 브리핑 항목 생성."""
        ...

    async def generate_curation(
        self, topics: list[MergedTopic], audience: str
    ) -> Curation:
        """독자층 맞춤 큐레이션 생성 (전체 리드 + 킥 + 카테고리별)."""
        ...
