# Feature 004: Threads + LinkedIn 수집기

## 개요
Threads와 LinkedIn 알고리즘 피드 수집기. 모두 Playwright 기반.

**[NEW]** BrowserManager → CDP 연결 방식으로 변경. 사용자 Chrome에 직접 연결.

## 구현 파일
- `src/infrastructure/collectors/threads_collector.py`
- `src/infrastructure/collectors/linkedin_collector.py`

## Threads 수집기

### 수집 방식: GraphQL 인터셉트 + DOM 폴백 하이브리드
- **[NEW]** CDP로 사용자 Chrome에 연결하여 기존 탭 사용
- Meta의 GraphQL API 응답을 인터셉트하여 JSON 데이터 추출
- CSS 클래스가 난독화되어 있어 DOM 파싱은 보조 수단

### GraphQL 데이터 추출
재귀적 탐색으로 `thread_items`, `text`, `pk` 키를 찾아 게시물 데이터 추출.

### 추출 필드
| 필드 | 소스 |
|------|------|
| pk (ID) | `post.pk` |
| text | `post.caption.text` |
| username | `post.user.username` |
| likes | `post.like_count` |
| replies | `text_post_app_info.direct_reply_count` |
| media | `image_versions2.candidates[0].url` |
| time | `post.taken_at` (Unix timestamp) |

### DOM 폴백
- `div[data-pressable-container="true"]` — 게시물 컨테이너
- 가장 긴 `<span>` 텍스트를 본문으로 추출
- `a[href*='/post/']` — 게시물 링크

## LinkedIn 수집기

### 수집 방식: DOM 파싱 (보수적)
LinkedIn은 가장 엄격한 안티봇 → DOM 파싱만 사용, 느린 딜레이 필수.

**[NEW]** CDP로 사용자 Chrome에 연결하여 수집.

### 안티봇 우회 전략
- 스크롤 간 3~7초 랜덤 대기
- **[NEW]** 최대 8회 스크롤 (기존 4회 → 8회로 증가)
- **[NEW]** 스크롤 픽셀: 800~1500px (기존 400~900px → 증가)
- 간헐적 마우스 이동
- 초기 페이지 로딩 후 2~4초 대기

### 셀렉터
| 요소 | 셀렉터 |
|------|--------|
| 피드 항목 | `.feed-shared-update-v2` |
| 작성자 | `.update-components-actor__name` |
| 본문 | `.feed-shared-update-v2__description` |
| 좋아요 | `.social-details-social-counts__reactions-count` |
| Activity ID | `data-urn` 속성 (`urn:li:activity:xxx`) |

### "더 보기" 처리
긴 게시물의 `feed-shared-inline-show-more-text__button` 버튼을 클릭하여
전문을 가져옴.

### 세션 만료 감지
URL에 `login`, `authwall`, `checkpoint`, `security` 포함 시 만료.

## 설정
**[NEW]** 수집 주기 및 스크롤 설정 변경:
```yaml
collection:
  threads:
    enabled: true
    interval_minutes: 10         # [NEW] 45 → 10분
    scroll_rounds: 6
    scroll_delay_min: 2.5
    scroll_delay_max: 5.0
  linkedin:
    enabled: true
    interval_minutes: 10         # [NEW] 60 → 10분
    scroll_rounds: 8             # [NEW] 4 → 8회
    scroll_delay_min: 3.0
    scroll_delay_max: 7.0
```
