# SNS Tech Briefing

SNS 알고리즘 피드에서 AI/반도체/클라우드 정보를 자동 수집하고, AI로 요약/분류하여 매일 아침 브리핑을 생성하는 개인 시스템.

---

## 🎯 특징

| 항목 | 설명 |
|------|------|
| **자동 수집** | X(Twitter), Threads, LinkedIn, DCInside 4개 플랫폼 |
| **AI 처리** | GPT-4o로 필터링 + 요약 + 분류 + 중요도 판단 |
| **매일 브리핑** | 오전 6:30 이메일 + 웹 대시보드 |
| **저장소** | Posts: 로컬 SQLite (무료, 1개월 자동 정리) / Briefings: Firebase |
| **비용** | **$0/월** (무료) |
| **웹 대시보드** | GitHub Pages 공개 + 로컬 FastAPI |

---

## 🏗️ 아키텍처

```
┌─────────────────────────────────────────┐
│   SNS 수집기 (Python, 로컬 노트북)      │
│  X · Threads · LinkedIn · DCInside      │
└──────────────┬──────────────────────────┘
               │ 수집 (10분마다)
               ▼
        ┌──────────────┐
        │   AI 처리    │
        │  (GPT-4o)    │
        └──┬───────┬───┘
           │       │
           ▼       ▼
    ┌─────────┐  ┌──────────────┐
    │ SQLite  │  │  Firestore   │
    │ (Posts) │  │ (Briefings)  │
    │로컬저장 │  │ 클라우드     │
    └────┬────┘  └──────┬───────┘
         │               │
         │               ▼
         │      ┌────────────────┐
         │      │ 웹 대시보드    │
         └──────┤ GitHub Pages   │
                │ (공개, 읽기전용)
                └────────────────┘
```

---

## 📊 데이터 흐름

```
1. 수집 (10분마다)
   Twitter, Threads, LinkedIn, DCInside
        ↓ (로컬 저장)
   Posts (SQLite)

2. AI 처리 (30분마다)
   필터링: 기술 관련성 판단 (관련 없으면 삭제)
   요약: 요약문 생성
   분류: 카테고리 + 키워드 추출
   채점: 중요도 점수 (0~1)
        ↓ (로컬 업데이트)
   Posts (SQLite)

3. 매일 브리핑 (오전 6:30)
   미브리핑 게시물 수집
   중복제거 + 토픽 통합
   HTML 브리핑 생성
        ↓ (클라우드 저장)
   Briefings (Firestore)
        ↓ (이메일 전송)
   사용자 메일함

4. 자동 정리 (매일 자정)
   1개월 이상 된 Posts 삭제
   SQLite 용량 유지
```

---

## 🛠️ 기술 스택

| 층 | 기술 |
|-----|------|
| **언어** | Python 3.12 |
| **브라우저 자동화** | Playwright (Chrome CDP) |
| **AI** | OpenAI (gpt-4o, gpt-4o-mini) |
| **Posts 저장소** | SQLite (로컬) |
| **Briefings 저장소** | Firebase Firestore |
| **웹 대시보드** | FastAPI (로컬) + GitHub Pages (공개) |
| **스케줄러** | APScheduler |
| **웹앱** | Vue.js 3 (단일 페이지) |

---

## 📦 수집 소스

| 소스 | 방식 | 주기 | 상태 |
|------|------|------|------|
| X (Twitter) | CDP + GraphQL 인터셉트 | 10분 | ✅ 활성 |
| Threads | CDP + GraphQL 인터셉트 | 10분 | ✅ 활성 |
| LinkedIn | CDP + DOM 파싱 | 10분 | ✅ 활성 |
| DCInside | CDP + BeautifulSoup | 180분 | ✅ 활성 |

---

## 🚀 빠른 시작

### 1️⃣ 사전 준비

```bash
# 의존성 설치
pip install -r requirements.txt

# Playwright 브라우저 설치
playwright install chromium
```

### 2️⃣ 환경 설정

`.env` 파일 생성 (프로젝트 루트):

```env
# OpenAI API
OPENAI_API_KEY=sk-your-key-here

# 이메일 (Gmail)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
EMAIL_FROM=your-email@gmail.com

# Firebase (새 프로젝트)
FIREBASE_CREDENTIAL_PATH=firebase-service-account.json
FIREBASE_PROJECT_ID=sns-algorithm-13b18

# SNS 자격증명 (자동 로그인)
TWITTER_USERNAME=your-twitter-handle
TWITTER_PASSWORD=your-password
THREADS_USERNAME=your-threads-username
THREADS_PASSWORD=your-password
LINKEDIN_EMAIL=your-email@linkedin.com
LINKEDIN_PASSWORD=your-password
```

### 3️⃣ Chrome 디버그 모드 실행

```bash
# Windows
"C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir="C:\chrome_temp"
```

**Chrome 열리면 다음에 로그인:**
- Twitter
- Threads
- LinkedIn
- DCInside (갤러리 개념글 탭)

### 4️⃣ 서버 시작

새 터미널에서:

```bash
python main.py serve
```

**자동으로 시작:**
- ✅ 10분마다 SNS 수집
- ✅ 30분마다 AI 처리
- ✅ 매일 오전 6:30 브리핑 생성 + 이메일
- ✅ 매일 자정 1개월 이상 데이터 삭제

