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


# catch-all 토픽 판별용 — 이런 단어가 headline에 들어있고 source_count가 많으면 잡동사니 묶음으로 간주
_VAGUE_HEADLINE_PATTERNS = [
    "관련 주요 동향", "관련 동향", "다양한 업데이트", "다양한 발표",
    "업계 소식", "주요 소식", "최근 동향", "여러 기업", "여러 발표",
    "종합", "모음", "정리", "트렌드 요약",
]


def _is_catch_all(headline: str, post_count: int) -> bool:
    """모호한 headline + 과다한 출처 수 → catch-all 버킷으로 판정."""
    if post_count < 4:
        return False
    if not headline:
        return True
    return any(p in headline for p in _VAGUE_HEADLINE_PATTERNS)


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
        is_legacy = "gpt-4o" in model
        params: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }
        if is_legacy:
            params["max_tokens"] = max_tokens
            params["temperature"] = 0.1
        else:
            params["max_completion_tokens"] = max_tokens
        response = self._client.chat.completions.create(**params)
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
        """카테고리 분류 + 중요도 (gpt-4o-mini 사용, 배치)."""
        results: list[CategoryResult] = []

        for batch in _chunked(posts, self._config.batch_size_categorize):
            posts_json = _posts_to_json_lite(batch)
            prompt = CATEGORIZE.format(posts_json=posts_json)

            try:
                response_text = self._call_api(self._config.model_filter, prompt)
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
            response_text = self._call_api(self._config.model_filter, verify_prompt)
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
            post_map = {str(p.id): p for p in chunk if p.id is not None}
            posts_json = _posts_to_json_lite(chunk)
            prompt = DEDUPLICATE_AND_MERGE.format(posts_json=posts_json)

            try:
                response_text = self._call_api(
                    self._config.model_process, prompt, max_tokens=16384
                )
                parsed = _parse_json_response(response_text)

                for item in parsed:
                    post_ids = item.get("post_ids", [])
                    headline = item.get("headline", "")
                    # 출력 검증: 모호한 headline의 catch-all 버킷만 개별 토픽으로 분해
                    # (post_ids가 많아도 같은 사건 다출처 보도면 정당하므로 headline 내용으로만 판정)
                    if _is_catch_all(headline, len(post_ids)):
                        logger.warning(
                            f"청크 {i+1}: catch-all 토픽 분해 — headline='{headline[:60]}', "
                            f"post_count={len(post_ids)}"
                        )
                        for pid in post_ids:
                            p = post_map.get(str(pid))
                            if p is None:
                                continue
                            all_results.append(
                                MergedTopic(
                                    post_ids=[p.id],
                                    headline=p.summary or (p.content_text or "")[:100],
                                    body_bullets=[p.summary or (p.content_text or "")[:300]],
                                    primary_category=p.category_names[0] if p.category_names else "Other",
                                    importance_score=p.importance_score or 0.5,
                                    sources=[p.source],
                                    source_urls=[p.url] if p.url else [],
                                )
                            )
                        continue

                    all_results.append(
                        MergedTopic(
                            post_ids=post_ids,
                            headline=headline,
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

    _KR_SUFFIXES = re.compile(
        r'(는|은|가|이|를|을|의|에|로|와|과|며|고|도|만|서|나|든|까지|에서|으로|하며|이며'
        r'|했다|했으며|되었다|되었|하였다|있다|없다|이다|한다|된다|라고)$'
    )

    @classmethod
    def _normalize_token(cls, token: str) -> str:
        """토큰을 정규화한다 (소문자, 하이픈/공백 제거, 한국어 조사 제거)."""
        t = token.lower().replace('-', '').replace(' ', '')
        t = cls._KR_SUFFIXES.sub('', t)
        return t

    @staticmethod
    def _extract_key_tokens(headline: str) -> set[str]:
        """headline에서 핵심 토큰을 추출.

        - 영문 이름+버전을 하나의 토큰으로 유지 (GPT-5.5, Claude 4.7 등)
        - 한국어 조사 제거 후 어간만 추출
        - 고유명사 중심 추출
        """
        # 1단계: 영문 이름+버전번호를 하나로 묶음
        merged = re.findall(
            r'[A-Za-z][A-Za-z0-9]*(?:[- ]?\d+(?:\.\d+)*)?', headline
        )
        # 2단계: 한국어 단어 추출 (2자 이상)
        korean = re.findall(r'[가-힣]{2,}', headline)
        # 3단계: 독립 숫자+단위
        numbers = re.findall(r'\d+(?:\.\d+)?[조억만%]+', headline)

        stop_kr = {"에서", "으로", "하며", "이며", "하여", "있다", "했다", "했으며",
                   "되었", "되었다", "것으로", "대비", "전년", "동기", "기준", "기록",
                   "증가", "감소", "상회", "초과", "보고", "달러", "원으로",
                   "위해", "통해", "대한", "관한", "관련", "주요", "해당",
                   "발표", "출시", "공개", "도입"}
        stop_en = {"the", "and", "for", "with", "from", "that", "this", "are",
                   "was", "has", "have", "been", "will", "its", "new"}

        result = set()
        for t in merged:
            t = t.strip()
            if len(t) >= 2 and t.lower() not in stop_en:
                result.add(t)
        for t in korean:
            if t not in stop_kr:
                result.add(t)
        for t in numbers:
            result.add(t)

        return result

    _PRODUCT_NAME_PATTERN = re.compile(r'[a-z]+\d')

    @classmethod
    def _token_similarity(cls, set_a: set[str], set_b: set[str]) -> int:
        """두 토큰 집합의 매칭 수를 계산한다 (정규화 + 부분 문자열 매칭).

        영문+숫자 조합 토큰(제품명/모델명: gpt5.5, claude4.7 등)이 일치하면
        가중치 2로 계산하여 단일 제품명 매칭만으로도 병합 후보가 될 수 있게 한다.
        """
        if not set_a or not set_b:
            return 0

        norm_a = {cls._normalize_token(t): t for t in set_a}
        norm_b = {cls._normalize_token(t): t for t in set_b}

        # 정규화 후 완전 일치
        matched_keys = set(norm_a.keys()) & set(norm_b.keys())
        matches = 0
        for key in matched_keys:
            if cls._PRODUCT_NAME_PATTERN.search(key):
                matches += 2
            else:
                matches += 1

        # 부분 문자열 매칭 (이미 매칭된 것 제외)
        remaining_a = {k for k in norm_a if k not in matched_keys}
        remaining_b = {k for k in norm_b if k not in matched_keys}

        for na in remaining_a:
            if len(na) < 3:
                continue
            for nb in remaining_b:
                if len(nb) < 3:
                    continue
                if na in nb or nb in na:
                    matches += 1
                    break

        return matches

    def _find_merge_candidates(self, topics: list[MergedTopic]) -> list[list[int]]:
        """headline 토큰 유사도로 병합 후보군을 찾는다 (LLM 호출 없이)."""
        token_sets = [self._extract_key_tokens(t.headline) for t in topics]
        n = len(topics)
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i in range(n):
            if not token_sets[i]:
                continue
            for j in range(i + 1, n):
                if not token_sets[j]:
                    continue
                matched = self._token_similarity(token_sets[i], token_sets[j])
                smaller = min(len(token_sets[i]), len(token_sets[j]))
                ratio = matched / smaller if smaller > 0 else 0
                if matched >= 2 and ratio >= 0.4:
                    union(i, j)

        groups: dict[int, list[int]] = {}
        for i in range(n):
            root = find(i)
            groups.setdefault(root, []).append(i)

        return [indices for indices in groups.values() if len(indices) >= 2]

    @staticmethod
    def _merge_topic_group(topics: list[MergedTopic], indices: list[int]) -> MergedTopic:
        """토픽 인덱스 그룹을 하나의 MergedTopic으로 병합."""
        base = topics[indices[0]]
        combined_post_ids = []
        combined_bullets = []
        combined_sources = []
        combined_urls = []
        best_score = 0.0
        best_headline = base.headline

        for idx in indices:
            t = topics[idx]
            combined_post_ids.extend(t.post_ids)
            combined_bullets.extend(t.body_bullets)
            combined_sources.extend(t.sources)
            combined_urls.extend(t.source_urls)
            if t.importance_score > best_score:
                best_score = t.importance_score
                best_headline = t.headline

        combined_sources = list(dict.fromkeys(combined_sources))
        combined_urls = list(dict.fromkeys(combined_urls))

        return MergedTopic(
            post_ids=combined_post_ids,
            headline=best_headline,
            body_bullets=combined_bullets,
            primary_category=base.primary_category,
            importance_score=best_score,
            sources=combined_sources,
            source_urls=combined_urls,
        )

    async def _cross_chunk_merge(self, topics: list[MergedTopic]) -> list[MergedTopic]:
        """청크 간 동일 사건 토픽을 2차 병합한다.

        1단계: headline 토큰 유사도로 후보군 탐색 (빠르고 확실한 매칭)
        2단계: 후보군 내에서 LLM으로 최종 병합 판정 (의미 기반 검증)
        """
        candidate_groups = self._find_merge_candidates(topics)

        if not candidate_groups:
            logger.info("2차 청크 간 병합: 병합 후보 없음")
            return topics

        logger.info(f"2차 청크 간 병합: {len(candidate_groups)}개 후보군 발견")

        merged_indices: set[int] = set()
        merged_topics: list[MergedTopic] = []

        for group_indices in candidate_groups:
            if len(group_indices) <= 3:
                merged_topics.append(self._merge_topic_group(topics, group_indices))
                merged_indices.update(group_indices)
                continue

            # 4개 이상이면 LLM으로 세부 검증
            group_summary = []
            for idx in group_indices:
                t = topics[idx]
                first_bullet = t.body_bullets[0] if t.body_bullets else ""
                group_summary.append({
                    "index": idx,
                    "headline": t.headline,
                    "summary": first_bullet[:200],
                    "category": t.primary_category,
                })

            topics_json = json.dumps(group_summary, ensure_ascii=False, indent=2)
            prompt = CROSS_CHUNK_MERGE.format(topics_json=topics_json)

            try:
                response_text = self._call_api(
                    self._config.model_process, prompt, max_tokens=4096
                )
                sub_groups = _parse_json_response(response_text)

                if sub_groups:
                    sub_merged: set[int] = set()
                    for sg in sub_groups:
                        sg_indices = [i for i in sg.get("merge_indices", []) if 0 <= i < len(topics)]
                        if len(sg_indices) >= 2:
                            result = self._merge_topic_group(topics, sg_indices)
                            if sg.get("headline"):
                                result.headline = sg["headline"]
                            merged_topics.append(result)
                            sub_merged.update(sg_indices)
                    merged_indices.update(sub_merged)
                    for idx in group_indices:
                        if idx not in sub_merged:
                            pass  # 아래 병합되지 않은 토픽 유지에서 처리
                else:
                    merged_topics.append(self._merge_topic_group(topics, group_indices))
                    merged_indices.update(group_indices)

            except Exception as e:
                logger.warning(f"후보군 LLM 검증 실패, 토큰 매칭 기준으로 병합: {e}")
                merged_topics.append(self._merge_topic_group(topics, group_indices))
                merged_indices.update(group_indices)

        for i, t in enumerate(topics):
            if i not in merged_indices:
                merged_topics.append(t)

        logger.info(f"2차 청크 간 병합: {len(topics)}개 → {len(merged_topics)}개 토픽")
        return merged_topics
