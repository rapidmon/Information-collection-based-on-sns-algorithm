"""스케줄러 오케스트레이션.

APScheduler를 사용해 수집, AI 처리, 브리핑 생성/전달을 자동화한다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.domain.exceptions import SessionExpiredError
from src.infrastructure.config.container import Container

logger = logging.getLogger(__name__)


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
            start_time = now + timedelta(minutes=stagger_minutes)
            self.scheduler.add_job(
                self._run_collection,
                trigger=IntervalTrigger(minutes=cfg.interval_minutes),
                args=[source],
                id=f"collect_{source}",
                name=f"Collect {source}",
                max_instances=1,
                misfire_grace_time=300,
                next_run_time=start_time,
            )
            logger.info(
                f"수집 작업 등록: {source} "
                f"(+{stagger_minutes}분 후 시작, 매 {cfg.interval_minutes}분)"
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
        )
        logger.info(f"일일 브리핑 등록: {daily_time}")

        # ─── 헬스체크 (5분마다) ───
        self.scheduler.add_job(
            self._health_check,
            trigger=IntervalTrigger(minutes=5),
            id="health_check",
            name="Health Check",
        )

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

    async def _run_daily_briefing(self) -> None:
        logger.info("[scheduler] 일일 브리핑 생성 시작")
        try:
            now = datetime.now(tz=self._tz)
            period_end = now
            period_start = now - timedelta(hours=24)

            gen_uc = self._c.generate_briefing_use_case()
            briefing = await gen_uc.execute(period_start, period_end)

            if briefing.total_items > 0:
                send_uc = self._c.send_briefing_use_case()
                await send_uc.execute(briefing)

            logger.info(
                f"[scheduler] 일일 브리핑 완료: {briefing.title} ({briefing.total_items}건)"
            )
        except Exception as e:
            logger.error(f"[scheduler] 일일 브리핑 오류: {e}")

    async def _health_check(self) -> None:
        """각 소스의 연속 실패 횟수를 확인하고 임계치 초과 시 알림."""
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
