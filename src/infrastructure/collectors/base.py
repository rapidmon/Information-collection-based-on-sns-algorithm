"""수집기 공통 베이스 클래스."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from src.domain.entities import Post

logger = logging.getLogger(__name__)


class BaseCollector(ABC):
    """모든 수집기의 공통 베이스.

    도메인 Protocol(Collector)의 계약을 이행하면서,
    공통 로직(로깅, 기본 세션 검증 등)을 제공한다.
    """

    @property
    @abstractmethod
    def source_name(self) -> str: ...

    @abstractmethod
    async def collect(self) -> list[Post]: ...

    async def is_session_valid(self) -> bool:
        """기본값: 항상 유효. 브라우저 기반 수집기에서 오버라이드."""
        return True
