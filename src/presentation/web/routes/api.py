"""REST API 라우트 — HTMX 파셜 렌더링 및 수동 트리거."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["api"])


def _get_container(request: Request):
    return request.app.state.container


@router.get("/posts/search")
async def search_posts(
    request: Request,
    q: str | None = None,
    source: str | None = None,
    category: str | None = None,
    limit: int = 30,
    offset: int = 0,
):
    """게시물 검색 API."""
    c = _get_container(request)
    posts = await c.post_repo.search(
        query=q, source=source, category=category, limit=limit, offset=offset
    )
    return [
        {
            "id": p.id,
            "source": p.source,
            "author": p.author,
            "content_text": p.content_text[:300],
            "summary": p.summary,
            "url": p.url,
            "importance_score": p.importance_score,
            "category_names": p.category_names,
            "keywords": p.keywords,
            "collected_at": p.collected_at.isoformat() if p.collected_at else None,
        }
        for p in posts
    ]


@router.post("/collect/trigger/{source}")
async def trigger_collection(request: Request, source: str):
    """수동 수집 트리거."""
    c = _get_container(request)
    try:
        uc = c.collect_posts_use_case(source)
        run = await uc.execute()
        return {
            "status": run.status,
            "posts_collected": run.posts_collected,
            "error": run.error_message,
        }
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/process/trigger")
async def trigger_processing(request: Request):
    """수동 AI 처리 트리거."""
    c = _get_container(request)
    try:
        uc = c.process_posts_use_case()
        stats = await uc.execute()
        return stats
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/briefing/generate")
async def trigger_briefing(request: Request):
    """수동 브리핑 생성 + 이메일 발송 트리거."""
    c = _get_container(request)
    try:
        tz = ZoneInfo(c.config.timezone)
        now = datetime.now(tz=tz)
        gen_uc = c.generate_briefing_use_case()
        briefing = await gen_uc.execute(now - timedelta(hours=24), now)

        email_sent = False
        if briefing.total_items > 0:
            send_uc = c.send_briefing_use_case()
            email_sent = await send_uc.execute(briefing)

        return {
            "id": briefing.id,
            "title": briefing.title,
            "total_items": briefing.total_items,
            "email_sent": email_sent,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/briefings/latest")
async def latest_briefing(request: Request):
    """최신 브리핑 JSON."""
    c = _get_container(request)
    b = await c.briefing_repo.get_latest()
    if not b:
        return JSONResponse(status_code=404, content={"error": "브리핑 없음"})
    return {
        "id": b.id,
        "title": b.title,
        "generated_at": b.generated_at.isoformat() if b.generated_at else None,
        "total_items": b.total_items,
        "content_html": b.content_html,
    }


@router.get("/stats")
async def stats(request: Request):
    """수집 통계."""
    c = _get_container(request)
    now = datetime.utcnow()
    counts = await c.post_repo.count_by_source(now - timedelta(hours=24), now)
    runs = await c.run_repo.get_recent(limit=10)
    return {
        "source_counts_24h": counts,
        "recent_runs": [
            {
                "source": r.source,
                "status": r.status,
                "posts_collected": r.posts_collected,
                "started_at": r.started_at.isoformat() if r.started_at else None,
            }
            for r in runs
        ],
    }


@router.get("/briefings")
async def list_briefings(request: Request, limit: int = 20, offset: int = 0):
    """브리핑 목록 API."""
    c = _get_container(request)
    try:
        briefings = await c.briefing_repo.get_all(limit=limit * 2, offset=offset)
        return {
            "briefings": [
                {
                    "id": b.id,
                    "title": b.title,
                    "generated_at": b.generated_at.isoformat() if b.generated_at else None,
                    "total_items": b.total_items,
                }
                for b in briefings[:limit]
            ],
            "has_more": len(briefings) > limit,
        }
    except Exception:
        return JSONResponse(status_code=500, content={"error": "브리핑 조회 실패"})


@router.get("/briefings/{briefing_id}")
async def get_briefing(request: Request, briefing_id: str):
    """단일 브리핑 API."""
    c = _get_container(request)
    try:
        b = await c.briefing_repo.get_by_id(briefing_id)
        if not b:
            return JSONResponse(status_code=404, content={"error": "브리핑 없음"})
        return {
            "id": b.id,
            "title": b.title,
            "generated_at": b.generated_at.isoformat() if b.generated_at else None,
            "content_html": b.content_html,
            "total_items": b.total_items,
        }
    except Exception:
        return JSONResponse(status_code=500, content={"error": "브리핑 조회 실패"})


@router.get("/categories")
async def list_categories(request: Request):
    """카테고리 목록 API."""
    c = _get_container(request)
    try:
        categories = await c.category_repo.get_all()
        if not categories:
            # Fallback
            categories = [
                {"name": "AI", "name_ko": "AI"},
                {"name": "Semiconductor", "name_ko": "반도체"},
                {"name": "Cloud", "name_ko": "클라우드"},
                {"name": "BigTech", "name_ko": "빅테크"},
                {"name": "Startup", "name_ko": "스타트업"},
                {"name": "Regulation", "name_ko": "규제"},
                {"name": "Coding", "name_ko": "코딩"},
            ]
        return categories
    except Exception:
        return JSONResponse(status_code=500, content={"error": "카테고리 조회 실패"})
