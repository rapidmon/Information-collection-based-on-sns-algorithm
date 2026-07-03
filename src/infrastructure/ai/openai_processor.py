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

from src.domain.services.ai_processor import (
    CategoryCuration,
    CategoryResult,
    Curation,
    FilterResult,
    MergedTopic,
    VerificationResult,
)
from src.infrastructure.ai.prompts import (
    CATEGORIZE,
    CROSS_CHUNK_MERGE,
    CURATION,
    DEDUPLICATE_AND_MERGE,
    EXTRACT_CLAIMS,
    FILTER_AND_SUMMARIZE,
    SYSTEM_PROMPT,
    TIER,
    VERIFY_CLAIMS,
)
from src.infrastructure.ai.topic_merger import TopicMerger
from src.infrastructure.config.settings import ProcessingConfig

logger = logging.getLogger(__name__)


def _chunked(lst: list, size: int):
    """리스트를 size 크기의 청크로 분할."""
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def _build_calibration_block(examples: list | None, per_side: int = 6) -> str:
    """사용자 피드백(과대/과소)을 티어 판정용 few-shot 보정 텍스트로 변환."""
    if not examples:
        return ""
    under = [e for e in examples if e.get("label") == "under"][:per_side]  # 더 높게
    over = [e for e in examples if e.get("label") == "over"][:per_side]    # 더 낮게
    if not under and not over:
        return ""
    lines = ["", "## 사용자 보정 예시 (과거 피드백 — 등급 판정 시 반영)"]
    if under:
        lines.append("아래와 비슷한 사건은 그동안 **과소평가**됐다 → 한 단계 더 높게 볼 것:")
        lines += [f"  · {e.get('headline','')}" for e in under]
    if over:
        lines.append("아래와 비슷한 사건은 그동안 **과대평가**됐다 → 한 단계 더 낮게 볼 것:")
        lines += [f"  · {e.get('headline','')}" for e in over]
    return "\n".join(lines) + "\n"


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


def _extract_balanced_json(text: str, open_ch: str, close_ch: str) -> str:
    """응답에서 첫 번째 '균형 잡힌' open_ch...close_ch 스팬을 추출한다.

    문자열/이스케이프를 인식하며 깊이를 추적하므로, 코드펜스나 뒤에 붙은
    잡텍스트("Extra data")가 있어도 완결된 JSON만 정확히 잘라낸다.
    """
    text = text.strip()
    start = text.find(open_ch)
    if start == -1:
        raise ValueError(f"JSON({open_ch}) 없음: {text[:200]}")

    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    # 닫는 괄호를 못 찾은 경우: 마지막 close_ch까지 시도
    return text[start : text.rfind(close_ch) + 1]


def _parse_json_object(text: str) -> dict[str, Any]:
    """API 응답에서 첫 번째 균형 잡힌 JSON 객체({...})를 추출."""
    return json.loads(_extract_balanced_json(text, "{", "}"))


def _parse_json_response(text: str) -> list[dict[str, Any]]:
    """API 응답에서 첫 번째 균형 잡힌 JSON 배열([...])을 추출."""
    return json.loads(_extract_balanced_json(text, "[", "]"))


