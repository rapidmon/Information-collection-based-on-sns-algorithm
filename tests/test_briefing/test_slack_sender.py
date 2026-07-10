"""슬랙 브리핑 발송기 테스트 — 메시지 조립(순수 함수) + 게시 흐름(API 모킹)."""

from __future__ import annotations

from datetime import datetime

import pytest

from src.domain.entities.briefing import Briefing, BriefingItem
from src.infrastructure.config.settings import Settings, SlackConfig
from src.infrastructure.delivery import slack_sender
from src.infrastructure.delivery.slack_sender import (
    SlackNotifier,
    _split_chunks,
    build_header_text,
    build_item_text,
    pick_winner,
    pick_winners,
    render_winner_prompt,
)


def _item(**kwargs) -> BriefingItem:
    base = dict(headline="테스트 헤드라인", body="", importance_score=0.9)
    base.update(kwargs)
    return BriefingItem(**base)


def _briefing(items: list[BriefingItem]) -> Briefing:
    return Briefing(
        title="테스트 브리핑",
        period_start=datetime(2026, 7, 8, 8, 0),
        period_end=datetime(2026, 7, 9, 8, 0),
        items=items,
        total_items=len(items),
    )


# ──────────────────────────────────────────
# 메시지 조립
# ──────────────────────────────────────────

def test_item_text_escapes_mrkdwn_reserved_chars():
    it = _item(headline="A <B> & C", category_name="AI", body_bullets=["x < y"])
    text = build_item_text(it)
    assert "&lt;B&gt;" in text
    assert "&amp;" in text
    assert "x &lt; y" in text


def test_item_text_falls_back_to_body_when_no_bullets():
    it = _item(category_name="Coding", body="- 첫 불릿\n- 둘째 불릿")
    text = build_item_text(it)
    assert "• 첫 불릿" in text
    assert "• 둘째 불릿" in text


def test_item_text_caps_source_links_at_three():
    it = _item(category_name="AI", source_urls=[f"https://ex.com/{i}" for i in range(5)])
    text = build_item_text(it)
    assert "출처3" in text
    assert "출처4" not in text


def test_header_counts_by_category():
    items = [
        _item(category_name="AI"),
        _item(category_name="AI"),
        _item(category_name="Coding"),
    ]
    header = build_header_text("2026. 07. 09", items)
    assert "오늘의 브리핑 3건" in header
    assert "AI 2" in header
    assert "코딩 1" in header
    assert "투표" in header  # 투표 안내 문구 포함


# ──────────────────────────────────────────
# 게시 흐름
# ──────────────────────────────────────────

def _notifier(enabled=True, token="xoxb-test", channel="#brief", state_path=None) -> SlackNotifier:
    settings = Settings(slack_bot_token=token)
    data = {"enabled": enabled, "channel": channel}
    if state_path is not None:
        data["state_path"] = str(state_path)
    config = SlackConfig(data)
    return SlackNotifier(settings, config)


async def test_send_skipped_when_disabled():
    res = await _notifier(enabled=False).send_briefing(_briefing([_item()]), "2026. 07. 09")
    assert res == {"sent": False, "reason": "disabled"}


async def test_send_skipped_without_token():
    res = await _notifier(token="").send_briefing(_briefing([_item()]), "2026. 07. 09")
    assert res == {"sent": False, "reason": "not_configured"}


async def test_send_posts_header_then_threaded_items_with_reactions(monkeypatch, tmp_path):
    monkeypatch.setattr(slack_sender, "POST_INTERVAL_SECONDS", 0)
    notifier = _notifier(state_path=tmp_path / "state.json")

    calls: list[tuple[str, dict]] = []

    async def fake_call(client, method, payload):
        calls.append((method, payload))
        return {"ok": True, "channel": "C123", "ts": f"ts-{len(calls)}"}

    monkeypatch.setattr(notifier, "_call", fake_call)

    items = [
        _item(category_name="AI", sort_order=1),
        _item(category_name="Coding", sort_order=0),
        _item(category_name=None),  # 유효 카테고리 아님 → 제외
    ]
    res = await notifier.send_briefing(_briefing(items), "2026. 07. 09")

    assert res["sent"] is True
    assert res["items"] == 2

    # 헤더 1 + 항목 2 = 메시지 3, 항목당 리액션 2개(👍/👎) = 4
    posts = [(m, p) for m, p in calls if m == "chat.postMessage"]
    reactions = [(m, p) for m, p in calls if m == "reactions.add"]
    assert len(posts) == 3
    assert len(reactions) == 4
    assert {p["name"] for _, p in reactions} == {"+1", "-1"}

    # 헤더는 스레드 없음, 항목들은 헤더 ts에 스레딩 + sort_order 순서(Coding 먼저)
    header_payload = posts[0][1]
    assert "thread_ts" not in header_payload
    assert posts[1][1]["thread_ts"] == "ts-1"
    assert "코딩" in posts[1][1]["text"]
    assert "AI" in posts[2][1]["text"]


