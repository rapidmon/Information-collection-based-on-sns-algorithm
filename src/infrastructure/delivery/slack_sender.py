"""슬랙 브리핑 발송기.

브리핑을 슬랙 채널에 게시한다:
  ① 헤더 메시지 1개(날짜·건수·카테고리 요약)
  ② 각 항목은 헤더의 스레드 댓글로 게시
  ③ 댓글마다 투표용 이모지 리액션(기본 👍/👎)을 선부착 — 팀원은 클릭만으로 투표

게시 시 항목별 메시지 ts를 상태 파일(slack.state_path)에 저장해두고,
투표 마감 시각에 tally_votes()가 reactions.get으로 득표를 집계한다.

Slack Web API(chat.postMessage, reactions.add, reactions.get)를 httpx로 직접 호출한다.
봇 필요 스코프: chat:write, reactions:write, reactions:read.
봇이 대상 채널에 초대돼 있어야 한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx

from src.infrastructure.config.settings import Settings, SlackConfig
from src.infrastructure.delivery.categories import (
    CATEGORY_EMOJI,
    CATEGORY_KO,
    VALID_BRIEFING_CATEGORIES,
)

logger = logging.getLogger(__name__)

SLACK_API_BASE = "https://slack.com/api"

# 채널당 초당 1메시지 rate limit — 항목 게시 사이 대기
POST_INTERVAL_SECONDS = 1.1

# 항목당 표시할 최대 출처 링크 수
MAX_SOURCE_LINKS = 3

# reactions.get 집계 호출 사이 대기 (읽기라 쓰기보다 한도가 널널함)
TALLY_INTERVAL_SECONDS = 0.3

# 슬랙 메시지 1건 최대 길이(공식 40k) — 여유를 두고 이 길이에서 분할
MESSAGE_CHUNK_CHARS = 12000


def _esc(text: str) -> str:
    """Slack mrkdwn 예약 문자 이스케이프."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _item_bullets(item) -> list[str]:
    """구조화 불릿 우선, 구버전 브리핑은 body 텍스트에서 폴백."""
    if item.body_bullets:
        return list(item.body_bullets)
    return [
        line.strip().lstrip("- ").strip()
        for line in (item.body or "").split("\n")
        if line.strip()
    ]


def build_header_text(date_str: str, items: list) -> str:
    """채널 본문에 올라갈 헤더 메시지 (항목들은 이 메시지의 스레드로)."""
    counts: dict[str, int] = {}
    for it in items:
        cat = it.category_name or "Other"
        counts[cat] = counts.get(cat, 0) + 1
    parts = [
        f"{CATEGORY_EMOJI.get(c, '🗂')} {CATEGORY_KO.get(c, c)} {counts[c]}"
        for c in VALID_BRIEFING_CATEGORIES
        if c in counts
    ]
    lines = [f"☀️ *Morning Commit {date_str}* — 오늘의 브리핑 {len(items)}건"]
    if parts:
        lines.append(" · ".join(parts))
    lines.append("_각 항목은 이 메시지의 스레드에 있어요. 👍/👎 리액션으로 투표해 주세요!_")
    return "\n".join(lines)


def build_item_text(item) -> str:
    """스레드 댓글 하나에 들어갈 항목 본문 (mrkdwn)."""
    cat = item.category_name or "Other"
    lines = [f"{CATEGORY_EMOJI.get(cat, '🗂')} *[{CATEGORY_KO.get(cat, cat)}] {_esc(item.headline)}*"]
    for bullet in _item_bullets(item):
        lines.append(f"• {_esc(bullet)}")
    urls = (item.source_urls or [])[:MAX_SOURCE_LINKS]
    if urls:
        lines.append(" · ".join(f"<{u}|출처{i + 1}>" for i, u in enumerate(urls)))
    return "\n".join(lines)


def _split_chunks(text: str, size: int) -> list[str]:
    """줄 경계를 살려 text를 size 이하 조각으로 분할."""
    if len(text) <= size:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        # 한 줄 자체가 size를 넘으면 강제 분할
        while len(line) > size:
            chunks.append(line[:size])
            line = line[size:]
        if len(current) + len(line) + 1 > size:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def pick_winners(items: list[dict]) -> list[dict]:
    """순득표(up-down) 최고 동률 항목을 전부 반환 (중요도 내림차순 정렬).

    투표가 하나도 없으면 중요도 최고 항목 1건만 no_votes 플래그와 함께 반환.
    """
    if not items:
        return []
    total_votes = sum(it.get("up", 0) + it.get("down", 0) for it in items)
    if total_votes == 0:
        winner = max(items, key=lambda it: it.get("importance", 0))
        return [{**winner, "no_votes": True}]
    best = max(it.get("up", 0) - it.get("down", 0) for it in items)
    winners = [it for it in items if it.get("up", 0) - it.get("down", 0) == best]
    winners.sort(key=lambda it: -(it.get("importance") or 0))
    return [{**w, "no_votes": False} for w in winners]


