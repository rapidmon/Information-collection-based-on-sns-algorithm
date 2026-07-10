"""독자 피드백(과대/과소) → 중요도 채점 few-shot 보정 테스트."""

from __future__ import annotations

from src.infrastructure.ai.prompts import CATEGORIZE, build_feedback_calibration


def test_empty_examples_produce_no_block():
    assert build_feedback_calibration([]) == ""
    # 적절(appropriate)만 있으면 보정할 게 없다
    assert build_feedback_calibration([{"headline": "A", "label": "appropriate"}]) == ""


def test_block_groups_over_and_under():
    examples = [
        {"headline": "예고성 발표 뉴스", "category": "AI", "label": "over"},
        {"headline": "보안 취약점 공개", "category": "Coding", "label": "under"},
    ]
    block = build_feedback_calibration(examples)
    assert "점수를 낮춰라" in block
    assert "[AI] 예고성 발표 뉴스" in block
    assert "점수를 높여라" in block
    assert "[Coding] 보안 취약점 공개" in block


def test_long_headline_truncated():
    examples = [{"headline": "가" * 200, "category": "AI", "label": "over"}]
    block = build_feedback_calibration(examples)
    assert "가" * 80 in block
    assert "가" * 81 not in block


def test_categorize_prompt_formats_with_and_without_block():
    """CATEGORIZE 템플릿이 feedback_block 유무 모두에서 온전히 포맷된다."""
    with_block = CATEGORIZE.format(
        posts_json="[]",
        feedback_block=build_feedback_calibration(
            [{"headline": "예고성 발표", "category": "AI", "label": "over"}]
        ),
    )
    assert "독자 피드백 보정" in with_block
    assert "## 키워드 추출 규칙" in with_block

    without_block = CATEGORIZE.format(posts_json="[]", feedback_block="")
    assert "독자 피드백 보정" not in without_block
    assert "## 키워드 추출 규칙" in without_block
