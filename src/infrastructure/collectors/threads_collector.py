"""Threads 수집기.

사용자의 실행 중인 Chrome에 CDP로 연결하여
GraphQL 응답 인터셉트 + DOM 파싱 하이브리드로 피드를 수집한다.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime
from typing import Any, Optional

from src.domain.entities import Post
from src.domain.exceptions import SessionExpiredError
from src.infrastructure.collectors.cdp import cdp_connection, check_session
from src.infrastructure.config.settings import CollectorConfig

logger = logging.getLogger(__name__)


class ThreadsCollector:
    """Threads 알고리즘 피드 수집기 (CDP 기반)."""

    FEED_URL = "https://www.threads.net/"
    GRAPHQL_PATTERNS = ["api/graphql", "graphql"]

    def __init__(self, config: CollectorConfig, cdp_port: int = 9222):
        self._config = config
        self._cdp_url = f"http://127.0.0.1:{cdp_port}"

    @property
    def source_name(self) -> str:
        return "threads"

    async def is_session_valid(self) -> bool:
        return await check_session(
            self._cdp_url, "threads", self.FEED_URL, ["login"]
        )

    async def collect(self) -> list[Post]:
        """GraphQL 인터셉트 + DOM 파싱 하이브리드 방식으로 수집."""
        async with cdp_connection(self._cdp_url, "threads") as (pw, context):
            page = await context.new_page()
            captured_data: list[dict[str, Any]] = []

            async def on_response(response):
                try:
                    url = response.url
                    if any(p in url for p in self.GRAPHQL_PATTERNS) and response.status == 200:
                        content_type = response.headers.get("content-type", "")
                        if "json" in content_type or "application" in content_type:
                            data = await response.json()
                            captured_data.append(data)
                except Exception:
                    pass

            page.on("response", on_response)

            try:
                await page.goto(self.FEED_URL, wait_until="networkidle", timeout=30000)

                if "login" in page.url:
                    raise SessionExpiredError("threads — Chrome에서 Threads에 로그인 해주세요")

                for _ in range(self._config.scroll_rounds):
                    await page.mouse.wheel(0, random.randint(600, 1200))
                    await asyncio.sleep(
                        random.uniform(self._config.scroll_delay_min, self._config.scroll_delay_max)
                    )

                posts = self._parse_graphql_data(captured_data)

                if len(posts) < 5:
                    dom_posts = await self._collect_via_dom(page)
                    existing_ids = {p.external_id for p in posts}
                    for dp in dom_posts:
                        if dp.external_id not in existing_ids:
                            posts.append(dp)

                logger.info(f"[threads] {len(posts)}건 수집 완료")
                return posts

            finally:
                await page.close()

    # ─── GraphQL 파싱 ───

    def _parse_graphql_data(self, responses: list[dict[str, Any]]) -> list[Post]:
        posts: list[Post] = []
        seen_ids: set[str] = set()

        for data in responses:
            try:
                items = self._find_thread_items(data)
                for item in items:
                    post = self._parse_thread_item(item)
                    if post and post.external_id not in seen_ids:
                        seen_ids.add(post.external_id)
                        posts.append(post)
            except Exception as e:
                logger.debug(f"[threads] GraphQL 파싱 오류: {e}")

        return posts

    def _find_thread_items(self, data: Any, depth: int = 0) -> list[dict[str, Any]]:
        if depth > 10:
            return []

        items: list[dict[str, Any]] = []

        if isinstance(data, dict):
            if "thread_items" in data:
                items.extend(data["thread_items"])
            if "threads" in data and isinstance(data["threads"], list):
                for thread in data["threads"]:
                    if isinstance(thread, dict) and "thread_items" in thread:
                        items.extend(thread["thread_items"])
            if "text" in data and "pk" in data:
                items.append({"post": data})

            for val in data.values():
                items.extend(self._find_thread_items(val, depth + 1))

        elif isinstance(data, list):
            for item in data:
                items.extend(self._find_thread_items(item, depth + 1))

        return items

    def _parse_thread_item(self, item: dict[str, Any]) -> Optional[Post]:
        try:
            post_data = item.get("post", item)
            if not post_data or not isinstance(post_data, dict):
                return None

            pk = str(post_data.get("pk", post_data.get("id", "")))
            code = post_data.get("code", "")
            if not pk and not code:
                return None

            caption = post_data.get("caption", {})
            if isinstance(caption, dict):
                text = caption.get("text", "")
            elif isinstance(caption, str):
                text = caption
            else:
                text = str(post_data.get("text", ""))

            if not text:
                return None

            user = post_data.get("user", {}) or post_data.get("text_post_app_info", {}).get(
                "share_info", {}
            )
            username = user.get("username", "")
            display_name = user.get("full_name", username)

            likes = post_data.get("like_count", 0)
            replies = (
                post_data.get("text_post_app_info", {}).get("direct_reply_count", 0)
                or post_data.get("reply_count", 0)
            )
            reposts = post_data.get("repost_count", 0) or post_data.get(
                "text_post_app_info", {}
            ).get("repost_count", 0)

            media_urls: list[str] = []
            carousel = post_data.get("carousel_media", [])
            if carousel:
                for media in carousel:
                    candidates = media.get("image_versions2", {}).get("candidates", [])
                    if candidates:
                        media_urls.append(candidates[0].get("url", ""))
            else:
                candidates = post_data.get("image_versions2", {}).get("candidates", [])
                if candidates:
                    media_urls.append(candidates[0].get("url", ""))

            taken_at = post_data.get("taken_at")
            published_at = datetime.utcfromtimestamp(taken_at) if taken_at else None

            url = f"https://www.threads.net/@{username}/post/{code}" if code else ""

            return Post(
                source="threads",
                external_id=f"th_{pk}",
                url=url,
                author=display_name,
                author_url=f"https://www.threads.net/@{username}" if username else None,
                content_text=text,
                media_urls=[u for u in media_urls if u],
                engagement_likes=likes or 0,
                engagement_reposts=reposts or 0,
                engagement_comments=replies or 0,
                published_at=published_at,
                collected_at=datetime.utcnow(),
                raw_data=post_data,
            )
        except Exception as e:
            logger.debug(f"[threads] thread item 파싱 실패: {e}")
            return None

    # ─── DOM 폴백 ───

    async def _collect_via_dom(self, page) -> list[Post]:
        posts: list[Post] = []

        try:
            containers = await page.query_selector_all(
                'div[data-pressable-container="true"]'
            )
            if not containers:
                containers = await page.query_selector_all("article")

            for container in containers[:30]:
                try:
                    text_elements = await container.query_selector_all("span")
                    texts = []
                    for el in text_elements:
                        t = await el.inner_text()
                        if t and len(t) > 10:
                            texts.append(t)

                    if not texts:
                        continue

                    content_text = max(texts, key=len)

                    links = await container.query_selector_all("a[href*='/post/']")
                    post_url = ""
                    code = ""
                    for link in links:
                        href = await link.get_attribute("href") or ""
                        if "/post/" in href:
                            post_url = f"https://www.threads.net{href}" if href.startswith("/") else href
                            code = href.split("/post/")[-1].split("?")[0]
                            break

                    if not code:
                        code = str(hash(content_text))[:12]

                    author_links = await container.query_selector_all("a[href*='/@']")
                    author = ""
                    for al in author_links:
                        author = await al.inner_text()
                        if author:
                            break

                    posts.append(
                        Post(
                            source="threads",
                            external_id=f"th_dom_{code}",
                            url=post_url,
                            author=author,
                            content_text=content_text,
                            collected_at=datetime.utcnow(),
                        )
                    )
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"[threads] DOM 수집 오류: {e}")

        return posts
