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


async def auto_login(
    cdp_url: str,
    source_name: str,
    username: str,
    password: str,
    login_url: str,
    username_selector: str,
    password_selector: str,
    submit_selector: str,
    invalid_keywords: list[str],
    initial_wait_ms: int = 3000,
    submit_wait_ms: int = 5000,
) -> bool:
    """SNS 플랫폼의 자동 로그인을 수행한다.

    Args:
        cdp_url: Chrome CDP URL
        source_name: 소스명 (로깅용)
        username: 사용자명
        password: 비밀번호
        login_url: 로그인 페이지 URL
        username_selector: 사용자명 입력창 선택자
        password_selector: 비밀번호 입력창 선택자
        submit_selector: 제출 버튼 선택자
        invalid_keywords: 로그인 실패 시 URL에 포함될 키워드
        initial_wait_ms: 페이지 로딩 후 대기 시간
        submit_wait_ms: 제출 후 대기 시간
    """
    try:
        async with cdp_connection(cdp_url, source_name) as (pw, context):
            page = await context.new_page()
            try:
                await page.goto(
                    login_url,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                await page.wait_for_timeout(initial_wait_ms)

                # 사용자명 입력
                await page.locator(username_selector).fill(username)

                # 비밀번호 입력
                await page.locator(password_selector).fill(password)

                # 제출
                await page.locator(submit_selector).click()
                await page.wait_for_timeout(submit_wait_ms)

                # 로그인 성공 확인
                if not any(kw in page.url for kw in invalid_keywords):
                    logger.info(f"[{source_name}] 자동 로그인 성공")
                    return True

                logger.warning(f"[{source_name}] 자동 로그인 실패 — 로그인 페이지에 머무름")
                return False
            finally:
                await page.close()
    except Exception as e:
        logger.error(f"[{source_name}] 자동 로그인 오류: {e}")
        return False
