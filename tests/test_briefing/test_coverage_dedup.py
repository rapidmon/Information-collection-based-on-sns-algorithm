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


async def test_judge_only_top_candidates_per_category():
    """카테고리별 상위 15개만 판정 대상 — 로컬 인덱스는 전역으로 정확히 복원."""
    repo = FakeBriefingRepo(["과거 사건"])
    ai = FakeAI(dup_indexes=[0])  # 판정 대상(로컬) 0번
    uc = _uc(repo, ai)

    # 점수 오름차순 16개 → 최저점(전역 0)이 판정 대상에서 빠져
    # 판정 대상의 로컬 0번은 전역 1번("A 1")이 된다
    topics = [
        _topic(f"A {i}", "Semiconductor", round(0.5 + i * 0.01, 2)) for i in range(16)
    ]
    topics.append(_topic("B 사건", "AI", 0.9))  # 전역 16

    kept = await uc._drop_already_covered(topics)

    judged = ai.calls[0][0]
    assert len(judged) == 16  # Semiconductor 상위 15 + AI 1 — 최저점 "A 0"은 판정 제외
    assert all(t.headline != "A 0" for t in judged)
    # 로컬 0 → 전역 1 매핑: "A 1"이 제거되고, 판정 제외분("A 0")은 유지
    kept_headlines = [t.headline for t in kept]
    assert "A 1" not in kept_headlines
    assert "A 0" in kept_headlines
    assert "B 사건" in kept_headlines


class FakeGuardAI(FakeAI):
    """최종 가드 테스트용 — consolidate_topics까지 구현한 페이크."""

    def __init__(self, dup_indexes, merged=None, fail_merge=False):
        super().__init__(dup_indexes)
        self.merged = merged
        self.fail_merge = fail_merge
        self.consolidate_calls = 0

    async def consolidate_topics(self, topics):
        self.consolidate_calls += 1
        if self.fail_merge:
            raise RuntimeError("merge down")
        return self.merged if self.merged is not None else list(topics)


async def test_final_guard_merges_cross_category_duplicates():
    """카테고리가 갈려 중복 선정된 같은 사건은 최종 가드에서 병합된다."""
    hf1 = _topic("OpenAI, HF 침해 인정", "BigTech", 1.0)
    hf2 = _topic("OpenAI, HF 공격 출처 시인", "Policy", 1.0)
    gem = _topic("Gemini 3.6 Flash 출시", "AI", 1.0)
    merged_hf = _topic("OpenAI, HF 침해 공식 인정", "BigTech", 1.0)

    ai = FakeGuardAI(dup_indexes=[], merged=[merged_hf, gem])
    uc = _uc(FakeBriefingRepo(["과거 사건"]), ai)

    result = await uc._final_dedup_guard([hf1, hf2, gem])
    assert ai.consolidate_calls == 1
    assert [t.headline for t in result] == [merged_hf.headline, gem.headline]


async def test_final_guard_drops_covered_after_merge():
    """병합 후에도 최근 브리핑 기다룬 사건은 최종 가드에서 한 번 더 걸러진다."""
    old = _topic("Anthropic 합의 승인", "Policy", 1.0)
    new = _topic("신규 사건", "AI", 1.0)
    ai = FakeGuardAI(dup_indexes=[0])  # 병합 결과의 0번(old)이 기브리핑 사건
    uc = _uc(FakeBriefingRepo(["[Policy] Anthropic 합의 승인"]), ai)

    result = await uc._final_dedup_guard([old, new])
    assert [t.headline for t in result] == ["신규 사건"]


async def test_final_guard_merge_failure_still_rechecks_coverage():
    """병합 실패는 스킵하고 기브리핑 재확인은 계속 수행한다."""
    t1 = _topic("사건 A", "AI", 1.0)
    t2 = _topic("사건 B", "AI", 0.9)
    ai = FakeGuardAI(dup_indexes=[], fail_merge=True)
    uc = _uc(FakeBriefingRepo(["과거 사건"]), ai)

    result = await uc._final_dedup_guard([t1, t2])
    assert ai.consolidate_calls == 1
    assert result == [t1, t2]
    assert len(ai.calls) == 1  # find_covered_topics는 정상 호출됨


async def test_final_guard_single_item_skips_merge():
    """확정분이 1개면 병합 호출 없이 기브리핑 재확인만 한다."""
    ai = FakeGuardAI(dup_indexes=[])
    uc = _uc(FakeBriefingRepo(["과거 사건"]), ai)

    result = await uc._final_dedup_guard([_topic("사건 A")])
    assert ai.consolidate_calls == 0
    assert len(result) == 1
