"""OpenAI API 기반 AI 프로세서 구현.

도메인 AIProcessor 인터페이스를 구현한다.
GPT-4o-mini로 필터링, GPT-4o로 요약/분류/통합 — 비용 최적화.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from bs4 import BeautifulSoup
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
    normalize_topic_bullets,
)
from src.infrastructure.ai.prompts import (
    CATEGORIZE,
    COMPOSE_TOPICS,
    CROSS_CHUNK_MERGE,
    CURATION,
    EXTRACT_CLAIMS,
    FILTER_AND_SUMMARIZE,
    RECENT_COVERAGE_DEDUP,
    SYSTEM_PROMPT,
    TIER,
    VERIFY_CLAIMS,
    build_feedback_calibration,
)
from src.infrastructure.ai.topic_merger import TopicMerger
from src.infrastructure.collectors.http import fetch_text
from src.infrastructure.config.settings import ProcessingConfig
from src.infrastructure.delivery.categories import VALID_BRIEFING_CATEGORIES

logger = logging.getLogger(__name__)

# 웹검증 시 본문을 fetch할 상위 검색 결과 수 / 발췌 최대 길이.
# 스니펫만으론 루머·전망 기사("what we know" 류)를 확인 출처로 오독하기 쉬워
# 상위 결과의 실제 본문(대개 도입부에 "아직 미출시" 같은 결정적 문장)을 판정에 공급한다.
VERIFY_FETCH_TOP_N = 2
VERIFY_PAGE_MAX_CHARS = 3000

# 기브리핑 사건 판정 시 한 호출에 넣을 후보 수 상한.
# 후보가 많을수록 비교 recall이 급락하므로 반드시 분할한다.
COVERAGE_DEDUP_CHUNK_SIZE = 50

# 기브리핑 판정의 matched(지목된 최근 항목) 근거 검증 시 부분 일치를 허용할 최소 길이.
# 너무 짧은 문자열은 우연히 어느 항목에나 포함될 수 있어 근거로 치지 않는다.
_MATCHED_MIN_PARTIAL_LEN = 15


def _matched_in_recent(matched: str, recent_items: list[str]) -> bool:
    """중복 판정이 지목한 근거(matched)가 실제 최근 브리핑 항목인지 검증.

    "같은 분야의 비슷한 발표"까지 중복으로 쓸어담는 과잉 판정(yes-bias)을
    막는 구조적 가드 — 실존 항목을 지목하지 못한 판정은 기각된다.
    """
    m = matched.strip().lstrip("-").strip()
    if not m:
        return False
    for r in recent_items:
        rs = r.strip()
        if m == rs:
            return True
        if len(m) >= _MATCHED_MIN_PARTIAL_LEN and (m in rs or rs in m):
            return True
    return False


class LLMBackendError(RuntimeError):
    """LLM 백엔드 자체 장애 (CLI 미설치·인증 만료·한도 소진·타임아웃).

    개별 응답 파싱 실패와 달리 같은 백엔드로 재시도해도 소용없는 오류.
    compose/curation의 내부 예외 처리를 통과해 상위(hybrid)로 전파되어야
    OpenAI 폴백이 트리거된다.
    """


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


def _posts_to_json_filter(posts: list[Post]) -> str:
    """필터 단계용 JSON — 이 시점에 값이 없는 필드를 빼고 들여쓰기도 없앤다.

    summary/categories/importance_score 는 필터 **이후** 단계가 채우므로 필터
    시점엔 항상 비어 있다(키만 실려 간다). url 은 게시물 퍼머링크이고 필터 규칙이
    참조하지 않는다 — "링크만 있고 본문이 3문장 미만" 판정은 text 로 한다.
    indent 도 모델에 불필요하다.

    실측(40건 배치): 입력 −13.5%. 무손실.
    """
    items = [
        {
            "post_id": p.id,
            "source": p.source,
            "author": p.author,
            "text": p.content_text[:1000] if p.content_text else "",
        }
        for p in posts
    ]
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


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


def _primary_category_from_post(post: Post) -> str:
    for cat in post.category_names or []:
        if cat in VALID_BRIEFING_CATEGORIES:
            return cat
    return "Other"


def _fallback_topic_from_post(post: Post) -> MergedTopic:
    return MergedTopic(
        post_ids=[post.id] if post.id else [],
        headline=post.summary or (post.content_text or "")[:100],
        body_bullets=[post.summary or (post.content_text or "")[:300]],
        primary_category=_primary_category_from_post(post),
        importance_score=post.importance_score or 0.5,
        sources=[post.source],
        source_urls=[post.url] if post.url else [],
    )


def _singleton_topic_from_post(post: Post) -> MergedTopic:
    """Build a deterministic singleton topic used for candidate discovery."""
    return _fallback_topic_from_post(post)


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

    def _call_api(
        self, model: str, prompt: str, max_tokens: int = 4096, *, lean: bool = False
    ) -> str:
        """LLM 호출. 백엔드별 서브클래스(OpenAIProcessor/ClaudeCodeProcessor)가 구현한다.

        lean=True 는 "규칙이 프롬프트에 명시된 기계적 배치 작업"이라는 신호다
        (추론·도구가 품질에 기여하지 않으므로 백엔드가 그것들을 끌 수 있다).
        ⚠️ **모델 티어로 판단하면 안 된다** — judge_tiers 는 model_filter 를 쓰지만
        피드백 보정 기반의 품질 민감 판정이라 추론이 필요하다. 작업 단위로 지정한다.
        """
        raise NotImplementedError

    def _curation_model(self) -> str:
        """Model to use for curation JSON generation."""
        return self._config.model_process

    async def filter_and_summarize(self, posts: list[Post]) -> list[FilterResult]:
        """관련성 필터 + 요약 (GPT-4o-mini 사용, 배치)."""
        results: list[FilterResult] = []

        for batch in _chunked(posts, self._config.batch_size_filter):
            posts_json = _posts_to_json_filter(batch)
            prompt = FILTER_AND_SUMMARIZE.format(posts_json=posts_json)

            try:
                # gpt-5 계열은 추론 토큰이 completion 한도를 같이 소모 — 배치 40건
                # JSON이 잘리면 누락 게시물이 비관련 처리되므로 한도를 넉넉히 준다.
                # to_thread: 동기 API 호출이 이벤트 루프(웹·수집 잡)를 막지 않게.
                # lean: 규칙이 프롬프트에 명시된 기계적 분류 — 추론/도구가 품질에 기여하지 않는다.
                response_text = await asyncio.to_thread(
                    self._call_api, self._config.model_filter, prompt, 16384, lean=True
                )
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
                # 실패 시 비관련으로 컷(요약 없음). 과거엔 '전부 관련'으로 통과시켰으나,
                # 한도/파싱 실패 시 원문 쓰레기가 브리핑 풀에 쏟아져 품질이 붕괴됐다.
                # 진행은 계속하되(큐 스톨 방지) 미검증 원문은 발행 대상에서 제외한다.
                for p in batch:
                    results.append(
                        FilterResult(
                            post_id=p.id,
                            is_relevant=False,
                            summary=None,
                            language="unknown",
                        )
                    )

        logger.info(
            f"필터/요약 완료: {len(results)}건 (관련: {sum(1 for r in results if r.is_relevant)}건)"
        )
        return results

    def set_feedback_examples(self, examples: list[dict]) -> None:
        """독자 피드백(과대/과소) 예시 주입 — categorize 중요도 채점의 few-shot 보정."""
        self._feedback_examples = examples or []

    async def categorize(self, posts: list[Post]) -> list[CategoryResult]:
        """카테고리 분류 + 중요도 (gpt-4o-mini 사용, 배치)."""
        results: list[CategoryResult] = []
        # ClaudeCodeProcessor는 super().__init__을 안 타므로 getattr로 방어
        feedback_block = build_feedback_calibration(getattr(self, "_feedback_examples", []))
        if feedback_block:
            logger.info(
                f"분류 프롬프트에 독자 피드백 보정 주입: "
                f"{len(getattr(self, '_feedback_examples', []))}건"
            )

        for batch in _chunked(posts, self._config.batch_size_categorize):
            posts_json = _posts_to_json_lite(batch)
            prompt = CATEGORIZE.format(posts_json=posts_json, feedback_block=feedback_block)

            try:
                # 추론 토큰과 배치 JSON이 한도를 나눠 쓰므로 잘림 방지용 상향
                # lean: 필터와 같은 기계적 배치 분류. 지금은 OpenAI 경로라 무동작이지만
                # 향후 이 단계를 Claude로 옮길 때 추론이 되살아나지 않게 미리 표시한다.
                response_text = await asyncio.to_thread(
                    self._call_api, self._config.model_filter, prompt, 16384, lean=True
                )
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
                # 폴백 결과를 만들지 않고 배치를 통째로 누락시킨다 — 과거의
                # ["Other"] 폴백은 sanitize에서 전멸해 배치 40건이 비관련으로
                # 강등·삭제됐다. 누락된 게시물은 process_posts가 DB 업데이트에서
                # 제외해 다음 사이클에 자동 재시도된다.
                logger.error(f"분류 API 호출 실패(배치 {len(batch)}건 다음 사이클 재시도): {e}")

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

    def _dedup_candidate_groups(self, posts: list[Post]) -> list[list[Post]]:
        """Create stable merge candidate groups from the full post set.

        This step must not depend on `dedup_chunk_size`. The chunk size should be
        only a throughput/token parameter, not something that changes briefing
        membership. Candidate groups are based on deterministic token similarity
        over all singleton topics.
        """
        sorted_posts = sorted(
            [p for p in posts if p.id is not None],
            key=lambda p: (str(p.published_at or p.collected_at or ""), str(p.id)),
        )
        singleton_topics = [_singleton_topic_from_post(p) for p in sorted_posts]
        candidate_indices = self._merger.find_merge_candidates(singleton_topics)

        grouped_indices = {
            idx
            for group in candidate_indices
            for idx in group
        }
        groups = [
            [sorted_posts[idx] for idx in group]
            for group in candidate_indices
        ]
        groups.extend(
            [post]
            for idx, post in enumerate(sorted_posts)
            if idx not in grouped_indices
        )
        groups.sort(key=lambda group: (str(group[0].published_at or group[0].collected_at or ""), str(group[0].id)))
        return groups

    def _merge_group_deterministic(self, group: list[Post]) -> MergedTopic:
        """후보군 하나를 LLM 없이 하나의 토픽으로 병합한다.

        headline은 그룹 내 최고 중요도 게시물의 요약, 불릿은 멤버 요약들(≤3).
        발행이 확정되면 compose_topics()가 이 초안을 리라이트한다.
        """
        singletons = [_singleton_topic_from_post(p) for p in group]
        if len(singletons) == 1:
            return singletons[0]
        return self._merger.merge_topic_group(singletons, list(range(len(singletons))))

    async def deduplicate_and_merge(self, posts: list[Post]) -> list[MergedTopic]:
        """중복 제거 + 토픽 통합 (결정적, LLM 미사용).

        토큰 유사도 후보군을 그대로 토픽으로 병합만 한다. headline/불릿 작문은
        점수·선별이 끝난 뒤 발행 확정 항목에만 compose_topics()로 수행해,
        탈락할 토픽까지 LLM으로 작문하던 낭비를 없앤다.
        """
        if not posts:
            return []

        candidate_groups = self._dedup_candidate_groups(posts)
        all_results = [self._merge_group_deterministic(g) for g in candidate_groups]

        logger.info(
            "중복제거/병합(결정적): %s건 → %s개 토픽 (LLM 미사용)",
            len(posts),
            len(all_results),
        )
        return all_results

    async def compose_topics(self, topics: list[MergedTopic], posts: list[Post]) -> list[MergedTopic]:
        """발행 확정 토픽만 LLM으로 headline/불릿 작문 (배치, in-place).

        기계적 병합으로 다른 사건이 섞인 그룹은 LLM이 중심 사건 위주로 정리하고
        무관한 post_ids를 덜어낼 수 있다. 실패한 배치는 결정적 초안을 유지한다.
        """
        if not topics:
            return topics

        post_map = {str(p.id): p for p in posts if p.id is not None}
        batch_size = 20
        num_batches = (len(topics) - 1) // batch_size + 1

        for start in range(0, len(topics), batch_size):
            batch = topics[start : start + batch_size]
            payload = []
            for i, t in enumerate(batch):
                members = [post_map[str(pid)] for pid in (t.post_ids or []) if str(pid) in post_map]
                payload.append({
                    "index": i,
                    "category": t.primary_category,
                    "posts": [
                        {
                            "id": str(m.id),
                            "source": m.source,
                            "summary": m.summary or (m.content_text or "")[:200],
                        }
                        for m in members
                    ],
                })
            prompt = COMPOSE_TOPICS.format(
                topics_json=json.dumps(payload, ensure_ascii=False)
            )

            try:
                response_text = await asyncio.to_thread(
                    self._call_api, self._config.model_process, prompt, 8192
                )
                parsed = _parse_json_response(response_text)
            except LLMBackendError:
                raise  # 백엔드 장애는 상위(hybrid)로 — OpenAI 폴백 트리거
            except Exception as e:
                logger.warning(f"발행 항목 작문 실패(결정적 초안 유지): {e}")
                continue

            by_index = {
                item.get("index"): item for item in parsed if isinstance(item, dict)
            }
            for i, t in enumerate(batch):
                item = by_index.get(i)
                if not item:
                    continue
                headline = str(item.get("headline") or "").strip()
                if headline and not _is_catch_all(headline, len(t.post_ids or [])):
                    t.headline = headline
                bullets = item.get("body_bullets")
                if isinstance(bullets, list) and bullets:
                    t.body_bullets = normalize_topic_bullets([str(b) for b in bullets if b])
                # LLM이 무관 판정으로 덜어낸 post_ids 반영 (원래 멤버의 부분집합만 허용)
                original_ids = {str(pid) for pid in (t.post_ids or [])}
                kept_ids = [
                    str(pid) for pid in (item.get("post_ids") or [])
                    if str(pid) in original_ids
                ]
                if kept_ids and len(kept_ids) < len(original_ids):
                    kept_posts = [post_map[pid] for pid in kept_ids if pid in post_map]
                    if kept_posts:
                        t.post_ids = kept_ids
                        t.sources = list(dict.fromkeys(m.source for m in kept_posts))
                        t.source_urls = list(dict.fromkeys(m.url for m in kept_posts if m.url))

        logger.info(f"발행 확정 토픽 작문: {len(topics)}건 (LLM 배치 {num_batches}회)")
        return topics

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
                response_text = await asyncio.to_thread(
                    self._call_api, self._curation_model(), prompt, 8192
                )
                data = _parse_json_object(response_text)
                break
            except LLMBackendError:
                raise  # 백엔드 장애는 상위(hybrid)로 — OpenAI 폴백 트리거
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

    def _curation_model(self) -> str:
        # Curation needs reliable short JSON, not heavyweight reasoning.
        return self._config.model_filter

    def _call_api(
        self, model: str, prompt: str, max_tokens: int = 4096, *, lean: bool = False
    ) -> str:
        """OpenAI Chat Completions API 동기 호출.

        gpt-5 계열(추론 모델)은 추론 토큰이 completion 한도를 같이 소모해
        content가 비어 올 수 있다 — 이 경우 한도를 2배로 올려 1회만 재시도하고,
        그래도 비면 명시적 예외를 던져 호출부의 실패 처리(배치 스킵 등)를 태운다.

        lean 은 받되 사용하지 않는다 — 이 백엔드는 이미 모든 gpt-5 호출에
        reasoning_effort="low" 를 걸고 있어 추가로 끌 것이 없다.
        """
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
            # 분류/요약류 배치 작업엔 긴 추론이 불필요 — 추론 토큰(출력 과금) 절감
            params["reasoning_effort"] = "low"

        for attempt in range(2):
            response = self._client.chat.completions.create(**params)
            choice = response.choices[0]
            content = choice.message.content or ""
            # 비용 실측용 — 추론 토큰(출력 과금)이 지배 비용이라 호출마다 기록
            usage = getattr(response, "usage", None)
            if usage:
                details = getattr(usage, "completion_tokens_details", None)
                reasoning = getattr(details, "reasoning_tokens", 0) if details else 0
                logger.info(
                    f"[usage] model={model} in={usage.prompt_tokens} "
                    f"out={usage.completion_tokens} (reasoning={reasoning})"
                )
            if content.strip():
                return content
            if is_legacy or attempt == 1:
                break
            logger.warning(
                f"빈 응답(finish_reason={choice.finish_reason}, model={model}) — "
                f"completion 한도 {max_tokens}→{max_tokens * 2}로 1회 재시도"
            )
            params["max_completion_tokens"] = max_tokens * 2

        raise RuntimeError(
            f"LLM 빈 응답 (model={model}, finish_reason={choice.finish_reason})"
        )

    async def find_covered_topics(
        self, topics: list[MergedTopic], recent_items: list[str]
    ) -> list[int]:
        """최근 브리핑에서 이미 다룬 사건과 같은 사건인 토픽의 인덱스 목록.

        후보를 청크로 나눠 판정한다 — 수백 개를 단일 호출로 비교하면
        건초더미가 커져 명백한 중복도 놓친다.
        청크 하나가 실패하면 해당 청크만 건너뛴다(중복 발행이 잘못 삭제보다 낫다).
        """
        if not topics or not recent_items:
            return []

        recent_block = "\n".join(f"- {s}" for s in recent_items)
        indexed = list(enumerate(topics))
        dup_indexes: list[int] = []
        ungrounded = 0
        for chunk in _chunked(indexed, COVERAGE_DEDUP_CHUNK_SIZE):
            chunk_index_set = {i for i, _ in chunk}
            candidates = json.dumps(
                [
                    {
                        "index": i,
                        "headline": t.headline,
                        "summary": (t.body_bullets or [""])[0],
                    }
                    for i, t in chunk
                ],
                ensure_ascii=False,
                indent=2,
            )
            prompt = RECENT_COVERAGE_DEDUP.format(
                recent_items=recent_block,
                candidates=candidates,
            )
            try:
                response_text = await asyncio.to_thread(
                    self._call_api, self._config.model_filter, prompt, 8192
                )
                parsed = _parse_json_response(response_text)
            except Exception as e:
                logger.warning(f"기브리핑 판정 청크 실패(해당 청크만 스킵): {e}")
                continue

            for item in parsed:
                idx = item.get("index")
                if not (
                    item.get("duplicate")
                    and isinstance(idx, int)
                    and idx in chunk_index_set
                ):
                    continue
                matched = item.get("matched")
                if not isinstance(matched, str) or not _matched_in_recent(
                    matched, recent_items
                ):
                    ungrounded += 1
                    continue
                dup_indexes.append(idx)
        if ungrounded:
            logger.info(
                f"기브리핑 판정 {ungrounded}건 기각 — 실존하는 최근 항목(matched) 지목 실패"
            )
        return dup_indexes

    async def consolidate_topics(self, topics: list[MergedTopic]) -> list[MergedTopic]:
        """소규모 토픽 목록의 동일 사건 병합 — 발행 확정분 최종 가드용 공개 진입점."""
        return await self._consolidate_topics(topics)

    async def verify_claims(self, posts: list[Post]) -> list[VerificationResult]:
        """게시물의 핵심 주장을 웹 검색(DuckDuckGo)으로 교차 검증."""
        if not posts:
            return []

        # 1단계: GPT로 검증이 필요한 핵심 주장 추출
        posts_json = _posts_to_json_lite(posts)
        prompt = EXTRACT_CLAIMS.format(posts_json=posts_json)

        try:
            response_text = await asyncio.to_thread(
                self._call_api, self._config.model_filter, prompt, 8192
            )
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
        # 웹검증 대상 상한 — Claude 경로(claude_code_processor)와 동일하게 적용해
        # 검색 호출 수·검증 프롬프트 크기가 후보 수에 비례해 무한정 커지는 것을 막는다.
        if len(claims_to_verify) > self._config.verify_max_claims:
            logger.info(
                "웹검증 주장 %s건 → 상한 %s건으로 컷",
                len(claims_to_verify), self._config.verify_max_claims,
            )
            claims_to_verify = claims_to_verify[: self._config.verify_max_claims]

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
            search_results = await asyncio.to_thread(self._web_search, claim_item["claim"])
            if search_results is None:
                search_failed += 1
                continue
            # 상위 결과 본문 발췌 — 스니펫에 잘리는 결정적 문장(예: "아직 미출시")을 판정에 공급.
            # fetch 실패(paywall·봇 차단)는 발췌 없이 스니펫만으로 판정 (기존과 동일한 관대 폴백)
            excerpts = []
            for r in search_results[:VERIFY_FETCH_TOP_N]:
                if not r.get("href"):
                    continue
                excerpt = await self._fetch_page_excerpt(r["href"])
                if excerpt:
                    excerpts.append({"href": r["href"], "excerpt": excerpt})
            verification_data.append({
                "post_id": claim_item["post_id"],
                "claim": claim_item["claim"],
                "search_results": search_results,
                "page_excerpts": excerpts,
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
            response_text = await asyncio.to_thread(
                self._call_api, self._config.model_filter, verify_prompt, 8192
            )
            parsed = _parse_json_response(response_text)

            for item in parsed:
                # LLM이 post_id를 숫자로 echo해도 매칭되도록 str 정규화
                results.append(VerificationResult(
                    post_id=str(item["post_id"]),
                    credibility=item.get("credibility", "unverified"),
                    reason=item.get("reason"),
                ))
                verified_ids.add(str(item["post_id"]))
        except Exception as e:
            logger.warning(f"신뢰도 판정 실패 (검증 스킵): {e}")

        # 검증 대상이 아닌 게시물은 verified로 처리
        for p in posts:
            if str(p.id) not in verified_ids:
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

    async def _fetch_page_excerpt(self, url: str) -> str | None:
        """검색 결과 페이지의 본문 텍스트 발췌를 반환. 실패 시 None."""
        html = await fetch_text(url, "verify", timeout=15.0)
        if not html:
            return None
        try:
            soup = BeautifulSoup(html, "lxml")
            for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
                tag.decompose()
            text = " ".join(soup.get_text(" ").split())
            return text[:VERIFY_PAGE_MAX_CHARS] or None
        except Exception as e:
            logger.warning(f"본문 파싱 실패 '{url[:60]}': {e}")
            return None

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
