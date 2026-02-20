# SNS Tech Briefing — 전체 아키텍처 & 플로우

## 전체 데이터 플로우

```
[SNS 피드]                    [AI 처리]                    [대시보드]

Twitter ──┐                 ┌─ 관련성 필터 ─┐              GitHub Pages
Threads ──┤  수집 (CDP)     │  (gpt-4o-mini) │  관련 없음   (정적 HTML)
LinkedIn ─┤ ──────────────→ │               ├──→ 제외       ├─ index.html
DCInside ─┘  Firestore      │  관련 있음     │              ├─ posts.html
             posts 저장     └───────┬───────┘              └─ briefings.html
                                    │                          │
                                    ▼                          │
                            ┌─ 요약 + 분류 ─┐                 │
                            │  (gpt-4o)     │                 │
                            │  - summary    │    Firestore ◄──┘
                            │  - category   │    (읽기 전용)
                            │  - importance │
                            └───────┬───────┘
                                    │
                                    ▼
                            ┌─ 브리핑 생성 ──┐
                            │  중복제거+통합  │
                            │  HTML 렌더링   │
                            └───────┬───────┘
                                    │
                              ┌─────┴─────┐
                              ▼           ▼
                          이메일 발송   briefings
                          (SMTP)      컬렉션 저장
```

## 기능 목록

### 1. 데이터 수집

| 소스 | 방식 | 주기 | 파일 |
|------|------|------|------|
| Twitter | CDP + GraphQL 인터셉트 | 10분 | `collectors/twitter_collector.py` |
| Threads | CDP + GraphQL 인터셉트 | 10분 | `collectors/threads_collector.py` |
| LinkedIn | CDP + DOM 파싱 | 60분 | `collectors/linkedin_collector.py` |
| DCInside | CDP + BeautifulSoup | 180분 | `collectors/dcinside_collector.py` |

- 모든 수집기는 사용자의 Chrome(CDP 9222 포트)에 연결
- 수집 결과는 Firestore `posts` 컬렉션에 저장
- 수집 기록은 `collection_runs` 컬렉션에 기록

### 2. AI 처리 파이프라인

```
미처리 게시물 (summary == null)
    │
    ▼
[1단계] 관련성 필터 + 요약 (gpt-4o-mini, 20건/배치)
    │   → is_relevant: true/false
    │   → summary: 한국어 2-3문장
    │   → language: ko/en
    │
    ▼ (is_relevant == true인 것만)
[2단계] 카테고리 분류 + 중요도 (gpt-4o, 20건/배치)
    │   → category_names: ["AI", "Semiconductor", ...]
    │   → importance_score: 0.0 ~ 1.0
    │
    ▼
Firestore posts 업데이트
```

- 스케줄: 매시 :05분 자동 실행
- 수동: `POST /api/process/trigger`
- 관련 없는 게시물(`is_relevant=false`)은 대시보드에서 제외

### 3. 카테고리 (7개)

| 카테고리 | 이름 | 색상 | 키워드 예시 |
|----------|------|------|------------|
| AI | AI | `#4A90D9` | AI, LLM, GPT, Claude, 딥러닝 |
| Semiconductor | 반도체 | `#E74C3C` | TSMC, HBM, GPU, NVIDIA |
| Cloud | 클라우드 | `#2ECC71` | AWS, Azure, GCP, 데이터센터 |
| Startup | 스타트업 | `#F39C12` | 투자, 펀딩, M&A, 벤처 |
| BigTech | 빅테크 | `#9B59B6` | Google, Apple, Meta, Microsoft |
| Regulation | 규제/정책 | `#1ABC9C` | 규제, AI법, EU, 정부 정책 |
| Coding | 코딩 | `#E67E22` | 프로그래밍, GitHub, 오픈소스, React |

### 4. 브리핑 생성

```
매일 06:30 KST 자동 실행 (수동: POST /api/briefing/generate)
    │
    ▼
최근 24시간 관련 게시물 수집
    │
    ▼
중복제거 + 토픽 통합 (gpt-4o)
    │
    ▼
HTML/텍스트 브리핑 문서 생성
    │
    ├──→ Firestore briefings 컬렉션 저장
    └──→ 이메일 발송 (SMTP)
```

