"""SNS Tech Briefing System — 엔트리포인트.

1. 설정 로드 (.env + config/settings.yaml)
2. Firebase Firestore 초기화
3. 의존성 컨테이너 조립
4. 카테고리 시드 데이터
5. 스케줄러 시작
6. 웹 서버 시작

SNS 수집은 CDP를 통해 사용자의 Chrome 브라우저에 연결하여 수행.
Chrome을 --remote-debugging-port=9222 로 실행한 상태에서 사용.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import uvicorn

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent))

from src.domain.entities import Category
from src.infrastructure.config.container import Container
from src.infrastructure.config.settings import AppConfig, Settings, load_app_config
from src.infrastructure.database.firebase_client import get_firestore_client, init_firebase
from src.application.use_cases.scheduler import Orchestrator
from src.presentation.web.app import create_app


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/app.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


async def seed_categories(container: Container, config: AppConfig) -> None:
    """YAML에 정의된 카테고리를 Firestore에 시드."""
    for cat_cfg in config.categories:
        await container.category_repo.upsert(
            Category(name=cat_cfg.name, name_ko=cat_cfg.name_ko, color=cat_cfg.color)
        )
    logger.info(f"카테고리 {len(config.categories)}개 시드 완료")


async def run_server(
    settings: Settings,
    config: AppConfig,
    no_scheduler: bool = False,
) -> None:
    """메인 서버 실행."""
    Path("logs").mkdir(exist_ok=True)

    # Firebase 초기화
    init_firebase(
        credential_path=settings.firebase_credential_path,
        project_id=settings.firebase_project_id or None,
    )
    db = get_firestore_client()

    # 의존성 컨테이너 조립
    container = Container(
        settings=settings,
        app_config=config,
        firestore_db=db,
    )

    # 카테고리 시드
    await seed_categories(container, config)

    # 스케줄러
    orchestrator = None
    if not no_scheduler:
        orchestrator = Orchestrator(container)
        orchestrator.setup_jobs()
        orchestrator.start()

    # 웹 서버
    app = create_app(container)
    server_config = uvicorn.Config(
        app,
        host=config.web.host,
        port=config.web.port,
        log_level="info",
    )
    server = uvicorn.Server(server_config)

    logger.info(
        f"서버 시작: http://{config.web.host}:{config.web.port} "
        f"(스케줄러: {'ON' if orchestrator else 'OFF'})"
    )

    try:
        await server.serve()
    finally:
        if orchestrator:
            orchestrator.stop()


async def run_collect_now(settings: Settings, config: AppConfig, sources: list[str]) -> None:
    """지정한 소스를 즉시 수집 실행."""
    Path("logs").mkdir(exist_ok=True)

    init_firebase(
        credential_path=settings.firebase_credential_path,
        project_id=settings.firebase_project_id or None,
    )
    db = get_firestore_client()

    container = Container(
        settings=settings,
        app_config=config,
        firestore_db=db,
    )

    for source in sources:
        if source not in container.collectors:
            print(f"'{source}' 수집기가 등록되지 않았습니다.")
            continue

        print(f"[{source}] 수집 시작...")
        try:
            uc = container.collect_posts_use_case(source)
            run = await uc.execute()
            print(f"[{source}] 완료: {run.posts_collected}건 수집")
        except Exception as e:
            print(f"[{source}] 오류: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="SNS Tech Briefing System")
    subparsers = parser.add_subparsers(dest="command", help="실행 명령")

    # serve 명령
    serve_parser = subparsers.add_parser("serve", help="서버 시작 (수집 + 웹)")
    serve_parser.add_argument("--no-scheduler", action="store_true", help="스케줄러 없이 시작 (웹만)")

    # collect-now 명령
    collect_parser = subparsers.add_parser("collect-now", help="즉시 수집 실행")
    collect_parser.add_argument(
        "sources", nargs="*", default=["twitter", "threads", "linkedin", "dcinside"],
        help="수집할 소스 (기본: 전부)",
    )

    args = parser.parse_args()

    settings = Settings()
    config = load_app_config()

    if args.command == "serve":
        asyncio.run(run_server(settings, config, args.no_scheduler))
    elif args.command == "collect-now":
        asyncio.run(run_collect_now(settings, config, args.sources))
    else:
        parser.print_help()
        print("\n사용 방법:")
        print("  1. Chrome을 디버그 모드로 실행:")
        print('     "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222')
        print("  2. Chrome에서 Twitter, Threads, LinkedIn에 로그인")
        print("  3. 시스템 시작:")
        print("     python main.py serve                # 전체 시작")
        print("     python main.py collect-now           # 전체 즉시 수집")
        print("     python main.py collect-now twitter    # 트위터만 즉시 수집")


if __name__ == "__main__":
    main()
