"""기각 이력 자동 차단 테스트 — 동일 content_hash 비관련 전례 시 LLM 미경유 즉시 기각."""

from __future__ import annotations

from src.application.use_cases.process_posts import ProcessPostsUseCase
from src.domain.entities import Post
from src.domain.services.ai_processor import CategoryResult, FilterResult


def _post(pid: int, text: str, chash: str | None) -> Post:
    return Post(
        source="threads", external_id=f"th_{pid}", url="", author=f"a{pid}",
        content_text=text, id=pid, content_hash=chash,
    )


class FakeRepo:
    def __init__(self, rejected_hashes: set[str]):
        self.rejected = rejected_hashes
        self.updated: list[Post] = []

    def find_rejected_hashes(self, hashes: list[str]) -> set[str]:
        return {h for h in hashes if h in self.rejected}

    def update_many(self, posts: list[Post]) -> int:
        self.updated.extend(posts)
        return len(posts)


class FakeAI:
    def __init__(self):
        self.filtered: list[list[Post]] = []

    async def filter_and_summarize(self, posts: list[Post]):
        self.filtered.append(list(posts))
        return [
            FilterResult(post_id=p.id, is_relevant=True, summary="요약", language="ko")
            for p in posts
        ]

    async def categorize(self, posts: list[Post]):
        return [
            CategoryResult(post_id=p.id, categories=["AI"], importance_score=0.8, keywords=[])
            for p in posts
        ]


async def test_previously_rejected_hash_skips_llm():
    repo = FakeRepo(rejected_hashes={"scamhash"})
    ai = FakeAI()
    uc = ProcessPostsUseCase(post_repo=repo, ai_processor=ai)

    scam = _post(1, "SK하이닉스가 드디어 나스닥에...", "scamhash")
    legit = _post(2, "TSMC 2분기 실적 발표", "legithash")
    stats = await uc._process_chunk([scam, legit])

    # 스캠 복제글은 LLM에 아예 전달되지 않는다
    assert [p.id for p in ai.filtered[0]] == [2]
    # 즉시 비관련 처리 + DB 반영
    assert scam.is_relevant is False
    assert scam.summary == "[filtered]"
    assert any(p.id == 1 for p in repo.updated)
    # 통계는 차단분 포함
    assert stats == {"total": 2, "relevant": 1, "filtered_out": 1}


async def test_hash_lookup_failure_falls_back_to_llm():
    class BrokenRepo(FakeRepo):
        def find_rejected_hashes(self, hashes):
            raise RuntimeError("db down")

    repo = BrokenRepo(set())
    ai = FakeAI()
    uc = ProcessPostsUseCase(post_repo=repo, ai_processor=ai)

    posts = [_post(1, "글", "h1")]
    await uc._process_chunk(posts)

    # 이력 조회가 죽어도 파이프라인은 기존대로 LLM 필터를 탄다
    assert [p.id for p in ai.filtered[0]] == [1]


async def test_no_hash_posts_pass_through():
    repo = FakeRepo(rejected_hashes={"scamhash"})
    ai = FakeAI()
    uc = ProcessPostsUseCase(post_repo=repo, ai_processor=ai)

    posts = [_post(1, "해시 없는 글", None)]
    await uc._process_chunk(posts)
    assert [p.id for p in ai.filtered[0]] == [1]
