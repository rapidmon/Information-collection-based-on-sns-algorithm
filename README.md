# SNS Tech Briefing

SNS(X, Threads, LinkedIn, DCInside) 알고리즘 피드에서 기술 뉴스를 자동 수집하고, AI가 요약/분류하여 **매일 아침 이메일 브리핑**을 보내주는 시스템입니다.

평소 팔로우하는 정보성 계정들의 피드를 AI가 읽고, 중요한 것만 골라 한국어로 정리해줍니다.

---

## 이런 분에게 유용합니다

- X(트위터), Threads 등에서 기술 트렌드를 팔로우하고 있지만 매번 확인하기 힘든 분
- 정보성 SNS 계정을 많이 팔로우해뒀는데 피드를 놓치는 게 아까운 분
- 매일 아침 핵심 기술 뉴스만 이메일로 받아보고 싶은 분

---

## 동작 방식

```
1. 수집 (10분마다)
   로그인된 Chrome에서 SNS 피드를 자동 스크롤하며 게시물 수집

2. AI 분석 (30분마다)
   GPT-4o-mini가 각 게시물을 분석:
   - 기술 관련 여부 필터링 (일상/밈/잡담 제거)
   - 한국어 요약 생성
   - 카테고리 분류 (AI, 반도체, 클라우드 등 7개)
   - 중요도 점수 산정 (뉴스 가치 + 실무 활용도)

3. 브리핑 발송 (매일 오전 9시)
   중요도 높은 게시물을 모아 중복 제거 후 이메일로 발송
```

---

## 시작하기

### 필요한 것

