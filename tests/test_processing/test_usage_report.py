"""usage_report의 [usage] 줄 파싱 — Codex/Claude CLI 양쪽 포맷 호환."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "usage_report",
    Path(__file__).resolve().parents[2] / "scripts" / "usage_report.py",
)
usage_report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(usage_report)


def test_parses_legacy_reasoning_line():
    m = usage_report.LINE.search(
        "2026-07-30 INFO [usage] model=gpt-5-mini in=12345 out=678 (reasoning=512)"
    )
    assert m and m.group("model") == "gpt-5-mini"
    assert (m.group("in"), m.group("out"), m.group("reasoning")) == ("12345", "678", "512")
    assert m.group("cache_read") is None


def test_parses_codex_cli_line_with_cached_reasoning():
    m = usage_report.LINE.search(
        "[usage] model=gpt-5.6-luna/low in=100 out=20 (cached=80 reasoning=5)"
    )
    assert m and m.group("model") == "gpt-5.6-luna/low"
    assert (m.group("cached"), m.group("codex_reasoning")) == ("80", "5")


def test_parses_claude_cli_line_with_cache():
    m = usage_report.LINE.search(
        "[usage] model=claude-haiku-4-5 in=10 out=42 (cache_read=21569 cache_write=6773)"
    )
    assert m and m.group("model") == "claude-haiku-4-5"
    assert (m.group("in"), m.group("out")) == ("10", "42")
    assert (m.group("cache_read"), m.group("cache_write")) == ("21569", "6773")
    assert m.group("reasoning") is None


def test_ignores_non_usage_lines():
    assert usage_report.LINE.search("INFO 필터/요약 완료: 40건 (관련: 8건)") is None
