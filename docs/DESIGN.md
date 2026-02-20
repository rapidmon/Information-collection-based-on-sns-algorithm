# SNS 알고리즘 기반 기술 정보 수집 & 브리핑 시스템 설계서

## Context

SNS(X, Threads, LinkedIn)에 맞춰놓은 알고리즘 피드와 DCInside 특이점이온다 갤러리에서 AI/반도체/테크 관련 최신 정보를 자동 수집하고, AI로 요약/분류/통합하여 매일 아침 정보보고(브리핑) 형식으로 이메일+웹 대시보드로 전달하는 시스템.

---

## 기술 스택

**[NEW]** 전면 변경:

| 구분 | 선택 | 이유 |
|------|------|------|
| 언어 | **[NEW]** Python 3.12 | 스크래핑/AI/웹 생태계 최강 |
| 브라우저 자동화 | **[NEW]** Playwright (CDP로 사용자 Chrome에 연결) | 실제 사용자 브라우저 사용, 안티봇 우회 불필요 |
| HTTP 스크래핑 | **[NEW]** CDP + BeautifulSoup4 | DCInside도 CDP 기반으로 변경 |
| AI | **[NEW]** OpenAI gpt-4o-mini (필터링 + 분류 모두) | 비용 효율 (~$10/월) |
| 웹 | FastAPI + Jinja2 + HTMX + Tailwind CSS | 비동기, SSR, SPA 빌드 불필요 |
| **[NEW]** 웹 (공개) | GitHub Pages + Firebase JS SDK | 정적 대시보드, 어디서든 접속 |
| DB | **[NEW]** Firebase Firestore | 클라우드 NoSQL, 무료 티어 |
| 스케줄러 | APScheduler | cron/interval 트리거, asyncio 지원 |
| 이메일 | aiosmtplib | 비동기 SMTP |
| 설정 | pydantic-settings + YAML + .env | 타입 검증, 시크릿 분리 |

**예상 월 비용**: **[NEW]** OpenAI API ~$10/월 (gpt-4o-mini, 일 200-400건 처리 기준)

---

## 프로젝트 구조

**[NEW]** 클린 아키텍처로 재구성:

```
sns_algorithm_data_collection/
├── config/
│   └── settings.yaml          # 수집주기, 카테고리, 키워드 등 메인 설정
├── src/
│   ├── domain/                # 핵심 도메인 (의존성 없음)
│   │   ├── entities/          # Post, Briefing, Category, CollectionRun
│   │   ├── repositories/      # Repository Protocol (인터페이스)
│   │   ├── services/          # Service Protocol (Collector, AIProcessor, Notifier)
│   │   └── exceptions.py
│   ├── application/           # 애플리케이션 레이어
│   │   ├── use_cases/         # CollectPosts, ProcessPosts, GenerateBriefing, SendBriefing, Scheduler
│   │   └── dto/               # 데이터 전송 객체
│   ├── infrastructure/        # 인프라스트럭처 레이어
│   │   ├── config/            # Settings, AppConfig, Container (DI)
│   │   ├── database/          # [NEW] Firebase 클라이언트 + Firestore Repository 구현
│   │   ├── collectors/        # Twitter, Threads, LinkedIn, DCInside 수집기
│   │   ├── ai/                # [NEW] OpenAI 프로세서 + 프롬프트
│   │   └── delivery/          # 브리핑 빌더, 이메일 발송기
│   └── presentation/          # 프레젠테이션 레이어
│       └── web/               # FastAPI 앱, 라우트, 템플릿
├── docs/                      # [NEW] GitHub Pages 정적 대시보드
│   ├── index.html             # 메인 대시보드
│   ├── posts.html             # 게시물 탐색
│   ├── briefings.html         # 브리핑 아카이브
│   ├── js/                    # Firebase SDK, Firestore API, UI
│   └── css/                   # 스타일
├── tests/
├── .env                       # API키, SMTP, Firebase 인증 (.gitignore)
├── .gitignore
├── pyproject.toml
├── requirements.txt
├── main.py                    # CLI 엔트리포인트
└── docs/
    ├── DESIGN.md              # 이 문서
    ├── ARCHITECTURE.md        # 시스템 아키텍처 개요
    └── features/              # 기능별 상세 문서
```

---

## 데이터 수집 전략

### **[NEW]** 공통: CDP (Chrome DevTools Protocol) 연결

기존 BrowserManager(세션 관리) 대신, 사용자의 Chrome 브라우저에 CDP로 직접 연결:

- **CDP 연결**: `--remote-debugging-port=9222`로 Chrome 실행 후 Playwright가 연결
- **세션 관리 불필요**: 사용자가 이미 로그인한 브라우저 그대로 사용
- **안티봇 우회 불필요**: 실제 사용자 브라우저이므로 탐지 리스크 최소
- **세션 만료 감지**: 로그인 페이지 리다이렉트 감지 → 즉시 알림

```python
# CDP 연결 (각 수집기 공통)
browser = await playwright.chromium.connect_over_cdp("http://localhost:9222")
context = browser.contexts[0]  # 사용자의 기존 브라우저 컨텍스트
```

