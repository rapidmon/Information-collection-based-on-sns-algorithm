# SNS 알고리즘 기반 기술 정보 수집 & 브리핑 시스템 설계서

## Context

SNS(X, Threads, LinkedIn)에 맞춰놓은 알고리즘 피드와 DCInside 특이점이온다 갤러리에서 AI/반도체/테크 관련 최신 정보를 자동 수집하고, Claude API로 요약/분류/통합하여 매일 아침 정보보고(브리핑) 형식으로 이메일+웹 대시보드로 전달하는 시스템.

---

## 기술 스택

| 구분 | 선택 | 이유 |
|------|------|------|
| 언어 | Python 3.11+ | 스크래핑/AI/웹 생태계 최강 |
| 브라우저 자동화 | Playwright + playwright-stealth | 비동기, 세션 지속, 안티봇 우회 |
| HTTP 스크래핑 | httpx + BeautifulSoup4 | 비동기 HTTP, DCInside용 |
| AI | anthropic SDK (Haiku=필터링, Sonnet=요약/분류) | 공식 SDK, Batches API 50% 할인 |
| 웹 | FastAPI + Jinja2 + HTMX + Tailwind CSS | 비동기, SSR, SPA 빌드 불필요 |
| DB | SQLite + SQLAlchemy + aiosqlite | 로컬 배포, 설정 불필요, 비동기 |
| 스케줄러 | APScheduler | cron/interval 트리거, asyncio 지원 |
| 이메일 | aiosmtplib | 비동기 SMTP |
| 설정 | pydantic-settings + YAML + .env | 타입 검증, 시크릿 분리 |

**예상 월 비용**: Claude API ~$10-15/월 (일 200-400건 처리 기준)

---

## 프로젝트 구조

```
sns_algorithm_data_collection/
├── config/
│   ├── settings.yaml          # 수집주기, 카테고리, 키워드 등 메인 설정
│   └── logging.yaml
├── src/
│   ├── __init__.py
│   ├── collectors/            # 데이터 수집 레이어
│   │   ├── __init__.py
│   │   ├── base.py            # 추상 베이스 (RawPost 데이터 클래스)
│   │   ├── browser_manager.py # Playwright 세션/쿠키 관리
│   │   ├── twitter.py         # X 피드 수집 (GraphQL 인터셉트)
│   │   ├── threads.py         # Threads 피드 수집
│   │   ├── linkedin.py        # LinkedIn 피드 수집
│   │   └── dcinside.py        # DCInside HTTP 스크래핑
│   ├── processing/            # AI 처리 파이프라인
│   │   ├── __init__.py
│   │   ├── pipeline.py        # 메인 오케스트레이션
│   │   ├── summarizer.py      # 요약
│   │   ├── deduplicator.py    # 중복 제거
│   │   ├── categorizer.py     # 분류 + 중요도
│   │   ├── merger.py          # 유사 토픽 통합
│   │   └── prompts.py         # Claude API 프롬프트 템플릿
│   ├── briefing/              # 브리핑 생성
│   │   ├── __init__.py
│   │   ├── generator.py       # 브리핑 문서 생성
│   │   └── formatter.py       # HTML/텍스트 포매팅
│   ├── delivery/              # 전달
│   │   ├── __init__.py
│   │   ├── email_sender.py    # SMTP 이메일
│   │   └── templates/
│   │       ├── daily_briefing.html  # Jinja2 이메일 템플릿
│   │       └── daily_briefing.txt   # 텍스트 폴백
│   ├── web/                   # 웹 대시보드
│   │   ├── __init__.py
│   │   ├── app.py             # FastAPI 앱
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── dashboard.py   # 대시보드 페이지
│   │   │   ├── api.py         # REST API (검색, 필터)
│   │   │   └── briefings.py   # 브리핑 아카이브
│   │   ├── static/
│   │   │   ├── css/style.css
│   │   │   └── js/app.js
│   │   └── templates/
│   │       ├── base.html
│   │       ├── dashboard.html
│   │       ├── briefing_detail.html
│   │       └── archive.html
│   ├── database/
│   │   ├── __init__.py
│   │   ├── models.py          # SQLAlchemy ORM 모델
│   │   └── session.py         # DB 세션 관리
│   ├── scheduler/
│   │   ├── __init__.py
│   │   ├── orchestrator.py    # APScheduler 작업 정의
│   │   └── health.py          # 상태 모니터링
│   └── utils/
│       ├── __init__.py
│       ├── config.py          # 설정 로더 (pydantic-settings)
│       └── logger.py
├── browser_data/              # Playwright 로그인 세션 (.gitignore)
│   ├── twitter_profile/
│   ├── threads_profile/
│   └── linkedin_profile/
├── data/                      # SQLite DB 파일 (.gitignore)
│   └── backups/
├── logs/                      # 애플리케이션 로그
├── tests/
│   ├── __init__.py
│   ├── test_collectors/
│   ├── test_processing/
│   └── test_briefing/
├── .env                       # API키, SMTP 비밀번호 (.gitignore)
├── .env.example               # .env 템플릿
├── .gitignore
├── pyproject.toml
├── requirements.txt
├── main.py                    # 엔트리포인트
└── docs/
    └── DESIGN.md              # 이 문서
```

