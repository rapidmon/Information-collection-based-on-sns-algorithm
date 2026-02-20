"""X (Twitter) 수집기.

사용자의 실행 중인 Chrome 브라우저에 CDP로 연결하여
GraphQL 응답을 인터셉트하는 방식으로 타임라인을 수집한다.
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


class TwitterCollector:
    """X(Twitter) CDP 기반 수집기. 실행 중인 Chrome에 연결."""

    FEED_URL = "https://x.com/home"
    TIMELINE_PATTERNS = ["HomeTimeline", "HomeLatestTimeline"]

    def __init__(self, config: CollectorConfig, cdp_port: int = 9222):
        self._config = config
        self._cdp_url = f"http://127.0.0.1:{cdp_port}"

    @property
    def source_name(self) -> str:
        return "twitter"

    async def is_session_valid(self) -> bool:
        return await check_session(
            self._cdp_url, "twitter", self.FEED_URL, ["login", "flow"]
        )

    async def collect(self) -> list[Post]:
        """Chrome CDP로 연결하여 GraphQL 인터셉트 방식으로 타임라인을 수집한다."""
        async with cdp_connection(self._cdp_url, "twitter") as (pw, context):
            page = await context.new_page()
            captured: list[dict[str, Any]] = []

            async def on_response(response):
                try:
                    if any(p in response.url for p in self.TIMELINE_PATTERNS):
                        if response.status == 200:
                            data = await response.json()
                            captured.append(data)
                except Exception:
                    pass

            page.on("response", on_response)

            try:
                await page.goto(self.FEED_URL, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3000)  # 타임라인 API 응답 대기

                if "login" in page.url or "flow" in page.url:
                    raise SessionExpiredError("twitter — Chrome에서 X에 로그인 해주세요")

                for _ in range(self._config.scroll_rounds):
                    await page.mouse.wheel(0, random.randint(800, 1500))
                    await asyncio.sleep(
                        random.uniform(self._config.scroll_delay_min, self._config.scroll_delay_max)
                    )

                posts = self._parse_graphql_responses(captured)
                logger.info(f"[twitter] GraphQL 인터셉트: {len(posts)}건 수집")
                return posts

            finally:
                await page.close()

    # ─── GraphQL 파싱 ───

    def _parse_graphql_responses(self, responses: list[dict[str, Any]]) -> list[Post]:
        posts: list[Post] = []
        seen_ids: set[str] = set()

        for resp_data in responses:
            try:
                entries = self._extract_timeline_entries(resp_data)
                for entry in entries:
                    post = self._parse_tweet_entry(entry)
                    if post and post.external_id not in seen_ids:
                        seen_ids.add(post.external_id)
                        posts.append(post)
            except Exception as e:
                logger.debug(f"[twitter] GraphQL 응답 파싱 오류: {e}")

        return posts

    def _extract_timeline_entries(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        entries = []
        try:
            instructions = (
                data.get("data", {})
                .get("home", {})
                .get("home_timeline_urt", {})
                .get("instructions", [])
            )
            if not instructions:
                inner = data.get("data", {})
                for k, v in inner.items():
                    if isinstance(v, dict) and "instructions" in v:
                        instructions = v["instructions"]
                        break
                    if isinstance(v, dict):
                        for k2, v2 in v.items():
                            if isinstance(v2, dict) and "instructions" in v2:
                                instructions = v2["instructions"]
                                break

            for instruction in instructions:
                if instruction.get("type") == "TimelineAddEntries":
                    entries.extend(instruction.get("entries", []))
                elif "entries" in instruction:
                    entries.extend(instruction["entries"])
        except Exception:
            pass
        return entries

    def _parse_tweet_entry(self, entry: dict[str, Any]) -> Optional[Post]:
        try:
            content = entry.get("content", {})
            item_content = content.get("itemContent", {})
            tweet_results = item_content.get("tweet_results", {})
            result = tweet_results.get("result", {})

            if item_content.get("promotedMetadata"):
                return None

            if result.get("__typename") == "TweetWithVisibilityResults":
                result = result.get("tweet", result)

            core = result.get("core", {})
            user_results = core.get("user_results", {}).get("result", {})
            legacy = result.get("legacy", {})

            if not legacy:
                return None

            tweet_id = legacy.get("id_str") or result.get("rest_id", "")
            if not tweet_id:
                return None

            user_legacy = user_results.get("legacy", {})
            screen_name = user_legacy.get("screen_name", "")
            display_name = user_legacy.get("name", screen_name)

            full_text = legacy.get("full_text", "")
            if not full_text:
                return None

            media_urls = []
            entities = legacy.get("entities", {})
            extended = legacy.get("extended_entities", {})
            for media in extended.get("media", entities.get("media", [])):
                url = media.get("media_url_https") or media.get("media_url", "")
                if url:
                    media_urls.append(url)

            likes = legacy.get("favorite_count", 0)
            retweets = legacy.get("retweet_count", 0)
            replies = legacy.get("reply_count", 0)
            views_data = result.get("views", {})
            views = int(views_data.get("count", 0)) if views_data.get("count") else 0

            created_at_str = legacy.get("created_at", "")
            published_at = None
            if created_at_str:
                try:
                    published_at = datetime.strptime(created_at_str, "%a %b %d %H:%M:%S %z %Y")
                except ValueError:
                    pass

            return Post(
                source="twitter",
                external_id=f"tw_{tweet_id}",
                url=f"https://x.com/{screen_name}/status/{tweet_id}",
                author=display_name,
                author_url=f"https://x.com/{screen_name}" if screen_name else None,
                content_text=full_text,
                media_urls=media_urls,
                engagement_likes=likes,
                engagement_reposts=retweets,
                engagement_comments=replies,
                engagement_views=views,
                published_at=published_at,
                collected_at=datetime.utcnow(),
                raw_data=result,
            )
        except Exception as e:
            logger.debug(f"[twitter] 트윗 파싱 실패: {e}")
            return None
