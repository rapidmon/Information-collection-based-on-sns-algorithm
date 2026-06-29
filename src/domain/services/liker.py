from __future__ import annotations

from typing import Protocol

from src.domain.entities import Post


class PostLiker(Protocol):
    """게시물 자동 좋아요 인터페이스.

    AI 처리로 선별된 게시물에 좋아요를 누른다(설정에 따라 dry-run).
    구현체는 플랫폼별 방식(브라우저 등)을 캡슐화한다.
    """

    async def like_posts(self, source: str, posts: list[Post]) -> list[str]:
        """source 플랫폼의 게시물들에 좋아요. 처리된 post.id 목록을 반환."""
        ...
