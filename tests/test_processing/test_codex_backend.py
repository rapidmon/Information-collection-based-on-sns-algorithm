"""Codex CLI 백엔드 — 커맨드 구성·오류 전파·티어 매핑 회귀 방지.

폴백이 기존 종량 API에서 Codex CLI(ChatGPT 구독)로 바뀌었다(2026-08-20).
장애가 LLMBackendError로 전파돼야 hybrid의 상호 폴백이 실제로 작동한다.
"""

from __future__ import annotations

import subprocess

import pytest

from src.infrastructure.ai.codex_cli_processor import CodexCliProcessor
from src.infrastructure.ai.llm_processor import LLMBackendError
from src.infrastructure.config.settings import ProcessingConfig


def _proc(**kw) -> CodexCliProcessor:
    return CodexCliProcessor(config=ProcessingConfig({}), codex_bin="codex", **kw)


class _Run:
    """subprocess.run 대역 — 호출 인자를 기록하고 -o 파일에 결과를 쓴다."""

    def __init__(self, result="[]", returncode=0, stdout="", raises=None):
        self.result, self.returncode, self.stdout, self.raises = result, returncode, stdout, raises
        self.cmd = None
        self.input = None

    def __call__(self, cmd, **kw):
        self.cmd, self.input = cmd, kw.get("input")
        if self.raises:
            raise self.raises
        out_path = cmd[cmd.index("-o") + 1]
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(self.result)
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, "")


def test_headless_flags_are_set(monkeypatch):
    run = _Run(result='[{"ok":true}]')
    monkeypatch.setattr(subprocess, "run", run)

    assert _proc()._call_api("gpt-5-mini", "프롬프트") == '[{"ok":true}]'

    cmd = run.cmd
    assert "exec" in cmd
    assert "--ephemeral" in cmd, "세션 파일을 남기면 배치마다 디스크가 쌓인다"
    assert "--skip-git-repo-check" in cmd, "중립 tempdir은 git 저장소가 아니다"
    assert cmd[cmd.index("-s") + 1] == "read-only"
    assert cmd[-1] == "-", "프롬프트는 stdin으로 넘긴다"


def test_system_prompt_is_prepended(monkeypatch):
    run = _Run()
    monkeypatch.setattr(subprocess, "run", run)

    _proc()._call_api("m", "사용자프롬프트")

    assert run.input.endswith("사용자프롬프트")
    assert len(run.input) > len("사용자프롬프트"), "SYSTEM_PROMPT가 앞에 붙어야 한다"


@pytest.mark.parametrize(
    "tier,expected",
    [("filter", "F"), ("process", "P"), ("dedup", "P"), ("consolidate", "P")],
)
def test_tier_selects_model(monkeypatch, tier, expected):
    """model 인자가 아니라 호출부의 tier로 모델을 고른다."""
    run = _Run()
    monkeypatch.setattr(subprocess, "run", run)

    _proc(model_filter="F", model_process="P")._call_api("무시되는-openai-모델명", "p", tier=tier)

    assert run.cmd[run.cmd.index("-m") + 1] == expected


@pytest.mark.parametrize(
    "tier,expected",
    [
        ("filter", "gpt-5.6-luna"),
        ("process", "gpt-5.6-sol"),
        ("dedup", "gpt-5.6-sol"),
        ("consolidate", "gpt-5.6-terra"),
    ],
)
def test_configured_tier_models_reach_codex_cli(monkeypatch, tier, expected):
    run = _Run()
    monkeypatch.setattr(subprocess, "run", run)

    _proc(
        model_filter="gpt-5.6-luna",
        model_process="gpt-5.6-sol",
        model_dedup="gpt-5.6-sol",
        model_consolidate="gpt-5.6-terra",
    )._call_api("compat", "p", tier=tier)

    assert run.cmd[run.cmd.index("-m") + 1] == expected


def test_lean_filter_uses_supported_low_effort(monkeypatch):
    run = _Run()
    monkeypatch.setattr(subprocess, "run", run)

    _proc(model_filter="gpt-5.6-luna", effort_filter="low")._call_api(
        "compat", "p", lean=True
    )

    effort = run.cmd[run.cmd.index("-c") + 1]
    assert effort == "model_reasoning_effort=low"


