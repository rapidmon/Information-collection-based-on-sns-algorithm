# Feature 003: 브라우저 매니저 + X(Twitter) 수집기

## 개요
Playwright 기반 브라우저 세션 관리자와 X(Twitter) 알고리즘 피드 수집기.

## 구현 파일
- `src/infrastructure/collectors/browser_manager.py` — 브라우저 세션 관리
- `src/infrastructure/collectors/twitter_collector.py` — X 수집기

## 브라우저 매니저

### 핵심 기능
- **세션 지속**: `storageState` API로 쿠키/localStorage를 JSON 파일에 저장/복원
- **안티봇 우회**: `playwright-stealth` 적용, 랜덤 User-Agent
- **수동 로그인**: `manual_login()` 메서드로 headed 브라우저에서 수동 로그인 후 세션 저장
- **멀티 플랫폼**: 플랫폼별 독립 context (twitter, threads, linkedin)

### 세션 저장 경로
```
browser_data/
├── twitter_profile/state.json
├── threads_profile/state.json
└── linkedin_profile/state.json
```

### 최초 로그인 플로우
```
1. manual_login("twitter", "https://x.com/i/flow/login") 호출
2. headed 브라우저가 열림
3. 사용자가 직접 로그인
4. 터미널에서 Enter 입력
5. state.json에 세션 저장
6. 이후 자동 복원
```

## X(Twitter) 수집기

### 수집 방식: GraphQL 인터셉트 (기본)

Twitter는 타임라인을 GraphQL API로 로드. 네트워크 응답을 인터셉트하면
구조화된 JSON을 직접 획득 가능.

```
1. page.on("response") 핸들러 등록
2. x.com/home 접속
3. "HomeTimeline" 포함 URL의 응답을 캡처
4. 스크롤하여 추가 데이터 로드
5. 캡처된 JSON에서 트윗 데이터 추출
```

### GraphQL 응답 구조
```
data → home → home_timeline_urt → instructions → entries[]
  → content → itemContent → tweet_results → result
    → core → user_results (사용자 정보)
    → legacy (텍스트, 인게이지먼트, 미디어)
    → views (조회수)
```

### 추출 데이터
| 필드 | 소스 |
|------|------|
| tweet_id | `legacy.id_str` |
| text | `legacy.full_text` |
| author | `user_legacy.name` / `screen_name` |
| likes | `legacy.favorite_count` |
| retweets | `legacy.retweet_count` |
| replies | `legacy.reply_count` |
| views | `result.views.count` |
| media | `extended_entities.media[].media_url_https` |
| time | `legacy.created_at` |

### DOM 파싱 폴백
GraphQL 인터셉트 실패 시 DOM 파싱으로 폴백:
- `article[data-testid="tweet"]` — 트윗 컨테이너
- `[data-testid="tweetText"]` — 텍스트
- `[data-testid="User-Name"]` — 작성자
- `a[href*="/status/"]` — 트윗 링크

### 세션 만료 감지
페이지 로드 후 URL에 `login` 또는 `flow`가 포함되면 `SessionExpiredError` 발생.

## 설정
```yaml
collection:
  twitter:
    enabled: true
    interval_minutes: 30
    scroll_rounds: 8
    scroll_delay_min: 2.0
    scroll_delay_max: 4.0
    use_graphql_interception: true  # false 시 DOM 폴백
```
