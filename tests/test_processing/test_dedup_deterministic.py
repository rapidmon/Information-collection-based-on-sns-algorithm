"""결정적 dedup(LLM 미사용) — 후보군 병합이 전체 게시물을 빠짐없이 커버하는지."""

from __future__ import annotations

from src.domain.entities import Post
from src.infrastructure.ai.openai_processor import BaseLLMProcessor


class _StubProcessor(BaseLLMProcessor):
    """LLM 클라이언트 없이 결정적 경로만 테스트하기 위한 스텁."""

    def __init__(self):
        pass


def _post(post_id: int, summary: str, importance: float = 0.5) -> Post:
    return Post(
        id=post_id,
        source="twitter",
        external_id=str(post_id),
        url=f"https://example.com/{post_id}",
        author=f"author-{post_id}",
        content_text=summary,
        summary=summary,
        importance_score=importance,
        category_names=["AI"],
    )


async def test_deduplicate_and_merge_is_deterministic_and_covers_all_posts():
    posts = [
        _post(1, "OpenAI, GPT-5 출시 발표 - 벤치마크 전 영역 1위", 0.9),
        _post(2, "OpenAI GPT-5 공개, 코딩 성능 대폭 향상", 0.7),
        _post(3, "Figma 신규 디자인 협업 기능 공개", 0.5),
    ]
    proc = _StubProcessor()

    topics = await proc.deduplicate_and_merge(posts)

    # 같은 사건(1,2)은 병합, 무관(3)은 단독 — LLM 호출 없이
    assert len(topics) == 2
    covered = {str(pid) for t in topics for pid in t.post_ids}
    assert covered == {"1", "2", "3"}

    merged = next(t for t in topics if len(t.post_ids) == 2)
    # 결정적 초안 headline은 그룹 내 최고 중요도 게시물의 요약
    assert merged.headline == posts[0].summary

    # 같은 입력 → 같은 결과 (결정론)
    topics2 = await proc.deduplicate_and_merge(posts)
    assert [sorted(map(str, t.post_ids)) for t in topics] == [
        sorted(map(str, t.post_ids)) for t in topics2
    ]
