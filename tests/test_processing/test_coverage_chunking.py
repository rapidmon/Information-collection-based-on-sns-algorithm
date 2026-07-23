"""기브리핑 판정 청크 분할 테스트 — 대량 후보를 청크로 나눠 판정하고 전역 인덱스를 보존."""

from __future__ import annotations

import json
from types import SimpleNamespace

from src.domain.services.ai_processor import MergedTopic
from src.infrastructure.ai.openai_processor import (
    COVERAGE_DEDUP_CHUNK_SIZE,
    OpenAIProcessor,
)


def _topic(headline: str) -> MergedTopic:
    return MergedTopic(
        post_ids=[1], headline=headline, body_bullets=["요약"],
        primary_category="AI", importance_score=0.9, sources=["news"],
    )


def _proc() -> OpenAIProcessor:
    return OpenAIProcessor(
        api_key="test-key",
        config=SimpleNamespace(model_filter="test-model", model_process="test-model"),
    )


async def test_chunks_and_keeps_global_indexes(monkeypatch):
    proc = _proc()
    topics = [_topic(f"사건 {i}") for i in range(COVERAGE_DEDUP_CHUNK_SIZE + 10)]
    calls: list[str] = []

    def fake_call(model, prompt, max_tokens=4096):
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps([{"index": 0, "duplicate": True, "reason": "r"}])
        # 두 번째 청크: 전역 인덱스로 응답 + 청크 범위 밖 인덱스(0)는 무시돼야 함
        return json.dumps([
            {"index": COVERAGE_DEDUP_CHUNK_SIZE, "duplicate": True, "reason": "r"},
            {"index": 0, "duplicate": True, "reason": "범위 밖 — 무시"},
        ])

    monkeypatch.setattr(proc, "_call_api", fake_call)
    dup = await proc.find_covered_topics(topics, ["[AI] 과거 사건"])

    assert len(calls) == 2  # 60개 → 50 + 10 두 청크
    assert sorted(dup) == [0, COVERAGE_DEDUP_CHUNK_SIZE]


async def test_chunk_failure_skips_only_that_chunk(monkeypatch):
    proc = _proc()
    topics = [_topic(f"사건 {i}") for i in range(COVERAGE_DEDUP_CHUNK_SIZE + 5)]
    calls: list[int] = []

    def fake_call(model, prompt, max_tokens=4096):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("llm down")
        return json.dumps([
            {"index": COVERAGE_DEDUP_CHUNK_SIZE, "duplicate": True, "reason": "r"},
        ])

    monkeypatch.setattr(proc, "_call_api", fake_call)
    dup = await proc.find_covered_topics(topics, ["[AI] 과거 사건"])

    # 첫 청크 실패는 그 청크만 스킵 — 두 번째 청크 결과는 살아있다
    assert len(calls) == 2
    assert dup == [COVERAGE_DEDUP_CHUNK_SIZE]


async def test_empty_inputs_no_call(monkeypatch):
    proc = _proc()
    monkeypatch.setattr(
        proc, "_call_api",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("호출되면 안 됨")),
    )
    assert await proc.find_covered_topics([], ["과거"]) == []
    assert await proc.find_covered_topics([_topic("사건")], []) == []
