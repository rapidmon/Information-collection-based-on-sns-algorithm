"""의존성 주입 컨테이너.

클린 아키텍처에서 모든 의존성 조립은 최외곽(Composition Root)에서 이루어진다.
이 컨테이너가 설정에 따라 구체 구현을 생성하고 유즈케이스에 주입한다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from src.domain.entities import Category
from src.application.use_cases.collect_posts import CollectPostsUseCase
from src.application.use_cases.generate_briefing import GenerateBriefingUseCase
from src.application.use_cases.like_posts import LikePostsUseCase
from src.application.use_cases.process_posts import ProcessPostsUseCase
from src.infrastructure.ai.claude_code_processor import ClaudeCodeProcessor
from src.infrastructure.ai.hybrid_processor import HybridAIProcessor
from src.infrastructure.ai.openai_processor import OpenAIProcessor
from src.infrastructure.collectors.dcinside_collector import DCInsideCollector
from src.infrastructure.collectors.kr36_collector import Kr36Collector
from src.infrastructure.collectors.linkedin_collector import LinkedInCollector
from src.infrastructure.collectors.post_liker import CdpPostLiker
from src.infrastructure.collectors.producthunt_collector import ProductHuntCollector
from src.infrastructure.collectors.threads_collector import ThreadsCollector
from src.infrastructure.collectors.twitter_collector import TwitterCollector
from src.infrastructure.config.settings import AppConfig, Settings, SnsCredentials
from src.infrastructure.database.repositories.briefing_repo import FirestoreBriefingRepository
from src.infrastructure.database.repositories.category_repo_memory import MemoryCategoryRepository
from src.infrastructure.database.repositories.collection_run_repo_sqlite import SQLiteCollectionRunRepository
from src.infrastructure.database.repositories.feedback_repo_sqlite import FeedbackRepositorySQLite
from src.infrastructure.database.repositories.post_repo_sqlite import PostRepositorySQLite
from src.infrastructure.delivery.briefing_builder import DefaultBriefingGenerator
from src.infrastructure.delivery.email_sender import EmailNotifier
from src.infrastructure.delivery.slack_sender import SlackNotifier


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

        # AI 처리 단일 실행 락 — interval 잡·브리핑 전 처리·수동 트리거가 겹쳐도
        # 같은 미처리 배치가 LLM을 2중으로 타지 않게 한다.
        self._process_run_lock = asyncio.Lock()

        # ─── Repositories ───
        self.post_repo = PostRepositorySQLite()
        self.briefing_repo = FirestoreBriefingRepository(firestore_db)  # 브리핑만 Firestore
        self.feedback_repo = FeedbackRepositorySQLite()  # 항목 피드백(적절/과대/과소)
        self.category_repo = MemoryCategoryRepository(
            [Category(name=c.name, name_ko=c.name_ko, color=c.color) for c in app_config.categories]
        )
        self.run_repo = SQLiteCollectionRunRepository()

        # ─── Infrastructure Services ───
        # AI 백엔드는 하이브리드 고정: 고빈도 배치(필터·분류·검증)=OpenAI,
        # 저빈도·품질 민감(발행 작문·큐레이션)=Claude Code. (백엔드 선택 옵션은 제거됨)
        openai_processor = OpenAIProcessor(
            api_key=settings.openai_api_key,
            config=app_config.processing,
        )
        claude_processor = ClaudeCodeProcessor(
            config=app_config.processing,
            model_filter=app_config.processing.claude_model_filter,
            model_process=app_config.processing.claude_model_process,
            timeout=app_config.processing.claude_timeout,
            oauth_token=settings.claude_code_oauth_token or None,
        )
        self.ai_processor = HybridAIProcessor(openai_processor, claude_processor)
        # 슬랙 투표 1위 심층 글 등 파이프라인 밖 자유 프롬프트 실행용 직접 참조
        self.claude_processor = claude_processor

        self.briefing_generator = DefaultBriefingGenerator(app_config.briefing)

        self.notifier = EmailNotifier(settings, app_config.email)

        # 슬랙 브리핑 게시 (헤더+스레드, 항목별 투표 리액션 선부착)
        self.slack_notifier = SlackNotifier(settings, app_config.slack)

        # 자동 좋아요 (AI 처리 후 관련+중요 게시물에만)
        self.post_liker = CdpPostLiker(app_config.like)

        # ─── Collectors ───
        self.collectors: dict[str, object] = {}
        self._init_collectors()

    def _init_collectors(self) -> None:
        collector_configs = self.config.collectors

        if "dcinside" in collector_configs and collector_configs["dcinside"].enabled:
            self.collectors["dcinside"] = DCInsideCollector(collector_configs["dcinside"])

        if "36kr" in collector_configs and collector_configs["36kr"].enabled:
            self.collectors["36kr"] = Kr36Collector(collector_configs["36kr"])

        if "producthunt" in collector_configs and collector_configs["producthunt"].enabled:
            self.collectors["producthunt"] = ProductHuntCollector(collector_configs["producthunt"])

        # SNS 수집기 — 모두 CDP 기반 (사용자의 Chrome에 연결)
        if "twitter" in collector_configs and collector_configs["twitter"].enabled:
            self.collectors["twitter"] = TwitterCollector(
                config=collector_configs["twitter"],
                credentials=SnsCredentials(
                    self.settings.twitter_username,
                    self.settings.twitter_password,
                ),
            )
        if "threads" in collector_configs and collector_configs["threads"].enabled:
            self.collectors["threads"] = ThreadsCollector(
                config=collector_configs["threads"],
                credentials=SnsCredentials(
                    self.settings.threads_username,
                    self.settings.threads_password,
                ),
            )
        if "linkedin" in collector_configs and collector_configs["linkedin"].enabled:
            self.collectors["linkedin"] = LinkedInCollector(
                config=collector_configs["linkedin"],
                credentials=SnsCredentials(
                    self.settings.linkedin_email,
                    self.settings.linkedin_password,
                ),
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
            run_lock=self._process_run_lock,
        )

    def like_posts_use_case(self) -> LikePostsUseCase:
        return LikePostsUseCase(
            post_repo=self.post_repo,
            liker=self.post_liker,
            config=self.config.like,
        )

    def generate_briefing_use_case(self) -> GenerateBriefingUseCase:
        return GenerateBriefingUseCase(
            post_repo=self.post_repo,
            briefing_repo=self.briefing_repo,
            ai_processor=self.ai_processor,
            briefing_generator=self.briefing_generator,
            scoring_config=self.config.scoring,
            feedback_repo=self.feedback_repo,
        )

    async def send_curated_briefing(self, briefing) -> dict:
        """독자층별 큐레이션 생성 → Morning Commit HTML 렌더 → 그룹별 발송."""
        from src.domain.services.ai_processor import Curation, MergedTopic
        from src.infrastructure.delivery.categories import VALID_BRIEFING_CATEGORIES
        from src.infrastructure.delivery.email_renderer import render_email_html

        ecfg = self.config.email
        topics = []
        for it in briefing.items:
            if it.category_name not in VALID_BRIEFING_CATEGORIES:
                continue
            # 구조화 불릿을 그대로 사용(재파싱 방지). 구버전 브리핑엔 없으므로 body에서 폴백.
            bullets = list(it.body_bullets) if it.body_bullets else [
                l.strip().lstrip("- ").strip() for l in (it.body or "").split("\n") if l.strip()
            ]
            topics.append(MergedTopic(
                post_ids=it.source_post_ids or [], headline=it.headline, body_bullets=bullets,
                primary_category=it.category_name,
                importance_score=it.importance_score or 0.5,
                sources=[], source_urls=it.source_urls or [],
            ))

        d = briefing.period_end or briefing.generated_at
        # Firestore는 datetime을 UTC로 저장/복원한다. 08:00 KST는 전날 23:00 UTC라
        # 변환 없이 strftime하면 하루 밀린다 → 표시 전 반드시 KST로 환산.
        if hasattr(d, "strftime"):
            if getattr(d, "tzinfo", None) is not None:
                d = d.astimezone(ZoneInfo("Asia/Seoul"))
            date_str = d.strftime("%Y. %m. %d")
        else:
            date_str = str(d)[:10]
        subject = f"[{ecfg.subject_prefix}] {date_str}"

        # 독자층 지정이 있으면 그룹별, 없으면 to_addresses로 단일(중립) 발송
        targets = ecfg.audiences if ecfg.audiences else {"": ecfg.to_addresses}
        results: dict = {}
        for persona, addrs in targets.items():
            if not addrs:
                continue
            if persona and ecfg.curation_enabled:
                curation = await self.ai_processor.generate_curation(topics, persona)
            else:
                curation = Curation(title="", paragraphs=[], kick="", categories={})
            html = render_email_html(briefing, curation, "cid:logo", date_str)
            ok = await self.notifier.send_html(
                subject, html, briefing.content_text, addrs, ecfg.logo_path
            )
            results[persona or "default"] = {"sent": ok, "recipients": len(addrs)}

        # 슬랙 게시 — 이메일과 독립적으로 시도 (실패해도 이메일 결과에 영향 없음)
        if self.slack_notifier.is_configured:
            try:
                results["slack"] = await self.slack_notifier.send_briefing(briefing, date_str)
            except Exception as e:
                results["slack"] = {"sent": False, "error": str(e)}
        return results

    async def run_slack_vote_winner(self) -> dict:
        """슬랙 투표 집계 → 1위 항목에 winner_prompt 실행 → 결과 채널 게시.

        투표가 하나도 없으면 중요도 최고 항목으로 폴백하고 메시지에 그 사실을 밝힌다.
        오늘 게시분이 아니면(서버 재시작 등으로 상태가 오래됨) 아무것도 하지 않는다.
        """
        from src.infrastructure.delivery.categories import CATEGORY_EMOJI, CATEGORY_KO
        from src.infrastructure.delivery.slack_sender import pick_winner, render_winner_prompt

        scfg = self.config.slack
        if not (self.slack_notifier.is_configured and scfg.winner_prompt.strip()):
            return {"run": False, "reason": "not_configured"}

        tally = await self.slack_notifier.tally_votes()
        if not tally or not tally.get("items"):
            return {"run": False, "reason": "no_state"}

        # 상태 파일이 오늘 게시분인지 확인 (지난 브리핑 재집계 방지)
        today = datetime.now(ZoneInfo(self.config.timezone)).strftime("%Y. %m. %d")
        if tally.get("date_str") != today:
            return {"run": False, "reason": f"stale_state({tally.get('date_str')})"}

        winner = pick_winner(tally["items"])
        if winner is None:
            return {"run": False, "reason": "no_items"}

        # 원본 게시물 본문 수집 (브리핑 후 정리로 삭제됐을 수 있어 있는 것만)
        posts_lines = []
        for pid in (winner.get("source_post_ids") or [])[:5]:
            try:
                p = self.post_repo.find_by_id(str(pid))
            except Exception:
                p = None
            if p is not None and getattr(p, "content", None):
                posts_lines.append(f"[{p.source}] {p.content[:1500]}")
        posts_text = "\n\n".join(posts_lines) if posts_lines else "(원본 게시물 없음)"

        prompt = render_winner_prompt(scfg.winner_prompt, winner, posts_text, tally["date_str"])
        text = await self.claude_processor.run_freeform(
            prompt, websearch=scfg.winner_websearch
        )

        cat = winner.get("category") or "Other"
        vote_note = (
            "투표 없음 — 중요도 기준 선정" if winner.get("no_votes")
            else f"👍 {winner.get('up', 0)} · 👎 {winner.get('down', 0)}"
        )
        header = (
            f"🏆 *오늘의 투표 1위* — "
            f"{CATEGORY_EMOJI.get(cat, '🗂')} [{CATEGORY_KO.get(cat, cat)}] "
            f"{winner.get('headline', '')} ({vote_note})"
        )
        await self.slack_notifier.post_message(f"{header}\n\n{text}")
        return {
            "run": True,
            "winner": winner.get("headline"),
            "up": winner.get("up", 0),
            "down": winner.get("down", 0),
            "no_votes": winner.get("no_votes", False),
            "text": text,
        }
