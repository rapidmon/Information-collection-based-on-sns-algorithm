from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────
# SNS 자격증명
# ──────────────────────────────────────────
@dataclass
class SnsCredentials:
    username: str = ""
    password: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.username and self.password)


# ──────────────────────────────────────────
# 환경변수 기반 시크릿 설정 (.env)
# ──────────────────────────────────────────
class Settings(BaseSettings):
    openai_api_key: str = ""
    # claude_code 백엔드를 다른 머신/서비스 컨텍스트에서 쓸 때만 필요(선택).
    # 본인 머신에서 claude 로그인이 되어 있으면 비워둬도 됨.
    claude_code_oauth_token: str = ""
    # 위 토큰의 만료일(YYYY-MM-DD). `claude setup-token` 발급 시점 +1년을 적어두면
    # 만료 7일 전부터 매일 슬랙 DM 알림을 보낸다 (slack.alert_user_id 필요).
    claude_code_oauth_token_expires_at: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""

    # Firebase
    firebase_credential_path: str = "firebase-service-account.json"
    firebase_project_id: str = ""

    # Slack 브리핑 발송용 Bot Token (xoxb-...). 필요 스코프: chat:write, reactions:write
    slack_bot_token: str = ""
    # Slack Events API 서명 검증용 Signing Secret (앱 Basic Information에서 확인).
    # 비워두면 @멘션 이벤트 수신이 비활성화된다 (공개 엔드포인트 보호).
    slack_signing_secret: str = ""

    # SNS Credentials (auto-login)
    twitter_username: str = ""
    twitter_password: str = ""
    threads_username: str = ""
    threads_password: str = ""
    linkedin_email: str = ""
    linkedin_password: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


# ──────────────────────────────────────────
# YAML 기반 앱 설정 (config/settings.yaml)
# ──────────────────────────────────────────
class CollectorConfig:
    def __init__(self, data: dict[str, Any]):
        self.enabled: bool = data.get("enabled", True)
        self.interval_minutes: int = data.get("interval_minutes", 30)
        self.scroll_rounds: int = data.get("scroll_rounds", 6)
        self.scroll_delay_min: float = data.get("scroll_delay_min", 2.0)
        self.scroll_delay_max: float = data.get("scroll_delay_max", 4.0)
        self.use_graphql_interception: bool = data.get("use_graphql_interception", True)
        # DCInside 전용
        self.gallery_id: str = data.get("gallery_id", "thesingularity")
        self.gallery_type: str = data.get("gallery_type", "mgallery")
        self.pages_to_scrape: int = data.get("pages_to_scrape", 3)
        self.request_delay_min: float = data.get("request_delay_min", 1.5)
        self.request_delay_max: float = data.get("request_delay_max", 3.0)
        # 수집 단계 게시일 컷오프 (collection.max_age_days에서 주입)
        self.max_age_days: int = data.get("max_age_days", 2)
        # news 전용 — RSS/Atom 피드 선언 목록 [{name, tier, url}] + 피드당 항목 상한
        self.feeds: list[dict[str, Any]] = data.get("feeds", [])
        self.max_items_per_feed: int = data.get("max_items_per_feed", 30)
        # donga_series 전용 — 연재 페이지 URL과 표시용 시리즈명
        self.series_url: str = data.get("series_url", "")
        self.series_name: str = data.get("series_name", "연재")


class CategoryConfig:
    def __init__(self, data: dict[str, Any]):
        self.name: str = data["name"]
        self.name_ko: str = data.get("name_ko", data["name"])
        self.color: str = data.get("color", "#888888")
        self.keywords: list[str] = data.get("keywords", [])


