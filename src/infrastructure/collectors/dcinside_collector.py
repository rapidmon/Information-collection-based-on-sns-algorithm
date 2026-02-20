"""DCInside 특이점이온다 갤러리 수집기.

CDP로 사용자의 Chrome에 연결하여 이미 열려있는 개념글 탭을 읽거나,
없으면 새 탭으로 개념글 페이지를 열어 수집한다.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup, Tag
from playwright.async_api import async_playwright

from src.domain.entities import Post
from src.infrastructure.collectors.base import BaseCollector
from src.infrastructure.config.settings import CollectorConfig

logger = logging.getLogger(__name__)

# 개념글 URL 패턴
_RECOMMEND_URL_DESKTOP = (
    "https://gall.dcinside.com/mgallery/board/lists/"
    "?id={gallery_id}&exception_mode=recommend"
)
_RECOMMEND_URL_MOBILE = (
    "https://m.dcinside.com/board/{gallery_id}?recommend=1"
)


class DCInsideCollector(BaseCollector):
    """DCInside 마이너 갤러리 개념글 수집기 (CDP 기반)."""

    def __init__(self, config: CollectorConfig, cdp_port: int = 9222):
        self._config = config
        self._gallery_id = config.gallery_id
        self._pages = config.pages_to_scrape
        self._cdp_url = f"http://127.0.0.1:{cdp_port}"

    @property
    def source_name(self) -> str:
        return "dcinside"

    async def collect(self) -> list[Post]:
        """Chrome에서 개념글 탭을 찾거나 새로 열어서 수집."""
        pw = await async_playwright().start()

        try:
            browser = await pw.chromium.connect_over_cdp(self._cdp_url)
        except Exception as e:
            await pw.stop()
            logger.error(f"[dcinside] Chrome 연결 실패: {e}")
            raise

        context = browser.contexts[0]
        posts: list[Post] = []
        seen_ids: set[str] = set()

        try:
            # 이미 열려있는 개념글 탭 찾기
            page = self._find_gallery_tab(context)

            if page:
                logger.info(f"[dcinside] 기존 탭 발견: {page.url}")
                # 기존 탭 새로고침하여 최신 데이터 로드
                await page.reload(wait_until="domcontentloaded", timeout=30000)
            else:
                # 새 탭으로 개념글 페이지 열기
                recommend_url = _RECOMMEND_URL_MOBILE.format(gallery_id=self._gallery_id)
                page = await context.new_page()
                await page.goto(recommend_url, wait_until="domcontentloaded", timeout=30000)
                logger.info(f"[dcinside] 새 탭으로 개념글 페이지 열기: {recommend_url}")

            await asyncio.sleep(random.uniform(1.0, 2.0))

            # 현재 페이지 파싱
            html = await page.content()
            page_posts = self._parse_list_page(html, seen_ids)
            posts.extend(page_posts)

            # 추가 페이지 로드 (스크롤 또는 페이지 이동)
            for page_num in range(2, self._pages + 1):
                await asyncio.sleep(random.uniform(1.5, 3.0))

                # 다음 페이지로 이동
                next_url = _RECOMMEND_URL_MOBILE.format(gallery_id=self._gallery_id)
                next_url += f"&page={page_num}"
                await page.goto(next_url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(random.uniform(1.0, 2.0))

                html = await page.content()
                page_posts = self._parse_list_page(html, seen_ids)
                posts.extend(page_posts)
                logger.debug(f"[dcinside] 페이지 {page_num}: {len(page_posts)}건")

            # 상세 페이지에서 본문 가져오기
            for post in posts:
                if post.content_text and post.url:
                    try:
                        await asyncio.sleep(random.uniform(0.8, 1.5))
                        detail_url = self._to_mobile_detail_url(post.url)
                        await page.goto(detail_url, wait_until="domcontentloaded", timeout=20000)
                        html = await page.content()
                        content_text, media_urls = self._parse_detail_page(html)
                        if content_text:
                            title = post.content_text
                            post.content_text = f"{title}\n\n{content_text}"
                            post.media_urls = media_urls
                    except Exception as e:
                        logger.debug(f"[dcinside] 상세 페이지 로드 실패: {e}")

            logger.info(f"[dcinside] 총 {len(posts)}건 수집 완료")

        finally:
            await pw.stop()

        return posts

    def _find_gallery_tab(self, context) -> Optional[object]:
        """이미 열려있는 DCInside 갤러리 탭을 찾는다."""
        for page in context.pages:
            url = page.url
            if self._gallery_id in url and ("dcinside" in url):
                return page
        return None

    def _parse_list_page(self, html: str, seen_ids: set[str]) -> list[Post]:
        """목록 페이지 HTML에서 게시물 리스트를 추출."""
        soup = BeautifulSoup(html, "lxml")
        posts: list[Post] = []

        article_list = soup.select("ul.gall-detail-lst li")
        if not article_list:
            article_list = soup.select("div.gall-detail-lnktb ul li")

        for item in article_list:
            try:
                post = self._parse_list_item(item, seen_ids)
                if post:
                    posts.append(post)
            except Exception as e:
                logger.debug(f"[dcinside] 항목 파싱 실패: {e}")

        return posts

    def _parse_list_item(self, item: Tag, seen_ids: set[str]) -> Optional[Post]:
        """목록 아이템에서 게시물 정보 추출."""
        class_list = item.get("class", [])
        if isinstance(class_list, list) and any(
            c in class_list for c in ["ad", "adv", "notice"]
        ):
            return None

        link_tag = item.select_one("a.lt") or item.select_one("a")
        if not link_tag:
            return None

        href = link_tag.get("href", "")
        if not href:
            return None

        post_no = self._extract_post_no(href)
        if not post_no:
            return None

        external_id = f"dc_{self._gallery_id}_{post_no}"
        if external_id in seen_ids:
            return None
        seen_ids.add(external_id)

        subject_tag = link_tag.select_one(".subjectin") or link_tag
        title = subject_tag.get_text(strip=True)
        if not title:
            return None

        author = ""
        author_tag = item.select_one(".ginfo .nick") or item.select_one(".name")
        if author_tag:
            author = author_tag.get_text(strip=True)

        comment_count = 0
        comment_tag = item.select_one(".ct")
        if comment_tag:
            ct_text = comment_tag.get_text(strip=True).strip("[]")
            if ct_text.isdigit():
                comment_count = int(ct_text)

        desktop_url = (
            f"https://gall.dcinside.com/mgallery/board/view/"
            f"?id={self._gallery_id}&no={post_no}"
        )

        return Post(
            source="dcinside",
            external_id=external_id,
            url=desktop_url,
            author=author,
            content_text=title,
            engagement_comments=comment_count,
            collected_at=datetime.utcnow(),
        )

    def _parse_detail_page(self, html: str) -> tuple[str, list[str]]:
        """상세 페이지에서 본문과 이미지를 추출."""
        soup = BeautifulSoup(html, "lxml")

        content_div = (
            soup.select_one(".thum-txtin")
            or soup.select_one(".writing_view_box")
            or soup.select_one("#viewContent")
        )

        if not content_div:
            return "", []

        content_text = content_div.get_text(separator="\n", strip=True)

        media_urls = []
        for img in content_div.select("img"):
            src = img.get("src") or img.get("data-src") or ""
            if src and ("dcimg" in src or "dcinside" in src or src.startswith("http")):
                media_urls.append(src)

        return content_text, media_urls

    def _to_mobile_detail_url(self, desktop_url: str) -> str:
        """데스크톱 URL을 모바일 상세 URL로 변환."""
        match = re.search(r"[?&]no=(\d+)", desktop_url)
        if match:
            return f"https://m.dcinside.com/board/{self._gallery_id}/{match.group(1)}"
        return desktop_url

    def _extract_post_no(self, href: str) -> Optional[str]:
        """URL에서 게시물 번호를 추출."""
        match = re.search(r"/board/[^/]+/(\d+)", href)
        if match:
            return match.group(1)
        match = re.search(r"[?&]no=(\d+)", href)
        if match:
            return match.group(1)
        return None
