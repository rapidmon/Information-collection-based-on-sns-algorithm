"""스케줄러 오케스트레이션.

APScheduler를 사용해 수집, AI 처리, 브리핑 생성/전달을 자동화한다.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.domain.exceptions import SessionExpiredError
from src.infrastructure.config.container import Container

logger = logging.getLogger(__name__)

# 토큰 만료 며칠 전부터 매일 DM 알림을 보낼지
TOKEN_ALERT_DAYS_BEFORE = 7


def token_expiry_message(expires_at: str, today: date) -> str | None:
    """Claude OAuth 토큰 만료 알림 문구를 만든다. 알림 불필요 시 None.

    D-7부터 매일, 만료 후에도 갱신 전까지 계속 알린다 (한 번 놓치면 끝인
    일회성 알림 대신 — 갱신해서 .env의 만료일을 바꾸면 자연히 멈춘다).
    날짜 형식이 잘못됐으면 None 대신 예외를 던져 호출부가 로그를 남기게 한다.
    """
    expiry = date.fromisoformat(expires_at.strip())
    days_left = (expiry - today).days
    if days_left > TOKEN_ALERT_DAYS_BEFORE:
        return None
    renew_guide = (
        "갱신: 터미널에서 `claude setup-token` 실행 → 새 토큰과 만료일을 "
        ".env의 CLAUDE_CODE_OAUTH_TOKEN / CLAUDE_CODE_OAUTH_TOKEN_EXPIRES_AT에 "
        "반영 → 서버 재시작"
    )
    if days_left < 0:
        return (
            f"🚨 *Claude 토큰이 {-days_left}일 전에 만료됐습니다* ({expires_at}). "
            f"카드뉴스 생성이 실패하고 있을 수 있어요.\n{renew_guide}"
        )
    when = "오늘" if days_left == 0 else f"D-{days_left} ({expires_at})"
    return f"⏰ *Claude 토큰 만료 {when}* — 미리 갱신해 주세요.\n{renew_guide}"


class Orchestrator:
    """스케줄 기반 작업 오케스트레이터."""

    def __init__(self, container: Container):
        self._c = container
        self._tz = ZoneInfo(container.config.timezone)
        self.scheduler = AsyncIOScheduler(timezone=self._tz)

    def setup_jobs(self) -> None:
        """모든 정기 작업을 등록."""
        configs = self._c.config.collectors

        # ─── 수집 작업 (서버 시작 시 순차 실행 후 interval 반복) ───
        # CDP 수집기 동시 실행 방지: 2분 간격으로 stagger
        now = datetime.now(tz=self._tz)
        stagger_minutes = 0
        for source, cfg in configs.items():
            if not cfg.enabled or source not in self._c.collectors:
                continue
            trig = self._collection_trigger(cfg.interval_minutes, stagger_minutes)
            # 시작 직후 '너무 이른' 첫 슬롯은 건너뛴다: 한 인터벌 뒤부터의 정렬 슬롯을 첫 실행으로.
            # (예: 13:07 시작·10분 주기 → 13:10 건너뛰고 13:20부터)
            first_after = now + timedelta(minutes=cfg.interval_minutes)
            first_run = trig.get_next_fire_time(None, first_after)
            self.scheduler.add_job(
                self._run_collection,
                trigger=trig,
                args=[source],
                id=f"collect_{source}",
                name=f"Collect {source}",
                max_instances=1,
                misfire_grace_time=1800,  # AI 처리로 루프가 막혀도 스킵 안 되게 넉넉히
                next_run_time=first_run,  # 정렬된 첫 슬롯(이른 슬롯은 건너뜀)
            )

            # 서버 시작 직후 1회 즉시 수집 (스태거 적용) — 정렬 스케줄과 별개의 일회성 잡
            startup_time = now + timedelta(minutes=stagger_minutes)
            self.scheduler.add_job(
                self._run_collection,
                trigger=DateTrigger(run_date=startup_time, timezone=self._tz),
                args=[source],
                id=f"collect_{source}_startup",
                name=f"Collect {source} (startup)",
                max_instances=1,
                misfire_grace_time=300,
            )

            logger.info(
                f"수집 작업 등록: {source} (매 {cfg.interval_minutes}분, KST 정렬 / "
                f"시작 직후 1회 {startup_time.strftime('%H:%M')}, 정렬 첫 슬롯 "
                f"{first_run.strftime('%H:%M') if first_run else '?'})"
            )
            stagger_minutes += 2

        # ─── AI 처리 (설정 간격마다, 시작 시 5분 후 첫 실행 — 수집 완료 대기) ───
        processing_interval = self._c.config.processing.processing_interval_minutes
        self.scheduler.add_job(
            self._run_processing,
            trigger=IntervalTrigger(minutes=processing_interval),
            id="process_posts",
            name="AI Process Posts",
            max_instances=1,
            misfire_grace_time=300,
            next_run_time=now + timedelta(minutes=5),
        )
        logger.info(f"AI 처리 작업 등록: 매 {processing_interval}분")

        # ─── 일일 브리핑 ───
        daily_time = self._c.config.briefing.daily_time
        hour, minute = map(int, daily_time.split(":"))
        self.scheduler.add_job(
            self._run_daily_briefing,
            trigger=CronTrigger(hour=hour, minute=minute),
            id="daily_briefing",
            name="Daily Briefing",
            max_instances=1,
            misfire_grace_time=600,
        )
        logger.info(f"일일 브리핑 등록: {daily_time}")

        # ─── 슬랙 투표 마감 → 1위 항목 심층 글 게시 ───
        scfg = self._c.config.slack
        if scfg.enabled and scfg.winner_prompt.strip():
            v_hour, v_minute = map(int, scfg.vote_close_time.split(":"))
            self.scheduler.add_job(
                self._run_slack_vote_winner,
                trigger=CronTrigger(
                    hour=v_hour, minute=v_minute, day_of_week=scfg.vote_days
                ),
                id="slack_vote_winner",
                name="Slack Vote Winner",
                max_instances=1,
                misfire_grace_time=600,
            )
            logger.info(f"슬랙 투표 집계 등록: {scfg.vote_close_time} ({scfg.vote_days})")

        # ─── 헬스체크 (5분마다) ───
        self.scheduler.add_job(
            self._health_check,
            trigger=IntervalTrigger(minutes=5),
            id="health_check",
            name="Health Check",
        )

        # ─── 자동 데이터 정리 (매일 자정, 1개월 이상 된 데이터 삭제) ───
        self.scheduler.add_job(
            self._cleanup_old_posts,
            trigger=CronTrigger(hour=0, minute=0),  # 매일 자정
            id="cleanup_posts",
            name="Cleanup Old Posts",
            max_instances=1,
        )
        logger.info("자동 데이터 정리 등록: 매일 자정 (1개월 이상 데이터 삭제)")

        # ─── Claude 토큰 만료 알림 (매일 09:00, D-7부터 슬랙 DM) ───
        expires_at = self._c.settings.claude_code_oauth_token_expires_at.strip()
        if expires_at:
            try:
                date.fromisoformat(expires_at)  # 형식 검증만 (판정은 실행 시점 날짜로)
            except ValueError:
                logger.warning(
                    f"CLAUDE_CODE_OAUTH_TOKEN_EXPIRES_AT 형식 오류('{expires_at}') — "
                    "YYYY-MM-DD로 적어야 만료 알림이 동작합니다"
                )
            else:
                self.scheduler.add_job(
                    self._check_token_expiry,
                    trigger=CronTrigger(hour=9, minute=0),
                    id="token_expiry_alert",
                    name="Claude Token Expiry Alert",
                    max_instances=1,
                    misfire_grace_time=3600,
                )
                logger.info(f"Claude 토큰 만료 알림 등록: 매일 09:00 (만료일 {expires_at})")

    def _collection_trigger(self, interval_minutes: int, offset: int):
        """수집 트리거를 KST 벽시계 정각에 정렬해서 만든다 (스태거 오프셋 유지).

        - interval이 60의 약수(10·30 등): 매시 [offset, offset+interval, ...]분에 실행
          (예: 10분·offset 2 → :02,:12,:22,:32,:42,:52)
        - interval이 60의 배수(60·120 등): **매 N시간 정각(:00)** (예: 120 → 짝수시 0·2·4…시 :00)
          (이 소스들은 HTTP 수집이라 CDP 스태거가 필요 없어 정각으로 정렬)
        - 그 외(60의 약수·배수 아님): 정렬 불가 → 기존 인터벌 방식 폴백
        """
        tz = self._tz
        interval = interval_minutes if interval_minutes and interval_minutes > 0 else 10

        if interval < 60 and 60 % interval == 0:
            base = offset % interval
            minutes = sorted({(base + k * interval) % 60 for k in range(60 // interval)})
            return CronTrigger(minute=",".join(str(m) for m in minutes), timezone=tz)

        if interval % 60 == 0:
            hours = interval // 60
            hour_spec = "*" if hours == 1 else f"*/{hours}"
            return CronTrigger(minute=0, hour=hour_spec, timezone=tz)  # 정각(:00)

        # 60의 약수/배수가 아니면 정각 정렬이 불가능 → 인터벌 유지
        logger.warning(f"정각 정렬 불가(interval={interval}) — 인터벌 방식 유지")
        return IntervalTrigger(minutes=interval)

    def start(self) -> None:
        self.scheduler.start()
        logger.info("스케줄러 시작됨")

    def stop(self) -> None:
        self.scheduler.shutdown(wait=False)
        logger.info("스케줄러 종료됨")

    # ─── 작업 실행 함수 ───

    async def _run_collection(self, source: str) -> None:
        logger.info(f"[scheduler] 수집 시작: {source}")
        try:
            uc = self._c.collect_posts_use_case(source)
            run = await uc.execute()
            logger.info(
                f"[scheduler] 수집 완료: {source} — "
                f"{run.status}, {run.posts_collected}건"
            )
        except SessionExpiredError as e:
            logger.error(f"[scheduler] {e}")
        except Exception as e:
            logger.error(f"[scheduler] 수집 오류 {source}: {e}")

    async def _run_processing(self) -> None:
        logger.info("[scheduler] AI 처리 시작")
        try:
            uc = self._c.process_posts_use_case()
            min_posts = self._c.config.processing.min_posts_to_process
            stats = await uc.execute(min_posts_threshold=min_posts)
            logger.info(f"[scheduler] AI 처리 완료: {stats}")
        except Exception as e:
            logger.error(f"[scheduler] AI 처리 오류: {e}")

        # AI 처리 후 자동 좋아요 (관련+중요 게시물에만)
        try:
            like_uc = self._c.like_posts_use_case()
            like_stats = await like_uc.execute()
            if like_stats:
                logger.info(f"[scheduler] 자동 좋아요: {like_stats}")
        except Exception as e:
            logger.error(f"[scheduler] 자동 좋아요 오류: {e}")

        # 좋아요가 누적된 계정 자동 팔로우 (좋아요 직후 — 방금 늘어난 집계를 바로 반영)
        try:
            follow_uc = self._c.follow_accounts_use_case()
            follow_stats = await follow_uc.execute()
            if follow_stats:
                logger.info(f"[scheduler] 자동 팔로우: {follow_stats}")
        except Exception as e:
            logger.error(f"[scheduler] 자동 팔로우 오류: {e}")

    async def _run_daily_briefing(self) -> None:
        logger.info("[scheduler] 일일 브리핑 생성 시작")
        try:
            now = datetime.now(tz=self._tz)
            period_end = now
            period_start = now - timedelta(hours=24)

            # 브리핑 직전에 미처리분을 먼저 AI 처리 (밤사이 수집된 최신 뉴스가 누락되지 않도록)
            try:
                proc_uc = self._c.process_posts_use_case()
                await proc_uc.execute()
                logger.info("[scheduler] 브리핑 전 AI 처리 완료")
            except Exception as e:
                logger.error(f"[scheduler] 브리핑 전 AI 처리 오류: {e}")

            gen_uc = self._c.generate_briefing_use_case()
            briefing = await gen_uc.execute(period_start, period_end)

            if briefing.total_items > 0:
                send_results = await self._c.send_curated_briefing(briefing)
                logger.info(f"[scheduler] 브리핑 발송: {send_results}")

            logger.info(
                f"[scheduler] 일일 브리핑 완료: {briefing.title} ({briefing.total_items}건)"
            )
        except Exception as e:
            logger.error(f"[scheduler] 일일 브리핑 오류: {e}")

    async def _run_slack_vote_winner(self) -> None:
        logger.info("[scheduler] 슬랙 투표 집계 시작")
        try:
            result = await self._c.run_slack_vote_winner()
            logger.info(f"[scheduler] 슬랙 투표 1위 처리: {result}")
        except Exception as e:
            logger.error(f"[scheduler] 슬랙 투표 처리 오류: {e}")

    async def _health_check(self) -> None:
        """각 소스의 연속 실패 횟수를 확인하고 임계치 초과 시 알림. RSS도 함께 로깅."""
        self._log_memory_usage()
        for source in self._c.collectors:
            try:
                failures = await self._c.run_repo.count_consecutive_failures(source)
                if failures >= 3:
                    await self._c.notifier.send_alert(
                        f"{source} 수집 연속 실패",
                        f"{source} 수집기가 {failures}회 연속 실패했습니다. 확인이 필요합니다.",
                    )
            except Exception:
                pass

    @staticmethod
    def _log_memory_usage() -> None:
        """Python 프로세스와 모든 chrome.exe 프로세스의 RSS를 로깅."""
        try:
            import psutil

            py_rss = psutil.Process().memory_info().rss
            chrome_rss = 0
            chrome_count = 0
            for p in psutil.process_iter(["name", "memory_info"]):
                try:
                    if (p.info.get("name") or "").lower() == "chrome.exe":
                        mi = p.info.get("memory_info")
                        if mi:
                            chrome_rss += mi.rss
                            chrome_count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            logger.info(
                f"[health] RSS python={py_rss / 1024 / 1024:.0f}MB, "
                f"chrome={chrome_rss / 1024 / 1024:.0f}MB ({chrome_count}개 프로세스)"
            )
        except Exception as e:
            logger.warning(f"[health] 메모리 측정 실패: {e}")

    async def _cleanup_old_posts(self) -> None:
        """포스트 자동 삭제 (로컬 SQLite 정리).

        - 필터 탈락(비관련) 글: 3일간 재수집 안 됐으면 삭제
          (수집 컷오프 max_age_days=2를 넘겨야 삭제→재수집→재필터링 루프가 없다)
        - 그 외: 30일 경과 시 삭제
        """
        logger.info("[scheduler] 데이터 정리 시작 (비관련 3일 / 전체 30일)")
        try:
            irrelevant_deleted = self._c.post_repo.delete_irrelevant_older_than(days=3)
            if irrelevant_deleted:
                logger.info(f"[scheduler] 필터 탈락 게시물 정리: {irrelevant_deleted}건 삭제")
            deleted_count = self._c.post_repo.delete_older_than(days=30)
            storage_info = self._c.post_repo.get_storage_info()
            logger.info(
                f"[scheduler] 데이터 정리 완료: {deleted_count}건 삭제, "
                f"남은 데이터: {storage_info['document_count']}건 "
                f"({storage_info['size_mb']}MB)"
            )
        except Exception as e:
            logger.error(f"[scheduler] 데이터 정리 오류: {e}")

    async def _check_token_expiry(self) -> None:
        """Claude OAuth 토큰 만료 임박(D-7~) 시 슬랙 DM 알림."""
        expires_at = self._c.settings.claude_code_oauth_token_expires_at.strip()
        try:
            msg = token_expiry_message(expires_at, datetime.now(tz=self._tz).date())
        except ValueError as e:
            logger.warning(f"[scheduler] 토큰 만료일 파싱 실패('{expires_at}'): {e}")
            return
        if not msg:
            return
        logger.info(f"[scheduler] Claude 토큰 만료 임박 — DM 알림: {expires_at}")
        try:
            result = await self._c.slack_notifier.send_dm(msg)
            if not result.get("sent"):
                logger.warning(
                    f"[scheduler] 토큰 만료 DM 미발송({result.get('reason')}) — "
                    "slack.alert_user_id / SLACK_BOT_TOKEN 확인 필요"
                )
        except Exception as e:
            logger.error(f"[scheduler] 토큰 만료 DM 발송 오류: {e}")