### 플랫폼별 전략

**[NEW]** 수집 주기 및 방식 전면 변경:

| 플랫폼 | 방식 | 수집 주기 | 스크롤 횟수 | 딜레이 | 회당 예상 수집량 |
|--------|------|-----------|------------|--------|----------------|
| X (Twitter) | CDP + GraphQL 인터셉트 | **10분** | 8회 | 2-4초 | 40-60건 |
| Threads | CDP + GraphQL 인터셉트 | **10분** | 6회 | 2.5-5초 | 30-50건 |
| LinkedIn | CDP + DOM 파싱 | **10분** | **8회** | 3-7초 | 15-25건 |
| DCInside | **CDP + BeautifulSoup** | **180분** | N/A | 1.5-3초 | **최대 20건** |

### X (Twitter) - GraphQL 인터셉트 방식

Twitter의 타임라인 데이터는 GraphQL API(`HomeTimeline`, `HomeLatestTimeline`)를 통해 로드됨.
네트워크 응답을 인터셉트하면 구조화된 JSON을 직접 획득 가능 (DOM 파싱보다 안정적).

### Threads - GraphQL 인터셉트

Threads도 Meta의 GraphQL 기반. CSS 클래스가 난독화되어 있어 네트워크 인터셉트가 유일하게 안정적인 방법.

### LinkedIn - DOM 파싱 (보수적)

LinkedIn은 가장 엄격한 안티봇. 보수적 딜레이(3-7초)와 **[NEW]** 8회 스크롤(800-1500px).

### **[NEW]** DCInside - CDP + BeautifulSoup (데스크톱 파싱)

기존 HTTP 스크래핑 → CDP 기반으로 변경. 사용자 Chrome에서 열려있는 개념글 탭의 HTML을 파싱.
- 데스크톱 셀렉터 사용 (`tr.ub-content`, `td.gall_tit` 등)
- 1페이지에서 최대 20건만 수집
- 각 게시물 상세 페이지 방문 후 개념글 페이지로 복귀

---

## 공통 데이터 모델

```python
@dataclass
class Post:
    """수집된 게시물 도메인 엔티티"""
    source: str              # 'twitter', 'threads', 'linkedin', 'dcinside'
    external_id: str         # 플랫폼 고유 ID
    url: str
    author: str
    content_text: str
    # ... engagement, media, timestamps

    # AI 처리 결과
    summary: Optional[str] = None
    importance_score: Optional[float] = None
    language: Optional[str] = None
    is_relevant: Optional[bool] = None
    category_names: list[str] = field(default_factory=list)

    # [NEW] 브리핑 포함 여부
    briefed_at: Optional[datetime] = None

    # 중복 처리
    content_hash: Optional[str] = None
    dedup_cluster_id: Optional[int] = None
```

---

## **[NEW]** Firestore 컬렉션 구조

```
posts/                         # 문서 ID = external_id
  └── source, url, author, content_text, engagement_*,
      published_at, collected_at, summary, importance_score,
      is_relevant, category_names, briefed_at, content_hash

briefings/                     # 문서 ID = 자동 생성
  └── title, briefing_type, generated_at, period_start/end,
      content_html, content_text, email_sent, items[]

categories/                    # 문서 ID = name
  └── name, name_ko, color, keywords[]

collection_runs/               # 문서 ID = 자동 생성
  └── source, started_at, completed_at, status,
      posts_collected, error_message
```

---

## AI 처리 파이프라인

### 전체 흐름

**[NEW]** Claude → OpenAI, 처리 주기 변경:

```
수집 직후 (10분마다):
  새 게시물 → [관련성 필터 + 요약 (gpt-4o-mini)] → [분류+중요도 (gpt-4o-mini)]
    → 관련 있음: DB 업데이트
    → 관련 없음: Firestore에서 삭제

매일 09:00 KST:
  미브리핑 관련 게시물 → [중복제거+통합 (gpt-4o-mini)] → 브리핑 생성 → 이메일 발송
  → 포함된 게시물에 briefed_at 마킹
```

### **[NEW]** 모델 사용 전략

| 작업 | 모델 | 배치 크기 |
|------|------|-----------|
| 관련성 필터링 + 요약 | gpt-4o-mini | 20건/호출 |
| 분류 + 중요도 | gpt-4o-mini | 20건/호출 |
| 중복제거 + 통합 | gpt-4o-mini | 전체 (1-2회 호출) |

### **[NEW]** 카테고리 (7개)

| 카테고리 | 한국어 | 색상 |
|----------|--------|------|
| AI | AI | #4A90D9 |
| Semiconductor | 반도체 | #E74C3C |
| Cloud | 클라우드 | #2ECC71 |
| Startup | 스타트업 | #F39C12 |
| BigTech | 빅테크 | #9B59B6 |
| Regulation | 규제/정책 | #1ABC9C |
| **Coding** | **코딩** | **#E67E22** |

### 비용 추정