---

## 데이터 수집 전략

### 공통: Playwright 브라우저 관리

- **세션 지속**: `storageState`로 쿠키/localStorage를 `browser_data/{platform}_profile/state.json`에 저장/복원
- **안티봇 우회**: playwright-stealth 적용, 랜덤 스크롤 딜레이, 랜덤 마우스 이동, headed 모드
- **최초 로그인**: headed 모드로 브라우저 열어서 수동 로그인 → 이후 자동 세션 복원
- **세션 만료 감지**: 로그인 페이지 리다이렉트 감지 → 즉시 알림

```python
class BrowserManager:
    """Playwright 브라우저 생명주기 및 세션 관리"""

    async def initialize(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=False,
            args=['--disable-blink-features=AutomationControlled']
        )

    async def get_context(self, platform: str) -> BrowserContext:
        """플랫폼별 persistent context (쿠키 자동 복원)"""
        storage_path = f"browser_data/{platform}_profile/state.json"
        context = await self._browser.new_context(
            storage_state=storage_path if Path(storage_path).exists() else None,
            viewport={'width': 1920, 'height': 1080},
            locale='ko-KR', timezone_id='Asia/Seoul',
        )
        await stealth_async(context)  # playwright-stealth 적용
        return context

    async def save_state(self, platform: str):
        """수집 후 쿠키/세션 저장"""
        await self._contexts[platform].storage_state(
            path=f"browser_data/{platform}_profile/state.json"
        )
```

### 플랫폼별 전략

| 플랫폼 | 방식 | 수집 주기 | 스크롤 횟수 | 딜레이 | 회당 예상 수집량 |
|--------|------|-----------|------------|--------|----------------|
| X (Twitter) | Playwright + GraphQL 인터셉트 | 30분 | 8-10회 | 2-4초 | 40-60건 |
| Threads | Playwright + GraphQL 인터셉트 | 45분 | 6-8회 | 2.5-5초 | 30-50건 |
| LinkedIn | Playwright DOM 파싱 | 60분 | 3-5회 | 3-7초 | 15-25건 |
| DCInside | httpx + BeautifulSoup (HTTP) | 30분 | N/A | 1.5-3초 | 40-60건 |

### X (Twitter) - GraphQL 인터셉트 방식

Twitter의 타임라인 데이터는 GraphQL API(`HomeTimeline`, `HomeLatestTimeline`)를 통해 로드됨.
네트워크 응답을 인터셉트하면 구조화된 JSON을 직접 획득 가능 (DOM 파싱보다 안정적).

```python
class TwitterCollector(BaseCollector):
    async def collect(self) -> list[RawPost]:
        page = await self.get_page('twitter')
        captured_data = []

        async def handle_response(response):
            if 'HomeTimeline' in response.url:
                captured_data.append(await response.json())

        page.on('response', handle_response)
        await page.goto('https://x.com/home', wait_until='networkidle')

        # 사람처럼 스크롤
        for _ in range(self.config.scroll_rounds):
            await page.mouse.wheel(0, random.randint(800, 1500))
            await asyncio.sleep(random.uniform(2.0, 4.0))

        return self._parse_graphql_timeline(captured_data)
```

