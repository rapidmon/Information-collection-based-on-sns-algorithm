"""외신·AI 기업 뉴스 수집기 — RSS/Atom (HTTP, 로그인 불필요).

config/settings.yaml `collection.news.feeds`에 선언된 피드를 순회한다.
피드는 세 티어로 구분한다:
- official: 정식 tech 매체 (추후 보도폭 점수 가산 대상)
- paywalled: 페이월 매체 — Google News RSS로 헤드라인·재인용만
- primary: AI 기업 1차 소스 (공식 블로그)

티어는 Post에 저장하지 않고 채점 단계에서 author(매체명)→설정 역참조로 쓴다.
공신력 매체 보도라 스캠 검증 대상이 아니다 — generate_briefing._verify_top에서
source='news'는 producthunt와 함께 제외된다.
"""

from __future__ import annotations

import asyncio
import hashlib
import html as _html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from src.domain.entities import Post
from src.infrastructure.collectors.http import fetch_text
from src.infrastructure.config.settings import CollectorConfig

logger = logging.getLogger(__name__)

_TAG = re.compile(r"<[^>]+>")

# 피드 사이 대기(초) — Google News 프록시 피드가 여럿이라 연속 타격을 피한다
_FEED_DELAY = 0.5


def _local(tag: str) -> str:
    """네임스페이스 제거한 로컬 태그명."""
    return tag.rsplit("}", 1)[-1]


def _child_text(elem: ET.Element, *names: str) -> str:
    """로컬명이 names 중 하나인 첫 자식의 텍스트."""
    for child in elem:
        if _local(child.tag) in names and child.text:
            return child.text.strip()
    return ""


def _entry_link(elem: ET.Element) -> str:
    """RSS <link>텍스트</link> 또는 Atom <link href=...> 추출."""
    alternate = ""
    for child in elem:
        if _local(child.tag) != "link":
            continue
        if child.text and child.text.strip():  # RSS 2.0
            return child.text.strip()
        href = child.get("href", "")  # Atom
        if href and child.get("rel", "alternate") == "alternate":
            return href
        alternate = alternate or href
    return alternate


def _entry_date(elem: ET.Element) -> datetime | None:
    """pubDate(RFC822) 또는 published/updated(ISO8601) → naive UTC."""
    raw = _child_text(elem, "pubDate", "published", "updated")
    if not raw:
        return None
    try:
        if raw[:1].isdigit():  # ISO8601
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:  # RFC822 ("Tue, 14 Jul 2026 ...")
            dt = parsedate_to_datetime(raw)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return None


class NewsCollector:
    """설정 선언 피드 목록 기반 뉴스 수집기 (HTTP, 로그인 불필요)."""

    def __init__(self, config: CollectorConfig):
        self._config = config

    @property
    def source_name(self) -> str:
        return "news"

    async def is_session_valid(self) -> bool:
        return True  # 공개 피드 — 로그인 불필요

    async def collect(self) -> list[Post]:
        cutoff = datetime.utcnow() - timedelta(days=self._config.max_age_days)
        seen: set[str] = set()
        posts: list[Post] = []

        for i, feed in enumerate(self._config.feeds):
            name, url = feed.get("name", ""), feed.get("url", "")
            if not name or not url:
                logger.warning(f"[news] 피드 설정 불완전 — 스킵: {feed}")
                continue
            if i:
                await asyncio.sleep(_FEED_DELAY)

            xml = await fetch_text(url, f"news:{name}")
            if xml is None:
                continue  # 개별 피드 실패가 나머지 피드를 막지 않는다

            entries = self._parse_feed(xml, name)
            kept = 0
            for entry in entries[: self._config.max_items_per_feed]:
                post = self._to_post(entry, name, cutoff, seen)
                if post:
                    posts.append(post)
                    kept += 1
            logger.info(f"[news] {name}: {kept}건 (피드 {len(entries)}건)")

        logger.info(f"[news] 전체 {len(posts)}건 수집 완료 (피드 {len(self._config.feeds)}개)")
        return posts

    def _parse_feed(self, xml: str, feed_name: str) -> list[dict]:
        """RSS 2.0 <item> / Atom <entry>를 공통 dict로 파싱."""
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as e:
            logger.warning(f"[news] {feed_name}: XML 파싱 실패 — {e}")
            return []

        entries = []
        for elem in root.iter():
            if _local(elem.tag) not in ("item", "entry"):
                continue
            entries.append({
                "title": _child_text(elem, "title"),
                "link": _entry_link(elem),
                "summary": _child_text(elem, "description", "summary", "content"),
                "published": _entry_date(elem),
                "guid": _child_text(elem, "guid", "id") or _entry_link(elem),
            })
        return entries

    def _to_post(
        self, entry: dict, feed_name: str, cutoff: datetime, seen: set[str]
    ) -> Post | None:
        try:
            title = _html.unescape(entry["title"]).strip()
            if not title or not entry["guid"]:
                return None

            eid = f"news_{hashlib.sha1(entry['guid'].encode()).hexdigest()[:16]}"
            if eid in seen:
                return None
            seen.add(eid)

            # 게시일 컷오프 — 아카이브형 피드(OpenAI 등)의 과거 항목 배제.
            # 날짜 없는 항목은 유지 (dedup이 external_id로 재수집을 막는다)
            published = entry["published"]
            if published and published < cutoff:
                return None

            # description/summary의 HTML 제거 후 발췌 — 페이월 피드는 대개 빈 값(헤드라인만)
            summary = _html.unescape(_TAG.sub(" ", entry["summary"]))
            summary = re.sub(r"\s+", " ", summary).strip()[:500]

            body = f"{title}\n\n{summary}" if summary and summary != title else title

            return Post(
                source="news",
                external_id=eid,
                url=entry["link"],
                author=feed_name,
                content_text=body,
                published_at=published,
                collected_at=datetime.utcnow(),
            )
        except Exception as e:
            logger.debug(f"[news] {feed_name} 항목 파싱 실패: {e}")
            return None
