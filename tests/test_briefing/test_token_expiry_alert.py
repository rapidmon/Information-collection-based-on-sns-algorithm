"""Claude 토큰 만료 알림 테스트 — D-7부터 매일, 만료 후에도 갱신 전까지 지속."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from src.application.use_cases.scheduler import token_expiry_message
from src.infrastructure.config.settings import Settings, SlackConfig
from src.infrastructure.delivery.slack_sender import SlackNotifier

TODAY = date(2026, 7, 29)


def test_no_alert_before_window():
    # D-8: 아직 조용
    assert token_expiry_message("2026-08-06", TODAY) is None


def test_alert_starts_at_d7():
    msg = token_expiry_message("2026-08-05", TODAY)
    assert msg is not None
    assert "D-7" in msg
    assert "claude setup-token" in msg


def test_alert_on_expiry_day():
    msg = token_expiry_message("2026-07-29", TODAY)
    assert msg is not None
    assert "오늘" in msg


def test_alert_continues_after_expiry():
    msg = token_expiry_message("2026-07-26", TODAY)
    assert msg is not None
    assert "3일 전에 만료" in msg


def test_invalid_date_raises():
    with pytest.raises(ValueError):
        token_expiry_message("2026/08/05", TODAY)


async def test_send_dm_skips_without_user_id():
    notifier = SlackNotifier(
        settings=SimpleNamespace(slack_bot_token="xoxb-test"),
        config=SlackConfig({"enabled": True, "channel": "#x"}),  # alert_user_id 없음
    )
    assert (await notifier.send_dm("테스트"))["reason"] == "not_configured"


async def test_send_dm_skips_without_token():
    notifier = SlackNotifier(
        settings=SimpleNamespace(slack_bot_token=""),
        config=SlackConfig({"alert_user_id": "U012345"}),
    )
    assert (await notifier.send_dm("테스트"))["reason"] == "not_configured"


async def test_send_dm_opens_conversation_and_posts(monkeypatch):
    notifier = SlackNotifier(
        settings=SimpleNamespace(slack_bot_token="xoxb-test"),
        # DM은 채널 게시 설정(enabled/channel)과 무관하게 동작해야 한다
        config=SlackConfig({"enabled": False, "alert_user_id": "U012345"}),
    )
    calls: list[tuple[str, dict]] = []

    async def fake_call(client, method, payload, http_get=False):
        calls.append((method, payload))
        if method == "conversations.open":
            return {"ok": True, "channel": {"id": "D0AAA"}}
        return {"ok": True}

    monkeypatch.setattr(notifier, "_call", fake_call)
    result = await notifier.send_dm("⏰ 토큰 D-7")

    assert result["sent"] is True
    assert calls[0] == ("conversations.open", {"users": "U012345"})
    assert calls[1][0] == "chat.postMessage"
    assert calls[1][1]["channel"] == "D0AAA"
    assert "D-7" in calls[1][1]["text"]