### Threads - GraphQL 인터셉트

Threads도 Meta의 GraphQL 기반. CSS 클래스가 난독화되어 있어 네트워크 인터셉트가 유일하게 안정적인 방법.

### LinkedIn - DOM 파싱 (보수적)

LinkedIn은 가장 엄격한 안티봇. 보수적 딜레이(3-7초)와 최소 스크롤(3-5회) 필수.

```python
class LinkedInCollector(BaseCollector):
    SELECTORS = {
        'feed_update': '.feed-shared-update-v2',
        'post_text': '.feed-shared-update-v2__description',
        'actor_name': '.update-components-actor__name',
    }
    # 스크롤 간 3-7초 랜덤 대기, 최대 5회 스크롤
```

### DCInside 특이점이온다 갤러리 - HTTP 스크래핑

로그인 불필요. 모바일 페이지(`m.dcinside.com`)가 HTML이 단순하여 파싱 용이.

```python
class DCInsideCollector(BaseCollector):
    GALLERY_ID = "thesingularity"  # 마이너 갤러리
    MOBILE_LIST_URL = "https://m.dcinside.com/board/thesingularity"

    async def collect(self) -> list[RawPost]:
        async with httpx.AsyncClient(headers=self.HEADERS) as client:
            for page_num in range(1, self.config.pages + 1):
                response = await client.get(f"{self.MOBILE_LIST_URL}?page={page_num}")
                soup = BeautifulSoup(response.text, 'lxml')
                # 글 목록 파싱 → 상세 페이지 요청 → 본문 추출
                await asyncio.sleep(random.uniform(1.5, 3.0))
```

---

## 공통 데이터 모델

```python
@dataclass
class RawPost:
    """모든 플랫폼의 수집 데이터를 담는 통일 구조"""
    source: str              # 'twitter', 'threads', 'linkedin', 'dcinside'
    external_id: str         # 플랫폼 고유 ID
    url: str                 # 원문 링크
    author: str
    author_url: Optional[str]
    content_text: str        # 전문 텍스트
    content_html: Optional[str]
    media_urls: list[str]
    engagement: dict         # {'likes': int, 'reposts': int, 'comments': int}
    published_at: Optional[datetime]
    collected_at: datetime
    raw_data: Optional[dict] # 디버깅용 원본 데이터
```

---

## DB 스키마 (SQLAlchemy ORM)

### posts - 수집된 원본 게시물

```python
class Post(Base):
    __tablename__ = 'posts'

    id = Column(Integer, primary_key=True)
    source = Column(String(20), nullable=False, index=True)
    external_id = Column(String(255), nullable=False, unique=True)
    url = Column(String(2048))

    author = Column(String(255))
    author_url = Column(String(2048))
    content_text = Column(Text, nullable=False)
    content_html = Column(Text)
    media_urls = Column(JSON)

    engagement_likes = Column(Integer, default=0)
    engagement_reposts = Column(Integer, default=0)
    engagement_comments = Column(Integer, default=0)
    engagement_views = Column(Integer, default=0)

    published_at = Column(DateTime)
    collected_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # AI 생성 필드
    summary = Column(Text)
    importance_score = Column(Float)          # 0.0 ~ 1.0
    language = Column(String(10))
    is_relevant = Column(Boolean, default=True)

    # 중복 감지
    content_hash = Column(String(64), index=True)  # SHA-256
    dedup_cluster_id = Column(Integer, ForeignKey('dedup_clusters.id'))

    raw_data = Column(JSON)
```

### categories - 토픽 카테고리

```python
class Category(Base):
    __tablename__ = 'categories'
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True)     # 'AI', 'Semiconductor'
    name_ko = Column(String(50))               # 'AI', '반도체'
    color = Column(String(7))                  # '#4A90D9'
```

### briefings - 브리핑 문서

