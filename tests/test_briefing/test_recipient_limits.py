"""수신자별 카테고리 한도(개인화 브리핑) 테스트.

- EmailConfig: 문자열/딕셔너리 혼합 수신자 파싱 + limits_key 그룹핑
- AppConfig: 개인화 한도 → 생성 단계 슈퍼셋 상한(cap_for) 반영
- trim_items_per_category: 발송 시 수신자 뷰 트리밍
- select_topics: 카테고리별 슈퍼셋 상한으로 선별
"""

from __future__ import annotations

from src.domain.entities import BriefingItem
from src.domain.services.ai_processor import MergedTopic
from src.infrastructure.config.settings import AppConfig, BriefingConfig, EmailConfig
from src.infrastructure.delivery.briefing_builder import (
    DefaultBriefingGenerator,
    trim_items_per_category,
)

AUDIENCES = {
    "국내 개발자": [
        "a@b.com",
        {
            "email": "taeyeon.bang@samsung.com",
            "max_per_category": 4,
            "category_limits": {"Coding": 10, "Showcase": 10},
        },
    ],
}


def _item(cat: str, score: float) -> BriefingItem:
    return BriefingItem(headline=f"{cat}-{score}", body="", importance_score=score,
                        category_name=cat)


def test_email_config_parses_mixed_recipients():
    cfg = EmailConfig({"audiences": AUDIENCES})
    rs = cfg.audiences["국내 개발자"]
    assert [r.email for r in rs] == ["a@b.com", "taeyeon.bang@samsung.com"]
    assert rs[0].max_per_category is None and rs[0].category_limits == {}
    assert rs[1].max_per_category == 4
    assert rs[1].category_limits == {"Coding": 10, "Showcase": 10}
    # 한도 조합이 다르면 다른 그룹 키
    assert rs[0].limits_key != rs[1].limits_key


def test_recipient_limits_clamped_to_2_10():
    cfg = EmailConfig({"audiences": {"g": [
        {"email": "x@y.com", "max_per_category": 1,
         "category_limits": {"Coding": 30, "AI": 0}},
    ]}})
    r = cfg.audiences["g"][0]
    assert r.max_per_category == 2                 # 하한 보정
    assert r.category_limits["Coding"] == 10       # 상한 보정
    # 0은 '무제한'으로 오해될 수 있는 값 — 하한(2)으로 보정
    assert r.category_limits["AI"] == 2


def test_app_config_applies_superset_caps():
    app = AppConfig({
        "briefing": {"max_per_category": 6},
        "email": {"audiences": AUDIENCES},
    })
    b = app.briefing
    # 개인화 한도가 있는 카테고리만 슈퍼셋으로 상향, 나머지는 기본값
    assert b.cap_for("Coding") == 10
    assert b.cap_for("Showcase") == 10
    assert b.cap_for("AI") == 6
    # 개인 max_per_category(4)는 기본(6)보다 작으므로 생성 상한을 낮추지 않음
    assert b.max_per_category == 6


def test_trim_items_per_category_respects_limits():
    items = [_item("Coding", 1 - i * 0.01) for i in range(10)]
    items += [_item("AI", 1 - i * 0.01) for i in range(6)]

    # 기본 뷰: 카테고리당 6개
    default_view = trim_items_per_category(items, 6)
    assert sum(1 for i in default_view if i.category_name == "Coding") == 6
    assert sum(1 for i in default_view if i.category_name == "AI") == 6

    # 개인화 뷰: 기본 4개, 코딩만 10개
    custom_view = trim_items_per_category(items, 4, {"Coding": 10, "Showcase": 10})
    assert sum(1 for i in custom_view if i.category_name == "Coding") == 10
    assert sum(1 for i in custom_view if i.category_name == "AI") == 4
    # 카테고리 내 중요도 내림차순 유지
    ai_scores = [i.importance_score for i in custom_view if i.category_name == "AI"]
    assert ai_scores == sorted(ai_scores, reverse=True)


def test_select_topics_uses_superset_cap():
    bcfg = BriefingConfig({"max_per_category": 6})
    ecfg = EmailConfig({"audiences": AUDIENCES})
    bcfg.apply_recipient_caps(ecfg.iter_recipients())
    gen = DefaultBriefingGenerator(bcfg)

    def _topic(cat: str, score: float) -> MergedTopic:
        return MergedTopic(post_ids=[], headline=f"{cat}-{score}", body_bullets=[],
                           primary_category=cat, importance_score=score,
                           sources=[], source_urls=[])

    topics = [_topic("Coding", 1 - i * 0.01) for i in range(12)]
    topics += [_topic("AI", 1 - i * 0.01) for i in range(12)]
    selected = gen.select_topics(topics)
    # 코딩은 슈퍼셋(10), AI는 기본(6)까지 선별 (min_importance=0.8 통과분 기준)
    assert sum(1 for t in selected if t.primary_category == "Coding") == 10
    assert sum(1 for t in selected if t.primary_category == "AI") == 6
