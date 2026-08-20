"""claude 바이너리 탐색 — npm 스텁 회귀 방지.

2026-08-20 실사고: latest(2.1.237)에 win32-x64 네이티브 패키지가 배포되지 않아
npm이 bin/claude.exe 자리에 안내문을 echo 하는 500바이트 셸 스크립트를 남겼다.
탐색기가 이걸 정상 실행 파일로 골라 모든 Claude 호출이 WinError 216으로 죽었고,
파일이 존재하고 이름도 맞아서 원인 파악이 오래 걸렸다.
"""

from __future__ import annotations

import src.infrastructure.ai.claude_code_processor as mod
from src.infrastructure.ai.claude_code_processor import _is_real_exe, _resolve_claude_bin

STUB = b'echo "Error: claude native binary not installed." >&2\n'
REAL = b"MZ\x90\x00" + b"\x00" * 128


def test_detects_stub_vs_real(tmp_path):
    stub = tmp_path / "stub.exe"
    stub.write_bytes(STUB)
    real = tmp_path / "real.exe"
    real.write_bytes(REAL)

    assert _is_real_exe(str(stub)) is False
    assert _is_real_exe(str(real)) is True
    assert _is_real_exe(str(tmp_path / "없는파일.exe")) is False


def _fake_env(monkeypatch, hits, which=None):
    monkeypatch.setattr(mod.os, "environ", {"APPDATA": "X"})
    monkeypatch.setattr(mod.glob, "glob", lambda *a, **kw: hits)
    monkeypatch.setattr(mod.shutil, "which", lambda name: which)


def test_stub_is_skipped_and_falls_back(tmp_path, monkeypatch):
    stub = tmp_path / "claude.exe"
    stub.write_bytes(STUB)
    _fake_env(monkeypatch, [str(stub)], which="C:/npm/claude.cmd")

    assert _resolve_claude_bin() == "C:/npm/claude.cmd", "스텁을 고르면 모든 호출이 죽는다"


def test_real_exe_is_preferred(tmp_path, monkeypatch):
    real = tmp_path / "claude.exe"
    real.write_bytes(REAL)
    _fake_env(monkeypatch, [str(real)], which="C:/npm/claude.cmd")

    assert _resolve_claude_bin() == str(real)


def test_picks_real_among_candidates(tmp_path, monkeypatch):
    stub = tmp_path / "a" / "claude.exe"
    stub.parent.mkdir()
    stub.write_bytes(STUB)
    real = tmp_path / "b" / "claude.exe"
    real.parent.mkdir()
    real.write_bytes(REAL)
    _fake_env(monkeypatch, [str(stub), str(real)])

    assert _resolve_claude_bin() == str(real)


def test_no_candidate_falls_back_to_name(monkeypatch):
    _fake_env(monkeypatch, [], which=None)

    assert _resolve_claude_bin() == "claude"