class ProcessingConfig:
    """AI 백엔드는 하이브리드 고정 — OpenAI(고빈도 배치) + Claude(발행 작문·큐레이션)."""

    def __init__(self, data: dict[str, Any]):
        # OpenAI 모델: 필터/분류/검증(model_filter), 작문 폴백(model_process)
        self.model_filter: str = data.get("model_filter", "gpt-4o-mini")
        self.model_process: str = data.get("model_process", "gpt-4o")
        # Claude 모델 (발행 작문·큐레이션용)
        self.claude_model_filter: str = data.get("claude_model_filter", "claude-haiku-4-5")
        self.claude_model_process: str = data.get("claude_model_process", "claude-sonnet-4-6")
        # 브리핑 dedup 두 단계는 사고 이력(7월 recall/precision 붕괴) 때문에 모델을
        # 독립 키로 분리한다. 하루 1회 실행이라 상위 모델을 써도 한도 영향 미미.
        self.claude_model_dedup: str = data.get("claude_model_dedup", "claude-opus-4-8")
        self.claude_model_consolidate: str = data.get(
            "claude_model_consolidate", "claude-haiku-4-5"
        )
        self.claude_timeout: int = data.get("claude_timeout", 300)
        # Codex CLI(ChatGPT 구독) — OpenAI API를 대체하는 폴백/보조 백엔드.
        # 빈 문자열이면 -m 을 넘기지 않아 codex 기본 모델을 쓴다(구독 등급에 맞춰 자동).
        self.codex_model_filter: str = data.get("codex_model_filter", "")
        self.codex_model_process: str = data.get("codex_model_process", "")
        self.codex_timeout: int = data.get("codex_timeout", 600)
        self.codex_model_dedup: str = data.get("codex_model_dedup", "")
        self.codex_model_consolidate: str = data.get("codex_model_consolidate", "")
        self.codex_effort_filter: str = data.get("codex_effort_filter", "low")
        self.codex_effort_process: str = data.get("codex_effort_process", "medium")
        self.codex_effort_dedup: str = data.get("codex_effort_dedup", "")
        self.codex_effort_consolidate: str = data.get("codex_effort_consolidate", "")
        # True면 Claude를 아예 쓰지 않는다(고정 경로 포함) — 단일 백엔드 품질 비교용.
        self.codex_only: bool = data.get("codex_only", False)
        # 고빈도 배치(필터·분류·검증·기브리핑판정·통합가드) 백엔드:
        # "openai"(종량 과금) | "claude"(정액 구독). OpenAI는 토큰당 실지출이고
        # Claude CLI는 구독 한도를 쓴다 — 현금을 줄이려면 "claude"가 최선이나,
        # 무인 파이프라인이 대화형 작업과 한도를 공유하므로 전환 후 최소 1주일은
        # rate limit 체감을 관찰할 것. 백엔드 장애 시 OpenAI로 자동 폴백.
        # (filter_backend는 필터만 가리키던 구 키 — 호환용으로 계속 읽는다)
        self.routine_backend: str = data.get(
            "routine_backend", data.get("filter_backend", "openai")
        )
        self.batch_size_filter: int = data.get("batch_size_filter", 20)
        self.batch_size_categorize: int = data.get("batch_size_categorize", 20)
        self.dedup_chunk_size: int = data.get("dedup_chunk_size", 80)
        # verify_claims: 웹검증 대상 주장 상한(C) — 호출/쿼터 절감
        self.verify_max_claims: int = data.get("verify_max_claims", 8)
        self.processing_interval_minutes: int = data.get("processing_interval_minutes", 30)
        self.min_posts_to_process: int = data.get("min_posts_to_process", 5)


class LikeConfig:
    """자동 좋아요 설정. AI 처리 후 관련+중요 게시물에만 좋아요를 누른다."""

    def __init__(self, data: dict[str, Any]):
        self.enabled: bool = data.get("enabled", False)
        self.dry_run: bool = data.get("dry_run", True)
        self.max_per_run: int = data.get("max_per_run", 10)
        # 이 중요도 이상인 관련 게시물만 좋아요
        self.min_importance: float = data.get("min_importance", 0.7)
        self.delay_min: float = data.get("delay_min", 2.0)
        self.delay_max: float = data.get("delay_max", 5.0)
        self.platforms: list[str] = data.get(
            "platforms", ["twitter", "threads", "linkedin"]
        )