async def test_one_item_failure_does_not_stop_the_rest(monkeypatch, tmp_path):
    monkeypatch.setattr(slack_sender, "POST_INTERVAL_SECONDS", 0)
    notifier = _notifier(state_path=tmp_path / "state.json")

    calls: list[str] = []

    async def fake_call(client, method, payload):
        calls.append(method)
        # 두 번째 메시지(첫 항목) 게시만 실패시킨다
        if method == "chat.postMessage" and len([c for c in calls if c == "chat.postMessage"]) == 2:
            raise RuntimeError("Slack API chat.postMessage 실패: channel_not_found")
        return {"ok": True, "channel": "C123", "ts": f"ts-{len(calls)}"}

    monkeypatch.setattr(notifier, "_call", fake_call)

    items = [_item(category_name="AI", sort_order=0), _item(category_name="AI", sort_order=1)]
    res = await notifier.send_briefing(_briefing(items), "2026. 07. 09")

    assert res["sent"] is True
    assert res["items"] == 1
    assert res["total"] == 2


# ──────────────────────────────────────────
# 투표 집계·1위 선정
# ──────────────────────────────────────────

def test_send_saves_vote_state(monkeypatch, tmp_path):
    """게시 후 상태 파일에 ts↔항목 매핑이 남는다 (집계 잡이 읽음)."""
    state_file = tmp_path / "state.json"
    notifier = _notifier(state_path=state_file)
    monkeypatch.setattr(slack_sender, "POST_INTERVAL_SECONDS", 0)

    async def fake_call(client, method, payload, **kw):
        return {"ok": True, "channel": "C123", "ts": "ts-x"}

    monkeypatch.setattr(notifier, "_call", fake_call)

    import asyncio
    asyncio.run(notifier.send_briefing(
        _briefing([_item(category_name="AI", source_post_ids=[7])]), "2026. 07. 09"
    ))

    state = notifier.load_state()
    assert state["date_str"] == "2026. 07. 09"
    assert state["channel_id"] == "C123"
    assert state["items"][0]["source_post_ids"] == [7]


async def test_tally_votes_subtracts_bot_reactions(monkeypatch, tmp_path):
    state_file = tmp_path / "state.json"
    notifier = _notifier(state_path=state_file)
    monkeypatch.setattr(slack_sender, "TALLY_INTERVAL_SECONDS", 0)
    notifier._save_state({
        "date_str": "2026. 07. 09",
        "channel_id": "C123",
        "thread_ts": "ts-h",
        "items": [{"ts": "ts-1", "headline": "A"}, {"ts": "ts-2", "headline": "B"}],
    })

    reactions_by_ts = {
        "ts-1": [{"name": "+1", "count": 4}, {"name": "-1", "count": 1}],  # 실투표 👍3 👎0
        "ts-2": [{"name": "+1", "count": 1}, {"name": "-1", "count": 3}],  # 실투표 👍0 👎2
    }

    async def fake_call(client, method, payload, **kw):
        assert method == "reactions.get"
        return {"ok": True, "message": {"reactions": reactions_by_ts[payload["timestamp"]]}}

    monkeypatch.setattr(notifier, "_call", fake_call)

    tally = await notifier.tally_votes()
    assert (tally["items"][0]["up"], tally["items"][0]["down"]) == (3, 0)
    assert (tally["items"][1]["up"], tally["items"][1]["down"]) == (0, 2)


