"""Playwright 브라우저 세션 관리자.

모든 SNS 수집기(X, Threads, LinkedIn)가 공유하는 브라우저 인스턴스와
플랫폼별 persistent context를 관리한다.
"""

from __future__ import annotations

import logging
import random
from pathlib import Path

from playwright.async_api import BrowserContext, async_playwright

from src.infrastructure.config.settings import BrowserConfig

logger = logging.getLogger(__name__)


class BrowserManager:
    """Playwright 브라우저 생명주기 및 세션 관리."""

    def __init__(self, config: BrowserConfig):
        self._config = config
        self._playwright = None
        self._browser = None
        self._contexts: dict[str, BrowserContext] = {}
        self._profile_dir = Path(config.profile_dir)

    async def initialize(self) -> None:
        """Playwright 브라우저를 시작한다."""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            channel="chrome",
            headless=self._config.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )
        logger.info("Playwright 브라우저 초기화 완료")

    async def get_context(self, platform: str) -> BrowserContext:
        """플랫폼별 browser context를 가져온다. 세션이 저장되어 있으면 복원."""
        if platform in self._contexts:
            return self._contexts[platform]

        storage_path = self._profile_dir / f"{platform}_profile" / "state.json"
        storage_state = str(storage_path) if storage_path.exists() else None

        ua = random.choice(self._config.user_agents) if self._config.user_agents else None

        context = await self._browser.new_context(
            storage_state=storage_state,
            viewport={"width": 1920, "height": 1080},
            user_agent=ua,
            locale="ko-KR",
            timezone_id="Asia/Seoul",
        )

        # stealth 패치 (playwright-stealth가 설치된 경우)
        try:
            from playwright_stealth import stealth_async

            await stealth_async(context)
            logger.debug(f"[{platform}] playwright-stealth 적용")
        except ImportError:
            logger.debug("playwright-stealth 미설치 — 기본 모드")

        self._contexts[platform] = context

        if storage_state:
            logger.info(f"[{platform}] 기존 세션 복원됨")
        else:
            logger.info(f"[{platform}] 새 세션 (수동 로그인 필요)")

        return context

    async def save_state(self, platform: str) -> None:
        """현재 세션(쿠키, localStorage)을 파일에 저장."""
        context = self._contexts.get(platform)
        if context is None:
            return

        storage_dir = self._profile_dir / f"{platform}_profile"
        storage_dir.mkdir(parents=True, exist_ok=True)
        storage_path = storage_dir / "state.json"

        await context.storage_state(path=str(storage_path))
        logger.debug(f"[{platform}] 세션 저장됨 → {storage_path}")

    async def manual_login(self, platform: str, url: str) -> None:
        """수동 로그인을 위해 브라우저 페이지를 열고 로그인 완료를 대기.

        headed 모드에서 실행. 사용자가 로그인 후 콘솔에서 Enter를 누르면 세션 저장.
        """
        context = await self.get_context(platform)
        page = await context.new_page()
        await page.goto(url)

        logger.info(f"[{platform}] 브라우저에서 수동 로그인을 진행하세요.")
        logger.info(f"[{platform}] 로그인 완료 후 이 터미널에서 Enter를 누르세요...")

        # 비동기 입력 대기 (실제 운영에서는 이벤트 기반으로 전환 가능)
        import asyncio

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, input)

        await self.save_state(platform)
        await page.close()
        logger.info(f"[{platform}] 로그인 세션 저장 완료")

    async def close(self) -> None:
        """모든 context와 브라우저를 종료."""
        for platform, context in self._contexts.items():
            try:
                await self.save_state(platform)
                await context.close()
            except Exception as e:
                logger.warning(f"[{platform}] context 종료 중 오류: {e}")

        self._contexts.clear()

        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

        logger.info("Playwright 브라우저 종료 완료")
