"""이메일 발송기 구현.

도메인 Notifier 인터페이스를 구현. aiosmtplib 기반 비동기 SMTP.
"""

from __future__ import annotations

import logging
import os
import time
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from src.domain.entities import Briefing
from src.infrastructure.config.settings import EmailConfig, Settings

logger = logging.getLogger(__name__)

ALERT_COOLDOWN_SECONDS = 3600


class EmailNotifier:
    """SMTP 이메일 발송 Notifier 구현."""

    def __init__(self, settings: Settings, email_config: EmailConfig):
        self._host = settings.smtp_host
        self._port = settings.smtp_port
        self._user = settings.smtp_user
        self._password = settings.smtp_password
        self._from = settings.email_from
        self._to_addresses = email_config.to_addresses
        self._enabled = email_config.enabled
        self._alert_last_sent: dict[str, float] = {}

    async def send_briefing(self, briefing: Briefing) -> bool:
        """브리핑을 이메일로 전송."""
        if not self._enabled:
            logger.info("이메일 비활성화 상태 — 전송 건너뜀")
            return False

        if not self._to_addresses:
            logger.warning("수신자 주소가 설정되지 않음")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[기술 브리핑] {briefing.title}"
            msg["From"] = self._from
            msg["To"] = ", ".join(self._to_addresses)

            # 텍스트 파트
            if briefing.content_text:
                msg.attach(MIMEText(briefing.content_text, "plain", "utf-8"))

            # HTML 파트
            if briefing.content_html:
                msg.attach(MIMEText(briefing.content_html, "html", "utf-8"))

            await aiosmtplib.send(
                msg,
                hostname=self._host,
                port=self._port,
                start_tls=True,
                username=self._user,
                password=self._password,
            )

            logger.info(
                f"브리핑 이메일 전송 완료 → {', '.join(self._to_addresses)}"
            )
            return True

        except Exception as e:
            logger.error(f"이메일 전송 실패: {e}")
            return False

    async def send_html(
        self,
        subject: str,
        html: str,
        text: str | None,
        to_addresses: list[str],
        logo_path: str | None = None,
    ) -> bool:
        """지정 수신자에게 HTML 메일 발송. logo_path가 있으면 cid:logo로 임베드."""
        if not self._enabled:
            logger.info("이메일 비활성화 상태 — 전송 건너뜀")
            return False
        if not to_addresses:
            logger.warning("수신자 주소 없음 — 전송 건너뜀")
            return False

        try:
            has_logo = bool(logo_path) and os.path.exists(logo_path)
            if has_logo:
                msg = MIMEMultipart("related")
                alt = MIMEMultipart("alternative")
                msg.attach(alt)
                if text:
                    alt.attach(MIMEText(text, "plain", "utf-8"))
                alt.attach(MIMEText(html, "html", "utf-8"))
                with open(logo_path, "rb") as f:
                    img = MIMEImage(f.read())
                img.add_header("Content-ID", "<logo>")
                img.add_header("Content-Disposition", "inline", filename="logo.png")
                msg.attach(img)
            else:
                msg = MIMEMultipart("alternative")
                if text:
                    msg.attach(MIMEText(text, "plain", "utf-8"))
                msg.attach(MIMEText(html, "html", "utf-8"))

            msg["Subject"] = subject
            msg["From"] = self._from
            msg["To"] = ", ".join(to_addresses)

            await aiosmtplib.send(
                msg, hostname=self._host, port=self._port, start_tls=True,
                username=self._user, password=self._password,
            )
            logger.info(f"메일 전송 완료 → {', '.join(to_addresses)} | {subject}")
            return True
        except Exception as e:
            logger.error(f"메일 전송 실패: {e}")
            return False

    async def send_alert(self, title: str, message: str) -> bool:
        """시스템 알림 이메일 전송 (동일 제목 1시간 쿨다운)."""
        if not self._enabled or not self._to_addresses:
            return False

        now = time.monotonic()
        last = self._alert_last_sent.get(title, 0)
        if now - last < ALERT_COOLDOWN_SECONDS:
            logger.info(f"알림 쿨다운 중 (전송 생략): {title}")
            return False

        try:
            msg = MIMEMultipart()
            msg["Subject"] = f"[SNS Briefing 알림] {title}"
            msg["From"] = self._from
            msg["To"] = ", ".join(self._to_addresses)
            msg.attach(MIMEText(message, "plain", "utf-8"))

            await aiosmtplib.send(
                msg,
                hostname=self._host,
                port=self._port,
                start_tls=True,
                username=self._user,
                password=self._password,
            )
            self._alert_last_sent[title] = now
            return True

        except Exception as e:
            logger.error(f"알림 이메일 전송 실패: {e}")
            return False
