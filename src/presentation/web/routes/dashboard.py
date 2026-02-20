"""대시보드 HTML 페이지 라우트."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])


def _get_container(request: Request):
    return request.app.state.container


def _get_templates(request: Request):
    return request.app.state.templates


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """메인 대시보드 — 최신 브리핑 + 최근 게시물 + 시스템 상태."""
    c = _get_container(request)
    templates = _get_templates(request)

    latest_briefing = await c.briefing_repo.get_latest()
    recent_posts = await c.post_repo.search(limit=30)
    recent_runs = await c.run_repo.get_recent(limit=10)

    now = datetime.utcnow()
    source_counts = await c.post_repo.count_by_source(
        now - timedelta(hours=24), now
    )

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "briefing": latest_briefing,
            "posts": recent_posts,
            "runs": recent_runs,
            "source_counts": source_counts,
        },
    )


@router.get("/briefings", response_class=HTMLResponse)
async def briefing_archive(request: Request, page: int = 1):
    """브리핑 아카이브."""
    c = _get_container(request)
    templates = _get_templates(request)

    per_page = 20
    briefings = await c.briefing_repo.get_all(limit=per_page, offset=(page - 1) * per_page)

    return templates.TemplateResponse(
        "archive.html",
        {"request": request, "briefings": briefings, "page": page},
    )


@router.get("/briefings/{briefing_id}", response_class=HTMLResponse)
async def briefing_detail(request: Request, briefing_id: int):
    """개별 브리핑 상세."""
    c = _get_container(request)
    templates = _get_templates(request)

    briefing = await c.briefing_repo.get_by_id(briefing_id)

    return templates.TemplateResponse(
        "briefing_detail.html",
        {"request": request, "briefing": briefing},
    )


@router.get("/posts", response_class=HTMLResponse)
async def posts_page(
    request: Request,
    source: str | None = None,
    category: str | None = None,
    q: str | None = None,
    page: int = 1,
):
    """게시물 탐색 (검색 + 필터)."""
    c = _get_container(request)
    templates = _get_templates(request)

    per_page = 50
    posts = await c.post_repo.search(
        query=q, source=source, category=category, limit=per_page, offset=(page - 1) * per_page
    )
    categories = await c.category_repo.get_all()

    return templates.TemplateResponse(
        "posts.html",
        {
            "request": request,
            "posts": posts,
            "categories": categories,
            "current_source": source,
            "current_category": category,
            "current_query": q,
            "page": page,
        },
    )


@router.get("/status", response_class=HTMLResponse)
async def system_status(request: Request):
    """시스템 상태 페이지."""
    c = _get_container(request)
    templates = _get_templates(request)

    runs = await c.run_repo.get_recent(limit=30)

    # 소스별 마지막 성공 수집
    last_success = {}
    for source in c.collectors:
        last_success[source] = await c.run_repo.get_last_successful(source)

    return templates.TemplateResponse(
        "status.html",
        {
            "request": request,
            "runs": runs,
            "last_success": last_success,
            "collectors": list(c.collectors.keys()),
        },
    )
