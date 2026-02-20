# Feature 005: AI 처리 파이프라인

## 개요
**[NEW]** ~~Claude API~~ → OpenAI API를 활용한 AI 처리 파이프라인.
수집된 게시물의 관련성 필터링, 요약, 분류, 중복 제거 및 토픽 통합을 수행.

## 구현 파일
- `src/infrastructure/ai/prompts.py` — 프롬프트 템플릿
- **[NEW]** `src/infrastructure/ai/openai_processor.py` — OpenAI API 프로세서 (AIProcessor 구현)
- `src/application/use_cases/process_posts.py` — 처리 유즈케이스

## 파이프라인 흐름

**[NEW]** Haiku/Sonnet 분리 → gpt-4o-mini 단일 모델로 변경:
```
[미처리 게시물] → [1. 필터+요약 (gpt-4o-mini)] → [2. 분류+중요도 (gpt-4o-mini)] → [DB 업데이트]
                                                                                        │
                        [NEW] 관련 없는 게시물 → Firestore에서 삭제                       │
                                                                                        │
                                                        (일일 브리핑 시)                  │
                                                                                        ▼
                                            [3. 중복제거+통합 (gpt-4o-mini)] → [브리핑 항목]
```

## 단계별 상세

### 1단계: 관련성 필터 + 요약
- **[NEW]** **모델**: gpt-4o-mini (Haiku → gpt-4o-mini)
- **배치 크기**: 20건/호출
- **입력**: 게시물 텍스트 (1000자 제한)
- **출력**: `{is_relevant, summary, language}`
- **[NEW]** **관련 판단 기준**: AI, 반도체, 클라우드, 스타트업, 빅테크, 기술 규제, **코딩/개발**
- **[NEW]** 관련 없는 게시물은 Firestore에서 **삭제** (기존: 마킹만)

### 2단계: 분류 + 중요도
- **[NEW]** **모델**: gpt-4o-mini (Sonnet → gpt-4o-mini)
- **배치 크기**: 20건/호출
- **입력**: 필터링된 관련 게시물의 요약
- **출력**: `{categories[], importance_score}`
- **[NEW]** **카테고리**: AI, Semiconductor, Cloud, Startup, BigTech, Regulation, **Coding** (6개 → 7개)
- **중요도 기준**:
  - 0.9-1.0: 산업 영향 중대 (M&A, 정책, 혁신 제품)
  - 0.7-0.9: 주요 (실적, 기술 발표, 투자)
  - 0.5-0.7: 일반 (업데이트, 소규모 발표)
  - 0.3-0.5: 배경/의견
  - 0.0-0.3: 낮음

### 3단계: 중복 제거 + 토픽 통합
- **[NEW]** **모델**: gpt-4o-mini (Sonnet → gpt-4o-mini)
- **배치**: 전체 기간 게시물 1회 호출
- **입력**: 기간 내 관련 게시물 전체 요약
- **출력**: `{post_ids[], headline, body_bullets[], category, importance, sources[]}`
- **통합 규칙**: 같은 사건을 다루는 여러 출처를 하나의 브리핑 항목으로 병합

## **[NEW]** 처리 주기
- **기존**: 매시 :05분 (CronTrigger)
- **변경**: 10분마다 (IntervalTrigger) — 수집 직후 빠르게 처리

## **[NEW]** 관련 없는 게시물 처리
- 기존: `is_relevant = False`로 마킹만 → 대시보드에서 필터링
- 변경: Firestore에서 **완전 삭제** (`delete_many()`) → DB 정리, 대시보드 성능 향상

## 에러 처리
- API 호출 실패 시 안전 기본값 사용 (모두 관련으로 표시, 기본 카테고리 할당)
- 개별 배치 실패가 전체 파이프라인을 중단시키지 않음

## 비용 최적화
**[NEW]** 비용 구조 변경:
- 전체 파이프라인에 gpt-4o-mini 사용 (~$10/월)
- 텍스트 1000자 제한으로 토큰 절약
- 배치 처리로 API 호출 횟수 최소화

## JSON 파싱
- ```json 코드 블록 처리
- 응답 내 JSON 배열 자동 탐지
- 파싱 실패 시 폴백 로직
