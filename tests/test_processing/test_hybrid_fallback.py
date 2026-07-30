"""Hybrid 백엔드 폴백 — Claude CLI 장애(LLMBackendError)가 OpenAI 폴백을 실제로 트리거하는지."""

from __future__ import annotations

from src.infrastructure.ai.hybrid_processor import HybridAIProcessor
from src.infrastructure.ai.openai_processor import LLMBackendError


class _Config:
    def __init__(self, routine_backend="openai"):
        self.routine_backend = routine_backend


class _FakeOpenAI:
    def __init__(self, routine_backend="openai"):
        self._config = _Config(routine_backend)
        self.compose_called = False
        self.curation_called = False
        self.filter_called = False
        self.categorize_called = False
        self.feedback = None

    def set_feedback_examples(self, examples):
        self.feedback = examples

    async def compose_topics(self, topics, posts):
        self.compose_called = True
        return topics

    async def generate_curation(self, topics, audience):
        self.curation_called = True
        return "openai-curation"

    async def filter_and_summarize(self, posts):
        self.filter_called = True
        return "openai-filter"

    async def categorize(self, posts):
        self.categorize_called = True
        return "openai-categorize"


class _DeadClaude:
    """CLI 미설치/한도 소진 상황을 흉내내는 스텁."""

    def __init__(self):
        self.feedback = None

    def set_feedback_examples(self, examples):
        self.feedback = examples

    async def compose_topics(self, topics, posts):
        raise LLMBackendError("claude CLI 실행 실패")

    async def generate_curation(self, topics, audience):
        raise LLMBackendError("claude CLI 실행 실패")

    async def filter_and_summarize(self, posts):
        raise LLMBackendError("claude CLI 실행 실패")

    async def categorize(self, posts):
        raise LLMBackendError("claude CLI 실행 실패")


class _LiveClaude(_DeadClaude):
    async def filter_and_summarize(self, posts):
        return "claude-filter"


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


async def test_routine_backend_claude_routes_filter_to_claude():
    openai = _FakeOpenAI(routine_backend="claude")
    hybrid = HybridAIProcessor(openai, _LiveClaude())

    assert await hybrid.filter_and_summarize([]) == "claude-filter"
    assert not openai.filter_called


async def test_routine_backend_claude_falls_back_on_backend_error():
    openai = _FakeOpenAI(routine_backend="claude")
    hybrid = HybridAIProcessor(openai, _DeadClaude())

    assert await hybrid.filter_and_summarize([]) == "openai-filter"
    assert await hybrid.categorize([]) == "openai-categorize"


async def test_routine_backend_openai_never_calls_claude():
    openai = _FakeOpenAI(routine_backend="openai")
    hybrid = HybridAIProcessor(openai, _DeadClaude())

    # claude 스텁은 호출되면 raise — openai 백엔드에선 폴백 경고 없이 직행해야 한다
    assert await hybrid.filter_and_summarize([]) == "openai-filter"


async def test_feedback_examples_injected_into_both_backends():
    openai, claude = _FakeOpenAI(), _DeadClaude()
    hybrid = HybridAIProcessor(openai, claude)

    hybrid.set_feedback_examples([{"headline": "h", "verdict": "과대"}])

    assert openai.feedback == claude.feedback == [{"headline": "h", "verdict": "과대"}]
