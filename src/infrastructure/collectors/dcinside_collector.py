"""DCInside 특이점이온다 갤러리 수집기.

CDP로 사용자의 Chrome에 연결하여 이미 열려있는 개념글 탭에서
게시물 목록을 읽고, 각 게시물 상세 페이지에서 본문을 수집한다.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup, Tag

from src.domain.entities import Post
from src.infrastructure.collectors.cdp import cdp_connection
from src.infrastructure.config.settings import CollectorConfig

logger = logging.getLogger(__name__)

_RECOMMEND_URL_DESKTOP = (
    "https://gall.dcinside.com/mgallery/board/lists/"
    "?id={gallery_id}&exception_mode=recommend"
)


class DCInsideCollector:
    """DCInside 마이너 갤러리 개념글 수집기 (CDP 기반)."""

    def __init__(self, config: CollectorConfig, cdp_port: int = 9222):
        self._config = config
        self._gallery_id = config.gallery_id
        self._pages = config.pages_to_scrape
        self._cdp_url = f"http://127.0.0.1:{cdp_port}"

    @property
    def source_name(self) -> str:
        return "dcinside"

    async def is_session_valid(self) -> bool:
        return True

    async def collect(self) -> list[Post]:
        """개념글 탭에서 목록을 읽고, 각 게시물 상세에서 본문 수집."""
        async with cdp_connection(self._cdp_url, "dcinside") as (pw, context):
            posts: list[Post] = []
            seen_ids: set[str] = set()

            # 이미 열려있는 개념글 탭 찾기
            page = self._find_gallery_tab(context)

            if page:
                logger.info(f"[dcinside] 기존 탭 발견: {page.url}")
                await page.reload(wait_until="domcontentloaded", timeout=30000)
            else:
                recommend_url = _RECOMMEND_URL_DESKTOP.format(gallery_id=self._gallery_id)
                page = await context.new_page()
                await page.goto(recommend_url, wait_until="domcontentloaded", timeout=30000)
                logger.info(f"[dcinside] 새 탭으로 개념글 페이지 열기: {recommend_url}")

            await asyncio.sleep(random.uniform(1.0, 2.0))

            # 현재 페이지에서 게시물 목록 파싱 (최대 20건)
            html = await page.content()
            posts = self._parse_list_page(html, seen_ids)[:20]

            logger.info(f"[dcinside] 목록에서 {len(posts)}건 발견, 상세 페이지 수집 시작")

            # 각 게시물 상세 페이지에서 본문 가져오기
            for post in posts:
                try:
                    await asyncio.sleep(random.uniform(0.8, 1.5))
                    await page.goto(post.url, wait_until="domcontentloaded", timeout=20000)
                    html = await page.content()
                    content_text, media_urls = self._parse_detail_page(html)
                    if content_text:
                        title = post.content_text
                        post.content_text = f"{title}\n\n{content_text}"
                        post.media_urls = media_urls
                except Exception as e:
                    logger.debug(f"[dcinside] 상세 페이지 로드 실패 ({post.external_id}): {e}")

            # 개념글 탭으로 복귀
            try:
                recommend_url = _RECOMMEND_URL_DESKTOP.format(gallery_id=self._gallery_id)
                await page.goto(recommend_url, wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass

            logger.info(f"[dcinside] 총 {len(posts)}건 수집 완료")
            return posts

    def _find_gallery_tab(self, context) -> Optional[object]:
        """이미 열려있는 DCInside 갤러리 탭을 찾는다."""
        for page in context.pages:
            url = page.url
            if self._gallery_id in url and "dcinside" in url:
                return page
        return None

    def _parse_list_page(self, html: str, seen_ids: set[str]) -> list[Post]:
        """데스크톱 개념글 목록 페이지에서 게시물 리스트를 추출."""
        soup = BeautifulSoup(html, "lxml")
        posts: list[Post] = []

        for row in soup.select("tr.ub-content"):
            try:
                post = self._parse_desktop_row(row, seen_ids)
                if post:
                    posts.append(post)
            except Exception as e:
                logger.debug(f"[dcinside] 행 파싱 실패: {e}")

        return posts

    def _parse_desktop_row(self, row: Tag, seen_ids: set[str]) -> Optional[Post]:
        """데스크톱 테이블 행에서 게시물 정보 추출."""
        gall_num = row.select_one("td.gall_num")
        if gall_num:
            num_text = gall_num.get_text(strip=True)
            if num_text in ("공지", "설문", "AD", "뉴스"):
                return None

        post_no = row.get("data-no", "")
        if not post_no:
            if gall_num:
                num_text = gall_num.get_text(strip=True)
                if num_text.isdigit():
                    post_no = num_text
        if not post_no:
            return None

        external_id = f"dc_{self._gallery_id}_{post_no}"
        if external_id in seen_ids:
            return None
        seen_ids.add(external_id)

        title_td = row.select_one("td.gall_tit")
        if not title_td:
            return None

        title_link = title_td.select_one("a:not(.icon_img):not(.reply_numbox)")
        if not title_link:
            title_link = title_td.select_one("a")
        if not title_link:
            return None

        title = title_link.get_text(strip=True)
        if not title:
            return None

        author = ""
        writer_td = row.select_one("td.gall_writer")
        if writer_td:
            nick_el = writer_td.select_one(".nickname") or writer_td.select_one("em")
            if nick_el:
                author = nick_el.get_text(strip=True)
            else:
                author = writer_td.get("data-nick", "")

        comment_count = 0
        reply_el = title_td.select_one(".reply_numbox .reply_num")
        if reply_el:
            ct_text = reply_el.get_text(strip=True).strip("[]")
            if ct_text.isdigit():
                comment_count = int(ct_text)

        views = 0
        count_td = row.select_one("td.gall_count")
        if count_td:
            count_text = count_td.get_text(strip=True)
            if count_text.isdigit():
                views = int(count_text)

        post_url = (
            f"https://gall.dcinside.com/mgallery/board/view/"
            f"?id={self._gallery_id}&no={post_no}"
        )

        return Post(
            source="dcinside",
            external_id=external_id,
            url=post_url,
            author=author,
            content_text=title,
            engagement_comments=comment_count,
            engagement_views=views,
            collected_at=datetime.utcnow(),
        )

    def _parse_detail_page(self, html: str) -> tuple[str, list[str]]:
        """데스크톱 상세 페이지에서 본문과 이미지를 추출."""
        soup = BeautifulSoup(html, "lxml")

        content_div = (
            soup.select_one(".write_div")
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