```python
class Briefing(Base):
    __tablename__ = 'briefings'
    id = Column(Integer, primary_key=True)
    title = Column(String(255))                # "2026-02-19 기술 모닝 브리핑"
    briefing_type = Column(String(20))         # daily, weekly
    generated_at = Column(DateTime)
    period_start = Column(DateTime)
    period_end = Column(DateTime)
    total_posts_analyzed = Column(Integer)
    total_items = Column(Integer)
    content_html = Column(Text)
    content_text = Column(Text)
    email_sent = Column(Boolean, default=False)
```

### briefing_items - 브리핑 항목

```python
class BriefingItem(Base):
    __tablename__ = 'briefing_items'
    id = Column(Integer, primary_key=True)
    briefing_id = Column(Integer, ForeignKey('briefings.id'))
    headline = Column(String(500))             # 굵은 헤드라인
    body = Column(Text)                        # 불릿 포인트 본문
    category_id = Column(Integer, ForeignKey('categories.id'))
    importance_score = Column(Float)
    sort_order = Column(Integer)
    source_count = Column(Integer)
    sources_summary = Column(String(255))      # "X, LinkedIn, DCInside"
```

### dedup_clusters - 중복 그룹

```python
class DedupCluster(Base):
    __tablename__ = 'dedup_clusters'
    id = Column(Integer, primary_key=True)
    representative_post_id = Column(Integer, ForeignKey('posts.id'))
    topic_summary = Column(Text)
```

### collection_runs - 수집 로그

```python
class CollectionRun(Base):
    __tablename__ = 'collection_runs'
    id = Column(Integer, primary_key=True)
    source = Column(String(20))
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    status = Column(String(20))                # success, failed, partial
    posts_collected = Column(Integer)
    error_message = Column(Text)
```

**중복 방지 전략:**
- **Level 1** (저장 시): `content_hash` (SHA-256 of normalized text) + `external_id` UNIQUE 제약
- **Level 2** (AI 처리): Claude API로 의미적 중복 탐지 및 클러스터링

**아카이브**: 90일 이후 `raw_data`/`content_html` 삭제, 요약만 보존

---

## AI 처리 파이프라인

### 전체 흐름

```
수집 직후 (매시 :05분):
  새 게시물 → [관련성 필터 (Haiku)] → [요약 (Sonnet)] → [분류+중요도 (Sonnet)] → DB 업데이트

매일 06:30 KST:
  24시간 관련 게시물 → [중복제거+통합 (Sonnet)] → 브리핑 항목 생성 → HTML/텍스트 렌더링 → 이메일 발송
```

### 모델 사용 전략

| 작업 | 모델 | 이유 | 배치 크기 |
|------|------|------|-----------|
| 관련성 필터링 | Haiku | 저렴, 빠름, 단순 판단 | 20건/호출 |
| 요약 | Sonnet | 품질-비용 밸런스 | 15건/호출 |
| 분류+중요도 | Sonnet | 다중 카테고리 판단 필요 | 20건/호출 |
| 중복제거+통합 | Sonnet | 추론 능력 필요 | 전체 (1-2회 호출) |
| 일일 브리핑 | Batches API | 50% 비용 절감 | - |

### 프롬프트 설계

#### 1. 관련성 필터 + 요약 (배치)

```
당신은 기술 뉴스 분석가입니다. 아래 소셜 미디어 게시물들을 분석해주세요.

각 게시물에 대해:
1. 관련성 판단: AI, 반도체, 클라우드, 스타트업, 빅테크 등 기술 산업과 관련이 있는지 (true/false)
2. 한국어 요약: 관련성 있는 게시물만 2-3문장으로 핵심 내용 요약
3. 언어: 원문 언어 (ko/en/etc)

JSON 응답: [{"post_id": "...", "is_relevant": true, "summary": "...", "language": "ko"}]
```

#### 2. 분류 + 중요도 (배치)

