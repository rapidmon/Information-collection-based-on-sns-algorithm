"""REST API 라우트 — HTMX 파셜 렌더링 및 수동 트리거."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["api"])

# ─── 간단한 인메모리 TTL 캐시 ───
_cache: dict[str, tuple[float, object]] = {}
_CACHE_TTL = 60  # 초


def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and time.time() - entry[0] < _CACHE_TTL:
        return entry[1]
    return None


def _cache_set(key: str, value):
    _cache[key] = (time.time(), value)


def _get_container(request: Request):
    return request.app.state.container


def _iso(val) -> str | None:
    if val is None:
        return None
    return val.isoformat() if hasattr(val, 'isoformat') else val


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
    cache_key = f"posts:{q}:{source}:{category}:{limit}:{offset}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    c = _get_container(request)
    posts = await c.post_repo.search(
        query=q, source=source, category=category, limit=limit, offset=offset
    )
    result = [
        {
            "id": p.id,
            "source": p.source,
            "author": p.author,
            "content_text": p.content_text[:200],
            "summary": p.summary,
            "url": p.url,
            "importance_score": p.importance_score,
            "category_names": p.category_names,
            "keywords": p.keywords,
            "collected_at": _iso(p.collected_at),
        }
        for p in posts
    ]
    _cache_set(cache_key, result)
    return result


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
    """수동 AI 처리 트리거 (처리 후 자동 좋아요 포함)."""
    c = _get_container(request)
    try:
        uc = c.process_posts_use_case()
        stats = await uc.execute()
        try:
            like_stats = await c.like_posts_use_case().execute()
            if like_stats:
                stats = {**stats, "liked": like_stats}
        except Exception as e:
            stats = {**stats, "like_error": str(e)}
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

        send_results = {}
        if briefing.total_items > 0:
            send_results = await c.send_curated_briefing(briefing)

        return {
            "id": briefing.id,
            "title": briefing.title,
            "total_items": briefing.total_items,
            "send": send_results,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/slack/vote/trigger")
async def trigger_slack_vote(request: Request):
    """슬랙 투표 집계 → 1위 항목 심층 글 게시 수동 트리거."""
    c = _get_container(request)
    try:
        return await c.run_slack_vote_winner()
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# 처리한 슬랙 이벤트 ID (재시도·중복 방지, 프로세스 수명 동안만)
_slack_seen_events: set[str] = set()


@router.post("/slack/events")
async def slack_events(request: Request):
    """Slack Events API 수신 — @멘션에 붙여넣은 게시물로 프롬프트 실행.

    공개 엔드포인트(Cloudflare Tunnel 경유)이므로 서명 검증 필수.
    처리(1~3분)는 백그라운드로 넘기고 3초 안에 200을 반환한다.
    """
    import asyncio
    import json as _json
    import re as _re

    from src.infrastructure.delivery.slack_sender import verify_slack_signature

    c = _get_container(request)
    body = await request.body()

    secret = c.settings.slack_signing_secret
    if not secret:
        return JSONResponse(status_code=503, content={"error": "signing secret 미설정"})
    if not verify_slack_signature(
        secret,
        request.headers.get("x-slack-request-timestamp", ""),
        body,
        request.headers.get("x-slack-signature", ""),
    ):
        return JSONResponse(status_code=401, content={"error": "invalid signature"})

    try:
        data = _json.loads(body)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "bad json"})

    # 최초 URL 등록 시 슬랙이 보내는 검증 챌린지
    if data.get("type") == "url_verification":
        return {"challenge": data.get("challenge")}

    # 슬랙 재전송(3초 내 미응답 시)은 이미 처리 중이므로 무시
    if request.headers.get("x-slack-retry-num"):
        return {"ok": True}

    event = data.get("event") or {}
    event_id = data.get("event_id", "")
    if (
        data.get("type") == "event_callback"
        and event.get("type") == "app_mention"
        and event_id not in _slack_seen_events
        and not event.get("bot_id")  # 봇 자신의 메시지 무시
    ):
        _slack_seen_events.add(event_id)
        if len(_slack_seen_events) > 1000:
            _slack_seen_events.clear()
        text = _re.sub(r"<@[A-Z0-9]+>", "", event.get("text") or "").strip()
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts", "")
        if text and channel and thread_ts:
            asyncio.create_task(c.run_slack_mention(text, channel, thread_ts))
    return {"ok": True}


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
        "generated_at": _iso(b.generated_at),
        "total_items": b.total_items,
        "content_html": b.content_html,
    }


@router.get("/stats")
async def stats(request: Request):
    """수집 통계."""
    cached = _cache_get("stats")
    if cached is not None:
        return cached

    c = _get_container(request)
    try:
        now = datetime.utcnow()
        counts = await c.post_repo.count_by_source(now - timedelta(hours=24), now)
        runs = await c.run_repo.get_recent(limit=10)
        result = {
            "source_counts_24h": counts,
            "recent_runs": [
                {
                    "source": r.source,
                    "status": r.status,
                    "posts_collected": r.posts_collected,
                    "started_at": _iso(r.started_at),
                }
                for r in runs
            ],
        }
        _cache_set("stats", result)
        return result
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


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
                    "generated_at": _iso(b.generated_at),
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
            "generated_at": _iso(b.generated_at),
            "content_html": b.content_html,
            "total_items": b.total_items,
        }
    except Exception:
        return JSONResponse(status_code=500, content={"error": "브리핑 조회 실패"})


@router.post("/feedback")
async def submit_feedback(request: Request):
    """브리핑 항목 피드백 저장 (적절/과대/과소).

    body: {briefing_id, item_index, label}. 서버가 해당 항목의 헤드라인·점수·
    피처 스냅샷을 브리핑에서 조회해 라벨과 함께 저장한다(캘리브레이션 재료).
    """
    from src.infrastructure.database.repositories.feedback_repo_sqlite import VALID_LABELS

    c = _get_container(request)
    try:
        body = await request.json()
        briefing_id = body.get("briefing_id")
        label = body.get("label")
        try:
            item_index = int(body.get("item_index"))
        except (TypeError, ValueError):
            return JSONResponse(status_code=400, content={"error": "item_index는 정수여야 함"})

        if not briefing_id or label not in VALID_LABELS:
            return JSONResponse(status_code=400, content={"error": "briefing_id/label 필요"})

        b = await c.briefing_repo.get_by_id(str(briefing_id))
        if not b or item_index < 0 or item_index >= len(b.items):
            return JSONResponse(status_code=404, content={"error": "항목을 찾을 수 없음"})

        it = b.items[item_index]
        c.feedback_repo.upsert(
            briefing_id=str(briefing_id),
            item_index=item_index,
            headline=it.headline,
            category=it.category_name,
            importance_score=it.importance_score,
            tier=getattr(it, "tier", "minor"),
            features=getattr(it, "score_features", {}) or {},
            label=label,
        )
        return {"ok": True, "label": label, "total_feedback": c.feedback_repo.count()}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/feedback/{briefing_id}")
async def get_feedback(request: Request, briefing_id: str):
    """해당 브리핑의 항목별 피드백 라벨 조회 {item_index: label} (버튼 상태 표시용)."""
    c = _get_container(request)
    try:
        return c.feedback_repo.get_for_briefing(str(briefing_id))
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/keywords/top")
async def top_keywords(request: Request, limit: int = 20, days: int = 2):
    """최근 N일간 자주 언급된 키워드 top K."""
    cache_key = f"keywords:{limit}:{days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    c = _get_container(request)
    try:
        keywords = await c.post_repo.get_top_keywords(limit=limit, days=days)
        _cache_set(cache_key, keywords)
        return keywords
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/categories")
async def list_categories(request: Request):
    """카테고리 목록 API."""
    c = _get_container(request)
    try:
        categories = await c.category_repo.get_all()
        if not categories:
            from src.infrastructure.delivery.categories import CATEGORY_KO
            categories = [
                {"name": k, "name_ko": ko}
                for k, ko in CATEGORY_KO.items() if k != "Other"
            ]
        return categories
    except Exception:
        return JSONResponse(status_code=500, content={"error": "카테고리 조회 실패"})
