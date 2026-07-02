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
from src.infrastructure.collectors.cdp import cdp_connection, get_or_create_page, minimize_window
from src.infrastructure.config.settings import LikeConfig

logger = logging.getLogger(__name__)


# 플랫폼별 좋아요 상태 셀렉터 (HTML 상태 태그로 '이미 좋아요' vs '미좋아요' 구분)
#  - liked: 이미 좋아요를 누른 상태를 나타내는 요소
#  - not_liked: 아직 안 누른(=누를 수 있는) 상태의 버튼
_LIKE_SELECTORS = {
    "twitter": {
        # data-testid로 상태가 명확히 갈린다: unlike=이미 좋아요, like=미좋아요
        "liked": 'button[data-testid="unlike"]',
        "not_liked": 'button[data-testid="like"]',
    },
    "threads": {
        # svg aria-label로 구분: '좋아요 취소'/'Unlike'=이미 누름, '좋아요'/'Like'=미누름
        "liked": 'svg[aria-label="좋아요 취소"], svg[aria-label="Unlike"]',
        "not_liked": 'svg[aria-label="좋아요"], svg[aria-label="Like"]',
    },
    "linkedin": {
        # 반응(추천) 버튼. 누른 뒤엔 aria-pressed="true"로 바뀐다.
        "liked": 'button.react-button__trigger[aria-pressed="true"], '
                 'button[aria-pressed="true"][aria-label*="추천"], '
                 'button[aria-pressed="true"][aria-label*="reaction"]',
        "not_liked": 'button[aria-label*="반응 없음"], button[aria-label*="No reaction"]',
    },
}


async def _get_like_state(page, source: str):
    """현재 페이지 메인 게시물의 좋아요 상태를 HTML 태그로 판정.

    반환: (state, button) — state ∈ {'liked', 'not_liked', 'not_found'}
    'liked'는 이미 좋아요된 상태(버튼은 참고용), 'not_liked'는 누를 버튼을 함께 반환.
    """
    sels = _LIKE_SELECTORS.get(source)
    if not sels:
        return ("not_found", None)

    # 1) 이미 좋아요된 상태인지 먼저 확인 (상태 태그로 명확히)
    liked_el = await page.query_selector(sels["liked"])
    if liked_el:
        return ("liked", liked_el)

    # 2) 미좋아요(누를 수 있는) 버튼 확인
    not_liked_el = await page.query_selector(sels["not_liked"])
    if not_liked_el:
        if source == "threads":
            # svg → 실제 클릭 대상(버튼)으로 승격
            handle = await not_liked_el.evaluate_handle(
                "el => el.closest('div[role=\"button\"], a[role=\"button\"], button')"
            )
            not_liked_el = handle.as_element()
        return ("not_liked", not_liked_el)

    # 3) 둘 다 없음 = 아직 렌더 안 됐거나 셀렉터 불일치
    return ("not_found", None)


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
                domain = {"twitter": "x.com", "threads": "threads", "linkedin": "linkedin.com"}.get(source)
                page = await get_or_create_page(context, domain)  # 기존 플랫폼 탭 재사용
                await minimize_window(page)
                for post in posts:
                    if len(done) >= self._cfg.max_per_run:
                        break
                    if await self._like_one(page, source, post):
                        done.append(post.id)
                        if not self._cfg.dry_run:
                            await asyncio.sleep(
                                random.uniform(self._cfg.delay_min, self._cfg.delay_max)
                            )
                # 탭은 닫지 않고 남겨 다음 사이클에 재사용
        except Exception as e:
            logger.error(f"[{source}][like] 연결/처리 오류: {e}")

        mode = "dry-run" if self._cfg.dry_run else "실제"
        logger.info(f"[{source}][like] 좋아요 {len(done)}건 ({mode})")
        return done

    async def _wait_for_like_state(self, page, source: str, timeout_ms: int = 8000):
        """좋아요 영역이 렌더돼 상태가 판정될 때까지 폴링(고정 sleep보다 견고).

        반환: (state, button) — 'not_found'가 아니게 되면 즉시 반환.
        """
        elapsed = 0
        step = 500
        state, btn = "not_found", None
        while elapsed < timeout_ms:
            state, btn = await _get_like_state(page, source)
            if state != "not_found":
                return state, btn
            await page.wait_for_timeout(step)
            elapsed += step
        return state, btn

    async def _like_one(self, page, source: str, post: Post) -> bool:
        snippet = (post.summary or post.content_text or "")[:60].replace("\n", " ")
        try:
            await page.goto(post.url, wait_until="domcontentloaded", timeout=20000)

            state, btn = await self._wait_for_like_state(page, source)

            # 이미 좋아요된 상태 — HTML 상태 태그로 명확히 판별, 건너뜀
            if state == "liked":
                logger.info(f"[{source}][like] 이미 좋아요됨 — 건너뜀 | {snippet}")
                return False

            # 상태를 못 읽음 — 셀렉터 불일치이거나 렌더 실패 (이미 좋아요와 구분됨)
            if state == "not_found" or btn is None:
                logger.warning(
                    f"[{source}][like] 좋아요 상태 판정 실패(셀렉터 불일치/렌더 실패): "
                    f"{post.url} | {snippet}"
                )
                return False

            # state == "not_liked" — 누를 수 있는 상태
            if self._cfg.dry_run:
                logger.info(
                    f"[{source}][like:dry-run] 좋아요 대상(미좋아요 확인) "
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