```
다음 게시물 요약들을 카테고리로 분류하고 중요도를 매겨주세요.

카테고리: AI, Semiconductor, Cloud, Startup, BigTech, Regulation, Other

importance_score 기준:
- 0.9-1.0: 산업 영향 중대 뉴스 (대규모 M&A, 신규 정책, 혁신 제품)
- 0.7-0.9: 주요 뉴스 (기업 실적, 기술 발표, 투자)
- 0.5-0.7: 일반 뉴스 (업데이트, 소규모 발표)
- 0.3-0.5: 배경 정보/의견
- 0.0-0.3: 낮은 중요도

JSON 응답: [{"post_id": "...", "categories": ["AI"], "importance_score": 0.85}]
```

#### 3. 중복제거 + 통합 브리핑 생성

```
뉴스 편집자로서 아래 요약들에서 동일 사건/주제를 그룹으로 묶고 통합 브리핑을 생성해주세요.

각 그룹에 대해:
1. 통합 헤드라인 (한국어, 구체적)
2. 통합 본문 (불릿 포인트 3-5개, 수치/사실 중심)
3. 중요도 점수
4. 출처 목록

JSON 응답: [{"post_ids": [...], "headline": "...", "body_bullets": [...], "importance_score": 0.92}]
```

### 비용 추정

| 항목 | 일일 호출 수 | 단가 | 일일 비용 |
|------|-------------|------|-----------|
| Haiku 필터링 | ~20회 | ~$0.001 | ~$0.02 |
| Sonnet 요약 | ~10회 | ~$0.01 | ~$0.10 |
| Sonnet 분류 | ~10회 | ~$0.01 | ~$0.10 |
| Sonnet 통합 | ~2회 | ~$0.03 | ~$0.06 |
| **합계** | | | **~$0.30-0.50/일** |

**월 예상**: $10-15 (Batches API 활용 시 더 절감 가능)

---

## 브리핑 출력 형식

### 텍스트 형식 (이메일 + 웹)

```
===== 2026-02-19 기술 모닝 브리핑 =====

[AI] (3건)

**ByteDance, 자체 AI 칩 'SeedChip' 개발 본격화**
- ByteDance가 AI 추론(inference)용 자체 칩 'SeedChip' 개발 중 - 2026년 최소 10만개, 최대 35만개 생산 목표로 3월 말 엔지니어링 샘플 확보 예정
- 삼성전자와 위탁생산 협상 진행 중이며, 칩 제조와 함께 현재 공급 부족 상태인 HBM(고대역폭 메모리) 물량 확보도 동시 논의
- 미국의 Nvidia 고성능 GPU 수출 규제에 대응한 자체 칩 개발 전략으로, 올해 AI 관련 22조원 이상 투자 예정이나 절반 이상은 여전히 Nvidia H200 등 구매에 사용 계획
📌 중요도: ★★★★★ | 출처: X, DCInside

**OpenAI, GPT-5 공개 임박 - 3월 초 발표 예정**
- ...
📌 중요도: ★★★★☆ | 출처: X, Threads, LinkedIn

---

[반도체] (2건)

**TSMC, 미국 애리조나 3nm 공장 양산 시작**
- ...

---

[스타트업] (1건)
...

===== 수집 통계 =====
분석 게시물: 247건 | 관련 게시물: 89건 | 브리핑 항목: 12건
수집 출처: X(82), Threads(45), LinkedIn(38), DCInside(82)
```

### 중요도 별점 매핑

```python
def importance_to_stars(score: float) -> str:
    if score >= 0.9: return "★★★★★"
    if score >= 0.7: return "★★★★☆"
    if score >= 0.5: return "★★★☆☆"
    if score >= 0.3: return "★★☆☆☆"
    return "★☆☆☆☆"
```

---

## 웹 대시보드

### 기술: FastAPI + Jinja2 + HTMX + Tailwind CSS (CDN)

SPA 빌드 없이 서버사이드 렌더링. HTMX로 동적 검색/필터/무한 스크롤 구현.

### 페이지 구성

| 경로 | 기능 |
|------|------|
| `/` | 메인 대시보드: 오늘 브리핑 + 실시간 게시물 + 수집 상태 |
| `/briefings` | 브리핑 아카이브 (페이지네이션) |
| `/briefings/{id}` | 개별 브리핑 상세 |
| `/posts` | 전체 게시물 검색/필터 (소스, 카테고리, 날짜, 키워드) |
| `/status` | 시스템 상태: 소스별 마지막 수집, 오류, 다음 스케줄 |

