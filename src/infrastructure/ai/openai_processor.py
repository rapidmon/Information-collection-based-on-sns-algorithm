"""OpenAI API 기반 AI 프로세서 구현.

도메인 AIProcessor 인터페이스를 구현한다.
GPT-4o-mini로 필터링, GPT-4o로 요약/분류/통합 — 비용 최적화.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import OpenAI

from src.domain.entities import Post
from duckduckgo_search import DDGS

from src.domain.services.ai_processor import CategoryResult, FilterResult, MergedTopic, VerificationResult
from src.infrastructure.ai.prompts import (
    CATEGORIZE,
    CROSS_CHUNK_MERGE,
    DEDUPLICATE_AND_MERGE,
    EXTRACT_CLAIMS,
    FILTER_AND_SUMMARIZE,
    SYSTEM_PROMPT,
    VERIFY_CLAIMS,
)
from src.infrastructure.config.settings import ProcessingConfig

logger = logging.getLogger(__name__)


def _chunked(lst: list, size: int):
    """리스트를 size 크기의 청크로 분할."""
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def _posts_to_json(posts: list[Post]) -> str:
    """Post 리스트를 프롬프트에 삽입할 JSON 문자열로 변환 (캐싱)."""
    items = []
    for p in posts:
        items.append({
            "post_id": p.id,
            "source": p.source,
            "author": p.author,
            "text": p.content_text[:1000] if p.content_text else "",  # 토큰 절약
            "summary": p.summary,
            "categories": p.category_names or [],
            "importance_score": p.importance_score,
            "url": p.url,
        })
    # 한 번의 JSON 직렬화로 모든 포스트 처리
    return json.dumps(items, ensure_ascii=False, indent=2)


def _posts_to_json_lite(posts: list[Post]) -> str:
    """Post 리스트를 프롬프트에 삽입할 JSON 문자열로 변환 (요약 단계용, text 필드 제외)."""
    items = []
    for p in posts:
        items.append({
            "post_id": p.id,
            "source": p.source,
            "summary": p.summary,
            "categories": p.category_names,
            "importance_score": p.importance_score,
            "url": p.url,
        })
    return json.dumps(items, ensure_ascii=False, indent=2)


def _parse_json_response(text: str) -> list[dict[str, Any]]:
    """API 응답에서 JSON 배열을 추출 (최적화: 한 번에 처리)."""
    text = text.strip()

    # 바로 JSON인 경우 (가장 빠른 경로)
    if text.startswith("["):
        return json.loads(text)

    # 한 번의 find/rfind로 JSON 배열 찾기
    start = text.find("[")
    if start == -1:
        raise ValueError(f"JSON 배열을 찾을 수 없음: {text[:200]}")

    end = text.rfind("]") + 1
    if end <= start:
        raise ValueError(f"JSON 배열을 찾을 수 없음: {text[:200]}")

    return json.loads(text[start:end])


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
            posts_json = _posts_to_json_lite(batch)
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
                            keywords=item.get("keywords", []),
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

    async def verify_claims(self, posts: list[Post]) -> list[VerificationResult]:
        """게시물의 핵심 주장을 웹 검색으로 교차 검증."""
        if not posts:
            return []

        # 1단계: GPT로 검증이 필요한 핵심 주장 추출
        posts_json = _posts_to_json_lite(posts)
        prompt = EXTRACT_CLAIMS.format(posts_json=posts_json)

        try:
            response_text = self._call_api(self._config.model_filter, prompt)
            claims = _parse_json_response(response_text)
        except Exception as e:
            logger.warning(f"주장 추출 실패 (검증 스킵): {e}")
            return [
                VerificationResult(post_id=p.id, credibility="verified")
                for p in posts
            ]

        # 검증 필요한 주장만 필터
        claims_to_verify = [
            c for c in claims
            if c.get("needs_verification") and c.get("claim")
        ]

        if not claims_to_verify:
            logger.info("검증 필요한 주장 없음, 전체 통과")
            return [
                VerificationResult(post_id=p.id, credibility="verified")
                for p in posts
            ]

        # 2단계: 웹 검색으로 각 주장 검증
        verification_data = []
        for claim_item in claims_to_verify:
            search_results = self._web_search(claim_item["claim"])
            verification_data.append({
                "post_id": claim_item["post_id"],
                "claim": claim_item["claim"],
                "search_results": search_results,
            })

        # 3단계: GPT로 원문 vs 검색 결과 비교 판정
        verification_json = json.dumps(verification_data, ensure_ascii=False, indent=2)
        verify_prompt = VERIFY_CLAIMS.format(verification_data=verification_json)

        results: list[VerificationResult] = []
        verified_ids: set = set()

        try:
            response_text = self._call_api(self._config.model_process, verify_prompt)
            parsed = _parse_json_response(response_text)

            for item in parsed:
                results.append(VerificationResult(
                    post_id=item["post_id"],
                    credibility=item.get("credibility", "unverified"),
                    reason=item.get("reason"),
                ))
                verified_ids.add(item["post_id"])
        except Exception as e:
            logger.warning(f"신뢰도 판정 실패 (검증 스킵): {e}")

        # 검증 대상이 아닌 게시물은 verified로 처리
        for p in posts:
            if p.id not in verified_ids:
                results.append(VerificationResult(
                    post_id=p.id, credibility="verified"
                ))

        contradicted = sum(1 for r in results if r.credibility == "contradicted")
        unverified = sum(1 for r in results if r.credibility == "unverified")
        verified = sum(1 for r in results if r.credibility == "verified")
        logger.info(
            f"신뢰도 검증 완료: {len(results)}건 "
            f"(검증됨: {verified}, 미검증: {unverified}, 허위/스캠: {contradicted})"
        )
        return results

    def _web_search(self, query: str, max_results: int = 5) -> list[dict]:
        """DuckDuckGo로 웹 검색하여 결과 반환."""
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
            return [
                {"title": r.get("title", ""), "body": r.get("body", ""), "href": r.get("href", "")}
                for r in results
            ]
        except Exception as e:
            logger.warning(f"웹 검색 실패 '{query[:50]}': {e}")
            return []

    async def deduplicate_and_merge(self, posts: list[Post]) -> list[MergedTopic]:
        """중복 제거 + 토픽 통합 (GPT-4o 사용, 청킹)."""
        if not posts:
            return []

        all_results: list[MergedTopic] = []
        chunk_size = self._config.dedup_chunk_size

        for i, chunk in enumerate(_chunked(posts, chunk_size)):
            posts_json = _posts_to_json_lite(chunk)
            prompt = DEDUPLICATE_AND_MERGE.format(posts_json=posts_json)

            try:
                response_text = self._call_api(
                    self._config.model_process, prompt, max_tokens=16384
                )
                parsed = _parse_json_response(response_text)

                for item in parsed:
                    all_results.append(
                        MergedTopic(
                            post_ids=item.get("post_ids", []),
                            headline=item.get("headline", ""),
                            body_bullets=item.get("body_bullets", []),
                            primary_category=item.get("primary_category", "Other"),
                            importance_score=item.get("importance_score", 0.5),
                            sources=item.get("sources", []),
                            source_urls=item.get("source_urls", []),
                        )
                    )

            except Exception as e:
                logger.error(f"청크 {i+1} 중복제거/통합 API 호출 실패: {e}")
                # 실패 시 해당 청크의 각 게시물을 개별 토픽으로
                for p in chunk:
                    all_results.append(
                        MergedTopic(
                            post_ids=[p.id] if p.id else [],
                            headline=p.summary or p.content_text[:100],
                            body_bullets=[p.summary or p.content_text[:300]],
                            primary_category=p.category_names[0] if p.category_names else "Other",
                            importance_score=p.importance_score or 0.5,
                            sources=[p.source],
                            source_urls=[p.url] if p.url else [],
                        )
                    )

        num_chunks = (len(posts) - 1) // chunk_size + 1
        logger.info(f"1차 중복제거: {len(posts)}건 → {len(all_results)}개 토픽 ({num_chunks}개 청크)")

        # 2차: 청크 간 중복 병합 (청크가 2개 이상이고 토픽이 2개 이상일 때만)
        if num_chunks >= 2 and len(all_results) >= 2:
            all_results = await self._cross_chunk_merge(all_results)

        logger.info(f"최종 토픽 수: {len(all_results)}개")
        return all_results

    async def _cross_chunk_merge(self, topics: list[MergedTopic]) -> list[MergedTopic]:
        """청크 간 동일 사건 토픽을 2차 병합한다."""
        # 토픽 headline + primary_category만 보내서 토큰 절약
        topics_summary = []
        for i, t in enumerate(topics):
            topics_summary.append({
                "index": i,
                "headline": t.headline,
                "category": t.primary_category,
            })

        topics_json = json.dumps(topics_summary, ensure_ascii=False, indent=2)
        prompt = CROSS_CHUNK_MERGE.format(topics_json=topics_json)

        try:
            response_text = self._call_api(
                self._config.model_process, prompt, max_tokens=4096
            )
            merge_groups = _parse_json_response(response_text)

            if not merge_groups:
                return topics

            # 병합 실행
            merged_indices: set[int] = set()
            merged_topics: list[MergedTopic] = []

            for group in merge_groups:
                indices = group.get("merge_indices", [])
                if len(indices) < 2:
                    continue

                # 유효 인덱스만
                indices = [idx for idx in indices if 0 <= idx < len(topics)]
                if len(indices) < 2:
                    continue

                # 첫 번째 토픽을 기준으로 나머지를 병합
                base = topics[indices[0]]
                combined_post_ids = list(base.post_ids)
                combined_bullets = list(base.body_bullets)
                combined_sources = list(base.sources)
                combined_urls = list(base.source_urls)

                for idx in indices[1:]:
                    other = topics[idx]
                    combined_post_ids.extend(other.post_ids)
                    combined_bullets.extend(other.body_bullets)
                    combined_sources.extend(other.sources)
                    combined_urls.extend(other.source_urls)

                # 중복 제거
                combined_sources = list(dict.fromkeys(combined_sources))
                combined_urls = list(dict.fromkeys(combined_urls))

                merged_topics.append(MergedTopic(
                    post_ids=combined_post_ids,
                    headline=group.get("headline", base.headline),
                    body_bullets=combined_bullets,
                    primary_category=base.primary_category,
                    importance_score=group.get("importance_score", base.importance_score),
                    sources=combined_sources,
                    source_urls=combined_urls,
                ))
                merged_indices.update(indices)

            # 병합되지 않은 토픽 유지
            for i, t in enumerate(topics):
                if i not in merged_indices:
                    merged_topics.append(t)

            logger.info(f"2차 청크 간 병합: {len(topics)}개 → {len(merged_topics)}개 토픽")
            return merged_topics

        except Exception as e:
            logger.warning(f"2차 청크 간 병합 실패 (원본 유지): {e}")
            return topics
