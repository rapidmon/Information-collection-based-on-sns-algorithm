"""의존성 주입 컨테이너.

클린 아키텍처에서 모든 의존성 조립은 최외곽(Composition Root)에서 이루어진다.
이 컨테이너가 설정에 따라 구체 구현을 생성하고 유즈케이스에 주입한다.
"""

from __future__ import annotations

from src.application.use_cases.collect_posts import CollectPostsUseCase
from src.application.use_cases.generate_briefing import GenerateBriefingUseCase
from src.application.use_cases.process_posts import ProcessPostsUseCase
from src.application.use_cases.send_briefing import SendBriefingUseCase
from src.infrastructure.ai.openai_processor import OpenAIProcessor
from src.infrastructure.collectors.dcinside_collector import DCInsideCollector
from src.infrastructure.collectors.linkedin_collector import LinkedInCollector
from src.infrastructure.collectors.threads_collector import ThreadsCollector
from src.infrastructure.collectors.twitter_collector import TwitterCollector
from src.infrastructure.config.settings import AppConfig, Settings
from src.infrastructure.database.repositories.briefing_repo import FirestoreBriefingRepository
from src.infrastructure.database.repositories.category_repo import FirestoreCategoryRepository
from src.infrastructure.database.repositories.collection_run_repo import (
    FirestoreCollectionRunRepository,
)
from src.infrastructure.database.repositories.post_repo import FirestorePostRepository
from src.infrastructure.delivery.briefing_builder import DefaultBriefingGenerator
from src.infrastructure.delivery.email_sender import EmailNotifier


class Container:
    """애플리케이션 의존성 컨테이너."""

    def __init__(
        self,
        settings: Settings,
        app_config: AppConfig,
        firestore_db,
    ):
        self.settings = settings
        self.config = app_config

        # ─── Repositories (Firebase Firestore) ───
        self.post_repo = FirestorePostRepository(firestore_db)
        self.briefing_repo = FirestoreBriefingRepository(firestore_db)
        self.category_repo = FirestoreCategoryRepository(firestore_db)
        self.run_repo = FirestoreCollectionRunRepository(firestore_db)

        # ─── Infrastructure Services ───
        self.ai_processor = OpenAIProcessor(
            api_key=settings.openai_api_key,
            config=app_config.processing,
        )

        self.briefing_generator = DefaultBriefingGenerator(app_config.briefing)

        self.notifier = EmailNotifier(settings, app_config.email)

        # ─── Collectors ───
        self.collectors: dict[str, object] = {}
        self._init_collectors()

    def _init_collectors(self) -> None:
        collector_configs = self.config.collectors

        if "dcinside" in collector_configs and collector_configs["dcinside"].enabled:
            self.collectors["dcinside"] = DCInsideCollector(collector_configs["dcinside"])

        # SNS 수집기 — 모두 CDP 기반 (사용자의 Chrome에 연결)
        if "twitter" in collector_configs and collector_configs["twitter"].enabled:
            self.collectors["twitter"] = TwitterCollector(
                config=collector_configs["twitter"],
            )
        if "threads" in collector_configs and collector_configs["threads"].enabled:
            self.collectors["threads"] = ThreadsCollector(
                config=collector_configs["threads"],
            )
        if "linkedin" in collector_configs and collector_configs["linkedin"].enabled:
            self.collectors["linkedin"] = LinkedInCollector(
                config=collector_configs["linkedin"],
            )

    # ─── Use Case 팩토리 ───

    def collect_posts_use_case(self, source: str) -> CollectPostsUseCase:
        collector = self.collectors.get(source)
        if collector is None:
            raise ValueError(f"'{source}' 수집기가 등록되지 않음")
        return CollectPostsUseCase(
            collector=collector,
            post_repo=self.post_repo,
            run_repo=self.run_repo,
        )

    def process_posts_use_case(self) -> ProcessPostsUseCase:
        return ProcessPostsUseCase(
            post_repo=self.post_repo,
            ai_processor=self.ai_processor,
        )

    def generate_briefing_use_case(self) -> GenerateBriefingUseCase:
        return GenerateBriefingUseCase(
            post_repo=self.post_repo,
            briefing_repo=self.briefing_repo,
            ai_processor=self.ai_processor,
            briefing_generator=self.briefing_generator,
        )

    def send_briefing_use_case(self) -> SendBriefingUseCase:
        return SendBriefingUseCase(
            briefing_repo=self.briefing_repo,
            notifier=self.notifier,
        )
