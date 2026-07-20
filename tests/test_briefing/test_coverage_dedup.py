"""기브리핑 사건 dedup 테스트 — 최근 브리핑에서 다룬 사건의 재발행 차단."""

from __future__ import annotations

from types import SimpleNamespace

from src.application.use_cases.generate_briefing import GenerateBriefingUseCase
from src.domain.services.ai_processor import MergedTopic


def _topic(headline: str, category: str = "Semiconductor", score: float = 0.9) -> MergedTopic:
    return MergedTopic(
        post_ids=[1], headline=headline, body_bullets=["요약"],
        primary_category=category, importance_score=score, sources=["threads"],
    )


def _uc(briefing_repo, ai) -> GenerateBriefingUseCase:
    return GenerateBriefingUseCase(
        post_repo=None, briefing_repo=briefing_repo, ai_processor=ai,
        briefing_generator=None, scoring_config=None,
    )


class FakeBriefingRepo:
    def __init__(self, items: list[str]):
        self._items = items

    async def get_all(self, limit: int = 30, offset: int = 0):
        return [SimpleNamespace(items=[
            SimpleNamespace(headline=h, category_name="Semiconductor")
            for h in self._items
        ])]


class FakeAI:
    def __init__(self, dup_indexes: list[int]):
        self.dup = dup_indexes
        self.calls: list[tuple] = []

    async def find_covered_topics(self, topics, recent_items):
        self.calls.append((list(topics), list(recent_items)))
        return self.dup


async def test_covered_topics_dropped():
    repo = FakeBriefingRepo(["SK하이닉스, 미국 Nasdaq 상장"])
    ai = FakeAI(dup_indexes=[0])
    uc = _uc(repo, ai)

    topics = [_topic("SK하이닉스, 나스닥 상장 완료"), _topic("TSMC 2분기 실적")]
    kept = await uc._drop_already_covered(topics)

    assert [t.headline for t in kept] == ["TSMC 2분기 실적"]
    # 최근 브리핑 헤드라인이 카테고리와 함께 전달됐는지
    assert ai.calls[0][1] == ["[Semiconductor] SK하이닉스, 미국 Nasdaq 상장"]


async def test_no_recent_items_skips_llm():
    repo = FakeBriefingRepo([])
    ai = FakeAI(dup_indexes=[0])
    uc = _uc(repo, ai)

    topics = [_topic("아무 사건")]
    kept = await uc._drop_already_covered(topics)
    assert kept == topics
    assert ai.calls == []  # 비교 대상이 없으면 LLM 호출 없음


async def test_repo_failure_keeps_all_topics():
    class BrokenRepo:
        async def get_all(self, limit=30, offset=0):
            raise RuntimeError("firestore down")

    uc = _uc(BrokenRepo(), FakeAI([0]))
    topics = [_topic("사건 A")]
    assert await uc._drop_already_covered(topics) == topics


async def test_ai_failure_keeps_all_topics():
    class BrokenAI:
        async def find_covered_topics(self, topics, recent_items):
            raise RuntimeError("llm down")

    uc = _uc(FakeBriefingRepo(["과거 사건"]), BrokenAI())
    topics = [_topic("사건 A")]
    assert await uc._drop_already_covered(topics) == topics


async def test_renormalize_only_affected_category():
    """카테고리 1위가 dedup으로 제거되면 생존분 기준으로 재정규화 — 다른 카테고리는 불변."""
    repo = FakeBriefingRepo(["SK하이닉스, 미국 Nasdaq 상장"])
    ai = FakeAI(dup_indexes=[0])  # 반도체 1위(0.95)가 기브리핑 사건
    uc = _uc(repo, ai)

    topics = [
        _topic("SK하이닉스 상장 재보도", "Semiconductor", 0.95),
        _topic("삼성 파운드리 신규 수주", "Semiconductor", 0.76),
        _topic("HBM 가격 동향", "Semiconductor", 0.57),
        _topic("GPT 신모델", "AI", 0.6),
    ]
    kept = await uc._drop_already_covered(topics)

    scores = {t.headline: t.importance_score for t in kept}
    # 반도체: 생존 1위(0.76)가 새 기준 1.0, 나머지는 비례 (0.57/0.76=0.75)
    assert scores["삼성 파운드리 신규 수주"] == 1.0
    assert scores["HBM 가격 동향"] == round(0.57 / 0.76, 4)
    # 제거가 없던 AI 카테고리는 그대로
    assert scores["GPT 신모델"] == 0.6


async def test_no_removal_no_renormalize():
    repo = FakeBriefingRepo(["과거 사건"])
    ai = FakeAI(dup_indexes=[])
    uc = _uc(repo, ai)

    topics = [_topic("사건 A", "Semiconductor", 0.7)]
    kept = await uc._drop_already_covered(topics)
    assert kept[0].importance_score == 0.7  # 제거 없음 → 점수 불변
