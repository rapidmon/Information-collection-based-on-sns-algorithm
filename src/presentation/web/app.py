"""FastAPI 웹 애플리케이션 팩토리."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.infrastructure.config.container import Container
from src.presentation.web.routes import api, dashboard

_STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATE_DIR = Path(__file__).parent / "templates"


def create_app(container: Container) -> FastAPI:
    app = FastAPI(title="SNS Tech Briefing", version="0.1.0")

    # 컨테이너를 앱 state에 저장
    app.state.container = container
    app.state.templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

    # 정적 파일
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # 라우터 등록
    app.include_router(dashboard.router)
    app.include_router(api.router, prefix="/api")

    return app
