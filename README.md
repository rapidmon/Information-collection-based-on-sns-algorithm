# SNS Tech Briefing

SNS 알고리즘 피드에서 AI/반도체/테크 정보를 자동 수집하고, AI로 요약/분류하여 매일 아침 브리핑을 생성하는 시스템.

## 수집 소스

| 소스 | 방식 | 주기 |
|------|------|------|
| X (Twitter) | Playwright + GraphQL 인터셉트 | 10분 |
| Threads | Playwright + GraphQL 인터셉트 | 10분 |
| LinkedIn | Playwright DOM 파싱 | 60분 |
| DCInside | httpx + BeautifulSoup (HTTP) | 180분 |

## 기술 스택

- **언어**: Python 3.11+
- **브라우저 자동화**: Playwright + playwright-stealth
- **AI 처리**: OpenAI (gpt-4o-mini 필터링, gpt-4o 요약/분류)
- **DB**: Firebase Firestore (클라우드)
- **웹 대시보드**: FastAPI + Jinja2 + Tailwind CSS (로컬), GitHub Pages (공개)
- **스케줄러**: APScheduler
- **이메일**: aiosmtplib

## 프로젝트 구조

```
├── config/settings.yaml          # 수집주기, 카테고리, AI 모델 등 설정
├── main.py                       # 엔트리포인트
├── src/
│   ├── domain/                   # 엔티티, 리포지토리 인터페이스, 서비스 인터페이스
│   ├── application/              # 유스케이스 (수집, 처리, 브리핑, 이메일, 스케줄러)
│   ├── infrastructure/           # 구현체
│   │   ├── collectors/           # SNS 수집기 + 브라우저 매니저
│   │   ├── ai/                   # OpenAI 프로세서
│   │   ├── database/             # Firebase Firestore 리포지토리
│   │   ├── delivery/             # 이메일 발송, 브리핑 빌더
│   │   └── config/               # 설정 로더, DI 컨테이너
│   └── presentation/web/         # FastAPI 앱, 라우트, HTML 템플릿
├── docs/                         # GitHub Pages 정적 대시보드
│   ├── index.html                # 메인 (통계 + 최신 브리핑 + 최근 게시물)
│   ├── posts.html                # 게시물 탐색 (필터, 검색, 페이지네이션)
│   ├── briefings.html            # 브리핑 아카이브 + 상세
│   ├── js/                       # Firebase SDK 초기화, Firestore API, UI 유틸
│   └── css/                      # 커스텀 스타일
└── tests/
```

## 설치 및 실행

```bash
# 의존성 설치
pip install -r requirements.txt
playwright install chromium

# 환경 변수 설정 (.env 파일 생성)
# OPENAI_API_KEY, SMTP 설정, Firebase 서비스 계정 경로 등

# 실행
python main.py
```

## GitHub Pages 대시보드

Firebase JS SDK로 Firestore를 직접 읽는 정적 대시보드. 별도 서버 없이 어디서든 접속 가능.

**배포**: Repository Settings → Pages → Source: `main` 브랜치, `/docs` 폴더

## 카테고리

| 카테고리 | 키워드 예시 |
|----------|------------|
| AI | AI, LLM, GPT, Claude, OpenAI, 딥러닝 |
| 반도체 | TSMC, HBM, GPU, NVIDIA, 파운드리 |
| 클라우드 | AWS, Azure, GCP, 데이터센터 |
| 스타트업 | 투자, 펀딩, M&A, 벤처 |
| 빅테크 | Google, Apple, Meta, Amazon, Microsoft |
| 규제/정책 | AI법, EU, 독점, 정부 정책 |