async def test_tally_merges_skin_tone_variants(monkeypatch, tmp_path):
    """피부톤 이모지(+1::skin-tone-N)로 투표한 표도 합산된다 (2026-07-10 오집계 재발 방지)."""
    notifier = _notifier(state_path=tmp_path / "state.json")
    monkeypatch.setattr(slack_sender, "TALLY_INTERVAL_SECONDS", 0)
    notifier._save_state({
        "date_str": "d", "channel_id": "C123", "thread_ts": "h",
        "items": [{"ts": "ts-1", "headline": "A"}],
    })

    async def fake_call(client, method, payload, **kw):
        return {"ok": True, "message": {"reactions": [
            {"name": "+1", "count": 3},                 # 봇1 + 실투표2
            {"name": "+1::skin-tone-2", "count": 1},    # 피부톤 실투표1
            {"name": "-1", "count": 1},                 # 봇만
        ]}}

    monkeypatch.setattr(notifier, "_call", fake_call)

    tally = await notifier.tally_votes()
    assert (tally["items"][0]["up"], tally["items"][0]["down"]) == (3, 0)


def test_pick_winner_by_net_votes_then_importance():
    items = [
        {"headline": "A", "up": 3, "down": 2, "importance": 0.9},   # 순득표 1
        {"headline": "B", "up": 2, "down": 0, "importance": 0.5},   # 순득표 2 → 승
        {"headline": "C", "up": 0, "down": 0, "importance": 1.0},
    ]
    winner = pick_winner(items)
    assert winner["headline"] == "B"
    assert winner["no_votes"] is False


def test_pick_winner_falls_back_to_importance_when_no_votes():
    items = [
        {"headline": "A", "up": 0, "down": 0, "importance": 0.7},
        {"headline": "B", "up": 0, "down": 0, "importance": 0.95},
    ]
    winner = pick_winner(items)
    assert winner["headline"] == "B"
    assert winner["no_votes"] is True


def test_pick_winners_returns_all_ties_sorted_by_importance():
    items = [
        {"headline": "A", "up": 3, "down": 0, "importance": 0.9},
        {"headline": "B", "up": 3, "down": 0, "importance": 1.0},
        {"headline": "C", "up": 4, "down": 1, "importance": 0.5},  # 순득표 3 동률
        {"headline": "D", "up": 1, "down": 0, "importance": 0.99},
    ]
    winners = pick_winners(items)
    assert [w["headline"] for w in winners] == ["B", "A", "C"]
    assert all(w["no_votes"] is False for w in winners)


def test_pick_winners_no_votes_returns_single_importance_fallback():
    items = [
        {"headline": "A", "up": 0, "down": 0, "importance": 0.7},
        {"headline": "B", "up": 0, "down": 0, "importance": 0.95},
    ]
    winners = pick_winners(items)
    assert len(winners) == 1
    assert winners[0]["headline"] == "B"
    assert winners[0]["no_votes"] is True


def test_render_winner_prompt_replaces_placeholders_safely():
    template = "제목: {headline}\n요약:\n{bullets}\n{unknown} {그대로}"
    item = {"headline": "GPT-5.2 공개", "bullets": ["b1", "b2"], "category": "AI"}
    out = render_winner_prompt(template, item, "(원본 없음)", "2026. 07. 09")
    assert "제목: GPT-5.2 공개" in out
    assert "- b1\n- b2" in out
    # 모르는 중괄호는 건드리지 않는다 (str.format이면 KeyError)
    assert "{unknown} {그대로}" in out


def test_render_appends_input_block_when_no_placeholders():
    """입력 섹션을 빼먹은 프롬프트도 뉴스 내용이 항상 전달된다."""
    template = "카드뉴스 타이틀을 만들어줘. 17자 이내."
    item = {"headline": "토큰 절감 플러그인", "bullets": ["b1"], "category": "Coding"}
    out = render_winner_prompt(template, item, "원본텍스트", "2026. 07. 09")
    assert "제목: 토큰 절감 플러그인" in out
    assert "원본텍스트" in out


def test_split_chunks_respects_line_boundaries():
    text = "\n".join(f"line-{i}" * 10 for i in range(100))
    chunks = _split_chunks(text, 500)
    assert all(len(c) <= 500 for c in chunks)
    assert "\n".join(chunks) == text
