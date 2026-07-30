"""Claude Code CLI 기반 AI 프로세서 (API 키 없이 구독 인증 사용).

OpenAIProcessor의 프롬프트·JSON 파싱·토픽 병합·웹 검색 로직을 그대로 상속하고,
실제 LLM 호출(_call_api)만 OpenAI API → `claude -p` 헤드리스 호출로 교체한다.

동작 검증으로 확정한 호출 레시피(깨지기 쉬운 지점 회피):
- `claude -p --output-format json --model <claude-model> --max-turns 1`
- 역할/규칙/데이터/출력형식을 전부 stdin 프롬프트에 넣는다.
  (--append-system-prompt로 역할을 주입하면 에이전트가 이를 prompt injection으로
   오탐해 거부하는 경우가 있어 사용하지 않는다.)
- 중립 작업 디렉터리(tempdir)에서 실행한다.
  (프로젝트 디렉터리에서 실행하면 CLAUDE.md·레포 컨텍스트·스킬이 로딩되어
   모델이 "코딩 조수"처럼 굴며 JSON 대신 대화형 응답을 내놓는다.)
- 인증은 사용자의 Claude 구독 로그인(`~/.claude` 저장 자격증명)을 그대로 사용.
  별도 머신/서비스 컨텍스트라면 CLAUDE_CODE_OAUTH_TOKEN 환경변수로 보강 가능.
"""

from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import shutil
import subprocess
import tempfile

from src.domain.services.ai_processor import VerificationResult
from src.infrastructure.ai.openai_processor import (
    BaseLLMProcessor,
    LLMBackendError,
    _parse_json_response,
    _posts_to_json_lite,
)
from src.infrastructure.ai.prompts import EXTRACT_CLAIMS, SYSTEM_PROMPT, VERIFY_WITH_SEARCH
from src.infrastructure.config.settings import ProcessingConfig

logger = logging.getLogger(__name__)


