"""Hybrid 백엔드 폴백 — Claude CLI 장애(LLMBackendError)가 OpenAI 폴백을 실제로 트리거하는지."""

from __future__ import annotations

from src.infrastructure.ai.hybrid_processor import HybridAIProcessor
from src.infrastructure.ai.openai_processor import LLMBackendError


class _FakeOpenAI:
    _config = object()

    def __init__(self):
        self.compose_called = False
        self.curation_called = False

    async def compose_topics(self, topics, posts):
        self.compose_called = True
        return topics

    async def generate_curation(self, topics, audience):
        self.curation_called = True
        return "openai-curation"


class _DeadClaude:
    """CLI 미설치/한도 소진 상황을 흉내내는 스텁."""

    async def compose_topics(self, topics, posts):
        raise LLMBackendError("claude CLI 실행 실패")

    async def generate_curation(self, topics, audience):
        raise LLMBackendError("claude CLI 실행 실패")


async def test_compose_falls_back_to_openai_on_backend_error():
    openai = _FakeOpenAI()
    hybrid = HybridAIProcessor(openai, _DeadClaude())

    result = await hybrid.compose_topics(["topic"], ["post"])

    assert openai.compose_called
    assert result == ["topic"]


async def test_curation_falls_back_to_openai_on_backend_error():
    openai = _FakeOpenAI()
    hybrid = HybridAIProcessor(openai, _DeadClaude())

    result = await hybrid.generate_curation(["topic"], "개발자")

    assert openai.curation_called
    assert result == "openai-curation"