class FollowConfig:
    """자동 팔로우 설정. 좋아요가 누적된 계정을 팔로우해 알고리즘 피드를 유도한다.

    max_per_run이 낮은 이유: X는 '짧은 시간에 몰아서'를 자동화로 판정한다.
    총량이 같아도 사이클당 소수로 쪼개면 훨씬 안전하다.
    """

    def __init__(self, data: dict[str, Any]):
        self.enabled: bool = data.get("enabled", False)
        self.dry_run: bool = data.get("dry_run", True)
        # 이 수 이상 좋아요한 계정을 팔로우 대상으로
        self.min_likes: int = data.get("min_likes", 5)
        self.max_per_run: int = data.get("max_per_run", 5)
        self.max_attempts: int = data.get("max_attempts", 3)
        self.delay_min: float = data.get("delay_min", 5.0)
        self.delay_max: float = data.get("delay_max", 12.0)
        self.platforms: list[str] = data.get("platforms", ["twitter"])


class BriefingConfig:
    def __init__(self, data: dict[str, Any]):
        self.daily_time: str = data.get("daily_time", "06:30")
        self.max_items: int = data.get("max_items", 0)
        self.include_stats: bool = data.get("include_stats", True)
        # 병합 후(재산정) 점수 기준 하한 + 카테고리별 상한 (항목 과다 방지)
        self.min_importance: float = data.get("min_importance", 0.8)
        self.max_per_category: int = data.get("max_per_category", 8)
        # ─ 생성 단계 슈퍼셋 상한 (발송단 개인화용) ─
        # 수신자별 카테고리 한도가 기본 상한을 넘으면 생성 단계에서 그만큼
        # 넉넉히 뽑아 저장해 둬야 발송 시 트리밍으로 개인화가 가능하다.
        # AppConfig가 email.audiences를 읽어 apply_recipient_caps()로 채운다.
        self.category_caps: dict[str, int] = {}
        self._gen_max_default: int = self.max_per_category

    def apply_recipient_caps(self, recipients: "list[EmailRecipient]") -> None:
        """수신자 개인화 한도의 최대값을 생성 단계 상한에 반영."""
        for r in recipients:
            if r.max_per_category:
                self._gen_max_default = max(self._gen_max_default, r.max_per_category)
            for cat, n in r.category_limits.items():
                self.category_caps[cat] = max(self.category_caps.get(cat, 0), n)

    def cap_for(self, category: str) -> int:
        """생성 단계에서 이 카테고리를 최대 몇 개까지 선별할지."""
        return max(self._gen_max_default, self.category_caps.get(category, 0))


# 수신자 개인화 한도(max_per_category·category_limits)의 허용 범위.
# 하한 2: 카테고리를 사실상 꺼버리는 설정 방지 / 상한 10: 생성 슈퍼셋·작문
# 토큰이 수신자 설정만으로 무한정 커지는 것 방지. 범위 밖 값은 경고 후 보정.
RECIPIENT_LIMIT_MIN = 2
RECIPIENT_LIMIT_MAX = 10


class EmailRecipient:
    """브리핑 수신자 1명.

    YAML에서 문자열(주소만) 또는 dict로 정의한다:
      - "a@b.com"                              # 기본 한도(briefing.max_per_category)
      - email: "c@d.com"                       # 개인별 카테고리 한도 조정
        max_per_category: 4                    # 기본 카테고리당 항목 수 override
        category_limits: { Coding: 10 }        # 특정 카테고리만 별도 한도 (영문 키)
    한도 값은 RECIPIENT_LIMIT_MIN~MAX(2~10) 범위로 보정된다.
    """

    def __init__(self, data: str | dict[str, Any]):
        if isinstance(data, str):
            self.email: str = data.strip()
            self.max_per_category: int | None = None
            self.category_limits: dict[str, int] = {}
        else:
            self.email = str(data.get("email", "")).strip()
            mpc = data.get("max_per_category")
            self.max_per_category = self._clamp("max_per_category", mpc) if mpc else None
            self.category_limits = {
                str(k): self._clamp(f"category_limits.{k}", v)
                for k, v in (data.get("category_limits") or {}).items()
            }

    def _clamp(self, field: str, value: Any) -> int:
        n = int(value)
        clamped = max(RECIPIENT_LIMIT_MIN, min(RECIPIENT_LIMIT_MAX, n))
        if clamped != n:
            logger.warning(
                f"email.audiences 수신자 {self.email}의 {field}={n}은 허용 범위"
                f"({RECIPIENT_LIMIT_MIN}~{RECIPIENT_LIMIT_MAX}) 밖 — {clamped}로 보정"
            )
        return clamped

    @property
    def limits_key(self) -> tuple:
        """같은 뷰(한도 조합)를 받는 수신자를 묶는 그룹 키."""
        return (self.max_per_category, tuple(sorted(self.category_limits.items())))


