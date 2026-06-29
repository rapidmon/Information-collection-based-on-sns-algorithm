# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

SNS(X·Threads·LinkedIn·DCInside) 알고리즘 피드에서 기술 뉴스를 수집 → GPT로 필터/요약/분류 → 매일 아침 이메일 브리핑을 발송하는 시스템. SNS 수집은 사용자의 로그인된 Chrome에 **CDP로 연결**해서 수행한다 (별도 헤드리스 브라우저가 아님).

## Commands

```bash
# 설치
pip install -r requirements.txt
playwright install chromium

# 수집 전제조건: Chrome을 디버그 모드로 띄우고 SNS에 로그인해 둬야 함 (포트 9222)
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\chrome_temp"

# 서버 (스케줄러 + 웹) 실행
python main.py serve
python main.py serve --no-scheduler      # 웹만, 자동 작업 없이

# 수동 즉시 수집
python main.py collect-now                # 전체 소스
python main.py collect-now twitter        # 특정 소스만

# 실행 중 서버에 수동 트리거 (localhost:8000)
curl -X POST http://localhost:8000/api/collect/trigger/twitter
curl -X POST http://localhost:8000/api/process/trigger
curl -X POST http://localhost:8000/api/briefing/generate

# 테스트 (pytest, asyncio_mode=auto)
pytest                                     # 전체
pytest tests/test_processing              # 디렉터리 단위
pytest tests/test_x.py::test_name         # 단일 테스트
```

> `tests/` 하위는 현재 패키지 골격(`__init__.py`)만 있고 실제 테스트는 거의 없다. 새 기능에 테스트를 추가할 때 `test_collectors` / `test_processing` / `test_briefing` 구분을 따른다.

## Architecture

클린 아키텍처 4계층. 의존성은 항상 안쪽(domain)을 향한다.

- **`src/domain/`** — 순수 비즈니스. `entities/`(Post, Briefing, Category, CollectionRun), `repositories/`·`services/`는 전부 `Protocol` 인터페이스, `value_objects/`(ContentHash). 외부 라이브러리 의존 없음.
- **`src/application/use_cases/`** — 오케스트레이션. `collect_posts`, `process_posts`, `generate_briefing`, `send_briefing`, `scheduler`. 도메인 인터페이스만 받아 동작.
- **`src/infrastructure/`** — 구체 구현. `collectors/`(CDP/HTTP 수집기), `ai/`(OpenAI 프로세서 + 프롬프트), `database/`(SQLite·Firestore 레포), `delivery/`(이메일·브리핑 빌더), `config/`(설정·**Container**).
- **`src/presentation/web/`** — FastAPI 앱, REST 라우트(`routes/api.py`), HTMX 대시보드(`routes/dashboard.py`).

### Composition Root — `src/infrastructure/config/container.py`

**모든 의존성 조립은 `Container` 한 곳에서만** 이뤄진다. 새 수집기/레포/서비스를 추가하면 여기서 와이어링하고, use case는 `Container`의 팩토리 메서드(`collect_posts_use_case(source)` 등)로 생성한다. `main.py`가 Container를 만들어 `Orchestrator`와 웹앱에 넘긴다.

### 데이터 저장소가 분리되어 있음 (중요)

| 데이터 | 저장소 | 비고 |
|--------|--------|------|
| Posts, CollectionRun | **SQLite** (`data/posts.db`, 자동 생성) | 비용 $0, 30일 후 자동 삭제 |
| Briefings | **Firestore** | 영구 보관 |
| Categories | **인메모리** (`MemoryCategoryRepository`) | YAML에서 시드, 영속 안 함 |

README 다이어그램은 Firestore에 Posts가 가는 것처럼 보이지만, 실제 코드는 위 표가 정확하다. Post 관련 작업은 SQLite 레포(`post_repo_sqlite.py`)를 본다.

### 수집 (Collectors) — CDP 패턴

- SNS 3종(twitter/threads/linkedin)은 `infrastructure/collectors/cdp.py`의 `cdp_connection()` 컨텍스트 매니저로 사용자 Chrome(포트 9222)에 붙는다. CDP 무응답 시 Chrome을 `taskkill` 후 자동 재시작·재연결한다.
- DCInside는 브라우저 없이 httpx + BeautifulSoup HTTP 스크레이핑.
- 모든 수집기는 `domain/services/collector.py`의 `Collector` Protocol(`source_name`, `collect()`, `is_session_valid()`)을 구현.
- 스케줄러는 CDP 동시 접속 충돌을 막으려고 소스별 시작을 **2분씩 stagger**한다.

### AI 처리 파이프라인 — `process_posts.py` + `ai/openai_processor.py`

청크 단위로 4단계를 거친다: **① filter+summarize → ② verify_claims(웹 검색 교차검증으로 스캠/허위 제거) → ③ categorize+중요도 → ④ 청크별 즉시 DB 업데이트**(크래시 시 진행분 보존). AI 응답에 누락된 게시물은 비관련 처리해 재처리 루프를 막는다. 비용 최적화로 필터는 저렴한 모델, 처리는 상위 모델을 쓴다(모델명은 YAML `processing.model_*`).

### 스케줄 작업 — `application/use_cases/scheduler.py`

APScheduler `Orchestrator`가 등록: 소스별 수집(interval), AI 처리(시작 5분 후 첫 실행), 일일 브리핑(cron), 5분 헬스체크(연속 3회 실패 시 알림 + 메모리 RSS 로깅), 매일 자정 30일 이상 데이터 정리.

## Configuration

설정은 **두 곳**으로 갈린다:

- **`.env`** — 시크릿. `Settings`(pydantic-settings)로 로드. OpenAI 키, SMTP, Firebase, SNS 자격증명.
- **`config/settings.yaml`** — 동작 파라미터. `load_app_config()` → `AppConfig`로 로드. 소스 on/off·수집 주기·스크롤 횟수, 카테고리 정의, AI 모델·배치·`min_importance_for_briefing`, 브리핑 시각, 이메일 수신자.

`collection.max_age_days`는 전역값이며 각 collector 설정에 주입된다(개별 override 가능) — 이 일수보다 오래된 게시물은 수집 단계에서 컷오프.

## Conventions

- 모든 신규 모듈 상단에 `from __future__ import annotations`.
- 도메인 경계는 `Protocol`로 정의하고 인프라에서 구현 — 구체 클래스를 use case가 직접 import하지 않는다.
- 주석·로그·docstring은 한국어.
- I/O 경계는 async (`asyncio.run`으로 진입, Playwright/FastAPI/aiosmtplib 모두 async). SQLite 레포는 동기 호출도 섞여 있으니 시그니처를 확인할 것.

## Latest Session
- Date: 2026-03-21
- Summary: .claude/context/session-2026-03-21-2.md
- Status: ✅ 브리핑 시스템 완성 — 버그 수정, 중요도 0.7 필터, 40개 토픽 이메일 발송 완료
