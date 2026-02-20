"""OpenAI API 기반 AI 프로세서 구현.

도메인 AIProcessor 인터페이스를 구현한다.
GPT-4o-mini로 필터링, GPT-4o로 요약/분류/통합 — 비용 최적화.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import OpenAI

from src.domain.entities import Post
from src.domain.services.ai_processor import CategoryResult, FilterResult, MergedTopic
from src.infrastructure.ai.prompts import (
    CATEGORIZE,
    DEDUPLICATE_AND_MERGE,
    FILTER_AND_SUMMARIZE,
    SYSTEM_PROMPT,
)
from src.infrastructure.config.settings import ProcessingConfig

logger = logging.getLogger(__name__)


def _chunked(lst: list, size: int):
    """리스트를 size 크기의 청크로 분할."""
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def _posts_to_json(posts: list[Post]) -> str:
    """Post 리스트를 프롬프트에 삽입할 JSON 문자열로 변환."""
    items = []
    for p in posts:
        items.append({
            "post_id": p.id,
            "source": p.source,
            "author": p.author,
            "text": p.content_text[:1000],  # 토큰 절약
            "summary": p.summary,
            "categories": p.category_names,
            "importance_score": p.importance_score,
        })
    return json.dumps(items, ensure_ascii=False, indent=2)


def _parse_json_response(text: str) -> list[dict[str, Any]]:
    """API 응답에서 JSON 배열을 추출."""
    text = text.strip()

    # JSON 블록이 ```로 감싸진 경우 처리
    if "```" in text:
        start = text.find("[")
        end = text.rfind("]") + 1
        if start != -1 and end > start:
            text = text[start:end]

    # 바로 JSON인 경우
    if text.startswith("["):
        return json.loads(text)

    # 텍스트 중에 JSON 배열이 포함된 경우
    start = text.find("[")
    end = text.rfind("]") + 1
    if start != -1 and end > start:
        return json.loads(text[start:end])

    raise ValueError(f"JSON 배열을 찾을 수 없음: {text[:200]}")


class OpenAIProcessor:
    """OpenAI GPT API 기반 AI 프로세서."""

    def __init__(self, api_key: str, config: ProcessingConfig):
        self._client = OpenAI(api_key=api_key)
        self._config = config

    def _call_api(self, model: str, prompt: str, max_tokens: int = 4096) -> str:
        """OpenAI Chat Completions API 동기 호출."""
        response = self._client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0.1,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content

    async def filter_and_summarize(self, posts: list[Post]) -> list[FilterResult]:
        """관련성 필터 + 요약 (GPT-4o-mini 사용, 배치)."""
        results: list[FilterResult] = []

        for batch in _chunked(posts, self._config.batch_size_filter):
            posts_json = _posts_to_json(batch)
            prompt = FILTER_AND_SUMMARIZE.format(posts_json=posts_json)

            try:
                response_text = self._call_api(self._config.model_filter, prompt)
                parsed = _parse_json_response(response_text)

                for item in parsed:
                    results.append(
                        FilterResult(
                            post_id=item["post_id"],
                            is_relevant=item.get("is_relevant", False),
                            summary=item.get("summary"),
                            language=item.get("language"),
                        )
                    )
            except Exception as e:
                logger.error(f"필터/요약 API 호출 실패: {e}")
                # 실패 시 모든 게시물을 관련으로 표시 (안전 기본값)
                for p in batch:
                    results.append(
                        FilterResult(
                            post_id=p.id,
                            is_relevant=True,
                            summary=p.content_text[:200],
                            language="unknown",
                        )
                    )

        logger.info(
            f"필터/요약 완료: {len(results)}건 (관련: {sum(1 for r in results if r.is_relevant)}건)"
        )
        return results

    async def categorize(self, posts: list[Post]) -> list[CategoryResult]:
        """카테고리 분류 + 중요도 (GPT-4o 사용, 배치)."""
        results: list[CategoryResult] = []

        for batch in _chunked(posts, self._config.batch_size_categorize):
            posts_json = _posts_to_json(batch)
            prompt = CATEGORIZE.format(posts_json=posts_json)

            try:
                response_text = self._call_api(self._config.model_process, prompt)
                parsed = _parse_json_response(response_text)

                for item in parsed:
                    results.append(
                        CategoryResult(
                            post_id=item["post_id"],
                            categories=item.get("categories", []),
                            importance_score=item.get("importance_score", 0.5),
                        )
                    )
            except Exception as e:
                logger.error(f"분류 API 호출 실패: {e}")
                for p in batch:
                    results.append(
                        CategoryResult(
                            post_id=p.id, categories=["Other"], importance_score=0.5
                        )
                    )

        logger.info(f"분류 완료: {len(results)}건")
        return results

    async def deduplicate_and_merge(self, posts: list[Post]) -> list[MergedTopic]:
        """중복 제거 + 토픽 통합 (GPT-4o 사용)."""
        if not posts:
            return []

        posts_json = _posts_to_json(posts)
        prompt = DEDUPLICATE_AND_MERGE.format(posts_json=posts_json)

        try:
            response_text = self._call_api(
                self._config.model_process, prompt, max_tokens=8192
            )
            parsed = _parse_json_response(response_text)

            results = []
            for item in parsed:
                results.append(
                    MergedTopic(
                        post_ids=item.get("post_ids", []),
                        headline=item.get("headline", ""),
                        body_bullets=item.get("body_bullets", []),
                        primary_category=item.get("primary_category", "Other"),
                        importance_score=item.get("importance_score", 0.5),
                        sources=item.get("sources", []),
                    )
                )

            logger.info(f"중복제거/통합 완료: {len(posts)}건 → {len(results)}개 토픽")
            return results

        except Exception as e:
            logger.error(f"중복제거/통합 API 호출 실패: {e}")
            # 실패 시 각 게시물을 개별 토픽으로
            return [
                MergedTopic(
                    post_ids=[p.id] if p.id else [],
                    headline=p.summary or p.content_text[:100],
                    body_bullets=[p.summary or p.content_text[:300]],
                    primary_category=p.category_names[0] if p.category_names else "Other",
                    importance_score=p.importance_score or 0.5,
                    sources=[p.source],
                )
                for p in posts
            ]