def test_no_model_flag_when_unset(monkeypatch):
    """모델 미지정이면 -m 을 붙이지 않아 codex 기본 모델을 쓴다."""
    run = _Run()
    monkeypatch.setattr(subprocess, "run", run)

    _proc()._call_api("m", "p")

    assert "-m" not in run.cmd


@pytest.mark.parametrize(
    "kwargs",
    [
        {"returncode": 1},
        {"result": "   "},
        {"raises": subprocess.TimeoutExpired("codex", 1)},
        {"raises": OSError("실행 불가")},
    ],
)
def test_failures_raise_backend_error(monkeypatch, kwargs):
    """장애는 LLMBackendError로 전파돼야 hybrid 폴백이 작동한다."""
    monkeypatch.setattr(subprocess, "run", _Run(**kwargs))

    with pytest.raises(LLMBackendError):
        _proc()._call_api("m", "p")


def test_usage_is_logged(monkeypatch, caplog):
    stdout = (
        '{"type":"thread.started"}\n'
        '{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":20,'
        '"cached_input_tokens":80,"reasoning_output_tokens":5}}\n'
    )
    monkeypatch.setattr(subprocess, "run", _Run(stdout=stdout))

    with caplog.at_level("INFO"):
        _proc(model_filter="M")._call_api("m", "p")

    line = next(r.message for r in caplog.records if "[usage]" in r.message)
    assert "in=100" in line and "out=20" in line and "cached=80" in line


def test_cmd_shim_is_wrapped(monkeypatch):
    """npm .cmd 셰임은 cmd.exe /c 로 감싸야 실행된다."""
    run = _Run()
    monkeypatch.setattr(subprocess, "run", run)

    CodexCliProcessor(config=ProcessingConfig({}), codex_bin="C:/npm/codex.cmd")._call_api("m", "p")

    assert run.cmd[1] == "/c" and run.cmd[2].endswith("codex.cmd")


# ─── codex_only 모드 (품질 비교 기간) ───


class _Boom:
    """Claude 대역 — 호출되면 즉시 실패시켜 '안 불렸는지'를 검증한다."""

    def __init__(self):
        self.called = []

    def __getattr__(self, name):
        async def _call(*a, **k):
            self.called.append(name)
            raise AssertionError(f"codex_only 인데 Claude.{name} 가 호출됐다")
        return _call


class _Fallback:
    def __init__(self):
        self._config = ProcessingConfig({"codex_only": True})
        self.called = []

    def __getattr__(self, name):
        async def _call(*a, **k):
            self.called.append(name)
            return f"{name}-ok"
        return _call


@pytest.mark.parametrize(
    "method,args",
    [
        ("filter_and_summarize", ([],)),
        ("categorize", ([],)),
        ("judge_tiers", ([],)),
        ("generate_curation", ([], "독자")),
        ("compose_topics", ([], [])),
        ("find_covered_topics", ([], [])),
    ],
)
async def test_codex_only_never_touches_claude(method, args):
    """Claude 고정 경로(작문·큐레이션·티어)까지 막혀야 비교가 오염되지 않는다."""
    from src.infrastructure.ai.hybrid_processor import HybridAIProcessor

    fallback, claude = _Fallback(), _Boom()
    h = HybridAIProcessor(fallback, claude)

    result = await getattr(h, method)(*args)

    assert claude.called == [], "codex_only 모드에서 Claude가 불렸다"
    assert result == f"{method}-ok"


async def test_hybrid_uses_claude_when_not_codex_only():
    """플래그를 끄면 원래대로 Claude 고정 경로가 살아난다(되돌릴 수 있는지 확인)."""
    from src.infrastructure.ai.hybrid_processor import HybridAIProcessor

    class _Claude:
        def __init__(self): self.called = []
        def __getattr__(self, name):
            async def _call(*a, **k):
                self.called.append(name)
                return f"claude-{name}"
            return _call

    fallback = _Fallback()
    fallback._config = ProcessingConfig({"codex_only": False, "routine_backend": "claude"})
    claude = _Claude()

    assert await HybridAIProcessor(fallback, claude).generate_curation([], "독자") == "claude-generate_curation"
    assert claude.called == ["generate_curation"]
