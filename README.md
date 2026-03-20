# SNS Tech Briefing

SNS 알고리즘 피드에서 AI/반도체/클라우드 정보를 자동 수집하고, AI로 요약/분류하여 매일 아침 브리핑을 생성하는 개인 시스템.

---

## 특징

| 항목 | 설명 |
|------|------|
| **자동 수집** | X(Twitter), Threads, LinkedIn, DCInside 4개 플랫폼 |
| **AI 처리** | GPT-4o-mini로 필터링 + 요약 + 분류 + 중요도 판단 |
| **매일 브리핑** | 오전 9:00 이메일 발송 |
| **저장소** | Posts: 로컬 SQLite / Briefings: Firebase Firestore |
| **웹 대시보드** | GitHub Pages (공개) + 로컬 FastAPI + Cloudflare Tunnel |

---

## 아키텍처

```
┌─────────────────────────────────────────┐
│   SNS 수집기 (Python, 로컬)             │
│  X · Threads · LinkedIn · DCInside      │
└──────────────┬──────────────────────────┘
               │ 수집 (10분마다)
               ▼
        ┌──────────────┐
        │   AI 처리    │
        │ (GPT-4o-mini)│
        └──┬───────┬───┘
           │       │
           ▼       ▼
    ┌─────────┐  ┌──────────────┐
    │ SQLite  │  │  Firestore   │
    │ (Posts) │  │ (Briefings)  │
    └────┬────┘  └──────┬───────┘
         │               │
         ▼               ▼
  ┌─────────────────────────────┐
  │  FastAPI (localhost:8000)   │
  │  ↑ Cloudflare Tunnel        │
  └──────────────┬──────────────┘
                 │ REST API
                 ▼
        ┌─────────────────┐
        │  GitHub Pages   │
        │ (웹 대시보드)   │
        └─────────────────┘
```

---

## 데이터 흐름

```
1. 수집 (10분마다)
   Twitter, Threads, LinkedIn → SQLite
   DCInside → SQLite (60분마다)

2. AI 처리 (30분마다)
   필터링: 기술 관련성 판단 (무관 게시물 삭제)
   요약: 한국어 요약 생성
   분류: 카테고리 + 키워드 추출
   채점: 중요도 점수 (0~1)

3. 매일 브리핑 (오전 9:00)
   중요 게시물 수집 → 중복 제거 → HTML 생성
   → Firestore 저장 + 이메일 발송

4. 자동 정리 (매일 자정)
   1개월 이상 된 Posts 삭제
```

---

## 기술 스택

| 층 | 기술 |
|-----|------|
| **언어** | Python 3.12+ |
| **브라우저 자동화** | Playwright (Chrome CDP) |
| **AI** | OpenAI GPT-4o-mini |
| **Posts 저장소** | SQLite (로컬, 비용 $0) |
| **Briefings 저장소** | Firebase Firestore |
| **웹 서버** | FastAPI + uvicorn |
| **외부 접근** | Cloudflare Tunnel (고정 도메인) |
| **웹 대시보드** | GitHub Pages (정적) |
| **스케줄러** | APScheduler |

---

## 수집 소스

| 소스 | 방식 | 주기 |
|------|------|------|
| X (Twitter) | CDP + GraphQL 인터셉트 | 10분 |
| Threads | CDP + GraphQL 인터셉트 | 10분 |
| LinkedIn | CDP + DOM 파싱 | 10분 |
| DCInside | HTTP + BeautifulSoup | 60분 |

---

## 빠른 시작

### 1. 의존성 설치

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. 환경 변수 설정

`.env` 파일 생성:

```env
# OpenAI
OPENAI_API_KEY=sk-...

# 이메일 (Gmail 앱 비밀번호)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your@gmail.com
SMTP_PASSWORD=your-app-password
EMAIL_FROM=your@gmail.com

# Firebase
FIREBASE_CREDENTIAL_PATH=firebase-service-account.json
FIREBASE_PROJECT_ID=your-project-id

# SNS 계정
TWITTER_USERNAME=your-handle
TWITTER_PASSWORD=your-password
THREADS_USERNAME=your-username
THREADS_PASSWORD=your-password
LINKEDIN_EMAIL=your@email.com
LINKEDIN_PASSWORD=your-password
```