def pick_winner(items: list[dict]) -> dict | None:
    """1위 1건만 필요할 때의 편의 함수 (동률이면 중요도 최고)."""
    winners = pick_winners(items)
    return winners[0] if winners else None


# winner_prompt에 플레이스홀더가 하나도 없을 때 자동으로 덧붙이는 입력 블록.
# (프롬프트만 쓰고 입력 섹션을 빼먹으면 LLM이 "뉴스가 없다"고 답하는 사고 방지)
INPUT_BLOCK = '''
입력 게시물:
"""
제목: {headline}
카테고리: {category}
요약:
{bullets}
원본 게시물:
{posts}
출처:
{sources}
"""'''

_PLACEHOLDERS = ("{headline}", "{bullets}", "{posts}", "{sources}", "{category}")


def ensure_input_block(template: str) -> str:
    """템플릿에 플레이스홀더가 하나도 없으면 표준 입력 블록을 덧붙인다."""
    if any(key in template for key in _PLACEHOLDERS):
        return template
    logger.warning("winner_prompt에 플레이스홀더 없음 — 표준 입력 블록을 자동 첨부")
    return template.rstrip() + "\n" + INPUT_BLOCK


def render_winner_prompt(template: str, item: dict, posts_text: str, date_str: str) -> str:
    """winner_prompt 템플릿의 플레이스홀더 치환.

    사용자가 쓴 템플릿에 임의의 중괄호가 있어도 안전하도록
    str.format 대신 알려진 키만 치환한다.
    플레이스홀더가 아예 없는 템플릿엔 표준 입력 블록을 자동으로 붙인다.
    """
    template = ensure_input_block(template)
    replacements = {
        "{headline}": item.get("headline", ""),
        "{bullets}": "\n".join(f"- {b}" for b in item.get("bullets", [])),
        "{category}": CATEGORY_KO.get(item.get("category", ""), item.get("category", "")),
        "{sources}": "\n".join(item.get("source_urls", [])),
        "{posts}": posts_text,
        "{date}": date_str,
    }
    out = template
    for key, value in replacements.items():
        out = out.replace(key, value)
    return out


