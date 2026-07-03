"""Product Hunt 쇼케이스 수집기 — Atom RSS (HTTP, 로그인 불필요).

AI 카테고리 제품 런칭을 가져온다. '만든 결과물'이라 사실 검증 대상이 아니므로,
verify_claims 단계에서 source='producthunt'는 제외된다(process_posts에서 분기).
PH 피드는 큐레이션된 최신 런칭이라 게시일 컷오프는 적용하지 않는다(dedup은 external_id로).
"""

from __future__ import annotations

import html as _html
import logging
import re
from datetime import datetime, timezone

from src.domain.entities import Post
from src.infrastructure.collectors.http import fetch_text
from src.infrastructure.config.settings import CollectorConfig

logger = logging.getLogger(__name__)

FEED_URL = "https://www.producthunt.com/feed?category=artificial-intelligence"
_ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)
_TAG = re.compile(r"<[^>]+>")


def _field(entry: str, tag: str) -> str:
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", entry, re.S)
    return m.group(1).strip() if m else ""


class ProductHuntCollector:
    """Product Hunt AI 런칭 수집기 (HTTP 기반, 로그인 불필요)."""

    def __init__(self, config: CollectorConfig):
        self._config = config

    @property
    def source_name(self) -> str:
        return "producthunt"

    async def is_session_valid(self) -> bool:
        return True

    async def collect(self) -> list[Post]:
        xml = await fetch_text(FEED_URL, "producthunt")
        if xml is None:
            return []

        seen: set[str] = set()
        posts: list[Post] = []
        for entry in _ENTRY.findall(xml):
            p = self._parse(entry, seen)
            if p:
                posts.append(p)
        logger.info(f"[producthunt] {len(posts)}건 수집 완료")
        return posts

    def _parse(self, entry: str, seen: set[str]) -> Post | None:
        try:
            title = _html.unescape(_field(entry, "title")).strip()
            if not title:
                return None

            m = re.search(r"Post/(\d+)", _field(entry, "id"))
            pid = m.group(1) if m else ""
            if not pid or pid in seen:
                return None
            seen.add(pid)

            link_m = re.search(r'<link[^>]*href="([^"]+)"', entry)
            url = _html.unescape(link_m.group(1)) if link_m else ""

            # content(HTML) → 태그라인 텍스트
            text = _html.unescape(_field(entry, "content"))
            text = re.sub(r"\s+", " ", _TAG.sub(" ", text)).strip()
            tagline = text.split("Discussion")[0].strip()[:300]

            published_at = None
            pub = _field(entry, "published")
            if pub:
                try:
                    published_at = (
                        datetime.fromisoformat(pub.replace("Z", "+00:00"))
                        .astimezone(timezone.utc)
                        .replace(tzinfo=None)
                    )
                except ValueError:
                    pass

            body = f"{title} — {tagline}" if tagline else title
            return Post(
                source="producthunt",
                external_id=f"ph_{pid}",
                url=url,
                author="Product Hunt",
                content_text=body,
                published_at=published_at,
                collected_at=datetime.utcnow(),
            )
        except Exception as e:
            logger.debug(f"[producthunt] 파싱 실패: {e}")
            return None