**[NEW]** gpt-4o-mini 기준:

| 항목 | 예상 비용 |
|------|-----------|
| 전체 파이프라인 (gpt-4o-mini) | ~$0.30/일 |
| **월 예상** | **~$10** |

---

## 스케줄러 & 오케스트레이션

### **[NEW]** 스케줄 요약

| 작업 | 주기 | 시간 |
|------|------|------|
| X 수집 | **10분** | 상시 |
| Threads 수집 | **10분** | 상시 |
| LinkedIn 수집 | **10분** | 상시 |
| DCInside 수집 | **180분** | 상시 |
| AI 처리 (신규 게시물) | **10분** | 상시 |
| 일일 브리핑 생성+이메일 | 1일 1회 | **09:00 KST** |
| 헬스체크 | 5분 | 상시 |

---

## 웹 대시보드

### 로컬 대시보드 (FastAPI)
- `http://localhost:8000`
- FastAPI + Jinja2 + HTMX + Tailwind CSS
- **[NEW]** `is_relevant == true` 필터 적용 (AI 처리 완료 게시물만 표시)

### **[NEW]** GitHub Pages 정적 대시보드
- Firebase JS SDK v10으로 Firestore 직접 읽기
- `docs/index.html` — 메인 대시보드 (통계 + 최신 브리핑)
- `docs/posts.html` — 게시물 탐색 (소스/카테고리 필터, 검색)
- `docs/briefings.html` — 브리핑 아카이브
- Firestore 보안 규칙: 읽기 공개, 쓰기 차단

---

## 설정 파일

### config/settings.yaml

**[NEW]** 현재 설정:

```yaml
app:
  name: "SNS Tech Briefing"
  timezone: "Asia/Seoul"

collection:
  twitter:
    enabled: true
    interval_minutes: 10         # [NEW] 30 → 10
    scroll_rounds: 8
    use_graphql_interception: true
  threads:
    enabled: true
    interval_minutes: 10         # [NEW] 45 → 10
    scroll_rounds: 6
  linkedin:
    enabled: true
    interval_minutes: 10         # [NEW] 60 → 10
    scroll_rounds: 8             # [NEW] 4 → 8
  dcinside:
    enabled: true
    interval_minutes: 180        # [NEW] 30 → 180
    gallery_id: "thesingularity"

categories:                      # [NEW] 7개 (Coding 추가)
  - { name: "AI", color: "#4A90D9" }
  - { name: "Semiconductor", color: "#E74C3C" }
  - { name: "Cloud", color: "#2ECC71" }
  - { name: "Startup", color: "#F39C12" }
  - { name: "BigTech", color: "#9B59B6" }
  - { name: "Regulation", color: "#1ABC9C" }
  - { name: "Coding", color: "#E67E22" }  # [NEW]

processing:
  model_filter: "gpt-4o-mini"   # [NEW] claude-haiku → gpt-4o-mini
  model_process: "gpt-4o-mini"  # [NEW] claude-sonnet → gpt-4o-mini

briefing:
  daily_time: "09:00"           # [NEW] 06:30 → 09:00
  max_items: 20
```

### .env

**[NEW]** 환경변수:

```ini
# [NEW] OpenAI (기존 Anthropic 대체)
OPENAI_API_KEY=sk-your-key-here

# Email SMTP
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
EMAIL_FROM=your-email@gmail.com

# [NEW] Firebase (기존 DATABASE_URL 대체)
FIREBASE_CREDENTIAL_PATH=firebase-service-account.json
FIREBASE_PROJECT_ID=your-project-id
```

---

## 실행 방법

**[NEW]** Chrome CDP 연결 방식:

```bash
# 1. 의존성 설치
py -3.12 -m pip install -r requirements.txt
py -3.12 -m playwright install chromium

# 2. Chrome 디버그 모드로 실행
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222

# 3. Chrome에서 Twitter, Threads, LinkedIn 로그인
#    DCInside 특이점이온다 갤러리 개념글 탭 열기

# 4. 서버 시작
py -3.12 main.py serve

# 5. 수동 트리거 (필요 시)
curl -X POST http://localhost:8000/api/process/trigger
curl -X POST http://localhost:8000/api/briefing/generate
```

---

## 주요 리스크 & 대응

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 플랫폼 HTML/API 구조 변경 | 수집 중단 | GraphQL 인터셉트 우선, 셀렉터를 config로 분리, 에러 즉시 알림 |
| 계정 차단 (특히 LinkedIn) | 피드 접근 불가 | **[NEW]** CDP로 실제 사용자 브라우저 사용하여 리스크 최소화 |
| 세션 만료 | 수집 실패 | 자동 감지 + 즉시 알림, Chrome에서 재로그인 |
| API 비용 초과 | 예산 초과 | **[NEW]** gpt-4o-mini 단일 모델로 비용 최적화 (~$10/월) |
| DCInside 차단 | 갤러리 접근 불가 | **[NEW]** CDP 기반이므로 실제 브라우저 사용, 180분 간격으로 보수적 수집 |
