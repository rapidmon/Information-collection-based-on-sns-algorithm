"""LinkedIn 수집기.

사용자의 실행 중인 Chrome에 CDP로 연결하여 DOM 파싱으로 피드를 수집한다.
LinkedIn 2025+ 리디자인 대응: 해시 클래스명 대신 data-testid, aria-label 등 안정적 속성 사용.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import re
from datetime import datetime, timedelta
from typing import Optional

from src.domain.entities import Post
from src.domain.exceptions import SessionExpiredError
from src.infrastructure.collectors.cdp import auto_login, cdp_connection, check_session, get_or_create_page, minimize_window
from src.infrastructure.config.settings import CollectorConfig, SnsCredentials

logger = logging.getLogger(__name__)


class LinkedInCollector:
    """LinkedIn 알고리즘 피드 수집기 (CDP 기반)."""

    FEED_URL = "https://www.linkedin.com/feed/"

    def __init__(
        self,
        config: CollectorConfig,
        credentials: SnsCredentials | None = None,
        cdp_port: int = 9222,
    ):
        self._config = config
        self._credentials = credentials or SnsCredentials()
        self._cdp_url = f"http://127.0.0.1:{cdp_port}"

    @property
    def source_name(self) -> str:
        return "linkedin"

    async def is_session_valid(self) -> bool:
        return await check_session(
            self._cdp_url, "linkedin", self.FEED_URL,
            ["login", "authwall", "checkpoint"],
            match="linkedin.com",
        )

    async def login(self) -> bool:
        """CDP로 LinkedIn 자동 로그인을 시도한다."""
        if not self._credentials.is_configured:
            logger.warning("[linkedin] 자격증명 미설정 — 자동 로그인 불가")
            return False

        logger.info("[linkedin] 자동 로그인 시도")
        return await auto_login(
            cdp_url=self._cdp_url,
            source_name="linkedin",
            username=self._credentials.username,
            password=self._credentials.password,
            login_url="https://www.linkedin.com/login",
            username_selector="#username",
            password_selector="#password",
            submit_selector='button[type="submit"], button:has-text("Sign in"), button:has-text("로그인")',
            invalid_keywords=["login", "authwall", "checkpoint"],
            initial_wait_ms=2000,
            submit_wait_ms=5000,
        )

    async def collect(self) -> list[Post]:
        """DOM 파싱으로 LinkedIn 피드를 수집."""
        async with cdp_connection(self._cdp_url, "linkedin") as (pw, context):
            page = await get_or_create_page(context, "linkedin.com")  # 기존 LinkedIn 탭 재사용
            await minimize_window(page)

            try:
                # 재사용 탭이 이미 피드면 reload로 최신 글을 받고, 개별 게시물 페이지 등이면 goto.
                cur = page.url or ""
                if "linkedin.com/feed" in cur and "/update/" not in cur:
                    await page.reload(wait_until="domcontentloaded", timeout=60000)
                else:
                    await page.goto(self.FEED_URL, wait_until="domcontentloaded", timeout=60000)

                if any(kw in page.url for kw in ["login", "authwall", "checkpoint", "security"]):
                    raise SessionExpiredError("linkedin — Chrome에서 LinkedIn에 로그인 해주세요")

                await asyncio.sleep(random.uniform(2.0, 4.0))
                await page.mouse.move(random.randint(100, 800), random.randint(100, 600))

                posts: list[Post] = []
                seen_ids: set[str] = set()

                for round_num in range(self._config.scroll_rounds):
                    # 새 DOM: mainFeed 안의 포스트 항목들
                    feed_items = await self._get_feed_items(page)

                    for item in feed_items:
                        post = await self._parse_feed_update(item, page)
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
                pass  # 탭을 닫지 않고 남겨 다음 수집에 재사용

    async def _get_feed_items(self, page) -> list:
        """피드에서 실제 게시물 항목만 추출한다."""
        # mainFeed 컨테이너의 직접 자식 중 본문 텍스트가 있는 것만 선택
        items = await page.evaluate(
            """() => {
                const feed = document.querySelector('[data-testid="mainFeed"]');
                if (!feed) return [];
                const indices = [];
                for (let i = 0; i < feed.children.length; i++) {
                    const child = feed.children[i];
                    // expandable-text-box가 있으면 실제 게시물
                    const hasText = child.querySelector('[data-testid="expandable-text-box"]');
                    if (hasText) {
                        indices.push(i);
                    }
                }
                return indices;
            }"""
        )
        # 인덱스 기반으로 ElementHandle 획득
        result = []
        feed = await page.query_selector('[data-testid="mainFeed"]')
        if not feed:
            # 폴백: 이전 선택자 시도
            legacy = await page.query_selector_all(".feed-shared-update-v2")
            if legacy:
                return legacy
            return []

        children = await feed.query_selector_all(":scope > div")
        for idx in items:
            if idx < len(children):
                result.append(children[idx])
        return result

    async def _parse_feed_update(self, element, page) -> Post | None:
        try:
            # 본문 텍스트: data-testid="expandable-text-box"
            content_text = await self._extract_content(element)
            if not content_text:
                return None

            # 게시일 추출 (상대 시간 → datetime). 너무 오래된 건 빠르게 컷오프
            published_at = await self._extract_published_at(element)
            if published_at:
                cutoff = datetime.utcnow() - timedelta(days=self._config.max_age_days)
                if published_at < cutoff:
                    return None

            # 작성자 이름: "~님의 게시물에 대한 관리 메뉴 열기" 버튼의 aria-label에서 추출
            author = await self._extract_author(element)

            # 작성자 프로필 URL
            author_url = await self._extract_author_url(element)

            # 포스트 URN 추출: 관리 메뉴 → embed 링크에서 실제 URN 획득
            post_urn = await self._extract_post_urn(element, page)

            # URN에서 ID와 URL 생성
            if post_urn:
                # urn:li:ugcPost:7434660713215885312 형태
                post_id = post_urn.split(":")[-1]
                external_id = f"li_{post_id}"
                post_url = f"https://www.linkedin.com/feed/update/{post_urn}/"
            else:
                # 폴백: 본문 해시 (URL은 작성자 프로필로 대체)
                fallback_id = hashlib.md5(content_text[:200].encode()).hexdigest()[:16]
                external_id = f"li_{fallback_id}"
                post_url = author_url or self.FEED_URL

            # 인게이지먼트: innerText에서 "반응 N", "댓글 N", "퍼온글 N" 패턴 추출
            likes, comments, reposts = await self._extract_engagement(element)

            return Post(
                source="linkedin",
                external_id=external_id,
                url=post_url,
                author=author,
                author_url=author_url or None,
                content_text=content_text,
                engagement_likes=likes,
                engagement_reposts=reposts,
                engagement_comments=comments,
                published_at=published_at,
                collected_at=datetime.utcnow(),
            )
        except Exception as e:
            logger.debug(f"[linkedin] 피드 항목 파싱 실패: {e}")
            return None

    async def _extract_published_at(self, element) -> Optional[datetime]:
        """게시물 작성 시간을 추출. <time>의 datetime 속성 우선, 없으면 상대 시간 파싱."""
        # 1순위: <time> 태그의 datetime 속성 (절대 시간)
        try:
            time_el = await element.query_selector("time[datetime]")
            if time_el:
                dt_attr = await time_el.get_attribute("datetime") or ""
                if dt_attr:
                    try:
                        return datetime.fromisoformat(dt_attr.replace("Z", "+00:00")).replace(tzinfo=None)
                    except ValueError:
                        pass
        except Exception:
            pass

        # 2순위: 작성자 영역의 sub-description 텍스트에서 상대 시간 파싱
        sub_selectors = [
            ".update-components-actor__sub-description",
            ".feed-shared-actor__sub-description",
            'a[href*="/feed/update/"] span[aria-hidden="true"]',
        ]
        for sel in sub_selectors:
            try:
                el = await element.query_selector(sel)
                if el:
                    text = (await el.inner_text()).strip()
                    dt = _parse_relative_time(text)
                    if dt:
                        return dt
            except Exception:
                continue

        return None

    async def _extract_post_urn(self, element, page) -> str:
        """관리 메뉴의 '게시물 삽입' 링크에서 실제 포스트 URN을 추출한다."""
        # 1순위: data-urn (레거시)
        urn = await element.get_attribute("data-urn") or ""
        if "urn:li:" in urn:
            return urn

        # 2순위: 관리 메뉴 열기 → embed 링크에서 URN 추출
        try:
            menu_btn = await element.query_selector(
                'button[aria-label*="관리 메뉴"], button[aria-label*="control menu"]'
            )
            if menu_btn:
                await menu_btn.click()
                await asyncio.sleep(0.8)

                # "게시물 삽입" 링크에서 targetUrn 파라미터 추출
                embed_links = await page.query_selector_all('a[href*="embed-modal"]')
                for link in embed_links:
                    href = await link.get_attribute("href") or ""
                    match = re.search(
                        r"targetUrn=urn%3Ali%3A(ugcPost|activity)%3A(\d+)", href
                    )
                    if match:
                        post_type = match.group(1)
                        post_id = match.group(2)
                        await page.keyboard.press("Escape")
                        await asyncio.sleep(0.3)
                        return f"urn:li:{post_type}:{post_id}"

                # 메뉴 닫기
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)
        except Exception as e:
            logger.debug(f"[linkedin] 관리 메뉴 URN 추출 실패: {e}")
            # 메뉴가 열려있을 수 있으므로 닫기 시도
            try:
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)
            except Exception:
                pass

        return ""

    async def _extract_author(self, element) -> str:
        """작성자 이름을 추출한다."""
        # 1순위: "~님의 게시물에 대한 관리 메뉴 열기" aria-label
        try:
            mgmt_btn = await element.query_selector(
                'button[aria-label*="게시물에 대한 관리"]'
            )
            if mgmt_btn:
                label = await mgmt_btn.get_attribute("aria-label") or ""
                # "Douglas Guen 님의 게시물에 대한 관리 메뉴 열기" → "Douglas Guen"
                match = re.match(r"(.+?)\s*님의 게시물", label)
                if match:
                    return match.group(1).strip()

            # 영어 UI 대응: "Open control menu for post by ~"
            mgmt_btn_en = await element.query_selector(
                'button[aria-label*="control menu for post"]'
            )
            if mgmt_btn_en:
                label = await mgmt_btn_en.get_attribute("aria-label") or ""
                match = re.match(r"Open control menu for post by (.+)", label)
                if match:
                    return match.group(1).strip()
        except Exception:
            pass

        # 2순위: "~님의 프로필 보기" aria-label (첫 번째)
        try:
            profile_svgs = await element.query_selector_all('[aria-label*="프로필 보기"]')
            for svg in profile_svgs:
                label = await svg.get_attribute("aria-label") or ""
                match = re.match(r"(.+?)님의 프로필 보기", label)
                if match:
                    return match.group(1).strip()
        except Exception:
            pass

        # 3순위: "회사 보기: ~" (기업 게시물)
        try:
            company_el = await element.query_selector('[aria-label*="회사 보기"]')
            if company_el:
                label = await company_el.get_attribute("aria-label") or ""
                match = re.match(r"회사 보기:\s*(.+)", label)
                if match:
                    return match.group(1).strip()
        except Exception:
            pass

        # 4순위: 레거시 선택자
        try:
            actor_el = await element.query_selector(
                ".update-components-actor__name span:first-child"
            )
            if not actor_el:
                actor_el = await element.query_selector(".feed-shared-actor__name")
            if actor_el:
                text = (await actor_el.inner_text()).strip()
                return text.split("\n")[0].strip()
        except Exception:
            pass

        return ""

    async def _extract_author_url(self, element) -> str:
        """작성자 프로필 URL을 추출한다."""
        # /in/username 또는 /company/name 링크 찾기
        links = await element.query_selector_all('a[href*="/in/"], a[href*="/company/"]')
        for link in links:
            href = await link.get_attribute("href") or ""
            if "/in/" in href or "/company/" in href:
                if href.startswith("/"):
                    return f"https://www.linkedin.com{href}"
                return href
        return ""

    async def _extract_content(self, element) -> str:
        """본문 텍스트를 추출한다."""
        # 1순위: data-testid="expandable-text-box"
        text_el = await element.query_selector('[data-testid="expandable-text-box"]')

        # 2순위: 레거시 선택자들
        if not text_el:
            text_el = await element.query_selector(".feed-shared-update-v2__description")
        if not text_el:
            text_el = await element.query_selector(".feed-shared-text")
        if not text_el:
            text_el = await element.query_selector('span[dir="ltr"]')

        if not text_el:
            return ""

        content = (await text_el.inner_text()).strip()

        # "...더 보기" 버튼 클릭 시도
        try:
            see_more = await element.query_selector(
                'button[aria-label*="더 보기"], button[aria-label*="see more"], '
                'button[aria-label*="See more"]'
            )
            if see_more:
                await see_more.click()
                await asyncio.sleep(0.5)
                # 다시 읽기
                text_el2 = await element.query_selector('[data-testid="expandable-text-box"]')
                if text_el2:
                    content = (await text_el2.inner_text()).strip()
        except Exception:
            pass

        return content

    async def _extract_engagement(self, element) -> tuple[int, int, int]:
        """인게이지먼트(반응, 댓글, 퍼온글) 수를 추출한다."""
        likes = 0
        comments = 0
        reposts = 0

        try:
            full_text = await element.inner_text()

            # 한국어: "반응 39", "댓글 1", "퍼온글 1"
            likes_match = re.search(r"반응\s+([\d,.]+(?:k|K)?)", full_text)
            if likes_match:
                likes = self._parse_count(likes_match.group(1))

            comments_match = re.search(r"댓글\s+([\d,.]+(?:k|K)?)", full_text)
            if comments_match:
                comments = self._parse_count(comments_match.group(1))

            reposts_match = re.search(r"퍼온글\s+([\d,.]+(?:k|K)?)", full_text)
            if reposts_match:
                reposts = self._parse_count(reposts_match.group(1))

            # 영어 UI: "39 reactions", "1 comment", "1 repost"
            if not likes_match:
                en_likes = re.search(r"([\d,.]+(?:k|K)?)\s+reaction", full_text)
                if en_likes:
                    likes = self._parse_count(en_likes.group(1))

            if not comments_match:
                en_comments = re.search(r"([\d,.]+(?:k|K)?)\s+comment", full_text)
                if en_comments:
                    comments = self._parse_count(en_comments.group(1))

            if not reposts_match:
                en_reposts = re.search(r"([\d,.]+(?:k|K)?)\s+repost", full_text)
                if en_reposts:
                    reposts = self._parse_count(en_reposts.group(1))
        except Exception:
            pass

        # 폴백: 레거시 CSS 선택자
        if likes == 0:
            likes = await self._extract_count_legacy(
                element, ".social-details-social-counts__reactions-count"
            )
        if comments == 0:
            comments = await self._extract_count_legacy(
                element, "button.social-details-social-counts__comments"
            )
        if reposts == 0:
            reposts = await self._extract_count_legacy(
                element, "button.social-details-social-counts__reposts"
            )

        return likes, comments, reposts

    @staticmethod
    def _parse_count(text: str) -> int:
        """숫자 텍스트를 정수로 변환한다. (예: "2.5K" → 2500)"""
        text = text.strip().replace(",", "").replace(" ", "").lower()
        if "k" in text:
            return int(float(text.replace("k", "")) * 1000)
        if "m" in text:
            return int(float(text.replace("m", "")) * 1_000_000)
        digits = "".join(c for c in text if c.isdigit())
        return int(digits) if digits else 0

    async def _extract_count_legacy(self, parent, selector: str) -> int:
        """레거시 CSS 선택자로 인게이지먼트 수를 추출한다."""
        try:
            el = await parent.query_selector(selector)
            if el:
                text = (await el.inner_text()).strip()
                return self._parse_count(text)
        except Exception:
            pass
        return 0


# 한국어/영어 상대 시간 패턴 (긴 단위부터 매칭해 모호성 회피)
_RELATIVE_TIME_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(\d+)\s*년"), "years"),
    (re.compile(r"(\d+)\s*개월"), "months"),
    (re.compile(r"(\d+)\s*달"), "months"),
    (re.compile(r"(\d+)\s*주"), "weeks"),
    (re.compile(r"(\d+)\s*일"), "days"),
    (re.compile(r"(\d+)\s*시간"), "hours"),
    (re.compile(r"(\d+)\s*분"), "minutes"),
    (re.compile(r"(\d+)\s*초"), "seconds"),
    (re.compile(r"\b(\d+)\s*y(?:r|ear)?s?\b", re.I), "years"),
    (re.compile(r"\b(\d+)\s*mo(?:nth)?s?\b", re.I), "months"),
    (re.compile(r"\b(\d+)\s*w(?:eek)?s?\b", re.I), "weeks"),
    (re.compile(r"\b(\d+)\s*d(?:ay)?s?\b", re.I), "days"),
    (re.compile(r"\b(\d+)\s*h(?:our|r)?s?\b", re.I), "hours"),
    (re.compile(r"\b(\d+)\s*m(?:in|inute)?s?\b", re.I), "minutes"),
    (re.compile(r"\b(\d+)\s*s(?:ec|econd)?s?\b", re.I), "seconds"),
]


def _parse_relative_time(text: str) -> Optional[datetime]:
    """'2일', '1주', '3시간', '5h' 등을 datetime(naive UTC)으로 변환."""
    if not text:
        return None
    if "방금" in text or "just now" in text.lower():
        return datetime.utcnow()

    now = datetime.utcnow()
    for pattern, unit in _RELATIVE_TIME_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        n = int(m.group(1))
        if unit == "years":
            return now - timedelta(days=n * 365)
        if unit == "months":
            return now - timedelta(days=n * 30)
        return now - timedelta(**{unit: n})
    return None
