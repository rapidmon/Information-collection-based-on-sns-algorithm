"""유즈케이스: AI 처리 파이프라인.

미처리 게시물(SQLite)을 청크 단위로 가져와 필터링·요약·분류를 수행하고
결과를 DB에 반영한다. 비관련 게시물은 삭제하지 않고 is_relevant=False로만 표시한다.
"""

from __future__ import annotations

import asyncio
import logging
import unicodedata

from src.domain.repositories.post_repository import PostRepository
from src.domain.services.ai_processor import AIProcessor
from src.infrastructure.delivery.categories import VALID_BRIEFING_CATEGORIES

logger = logging.getLogger(__name__)


_BLOCKLIST_PATTERNS = (
    "월급 쌓아두는 곳",
    "CMA",
    "ISA",
    "IRP",
    "KRX 금현물",
    "금 살 거면",
    "세액공제",
    "비과세 한도",
    "지금 탑승하면",
    "빌라 한 채",
    "딸이 말해줬",
    "대박주",
)

_NON_STARTUP_PATTERNS = (
    "SK하이닉스",
    "SK Hynix",
    "삼성전자",
    "Samsung",
    "NVIDIA",
    "AMD",
    "Intel",
    "TSMC",
    "Micron",
    "Google",
    "Microsoft",
    "Amazon",
    "Meta",
    "Apple",
    "OpenAI",
    "Anthropic",
)

_STARTUP_HINTS = (
    "스타트업",
    "startup",
    "seed",
    "시드",
    "Series",
    "시리즈",
    "벤처",
    "VC",
    "Y Combinator",
    "비상장",
)


def _is_obviously_irrelevant(post) -> bool:
    """Deterministic guard for recurring non-tech investment spam."""
    text = unicodedata.normalize("NFC", f"{post.content_text or ''}\n{post.summary or ''}")
    hits = sum(1 for pattern in _BLOCKLIST_PATTERNS if pattern in text)
    return hits >= 2


def _sanitize_categories(post, categories: list[str]) -> list[str]:
    """Remove category assignments that are structurally inconsistent."""
    text = unicodedata.normalize("NFC", f"{post.content_text or ''}\n{post.summary or ''}")
    cleaned = [
        c for c in (categories or [])
        if c in VALID_BRIEFING_CATEGORIES
    ]
    if "Startup" in cleaned:
        is_known_large_company = any(
            unicodedata.normalize("NFC", pattern) in text
            for pattern in _NON_STARTUP_PATTERNS
        )
        has_startup_hint = any(
            unicodedata.normalize("NFC", pattern) in text
            for pattern in _STARTUP_HINTS
        )
        if is_known_large_company and not has_startup_hint:
            cleaned = [c for c in cleaned if c != "Startup"]
    return cleaned


