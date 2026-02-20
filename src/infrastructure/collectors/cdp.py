"""CDP 연결 유틸리티.

모든 SNS 수집기가 공유하는 Chrome CDP 연결 로직을 중앙화한다.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

logger = logging.getLogger(__name__)


@asynccontextmanager
async def cdp_connection(
    cdp_url: str, source_name: str
) -> AsyncGenerator[tuple[Playwright, BrowserContext], None]:
    """Chrome CDP 연결 context manager.

    Yields (playwright, context). 종료 시 playwright를 정리한다.
    """
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(cdp_url)
    except Exception as e:
        await pw.stop()
        logger.error(f"[{source_name}] Chrome 연결 실패: {e}")
        raise

    try:
        yield pw, browser.contexts[0]
    finally:
        await pw.stop()


async def check_session(
    cdp_url: str, source_name: str, feed_url: str, invalid_keywords: list[str]
) -> bool:
    """CDP 연결 후 피드 URL로 이동하여 로그인 상태를 확인한다."""
    try:
        async with cdp_connection(cdp_url, source_name) as (pw, context):
            page = await context.new_page()
            try:
                await page.goto(feed_url, wait_until="domcontentloaded", timeout=15000)
                return not any(kw in page.url for kw in invalid_keywords)
            finally:
                await page.close()
    except Exception as e:
        logger.warning(f"[{source_name}] 세션 확인 실패: {e}")
        return False
