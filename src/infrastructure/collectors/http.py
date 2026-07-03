"""HTTP 수집기 공용 유틸.

로그인이 필요 없는 소스(36kr·Product Hunt 등)의 공통 fetch 로직 —
User-Agent, httpx 클라이언트 설정, 예외 처리를 한곳에 모은다. (CDP/Chrome 불필요)
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


async def fetch_text(
    url: str,
    source: str,
    extra_headers: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> str | None:
    """GET 요청 후 본문 텍스트를 반환. 실패 시 None(에러 로그)."""
    headers = {"User-Agent": USER_AGENT}
    if extra_headers:
        headers.update(extra_headers)
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers=headers
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
    except Exception as e:
        logger.error(f"[{source}] 페이지 요청 실패: {e}")
        return None
