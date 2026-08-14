"""좋아요가 누적된 계정 자동 팔로우 (CDP 기반).

자동 좋아요가 게시물 단위 신호라면, 팔로우는 계정 단위 신호다. 같은 계정의
글을 반복해서 좋아요했다면 그 계정 자체가 양질이라는 뜻이므로 팔로우해
알고리즘 피드가 그 계정을 더 자주 띄우게 유도한다.

⚠️ 셀렉터 주의 — 프로필 페이지에는 **대상 계정 말고 추천 계정의 팔로우 버튼도
같이 렌더된다**(실측: @CNBC 프로필에 팔로우 버튼 4개 — CNBC/economics/CNN/SquawkCNBC).
primaryColumn으로 좁혀도 마찬가지라, 첫 매치를 집으면 엉뚱한 계정을 팔로우한다.
그래서 aria-label의 핸들(`@screen_name`)로 정확히 겨냥한다. `@` 접두가 있어
유사 핸들(@CNBC vs @SquawkCNBC)끼리도 섞이지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
import random

from src.infrastructure.collectors.cdp import cdp_connection, minimize_window
from src.infrastructure.config.settings import FollowConfig

logger = logging.getLogger(__name__)

PROFILE_URL = {"twitter": "https://x.com/{handle}"}


def _selectors(handle: str) -> tuple[str, str]:
    """(팔로우 버튼, 이미 팔로우 중 버튼) — 핸들 정확 매칭."""
    return (
        f'button[data-testid$="-follow"][aria-label$="@{handle}"]',
        f'button[data-testid$="-unfollow"][aria-label$="@{handle}"]',
    )


class CdpAccountFollower:
    """사용자 Chrome(CDP)에 연결해 계정 프로필을 열고 팔로우를 누른다."""

    def __init__(self, config: FollowConfig, cdp_port: int = 9222):
        self._cfg = config
        self._cdp_url = f"http://127.0.0.1:{cdp_port}"

    async def follow_accounts(self, source: str, candidates: list[dict]) -> list[dict]:
        """후보 계정들을 팔로우. 각 계정의 처리 결과(status 포함)를 반환한다.

        status: followed / already / failed
        """
        if not candidates or source not in PROFILE_URL:
            return []

        results: list[dict] = []
        try:
            async with cdp_connection(self._cdp_url, source) as (pw, context):
                page = await context.new_page()  # 새 탭 (완료 후 닫아 메모리 회수)
                await minimize_window(page)
                try:
                    for cand in candidates:
                        if len(results) >= self._cfg.max_per_run:
                            break
                        status = await self._follow_one(page, source, cand)
                        results.append({**cand, "status": status})
                        # 실제 팔로우가 나간 경우만 사람처럼 쉬어간다
                        if status == "followed" and not self._cfg.dry_run:
                            await asyncio.sleep(
                                random.uniform(self._cfg.delay_min, self._cfg.delay_max)
                            )
                finally:
                    await page.close()
        except Exception as e:
            logger.error(f"[{source}][follow] 연결/처리 오류: {e}")

        mode = "dry-run" if self._cfg.dry_run else "실제"
        followed = sum(1 for r in results if r["status"] == "followed")
        logger.info(f"[{source}][follow] 팔로우 {followed}/{len(results)}건 ({mode})")
        return results

    async def _follow_one(self, page, source: str, cand: dict) -> str:
        handle = (cand.get("screen_name") or "").strip()
        likes = cand.get("like_count")
        if not handle:
            logger.warning(f"[{source}][follow] 핸들 없음 — 스킵 | {cand.get('author_url')}")
            return "failed"

        follow_sel, unfollow_sel = _selectors(handle)
        try:
            await page.goto(
                PROFILE_URL[source].format(handle=handle),
                wait_until="domcontentloaded",
                timeout=30000,
            )

            # 팔로우 영역이 렌더될 때까지 폴링 (고정 sleep보다 견고)
            btn = None
            state = "not_found"
            for _ in range(16):
                if await page.query_selector(unfollow_sel):
                    state = "already"
                    break
                btn = await page.query_selector(follow_sel)
                if btn:
                    state = "not_followed"
                    break
                await page.wait_for_timeout(500)

            if state == "already":
                logger.info(f"[{source}][follow] 이미 팔로우 중 — 건너뜀 | @{handle}")
                return "already"

            if state == "not_found" or btn is None:
                # 셀렉터 불일치·렌더 실패·계정 정지/비공개 등
                logger.warning(
                    f"[{source}][follow] 팔로우 버튼 못 찾음(정지·비공개·셀렉터 불일치?) | @{handle}"
                )
                return "failed"

            if self._cfg.dry_run:
                logger.info(f"[{source}][follow:dry-run] 팔로우 대상 likes={likes} | @{handle}")
                return "followed"

            await btn.scroll_into_view_if_needed(timeout=3000)
            await btn.click(timeout=3000)

            # 클릭이 실제로 반영됐는지 확인 — 확인 못 하면 성공으로 기록하지 않는다
            # (기록만 되고 팔로우는 안 된 채 후보에서 영영 빠지는 사태 방지)
            for _ in range(10):
                await page.wait_for_timeout(400)
                if await page.query_selector(unfollow_sel):
                    logger.info(f"[{source}][follow] 팔로우 완료 likes={likes} | @{handle}")
                    return "followed"

            logger.warning(f"[{source}][follow] 클릭했으나 상태 미확인 | @{handle}")
            return "failed"

        except Exception as e:
            logger.warning(f"[{source}][follow] 처리 실패 @{handle}: {e}")
            return "failed"