def _resolve_claude_bin() -> str:
    """실행 가능한 claude 바이너리 경로를 찾는다.

    Windows의 npm 셰임(claude.cmd)은 subprocess에서 직접 실행 시 내부 따옴표가
    깨지므로, 가능하면 실제 claude.exe를 우선 사용한다. 못 찾으면 PATH의 claude
    (Linux/mac 바이너리) 또는 .cmd 셰임으로 폴백한다(.cmd는 _build_command에서
    cmd.exe /c로 감싼다).
    """
    for env_var in ("APPDATA", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(env_var)
        if not base:
            continue
        pattern = os.path.join(
            base, "npm", "node_modules", "@anthropic-ai", "claude-code", "**", "claude.exe"
        )
        hits = glob.glob(pattern, recursive=True)
        if hits:
            return hits[0]
    return shutil.which("claude") or shutil.which("claude.cmd") or "claude"


# 규칙 기반 배치 호출(필터·분류)용 감량 플래그.
#
# 실측 (40건 배치, claude-haiku-4-5, 2026-07-30):
#   추론 켬 → 출력 6,995 tok / 호출당 총 등가 64,254
#   추론 끔 → 출력 2,236 tok / 호출당 총 등가 35,270   (출력 −68%, 총 −45%)
#   판정 결과(40/40건, 관련 8건=20%)와 요약 길이(89자→88자)는 동일했다.
# 규칙이 프롬프트에 명시된 분류 작업이라 추론이 기여하는 바가 없다.
# OpenAI 경로가 이미 reasoning_effort="low"를 쓰는 것과 같은 취지 —
# 이 설정을 빼고 Claude로 옮기면 추론 비용이 조용히 되살아난다.
#
# 도구·번들 스킬은 이 작업에서 쓰이지 않아 시스템 프롬프트에서 뺀다.
# 대부분 캐시되는 자리(호출당 22,961 중 거의 전부가 cache_read)라 절감폭은
# 작지만 무손실이다.  ⚠️ WebSearch가 필요한 _call_api_with_search 에는 적용하지 않는다.
_LEAN_TOOLS = [
    "Bash", "Read", "Write", "Edit", "Glob", "Grep",
    "WebSearch", "WebFetch", "Task", "TodoWrite", "NotebookEdit",
]
_LEAN_SETTINGS = json.dumps(
    {"disableBundledSkills": True, "alwaysThinkingEnabled": False},
    separators=(",", ":"),
)


class ClaudeCodeProcessor(BaseLLMProcessor):
    """Claude Code CLI(`claude -p`)를 LLM 백엔드로 쓰는 AI 프로세서.

    BaseLLMProcessor(공용 파이프라인)를 상속해 필터/분류/티어/병합/큐레이션을
    재사용하고, LLM 호출부(_call_api)와 웹검증(verify_claims)만 Claude용으로 구현한다.
    OpenAI 클라이언트/키가 필요 없다.
    """

    def __init__(
        self,
        config: ProcessingConfig,
        claude_bin: str | None = None,
        model_filter: str = "claude-haiku-4-5",
        model_process: str = "claude-sonnet-4-6",
        timeout: int = 300,
        oauth_token: str | None = None,
        work_dir: str | None = None,
    ):
        # NOTE: super().__init__()를 호출하지 않는다 (OpenAI 클라이언트 생성을 피함).
        self._config = config
        self._claude_bin = claude_bin or _resolve_claude_bin()
        self._claude_model_filter = model_filter
        self._claude_model_process = model_process
        self._timeout = timeout
        # 프로젝트 컨텍스트 로딩을 피하기 위한 중립 작업 디렉터리
        self._work_dir = work_dir or tempfile.gettempdir()
        self._env = dict(os.environ)
        if oauth_token:
            self._env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token
        logger.info(
            f"ClaudeCodeProcessor 초기화: bin={self._claude_bin}, "
            f"filter={model_filter}, process={model_process}"
        )

    def _build_command(self, args: list[str]) -> list[str]:
        """claude 실행 커맨드를 만든다. .cmd/.bat 셰임은 cmd.exe /c로 감싼다."""
        if self._claude_bin.lower().endswith((".cmd", ".bat")):
            comspec = os.environ.get("COMSPEC", "cmd.exe")
            return [comspec, "/c", self._claude_bin, *args]
        return [self._claude_bin, *args]

    def _run_claude(
        self,
        args: list[str],
        prompt: str,
        label: str = "claude",
        use_system_prompt: bool = True,
    ) -> str:
        """`claude -p`를 헤드리스로 실행하고 응답 봉투의 result(텍스트)를 반환한다.

        subprocess 실행·타임아웃·종료코드·봉투 JSON 파싱·is_error 처리를 한곳에 모은다.
        역할/규칙(SYSTEM_PROMPT)은 시스템 주입이 아니라 사용자 프롬프트 최상단에 둔다.
        (SYSTEM_PROMPT는 'JSON 배열만 출력'을 강제하므로 자유 텍스트 출력이 필요한
         호출은 use_system_prompt=False로 우회한다.)
        """
        full_prompt = f"{SYSTEM_PROMPT}\n\n{prompt}" if use_system_prompt else prompt
        cmd = self._build_command(args)
        # CLI 수준 장애는 전부 LLMBackendError로 승격 — compose/curation의 내부
        # 예외 삼킴을 통과해 hybrid의 OpenAI 폴백이 실제로 작동하게 한다.
        try:
            proc = subprocess.run(
                cmd,
                input=full_prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                cwd=self._work_dir,
                env=self._env,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise LLMBackendError(f"{label} 타임아웃({self._timeout}s)") from e
        except OSError as e:  # 바이너리 미설치·실행 불가(FileNotFoundError 포함)
            raise LLMBackendError(f"{label} 실행 실패: {e}") from e

        if proc.returncode != 0:
            raise LLMBackendError(f"{label} 종료코드 {proc.returncode}: {(proc.stderr or '')[:300]}")

        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise LLMBackendError(f"{label} 응답 봉투 JSON 파싱 실패: {(proc.stdout or '')[:300]}") from e

        if envelope.get("is_error"):
            raise LLMBackendError(f"{label} 처리 오류: {str(envelope.get('result'))[:300]}")

        # 봉투의 result에 모델의 실제 텍스트가 담긴다. 파싱은 base의 _parse_json_response가 처리.
        return envelope.get("result", "")

    def _call_api(
        self, model: str, prompt: str, max_tokens: int = 4096, *, lean: bool = False
    ) -> str:
        """필터/분류/검증엔 filter 모델, 통합엔 process 모델을 매핑해 호출. (max_tokens는 CLI 무시)

        lean=True 인 호출에만 감량 플래그를 붙인다 — 근거는 위 _LEAN_* 주석.
        ⚠️ **모델 티어로 판단하면 안 된다**: judge_tiers 는 model_filter 를 쓰지만
        피드백 보정 기반의 품질 민감 판정이라 추론이 필요하다. 호출부가 지정한다.
        """
        claude_model = (
            self._claude_model_process
            if model == self._config.model_process
            else self._claude_model_filter
        )
        args = ["-p", "--output-format", "json", "--max-turns", "1"]
        if claude_model:
            args += ["--model", claude_model]
        if lean:
            args += ["--settings", _LEAN_SETTINGS, "--disallowed-tools", *_LEAN_TOOLS]
        return self._run_claude(args, prompt, label="claude CLI")

    def _call_api_with_search(
        self,
        prompt: str,
        max_turns: int = 6,
        model: str | None = None,
        use_system_prompt: bool = True,
    ) -> str:
        """WebSearch 도구를 허용해 Claude가 직접 웹을 검색하며 응답하게 한다(구독, API 키 X)."""
        args = [
            "-p", "--output-format", "json",
            "--allowedTools", "WebSearch",
            "--max-turns", str(max_turns),
        ]
        claude_model = model or self._claude_model_filter
        if claude_model:
            args += ["--model", claude_model]
        return self._run_claude(
            args, prompt, label="claude WebSearch", use_system_prompt=use_system_prompt
        )

    async def run_freeform(
        self, prompt: str, websearch: bool = True, max_turns: int = 8
    ) -> str:
        """임의 프롬프트를 process 모델로 실행해 자유 텍스트를 반환.

        슬랙 투표 1위 심층 글 등 파이프라인 밖 용도. SYSTEM_PROMPT(JSON 강제)를
        붙이지 않으며, subprocess 블로킹은 to_thread로 이벤트 루프에서 분리한다.
        """
        if websearch:
            return await asyncio.to_thread(
                self._call_api_with_search,
                prompt,
                max_turns,
                self._claude_model_process,
                False,
            )
        args = ["-p", "--output-format", "json", "--max-turns", "1"]
        if self._claude_model_process:
            args += ["--model", self._claude_model_process]
        return await asyncio.to_thread(
            self._run_claude, args, prompt, "claude freeform", False
        )

    async def verify_claims(self, posts: list) -> list:
        """Claude 내장 WebSearch로 교차검증(DuckDuckGo 미사용). 실패 시 전체 통과.

        A) 검색+판정을 Claude가 직접(구독). C) 검증 대상 주장 상한으로 호출 절감.
        """
        if not posts:
            return []

        try:
            resp = self._call_api(
                self._config.model_filter,
                EXTRACT_CLAIMS.format(posts_json=_posts_to_json_lite(posts)),
            )
            claims = _parse_json_response(resp)
        except LLMBackendError:
            # CLI 장애는 hybrid의 OpenAI 폴백으로 — 검증 전체 스킵(스캠 통과)보다 낫다
            raise
        except Exception as e:
            logger.warning(f"주장 추출 실패(검증 스킵): {e}")
            return [VerificationResult(post_id=p.id, credibility="verified") for p in posts]

        to_verify = [
            c for c in claims
            if c.get("needs_verification") and c.get("claim") and c.get("post_id")
        ]
        to_verify = to_verify[: self._config.verify_max_claims]  # C: 상한
        if not to_verify:
            return [VerificationResult(post_id=p.id, credibility="verified") for p in posts]

        claims_json = json.dumps(
            [{"post_id": c["post_id"], "claim": c["claim"]} for c in to_verify],
            ensure_ascii=False, indent=2,
        )
        results: list = []
        verified_ids: set = set()
        try:
            resp = self._call_api_with_search(VERIFY_WITH_SEARCH.format(claims_json=claims_json))
            for item in _parse_json_response(resp):
                pid = item.get("post_id")
                if pid is None:
                    continue
                results.append(VerificationResult(
                    post_id=pid,
                    credibility=item.get("credibility", "unverified"),
                    reason=item.get("reason"),
                ))
                verified_ids.add(pid)
        except LLMBackendError:
            raise  # CLI 장애는 hybrid의 OpenAI 폴백으로 — 전체 통과보다 낫다
        except Exception as e:
            logger.warning(f"Claude 웹검증 실패(스킵/통과): {e}")

        for p in posts:
            if p.id not in verified_ids:
                results.append(VerificationResult(post_id=p.id, credibility="verified"))

        contradicted = sum(1 for r in results if r.credibility == "contradicted")
        logger.info(
            f"웹검증(Claude WebSearch) 완료: {len(to_verify)}건 검증, 허위/스캠 {contradicted}건"
        )
        return results
