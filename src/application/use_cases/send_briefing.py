"""유즈케이스: 브리핑 전달 (이메일 등)."""

from __future__ import annotations

import logging
from datetime import datetime

from src.domain.entities import Briefing
from src.domain.repositories.briefing_repository import BriefingRepository
from src.domain.services.notifier import Notifier

logger = logging.getLogger(__name__)


class SendBriefingUseCase:
    def __init__(self, briefing_repo: BriefingRepository, notifier: Notifier):
        self._briefing_repo = briefing_repo
        self._notifier = notifier

    async def execute(self, briefing: Briefing) -> bool:
        """브리핑을 이메일로 전달하고 전송 상태를 업데이트."""
        success = await self._notifier.send_briefing(briefing)

        if success:
            briefing.email_sent = True
            briefing.email_sent_at = datetime.utcnow()
            await self._briefing_repo.update(briefing)
            logger.info(f"브리핑 이메일 전송 완료: '{briefing.title}'")
        else:
            logger.error(f"브리핑 이메일 전송 실패: '{briefing.title}'")

        return success