### API 엔드포인트 (HTMX + 수동 트리거)

| 경로 | 기능 |
|------|------|
| `GET /api/posts/search?q=&source=&category=` | HTMX 파셜 렌더링 검색 |
| `POST /api/collect/trigger/{source}` | 수동 수집 트리거 |
| `POST /api/briefing/generate` | 수동 브리핑 생성 |

### 검색: SQLite FTS5

```sql
CREATE VIRTUAL TABLE posts_fts USING fts5(
    content_text, summary,
    content='posts', content_rowid='id'
);
```

한국어+영어 전문 검색. 외부 검색 엔진 불필요.

---

## 이메일 시스템

### SMTP 설정

Gmail App Password 또는 Naver SMTP 사용.

```python
class EmailSender:
    async def send_briefing(self, briefing: Briefing):
        message = MIMEMultipart('alternative')
        message['Subject'] = f"[기술 브리핑] {briefing.title}"
        message.attach(MIMEText(briefing.content_text, 'plain', 'utf-8'))
        message.attach(MIMEText(briefing.content_html, 'html', 'utf-8'))

        async with aiosmtplib.SMTP(hostname=host, port=587, start_tls=True) as smtp:
            await smtp.login(user, password)
            await smtp.send_message(message)
```

### HTML 이메일 템플릿

반응형 디자인, 모바일 최적화. 카테고리별 색상 구분, 중요도 별점, 출처 표시.

```html
<div class="briefing-item" style="border-left: 4px solid #4a90d9;">
  <div class="headline" style="font-weight: bold;">{{ item.headline }}</div>
  {% for bullet in item.body.split('\n') %}
    <div class="body-bullet">{{ bullet }}</div>
  {% endfor %}
  <div class="meta">중요도: {{ stars }} | 출처: {{ item.sources_summary }}</div>
</div>
```

---

## 스케줄러 & 오케스트레이션

### APScheduler 작업 정의

```python
class Orchestrator:
    def setup_jobs(self):
        # 수집 작업
        self.scheduler.add_job(self._collect, IntervalTrigger(minutes=30), args=['twitter'])
        self.scheduler.add_job(self._collect, IntervalTrigger(minutes=45), args=['threads'])
        self.scheduler.add_job(self._collect, IntervalTrigger(minutes=60), args=['linkedin'])
        self.scheduler.add_job(self._collect, IntervalTrigger(minutes=30), args=['dcinside'])

        # AI 처리 (매시 :05분)
        self.scheduler.add_job(self._process, CronTrigger(minute=5))

        # 일일 브리핑 (06:30 KST)
        self.scheduler.add_job(self._daily_briefing, CronTrigger(hour=6, minute=30))

        # 헬스체크 (5분마다)
        self.scheduler.add_job(self._health_check, IntervalTrigger(minutes=5))

        # DB 백업 (일요일 03:00)
        self.scheduler.add_job(self._backup, CronTrigger(day_of_week='sun', hour=3))
```

### 스케줄 요약

| 작업 | 주기 | 시간 |
|------|------|------|
| X 수집 | 30분 | 상시 |
| Threads 수집 | 45분 | 상시 |
| LinkedIn 수집 | 60분 | 상시 |
| DCInside 수집 | 30분 | 상시 |
| AI 처리 (신규 게시물) | 매시 | :05분 |
| 일일 브리핑 생성+이메일 | 1일 1회 | 06:30 KST |
| 헬스체크 | 5분 | 상시 |
| DB 백업 | 주 1회 | 일요일 03:00 |

### 헬스 모니터링

- 각 소스의 마지막 성공 수집 시간 추적
- 2시간 이상 수집 실패 → warning
- 3회 연속 실패 → critical 알림
- DB 용량 모니터링

---

## 설정 파일

### config/settings.yaml

