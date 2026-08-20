"""의존성 주입 컨테이너.

클린 아키텍처에서 모든 의존성 조립은 최외곽(Composition Root)에서 이루어진다.
이 컨테이너가 설정에 따라 구체 구현을 생성하고 유즈케이스에 주입한다.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from src.domain.entities import Category
from src.application.use_cases.collect_posts import CollectPostsUseCase
from src.application.use_cases.follow_accounts import FollowAccountsUseCase
from src.application.use_cases.generate_briefing import GenerateBriefingUseCase
from src.application.use_cases.like_posts import LikePostsUseCase
from src.application.use_cases.process_posts import ProcessPostsUseCase
from src.infrastructure.ai.claude_code_processor import ClaudeCodeProcessor
from src.infrastructure.ai.codex_cli_processor import CodexCliProcessor
from src.infrastructure.ai.hybrid_processor import HybridAIProcessor
from src.infrastructure.collectors.account_follower import CdpAccountFollower
from src.infrastructure.collectors.dcinside_collector import DCInsideCollector
from src.infrastructure.collectors.donga_series_collector import DongaSeriesCollector
from src.infrastructure.collectors.kr36_collector import Kr36Collector
from src.infrastructure.collectors.linkedin_collector import LinkedInCollector
from src.infrastructure.collectors.news_collector import NewsCollector
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

logger = logging.getLogger(__name__)


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
        # AI 백엔드는 하이브리드: 고빈도 배치(필터·분류·검증 등)는 routine_backend
        # 설정으로 선택(현재 claude=정액 구독), 발행 작문·큐레이션은 Claude 고정.
        # Claude 장애 시 Codex CLI로 폴백한다. 두 경로 모두 구독 인증을 사용한다.
        # Codex 웹검증은 공용 DuckDuckGo 검증 파이프라인을 상속한다.
        fallback_processor = CodexCliProcessor(
            config=app_config.processing,
            model_filter=app_config.processing.codex_model_filter,
            model_process=app_config.processing.codex_model_process,
            model_dedup=app_config.processing.codex_model_dedup,
            model_consolidate=app_config.processing.codex_model_consolidate,
            effort_filter=app_config.processing.codex_effort_filter,
            effort_process=app_config.processing.codex_effort_process,
            effort_dedup=app_config.processing.codex_effort_dedup,
            effort_consolidate=app_config.processing.codex_effort_consolidate,
            timeout=app_config.processing.codex_timeout,
        )
        claude_processor = ClaudeCodeProcessor(
            config=app_config.processing,
            model_filter=app_config.processing.claude_model_filter,
            model_process=app_config.processing.claude_model_process,
            model_dedup=app_config.processing.claude_model_dedup,
            model_consolidate=app_config.processing.claude_model_consolidate,
            timeout=app_config.processing.claude_timeout,
            oauth_token=settings.claude_code_oauth_token or None,
        )
        self.ai_processor = HybridAIProcessor(fallback_processor, claude_processor)
        # 슬랙 투표 1위 심층 글 등 파이프라인 밖 자유 프롬프트 실행용 직접 참조
        self.claude_processor = claude_processor

        self.briefing_generator = DefaultBriefingGenerator(app_config.briefing)

        self.notifier = EmailNotifier(settings, app_config.email)

        # 슬랙 브리핑 게시 (헤더+스레드, 항목별 투표 리액션 선부착)
        self.slack_notifier = SlackNotifier(settings, app_config.slack)

        # 자동 좋아요 (AI 처리 후 관련+중요 게시물에만)
        self.post_liker = CdpPostLiker(app_config.like)
        self.account_follower = CdpAccountFollower(app_config.follow)

        # ─── Collectors ───
        self.collectors: dict[str, object] = {}
        self._init_collectors()

    def _init_collectors(self) -> None:
        collector_configs = self.config.collectors

        if "dcinside" in collector_configs and collector_configs["dcinside"].enabled:
            self.collectors["dcinside"] = DCInsideCollector(collector_configs["dcinside"])

        if (
            "donga_series" in collector_configs
            and collector_configs["donga_series"].enabled
            and collector_configs["donga_series"].series_url
        ):
            self.collectors["donga_series"] = DongaSeriesCollector(
                collector_configs["donga_series"]
            )

        if "36kr" in collector_configs and collector_configs["36kr"].enabled:
            self.collectors["36kr"] = Kr36Collector(collector_configs["36kr"])

        if "producthunt" in collector_configs and collector_configs["producthunt"].enabled:
            self.collectors["producthunt"] = ProductHuntCollector(collector_configs["producthunt"])

        if "news" in collector_configs and collector_configs["news"].enabled:
            self.collectors["news"] = NewsCollector(collector_configs["news"])

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
        # 독자 피드백(과대/과소)을 중요도 채점 few-shot 보정으로 주입.
        # 팩토리가 실행마다 호출되므로 최신 피드백이 매 처리 사이클에 반영된다.
        try:
            self.ai_processor.set_feedback_examples(self.feedback_repo.get_examples(20))
        except Exception:
            pass
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

    def follow_accounts_use_case(self) -> FollowAccountsUseCase:
        return FollowAccountsUseCase(
            post_repo=self.post_repo,
            follower=self.account_follower,
            config=self.config.follow,
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

    @staticmethod
    def _topics_from_items(items) -> list:
        """브리핑 항목(수신자 뷰) → 큐레이션 입력용 MergedTopic 목록."""
        from src.domain.services.ai_processor import MergedTopic
        from src.infrastructure.delivery.categories import VALID_BRIEFING_CATEGORIES

        topics = []
        for it in items:
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
        return topics

    async def send_curated_briefing(self, briefing) -> dict:
        """독자층별 큐레이션 생성 → Morning Commit HTML 렌더 → 그룹별 발송.

        저장된 브리핑은 전 수신자 요구치의 슈퍼셋(예: 코딩 10개)이고, 각
        수신자 뷰는 발송 직전 카테고리 한도로 트리밍한다. 같은 페르소나라도
        개인별 한도(category_limits)가 다르면 별도 뷰·큐레이션으로 발송한다.
        """
        from dataclasses import replace

        from src.domain.services.ai_processor import Curation
        from src.infrastructure.delivery.briefing_builder import trim_items_per_category
        from src.infrastructure.delivery.email_renderer import render_email_html

        ecfg = self.config.email
        bcfg = self.config.briefing

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
        results: dict = {}
        for persona, recipients in ecfg.briefing_targets().items():
            if not recipients:
                continue
            # 같은 한도 조합(뷰) 수신자끼리 묶어 큐레이션·렌더를 1회만 수행
            groups: dict[tuple, list] = {}
            for r in recipients:
                groups.setdefault(r.limits_key, []).append(r)
            for (mpc, limits), group in groups.items():
                addrs = [r.email for r in group]
                category_limits = dict(limits)
                view_items = trim_items_per_category(
                    briefing.items, mpc or bcfg.max_per_category, category_limits
                )
                view = replace(briefing, items=view_items, total_items=len(view_items))
                if persona and ecfg.curation_enabled:
                    curation = await self.ai_processor.generate_curation(
                        self._topics_from_items(view_items), persona
                    )
                else:
                    curation = Curation(title="", paragraphs=[], kick="", categories={})
                html = render_email_html(view, curation, "cid:logo", date_str)
                text = self.briefing_generator.render_text(view)
                ok = await self.notifier.send_html(
                    subject, html, text, addrs, ecfg.logo_path
                )
                label = persona or "default"
                if mpc or category_limits:
                    label = f"{label} (custom: {', '.join(addrs)})"
                results[label] = {"sent": ok, "recipients": len(addrs)}

        # 발송 성공 그룹이 하나라도 있으면 발송 완료 마킹 (대시보드 '이메일 전송됨' 표시)
        if any(r.get("sent") for r in results.values()) and briefing.id:
            try:
                briefing.email_sent = True
                briefing.email_sent_at = datetime.utcnow()
                await self.briefing_repo.update(briefing)
            except Exception as e:
                results["email_sent_mark_error"] = str(e)

        # 슬랙 게시 — 이메일과 독립적으로 시도 (실패해도 이메일 결과에 영향 없음)
        # 저장 브리핑은 개인화 슈퍼셋이므로 슬랙은 기본 한도 뷰로 트리밍해 게시
        if self.slack_notifier.is_configured:
            try:
                slack_items = trim_items_per_category(
                    briefing.items, bcfg.max_per_category
                )
                slack_view = replace(
                    briefing, items=slack_items, total_items=len(slack_items)
                )
                results["slack"] = await self.slack_notifier.send_briefing(slack_view, date_str)
            except Exception as e:
                results["slack"] = {"sent": False, "error": str(e)}

            # 슬랙 전용 소스(동아 연재 등) — AI를 안 태우므로 별도 섹션으로 붙인다.
            # 이메일 발송은 이미 끝난 뒤라 여기 항목은 메일에 들어가지 않는다.
            try:
                extras = await self.post_repo.get_slack_only_unbriefed()
                if extras:
                    posted = await self.slack_notifier.post_extra_section(extras)
                    if posted:
                        await self.post_repo.mark_briefed(
                            [p.id for p in extras], datetime.utcnow()
                        )
                    results["slack_extras"] = {"sent": posted, "items": len(extras)}
            except Exception as e:
                results["slack_extras"] = {"sent": False, "error": str(e)}
        return results

    async def run_slack_vote_winner(self) -> dict:
        """슬랙 투표 집계 → 순득표 1위(동률 전부)에 프롬프트 실행 → 채널 게시.

        헤더 메시지에 1위 항목들을 나열하고, 각 항목의 카드뉴스 타이틀·캡션
        결과물은 헤더의 스레드 댓글로 하나씩 단다.
        투표가 하나도 없으면 중요도 최고 1건으로 폴백하고 그 사실을 밝힌다.
        오늘 게시분이 아니면(서버 재시작 등으로 상태가 오래됨) 아무것도 하지 않는다.
        """
        from src.infrastructure.delivery.categories import CATEGORY_EMOJI, CATEGORY_KO
        from src.infrastructure.delivery.slack_sender import pick_winners, render_winner_prompt

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

        winners = pick_winners(tally["items"])
        if not winners:
            return {"run": False, "reason": "no_items"}
        no_votes = winners[0].get("no_votes", False)

        def _label(w: dict) -> str:
            cat = w.get("category") or "Other"
            return (
                f"{CATEGORY_EMOJI.get(cat, '🗂')} "
                f"[{CATEGORY_KO.get(cat, cat)}] {w.get('headline', '')}"
            )

        # ── 헤더: 1위 항목 전부 나열 ──
        if no_votes:
            title = "🏆 *오늘의 투표 1위* — 투표 없음, 중요도 기준 1건"
        elif len(winners) > 1:
            score = winners[0].get("up", 0) - winners[0].get("down", 0)
            title = f"🏆 *오늘의 투표 1위* — 순득표 {score} 동률 {len(winners)}건"
        else:
            title = f"🏆 *오늘의 투표 1위* — 👍 {winners[0].get('up', 0)}"
        lines = [title]
        for i, w in enumerate(winners, 1):
            vote = "" if no_votes else f" (👍{w.get('up', 0)}·👎{w.get('down', 0)})"
            lines.append(f"{i}. {_label(w)}{vote}")
        lines.append("_각 항목의 카드뉴스 타이틀·캡션 결과물은 이 스레드에 달려 있어요_")
        head = await self.slack_notifier.post_message("\n".join(lines))

        # ── 항목별 결과물을 스레드 댓글로 하나씩 ──
        results = []
        for w in winners:
            try:
                posts_lines = []
                for pid in (w.get("source_post_ids") or [])[:5]:
                    try:
                        p = self.post_repo.find_by_id(str(pid))
                    except Exception:
                        p = None
                    if p is not None and getattr(p, "content", None):
                        posts_lines.append(f"[{p.source}] {p.content[:1500]}")
                posts_text = "\n\n".join(posts_lines) if posts_lines else "(원본 게시물 없음)"

                prompt = render_winner_prompt(
                    scfg.winner_prompt, w, posts_text, tally["date_str"]
                )
                body = f"🎯 *{_label(w)}*\n\n"
                body += await self.run_freeform(prompt, websearch=scfg.winner_websearch)
                if scfg.caption_prompt.strip():
                    caption_prompt = render_winner_prompt(
                        scfg.caption_prompt, w, posts_text, tally["date_str"]
                    )
                    caption = await self.run_freeform(
                        caption_prompt, websearch=scfg.winner_websearch
                    )
                    body += f"\n\n──────────\n{caption}"
                await self.slack_notifier.post_message(body, thread_ts=head.get("ts"))
                results.append({"headline": w.get("headline"), "posted": True})
            except Exception as e:
                # 한 항목 실패가 나머지 결과물 게시를 막지 않게 한다
                results.append({"headline": w.get("headline"), "posted": False, "error": str(e)})

        return {
            "run": True,
            "no_votes": no_votes,
            "winners": results,
        }

    async def run_freeform(self, prompt: str, websearch: bool = True) -> str:
        """카드뉴스 등 파이프라인 밖 자유 프롬프트 — 백엔드 선택 + 폴백.

        과거엔 claude_processor.run_freeform 을 직접 불러 **폴백이 없었다**.
        Claude CLI가 죽으면(한도 소진·npm 스텁 등) 카드뉴스만 통째로 실패했다
        (2026-07-27, 08-12, 08-20 실사고). 이제 배치 경로와 같은 안전망을 둔다.
        codex_only 모드에서는 Claude를 아예 건너뛴다.
        """
        codex = self.ai_processor._fallback
        if getattr(self.config.processing, "codex_only", False):
            return await codex.run_freeform(prompt, websearch=websearch)
        try:
            return await self.claude_processor.run_freeform(prompt, websearch=websearch)
        except Exception as e:
            logger.warning(f"Claude freeform 실패, 폴백(Codex) 실행: {e}")
            return await codex.run_freeform(prompt, websearch=websearch)

    async def run_slack_mention(self, text: str, channel: str, thread_ts: str) -> None:
        """@멘션으로 붙여넣은 게시물에 winner_prompt(+캡션)를 돌려 스레드로 답장.

        주말 등 투표 집계가 없는 날, 사용자가 직접 고른 게시물을 처리하는 경로.
        처리에 1~3분 걸리므로 즉시 안내 답장을 먼저 남긴다.
        """
        from src.infrastructure.delivery.slack_sender import parse_pasted_post, render_winner_prompt

        scfg = self.config.slack
        if not scfg.winner_prompt.strip():
            await self.slack_notifier.post_message(
                "winner_prompt가 설정돼 있지 않아 처리할 수 없어요 (config/settings.yaml).",
                thread_ts=thread_ts, channel=channel,
            )
            return
        try:
            item = parse_pasted_post(text)
            if not item.get("headline"):
                await self.slack_notifier.post_message(
                    "게시물 텍스트를 함께 붙여넣어 주세요! (멘션 뒤에 헤드라인·요약을 그대로 복사)",
                    thread_ts=thread_ts, channel=channel,
                )
                return

            await self.slack_notifier.post_message(
                f"⏳ 받았습니다 — \"{item['headline'][:50]}\" 카드뉴스 타이틀·캡션 생성 중이에요 (1~3분)",
                thread_ts=thread_ts, channel=channel,
            )

            posts_text = text  # 붙여넣은 원문 전체를 원본 게시물로 전달
            date_str = datetime.now(ZoneInfo(self.config.timezone)).strftime("%Y. %m. %d")

            body = await self.run_freeform(
                render_winner_prompt(scfg.winner_prompt, item, posts_text, date_str),
                websearch=scfg.winner_websearch,
            )
            if scfg.caption_prompt.strip():
                caption = await self.run_freeform(
                    render_winner_prompt(scfg.caption_prompt, item, posts_text, date_str),
                    websearch=scfg.winner_websearch,
                )
                body += f"\n\n──────────\n{caption}"

            await self.slack_notifier.post_message(body, thread_ts=thread_ts, channel=channel)
        except Exception as e:
            try:
                await self.slack_notifier.post_message(
                    f"⚠️ 생성 실패: {str(e)[:200]}", thread_ts=thread_ts, channel=channel,
                )
            except Exception:
                pass
