"""36Kr 뉴스플래시(快讯) 수집기 — 순수 HTTP(httpx).

로그인이 필요 없는 공개 사이트. 페이지의 `window.initialState` JSON에서
뉴스플래시 목록을 추출한다. (CDP/Chrome 불필요)

데이터 경로: newsflashCatalogData.data.newsflashList.data.itemList
각 항목 templateMaterial: widgetTitle(제목), widgetContent(본문), publishTime(epoch ms)
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta

import httpx

from src.domain.entities import Post
from src.infrastructure.config.settings import CollectorConfig

logger = logging.getLogger(__name__)

NEWSFLASH_URL = "https://36kr.com/newsflashes"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_STATE_RE = re.compile(r"window\.initialState\s*=\s*(\{.*)", re.S)


class Kr36Collector:
    """36氪 뉴스플래시 수집기 (HTTP 기반, 로그인 불필요)."""

    def __init__(self, config: CollectorConfig):
        self._config = config

    @property
    def source_name(self) -> str:
        return "36kr"

    async def is_session_valid(self) -> bool:
        return True  # 공개 사이트 — 로그인 불필요

    async def collect(self) -> list[Post]:
        try:
            async with httpx.AsyncClient(
                timeout=20.0,
                follow_redirects=True,
                headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"},
            ) as client:
                resp = await client.get(NEWSFLASH_URL)
                resp.raise_for_status()
                html = resp.text
        except Exception as e:
            logger.error(f"[36kr] 페이지 요청 실패: {e}")
            return []

        items = self._extract_items(html)
        cutoff = datetime.utcnow() - timedelta(days=self._config.max_age_days)
        seen: set[str] = set()
        posts: list[Post] = []
        for it in items:
            post = self._parse_item(it, cutoff, seen)
            if post:
                posts.append(post)

        logger.info(f"[36kr] {len(posts)}건 수집 완료 (목록 {len(items)}건)")
        return posts

    def _extract_items(self, html: str) -> list[dict]:
        """window.initialState JSON에서 뉴스플래시 itemList 추출."""
        m = _STATE_RE.search(html)
        if not m:
            logger.warning("[36kr] initialState 미발견 — 페이지 구조 변경 의심")
            return []
        raw = m.group(1).split("</script>")[0].rstrip().rstrip(";")
        try:
            data = json.loads(raw)
        except Exception as e:
            logger.warning(f"[36kr] initialState JSON 파싱 실패: {e}")
            return []
        try:
            return data["newsflashCatalogData"]["data"]["newsflashList"]["data"]["itemList"]
        except (KeyError, TypeError):
            logger.warning("[36kr] itemList 경로 변경됨")
            return []

    def _parse_item(self, it: dict, cutoff: datetime, seen: set[str]) -> Post | None:
        try:
            tm = it.get("templateMaterial", {}) or {}
            item_id = str(it.get("itemId") or tm.get("itemId") or "")
            if not item_id or item_id in seen:
                return None
            seen.add(item_id)

            title = (tm.get("widgetTitle") or "").strip()
            content = (tm.get("widgetContent") or "").strip()
            if not title and not content:
                return None

            published_at = None
            pt = tm.get("publishTime")
            if pt:
                try:
                    published_at = datetime.utcfromtimestamp(int(pt) / 1000)
                except (ValueError, TypeError, OverflowError):
                    pass

            # 게시일 컷오프 — max_age_days 초과면 스킵 (publishTime은 naive UTC)
            if published_at and published_at < cutoff:
                return None

            text = f"{title}\n\n{content}".strip() if content else title

            return Post(
                source="36kr",
                external_id=f"kr36_{item_id}",
                url=f"https://36kr.com/newsflashes/{item_id}",
                author="36氪",
                content_text=text,
                published_at=published_at,
                collected_at=datetime.utcnow(),
            )
        except Exception as e:
            logger.debug(f"[36kr] 항목 파싱 실패: {e}")
            return None