```yaml
app:
  name: "SNS Tech Briefing"
  timezone: "Asia/Seoul"

collection:
  twitter:
    enabled: true
    interval_minutes: 30
    scroll_rounds: 8
    scroll_delay_min: 2.0
    scroll_delay_max: 4.0
    use_graphql_interception: true
  threads:
    enabled: true
    interval_minutes: 45
    scroll_rounds: 6
    scroll_delay_min: 2.5
    scroll_delay_max: 5.0
  linkedin:
    enabled: true
    interval_minutes: 60
    scroll_rounds: 4
    scroll_delay_min: 3.0
    scroll_delay_max: 7.0
  dcinside:
    enabled: true
    interval_minutes: 30
    gallery_id: "thesingularity"
    gallery_type: "mgallery"
    pages_to_scrape: 3
    request_delay_min: 1.5
    request_delay_max: 3.0

categories:
  - name: "AI"
    name_ko: "AI"
    color: "#4A90D9"
    keywords: ["AI", "인공지능", "LLM", "GPT", "Claude", "Gemini", "딥러닝", "머신러닝", "OpenAI", "Anthropic"]
  - name: "Semiconductor"
    name_ko: "반도체"
    color: "#E74C3C"
    keywords: ["반도체", "semiconductor", "칩", "TSMC", "삼성파운드리", "HBM", "GPU", "NVIDIA", "AMD"]
  - name: "Cloud"
    name_ko: "클라우드"
    color: "#2ECC71"
    keywords: ["클라우드", "cloud", "AWS", "Azure", "GCP", "데이터센터"]
  - name: "Startup"
    name_ko: "스타트업"
    color: "#F39C12"
    keywords: ["스타트업", "startup", "투자", "펀딩", "인수", "M&A"]
  - name: "BigTech"
    name_ko: "빅테크"
    color: "#9B59B6"
    keywords: ["Google", "Apple", "Meta", "Amazon", "Microsoft", "ByteDance"]
  - name: "Regulation"
    name_ko: "규제/정책"
    color: "#1ABC9C"
    keywords: ["규제", "정책", "법률", "regulation", "AI법"]

processing:
  model_filter: "claude-haiku-4-5-20250514"
  model_process: "claude-sonnet-4-5-20250514"
  batch_size_filter: 20
  batch_size_summarize: 15
  batch_size_categorize: 20
  use_batch_api: true
  min_importance_for_briefing: 0.4

briefing:
  daily_time: "06:30"
  max_items: 20
  include_stats: true

email:
  enabled: true
  to_addresses:
    - "user@example.com"

web:
  host: "0.0.0.0"
  port: 8000
  auto_refresh_seconds: 60

database:
  url: "sqlite+aiosqlite:///data/briefings.db"
  backup_dir: "data/backups"
  archive_days: 90

browser:
  headless: false
  profile_dir: "browser_data"
```

### .env.example

```ini
# API Keys
ANTHROPIC_API_KEY=sk-ant-your-key-here

# Email SMTP
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
EMAIL_FROM=your-email@gmail.com

# Database
DATABASE_URL=sqlite+aiosqlite:///data/briefings.db
```

### .gitignore

```gitignore
# Environment
.env

# Browser profiles (로그인 쿠키!)
browser_data/

# Database
data/*.db
data/backups/

# Logs
logs/

# Python
__pycache__/
*.pyc
.venv/
venv/

# IDE
.vscode/
.idea/
```

---

## 엔트리포인트 (main.py)

