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
**[NEW]** 스케줄 전면 변경:
| 작업 | 트리거 | 설명 |
|------|--------|------|
| `collect_{source}` | IntervalTrigger(분) | 각 소스별 수집 |
| **[NEW]** `process_posts` | IntervalTrigger(10분) | ~~매시 :05분~~ → 10분마다 AI 처리 |
| **[NEW]** `daily_briefing` | CronTrigger(09:00) | ~~06:30~~ → 09:00 브리핑 + 이메일 |
| `health_check` | IntervalTrigger(5분) | 연속 실패 감지 알림 |

### **[NEW]** 수집 주기 변경
| 소스 | 기존 | 변경 |
|------|------|------|
| Twitter | 30분 | **10분** |
| Threads | 45분 | **10분** |
| LinkedIn | 60분 | **10분** |
| DCInside | 30분 | **180분** |

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

### **[NEW]** GitHub Pages 정적 대시보드
로컬 FastAPI 대시보드 외에 GitHub Pages용 정적 대시보드도 구현:
- `docs/index.html` — 메인 대시보드
- `docs/posts.html` — 게시물 탐색
- `docs/briefings.html` — 브리핑 아카이브
- Firebase JS SDK로 Firestore 직접 읽기 (읽기만 공개)
- Tailwind CSS + 바닐라 JS

### API
| URL | Method | 설명 |
|-----|--------|------|
| `/api/posts/search` | GET | 게시물 검색 JSON |
| `/api/collect/trigger/{source}` | POST | 수동 수집 트리거 |
| `/api/process/trigger` | POST | 수동 AI 처리 |
| `/api/briefing/generate` | POST | 수동 브리핑 생성 |
| `/api/briefings/latest` | GET | 최신 브리핑 JSON |
| `/api/stats` | GET | 수집 통계 |

### **[NEW]** 대시보드 데이터 필터링
- 대시보드에는 `is_relevant == true`인 AI 처리 완료 게시물만 표시
- Firestore 쿼리와 GitHub Pages JS 모두 동일 필터 적용

### 기술 스택
- **FastAPI** + **Jinja2** (서버사이드 렌더링)
- **HTMX** (동적 업데이트, SPA 빌드 없이)
- **Tailwind CSS** (CDN)

## CLI 엔트리포인트 (main.py)

### 사용법
**[NEW]** Python 3.12 명시, CDP 연결 방식:
```bash
# 1. Chrome 디버그 모드로 실행 (필수)
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222

# 2. Chrome에서 Twitter, Threads, LinkedIn 로그인 + DCInside 개념글 탭 열기

# 3. 전체 시작 (수집 + AI + 웹 + 이메일)
py -3.12 main.py serve

# 웹 대시보드만 (스케줄러 없이)
py -3.12 main.py serve --no-scheduler

# 즉시 수집 (서버 없이)
py -3.12 main.py collect-now              # 전체 소스
py -3.12 main.py collect-now twitter      # 트위터만

# 서버 켜진 상태에서 수동 트리거
curl -X POST http://localhost:8000/api/process/trigger    # AI 처리
curl -X POST http://localhost:8000/api/briefing/generate  # 브리핑 생성
```

### 시작 순서
**[NEW]** Firebase + CDP 기반:
1. `.env` + `config/settings.yaml` 로드
2. **[NEW]** Firebase 초기화 (`init_firebase()` + `get_firestore_client()`)
3. **[NEW]** CDP로 사용자 Chrome에 연결 (Playwright `connect_over_cdp`)
4. Container 조립 (모든 의존성 주입)
5. 카테고리 시드 데이터
6. APScheduler 시작
7. Uvicorn 웹 서버 시작