class BaseLLMProcessor:
    """LLM 백엔드 공용 처리 파이프라인.

    필터/요약·분류·티어·중복제거/통합·큐레이션 등 백엔드에 무관한 로직을 담는다.
    실제 LLM 호출(_call_api)과 신뢰도 검증(verify_claims)만 서브클래스가 구현/오버라이드한다.
    """

    # 상태 없는 순수 병합 로직 — 클래스 속성으로 공유(서브클래스 포함)
    _merger = TopicMerger()

    def _call_api(self, model: str, prompt: str, max_tokens: int = 4096) -> str:
        """LLM 호출. 백엔드별 서브클래스(OpenAIProcessor/ClaudeCodeProcessor)가 구현한다."""
        raise NotImplementedError

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

    async def judge_tiers(self, topics: list, calibration_examples: list | None = None) -> list[str]:
        """클러스터(이벤트)별 뉴스가치 절대 등급(major/notable/minor)을 판정한다.

        중요도의 '주관적 자유 점수' 대신, LLM은 이 이산 등급만 판정하고
        실제 점수는 객관 신호(빈도·인게이지먼트)와 결합해 코드에서 계산한다.
        calibration_examples가 주어지면(사용자 과대/과소 피드백) few-shot 보정으로 주입.
        """
        if not topics:
            return []

        items = [
            {
                "index": i,
                "headline": t.headline,
                "summary": " / ".join((t.body_bullets or [])[:3]),
            }
            for i, t in enumerate(topics)
        ]
        prompt = TIER.format(
            calibration=_build_calibration_block(calibration_examples),
            topics_json=json.dumps(items, ensure_ascii=False),
        )

        tiers = ["minor"] * len(topics)
        try:
            response_text = self._call_api(self._config.model_filter, prompt, max_tokens=4096)
            parsed = _parse_json_response(response_text)
            for it in parsed:
                idx = it.get("index")
                tier = (it.get("tier") or "minor").lower()
                if isinstance(idx, int) and 0 <= idx < len(tiers) and tier in ("major", "notable", "minor"):
                    tiers[idx] = tier
        except Exception as e:
            logger.warning(f"티어 판정 실패(전부 minor 처리): {e}")

        logger.info(f"티어 판정: major={tiers.count('major')} notable={tiers.count('notable')} minor={tiers.count('minor')}")
        return tiers

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
                            headline=p.summary or (p.content_text or "")[:100],
                            body_bullets=[p.summary or (p.content_text or "")[:300]],
                            primary_category=p.category_names[0] if p.category_names else "Other",
                            importance_score=p.importance_score or 0.5,
                            sources=[p.source],
                            source_urls=[p.url] if p.url else [],
                        )
                    )

        num_chunks = (len(posts) - 1) // chunk_size + 1
        logger.info(f"1차 중복제거: {len(posts)}건 → {len(all_results)}개 토픽 ({num_chunks}개 청크)")

        # 2차: 전역 통합 — 토픽이 2개 이상이면 항상 실행(단일 청크여도)
        if len(all_results) >= 2:
            all_results = await self._consolidate_topics(all_results)

        logger.info(f"최종 토픽 수: {len(all_results)}개")
        return all_results

    async def generate_curation(self, topics: list[MergedTopic], audience: str) -> Curation:
        """독자층 맞춤 큐레이션 생성 (1회 LLM 호출로 전체+카테고리별)."""
        if not topics:
            return Curation(title="", paragraphs=[], kick="", categories={})

        by_cat: dict[str, list[MergedTopic]] = {}
        for t in topics:
            by_cat.setdefault(t.primary_category or "Other", []).append(t)

        summary: dict[str, list[dict]] = {}
        for cat, ts in by_cat.items():
            ts = sorted(ts, key=lambda x: x.importance_score or 0, reverse=True)[:8]
            summary[cat] = [
                {"headline": t.headline,
                 "fact": (t.body_bullets[0] if t.body_bullets else "")[:160]}
                for t in ts
            ]
        topics_json = json.dumps(summary, ensure_ascii=False, indent=2)
        prompt = CURATION.format(audience=audience, topics_json=topics_json)

        data = None
        for attempt in range(2):
            try:
                response_text = self._call_api(self._config.model_process, prompt, max_tokens=4096)
                data = _parse_json_object(response_text)
                break
            except Exception as e:
                logger.warning(f"큐레이션 생성 시도 {attempt + 1} 실패 (audience={audience}): {e}")
        if data is None:
            return Curation(title="", paragraphs=[], kick="", categories={})

        overall = data.get("overall", {}) or {}
        cats: dict[str, CategoryCuration] = {}
        for cat, c in (data.get("categories", {}) or {}).items():
            if not isinstance(c, dict):
                continue
            cats[cat] = CategoryCuration(
                hook=str(c.get("hook", "")),
                bullets=[str(b) for b in (c.get("bullets") or []) if b][:3],
                insight=str(c.get("insight", "")),
            )

        curation = Curation(
            title=str(overall.get("title", "")),
            paragraphs=[str(p) for p in (overall.get("paragraphs") or []) if p][:3],
            kick=str(overall.get("kick", "")),
            categories=cats,
        )
        logger.info(
            f"큐레이션 생성 완료 (audience={audience}): "
            f"카테고리 {len(cats)}개, kick={'있음' if curation.kick else '없음'}"
        )
        return curation

    async def _consolidate_topics(self, topics: list[MergedTopic]) -> list[MergedTopic]:
        """최종 전역 통합. 토픽 수가 적당하면 전체를 LLM에 한 번에 보내 의미 기반 병합,
        너무 많으면 토큰 유사도 기반(_cross_chunk_merge) 폴백."""
        if len(topics) < 2:
            return topics
        if len(topics) <= 80:
            merged = await self._global_llm_merge(topics)
            if merged is not None:
                logger.info(f"전역 통합(LLM): {len(topics)}개 → {len(merged)}개 토픽")
                return merged
        return await self._cross_chunk_merge(topics)

    async def _global_llm_merge(self, topics: list[MergedTopic]) -> list[MergedTopic] | None:
        """전체 토픽을 한 번의 LLM 호출로 의미 기반 병합. 실패 시 None."""
        summary = []
        for i, t in enumerate(topics):
            first_bullet = t.body_bullets[0] if t.body_bullets else ""
            summary.append({
                "index": i,
                "headline": t.headline,
                "summary": first_bullet[:200],
                "category": t.primary_category,
            })
        topics_json = json.dumps(summary, ensure_ascii=False, indent=2)
        prompt = CROSS_CHUNK_MERGE.format(topics_json=topics_json)

        try:
            response_text = self._call_api(
                self._config.model_process, prompt, max_tokens=8192
            )
            groups = _parse_json_response(response_text)
        except Exception as e:
            logger.warning(f"전역 통합 LLM 실패, 폴백 사용: {e}")
            return None

        merged_indices: set[int] = set()
        result: list[MergedTopic] = []
        for g in groups:
            idxs = [
                i for i in g.get("merge_indices", [])
                if isinstance(i, int) and 0 <= i < len(topics) and i not in merged_indices
            ]
            if len(idxs) < 2:
                continue
            merged = self._merger.merge_topic_group(topics, idxs)
            if g.get("headline"):
                merged.headline = g["headline"]
            # LLM이 종합한 간결한 세부(≤3)가 있으면 채택(단순 이어붙이기 대체)
            bullets = g.get("body_bullets")
            if isinstance(bullets, list) and bullets:
                merged.body_bullets = [str(b) for b in bullets if b][:3]
            if isinstance(g.get("importance_score"), (int, float)):
                merged.importance_score = max(merged.importance_score, g["importance_score"])
            result.append(merged)
            merged_indices.update(idxs)

        # 병합되지 않은 토픽 그대로 유지
        for i, t in enumerate(topics):
            if i not in merged_indices:
                result.append(t)

        return result

    async def _cross_chunk_merge(self, topics: list[MergedTopic]) -> list[MergedTopic]:
        """청크 간 동일 사건 토픽을 2차 병합한다.

        1단계: headline 토큰 유사도로 후보군 탐색 (빠르고 확실한 매칭)
        2단계: 후보군 내에서 LLM으로 최종 병합 판정 (의미 기반 검증)
        """
        candidate_groups = self._merger.find_merge_candidates(topics)

        if not candidate_groups:
            logger.info("2차 청크 간 병합: 병합 후보 없음")
            return topics

        logger.info(f"2차 청크 간 병합: {len(candidate_groups)}개 후보군 발견")

        merged_indices: set[int] = set()
        merged_topics: list[MergedTopic] = []

        for group_indices in candidate_groups:
            if len(group_indices) <= 3:
                merged_topics.append(self._merger.merge_topic_group(topics, group_indices))
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
                            result = self._merger.merge_topic_group(topics, sg_indices)
                            if sg.get("headline"):
                                result.headline = sg["headline"]
                            merged_topics.append(result)
                            sub_merged.update(sg_indices)
                    merged_indices.update(sub_merged)
                    for idx in group_indices:
                        if idx not in sub_merged:
                            pass  # 아래 병합되지 않은 토픽 유지에서 처리
                else:
                    merged_topics.append(self._merger.merge_topic_group(topics, group_indices))
                    merged_indices.update(group_indices)

            except Exception as e:
                logger.warning(f"후보군 LLM 검증 실패, 토큰 매칭 기준으로 병합: {e}")
                merged_topics.append(self._merger.merge_topic_group(topics, group_indices))
                merged_indices.update(group_indices)

        for i, t in enumerate(topics):
            if i not in merged_indices:
                merged_topics.append(t)

        logger.info(f"2차 청크 간 병합: {len(topics)}개 → {len(merged_topics)}개 토픽")
        return merged_topics