```python
import asyncio
import uvicorn

async def main():
    # 1. 설정 로드
    settings = Settings()
    config = load_yaml_config('config/settings.yaml')

    # 2. DB 초기화
    await init_db(settings.database_url)

    # 3. 브라우저 매니저 초기화
    browser_mgr = BrowserManager(config.browser)
    await browser_mgr.initialize()

    # 4. 수집기 초기화
    collectors = {
        'twitter': TwitterCollector(browser_mgr, config.collection.twitter),
        'threads': ThreadsCollector(browser_mgr, config.collection.threads),
        'linkedin': LinkedInCollector(browser_mgr, config.collection.linkedin),
        'dcinside': DCInsideCollector(config.collection.dcinside),
    }

    # 5. AI 파이프라인 초기화
    pipeline = ProcessingPipeline(settings.anthropic_api_key, config.processing)

    # 6. 브리핑 생성기 + 이메일 발송기
    briefing_gen = BriefingGenerator(config.briefing)
    email_sender = EmailSender(settings)

    # 7. 스케줄러 시작
    orchestrator = Orchestrator(config, collectors, pipeline, briefing_gen, email_sender)
    orchestrator.setup_jobs()
    orchestrator.start()

    # 8. 웹 서버 시작 (같은 이벤트 루프)
    app = create_app(config)
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8000))
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 의존성 (requirements.txt)

```
# Browser Automation
playwright==1.49.1
playwright-stealth==1.0.6

# HTTP Scraping
httpx==0.28.1
beautifulsoup4==4.12.3
lxml==5.3.0

# AI Processing
anthropic==0.42.0

# Web Framework
fastapi==0.115.6
uvicorn[standard]==0.34.0
jinja2==3.1.5
python-multipart==0.0.20

# Database
sqlalchemy[asyncio]==2.0.36
aiosqlite==0.20.0
alembic==1.14.1

# Scheduling
apscheduler==3.11.0

# Email
aiosmtplib==3.0.2

# Configuration & Security
pyyaml==6.0.2
python-dotenv==1.0.1
cryptography==44.0.0

# Utilities
pydantic==2.10.4
pydantic-settings==2.7.1
arrow==1.3.0
```

---

## 구현 순서 (10 Phase)

| Phase | 작업 | 핵심 산출물 |
|-------|------|------------|
| 1 | 프로젝트 기반 | 디렉토리, requirements.txt, .env, .gitignore, config 로더 |
| 2 | DB 모델 | SQLAlchemy ORM, 테이블 생성, 세션 관리 |
| 3 | DCInside 수집기 | HTTP 스크래핑 (가장 단순, 빠른 검증) |
| 4 | 브라우저 매니저 + X 수집기 | Playwright 세션, GraphQL 인터셉트 |
| 5 | Threads + LinkedIn 수집기 | Phase 4 패턴 재활용 |
| 6 | AI 파이프라인 | Claude API 연동, 필터/요약/분류 |
| 7 | 중복제거 + 브리핑 생성 | 토픽 통합, 브리핑 포매팅 |
| 8 | 이메일 전송 | SMTP, HTML 템플릿 |
| 9 | 스케줄러 | APScheduler 오케스트레이션, 헬스 모니터링 |
| 10 | 웹 대시보드 | FastAPI + Jinja2 + HTMX, 검색/필터/아카이브 |

---

## 주요 리스크 & 대응

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 플랫폼 HTML/API 구조 변경 | 수집 중단 | GraphQL 인터셉트 우선, 셀렉터를 config로 분리, 에러 즉시 알림 |
| 계정 차단 (특히 LinkedIn) | 피드 접근 불가 | 보수적 딜레이, 부계정 사용, RSS 폴백 고려 |
| 세션 만료 | 수집 실패 | 자동 감지 + 즉시 알림, 수동 재로그인 ~1분 |
| API 비용 초과 | 예산 초과 | Haiku/Batches API 활용, 일일 비용 추적+알림 설정 |
| DCInside 차단 | 갤러리 접근 불가 | User-Agent 로테이션, 모바일/데스크톱 전환, 딜레이 증가 |

---

## 검증 방법

1. **수집 테스트**: 각 수집기 개별 실행 → RawPost 데이터 정상 파싱 확인
2. **AI 파이프라인 테스트**: 수집 데이터로 필터→요약→분류 실행 → 결과 품질 검토
3. **브리핑 테스트**: `generate_daily_briefing()` 수동 호출 → HTML/텍스트 출력 확인
4. **이메일 테스트**: 테스트 이메일 발송 → 수신 확인 (Gmail App Password)
5. **통합 테스트**: 전체 파이프라인 1-2시간 가동 → 웹 대시보드에서 결과 확인
6. **스케줄러 테스트**: 로그에서 각 작업의 정상 실행 주기 확인