class EmailConfig:
    def __init__(self, data: dict[str, Any]):
        self.enabled: bool = data.get("enabled", True)
        self.to_addresses: list[str] = data.get("to_addresses", [])
        # 시스템 알림(로그인 오류·수집 실패 등) 수신자 — 브리핑 수신자와 별개
        self.alert_addresses: list[str] = data.get("alert_addresses", ["ehhwll@hanmail.net"])
        # 독자층별 발송: {페르소나(=큐레이션 대상): [수신자]} — 항목은 str 또는 dict
        self.audiences: dict[str, list[EmailRecipient]] = {
            persona: [
                r for r in (EmailRecipient(x) for x in (entries or [])) if r.email
            ]
            for persona, entries in (data.get("audiences", {}) or {}).items()
        }
        self.curation_enabled: bool = data.get("curation", True)
        self.logo_path: str = data.get("logo_path", "Logo.png")
        self.subject_prefix: str = data.get("subject_prefix", "Morning Commit")

    def briefing_targets(self) -> dict[str, list[EmailRecipient]]:
        """발송 대상: 독자층 지정이 있으면 그룹별, 없으면 to_addresses 단일(중립)."""
        if self.audiences:
            return self.audiences
        return {"": [EmailRecipient(a) for a in self.to_addresses]}

    def iter_recipients(self) -> "list[EmailRecipient]":
        return [r for rs in self.briefing_targets().values() for r in rs]


class SlackConfig:
    """슬랙 브리핑 발송 설정.

    헤더 메시지 1개 + 항목별 스레드 댓글로 게시하고, 각 항목에 투표용
    이모지 리액션을 선부착한다. 토큰은 .env의 SLACK_BOT_TOKEN.
    """

    def __init__(self, data: dict[str, Any]):
        self.enabled: bool = data.get("enabled", False)
        # 채널명("#tech-briefing") 또는 채널 ID("C0123..."). 봇이 초대돼 있어야 함.
        self.channel: str = data.get("channel", "")
        # 각 항목에 선부착할 리액션 이모지 이름 (콜론 없이). [0]=찬성, [1]=반대로 집계.
        self.reactions: list[str] = data.get("reactions", ["+1", "-1"])
        # ─ 투표 집계 → 1위 항목 심층 글 생성 ─
        # 집계 실행 시각 (HH:MM). winner_prompt가 비어 있으면 기능 전체 비활성.
        self.vote_close_time: str = data.get("vote_close_time", "12:00")
        # 집계 실행 요일 (APScheduler day_of_week 형식). 주말엔 투표를 안 하므로 기본 평일만.
        self.vote_days: str = data.get("vote_days", "mon-fri")
        # 1위 항목에 돌릴 프롬프트 템플릿. 플레이스홀더:
        #   {headline} {bullets} {category} {sources} {posts} {date}
        self.winner_prompt: str = data.get("winner_prompt", "")
        # 본문 캡션용 추가 프롬프트 (선택). 결과는 타이틀 메시지의 스레드에 게시.
        # winner_prompt가 있어야 동작하며, 같은 플레이스홀더 사용.
        self.caption_prompt: str = data.get("caption_prompt", "")
        # 프롬프트 실행 시 Claude WebSearch 허용 여부
        self.winner_websearch: bool = data.get("winner_websearch", True)
        # 게시된 항목 ts↔항목 매핑 저장 파일 (집계 잡이 읽음, 매일 덮어씀)
        self.state_path: str = data.get("state_path", "data/slack_briefing_state.json")
        # 운영 알림(토큰 만료 등) DM 수신자의 슬랙 멤버 ID ("U0123..." 형식).
        # 프로필 → ⋮ → '멤버 ID 복사'로 확인. 비우면 DM 알림 비활성.
        self.alert_user_id: str = data.get("alert_user_id", "")


