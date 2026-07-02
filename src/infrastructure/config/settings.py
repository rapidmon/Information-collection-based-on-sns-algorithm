from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings


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
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""

    # Firebase
    firebase_credential_path: str = "firebase-service-account.json"
    firebase_project_id: str = ""

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


class CategoryConfig:
    def __init__(self, data: dict[str, Any]):
        self.name: str = data["name"]
        self.name_ko: str = data.get("name_ko", data["name"])
        self.color: str = data.get("color", "#888888")
        self.keywords: list[str] = data.get("keywords", [])


class ProcessingConfig:
    def __init__(self, data: dict[str, Any]):
        # AI 백엔드 선택: "openai"(API 키) 또는 "claude_code"(claude CLI, 구독)
        self.ai_backend: str = data.get("ai_backend", "openai")
        self.model_filter: str = data.get("model_filter", "gpt-4o-mini")
        self.model_process: str = data.get("model_process", "gpt-4o")
        # claude_code 백엔드용 모델 (Claude 모델 ID)
        self.claude_model_filter: str = data.get("claude_model_filter", "claude-haiku-4-5")
        self.claude_model_process: str = data.get("claude_model_process", "claude-sonnet-4-6")
        self.claude_timeout: int = data.get("claude_timeout", 300)
        self.batch_size_filter: int = data.get("batch_size_filter", 20)
        self.batch_size_summarize: int = data.get("batch_size_summarize", 15)
        self.batch_size_categorize: int = data.get("batch_size_categorize", 20)
        self.use_batch_api: bool = data.get("use_batch_api", True)
        self.min_importance_for_briefing: float = data.get("min_importance_for_briefing", 0.7)
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


class BriefingConfig:
    def __init__(self, data: dict[str, Any]):
        self.daily_time: str = data.get("daily_time", "06:30")
        self.max_items: int = data.get("max_items", 0)
        self.include_stats: bool = data.get("include_stats", True)
        # 병합 후(재산정) 점수 기준 하한 + 카테고리별 상한 (항목 과다 방지)
        self.min_importance: float = data.get("min_importance", 0.8)
        self.max_per_category: int = data.get("max_per_category", 8)


class EmailConfig:
    def __init__(self, data: dict[str, Any]):
        self.enabled: bool = data.get("enabled", True)
        self.to_addresses: list[str] = data.get("to_addresses", [])
        # 시스템 알림(로그인 오류·수집 실패 등) 수신자 — 브리핑 수신자와 별개
        self.alert_addresses: list[str] = data.get("alert_addresses", ["ehhwll@hanmail.net"])
        # 독자층별 발송: {페르소나(=큐레이션 대상): [수신주소]}
        self.audiences: dict[str, list[str]] = data.get("audiences", {}) or {}
        self.curation_enabled: bool = data.get("curation", True)
        self.logo_path: str = data.get("logo_path", "Logo.png")
        self.subject_prefix: str = data.get("subject_prefix", "Morning Commit")


class ScoringConfig:
    """브리핑 중요도 산정 가중치 (객관 신호 + LLM 티어 보정).

    최종 점수 = freq_weight*고유출처수 + engagement_weight*인게이지먼트백분위 + tier_bonus[tier]
    를 그날 최고점이 1.0이 되도록 정규화. 피드백 학습으로 이 가중치를 조정한다.
    """

    def __init__(self, data: dict[str, Any]):
        self.freq_weight: float = data.get("freq_weight", 0.05)
        self.engagement_weight: float = data.get("engagement_weight", 0.4)
        self.tier_bonus: dict[str, float] = data.get(
            "tier_bonus", {"major": 1.0, "notable": 0.4, "minor": 0.0}
        )
        # 인게이지먼트 원점수 가중 (좋아요·리포스트·댓글)
        self.w_likes: float = data.get("w_likes", 1.0)
        self.w_reposts: float = data.get("w_reposts", 2.0)
        self.w_comments: float = data.get("w_comments", 1.5)


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
        self.briefing = BriefingConfig(data.get("briefing", {}))
        self.scoring = ScoringConfig(data.get("scoring", {}))
        self.email = EmailConfig(data.get("email", {}))
        self.web = WebConfig(data.get("web", {}))


def load_app_config(path: str = "config/settings.yaml") -> AppConfig:
    """YAML 설정 파일을 로드하여 AppConfig를 반환."""
    config_path = Path(path)
    if not config_path.exists():
        return AppConfig({})
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return AppConfig(data)