### 5. 대시보드

#### FastAPI (로컬, localhost:8000)
| 경로 | 기능 |
|------|------|
| `/` | 메인: 통계 + 최신 브리핑 + 수집 현황 + 최근 게시물 |
| `/posts` | 게시물 탐색: 소스/카테고리 필터, 검색 |
| `/briefings` | 브리핑 아카이브 |
| `/briefings/{id}` | 브리핑 상세 |
| `/status` | 시스템 상태 (수집기별 성공/실패) |

#### GitHub Pages (공개, /docs)
| 파일 | 기능 |
|------|------|
| `index.html` | 메인: 소스별 통계 + 최신 브리핑 + 최근 게시물 |
| `posts.html` | 게시물 탐색: 소스/카테고리 필터, 검색, 페이지네이션 |
| `briefings.html` | 브리핑 아카이브 + 상세 펼침 |

- Firebase JS SDK로 Firestore 직접 읽기 (서버 불필요)
- **AI 처리 완료된 게시물만 표시** (`is_relevant == true`)

### 6. API 엔드포인트

| 메서드 | 경로 | 기능 |
|--------|------|------|
| `POST` | `/api/collect/trigger/{source}` | 수동 수집 |
| `POST` | `/api/process/trigger` | 수동 AI 처리 |
| `POST` | `/api/briefing/generate` | 수동 브리핑 생성 |
| `GET` | `/api/posts/search` | 게시물 검색 |
| `GET` | `/api/briefings/latest` | 최신 브리핑 |
| `GET` | `/api/stats` | 수집 통계 |

### 7. 스케줄러

| 작업 | 주기 | 시간 |
|------|------|------|
| Twitter 수집 | 10분 | 상시 |
| Threads 수집 | 10분 | 상시 |
| LinkedIn 수집 | 60분 | 상시 |
| DCInside 수집 | 180분 | 상시 |
| AI 처리 | 매시 | :05분 |
| 일일 브리핑 | 1일 1회 | 06:30 KST |
| 헬스체크 | 5분 | 상시 |

## Firestore 컬렉션

| 컬렉션 | 용도 | 주요 필드 |
|--------|------|----------|
| `posts` | 수집된 게시물 | source, content_text, summary, category_names, importance_score, is_relevant |
| `briefings` | 생성된 브리핑 | title, content_html, generated_at, items[] |
| `categories` | 카테고리 마스터 | name, name_ko, color, keywords |
| `collection_runs` | 수집 실행 기록 | source, status, posts_collected, started_at |

## 프로젝트 구조

```
sns_algorithm_data_collection/
├── main.py                           # 엔트리포인트
├── config/settings.yaml              # 전체 설정
├── src/
│   ├── domain/                       # 엔티티, 인터페이스
│   │   ├── entities/                 # Post, Briefing, Category, CollectionRun
│   │   ├── repositories/            # 리포지토리 인터페이스
│   │   └── services/                # AI, 수집, 브리핑, 알림 인터페이스
│   ├── application/use_cases/        # 비즈니스 로직
│   │   ├── collect_posts.py          # 수집 실행
│   │   ├── process_posts.py          # AI 처리
│   │   ├── generate_briefing.py      # 브리핑 생성
│   │   ├── send_briefing.py          # 이메일 발송
│   │   └── scheduler.py             # 스케줄러 오케스트레이션
│   ├── infrastructure/               # 구현체
│   │   ├── collectors/               # Twitter, Threads, LinkedIn, DCInside
│   │   ├── ai/                       # OpenAI 프로세서 + 프롬프트
│   │   ├── database/repositories/    # Firestore 리포지토리
│   │   ├── delivery/                 # 이메일, 브리핑 빌더
│   │   └── config/                   # 설정, DI 컨테이너
│   └── presentation/web/             # FastAPI 앱
│       ├── routes/                   # dashboard, api
│       └── templates/                # Jinja2 HTML
├── docs/                             # GitHub Pages 정적 대시보드
│   ├── index.html / posts.html / briefings.html
│   ├── js/                           # Firebase SDK, Firestore API, UI
│   └── css/                          # 스타일
└── tests/
```
