"""CDP 연결 유틸리티.

모든 SNS 수집기가 공유하는 Chrome CDP 연결 로직을 중앙화한다.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

logger = logging.getLogger(__name__)

# ─── 먹통 Chrome 안전 복구 상태 ───
# 예전의 무한 taskkill+재실행(창 증식) 대신, '먹통(Timeout)일 때만' 쿨다운을 두고
# 1회만, 세션복원 없이, 디버그 Chrome만 재시작한다.
_CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
_RESTART_COOLDOWN_S = 600  # 안전 재시작 최소 간격(초) — 스파이럴 방지
_last_restart_monotonic = 0.0


def _detect_debug_chrome() -> tuple[list[str], str]:
    """--remote-debugging-port를 가진 Chrome의 (PID 목록, user-data-dir)를 반환.

    wmic은 Windows 11에서 제거됐으므로 PowerShell CIM(Get-CimInstance)을 사용한다.
    """
    import subprocess

    pids: list[str] = []
    dirs: list[str] = []
    ps = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -eq 'chrome.exe' -and $_.CommandLine -like '*remote-debugging-port*' } | "
        "ForEach-Object { 'PID=' + $_.ProcessId; "
        "if ($_.CommandLine -match '--user-data-dir=(\\S+)') { 'UD=' + $matches[1] } }"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=15,
        )
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("PID=") and line[4:].isdigit():
                pids.append(line[4:])
            elif line.startswith("UD="):
                dirs.append(line[3:].strip().strip('"'))
    except Exception:
        pass

    # 재실행 프로필은 로그인 세션이 있는 표준 chrome_temp를 우선 사용
    user_dir = next((d for d in dirs if "chrome_temp" in d), dirs[0] if dirs else r"C:\chrome_temp")
    return pids, user_dir


def _safe_restart_chrome(source_name: str) -> None:
    """먹통인 디버그 Chrome만 종료하고 세션복원 없이 최소 상태로 재실행.

    --restore-last-session 미사용(탭/창 증식 없음), 디버그 포트를 가진 Chrome만
    프로세스 트리로 종료(일반 Chrome은 건드리지 않음). 쿨다운은 호출부에서 관리.
    """
    import os
    import subprocess
    import time as _time

    pids, user_dir = _detect_debug_chrome()
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True, timeout=6)
        except Exception:
            pass

    # 프로필 락이 풀리도록 대기 (즉시 재실행하면 single-instance 충돌로 9222가 안 열린다)
    _time.sleep(3)
    # 스테일 락 파일 제거 — attach만 하고 죽는 것 방지
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        try:
            os.remove(os.path.join(user_dir, name))
        except OSError:
            pass

    try:
        subprocess.Popen([
            _CHROME_PATH,
            "--remote-debugging-port=9222",
            f"--user-data-dir={user_dir}",
            "--start-minimized",
            "--disable-backgrounding-occluded-windows",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-extensions",
            "--disable-features=Translate,MediaRouter",
            "--disable-background-networking",
            "--js-flags=--max-old-space-size=2048",
        ])
        logger.warning(f"[{source_name}] 디버그 Chrome 안전 재시작 완료 (종료 {len(pids)}개, 세션복원 없음)")
    except Exception as e:
        logger.error(f"[{source_name}] Chrome 안전 재실행 실패: {e}")


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

    global _last_restart_monotonic

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

    # 여전히 실패하면: 먹통(Timeout)/포트닫힘(ECONNREFUSED)일 때 '안전 재시작'을 쿨다운 내 1회 시도.
    if browser is None:
        msg = str(last_err or "")
        recoverable = ("Timeout" in msg) or ("ECONNREFUSED" in msg)
        now = time.monotonic()
        if recoverable and (now - _last_restart_monotonic) > _RESTART_COOLDOWN_S:
            _last_restart_monotonic = now
            logger.warning(
                f"[{source_name}] Chrome 응답 없음 — 안전 재시작 시도 "
                f"(쿨다운 {_RESTART_COOLDOWN_S // 60}분, 세션복원 없음)"
            )
            _safe_restart_chrome(source_name)
            await asyncio.sleep(6)
            try:
                browser = await pw.chromium.connect_over_cdp(cdp_url, timeout=15000)
            except Exception as e:
                last_err = e

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
            page = await context.new_page()  # 새 탭 (확인 후 닫아 메모리 회수)
            try:
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
