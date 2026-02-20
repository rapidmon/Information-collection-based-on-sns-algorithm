from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings


# ──────────────────────────────────────────
# 환경변수 기반 시크릿 설정 (.env)
# ──────────────────────────────────────────
class Settings(BaseSettings):
    openai_api_key: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""

    # Firebase
    firebase_credential_path: str = "firebase-service-account.json"
    firebase_project_id: str = ""

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


class CategoryConfig:
    def __init__(self, data: dict[str, Any]):
        self.name: str = data["name"]
        self.name_ko: str = data.get("name_ko", data["name"])
        self.color: str = data.get("color", "#888888")
        self.keywords: list[str] = data.get("keywords", [])


class ProcessingConfig:
    def __init__(self, data: dict[str, Any]):
        self.model_filter: str = data.get("model_filter", "gpt-4o-mini")
        self.model_process: str = data.get("model_process", "gpt-4o")
        self.batch_size_filter: int = data.get("batch_size_filter", 20)
        self.batch_size_summarize: int = data.get("batch_size_summarize", 15)
        self.batch_size_categorize: int = data.get("batch_size_categorize", 20)
        self.use_batch_api: bool = data.get("use_batch_api", True)
        self.min_importance_for_briefing: float = data.get("min_importance_for_briefing", 0.4)


class BriefingConfig:
    def __init__(self, data: dict[str, Any]):
        self.daily_time: str = data.get("daily_time", "06:30")
        self.max_items: int = data.get("max_items", 20)
        self.include_stats: bool = data.get("include_stats", True)


class EmailConfig:
    def __init__(self, data: dict[str, Any]):
        self.enabled: bool = data.get("enabled", True)
        self.to_addresses: list[str] = data.get("to_addresses", [])


class WebConfig:
    def __init__(self, data: dict[str, Any]):
        self.host: str = data.get("host", "0.0.0.0")
        self.port: int = data.get("port", 8000)
        self.auto_refresh_seconds: int = data.get("auto_refresh_seconds", 60)


class BrowserConfig:
    def __init__(self, data: dict[str, Any]):
        self.headless: bool = data.get("headless", False)
        self.profile_dir: str = data.get("profile_dir", "browser_data")
        self.user_agents: list[str] = data.get("user_agents", [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ])


class AppConfig:
    """YAML에서 로드된 전체 앱 설정."""

    def __init__(self, data: dict[str, Any]):
        self.name: str = data.get("app", {}).get("name", "SNS Tech Briefing")
        self.timezone: str = data.get("app", {}).get("timezone", "Asia/Seoul")

        collection = data.get("collection", {})
        self.collectors: dict[str, CollectorConfig] = {
            key: CollectorConfig(val) for key, val in collection.items()
        }

        self.categories: list[CategoryConfig] = [
            CategoryConfig(c) for c in data.get("categories", [])
        ]

        self.processing = ProcessingConfig(data.get("processing", {}))
        self.briefing = BriefingConfig(data.get("briefing", {}))
        self.email = EmailConfig(data.get("email", {}))
        self.web = WebConfig(data.get("web", {}))
        self.browser = BrowserConfig(data.get("browser", {}))


def load_app_config(path: str = "config/settings.yaml") -> AppConfig:
    """YAML 설정 파일을 로드하여 AppConfig를 반환."""
    config_path = Path(path)
    if not config_path.exists():
        return AppConfig({})
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return AppConfig(data)
