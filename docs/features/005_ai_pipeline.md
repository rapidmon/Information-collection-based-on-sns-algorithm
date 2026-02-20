# Feature 005: AI 처리 파이프라인

## 개요
Claude API를 활용한 3단계 AI 처리 파이프라인.
수집된 게시물의 관련성 필터링, 요약, 분류, 중복 제거 및 토픽 통합을 수행.

## 구현 파일
- `src/infrastructure/ai/prompts.py` — 프롬프트 템플릿
- `src/infrastructure/ai/claude_processor.py` — Claude API 프로세서 (AIProcessor 구현)
- `src/application/use_cases/process_posts.py` — 처리 유즈케이스

## 파이프라인 흐름

```
[미처리 게시물] → [1. 필터+요약 (Haiku)] → [2. 분류+중요도 (Sonnet)] → [DB 업데이트]
                                                                           │
                                                        (일일 브리핑 시)    │
                                                                           ▼
                                            [3. 중복제거+통합 (Sonnet)] → [브리핑 항목]
```

## 단계별 상세

### 1단계: 관련성 필터 + 요약
- **모델**: Haiku (저렴, 빠름)
- **배치 크기**: 20건/호출
- **입력**: 게시물 텍스트 (1000자 제한)
- **출력**: `{is_relevant, summary, language}`
- **관련 판단 기준**: AI, 반도체, 클라우드, 스타트업, 빅테크, 기술 규제

### 2단계: 분류 + 중요도
- **모델**: Sonnet
- **배치 크기**: 20건/호출
- **입력**: 필터링된 관련 게시물의 요약
- **출력**: `{categories[], importance_score}`
- **카테고리**: AI, Semiconductor, Cloud, Startup, BigTech, Regulation
- **중요도 기준**:
  - 0.9-1.0: 산업 영향 중대 (M&A, 정책, 혁신 제품)
  - 0.7-0.9: 주요 (실적, 기술 발표, 투자)
  - 0.5-0.7: 일반 (업데이트, 소규모 발표)
  - 0.3-0.5: 배경/의견
  - 0.0-0.3: 낮음

### 3단계: 중복 제거 + 토픽 통합
- **모델**: Sonnet
- **배치**: 전체 기간 게시물 1회 호출
- **입력**: 기간 내 관련 게시물 전체 요약
- **출력**: `{post_ids[], headline, body_bullets[], category, importance, sources[]}`
- **통합 규칙**: 같은 사건을 다루는 여러 출처를 하나의 브리핑 항목으로 병합

## 에러 처리
- API 호출 실패 시 안전 기본값 사용 (모두 관련으로 표시, 기본 카테고리 할당)
- 개별 배치 실패가 전체 파이프라인을 중단시키지 않음

## 비용 최적화
- 필터링에 Haiku 사용 (~$0.001/호출)
- 텍스트 1000자 제한으로 토큰 절약
- 배치 처리로 API 호출 횟수 최소화
- 일일 브리핑에 Batches API 활용 가능 (50% 절감)

## JSON 파싱
- ```json 코드 블록 처리
- 응답 내 JSON 배열 자동 탐지
- 파싱 실패 시 폴백 로직
