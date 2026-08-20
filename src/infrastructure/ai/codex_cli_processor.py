"""Codex CLI(`codex exec`)를 LLM 백엔드로 쓰는 AI 프로세서.

OpenAI Chat Completions API(종량 과금)를 대체한다. Codex CLI는 ChatGPT 구독으로
인증되므로 토큰당 실지출이 0이고, API 키도 필요 없다.

OpenAIProcessor를 상속하는 이유는 **웹검증(verify_claims) 재사용** 때문이다 —
그 구현은 DuckDuckGo 검색 + `_call_api`만 쓰고 OpenAI 클라이언트를 건드리지 않아
백엔드를 바꿔도 그대로 동작한다. `__init__`에서 super()를 호출하지 않아
OpenAI 클라이언트 생성(=API 키 요구)을 피한다.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile

from src.infrastructure.ai.openai_processor import LLMBackendError, OpenAIProcessor
from src.infrastructure.ai.prompts import SYSTEM_PROMPT
from src.infrastructure.config.settings import ProcessingConfig

logger = logging.getLogger(__name__)


def _resolve_codex_bin() -> str:
    """실행 가능한 codex 경로. npm 셰임(.cmd)은 _build_command에서 cmd.exe로 감싼다."""
    return shutil.which("codex") or shutil.which("codex.cmd") or "codex"


class CodexCliProcessor(OpenAIProcessor):
    """`codex exec` 헤드리스 실행 기반 프로세서 (ChatGPT 구독, API 키 불필요)."""

    def __init__(
        self,
        config: ProcessingConfig,
        codex_bin: str | None = None,
        model_filter: str = "",
        model_process: str = "",
        model_dedup: str = "",
        model_consolidate: str = "",
        effort_filter: str = "low",
        effort_process: str = "medium",
        effort_dedup: str = "",
        effort_consolidate: str = "",
        timeout: int = 600,
        work_dir: str | None = None,
    ):
        # NOTE: super().__init__()를 호출하지 않는다 (OpenAI 클라이언트 생성을 피함).
        self._config = config
        self._codex_bin = codex_bin or _resolve_codex_bin()
        self._codex_model_filter = model_filter
        self._codex_model_process = model_process
        self._codex_model_dedup = model_dedup or model_process
        self._codex_model_consolidate = model_consolidate or model_process
        self._effort_filter = effort_filter
        self._effort_process = effort_process
        self._effort_dedup = effort_dedup or effort_process
        self._effort_consolidate = effort_consolidate or effort_process
        self._timeout = timeout
        # 프로젝트 컨텍스트(AGENTS.md 등) 로딩을 피하기 위한 중립 작업 디렉터리
        self._work_dir = work_dir or tempfile.gettempdir()
        logger.info(
            f"CodexCliProcessor 초기화: bin={self._codex_bin}, "
            f"filter={model_filter or '(codex 기본)'}, "
            f"process={model_process or '(codex 기본)'}, "
            f"dedup={self._codex_model_dedup or '(codex 기본)'}, "
            f"consolidate={self._codex_model_consolidate or '(codex 기본)'}, "
            f"effort={effort_filter}/{effort_process}/"
            f"{self._effort_dedup}/{self._effort_consolidate}"
        )

    def _curation_model(self) -> str:
        # 이 백엔드는 model 인자를 쓰지 않는다(티어로 고른다) — 형식만 맞춘다.
        return self._config.model_filter

    def _build_command(self, args: list[str]) -> list[str]:
        """codex 실행 커맨드. .cmd/.bat 셰임은 cmd.exe /c로 감싼다."""
        if self._codex_bin.lower().endswith((".cmd", ".bat")):
            comspec = os.environ.get("COMSPEC", "cmd.exe")
            return [comspec, "/c", self._codex_bin, *args]
        return [self._codex_bin, *args]

    @staticmethod
    def _log_usage(stdout: str, model: str) -> None:
        """turn.completed 이벤트의 토큰 사용량을 [usage] 형식으로 남긴다.

        scripts/usage_report.py 가 Claude/OpenAI 경로와 합산할 수 있게 포맷을 맞춘다.
        """
        for line in (stdout or "").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") != "turn.completed":
                continue
            u = event.get("usage") or {}
            logger.info(
                f"[usage] model={model} in={u.get('input_tokens', 0)} "
                f"out={u.get('output_tokens', 0)} "
                f"(cached={u.get('cached_input_tokens', 0)} "
                f"reasoning={u.get('reasoning_output_tokens', 0)})"
            )
            return

    def _call_api(
        self,
        model: str,
        prompt: str,
        max_tokens: int = 4096,
        *,
        lean: bool = False,
        tier: str = "filter",
    ) -> str:
        """`codex exec`를 헤드리스로 실행하고 최종 메시지를 반환한다.

        model 인자는 무시한다 — OpenAI 모델명이라 codex 모델과 대응되지 않는다.
        호출부가 넘긴 tier로 고른다(ClaudeCodeProcessor와 같은 이유: 설정에서
        model_filter == model_process 가 되면 모델명으로는 호출 의도를 복원할 수 없다).
        max_tokens도 CLI에 대응 옵션이 없어 사용하지 않는다.

        최종 메시지는 stdout 파싱 대신 `-o` 파일로 받는다 — stdout에는 진행 이벤트가
        섞여 나오므로 파일 쪽이 훨씬 견고하다.
        """
        codex_model = {
            "process": self._codex_model_process,
            "dedup": self._codex_model_dedup,
            "consolidate": self._codex_model_consolidate,
        }.get(tier, self._codex_model_filter)
        # lean=True 는 "규칙이 프롬프트에 명시된 기계적 배치"라는 신호 — 추론을 끈다.
        # ⚠️ 티어로 판단하면 안 된다: judge_tiers 는 filter 티어지만 품질 민감이라
        # 추론이 필요하다. 호출부가 lean 으로 직접 말한다(Claude 경로와 같은 규칙).
        effort = "none" if lean else {
            "process": self._effort_process,
            "dedup": self._effort_dedup,
            "consolidate": self._effort_consolidate,
        }.get(tier, self._effort_filter)
        # SYSTEM_PROMPT(역할·JSON 강제)는 시스템 주입 경로가 없으므로 프롬프트 최상단에 둔다.
        full_prompt = f"{SYSTEM_PROMPT}\n\n{prompt}"

        fd, out_path = tempfile.mkstemp(prefix="codex_out_", suffix=".txt")
        os.close(fd)
        args = [
            "exec",
            "--ephemeral",            # 세션 파일을 남기지 않는다(배치라 이력 불필요)
            "--skip-git-repo-check",  # 중립 tempdir에서 돌므로 git 저장소가 아니다
            "-s", "read-only",        # 배치는 파일을 쓸 일이 없다
            "-C", self._work_dir,
            "--json",                 # stdout=이벤트 JSONL (토큰 사용량 집계용)
            "-c", f"model_reasoning_effort={effort}",
            "-o", out_path,           # 최종 메시지만 파일로
            "-",                      # 프롬프트는 stdin
        ]
        if codex_model:
            args[1:1] = ["-m", codex_model]

        try:
            proc = subprocess.run(
                self._build_command(args),
                input=full_prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                cwd=self._work_dir,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise LLMBackendError(f"codex exec 타임아웃({self._timeout}s)") from e
        except OSError as e:  # 미설치·실행 불가
            raise LLMBackendError(f"codex exec 실행 실패: {e}") from e
        finally:
            result = ""
            try:
                with open(out_path, encoding="utf-8") as f:
                    result = f.read()
            except OSError:
                pass
            try:
                os.unlink(out_path)
            except OSError:
                pass

        if proc.returncode != 0:
            raise LLMBackendError(
                f"codex exec 종료코드 {proc.returncode}: {(proc.stderr or '')[:300]}"
            )

        self._log_usage(proc.stdout, f"{codex_model or 'codex-기본'}/{effort}")

        if not result.strip():
            raise LLMBackendError("codex exec 빈 응답")
        return result

    async def run_freeform(
        self, prompt: str, websearch: bool = True, max_turns: int = 8
    ) -> str:
        """임의 프롬프트를 자유 텍스트로 실행 (슬랙 투표 1위 카드뉴스 등).

        파이프라인 밖 용도라 SYSTEM_PROMPT(JSON 강제)를 붙이지 않는다.
        websearch 인자는 시그니처 호환용 — codex exec 는 read-only 샌드박스에서
        도구 없이 돌리므로 웹 검색을 쓰지 않는다.
        """
        import asyncio

        return await asyncio.to_thread(self._run_freeform_sync, prompt)

    def _run_freeform_sync(self, prompt: str) -> str:
        fd, out_path = tempfile.mkstemp(prefix="codex_ff_", suffix=".txt")
        os.close(fd)
        args = ["exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only",
                "-C", self._work_dir, "--json",
                "-c", f"model_reasoning_effort={self._effort_process}",
                "-o", out_path, "-"]
        if self._codex_model_process:
            args[1:1] = ["-m", self._codex_model_process]
        try:
            proc = subprocess.run(
                self._build_command(args), input=prompt, capture_output=True,
                text=True, encoding="utf-8", cwd=self._work_dir, timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise LLMBackendError(f"codex freeform 타임아웃({self._timeout}s)") from e
        except OSError as e:
            raise LLMBackendError(f"codex freeform 실행 실패: {e}") from e
        finally:
            result = ""
            try:
                with open(out_path, encoding="utf-8") as f:
                    result = f.read()
            except OSError:
                pass
            try:
                os.unlink(out_path)
            except OSError:
                pass

        if proc.returncode != 0:
            raise LLMBackendError(
                f"codex freeform 종료코드 {proc.returncode}: {(proc.stderr or '')[:300]}"
            )
        self._log_usage(proc.stdout, f"{self._codex_model_process or 'codex-기본'}/freeform")
        if not result.strip():
            raise LLMBackendError("codex freeform 빈 응답")
        return result