### 3. Chrome 디버그 모드 실행

```bash
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

Chrome에서 X, Threads, LinkedIn, DCInside에 로그인.

### 4. Cloudflare Tunnel 실행

```bash
cloudflared tunnel run --url http://localhost:8000 sns-briefing
```

### 5. 서버 시작

```bash
python main.py serve
```

---

## 수동 실행

```bash
# 즉시 수집
python main.py collect-now
python main.py collect-now twitter

# AI 처리 트리거
curl -X POST http://localhost:8000/api/process/trigger

# 브리핑 생성
curl -X POST http://localhost:8000/api/briefing/generate

# 스케줄러 없이 웹만 실행
python main.py serve --no-scheduler
```

---

## 프로젝트 구조

```
sns_algorithm_data_collection/
├── src/
│   ├── domain/              # 도메인 엔티티 (Post, Briefing, Category 등)
│   ├── application/
│   │   └── use_cases/       # 비즈니스 로직
│   │       ├── collect_posts.py
│   │       ├── process_posts.py
│   │       ├── generate_briefing.py
│   │       └── scheduler.py
│   └── infrastructure/
│       ├── collectors/      # SNS 수집기
│       ├── database/
│       │   └── repositories/
│       │       ├── post_repo_sqlite.py          # Posts (로컬)
│       │       ├── collection_run_repo_sqlite.py # 수집 이력 (로컬)
│       │       ├── category_repo_memory.py       # 카테고리 (인메모리)
│       │       └── briefing_repo.py              # Briefings (Firebase)
│       ├── ai/              # OpenAI 처리
│       └── config/
├── docs/                    # GitHub Pages 웹 대시보드
│   ├── index.html           # 대시보드
│   ├── briefings.html       # 브리핑 아카이브
│   ├── posts.html           # 게시물 탐색
│   ├── js/
│   │   ├── config.js        # API URL 설정 (Cloudflare Tunnel)
│   │   ├── firestore-api.js # API 호출
│   │   └── app.js           # UI 렌더링
│   └── css/style.css
├── data/
│   └── posts.db             # SQLite (자동 생성)
├── config/
│   └── settings.yaml        # 수집/AI/브리핑 설정
├── main.py
├── requirements.txt
└── .env                     # 환경 변수 (git 제외)
```

---

## 카테고리

| 카테고리 | 키워드 예시 |
|----------|------------|
| AI | AI, LLM, GPT, Claude, OpenAI, 딥러닝 |
| 반도체 | TSMC, HBM, GPU, NVIDIA, 파운드리 |
| 클라우드 | AWS, Azure, GCP, 데이터센터 |
| 빅테크 | Google, Apple, Meta, Amazon, Microsoft |
| 스타트업 | 투자, 펀딩, M&A, 벤처 |
| 규제/정책 | AI법, EU, 독점, 정부 |
| 코딩 | GitHub, 오픈소스, React, DevOps |

---

## 저장소 정책

| 저장소 | 데이터 | 정리 정책 |
|--------|--------|-----------|
| SQLite (로컬) | Posts, 수집 이력 | 1개월 이상 자동 삭제 |
| Firebase Firestore | Briefings | 영구 보관 |

---

## Firebase Firestore 규칙

```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /briefings/{document=**} {
      allow read: if true;
      allow write: if false;
    }
    match /{document=**} {
      allow read, write: if false;
    }
  }
}
```

---

## 문제 해결

**Chrome 연결 실패**
```bash
netstat -ano | findstr :9222  # 포트 확인
```

**수집 안 됨**
1. Chrome 디버그 포트 9222 확인
2. SNS 로그인 상태 확인
3. `python main.py collect-now twitter` 로 로그 확인

**이메일 안 받음**
1. Gmail 앱 비밀번호 사용 (2단계 인증 필수)
2. `.env` 이메일 설정 확인

**GitHub Pages에서 데이터 안 뜸**
1. `docs/js/config.js`의 `API_BASE_URL` 확인
2. Cloudflare Tunnel 실행 여부 확인
3. 로컬 서버(`python main.py serve`) 실행 여부 확인

---

**마지막 업데이트**: 2026-03-20