class ScoringConfig:
    """브리핑 중요도 산정 가중치.

    게시물 점수 = (1 - engagement_weight)*AI중요도 + engagement_weight*인게이지먼트백분위.
    병합된 사건은 카테고리별 구성 게시물 점수의 top-3 평균으로 평가하고,
    카테고리 안에서만 상대 정규화한다.
    """

    def __init__(self, data: dict[str, Any]):
        self.freq_weight: float = data.get("freq_weight", 0.05)
        self.engagement_weight: float = data.get("engagement_weight", 0.2)
        self.tier_bonus: dict[str, float] = data.get(
            "tier_bonus", {"major": 1.0, "notable": 0.4, "minor": 0.0}
        )
        # 인게이지먼트 원점수 가중 (좋아요·리포스트·댓글)
        self.w_likes: float = data.get("w_likes", 1.0)
        self.w_reposts: float = data.get("w_reposts", 2.0)
        self.w_comments: float = data.get("w_comments", 1.5)
        # Deprecated. 카테고리 간 base 보정 대신 score_topics()에서 카테고리별 상대평가를 한다.
        self.category_base: dict[str, float] = data.get("category_base", {})


class WebConfig:
    def __init__(self, data: dict[str, Any]):
        self.host: str = data.get("host", "0.0.0.0")
        self.port: int = data.get("port", 8000)
        self.auto_refresh_seconds: int = data.get("auto_refresh_seconds", 60)


class AppConfig:
    """YAML에서 로드된 전체 앱 설정."""

    def __init__(self, data: dict[str, Any]):
        self.name: str = data.get("app", {}).get("name", "SNS Tech Briefing")
        self.timezone: str = data.get("app", {}).get("timezone", "Asia/Seoul")

        collection = dict(data.get("collection", {}))
        # 전역 max_age_days를 각 collector dict에 주입 (개별 override 가능)
        global_max_age = collection.pop("max_age_days", 2)
        for val in collection.values():
            if isinstance(val, dict):
                val.setdefault("max_age_days", global_max_age)
        self.collectors: dict[str, CollectorConfig] = {
            key: CollectorConfig(val) for key, val in collection.items()
        }

        self.categories: list[CategoryConfig] = [
            CategoryConfig(c) for c in data.get("categories", [])
        ]

        self.processing = ProcessingConfig(data.get("processing", {}))
        self.like = LikeConfig(data.get("like", {}))
        self.follow = FollowConfig(data.get("follow", {}))
        self.briefing = BriefingConfig(data.get("briefing", {}))
        self.scoring = ScoringConfig(data.get("scoring", {}))
        self.email = EmailConfig(data.get("email", {}))
        self.slack = SlackConfig(data.get("slack", {}))
        self.web = WebConfig(data.get("web", {}))

        # 수신자 개인화 한도(코딩 10개 등)를 생성 단계 슈퍼셋 상한에 반영.
        # 생성 시 넉넉히 뽑아 저장하고, 발송 시 수신자별로 트리밍한다.
        recipients = self.email.iter_recipients()
        self.briefing.apply_recipient_caps(recipients)
        # 카테고리 키 오타는 조용히 무시되므로 여기서 경고
        valid_cats = {c.name for c in self.categories}
        for r in recipients:
            for cat in r.category_limits:
                if valid_cats and cat not in valid_cats:
                    logger.warning(
                        f"email.audiences 수신자 {r.email}의 category_limits에 "
                        f"알 수 없는 카테고리 키: {cat!r} (유효: {sorted(valid_cats)})"
                    )


def load_app_config(path: str = "config/settings.yaml") -> AppConfig:
    """YAML 설정 파일을 로드하여 AppConfig를 반환."""
    config_path = Path(path)
    if not config_path.exists():
        return AppConfig({})
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return AppConfig(data)
