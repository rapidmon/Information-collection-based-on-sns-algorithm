"""좋아요가 누적된 계정 자동 팔로우 (CDP 기반).

자동 좋아요가 게시물 단위 신호라면, 팔로우는 계정 단위 신호다. 같은 계정의
글을 반복해서 좋아요했다면 그 계정 자체가 양질이라는 뜻이므로 팔로우해
알고리즘 피드가 그 계정을 더 자주 띄우게 유도한다.

⚠️ 셀렉터가 이 기능의 최대 위험 지점이다 — 프로필 페이지에는 **대상 계정 말고
추천 계정의 팔로우 버튼도 같이 렌더된다**(실측: @CNBC 프로필에 팔로우 버튼 4개 —
CNBC/economics/CNN/SquawkCNBC). 첫 매치를 집으면 엉뚱한 계정을 팔로우한다.
플랫폼마다 겨냥법이 달라서 전략을 분리한다.

- twitter: aria-label의 핸들(`@screen_name`)로 정확히 겨냥. `@` 접두가 경계라
  유사 핸들(@CNBC vs @SquawkCNBC)끼리 안 섞인다.
- threads: aria-label도 data-testid도 없다. 대신 프로필 페이지에서 정확히
  '팔로우' 텍스트를 가진 role=button 이 **딱 1개**다(6개 프로필 실측). 그래서
  개수가 1일 때만 누르고, 2개 이상이면 모호하므로 중단한다.
- linkedin: **지원하지 않는다.** 대상 자신의 버튼은 aria-label이 이름 없는
  "팔로우중"인데 추천 계정들은 이름이 붙은 "○○ 팔로우"라, 트위터 방식을 쓰면
  정확히 엉뚱한 계정만 골라 팔로우한다(실측: /company/openai/ 에서 본인 버튼은
  '팔로우중', 나머지는 Anthropic·Google·NVIDIA…). 개인 프로필(/in/)은 본인
  팔로우 버튼이 없고 '연결'이 기본이라 동작 자체가 다르다.

'팔로우' 텍스트 버튼만 누르므로 최악의 경우에도 언팔로우는 일어나지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
import random

from src.infrastructure.collectors.cdp import cdp_connection, minimize_window
from src.infrastructure.config.settings import FollowConfig

logger = logging.getLogger(__name__)

SUPPORTED = ("twitter", "threads")

PROFILE_URL = {
    "twitter": "https://x.com/{handle}",
    "threads": "https://www.threads.net/@{handle}",
}

# threads: 로케일별 버튼 라벨 (정확 일치로만 매칭)
_THREADS_FOLLOW = ("팔로우", "Follow")
_THREADS_FOLLOWING = ("팔로잉", "Following")


def _selectors(handle: str) -> tuple[str, str]:
    """트위터용 (팔로우 버튼, 이미 팔로우 중 버튼) — 핸들 정확 매칭."""
    return (
        f'button[data-testid$="-follow"][aria-label$="@{handle}"]',
        f'button[data-testid$="-unfollow"][aria-label$="@{handle}"]',
    )


# 정확히 일치하는 텍스트를 가진 role=button 의 개수를 센다.
# innerText 기준 — XPath text() 는 중첩 span 때문에 같은 버튼을 여러 번 센다.
_COUNT_JS = """(labels) => labels.map(l =>
    [...document.querySelectorAll('[role="button"]')]
        .filter(b => (b.innerText || '').trim() === l).length)"""


class CdpAccountFollower:
    """사용자 Chrome(CDP)에 연결해 계정 프로필을 열고 팔로우를 누른다."""

    def __init__(self, config: FollowConfig, cdp_port: int = 9222):
        self._cfg = config
        self._cdp_url = f"http://127.0.0.1:{cdp_port}"

    async def follow_accounts(self, source: str, candidates: list[dict]) -> list[dict]:
        """후보 계정들을 팔로우. 각 계정의 처리 결과(status 포함)를 반환한다.

        status: followed / already / failed
        """
        if not candidates:
            return []
        if source not in SUPPORTED:
            logger.info(f"[{source}][follow] 미지원 플랫폼 — 건너뜀 (사유는 모듈 docstring)")
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

        try:
            await page.goto(
                PROFILE_URL[source].format(handle=handle),
                wait_until="domcontentloaded",
                timeout=30000,
            )

            if source == "threads":
                return await self._follow_threads(page, handle, likes)

            follow_sel, unfollow_sel = _selectors(handle)

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

    async def _follow_threads(self, page, handle: str, likes) -> str:
        """Threads 전용 — 텍스트 정확 일치 + '유일할 때만' 클릭.

        aria-label·data-testid가 없어 텍스트로 겨냥할 수밖에 없다. 프로필
        페이지엔 '팔로우' 버튼이 딱 1개라는 걸 실측으로 확인했지만, 추천 카드가
        렌더되는 변형이 나오면 2개 이상이 될 수 있다. 그때는 어느 것이 대상인지
        분간할 수 없으므로 **누르지 않고 실패 처리**한다(오팔로우 방지).
        """
        follow_n = following_n = 0
        for _ in range(16):
            follow_n = sum(await page.evaluate(_COUNT_JS, list(_THREADS_FOLLOW)))
            following_n = sum(await page.evaluate(_COUNT_JS, list(_THREADS_FOLLOWING)))
            if follow_n or following_n:
                break
            await page.wait_for_timeout(500)

        if follow_n == 0 and following_n > 0:
            logger.info(f"[threads][follow] 이미 팔로우 중 — 건너뜀 | @{handle}")
            return "already"

        if follow_n == 0:
            logger.warning(
                f"[threads][follow] 팔로우 버튼 못 찾음(비공개·삭제·UI 변경?) | @{handle}"
            )
            return "failed"

        if follow_n > 1:
            logger.warning(
                f"[threads][follow] 팔로우 버튼 {follow_n}개 — 대상 분간 불가로 중단 | @{handle}"
            )
            return "failed"

        if self._cfg.dry_run:
            logger.info(f"[threads][follow:dry-run] 팔로우 대상 likes={likes} | @{handle}")
            return "followed"

        btn = page.get_by_role("button", name=_THREADS_FOLLOW[0], exact=True)
        if await btn.count() != 1:
            btn = page.get_by_role("button", name=_THREADS_FOLLOW[1], exact=True)
        if await btn.count() != 1:
            logger.warning(f"[threads][follow] 클릭 대상 특정 실패 | @{handle}")
            return "failed"

        await btn.click(timeout=5000)

        # 상태 전이 확인 — '팔로우'가 사라지고 '팔로잉'이 떠야 성공
        for _ in range(12):
            await page.wait_for_timeout(400)
            if sum(await page.evaluate(_COUNT_JS, list(_THREADS_FOLLOWING))) > 0:
                logger.info(f"[threads][follow] 팔로우 완료 likes={likes} | @{handle}")
                return "followed"

        logger.warning(f"[threads][follow] 클릭했으나 상태 미확인 | @{handle}")
        return "failed"
