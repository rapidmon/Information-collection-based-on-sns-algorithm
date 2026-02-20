# SNS Tech Briefing

SNS 알고리즘 피드에서 AI/반도체/테크 정보를 자동 수집하고, AI로 요약/분류하여 매일 아침 브리핑을 생성하는 시스템.

## 수집 소스

| 소스 | 방식 | 주기 |
|------|------|------|
| X (Twitter) | CDP + GraphQL 인터셉트 | 10분 |
| Threads | CDP + GraphQL 인터셉트 | 10분 |
| LinkedIn | CDP + DOM 파싱 | 10분 |
| DCInside | CDP + BeautifulSoup | 180분 |

## 기술 스택

- **언어**: Python 3.12
- **브라우저 자동화**: Playwright (CDP로 사용자 Chrome에 연결)
- **AI 처리**: OpenAI gpt-4o-mini (필터링 + 분류 모두)
- **DB**: Firebase Firestore
- **웹 대시보드**: FastAPI (로컬) + GitHub Pages (공개)
- **스케줄러**: APScheduler
- **이메일**: aiosmtplib

## 실행 방법

### 1. 사전 준비

```bash
# Python 3.12 의존성 설치
py -3.12 -m pip install -r requirements.txt

# Playwright 브라우저 설치
py -3.12 -m playwright install chromium
```

### 2. 환경 변수 설정

프로젝트 루트에 `.env` 파일 생성:

```
OPENAI_API_KEY=sk-your-key-here

SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
EMAIL_FROM=your-email@gmail.com

FIREBASE_CREDENTIAL_PATH=firebase-service-account.json
FIREBASE_PROJECT_ID=your-project-id
```

### 3. Chrome 디버그 모드로 실행

**수집기가 사용자의 Chrome 브라우저에 CDP로 연결하므로, Chrome을 디버그 포트를 열고 실행해야 합니다.**

```bash
# Windows
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

Chrome이 열리면 **Twitter, Threads, LinkedIn에 로그인**해둡니다.
DCInside 특이점이온다 갤러리 개념글 탭도 하나 열어둡니다.

### 4. 서버 시작

Chrome이 디버그 모드로 켜진 상태에서 **새 터미널**을 열고:

```bash
py -3.12 main.py serve
```

이제 자동으로:
- 10분마다 Twitter, Threads, LinkedIn, DCInside에서 게시물 수집
- 10분마다 미처리 게시물 AI 분류 (관련 없는 건 자동 삭제)
- 매일 오전 9시 브리핑 생성 + 이메일 발송

### 5. 대시보드 접속

- **로컬**: http://localhost:8000
- **GitHub Pages**: Repository Settings → Pages에서 설정한 URL

### 6. 수동 실행 (선택)

```bash
# 즉시 수집만 (서버 없이)
py -3.12 main.py collect-now                # 전체 소스
py -3.12 main.py collect-now twitter         # 트위터만

# 서버 켜진 상태에서 API로 수동 트리거
curl -X POST http://localhost:8000/api/process/trigger    # AI 처리
curl -X POST http://localhost:8000/api/briefing/generate  # 브리핑 생성
```

### 7. 스케줄러 없이 웹만 (선택)

```bash
py -3.12 main.py serve --no-scheduler
```

## 데이터 처리 플로우

```
수집 (10분마다)
  → AI 필터 + 요약 (관련 없으면 DB에서 삭제)
  → AI 카테고리 분류 + 중요도 채점
  → 대시보드에 표시

매일 오전 9시
  → 미브리핑 게시물 수집
  → 중복제거 + 토픽 통합
  → 브리핑 HTML 생성
  → 이메일 발송
```

## 카테고리

| 카테고리 | 키워드 예시 |
|----------|------------|
| AI | AI, LLM, GPT, Claude, OpenAI, 딥러닝 |
| 반도체 | TSMC, HBM, GPU, NVIDIA, 파운드리 |
| 클라우드 | AWS, Azure, GCP, 데이터센터 |
| 스타트업 | 투자, 펀딩, M&A, 벤처 |
| 빅테크 | Google, Apple, Meta, Amazon, Microsoft |
| 규제/정책 | AI법, EU, 독점, 정부 정책 |
| 코딩 | 프로그래밍, GitHub, 오픈소스, React, DevOps |

## 프로젝트 구조

상세 아키텍처는 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 참조.
