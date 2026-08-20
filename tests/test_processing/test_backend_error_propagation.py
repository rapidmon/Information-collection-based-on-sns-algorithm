"""배치 루프의 LLMBackendError 전파 — 폴백 버그 회귀 방지.

과거엔 배치 루프의 `except Exception`이 CLI 장애까지 삼켜 게시물을 조용히
비관련 처리했고, hybrid의 Codex 폴백이 영원히 작동하지 않았다.
백엔드 장애는 전파되어야 하고, 일반 오류(파싱·빈 응답)는 기존대로
배치 단위 실패 처리(비관련 컷/배치 스킵)를 유지해야 한다.
"""

from __future__ import annotations

import pytest

from src.domain.entities import Post
from src.infrastructure.ai.llm_processor import BaseLLMProcessor, LLMBackendError
from src.infrastructure.config.settings import ProcessingConfig


def _post(i: int) -> Post:
    return Post(
        id=i, external_id=f"e{i}", source="twitter", author="a",
        url="https://x.com/1", content_text="구체적 수치가 있는 충분히 긴 기술 게시물 본문 " * 3,
    )


class _BackendDown(BaseLLMProcessor):
    def __init__(self):
        self._config = ProcessingConfig({})

    def _call_api(self, model, prompt, max_tokens=4096, *, lean=False):
        raise LLMBackendError("claude CLI 한도 소진")


class _ParseFailure(BaseLLMProcessor):
    def __init__(self):
        self._config = ProcessingConfig({})

    def _call_api(self, model, prompt, max_tokens=4096, *, lean=False):
        raise RuntimeError("LLM 빈 응답")


async def test_filter_propagates_backend_error():
    with pytest.raises(LLMBackendError):
        await _BackendDown().filter_and_summarize([_post(1)])


async def test_categorize_propagates_backend_error():
    with pytest.raises(LLMBackendError):
        await _BackendDown().categorize([_post(1)])


async def test_filter_generic_error_still_marks_irrelevant():
    results = await _ParseFailure().filter_and_summarize([_post(1), _post(2)])
    assert len(results) == 2
    assert not any(r.is_relevant for r in results)


async def test_categorize_generic_error_still_skips_batch():
    assert await _ParseFailure().categorize([_post(1)]) == []
