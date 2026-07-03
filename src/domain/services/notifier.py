from __future__ import annotations

from typing import Protocol


class Notifier(Protocol):
    """알림/전달 인터페이스."""

    async def send_html(
        self,
        subject: str,
        html: str,
        text: str | None,
        to_addresses: list[str],
        logo_path: str | None = None,
    ) -> bool:
        """지정 수신자에게 HTML 메일 발송(로고 CID 임베드 지원)."""
        ...

    async def send_alert(self, title: str, message: str) -> bool:
        """시스템 알림 전달."""
        ...