class OpenAIProcessor(BaseLLMProcessor):
    """OpenAI GPT API 백엔드 (Chat Completions + DuckDuckGo 웹검증)."""

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
        return response.choices[0].message.content or ""

    async def verify_claims(self, posts: list[Post]) -> list[VerificationResult]:
        """게시물의 핵심 주장을 웹 검색(DuckDuckGo)으로 교차 검증."""
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
            if c.get("needs_verification") and c.get("claim") and c.get("post_id")
        ]

        if not claims_to_verify:
            logger.info("검증 필요한 주장 없음, 전체 통과")
            return [
                VerificationResult(post_id=p.id, credibility="verified")
                for p in posts
            ]

        # 2단계: 웹 검색으로 각 주장 검증
        # 검색이 '실패'(rate limit 등)한 주장은 검증 대상에서 제외한다.
        # (검색 인프라 장애로 정상 뉴스를 스캠으로 오판해 떨어뜨리는 것을 방지)
        verification_data = []
        search_failed = 0
        for claim_item in claims_to_verify:
            search_results = self._web_search(claim_item["claim"])
            if search_results is None:
                search_failed += 1
                continue
            verification_data.append({
                "post_id": claim_item["post_id"],
                "claim": claim_item["claim"],
                "search_results": search_results,
            })

        if search_failed:
            logger.warning(
                f"웹 검색 실패 {search_failed}건 — 해당 게시물은 검증 스킵(통과 처리)"
            )

        # 검증 가능한 주장이 하나도 없으면(전부 검색 실패) 전체 통과
        if not verification_data:
            logger.info("검색 가능한 주장 없음(검색 실패) — 전체 통과")
            return [
                VerificationResult(post_id=p.id, credibility="verified")
                for p in posts
            ]

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

    def _web_search(self, query: str, max_results: int = 5) -> list[dict] | None:
        """DuckDuckGo로 웹 검색.

        - 성공: 결과 리스트 반환(결과가 없으면 빈 리스트 []).
        - 실패(rate limit/네트워크 등): None 반환 → 호출부가 '검증 불가'로 처리해
          정상 게시물을 스캠으로 오판하지 않도록 한다.
        """
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
            return [
                {"title": r.get("title", ""), "body": r.get("body", ""), "href": r.get("href", "")}
                for r in results
            ]
        except Exception as e:
            logger.warning(f"웹 검색 실패 '{query[:50]}': {e}")
            return None
