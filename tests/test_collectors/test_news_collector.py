"""NewsCollector 파싱 테스트 — RSS 2.0 / Atom / Google News 프록시 형태."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.infrastructure.collectors import news_collector as nc
from src.infrastructure.collectors.news_collector import NewsCollector
from src.infrastructure.config.settings import CollectorConfig

RSS2 = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>TechCrunch</title>
  <item>
    <title>Apple sues OpenAI over trade secrets</title>
    <link>https://techcrunch.com/2026/07/13/apple-sues-openai/</link>
    <guid isPermaLink="false">https://techcrunch.com/?p=111</guid>
    <pubDate>{recent}</pubDate>
    <description><![CDATA[<p>Apple filed a lawsuit against a former employee...</p>]]></description>
  </item>
  <item>
    <title>Old news beyond cutoff</title>
    <link>https://techcrunch.com/2020/01/01/old/</link>
    <guid>https://techcrunch.com/?p=222</guid>
    <pubDate>Wed, 01 Jan 2020 00:00:00 +0000</pubDate>
    <description>ancient</description>
  </item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>The Verge</title>
  <entry>
    <title>Some gadget review</title>
    <id>https://www.theverge.com/entry/1</id>
    <link rel="alternate" href="https://www.theverge.com/2026/gadget"/>
    <published>{recent_iso}</published>
    <summary>A short summary here.</summary>
  </entry>
</feed>"""


def _config(**over) -> CollectorConfig:
    return CollectorConfig({"max_age_days": 2, "max_items_per_feed": 30, **over})


async def test_rss2_parsing_and_cutoff(monkeypatch):
    recent = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")

    async def fake_fetch(url, source, **kw):
        return RSS2.format(recent=recent)

    monkeypatch.setattr(nc, "fetch_text", fake_fetch)
    collector = NewsCollector(_config(feeds=[
        {"name": "TechCrunch", "tier": "official", "url": "https://techcrunch.com/feed/"},
    ]))

    posts = await collector.collect()

    # 컷오프(2일) 초과 항목은 제외되고 최신 항목만 남는다
    assert len(posts) == 1
    p = posts[0]
    assert p.source == "news"
    assert p.author == "TechCrunch"
    assert p.external_id.startswith("news_")
    assert "Apple sues OpenAI" in p.content_text
    assert "<p>" not in p.content_text  # description HTML 제거
    assert p.url == "https://techcrunch.com/2026/07/13/apple-sues-openai/"
    assert p.published_at is not None


async def test_atom_parsing(monkeypatch):
    recent_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    async def fake_fetch(url, source, **kw):
        return ATOM.format(recent_iso=recent_iso)

    monkeypatch.setattr(nc, "fetch_text", fake_fetch)
    collector = NewsCollector(_config(feeds=[
        {"name": "The Verge", "tier": "official", "url": "https://www.theverge.com/rss/index.xml"},
    ]))

    posts = await collector.collect()

    assert len(posts) == 1
    p = posts[0]
    assert p.author == "The Verge"
    assert p.url == "https://www.theverge.com/2026/gadget"
    assert "Some gadget review" in p.content_text


async def test_feed_failure_does_not_block_others(monkeypatch):
    recent = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")

    async def fake_fetch(url, source, **kw):
        if "broken" in url:
            return None  # fetch 실패
        return RSS2.format(recent=recent)

    monkeypatch.setattr(nc, "fetch_text", fake_fetch)
    monkeypatch.setattr(nc, "_FEED_DELAY", 0)
    collector = NewsCollector(_config(feeds=[
        {"name": "Broken", "tier": "paywalled", "url": "https://broken.example/rss"},
        {"name": "TechCrunch", "tier": "official", "url": "https://techcrunch.com/feed/"},
    ]))

    posts = await collector.collect()
    assert len(posts) == 1
    assert posts[0].author == "TechCrunch"


async def test_malformed_xml_skipped(monkeypatch):
    async def fake_fetch(url, source, **kw):
        return "<rss><channel><item><title>unclosed"

    monkeypatch.setattr(nc, "fetch_text", fake_fetch)
    collector = NewsCollector(_config(feeds=[
        {"name": "Bad", "tier": "official", "url": "https://bad.example/rss"},
    ]))
    assert await collector.collect() == []
