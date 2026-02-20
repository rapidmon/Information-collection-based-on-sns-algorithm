"""유즈케이스: 게시물 수집.

수집기(Collector)를 실행하여 게시물을 가져오고 저장한다.
도메인 인터페이스에만 의존하며, 구체 구현은 DI로 주입받는다.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime

from src.domain.entities import CollectionRun, Post
from src.domain.exceptions import CollectionError, SessionExpiredError
from src.domain.repositories.collection_run_repository import CollectionRunRepository
from src.domain.repositories.post_repository import PostRepository
from src.domain.services.collector import Collector
from src.domain.value_objects.content_hash import compute_content_hash

logger = logging.getLogger(__name__)


class CollectPostsUseCase:
    """한 소스에 대해 수집 사이클을 실행하는 유즈케이스."""

    def __init__(
        self,
        collector: Collector,
        post_repo: PostRepository,
        run_repo: CollectionRunRepository,
        max_retries: int = 3,
    ):
        self._collector = collector
        self._post_repo = post_repo
        self._run_repo = run_repo
        self._max_retries = max_retries

    async def execute(self) -> CollectionRun:
        source = self._collector.source_name
        run = CollectionRun(source=source)
        run = await self._run_repo.save(run)

        try:
            # 세션 유효성 확인
            if not await self._collector.is_session_valid():
                raise SessionExpiredError(source)

            # 재시도 로직으로 수집
            posts = await self._collect_with_retry()

            # 콘텐츠 해시 계산 및 저장
            for post in posts:
                post.content_hash = compute_content_hash(post.content_text)

            saved_count = await self._post_repo.save_many(posts)

            run.status = "success"
            run.posts_collected = saved_count
            run.completed_at = datetime.utcnow()
            logger.info(f"[{source}] 수집 완료: {saved_count}건 저장 (전체 {len(posts)}건)")

        except SessionExpiredError:
            run.status = "failed"
            run.error_message = f"{source} 세션 만료"
            run.completed_at = datetime.utcnow()
            logger.error(f"[{source}] 세션 만료 — 수동 재로그인 필요")
            raise

        except Exception as e:
            run.status = "failed"
            run.error_message = str(e)[:500]
            run.completed_at = datetime.utcnow()
            logger.error(f"[{source}] 수집 실패: {e}")

        finally:
            await self._run_repo.update(run)

        return run

    async def _collect_with_retry(self) -> list[Post]:
        for attempt in range(self._max_retries):
            try:
                return await self._collector.collect()
            except SessionExpiredError:
                raise
            except Exception as e:
                if attempt == self._max_retries - 1:
                    raise CollectionError(
                        f"{self._collector.source_name}: {self._max_retries}회 시도 실패"
                    ) from e
                wait = (2**attempt) + random.uniform(0, 1)
                logger.warning(
                    f"[{self._collector.source_name}] 시도 {attempt + 1} 실패: {e}. "
                    f"{wait:.1f}초 후 재시도"
                )
                await asyncio.sleep(wait)
        return []  # unreachable
