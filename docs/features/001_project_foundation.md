# Feature 001: 프로젝트 기반 + 클린 아키텍처 골격

## 개요
프로젝트의 클린 아키텍처 기반 골격을 구축한다.
모든 후속 기능이 이 구조 위에 구현된다.

## 아키텍처 레이어

```
┌─────────────────────────────────────┐
│       Presentation (Web, CLI)       │  ← 사용자 인터페이스
├─────────────────────────────────────┤
│    Application (Use Cases, DTO)     │  ← 비즈니스 로직 오케스트레이션
├─────────────────────────────────────┤
│    Domain (Entities, Protocols)     │  ← 핵심 비즈니스 규칙 (의존성 없음)
├─────────────────────────────────────┤
│  Infrastructure (DB, API, Scraper)  │  ← 외부 시스템 연동
└─────────────────────────────────────┘
```

### 의존성 방향: 바깥 → 안쪽 (Domain은 아무것도 의존하지 않음)

## 핵심 원칙

1. **의존성 역전 (DIP)**: Domain 레이어에 Protocol(인터페이스)을 정의하고,
   Infrastructure가 이를 구현. Use Case는 Protocol에만 의존.
2. **도메인 엔티티 독립**: `Post`, `Briefing` 등은 순수 dataclass.
   ORM 모델과 분리되며 `mapper.py`가 양방향 변환 담당.
3. **Use Case 단위 비즈니스 로직**: 각 유즈케이스가 하나의 비즈니스 작업을 담당.

## 디렉토리 구조

```
src/
├── domain/                    # 핵심 도메인 (의존성 없음)
│   ├── entities/              # 도메인 엔티티 (Post, Briefing, Category, CollectionRun)
│   ├── value_objects/         # 값 객체 (content_hash)
│   ├── repositories/          # Repository Protocol (인터페이스)
│   ├── services/              # Service Protocol (Collector, AIProcessor, Notifier)
│   └── exceptions.py          # 도메인 예외
├── application/               # 애플리케이션 레이어
│   ├── use_cases/             # CollectPosts, ProcessPosts, GenerateBriefing, SendBriefing
│   └── dto/                   # 데이터 전송 객체
├── infrastructure/            # 인프라스트럭처 레이어
│   ├── config/                # Settings (pydantic-settings), AppConfig (YAML)
│   ├── database/              # SQLAlchemy 모델, 세션, Repository 구현
│   ├── collectors/            # 각 플랫폼 수집기 구현
│   ├── ai/                    # Claude API 연동
│   └── delivery/              # 이메일 발송
└── presentation/              # 프레젠테이션 레이어
    └── web/                   # FastAPI 웹 대시보드
```

## 도메인 엔티티

| 엔티티 | 파일 | 설명 |
|--------|------|------|
| `Post` | `domain/entities/post.py` | 수집된 게시물 |
| `Briefing` | `domain/entities/briefing.py` | 일일 브리핑 문서 |
| `BriefingItem` | `domain/entities/briefing.py` | 브리핑 내 개별 항목 |
| `Category` | `domain/entities/category.py` | 토픽 카테고리 |
| `CollectionRun` | `domain/entities/collection_run.py` | 수집 실행 로그 |

## Repository Protocol

| Protocol | 파일 | 구현체 |
|----------|------|--------|
| `PostRepository` | `domain/repositories/post_repository.py` | `SqlitePostRepository` |
| `BriefingRepository` | `domain/repositories/briefing_repository.py` | `SqliteBriefingRepository` |
| `CategoryRepository` | `domain/repositories/category_repository.py` | `SqliteCategoryRepository` |
| `CollectionRunRepository` | `domain/repositories/collection_run_repository.py` | `SqliteCollectionRunRepository` |

## Service Protocol

| Protocol | 파일 | 용도 |
|----------|------|------|
| `Collector` | `domain/services/collector.py` | SNS 수집기 인터페이스 |
| `AIProcessor` | `domain/services/ai_processor.py` | AI 처리 인터페이스 |
| `BriefingGenerator` | `domain/services/briefing_generator.py` | 브리핑 생성기 인터페이스 |
| `Notifier` | `domain/services/notifier.py` | 알림/전달 인터페이스 |

## Use Case

| Use Case | 파일 | 설명 |
|----------|------|------|
| `CollectPostsUseCase` | `application/use_cases/collect_posts.py` | 수집기 실행 + 재시도 + 저장 |
| `ProcessPostsUseCase` | `application/use_cases/process_posts.py` | AI 필터/요약/분류 |
| `GenerateBriefingUseCase` | `application/use_cases/generate_briefing.py` | 중복제거 + 브리핑 생성 |
| `SendBriefingUseCase` | `application/use_cases/send_briefing.py` | 이메일 전달 |

## 설정

- **시크릿 (.env)**: `ANTHROPIC_API_KEY`, `SMTP_*`, `DATABASE_URL`
- **앱 설정 (config/settings.yaml)**: 수집 주기, 카테고리, AI 모델, 웹 포트 등
- **pydantic-settings** `Settings` 클래스가 .env를 자동 로드
- **`AppConfig`** 클래스가 YAML을 파싱하여 타입이 있는 설정 객체 제공

## DB

- **SQLite + aiosqlite**: 로컬 파일 기반, 비동기
- **SQLAlchemy ORM**: 도메인 엔티티와 분리된 DB 모델 (`*Model` 클래스)
- **mapper.py**: 도메인 엔티티 ↔ ORM 모델 양방향 변환
