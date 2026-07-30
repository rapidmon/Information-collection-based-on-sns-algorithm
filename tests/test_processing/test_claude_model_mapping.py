"""Claude 모델 티어 매핑 — model_filter == model_process 오매핑 회귀 방지.

과거 실사고(2026-07-30): OpenAI 모델명 문자열 비교로 매핑하다가 설정에서
model_filter == model_process("gpt-5-mini")가 되자 필터/분류 전량이
sonnet으로 올라가 구독 한도를 상위 모델 요율로 소모했다.
티어는 호출부가 명시하고, OpenAI 모델명은 매핑에 쓰지 않는다.
"""

from __future__ import annotations

from src.infrastructure.ai.claude_code_processor import ClaudeCodeProcessor
from src.infrastructure.config.settings import ProcessingConfig


def _processor() -> tuple[ClaudeCodeProcessor, dict]:
    p = ClaudeCodeProcessor(
        # 사고 조건 재현: OpenAI 폴백 모델명이 두 티어에서 동일
        config=ProcessingConfig({"model_filter": "gpt-5-mini", "model_process": "gpt-5-mini"}),
        model_filter="claude-haiku-4-5",
        model_process="claude-sonnet-4-6",
    )
    captured: dict = {}

    def fake_run(args, prompt, label="claude", use_system_prompt=True, model=None):
        captured["args"] = args
        captured["model"] = model
        return "[]"

    p._run_claude = fake_run
    return p, captured


def test_default_tier_maps_to_filter_model_even_when_openai_names_collide():
    p, captured = _processor()
    p._call_api("gpt-5-mini", "prompt", lean=True)
    assert captured["model"] == "claude-haiku-4-5"
    assert "claude-haiku-4-5" in captured["args"]


def test_process_tier_maps_to_process_model():
    p, captured = _processor()
    p._call_api("gpt-5-mini", "prompt", tier="process")
    assert captured["model"] == "claude-sonnet-4-6"
    assert "claude-sonnet-4-6" in captured["args"]


def test_lean_flags_attached_only_when_lean():
    p, captured = _processor()
    p._call_api("gpt-5-mini", "prompt", lean=True)
    assert "--disallowed-tools" in captured["args"]
    p._call_api("gpt-5-mini", "prompt")
    assert "--disallowed-tools" not in captured["args"]
