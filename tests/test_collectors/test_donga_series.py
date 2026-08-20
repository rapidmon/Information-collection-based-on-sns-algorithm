"""동아 연재 수집기 + 슬랙 전용 라우팅 회귀 방지.

이 소스는 **AI 필터·채점을 태우지 않는다**(사용자 결정). 그래서 두 가지가 성립해야 한다:
  ① summary가 채워진 채 저장돼야 get_unprocessed(summary IS NULL)에 안 걸린다
  ② get_unbriefed에서 제외돼야 일반 브리핑(이메일)의 상대평가·dedup에 안 섞인다
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.infrastructure.collectors.donga_series_collector import KST, DongaSeriesCollector
from src.infrastructure.config.settings import CollectorConfig

_TODAY = datetime.now(KST).strftime("%Y-%m-%d")
_OLD = (datetime.now(KST) - timedelta(days=40)).strftime("%Y-%m-%d")

HTML = f"""
<html><body>
<ul class="row_list">
  <li>
    <a href="https://www.donga.com/news/Economy/article/all/20260807/134438956/4?x=1"></a>
    <span class="tit">앱스토어 1위 [트렌디깅]</span>
    <span class="date">{_TODAY}</span>
    <p class="desc">폭염 속 그늘길 앱이 인기다.</p>
  </li>
  <li><span class="date">{_TODAY}</span></li>
  <li>
    <a href="https://www.donga.com/news/Culture/article/all/20260804/134313131/2"></a>
    <span class="tit">불교코어 열풍 [트렌디깅]</span>
    <span class="date">{_OLD}</span>
    <p class="desc">오래된 회차라 컷오프 대상.</p>
  </li>
  <li>
    <a href="https://www.donga.com/news/Economy/article/all/20260807/134438956/4"></a>
    <span class="tit">중복 회차</span>
    <span class="date">{_TODAY}</span>
  </li>
</ul>
<ul class="row_list">
  <li>
    <a href="https://www.donga.com/news/Culture/article/all/20260101/999999999/1"></a>
    <span class="tit">다른 목록(추천) — 수집하면 안 됨</span>
    <span class="date">{_TODAY}</span>
  </li>
</ul>
</body></html>
"""


def _collector(monkeypatch, html=HTML):
    cfg = CollectorConfig({
        "series_url": "https://www.donga.com/news/Series/1",
        "series_name": "트렌디깅",
        "max_age_days": 7,
    })
    col = DongaSeriesCollector(cfg)

    async def fake_fetch(url, source, *a, **kw):
        return html

    monkeypatch.setattr(
        "src.infrastructure.collectors.donga_series_collector.fetch_text", fake_fetch
    )
    return col


def test_parses_items(monkeypatch):
    posts = asyncio.run(_collector(monkeypatch).collect())

    assert [p.external_id for p in posts] == ["donga_134438956"]
    p = posts[0]
    assert p.source == "donga_series"
    assert p.author == "트렌디깅"
    assert "앱스토어 1위" in p.content_text
    assert p.summary == "폭염 속 그늘길 앱이 인기다."
    assert p.url.endswith("/134438956/4"), "쿼리스트링은 떼야 한다"


def test_ai_is_skipped_by_prefilled_fields(monkeypatch):
    """summary·is_relevant가 채워져야 AI 처리 대상에서 빠진다."""
    p = asyncio.run(_collector(monkeypatch).collect())[0]

    assert p.summary, "summary가 비면 get_unprocessed에 걸려 AI를 탄다"
    assert p.is_relevant is True


def test_missing_list_returns_empty(monkeypatch):
    assert asyncio.run(_collector(monkeypatch, "<html><body>없음</body></html>").collect()) == []


# ─── 슬랙 전용 라우팅 ───


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    import src.infrastructure.database.repositories.post_repo_sqlite as mod

    monkeypatch.setattr(mod, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(mod._thread_local, "db", None, raising=False)
    r = mod.PostRepositorySQLite()
    yield r
    mod._get_db().close()
    monkeypatch.setattr(mod._thread_local, "db", None, raising=False)


def _insert(source: str, pid: str):
    import src.infrastructure.database.repositories.post_repo_sqlite as mod

    conn = mod._get_db()
    now = datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO posts (id, source, external_id, content_text, collected_at,"
        " summary, is_relevant) VALUES (?,?,?,?,?,?,1)",
        (pid, source, pid, "본문", now, "요약"),
    )
    conn.commit()


async def test_slack_only_source_excluded_from_normal_briefing(repo):
    _insert("twitter", "tw1")
    _insert("donga_series", "donga_1")

    normal = await repo.get_unbriefed()

    assert [p.id for p in normal] == ["tw1"], "연재가 이메일 브리핑에 섞이면 안 된다"


async def test_slack_only_query_returns_them(repo):
    _insert("twitter", "tw1")
    _insert("donga_series", "donga_1")

    extras = await repo.get_slack_only_unbriefed()

    assert [p.id for p in extras] == ["donga_1"]


async def test_briefed_extras_are_not_reposted(repo):
    _insert("donga_series", "donga_1")
    await repo.mark_briefed(["donga_1"], datetime.utcnow())

    assert await repo.get_slack_only_unbriefed() == []