- **Python 3.12 이상** ([다운로드](https://www.python.org/downloads/))
- **Google Chrome** (이미 설치되어 있을 가능성이 높음)
- **OpenAI API 키** ([발급 방법](https://platform.openai.com/api-keys)) — 월 $2~5 수준
- **Gmail 계정** — 브리핑 이메일 발송용

### 1단계: 프로젝트 다운로드

```bash
git clone https://github.com/your-username/sns_algorithm_data_collection.git
cd sns_algorithm_data_collection
```

### 2단계: 패키지 설치

```bash
pip install -r requirements.txt
playwright install chromium
```

### 3단계: 환경 변수 설정

프로젝트 폴더에 `.env` 파일을 만들고 아래 내용을 채워주세요.

```env
# OpenAI API 키 (https://platform.openai.com/api-keys 에서 발급)
OPENAI_API_KEY=sk-...

# Gmail 이메일 발송 설정
# Gmail 앱 비밀번호 발급 방법:
#   1. Google 계정 → 보안 → 2단계 인증 활성화
#   2. Google 계정 → 보안 → 앱 비밀번호 → 새 비밀번호 생성
#   3. 생성된 16자리 비밀번호를 아래에 입력
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your@gmail.com
SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx
EMAIL_FROM=your@gmail.com

# Firebase (웹 대시보드를 쓰지 않으면 생략 가능)
# FIREBASE_CREDENTIAL_PATH=firebase-service-account.json
# FIREBASE_PROJECT_ID=your-project-id

# SNS 계정 정보 (수집할 플랫폼만 입력)
TWITTER_USERNAME=your-handle
TWITTER_PASSWORD=your-password
THREADS_USERNAME=your-username
THREADS_PASSWORD=your-password
LINKEDIN_EMAIL=your@email.com
LINKEDIN_PASSWORD=your-password
```

### 4단계: 브리핑 받을 이메일 주소 설정

`config/settings.yaml` 파일에서 브리핑을 받을 이메일 주소를 수정하세요.

```yaml
email:
  enabled: true
  to_addresses:
    - "your-email@example.com"
```

### 5단계: Chrome 디버그 모드 실행

**중요: 반드시 아래 명령어 그대로 실행하세요.** `--user-data-dir`로 기본 Chrome과 분리된 전용 세션을 쓰고(기존에 켜둔 일반 Chrome과 충돌 없음), `--restore-last-session`으로 재실행 시 로그인·탭이 복원됩니다.

> ⚠️ **먼저 이 명령으로 띄운 디버그 Chrome이 이미 떠 있으면 그걸 그대로 쓰세요.** 수집기는 이 창의 **기존 탭을 재사용**하므로, 서버가 새 창을 계속 띄우지 않습니다. (디버그 Chrome이 완전히 닫혀 CDP가 끊긴 경우에만 자동으로 한 번 재실행합니다.)

```bash
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\chrome_temp" --restore-last-session --start-minimized --disable-backgrounding-occluded-windows --disable-background-timer-throttling --disable-renderer-backgrounding --disable-extensions --disable-features=Translate,MediaRouter --disable-background-networking --js-flags="--max-old-space-size=2048"
```

> 추가 플래그: `--restore-last-session`(세션 복원), `--start-minimized`(최소화 시작), 나머지는 메모리·throttling 절약용(확장 비활성, 백그라운드 네트워킹 차단, V8 힙 상한 2048MB). 힙 상한을 512MB로 두면 X·LinkedIn 같은 무거운 페이지에서 GC 폭주·렌더러 불안정으로 CDP가 먹통이 될 수 있어 2048MB로 완화했습니다.

> Mac/Linux의 경우:
> ```bash
> # Mac
> /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222 --user-data-dir="/tmp/chrome_temp" --restore-last-session --disable-extensions --disable-features=Translate,MediaRouter --disable-background-networking --js-flags="--max-old-space-size=2048"
>
> # Linux
> google-chrome --remote-debugging-port=9222 --user-data-dir="/tmp/chrome_temp" --restore-last-session --disable-extensions --disable-features=Translate,MediaRouter --disable-background-networking --js-flags="--max-old-space-size=2048"
> ```

이 Chrome 창에서 수집하고 싶은 SNS에 로그인하세요:
- **X (Twitter)** — https://x.com 에 로그인
- **Threads** — https://www.threads.net 에 로그인
- **LinkedIn** — https://www.linkedin.com 에 로그인

> **팁**: 이 Chrome은 수집 전용이므로, 정보를 잘 올려주는 계정들만 팔로우한 계정으로 로그인하면 피드 품질이 좋아집니다.

### 6단계: 서버 시작

```bash
python main.py serve
```

끝입니다! 서버가 시작되면 자동으로:
- 10분마다 SNS 피드 수집
- 30분마다 AI 분석 처리
- 매일 오전 9시에 브리핑 이메일 발송

---

## 수집 소스 커스터마이징

### 특정 플랫폼만 사용하기

`config/settings.yaml`에서 사용하지 않을 플랫폼을 비활성화할 수 있습니다.

```yaml
collection:
  twitter:
    enabled: true     # 사용
  threads:
    enabled: true     # 사용
  linkedin:
    enabled: false    # 미사용
  dcinside:
    enabled: false    # 미사용
```

### DCInside 갤러리 변경

DCInside는 특정 갤러리의 게시물을 수집합니다. 관심 갤러리로 변경할 수 있습니다.

```yaml
  dcinside:
    enabled: true
    gallery_id: "thesingularity"    # 갤러리 ID
    gallery_type: "mgallery"        # mgallery(마이너) 또는 gallery(일반)
```

### 수집 주기 변경

```yaml
collection:
  twitter:
    interval_minutes: 10    # 기본 10분, 원하는 간격으로 변경
```

---

## 카테고리

수집된 게시물은 AI가 자동으로 7개 카테고리로 분류합니다.

| 카테고리 | 내용 |
|----------|------|
| **AI** | 인공지능, LLM, GPT, Claude, 딥러닝 |
| **반도체** | TSMC, HBM, GPU, NVIDIA, 파운드리 |
| **클라우드** | AWS, Azure, GCP, 데이터센터 |
| **빅테크** | Google, Apple, Meta, Amazon, Microsoft |
| **스타트업** | 투자, 펀딩, M&A, 벤처 |
| **규제/정책** | AI법, EU, 독점, 정부 정책 |
| **코딩** | GitHub, 오픈소스, React, DevOps |

카테고리를 추가/수정하려면 `config/settings.yaml`의 `categories` 항목을 편집하세요.

---

## 수동 실행

자동 스케줄러 외에 직접 실행할 수도 있습니다.

```bash
# 즉시 수집 (모든 플랫폼)
python main.py collect-now

# 특정 플랫폼만 수집
python main.py collect-now twitter

# AI 처리 수동 트리거
curl -X POST http://localhost:8000/api/process/trigger

# 브리핑 즉시 생성
curl -X POST http://localhost:8000/api/briefing/generate

# 스케줄러 없이 웹 서버만 실행
python main.py serve --no-scheduler
```

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

## 기술 스택

| 항목 | 기술 |
|------|------|
| 언어 | Python 3.12+ |
| 브라우저 자동화 | Playwright (Chrome CDP) |
| AI | OpenAI GPT-4o-mini |
| 게시물 저장소 | SQLite (로컬, 비용 $0) |
| 브리핑 저장소 | Firebase Firestore (선택) |
| 웹 서버 | FastAPI + uvicorn |
| 외부 접근 | Cloudflare Tunnel (선택) |
| 웹 대시보드 | GitHub Pages (선택) |
| 스케줄러 | APScheduler |

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
│       ├── ai/              # OpenAI 프롬프트 및 처리
│       └── config/
├── docs/                    # GitHub Pages 웹 대시보드
├── data/
│   └── posts.db             # SQLite (자동 생성)
├── config/
│   └── settings.yaml        # 수집/AI/브리핑 설정
├── main.py
├── requirements.txt
└── .env                     # 환경 변수 (git 제외)
```

---

## 웹 대시보드 (선택)

브리핑을 이메일 외에 웹에서도 확인하고 싶다면 Firebase + Cloudflare Tunnel + GitHub Pages를 추가 설정할 수 있습니다. 이메일 브리핑만으로 충분하다면 이 단계는 건너뛰어도 됩니다.

### Firebase 설정

1. [Firebase Console](https://console.firebase.google.com/)에서 프로젝트 생성
2. Firestore Database 생성
3. 프로젝트 설정 → 서비스 계정 → 새 비공개 키 생성 → JSON 파일 다운로드
4. 다운로드한 JSON 파일을 프로젝트 폴더에 넣고 `.env`에 경로 설정

### Cloudflare Tunnel 설정

라우팅(`api.cnvjb.uk → localhost:8000`)은 Cloudflare 대시보드(원격 관리형 터널)에 설정돼 있으므로, 터널은 이름으로 실행만 하면 됩니다.

```bash
# Cloudflare Tunnel 설치 후
cloudflared tunnel run sns-briefing
```

> 터널은 `localhost:8000`으로 전달만 하므로 `python main.py serve`가 함께 떠 있어야 합니다.
> 매번 켜기 번거로우면 윈도우 서비스로 등록해 자동 실행할 수 있습니다:
> ```bash
> cloudflared tunnel token sns-briefing        # 토큰 출력
> cloudflared service install <출력된_토큰>      # 관리자 권한, 부팅 시 자동 시작
> ```

---

## 문제 해결

### Chrome 연결 실패

```bash
# 포트 9222가 열려있는지 확인
netstat -ano | findstr :9222
```

- Chrome이 디버그 모드로 실행 중인지 확인하세요
- **반드시** `--user-data-dir="C:\chrome_temp"` 옵션과 함께 실행해야 합니다

### 수집이 안 됨

1. Chrome 디버그 모드가 포트 9222에서 실행 중인지 확인
2. Chrome에서 해당 SNS에 로그인되어 있는지 확인
3. `python main.py collect-now twitter`로 로그를 확인

### 이메일이 안 옴

1. Gmail **2단계 인증**이 활성화되어 있는지 확인
2. **앱 비밀번호**를 사용하고 있는지 확인 (일반 비밀번호 아님)
3. `.env`의 이메일 설정값 확인
4. `config/settings.yaml`의 `to_addresses`가 올바른지 확인

---

## 저장소 정책

| 저장소 | 데이터 | 정리 정책 |
|--------|--------|-----------|
| SQLite (로컬) | 게시물, 수집 이력 | 1개월 이상 자동 삭제 |
| Firebase Firestore | 브리핑 | 영구 보관 |

---

**마지막 업데이트**: 2026-07-02