class SlackNotifier:
    """Slack Web API 기반 브리핑 게시기."""

    def __init__(self, settings: Settings, config: SlackConfig):
        self._token = settings.slack_bot_token
        self._config = config

    @property
    def is_configured(self) -> bool:
        return bool(self._config.enabled and self._token and self._config.channel)

    async def _call(
        self,
        client: httpx.AsyncClient,
        method: str,
        payload: dict,
        http_get: bool = False,
    ) -> dict:
        """Slack API 호출. 429는 Retry-After만큼 1회 재시도.

        reactions.get 등 조회형 메서드는 http_get=True로 URL 파라미터 전달.
        """
        for attempt in range(2):
            if http_get:
                resp = await client.get(f"{SLACK_API_BASE}/{method}", params=payload)
            else:
                resp = await client.post(f"{SLACK_API_BASE}/{method}", json=payload)
            if resp.status_code == 429 and attempt == 0:
                wait = float(resp.headers.get("Retry-After", "1"))
                logger.info(f"슬랙 rate limit — {wait}초 대기 후 재시도")
                await asyncio.sleep(wait)
                continue
            data = resp.json()
            if not data.get("ok"):
                # 이미 달린 리액션은 성공으로 간주 (재발송 시)
                if data.get("error") == "already_reacted":
                    return data
                if data.get("error") == "not_in_channel":
                    raise RuntimeError(
                        "Slack API 실패: 봇이 채널에 없음 — 채널에서 '/invite @봇이름' 실행 필요"
                    )
                raise RuntimeError(f"Slack API {method} 실패: {data.get('error')}")
            return data
        raise RuntimeError(f"Slack API {method} 재시도 초과")

    async def send_briefing(self, briefing, date_str: str) -> dict:
        """헤더 1개 + 항목별 스레드 댓글 게시, 댓글마다 투표 리액션 선부착."""
        if not self._config.enabled:
            return {"sent": False, "reason": "disabled"}
        if not self._token or not self._config.channel:
            logger.warning("슬랙 토큰/채널 미설정 — 발송 건너뜀")
            return {"sent": False, "reason": "not_configured"}

        items = [
            it for it in briefing.items
            if it.category_name in VALID_BRIEFING_CATEGORIES
        ]
        items.sort(key=lambda it: it.sort_order)
        if not items:
            return {"sent": False, "reason": "no_items"}

        posted = 0
        posted_items: list[dict] = []  # 투표 집계용 ts↔항목 매핑
        async with httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {self._token}"},
        ) as client:
            head = await self._call(client, "chat.postMessage", {
                "channel": self._config.channel,
                "text": build_header_text(date_str, items),
                "unfurl_links": False,
                "unfurl_media": False,
            })
            channel_id = head["channel"]  # 이후 호출은 채널명 대신 확정된 ID 사용
            thread_ts = head["ts"]

            for it in items:
                await asyncio.sleep(POST_INTERVAL_SECONDS)
                try:
                    msg = await self._call(client, "chat.postMessage", {
                        "channel": channel_id,
                        "thread_ts": thread_ts,
                        "text": build_item_text(it),
                        "unfurl_links": False,
                        "unfurl_media": False,
                    })
                    for name in self._config.reactions:
                        await self._call(client, "reactions.add", {
                            "channel": channel_id,
                            "timestamp": msg["ts"],
                            "name": name,
                        })
                    posted += 1
                    posted_items.append({
                        "ts": msg["ts"],
                        "headline": it.headline,
                        "category": it.category_name,
                        "importance": it.importance_score or 0,
                        "bullets": _item_bullets(it),
                        "source_urls": list(it.source_urls or []),
                        "source_post_ids": list(it.source_post_ids or []),
                    })
                except Exception as e:
                    # 한 항목 실패가 나머지 게시를 막지 않게 한다
                    logger.error(f"슬랙 항목 게시 실패(계속 진행): {it.headline[:40]} — {e}")

        self._save_state({
            "date_str": date_str,
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "items": posted_items,
        })
        logger.info(f"슬랙 브리핑 게시: {posted}/{len(items)}건 → {self._config.channel}")
        return {
            "sent": posted > 0,
            "channel": self._config.channel,
            "items": posted,
            "total": len(items),
        }

    # ─── 투표 집계 ───

    def _save_state(self, state: dict) -> None:
        """게시 상태(ts↔항목)를 저장. 매일 덮어쓴다. 실패해도 발송 자체는 성공 처리."""
        try:
            path = Path(self._config.state_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"슬랙 게시 상태 저장 실패(투표 집계 불가): {e}")

    def load_state(self) -> dict | None:
        try:
            return json.loads(Path(self._config.state_path).read_text(encoding="utf-8"))
        except Exception:
            return None

    async def tally_votes(self) -> dict | None:
        """마지막 게시분의 항목별 득표를 reactions.get으로 집계.

        봇이 선부착한 리액션 1개씩은 차감한다. reactions[0]=찬성, [1]=반대.
        반환: state dict에 각 item의 up/down이 채워진 형태.
        """
        state = self.load_state()
        if not state or not state.get("items"):
            return None

        up_name = self._config.reactions[0] if self._config.reactions else "+1"
        down_name = self._config.reactions[1] if len(self._config.reactions) > 1 else "-1"

        async with httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {self._token}"},
        ) as client:
            for it in state["items"]:
                await asyncio.sleep(TALLY_INTERVAL_SECONDS)
                try:
                    data = await self._call(client, "reactions.get", {
                        "channel": state["channel_id"],
                        "timestamp": it["ts"],
                        "full": True,
                    }, http_get=True)
                    # 피부톤 변형("+1::skin-tone-2" 등)은 별도 리액션으로 오므로
                    # 베이스 이름으로 합산한다 — 안 하면 피부톤 설정 사용자의 표가 누락됨
                    counts: dict[str, int] = {}
                    for r in (data.get("message", {}).get("reactions") or []):
                        base = (r.get("name") or "").split("::")[0]
                        counts[base] = counts.get(base, 0) + r.get("count", 0)
                    # 봇 선부착분 1개 차감
                    it["up"] = max(0, counts.get(up_name, 0) - 1)
                    it["down"] = max(0, counts.get(down_name, 0) - 1)
                except Exception as e:
                    logger.error(f"리액션 집계 실패(0표 처리): {it['headline'][:40]} — {e}")
                    it["up"], it["down"] = 0, 0
        return state

    async def post_message(self, text: str, thread_ts: str | None = None) -> dict:
        """채널(또는 스레드)에 텍스트 게시. 길면 첫 조각만 본문, 나머지는 그 스레드에."""
        chunks = _split_chunks(text, MESSAGE_CHUNK_CHARS)
        async with httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {self._token}"},
        ) as client:
            payload = {
                "channel": self._config.channel,
                "text": chunks[0],
                "unfurl_links": False,
                "unfurl_media": False,
            }
            if thread_ts:
                payload["thread_ts"] = thread_ts
            head = await self._call(client, "chat.postMessage", payload)
            for chunk in chunks[1:]:
                await asyncio.sleep(POST_INTERVAL_SECONDS)
                await self._call(client, "chat.postMessage", {
                    "channel": head["channel"],
                    "thread_ts": thread_ts or head["ts"],
                    "text": chunk,
                    "unfurl_links": False,
                    "unfurl_media": False,
                })
        return head