class ProcessPostsUseCase:
    """미처리 게시물에 대해 AI 처리를 수행하는 유즈케이스."""

    def __init__(
        self,
        post_repo: PostRepository,
        ai_processor: AIProcessor,
        run_lock: asyncio.Lock | None = None,
    ):
        self._post_repo = post_repo
        self._ai = ai_processor
        # 컨테이너가 공유 락을 주입하면 interval 잡·브리핑 전 처리·수동 트리거가
        # 동시에 돌아도 같은 미처리 배치를 2중으로 LLM에 태우지 않는다.
        self._run_lock = run_lock

    async def execute(
        self,
        limit: int = 200,
        chunk_size: int = 40,  # batch_size_filter(40)와 정렬 — 청크가 40+10으로 쪼개져 템플릿을 2번 내는 것 방지
        min_posts_threshold: int = 0,
    ) -> dict[str, int]:
        """미처리 게시물을 청크 단위로 가져와 AI 처리. 처리 통계를 반환."""
        if self._run_lock is not None:
            if self._run_lock.locked():
                logger.info("AI 처리 이미 실행 중 — 이번 트리거는 대기 후 잔여분만 처리")
            async with self._run_lock:
                return await self._execute(limit, chunk_size, min_posts_threshold)
        return await self._execute(limit, chunk_size, min_posts_threshold)

    async def _execute(
        self,
        limit: int,
        chunk_size: int,
        min_posts_threshold: int,
    ) -> dict[str, int]:
        totals = {"total": 0, "relevant": 0, "filtered_out": 0}
        processed_total = 0
        first_fetch = True
        # 분류 실패로 미처리 상태로 남긴 게시물이 같은 실행에서 재fetch돼
        # 필터를 또 타는 것을 방지 (재시도는 다음 실행에서)
        seen_ids: set[str] = set()

        while processed_total < limit:
            remaining = limit - processed_total
            # 첫 fetch는 threshold 검사를 위해 max(chunk_size, threshold)만큼 가져옴
            fetch_size = (
                max(chunk_size, min_posts_threshold) if first_fetch
                else min(chunk_size, remaining)
            )
            chunk = self._post_repo.get_unprocessed(limit=fetch_size)
            chunk = [p for p in chunk if str(p.id) not in seen_ids]

            if not chunk:
                if first_fetch:
                    logger.info("처리할 새 게시물 없음")
                break

            if first_fetch and len(chunk) < min_posts_threshold:
                logger.info(
                    f"처리 건수 부족 ({len(chunk)}건 < {min_posts_threshold}건), 스킵"
                )
                break

            # 첫 fetch에서 chunk_size를 초과해 가져왔어도 처리는 chunk_size씩
            chunk = chunk[: min(chunk_size, remaining)]
            first_fetch = False
            seen_ids.update(str(p.id) for p in chunk)

            chunk_stats = await self._process_chunk(chunk)
            totals["total"] += chunk_stats["total"]
            totals["relevant"] += chunk_stats["relevant"]
            totals["filtered_out"] += chunk_stats["filtered_out"]
            processed_total += len(chunk)

        logger.info(
            f"AI 처리 완료: 전체 {totals['total']}건, "
            f"관련 {totals['relevant']}건, 비관련 {totals['filtered_out']}건"
        )
        return totals

    def _split_previously_rejected(self, posts: list) -> tuple[list, list]:
        """동일 content_hash가 과거에 비관련 판정된 게시물을 분리해 즉시 기각 처리.

        같은 텍스트를 다계정으로 뿌리는 복제 스팸은 LLM 필터의 비결정성
        (같은 글도 배치마다 판정이 갈림)을 물량으로 뚫는다 — 한 번 기각된
        해시는 LLM 없이 결정적으로 차단해 확률 게임 자체를 없앤다.
        """
        hashes = [p.content_hash for p in posts if p.content_hash]
        try:
            rejected = self._post_repo.find_rejected_hashes(hashes) if hashes else set()
        except Exception as e:
            logger.warning(f"기각 이력 조회 실패 — 자동 차단 스킵: {e}")
            return posts, []
        keep, pre = [], []
        for p in posts:
            if p.content_hash and p.content_hash in rejected:
                p.is_relevant = False
                p.summary = "[filtered]"
                pre.append(p)
            else:
                keep.append(p)
        if pre:
            logger.info(f"기각 이력 자동 차단: {len(pre)}건 (동일 해시 비관련 전례)")
        return keep, pre

    async def _process_chunk(self, posts: list) -> dict[str, int]:
        """단일 청크에 대해 필터→검증→분류→업데이트 파이프라인을 실행."""
        logger.info(f"AI 처리 시작: {len(posts)}건")

        # 0. 기각 이력 자동 차단 — 비관련 전례가 있는 동일 해시는 LLM을 태우지 않는다
        posts, pre_rejected = self._split_previously_rejected(posts)
        total_count = len(posts) + len(pre_rejected)

        # 1. 관련성 필터 + 요약 (자동 차단분 제외)
        filter_results = await self._ai.filter_and_summarize(posts) if posts else []

        # LLM이 post_id를 숫자/문자열 어느 쪽으로 반환해도 맞도록 str 키로 통일
        post_map = {str(p.id): p for p in posts}
        relevant_posts = []
        irrelevant_posts = list(pre_rejected)
        processed_ids: set[str] = set()

        for result in filter_results:
            post = post_map.get(str(result.post_id))
            if post is None:
                continue
            # 모델이 같은 post_id를 중복 방출하는 경우(긴 JSON 배열 반복 루프)
            # 첫 판정만 채택 — 중복 집계·판정 뒤집힘·분류 페이로드 중복 방지
            if str(result.post_id) in processed_ids:
                continue
            processed_ids.add(str(result.post_id))
            post.is_relevant = result.is_relevant and not _is_obviously_irrelevant(post)
            # summary IS NULL은 '미처리' 의미(get_unprocessed 기준) — 관련 판정인데
            # 모델이 요약을 비워 보내면 NULL이 남아 매 사이클 재처리 루프를 타므로,
            # 원문 앞부분으로라도 반드시 non-NULL을 기록한다.
            if result.is_relevant:
                post.summary = (
                    result.summary
                    or (post.content_text or "").strip()[:200]
                    or "(요약 없음)"
                )
            else:
                post.summary = result.summary or "[filtered]"
            post.language = result.language
            if result.is_relevant:
                if post.is_relevant:
                    relevant_posts.append(post)
                else:
                    post.summary = "[filtered]"
                    irrelevant_posts.append(post)
            else:
                irrelevant_posts.append(post)

        # AI가 응답에 포함하지 않은 게시물도 비관련으로 처리 (재처리 루프 방지)
        for post in posts:
            if str(post.id) not in processed_ids:
                post.is_relevant = False
                post.summary = "[filtered]"
                irrelevant_posts.append(post)

        # 2. (웹 검색 교차 검증은 여기서 하지 않는다 — 처리량 확보)
        #    검증은 비싸고(웹검색) 발행할 항목에만 필요하므로 브리핑 직전 후보에만 수행한다.
        #    (generate_briefing에서 verify_claims 실행)

        # 3. 관련 게시물만 분류 + 중요도
        retry_next_cycle: list = []
        if relevant_posts:
            cat_results = await self._ai.categorize(relevant_posts)
            cat_map = {str(r.post_id): r for r in cat_results}

            kept_relevant = []
            for post in relevant_posts:
                cr = cat_map.get(str(post.id))
                if cr is None:
                    # 분류 응답 누락(API 실패 배치 포함) — DB 업데이트에서 제외해
                    # summary NULL 상태를 유지하면 다음 사이클에 자동 재시도된다.
                    # (일시 장애 1회로 필터 통과 뉴스가 비관련 강등·삭제되는 것 방지)
                    retry_next_cycle.append(post)
                    continue
                post.category_names = _sanitize_categories(post, cr.categories or [])
                post.importance_score = cr.importance_score
                post.keywords = cr.keywords or []
                if post.category_names:
                    kept_relevant.append(post)
                else:
                    post.is_relevant = False
                    post.summary = "[filtered]"
                    post.importance_score = None
                    post.keywords = []
                    irrelevant_posts.append(post)
            relevant_posts = kept_relevant
            if retry_next_cycle:
                logger.warning(
                    f"분류 누락 {len(retry_next_cycle)}건 — DB 업데이트 제외, 다음 사이클 재시도"
                )

        # 4. 청크 단위로 즉시 DB 업데이트 — 메모리 해제 + 크래시 시 진행분 보존
        all_processed = relevant_posts + irrelevant_posts
        if all_processed:
            self._post_repo.update_many(all_processed)

        return {
            "total": total_count,
            "relevant": len(relevant_posts),
            "filtered_out": len(irrelevant_posts),
        }
