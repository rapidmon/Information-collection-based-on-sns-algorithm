"""AI 통과 게시물 자동 좋아요 (CDP 기반).

AI 처리로 선별된 게시물의 URL을 열어 메인 게시물의 좋아요 버튼을 누른다.
- dry_run=True 이면 버튼 존재만 확인하고 누르지 않는다(로그만).
- 이미 좋아요한 버튼은 셀렉터로 거른다(미좋아요 상태만 대상).
- max_per_run/딜레이로 사람처럼 제한한다.
- 어떤 예외도 상위(수집/처리)를 중단시키지 않는다.

수집 스크롤 중이 아니라 AI 처리 후 별도 단계로 실행되므로, 잡담·광고가 아닌
'관련 O + 중요도 높음' 게시물에만 좋아요가 나간다(알고리즘 피드 품질 향상).
"""

from __future__ import annotations

import asyncio
import logging
import random

from src.domain.entities import Post
from src.infrastructure.collectors.cdp import cdp_connection
from src.infrastructure.config.settings import LikeConfig

logger = logging.getLogger(__name__)


async def _find_like_button(page, source: str):
    """현재 페이지 메인 게시물의 '미좋아요' 상태 좋아요 버튼을 찾는다."""
    if source == "twitter":
        # 'like'=미좋아요, 'unlike'=이미 좋아요 → 'like'만 대상. 첫 번째가 메인 트윗.
        return await page.query_selector('button[data-testid="like"]')
    if source == "threads":
        svg = await page.query_selector('svg[aria-label="좋아요"], svg[aria-label="Like"]')
        if not svg:
            return None
        handle = await svg.evaluate_handle(
            "el => el.closest('div[role=\"button\"], a[role=\"button\"], button')"
        )
        return handle.as_element()
    if source == "linkedin":
        return await page.query_selector(
            'button[aria-label*="좋아요"]:not([aria-pressed="true"]), '
            'button[aria-label*="Like"]:not([aria-pressed="true"])'
        )
    return None


class CdpPostLiker:
    """사용자 Chrome(CDP)에 연결해 게시물 URL을 열고 좋아요를 누르는 Liker."""

    def __init__(self, config: LikeConfig, cdp_port: int = 9222):
        self._cfg = config
        self._cdp_url = f"http://127.0.0.1:{cdp_port}"

    async def like_posts(self, source: str, posts: list[Post]) -> list[str]:
        if not posts:
            return []

        done: list[str] = []
        try:
            async with cdp_connection(self._cdp_url, source) as (pw, context):
                page = await context.new_page()
                try:
                    for post in posts:
                        if len(done) >= self._cfg.max_per_run:
                            break
                        if await self._like_one(page, source, post):
                            done.append(post.id)
                            if not self._cfg.dry_run:
                                await asyncio.sleep(
                                    random.uniform(self._cfg.delay_min, self._cfg.delay_max)
                                )
                finally:
                    await page.close()
        except Exception as e:
            logger.error(f"[{source}][like] 연결/처리 오류: {e}")

        mode = "dry-run" if self._cfg.dry_run else "실제"
        logger.info(f"[{source}][like] 좋아요 {len(done)}건 ({mode})")
        return done

    async def _like_one(self, page, source: str, post: Post) -> bool:
        snippet = (post.summary or post.content_text or "")[:60].replace("\n", " ")
        try:
            await page.goto(post.url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2500)  # 좋아요 버튼 렌더 대기

            btn = await _find_like_button(page, source)
            if not btn:
                logger.warning(
                    f"[{source}][like] 좋아요 버튼 못찾음(이미 좋아요했거나 셀렉터 불일치): "
                    f"{post.url} | {snippet}"
                )
                return False

            if self._cfg.dry_run:
                logger.info(
                    f"[{source}][like:dry-run] 좋아요 대상(버튼확인) "
                    f"score={post.importance_score} | {snippet}"
                )
                return True

            await btn.scroll_into_view_if_needed(timeout=3000)
            await btn.click(timeout=3000)
            logger.info(
                f"[{source}][like] 좋아요 완료 score={post.importance_score} | {snippet}"
            )
            return True
        except Exception as e:
            logger.warning(f"[{source}][like] 처리 실패 {post.url}: {e}")
            return False
