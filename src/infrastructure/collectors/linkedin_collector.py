"""LinkedIn 수집기.

사용자의 실행 중인 Chrome에 CDP로 연결하여 DOM 파싱으로 피드를 수집한다.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime
from typing import Optional

from src.domain.entities import Post
from src.domain.exceptions import SessionExpiredError
from src.infrastructure.collectors.cdp import cdp_connection, check_session
from src.infrastructure.config.settings import CollectorConfig

logger = logging.getLogger(__name__)


class LinkedInCollector:
    """LinkedIn 알고리즘 피드 수집기 (CDP 기반)."""

    FEED_URL = "https://www.linkedin.com/feed/"

    def __init__(self, config: CollectorConfig, cdp_port: int = 9222):
        self._config = config
        self._cdp_url = f"http://127.0.0.1:{cdp_port}"

    @property
    def source_name(self) -> str:
        return "linkedin"

    async def is_session_valid(self) -> bool:
        return await check_session(
            self._cdp_url, "linkedin", self.FEED_URL,
            ["login", "authwall", "checkpoint"],
        )

    async def collect(self) -> list[Post]:
        """DOM 파싱으로 LinkedIn 피드를 수집."""
        async with cdp_connection(self._cdp_url, "linkedin") as (pw, context):
            page = await context.new_page()

            try:
                await page.goto(self.FEED_URL, wait_until="domcontentloaded", timeout=60000)

                if any(kw in page.url for kw in ["login", "authwall", "checkpoint", "security"]):
                    raise SessionExpiredError("linkedin — Chrome에서 LinkedIn에 로그인 해주세요")

                await asyncio.sleep(random.uniform(2.0, 4.0))
                await page.mouse.move(random.randint(100, 800), random.randint(100, 600))

                posts: list[Post] = []
                seen_ids: set[str] = set()

                for round_num in range(self._config.scroll_rounds):
                    updates = await page.query_selector_all(".feed-shared-update-v2")
                    if not updates:
                        updates = await page.query_selector_all(
                            'div[data-urn*="urn:li:activity"]'
                        )

                    for update in updates:
                        post = await self._parse_feed_update(update)
                        if post and post.external_id not in seen_ids:
                            seen_ids.add(post.external_id)
                            posts.append(post)

                    scroll_amount = random.randint(800, 1500)
                    await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
                    await asyncio.sleep(
                        random.uniform(self._config.scroll_delay_min, self._config.scroll_delay_max)
                    )

                    if round_num % 2 == 0:
                        await page.mouse.move(
                            random.randint(100, 800), random.randint(100, 600)
                        )

                logger.info(f"[linkedin] {len(posts)}건 수집 완료")
                return posts

            finally:
                await page.close()

    async def _parse_feed_update(self, element) -> Optional[Post]:
        try:
            urn = await element.get_attribute("data-urn") or ""
            activity_id = ""
            if "urn:li:activity:" in urn:
                activity_id = urn.split("urn:li:activity:")[-1]
            else:
                data_id = await element.get_attribute("data-id") or ""
                if data_id:
                    activity_id = data_id

            if not activity_id:
                text_content = await element.inner_text()
                if text_content:
                    activity_id = str(hash(text_content[:200]))[:12]
                else:
                    return None

            # 작성자
            author = ""
            actor_el = await element.query_selector(
                ".update-components-actor__name span:first-child"
            )
            if not actor_el:
                actor_el = await element.query_selector(".feed-shared-actor__name")
            if actor_el:
                author = (await actor_el.inner_text()).strip()
                author = author.split("\n")[0].strip()

            # 작성자 프로필 링크
            author_url = ""
            actor_link = await element.query_selector(".update-components-actor__meta-link")
            if not actor_link:
                actor_link = await element.query_selector(".feed-shared-actor__container-link")
            if actor_link:
                href = await actor_link.get_attribute("href") or ""
                if href:
                    author_url = (
                        f"https://www.linkedin.com{href}" if href.startswith("/") else href
                    )

            # 본문 텍스트
            content_text = ""
            text_el = await element.query_selector(".feed-shared-update-v2__description")
            if not text_el:
                text_el = await element.query_selector(".feed-shared-text")
            if not text_el:
                text_el = await element.query_selector('span[dir="ltr"]')
            if text_el:
                content_text = (await text_el.inner_text()).strip()

            if not content_text:
                return None

            # "...더 보기" 클릭 시도
            see_more = await element.query_selector(
                "button.feed-shared-inline-show-more-text__button"
            )
            if see_more:
                try:
                    await see_more.click()
                    await asyncio.sleep(0.5)
                    if text_el:
                        content_text = (await text_el.inner_text()).strip()
                except Exception:
                    pass

            # 인게이지먼트
            likes = await self._extract_count(
                element, ".social-details-social-counts__reactions-count"
            )
            comments = await self._extract_count(
                element, "button.social-details-social-counts__comments"
            )
            reposts = await self._extract_count(
                element, "button.social-details-social-counts__reposts"
            )

            post_url = f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}/"

            return Post(
                source="linkedin",
                external_id=f"li_{activity_id}",
                url=post_url,
                author=author,
                author_url=author_url or None,
                content_text=content_text,
                engagement_likes=likes,
                engagement_reposts=reposts,
                engagement_comments=comments,
                collected_at=datetime.utcnow(),
            )
        except Exception as e:
            logger.debug(f"[linkedin] 피드 항목 파싱 실패: {e}")
            return None

    async def _extract_count(self, parent, selector: str) -> int:
        try:
            el = await parent.query_selector(selector)
            if el:
                text = (await el.inner_text()).strip()
                text = text.replace(",", "").replace(".", "").lower()
                if "k" in text:
                    return int(float(text.replace("k", "")) * 1000)
                digits = "".join(c for c in text if c.isdigit())
                return int(digits) if digits else 0
        except Exception:
            pass
        return 0
