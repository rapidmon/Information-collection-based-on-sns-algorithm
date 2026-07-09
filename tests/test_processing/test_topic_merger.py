"""TopicMerger 한/영 별칭 통일 + 병합 후보군 탐지 테스트."""

from __future__ import annotations

from src.domain.services.ai_processor import MergedTopic
from src.infrastructure.ai.topic_merger import TopicMerger


def _topic(headline: str) -> MergedTopic:
    return MergedTopic(
        post_ids=[1],
        headline=headline,
        body_bullets=[],
        primary_category="AI",
        importance_score=0.5,
        sources=["twitter"],
    )


def test_korean_alias_normalized_to_english_token():
    tokens = TopicMerger.extract_key_tokens("엔비디아, 블랙웰 양산 확대 발표")
    assert "NVIDIA" in tokens
    assert "Blackwell" in tokens
    assert "엔비디아" not in tokens


def test_korean_and_english_headlines_become_merge_candidates():
    merger = TopicMerger()
    topics = [
        _topic("엔비디아 블랙웰 출하량 2배 확대"),
        _topic("NVIDIA, Blackwell 생산 2배 확대"),
        _topic("Figma 신규 AI 디자인 기능 공개"),
    ]
    groups = merger.find_merge_candidates(topics)
    assert [0, 1] in groups
    assert all(2 not in g for g in groups)


def test_guard_words_do_not_create_fake_company_tokens():
    # "애플리케이션"이 "Apple리케이션"으로 쪼개지면 가짜 Apple 매칭이 생긴다
    tokens = TopicMerger.extract_key_tokens("차세대 웹 애플리케이션 성능 최적화 기법")
    assert "Apple" not in tokens
    # "메타버스" 기사가 Meta(기업) 토큰을 만들면 안 된다
    tokens = TopicMerger.extract_key_tokens("메타버스 플랫폼 이용자 급감")
    assert "Meta" not in tokens


def test_mixed_script_alias_openai():
    tokens = TopicMerger.extract_key_tokens("오픈AI가 신형 모델 공개")
    assert "OpenAI" in tokens
