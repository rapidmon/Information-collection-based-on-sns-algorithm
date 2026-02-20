# Feature 007: 스케줄러 + 웹 대시보드 + 엔트리포인트

## 개요
APScheduler 기반 자동 스케줄링, FastAPI 웹 대시보드,
의존성 주입 컨테이너, CLI 엔트리포인트를 포함한 최종 통합.

## 구현 파일
- `src/infrastructure/config/container.py` — DI 컨테이너 (Composition Root)
- `src/application/use_cases/scheduler.py` — APScheduler 오케스트레이터
- `src/presentation/web/app.py` — FastAPI 앱 팩토리
- `src/presentation/web/routes/dashboard.py` — HTML 페이지 라우트
- `src/presentation/web/routes/api.py` — REST API 라우트
- `src/presentation/web/templates/*.html` — Jinja2 + HTMX 템플릿
- `main.py` — CLI 엔트리포인트

## 의존성 주입 컨테이너 (Container)

클린 아키텍처의 Composition Root. 모든 의존성을 한 곳에서 조립.

```
Container
├── Repositories: PostRepo, BriefingRepo, CategoryRepo, RunRepo
├── Services: AIProcessor, BriefingGenerator, Notifier
├── Collectors: Twitter, Threads, LinkedIn, DCInside
└── Use Case Factory: collect_posts_use_case(), process_posts_use_case(), ...
```

## 스케줄러 (Orchestrator)

### 등록 작업
| 작업 | 트리거 | 설명 |
|------|--------|------|
| `collect_{source}` | IntervalTrigger(분) | 각 소스별 수집 |
| `process_posts` | CronTrigger(:05분) | 매시 AI 처리 |
| `daily_briefing` | CronTrigger(06:30) | 일일 브리핑 + 이메일 |
| `health_check` | IntervalTrigger(5분) | 연속 실패 감지 알림 |

### 헬스체크
- 3회 연속 수집 실패 시 이메일 알림 발송

## 웹 대시보드

### 페이지
| URL | 설명 |
|-----|------|
| `/` | 대시보드: 최신 브리핑 + 수집 현황 + 최근 게시물 |
| `/briefings` | 브리핑 아카이브 (페이지네이션) |
| `/briefings/{id}` | 개별 브리핑 상세 |
| `/posts` | 게시물 검색/필터 (소스, 카테고리, 키워드) |
| `/status` | 시스템 상태: 수집 로그, 소스별 마지막 성공 |

### API
| URL | Method | 설명 |
|-----|--------|------|
| `/api/posts/search` | GET | 게시물 검색 JSON |
| `/api/collect/trigger/{source}` | POST | 수동 수집 트리거 |
| `/api/process/trigger` | POST | 수동 AI 처리 |
| `/api/briefing/generate` | POST | 수동 브리핑 생성 |
| `/api/briefings/latest` | GET | 최신 브리핑 JSON |
| `/api/stats` | GET | 수집 통계 |

### 기술 스택
- **FastAPI** + **Jinja2** (서버사이드 렌더링)
- **HTMX** (동적 업데이트, SPA 빌드 없이)
- **Tailwind CSS** (CDN)

## CLI 엔트리포인트 (main.py)

### 사용법
```bash
# 전체 시작 (수집 + AI + 웹 + 이메일)
python main.py serve

# DCInside만 수집 (브라우저 없이)
python main.py serve --no-browser

# 웹 대시보드만 (스케줄러 없이)
python main.py serve --no-scheduler

# SNS 수동 로그인
python main.py login twitter
python main.py login threads
python main.py login linkedin
```

### 시작 순서
1. `.env` + `config/settings.yaml` 로드
2. SQLite DB 초기화 (테이블 자동 생성)
3. Playwright 브라우저 초기화 (선택적)
4. Container 조립 (모든 의존성 주입)
5. 카테고리 시드 데이터
6. APScheduler 시작
7. Uvicorn 웹 서버 시작

## 초기 실행 가이드

```bash
# 1. 의존성 설치
pip install -r requirements.txt
playwright install chromium

# 2. 환경변수 설정
cp .env.example .env
# .env 파일을 편집하여 API 키, SMTP 정보 입력

# 3. SNS 로그인 (각 플랫폼)
python main.py login twitter
python main.py login threads
python main.py login linkedin

# 4. 서버 시작
python main.py serve
# → http://localhost:8000 에서 대시보드 확인
```