### 5️⃣ 대시보드 접속

- **로컬**: http://localhost:8000
- **공개**: GitHub Pages URL (설정 필요)

---

## 📋 수동 실행 (선택)

```bash
# 즉시 수집 (서버 없이)
python main.py collect-now
python main.py collect-now twitter

# 즉시 AI 처리
curl -X POST http://localhost:8000/api/process/trigger

# 즉시 브리핑 생성
curl -X POST http://localhost:8000/api/briefing/generate

# 스케줄러 없이 웹만 실행
python main.py serve --no-scheduler
```

---

## 🗂️ 프로젝트 구조

```
sns_algorithm_data_collection/
├── src/
│   ├── domain/              # 도메인 엔티티
│   │   └── entities/
│   │       ├── post.py      # Post
│   │       └── briefing.py  # Briefing
│   ├── application/         # 비즈니스 로직
│   │   └── use_cases/
│   │       ├── collect_posts.py
│   │       ├── process_posts.py
│   │       ├── generate_briefing.py
│   │       └── scheduler.py
│   └── infrastructure/      # 기술 구현
│       ├── collectors/      # SNS 수집기
│       ├── database/
│       │   ├── repositories/
│       │   │   ├── post_repo_sqlite.py  ← 로컬 저장
│       │   │   └── briefing_repo.py     ← Firebase
│       │   └── firebase_client.py
│       ├── ai/              # OpenAI 처리
│       └── config/
├── docs/                    # 웹 대시보드
│   ├── index.html
│   ├── briefings.html
│   ├── posts.html
│   ├── js/
│   │   ├── firebase-config.js   ← 새 프로젝트 설정
│   │   ├── firestore-api.js
│   │   └── app.js
│   └── css/style.css
├── data/
│   └── posts.db             ← SQLite (자동 생성)
├── config/
│   └── settings.yaml        # 수집 설정
├── main.py                  # 진입점
├── requirements.txt         # 의존성
└── .env                     # 환경 변수 (git 제외)
```

---

## 🎨 웹 대시보드

### 탭 1: 대시보드
- 24시간 수집 통계
- 최신 브리핑
- 최근 게시물
- 최근 수집 상태

### 탭 2: 브리핑 아카이브
- 과거 브리핑 목록
- 상세 조회

### 탭 3: 게시물 탐색
- 플립 카드 UI
- 필터링 (소스, 카테고리)
- 검색
- 키워드 하이라이트

---

## 🏷️ 카테고리

| 카테고리 | 키워드 예시 |
|----------|------------|
| **AI** | AI, LLM, GPT, Claude, OpenAI, 딥러닝, 머신러닝 |
| **반도체** | TSMC, HBM, GPU, NVIDIA, 파운드리, 칩 |
| **클라우드** | AWS, Azure, GCP, 데이터센터, 서버 |
| **빅테크** | Google, Apple, Meta, Amazon, Microsoft |
| **스타트업** | 투자, 펀딩, M&A, 벤처, 인수 |
| **규제/정책** | AI법, EU, 독점, 정부, 규제 |
| **코딩** | 프로그래밍, GitHub, 오픈소스, React, DevOps |

---

## 💾 저장소 정책

### Posts (로컬 SQLite)
- **위치**: `data/posts.db`
- **용량**: 무제한 (로컬)
- **자동 정리**: 1개월 이상 데이터 매일 자정 삭제
- **비용**: $0

### Briefings (Firebase Firestore)
- **위치**: Cloud
- **용량**: 필요한 만큼 (크지 않음)
- **자동 정리**: 없음 (영구 보관)
- **비용**: 무료 (읽기만 함)

---

## 🔐 Firebase 설정

### Firestore 규칙

```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    // Briefings: 누구나 읽기 (웹 대시보드)
    match /briefings/{document=**} {
      allow read: if true;
      allow write: if false;
    }

    // 나머지는 전부 차단
    match /{document=**} {
      allow read, write: if false;
    }
  }
}
```

### 색인
- 자동 생성됨 (필요시 Firestore에서 제안)

---

## 🐛 문제 해결

### "Quota exceeded" 에러
- Firebase가 아닌 로컬 SQLite를 사용하므로 더이상 발생 안 함

### Chrome 연결 실패
```bash
# 포트 확인
lsof -i :9222  # Linux/Mac
netstat -ano | findstr :9222  # Windows

# Chrome 종료 후 재시작
```

### 수집 안 됨
1. Chrome이 디버그 포트 9222로 열려있는지 확인
2. SNS에 로그인되어 있는지 확인
3. 로그 확인: `python main.py collect-now twitter`

### 이메일 안 받음
1. Gmail 앱 비밀번호 확인 (2단계 인증 필수)
2. .env에 올바른 이메일 입력
3. 로그에서 SMTP 에러 확인

---

## 📈 성능

| 항목 | 성능 |
|------|------|
| 수집 속도 | 소스당 1-3분 |
| AI 처리 | 배치 15-20개 약 2-3분 |
| 브리핑 생성 | 5-10분 |
| 웹 로드 | < 1초 (로컬) |

---

## 📄 라이센스

MIT License - 개인 사용 목적

---

## 🙋 기여

개선 사항이 있으면 이슈를 열어주세요.

---

**마지막 업데이트**: 2026-03-12 (새 Firebase 프로젝트, SQLite 마이그레이션)
