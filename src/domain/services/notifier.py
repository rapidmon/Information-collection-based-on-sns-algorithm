from __future__ import annotations

from typing import Protocol

from src.domain.entities import Briefing


class Notifier(Protocol):
    """알림/전달 인터페이스."""

    async def send_briefing(self, briefing: Briefing) -> bool:
        """브리핑을 전달 (이메일 등). 성공 여부 반환."""
        ...

    async def send_alert(self, title: str, message: str) -> bool:
        """시스템 알림 전달."""
        ...
