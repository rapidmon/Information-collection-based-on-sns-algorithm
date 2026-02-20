from __future__ import annotations

from typing import Protocol

from src.domain.entities import Post


class Collector(Protocol):
    """SNS 데이터 수집기 인터페이스.

    각 플랫폼(X, Threads, LinkedIn, DCInside) 수집기가 이 인터페이스를 구현한다.
    """

    @property
    def source_name(self) -> str:
        """수집기의 소스 이름 (twitter, threads, linkedin, dcinside)."""
        ...

    async def collect(self) -> list[Post]:
        """한 번의 수집 사이클을 실행하여 새 게시물 목록을 반환."""
        ...

    async def is_session_valid(self) -> bool:
        """로그인 세션이 유효한지 확인. HTTP 기반 수집기는 항상 True."""
        ...
