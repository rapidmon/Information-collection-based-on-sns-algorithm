from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from src.domain.entities import Post


class PostRepository(Protocol):
    """게시물 저장소 인터페이스 (의존성 역전).

    구현(SQLite)은 가벼운 조회/쓰기를 '동기'로, to_thread로 감싼 무거운 쿼리를
    '비동기'로 제공한다. Protocol도 그 실제 sync/async를 그대로 반영한다
    (전부 async로 선언하면 호출부가 await를 안 하는 실제 코드와 어긋난다).
    """

    # ── 동기: 가벼운 조회/쓰기 ──
    def save(self, post: Post) -> str:
        """게시물 저장. 저장된 id 반환."""
        ...

    def save_many(self, posts: list[Post]) -> int:
        """여러 게시물 일괄 저장. 저장된 건수 반환."""
        ...

    def update_many(self, posts: list[Post]) -> int:
        """여러 게시물 일괄 업데이트 (AI 처리 결과 등)."""
        ...

    def get_unprocessed(self, limit: int = 100) -> list[Post]:
        """AI 처리 안 된 게시물 조회 (summary가 None)."""
        ...

    def find_by_id(self, post_id: str) -> Post | None:
        """id(external_id)로 조회."""
        ...

    def find_by_external_id(self, external_id: str) -> Post | None:
        """external_id로 조회."""
        ...

    def find_recent(self, limit: int = 100) -> list[Post]:
        """최근 게시물 조회."""
        ...

    def find_by_source(self, source: str, limit: int = 100) -> list[Post]:
        """소스별 최근 게시물 조회."""
        ...

    def delete(self, post_id: str) -> None:
        """게시물 삭제."""
        ...

    def delete_older_than(self, days: int) -> int:
        """N일 이상 된 게시물 삭제. 삭제 건수 반환."""
        ...

    def count(self) -> int:
        """전체 게시물 수."""
        ...

    def get_storage_info(self) -> dict[str, Any]:
        """저장소 용량/건수 정보."""
        ...

    def get_likeable(self, source: str, min_importance: float, limit: int) -> list[Post]:
        """자동 좋아요 대상: 관련 O + 중요도 임계값 이상 + 미좋아요 게시물."""
        ...

    def mark_liked(self, post_ids: list[str], liked_at: datetime) -> int:
        """게시물들의 liked_at 설정 (자동 좋아요 완료 마킹)."""
        ...

    # ── 비동기: to_thread로 감싼 무거운 쿼리 ──
    async def search(
        self,
        query: str | None = None,
        source: str | None = None,
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Post]:
        """조건에 맞는 게시물 검색."""
        ...

    async def count_by_source(self, start: datetime, end: datetime) -> dict[str, int]:
        """기간별 소스별 게시물 수 집계."""
        ...

    async def get_by_period(
        self, start: datetime, end: datetime, relevant_only: bool = True
    ) -> list[Post]:
        """기간 내 게시물 조회."""
        ...

    async def get_top_keywords(self, limit: int = 20, days: int = 2) -> list[dict]:
        """최근 N일 키워드 빈도 top K."""
        ...

    async def get_unbriefed(self, limit: int = 500) -> list[Post]:
        """브리핑에 포함되지 않은 관련 게시물 조회."""
        ...

    async def mark_briefed(self, post_ids: list[str], briefed_at: datetime) -> int:
        """게시물들의 briefed_at 설정 (브리핑 완료 마킹)."""
        ...
