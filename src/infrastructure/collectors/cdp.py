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
    연결 실패 시 짧게 재시도만 하고, 그래도 안 되면 예외를 던져 이번 사이클을
    건너뛴다. (예전처럼 taskkill로 Chrome을 죽이고 재실행하지 않는다 — 사용자의
    기존 창을 유지하고 창이 불어나는 것을 막기 위함. 디버그 Chrome이 꺼져 있으면
    로그만 남기고 스킵하니, 9222 디버그 Chrome을 켜두면 된다.)
    """
    import asyncio

    pw = await async_playwright().start()
    browser = None
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            browser = await pw.chromium.connect_over_cdp(cdp_url, timeout=10000)
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                await asyncio.sleep(2)

    if browser is None:
        await pw.stop()
        logger.error(
            f"[{source_name}] Chrome CDP 연결 실패 ({cdp_url}) — "
            f"9222 디버그 Chrome이 켜져 있는지 확인하세요. 이번 수집은 건너뜁니다: {last_err}"
        )
        raise last_err if last_err else RuntimeError("CDP 연결 실패")

    try:
        yield pw, browser.contexts[0]
    finally:
        await pw.stop()


async def get_or_create_page(context, match: str | None = None) -> Page:
    """기존 탭을 최대한 재사용한다 (수집마다 새 창/탭이 뜨는 것 방지).

    1) match(도메인 등)를 URL에 포함하는 탭이 있으면 그 탭 재사용
    2) 없으면 빈 탭(about:blank/새 탭)을 재사용
    3) 그래도 없으면 새 탭 생성
    수집 후 탭을 닫지 않고 남겨두면 다음 사이클에 이 함수가 다시 그 탭을 찾아 쓴다.
    """
    pages = list(context.pages)
    if match:
        for p in pages:
            try:
                if match in (p.url or ""):
                    return p
            except Exception:
                continue
    for p in pages:
        try:
            u = p.url or ""
            if u in ("", "about:blank") or u.startswith("chrome://newtab"):
                return p
        except Exception:
            continue
    return await context.new_page()


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


async def check_session(
    cdp_url: str,
    source_name: str,
    feed_url: str,
    invalid_keywords: list[str],
    match: str | None = None,
    require_substr: str | None = None,
    login_markers: str | None = None,
) -> bool:
    """피드로 이동해 로그인 상태를 확인한다.

    아래 중 하나라도 걸리면 '로그아웃(무효)'으로 판정한다:
      (a) 최종 url에 invalid_keywords 포함 (login/authwall 등)
      (b) require_substr가 지정됐는데 최종 url에 없음
          (예: X는 로그인 시 /home에 머물고, 로그아웃 시 x.com/ 로 튕긴다)
      (c) login_markers 셀렉터(로그인/가입 버튼·입력창)가 페이지에 존재
    URL 키워드만으론 SNS 로그아웃을 놓치는 경우가 많아 (b)(c)를 추가했다.
    """
    try:
        async with cdp_connection(cdp_url, source_name) as (pw, context):
            page = await get_or_create_page(context, match)
            await minimize_window(page)
            await page.goto(feed_url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(1500)
            url = page.url or ""

            if any(kw in url for kw in invalid_keywords):
                return False
            if require_substr and require_substr not in url:
                logger.info(f"[{source_name}] 세션 무효: url에 '{require_substr}' 없음 ({url[:60]})")
                return False
            if login_markers:
                try:
                    if await page.query_selector(login_markers):
                        logger.info(f"[{source_name}] 세션 무효: 로그인 화면 감지")
                        return False
                except Exception:
                    pass
            return True
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
                await page.locator(username_selector).first.fill(username)

                # 비밀번호 입력
                await page.locator(password_selector).first.fill(password)

                # 제출 (셀렉터가 여러 요소에 매칭돼도 첫 번째 사용 — strict mode 위반 방지)
                await page.locator(submit_selector).first.click()
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
