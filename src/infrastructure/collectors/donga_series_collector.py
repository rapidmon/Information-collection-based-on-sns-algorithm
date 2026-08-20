"""동아일보 연재 시리즈 수집기 (HTTP, 로그인 불필요).

일반 파이프라인과 달리 **AI 필터·중요도 채점을 태우지 않는다**. 신문사가 이미
큐레이션한 연재라 필터가 오히려 방해되고(트렌디깅은 기술 뉴스가 아닌 회차가 많다),
사용자 요구도 "새로 올라오면 그냥 슬랙에 보여달라"이기 때문이다.

그래서 수집 시점에 `summary`(리드문)와 `is_relevant=1`을 미리 채워 넣는다.
`get_unprocessed`가 summary IS NULL 기준이라 AI 처리 대상에서 자연히 빠지고,
`get_unbriefed`는 source 제외 목록으로 걸러 일반 브리핑에도 섞이지 않는다
(슬랙 발송 단계에서 별도 섹션으로 붙는다).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from src.domain.entities import Post
from src.infrastructure.collectors.http import fetch_text
from src.infrastructure.config.settings import CollectorConfig

logger = logging.getLogger(__name__)

# 기사 URL에서 기사 ID 추출: /news/Economy/article/all/20260807/134438956/4
_ARTICLE_ID = re.compile(r"/article/all/(\d{8})/(\d+)")

KST = timezone(timedelta(hours=9))


class DongaSeriesCollector:
    """동아일보 연재(Series) 페이지 수집기.

    페이지가 서버 렌더라 httpx + BeautifulSoup 만으로 파싱된다(브라우저 불필요).
    목록은 `ul.row_list > li`에 제목·링크·날짜·리드문이 함께 들어 있다.
    """

    def __init__(self, config: CollectorConfig):
        self._config = config
        self._url = config.series_url
        self._name = config.series_name

    @property
    def source_name(self) -> str:
        return "donga_series"

    async def is_session_valid(self) -> bool:
        return True

    async def collect(self) -> list[Post]:
        html = await fetch_text(self._url, self.source_name)
        if html is None:
            return []

        soup = BeautifulSoup(html, "html.parser")
        lists = soup.select("ul.row_list")
        if not lists:
            logger.warning(f"[{self.source_name}] 목록(ul.row_list)을 찾지 못함 — 페이지 구조 변경?")
            return []

        cutoff = datetime.now(KST) - timedelta(days=self._config.max_age_days)
        posts: list[Post] = []
        seen: set[str] = set()

        # 첫 리스트가 연재 본목록. 나머지(추천 등)는 보지 않는다.
        for li in lists[0].select("li"):
            post = self._parse_item(li, cutoff, seen)
            if post:
                posts.append(post)

        logger.info(f"[{self.source_name}] {len(posts)}건 수집 완료 ({self._name})")
        return posts

    def _parse_item(self, li, cutoff: datetime, seen: set[str]) -> Post | None:
        # li 가 비어 있는 래퍼인 경우가 섞여 있다(링크 없는 li) → 조용히 건너뛴다
        a = li.find("a", href=True)
        if not a:
            return None
        url = a["href"].split("?")[0]
        m = _ARTICLE_ID.search(url)
        if not m:
            return None
        article_id = m.group(2)
        if article_id in seen:
            return None

        title_el = li.select_one(".tit, h4, h3, strong")
        title = (title_el.get_text(strip=True) if title_el else a.get_text(strip=True)).strip()
        if not title:
            return None
        seen.add(article_id)

        desc_el = li.select_one(".desc, .summary, p")
        desc = desc_el.get_text(strip=True) if desc_el else ""

        published_at = None
        date_el = li.select_one(".date, .time, span[class*=date]")
        raw_date = date_el.get_text(strip=True) if date_el else m.group(1)
        for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y%m%d"):
            try:
                published_at = datetime.strptime(raw_date[:10], fmt).replace(tzinfo=KST)
                break
            except ValueError:
                continue

        if published_at and published_at < cutoff:
            return None

        # AI를 태우지 않으므로 요약·관련성을 여기서 확정한다 (모듈 docstring 참조)
        return Post(
            source=self.source_name,
            external_id=f"donga_{article_id}",
            url=url,
            author=self._name,
            content_text=f"{title}\n\n{desc}" if desc else title,
            summary=desc or title,
            is_relevant=True,
            published_at=published_at,
            collected_at=datetime.utcnow(),
        )
