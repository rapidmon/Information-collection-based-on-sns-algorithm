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
    첫 연결 실패 시 기존 탭을 reload한 뒤 한 번 재시도한다.
    """
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(cdp_url)
    except Exception:
        try:
            browser = await _reload_and_reconnect(pw, cdp_url, source_name)
        except Exception as e:
            await pw.stop()
            logger.error(f"[{source_name}] Chrome 연결 실패: {e}")
            raise

    try:
        yield pw, browser.contexts[0]
    finally:
        await pw.stop()


async def minimize_window(page: Page) -> None:
    """페이지가 속한 Chrome 창을 최소화한다(새 탭 열 때 포커스 뺏김 방지).

    CDP Browser.setWindowBounds로 최소화. 최소화 상태에서도 GraphQL 인터셉트/
    DOM 수집은 동작한다(입력·네트워크는 CDP로 주입되므로). 렌더 throttling을
    막으려면 Chrome을 --disable-backgrounding-occluded-windows 등과 함께 실행.
    """
    try:
        cdp = await page.context.new_cdp_session(page)
        info = await cdp.send("Browser.getWindowForTarget")
        await cdp.send("Browser.setWindowBounds", {
            "windowId": info["windowId"],
            "bounds": {"windowState": "minimized"},
        })
        await cdp.detach()
    except Exception as e:
        logger.debug(f"[{page.url[:30]}] 창 최소화 실패(무시): {e}")


async def _reload_and_reconnect(pw: Playwright, cdp_url: str, source_name: str):
    """CDP 타임아웃 시 Chrome을 재시작한 뒤 재연결한다."""
    import asyncio
    import subprocess

    logger.info(f"[{source_name}] CDP 응답 없음 — Chrome 재시작 후 재시도")

    # 기존 Chrome 프로세스에서 user-data-dir 추출
    user_data_dir = _get_chrome_user_data_dir()

    try:
        subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], capture_output=True, timeout=5)
    except Exception:
        pass

    await asyncio.sleep(2)

    try:
        subprocess.Popen(
            [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                "--remote-debugging-port=9222",
                f"--user-data-dir={user_data_dir}",
                "--restore-last-session",
                "--start-minimized",
                # 백그라운드/최소화 상태에서도 수집 동작 (throttling 방지)
                "--disable-backgrounding-occluded-windows",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
                # 메모리 절약 플래그
                "--disable-extensions",
                "--disable-features=Translate,MediaRouter",
                "--disable-background-networking",
                "--js-flags=--max-old-space-size=512",
            ],
        )
    except Exception as e:
        logger.error(f"[{source_name}] Chrome 재시작 실패: {e}")
        raise

    await asyncio.sleep(5)
    return await pw.chromium.connect_over_cdp(cdp_url)


def _get_chrome_user_data_dir() -> str:
    """실행 중인 Chrome의 --user-data-dir 값을 추출한다."""
    import subprocess
    try:
        result = subprocess.run(
            ["wmic", "process", "where",
             "name='chrome.exe' and commandline like '%remote-debugging%'",
             "get", "commandline"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if "--user-data-dir=" in line:
                for part in line.split():
                    if part.startswith("--user-data-dir="):
                        return part.split("=", 1)[1].strip('"')
    except Exception:
        pass
    return r"C:\chrome_temp"


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
