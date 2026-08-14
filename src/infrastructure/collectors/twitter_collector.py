"""X (Twitter) 수집기.

사용자의 실행 중인 Chrome 브라우저에 CDP로 연결하여
GraphQL 응답을 인터셉트하는 방식으로 타임라인을 수집한다.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta
from typing import Any, Optional

from src.domain.entities import Post
from src.domain.exceptions import SessionExpiredError
from src.infrastructure.collectors.cdp import cdp_connection, check_session, minimize_window
from src.infrastructure.collectors.base_collector import BaseCdpCollector

logger = logging.getLogger(__name__)


class TwitterCollector(BaseCdpCollector):
    """X(Twitter) CDP 기반 수집기. 실행 중인 Chrome에 연결."""

    SOURCE = "twitter"
    FEED_URL = "https://x.com/home"
    TIMELINE_PATTERNS = ["HomeTimeline", "HomeLatestTimeline"]

    async def is_session_valid(self) -> bool:
        # 로그인 시 /home에 머물고, 로그아웃 시 x.com/ 랜딩이나 로그인 플로우로 튕긴다.
        return await check_session(
            self._cdp_url, "twitter", self.FEED_URL, ["login", "flow"],
            require_substr="/home",
            login_markers='[data-testid="loginButton"], a[href*="/i/flow/login"], a[href="/login"]',
        )

    async def login(self) -> bool:
        """CDP로 X(Twitter) 자동 로그인을 시도한다."""
        if not self._credentials.is_configured:
            logger.warning("[twitter] 자격증명 미설정 — 자동 로그인 불가")
            return False

        logger.info("[twitter] 자동 로그인 시도")
        try:
            async with cdp_connection(self._cdp_url, "twitter") as (pw, context):
                page = await context.new_page()
                try:
                    await page.goto(
                        "https://x.com/i/flow/login",
                        wait_until="domcontentloaded",
                        timeout=30000,
                    )
                    await page.wait_for_timeout(3000)

                    # username 입력
                    username_input = page.locator('input[autocomplete="username"]')
                    await username_input.fill(self._credentials.username)
                    await page.locator('button:has-text("Next"), button:has-text("다음")').click()
                    await page.wait_for_timeout(2000)

                    # password 입력
                    password_input = page.locator('input[type="password"]')
                    await password_input.fill(self._credentials.password)
                    await page.locator(
                        'button:has-text("Log in"), button:has-text("로그인")'
                    ).click()
                    await page.wait_for_timeout(5000)

                    if "login" not in page.url and "flow" not in page.url:
                        logger.info("[twitter] 자동 로그인 성공")
                        return True

                    logger.warning("[twitter] 자동 로그인 실패 — 로그인 페이지에 머무름")
                    return False
                finally:
                    await page.close()
        except Exception as e:
            logger.error(f"[twitter] 자동 로그인 오류: {e}")
            return False

    async def collect(self) -> list[Post]:
        """Chrome CDP로 연결하여 GraphQL 인터셉트 방식으로 타임라인을 수집한다."""
        async with cdp_connection(self._cdp_url, "twitter") as (pw, context):
            page = await context.new_page()  # 매 사이클 새 탭 (수집 후 닫아 메모리 회수)
            await minimize_window(page)
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

                # 로그인 상태 확인: 로그인돼 있으면 /home에 머문다. 로그아웃 시 X는
                # 랜딩(x.com/)이나 로그인 플로우로 튕기므로 url에 /home이 없으면 세션 만료.
                if "login" in page.url or "flow" in page.url or "/home" not in page.url:
                    raise SessionExpiredError("twitter — Chrome에서 X에 로그인 해주세요 (로그아웃 감지)")

                for _ in range(self._config.scroll_rounds):
                    await page.mouse.wheel(0, random.randint(800, 1500))
                    await asyncio.sleep(
                        random.uniform(self._config.scroll_delay_min, self._config.scroll_delay_max)
                    )

                posts = self._parse_graphql_responses(captured)
                logger.info(f"[twitter] GraphQL 인터셉트: {len(posts)}건 수집")
                return posts

            finally:
                await page.close()  # 탭 닫아 렌더러 메모리 회수 (누수·먹통 방지)

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

            # 작성자 식별. X가 유저 필드를 legacy → core 아래로 옮겼다(2026-08 확인:
            # user_results.result.legacy 는 빈 dict가 되고 screen_name/name 이 result.core 로 이동).
            # 구 경로만 읽던 탓에 author/author_url 이 전량 비고 URL도 익명형
            # (x.com/i/web/status/…)으로 저장됐다. 스키마가 또 바뀔 수 있어 양쪽을 본다.
            user_core = user_results.get("core") or {}
            user_legacy = user_results.get("legacy") or {}
            screen_name = user_core.get("screen_name") or user_legacy.get("screen_name", "")
            display_name = user_core.get("name") or user_legacy.get("name") or screen_name

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

            # 게시일 컷오프 — max_age_days 초과면 스킵
            if published_at:
                cutoff = datetime.now(published_at.tzinfo) - timedelta(
                    days=self._config.max_age_days
                )
                if published_at < cutoff:
                    return None

            return Post(
                source="twitter",
                external_id=f"tw_{tweet_id}",
                url=f"https://x.com/{screen_name}/status/{tweet_id}" if screen_name else f"https://x.com/i/web/status/{tweet_id}",
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
